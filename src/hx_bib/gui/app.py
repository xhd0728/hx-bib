"""NiceGUI front-end for hx-bib.

One page, one job per tab:

   上传  →  load a .bib file (drag & drop or local path)
   格式化 / Format       indentation, equals alignment, field order, sorting
   清理 / Clean          remove/add fields, months, "others", dedupe, advanced fixes
   归一化 / Normalize    venue style (abbr/full/family) and arXiv preset
   校验 / Check          offline structural validation
   校验引用 / Verify     multi-source hallucination verification
   预设 / Venues         browse venue presets (each rendering copyable)

A second page, ``/table``, formats LaTeX tabular environments (one row per
line, tidy ``&``/rules, column alignment) with a per-table diff preview.

Design notes
------------
* **Shared working copy.** Every applied change lands in one working copy that
  all tabs see. Each apply pushes a *stage* onto a history stack, so the user
  can undo / redo / reset to the original at any point.
* **Preview first.** Transforms never touch the working copy until the user
  clicks “Apply”; previews can be inspected three ways — itemized list,
  GitHub-style side-by-side diff, or per-entry cards with copy buttons.
* **Per-session isolation.** All state lives in a page-scoped dict, so
  concurrent sessions (and future server deployments) never share data.
  Heavy work runs in worker threads; ``Net`` is lock-guarded for concurrent
  verification.
"""

from __future__ import annotations

import asyncio
import html
import inspect
import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from nicegui import events, ui

from ..core import cleaner
from ..core.diff import diff_documents, diff_lines, split_entries
from ..core.latex_table import (
    CommentLine,
    Row,
    Rule,
    TableFormatOptions,
    find_tables,
    format_tables,
    parse_table_body,
)
from ..core.model import Entry
from ..core.normalize import NormalizeOptions, normalize_document
from ..core.parser import parse_bibtex
from ..core.validator import ValidateOptions, validate_document
from ..core.venues import (
    VENUES, families, get_venue, match_venue, render_venue, search_venues,
)
from ..core.writer import FormatOptions, render_document, render_entry
from ..verify import Net, RunControl, prefetch_arxiv, retry_failed, verify_entries
from ..verify.report import build_html as build_verify_html

from .i18n import t
from .styles import CSS
from .workspace import (
    DEFAULT_SETTINGS, invalidate_results, jump_stage as _jump_stage,
    merge_settings as _merge_settings, new_state as _new_state,
    preview_is_current, push_stage as _push_stage,
)

SEVERITY_COLOR = {"error": "#dc2626", "warning": "#d97706", "info": "#2563eb"}
VERDICT_COLOR = {
    "FAIL": "#dc2626", "WARN": "#d97706", "MINOR": "#ca8a04",
    "INFO": "#2563eb", "OK": "#16a34a",
}

_IN_SIM = bool(os.environ.get("NICEGUI_USER_SIMULATION"))
_SETTINGS_KEY = "hx-bib-settings"

# Lighthouse flags Quasar's generated markup: the uploader's icon-only "add file"
# button and its hidden <input type=file> have no accessible names.  Patch them
# as they appear (survives re-renders via MutationObserver).
_QUASAR_A11Y_PATCH = """
<script>
(function () {
  if (window.__hxBibA11y) return;
  window.__hxBibA11y = true;
  var fix = function () {
    document.querySelectorAll('.q-uploader__header-content a.q-btn').forEach(function (b) {
      if (!b.hasAttribute('aria-label'))
        b.setAttribute('aria-label', '添加文件 / Add files');
    });
    document.querySelectorAll('input.q-uploader__input').forEach(function (i) {
      if (!i.hasAttribute('aria-label'))
        i.setAttribute('aria-label', '选择 BibTeX 文件 / Choose BibTeX file');
    });
  };
  new MutationObserver(fix).observe(document.documentElement,
                                    {subtree: true, childList: true});
  document.addEventListener('DOMContentLoaded', fix);
  fix();
})();
</script>
"""

_SCROLL_RESTORE = """
<script>
(function () {
  const positions = new Map();
  const mounted = new WeakSet();
  document.addEventListener('scroll', event => {
    const el = event.target;
    if (el.dataset?.hxScroll) positions.set(el.dataset.hxScroll, el.scrollTop);
  }, true);
  window.addEventListener('hx-reset-scroll', () => positions.clear());
  const restore = () => {
    document.querySelectorAll('[data-hx-scroll]').forEach(el => {
      if (mounted.has(el)) return;
      mounted.add(el);
      const top = positions.get(el.dataset.hxScroll) || 0;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (el.isConnected) el.scrollTop = top;
      }));
    });
  };
  new MutationObserver(restore).observe(document.documentElement, {childList:true, subtree:true});
  document.addEventListener('DOMContentLoaded', restore);
})();
</script>
"""

def _splash_html(active: str = "tools") -> str:
    """A small loading overlay; never render a second, fake interface."""
    return """
<div id="hx-splash" role="status" aria-label="Loading hx-bib">
  <div class="hx-logo">hx</div><span>hx-bib</span>
</div>
<style>
#hx-splash { position:fixed; inset:0; z-index:9999; background:#f6f7fb;
  display:flex; align-items:center; justify-content:center; gap:12px;
  font-size:20px; font-weight:700; color:#3730a3; }
</style>
<script>
(function () {
  var start = Date.now();
  var timer = setInterval(function () {
    var content = document.querySelector('.hx-body');
    if ((content && content.children.length) || Date.now() - start > 10000) {
      clearInterval(timer);
      document.getElementById('hx-splash')?.remove();
    }
  }, 40);
})();
</script>
"""


def _head_html(description: str) -> str:
    """Shared <head> payload: meta, styles, a11y patch, splash + lang attr."""
    return (
        f"<meta name='description' content='{description}'>"
        f"<style>{CSS}</style>{_QUASAR_A11Y_PATCH}{_SCROLL_RESTORE}"
        "<script>document.documentElement.lang='zh-CN';</script>"
    )


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
def _set_setting(state: dict, path: str, key: str, value) -> None:
    node = state["settings"].setdefault(path, {})
    if isinstance(node, dict):
        node[key] = value
    state["settings_dirty"] = True


def _bind(widget, state: dict, path: str, key: str, after=None):
    """Persist a widget's value to the per-user settings (localStorage)."""

    def handler(e) -> None:
        _set_setting(state, path, key, e.value)
        if after is not None:
            after(e.value)

    widget.on_value_change(handler)
    return widget


def _opt(state: dict, path: str, key: str):
    return _merge_settings(DEFAULT_SETTINGS, state["settings"]).get(path, {}).get(key)


# ---------------------------------------------------------------------------
# Working copy + staging history
# ---------------------------------------------------------------------------
def _changed_count(before: str, after: str) -> Optional[int]:
    try:
        return len(diff_documents(parse_bibtex(before), parse_bibtex(after)).changed)
    except Exception:  # noqa: BLE001 - display-only helper
        return None


# ---------------------------------------------------------------------------
# HTML fragments
# ---------------------------------------------------------------------------
def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _entry_title(entry: Optional[Entry]) -> str:
    return _esc((entry.get("title") if entry else "") or "")[:120]


def changes_html(before: str, after: str, mode: str, lang: str) -> str:
    changes = diff_documents(parse_bibtex(before), parse_bibtex(after))
    diffs = changes.changed if mode == "changed" else changes.entries
    if not diffs:
        message = "no_field_changes" if before != after else "no_changes"
        return f'<div class="hx-tab-note" style="padding:12px 0">{_esc(t(lang, message))}</div>'
    rows = []
    for d in diffs:
        badges = []
        if d.type_changed and d.before and d.after:
            badges.append(
                f'<span class="hx-field-mod">@type {_esc(d.before.type)} → {_esc(d.after.type)}</span>'
            )
        for fc in d.field_changes:
            cls = {"added": "hx-field-add", "removed": "hx-field-del",
                   "modified": "hx-field-mod"}[fc.kind]
            sign = {"added": "+", "removed": "−", "modified": "~"}[fc.kind]
            badges.append(f'<span class="{cls}">{sign}{_esc(fc.field_name)}</span>')
        status = {
            "added": "#16a34a", "removed": "#dc2626",
            "modified": "#d97706", "unchanged": "#6b7280",
        }[d.kind]
        rows.append(
            f"<tr><td style='font-family:ui-monospace,monospace;font-weight:600'>{_esc(d.key)}</td>"
            f"<td><span class='hx-chip' style='color:{status};background:{status}18'>"
            f"{_esc(t(lang, 'kind_' + d.kind))}</span></td>"
            f"<td>{' '.join(badges) or '—'}</td></tr>"
        )
    return (
        '<table class="hx-changes"><thead><tr>'
        f"<th>{_esc(t(lang, 'col_entry'))}</th><th>{_esc(t(lang, 'col_change'))}</th>"
        f"<th>{_esc(t(lang, 'col_fields'))}</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _diff_rows_html(rows) -> str:
    body = []
    for r in rows:
        has_l, has_r = r.left is not None, r.right is not None
        body.append(
            f'<tr class="{r.kind}">'
            f'<td class="ln">{r.left_no or ""}</td>'
            f'<td class="old{"" if has_l else " empty"}">'
            f'{_esc(r.left) if has_l else ""}</td>'
            f'<td class="ln">{r.right_no or ""}</td>'
            f'<td class="new{"" if has_r else " empty"}">'
            f'{_esc(r.right) if has_r else ""}</td></tr>'
        )
    return "".join(body)


def _entry_badges(before_slice: str, after_slice: str) -> str:
    """Field-level badges for a modified entry (reuses the itemized diff)."""
    try:
        changes = diff_documents(parse_bibtex(before_slice), parse_bibtex(after_slice))
    except Exception:  # noqa: BLE001 - slice may not parse standalone
        return ""
    changed = [d for d in changes.entries if d.kind != "unchanged"]
    if not changed:
        return ""
    d = changed[0]
    badges = []
    if d.type_changed and d.before and d.after:
        badges.append(
            f'<span class="hx-field-mod">@type {_esc(d.before.type)} → '
            f'{_esc(d.after.type)}</span>'
        )
    for fc in d.field_changes[:8]:
        cls = {"added": "hx-field-add", "removed": "hx-field-del",
               "modified": "hx-field-mod"}[fc.kind]
        sign = {"added": "+", "removed": "−", "modified": "~"}[fc.kind]
        badges.append(f'<span class="{cls}">{sign}{_esc(fc.field_name)}</span>')
    extra = len(d.field_changes) - 8
    if extra > 0:
        badges.append(f'<span class="hx-field-mod">+{extra}</span>')
    return "".join(badges)


def entry_diff_html(before: str, after: str, mode: str, lang: str) -> str:
    """Side-by-side diff grouped per entry — each changed entry gets its own
    block (header with cite key, change kind and touched fields), so adjacent
    entries never merge into one undifferentiated wall of rows."""
    b_spans = split_entries(before)
    a_spans = split_entries(after)
    b_by_key: Dict[str, object] = {}
    for sp in b_spans:
        if sp.key is not None and sp.key not in b_by_key:
            b_by_key[sp.key] = sp
    a_by_key: Dict[str, object] = {}
    for sp in a_spans:
        if sp.key is not None and sp.key not in a_by_key:
            a_by_key[sp.key] = sp

    def block(key_html: str, kind: str, rows=None, badges: str = "") -> str:
        head = (
            f'<div class="hx-ediff-head"><span class="hx-ediff-key">{key_html}</span>'
            f'<span class="hx-kind hx-kind-{kind}">{_esc(t(lang, "kind_" + kind))}</span>'
            f'{badges}</div>'
        )
        if rows is None:
            return f'<section class="hx-ediff hx-ediff-{kind}">{head}</section>'
        return (
            f'<section class="hx-ediff hx-ediff-{kind}">{head}'
            f'<div class="hx-ediff-body"><table class="hx-diff"><tbody>'
            f'{_diff_rows_html(rows)}</tbody></table></div></section>'
        )

    blocks = []

    # Lines outside any entry (@string, comments, preamble …)
    b_free = "\n".join(line for sp in b_spans if sp.key is None for line in sp.lines)
    a_free = "\n".join(line for sp in a_spans if sp.key is None for line in sp.lines)
    if b_free.strip() or a_free.strip():
        rows = diff_lines(b_free, a_free)
        if any(r.kind != "equal" for r in rows):
            blocks.append(block(_esc(t(lang, "free_block")), "modified", rows))

    for sp in b_spans:
        if sp.key is None:
            continue
        left_text = "\n".join(sp.lines)
        a = a_by_key.get(sp.key)
        if a is None:
            blocks.append(block(_esc(sp.key), "removed",
                                diff_lines(left_text, "", sp.start, 0)))
            continue
        right_text = "\n".join(a.lines)
        rows = diff_lines(left_text, right_text, sp.start, a.start)
        if not any(r.kind != "equal" for r in rows):
            if mode != "changed":
                blocks.append(block(_esc(sp.key), "unchanged"))
            continue
        blocks.append(block(_esc(sp.key), "modified", rows,
                            _entry_badges(left_text, right_text)))

    for sp in a_spans:
        if sp.key is None or sp.key in b_by_key:
            continue
        blocks.append(block(_esc(sp.key), "added",
                            diff_lines("", "\n".join(sp.lines), 0, sp.start)))

    if not blocks:
        return (f'<div class="hx-tab-note" style="padding:12px 0">'
                f'{_esc(t(lang, "no_changes"))}</div>')
    return "".join(blocks)


BIB_EXAMPLE = """@inproceedings{demo2024,
  title = {An Example Reference.},
  author = {Doe, Jane and Smith, Alex and others},
  year = {2024},
  booktitle = {Proceedings of EMNLP},
  pages = {12-20},
  abstract = {Example text for trying field removal.}
}
@article{demo2023,
  title = {A Second Example},
  author = {Smith, Alex},
  year = {2023},
  journal = {Journal of Machine Learning Research}
}
"""

TABLE_EXAMPLE = r"""\begin{table}[t]
\centering
\caption{Ablation Study of CoRoute with Qwen3-32B.}
\label{tab:answer_ablation_combined}
\small
\setlength{\tabcolsep}{8pt}
\begin{tabular}{lcccc}
\toprule \textbf{Method} & \multicolumn{2}{c}{\textbf{TriviaQA}} & \multicolumn{2}{c}{\textbf{2WikiMQA}} \\ \cmidrule(l){2-3}\cmidrule(l){4-5} & \textbf{EM} & \textbf{F1} & \textbf{EM} & \textbf{F1} \\ \midrule \multicolumn{5}{l}{\textit{\textbf{Component Ablation}}} \\ \midrule Vanilla RAG & 61.5 & 70.0 & 23.4 & 27.5 \\ + Category-free rewriting & 61.2 & 69.8 & 23.7 & 27.4 \\ \bottomrule
\end{tabular}
\end{table}
"""


def _table_signature(body: str) -> Optional[tuple]:
    """Semantic signature of a table body — equal signatures mean the
    formatting only re-arranged whitespace / line breaks, not content."""
    try:
        elements = parse_table_body(body)
    except Exception:  # noqa: BLE001 - malformed body: fall back to text diff
        return None
    sig = []
    for el in elements:
        if isinstance(el, Row):
            sig.append(("row", tuple((c.text.strip(), c.span) for c in el.cells)))
        elif isinstance(el, Rule):
            sig.append(("rule", el.text.strip()))
        elif isinstance(el, CommentLine):
            sig.append(("comment", el.text.strip()))
    return tuple(sig)


def _table_layout_only(before_body: str, after_body: str) -> bool:
    """True when formatting only touched whitespace / line breaks.

    Whitespace-insensitive text equality catches everything the formatter is
    allowed to do (re-wrap rows, respace ``&``, re-indent); anything else —
    e.g. a ``%`` comment dropped from inside the table — differs.
    """
    if re.sub(r"\s+", "", before_body) != re.sub(r"\s+", "", after_body):
        return False
    sig_l = _table_signature(before_body)
    sig_r = _table_signature(after_body)
    return sig_l is None or sig_l == sig_r


def table_result_html(result_text: str, lang: str) -> str:
    """The formatted output itself, front and centre (scrollable)."""
    return (
        f'<details class="hx-tblock" open>'
        f"<summary>"
        f'<span class="material-icons hx-tcompare-ico">auto_awesome</span>'
        f'<span class="hx-tblock-title">{_esc(t(lang, "table_result_title"))}</span>'
        f"</summary>"
        f'<div class="hx-tblock-body"><pre class="hx-pre">{_esc(result_text)}</pre></div>'
        f"</details>"
    )


def table_compare_html(before: str, after: str, lang: str) -> str:
    """Per-table before / after comparison for the table formatter.

    Each table is a collapsible block with two nowrap panes (修改前 /
    格式化后) instead of a line-level diff — formatting merges multi-line
    rows, so line pairing misleads. Modified tables open by default.
    """
    b_slices, _ = find_tables(before)
    a_slices, _ = find_tables(after)
    if not b_slices:
        return ""
    blocks: List[str] = []
    for idx, (bs, asl) in enumerate(zip(b_slices, a_slices), start=1):
        left = before[bs.begin:bs.end]
        right = after[asl.begin:asl.end]
        changed = left != right
        kind = "modified" if changed else "unchanged"
        layout_only = False
        if changed:
            layout_only = _table_layout_only(
                before[bs.body_start:bs.body_end],
                after[asl.body_start:asl.body_end],
            )
        extra = ""
        if changed and layout_only:
            extra = (
                f'<span class="hx-chip" style="color:#0f766e;background:#0f766e1a">'
                f'{_esc(t(lang, "table_layout_only"))}</span>'
            )
        elif changed:
            extra = (
                f'<span class="hx-chip" style="color:#b91c1c;background:#b91c1c1a">'
                f'{_esc(t(lang, "table_content_changed"))}</span>'
            )
        head = (
            f"<summary>"
            f'<span class="material-icons hx-tcompare-ico">chevron_right</span>'
            f'<span class="hx-ediff-key">#{idx} · {_esc(bs.name)}{_esc(bs.colspec)}</span>'
            f'<span class="hx-kind hx-kind-{kind}">{_esc(t(lang, "kind_" + kind))}</span>'
            f"{extra}"
            f'<span class="hx-tab-note" style="margin-left:auto">L{bs.start_line + 1}</span>'
            f"</summary>"
        )
        if not changed:
            blocks.append(f'<details class="hx-tblock hx-tcompare">{head}</details>')
            continue
        note = t(lang, "table_layout_note" if layout_only else "table_content_note")
        blocks.append(
            f'<details class="hx-tblock hx-tcompare" open>{head}'
            f'<div class="hx-tblock-body"><div class="hx-tpanes">'
            f'<div class="hx-tpane">'
            f'<span class="hx-tpane-label hx-tpane-before">'
            f'<span class="material-icons">remove</span>'
            f'{_esc(t(lang, "table_before"))}</span>'
            f'<pre class="hx-pre">{_esc(left)}</pre></div>'
            f'<div class="hx-tpane">'
            f'<span class="hx-tpane-label hx-tpane-after">'
            f'<span class="material-icons">check</span>'
            f'{_esc(t(lang, "table_after"))}</span>'
            f'<pre class="hx-pre">{_esc(right)}</pre></div>'
            f'<div class="hx-tnote">{_esc(note)}</div>'
            f"</div></div></details>"
        )
    return "".join(blocks)


def _summary_line(before: str, after: str, lang: str) -> str:
    changes = diff_documents(parse_bibtex(before), parse_bibtex(after))
    n_add = changes.count("added")
    n_del = changes.count("removed")
    n_mod = changes.count("modified")
    total = len(changes.entries)
    return (
        f"<b>{total}</b> {_esc(t(lang, 'entries'))} · "
        f"<span style='color:#b45309'>{n_mod} {_esc(t(lang, 'kind_modified'))}</span> · "
        f"<span style='color:#15803d'>{n_add} {_esc(t(lang, 'kind_added'))}</span> · "
        f"<span style='color:#b91c1c'>{n_del} {_esc(t(lang, 'kind_removed'))}</span>"
    )


SEVERITY_ICON = {"error": "error", "warning": "warning", "info": "lightbulb"}


def check_html(report, lang: str, flt: str = "ALL") -> str:
    """Validation issues as expandable, severity-annotated cards.

    ``flt`` is one of ``ALL`` / ``error`` / ``warning`` / ``info`` — the
    severity tabs above the list drive it.
    """
    if not report.issues:
        return (
            f'<div class="hx-check-clean">'
            f'<span class="material-icons">check_circle</span>'
            f'{_esc(t(lang, "check_clean"))}</div>'
        )
    issues = [i for i in report.sorted() if flt in ("ALL", i.severity)]
    if not issues:
        return f'<div class="hx-tab-note" style="padding:8px 2px">{_esc(t(lang, "no_results"))}</div>'
    cards = []
    for issue in issues:
        color = SEVERITY_COLOR[issue.severity]
        icon = SEVERITY_ICON[issue.severity]
        meta_bits = []
        if issue.line:
            meta_bits.append(f"L{issue.line}")
        if issue.field_name:
            meta_bits.append(_esc(issue.field_name))
        meta = " · ".join(meta_bits)
        meta_html = f'<span class="hx-issue-meta">{meta}</span>' if meta else ""
        field_val = (
            f"<code>{_esc(issue.field_name)}</code>"
            if issue.field_name else _esc(t(lang, "issue_none"))
        )
        line_val = f"L{issue.line}" if issue.line else _esc(t(lang, "issue_none"))
        cards.append(
            f'<details class="hx-issue-card" style="border-left-color:{color}">'
            f"<summary>"
            f'<span class="material-icons hx-issue-ico" style="color:{color}">{icon}</span>'
            f'<span class="hx-chip" style="color:{color};background:{color}1a">'
            f'{_esc(t(lang, "sev_" + issue.severity))}</span>'
            f'<code class="hx-issue-key">{_esc(issue.key)}</code>'
            f'<span class="hx-issue-msg">{_esc(issue.message)}</span>'
            f"{meta_html}"
            f'<span class="material-icons hx-issue-caret">expand_more</span>'
            f"</summary>"
            f'<div class="hx-issue-body">'
            f'<div><div class="lbl">{_esc(t(lang, "issue_entry"))}</div>'
            f'<div class="val"><code>{_esc(issue.key)}</code></div></div>'
            f'<div><div class="lbl">{_esc(t(lang, "issue_line"))}</div>'
            f'<div class="val">{line_val}</div></div>'
            f'<div><div class="lbl">{_esc(t(lang, "issue_field"))}</div>'
            f'<div class="val">{field_val}</div></div>'
            f'<div><div class="lbl">{_esc(t(lang, "issue_level"))}</div>'
            f'<div class="val" style="color:{color};font-weight:700">'
            f'{_esc(t(lang, "sev_" + issue.severity))}</div></div>'
            f'<div class="hx-issue-hint">'
            f'{_esc(t(lang, "sev_" + issue.severity + "_hint"))}</div>'
            f"</div></details>"
        )
    return "".join(cards)


def verify_html(results, verdict_filter: str, lang: str) -> str:
    order = {"FAIL": 0, "WARN": 1, "MINOR": 2, "INFO": 3, "OK": 4}
    shown = [r for r in results if verdict_filter in ("ALL", r.verdict)]
    shown.sort(key=lambda r: (order[r.verdict], r.entry.line))
    if not shown:
        return f'<div class="hx-tab-note">{_esc(t(lang, "no_results"))}</div>'
    blocks = []
    for res in shown:
        color = VERDICT_COLOR[res.verdict]
        label = t(lang, "verdict_" + res.verdict)
        issues = "".join(
            f'<div class="hx-issue"><span style="color:{VERDICT_COLOR.get(sev, "#64748b")}">•</span>'
            f"<span>{_esc(msg)}</span></div>"
            for sev, msg in res.issues
        ) or f'<div class="hx-tab-note">✔ {_esc(t(lang, "verified_ok"))}</div>'
        evidence = ""
        records = [r for r in res.evidence if r.found]
        if records:
            items = "".join(
                f"<div><b style='color:#475569'>{_esc(r.source)}</b> · "
                f"{_esc(r.title)[:140]} ({_esc(str(r.year or ''))})"
                f"{(' · ' + _esc(r.venue)) if r.venue and r.source != 'arXiv' else ''}</div>"
                for r in records[:3]
            )
            evidence = f'<div class="hx-evidence">{items}</div>'
        diff_block = ""
        ad = res.author_diff
        if ad:
            def names(items):
                cells = "".join(
                    f'<li class="{"ok" if n["matched"] else "bad"}">{_esc(n["display"])}</li>'
                    for n in items
                )
                return f"<ol class='hx-names'>{cells}</ol>"

            diff_block = (
                '<div class="hx-evidence"><b>'
                + _esc(t(lang, "author_diff"))
                + "</b><div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px'>"
                f"<div><div class='hx-tab-note'>{_esc(t(lang, 'your_bib'))}</div>{names(ad['bib'])}</div>"
                f"<div><div class='hx-tab-note'>{_esc(ad['source'])}</div>{names(ad['src'])}</div>"
                "</div></div>"
            )
        blocks.append(
            f'<div class="hx-result" style="border-left:4px solid {color}">'
            f"<div style='display:flex;gap:10px;align-items:center;flex-wrap:wrap'>"
            f'<span class="hx-chip" style="color:{color};background:{color}18">{_esc(label)}</span>'
            f"<b style='font-family:ui-monospace,monospace'>{_esc(res.entry.key)}</b>"
            f"<span class='hx-tab-note'>@{_esc(res.entry.type)}</span>"
            f"<span style='font-size:13.5px;color:#334155'>{_entry_title(res.entry)}</span></div>"
            f"<div style='margin-top:6px'>{issues}</div>{diff_block}{evidence}</div>"
        )
    return "".join(blocks)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------
def _read_file_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


async def _copy_text(text: str, lang: str) -> None:
    try:
        copied = await ui.run_javascript(
            "return navigator.clipboard ? navigator.clipboard.writeText("
            + json.dumps(text)
            + ").then(() => true).catch(() => false) : false;"
        )
        ui.notify(t(lang, "copied" if copied else "copy_failed"),
                  type="positive" if copied else "warning", position="bottom-right")
    except Exception:  # noqa: BLE001 - clipboard may be unavailable
        ui.notify(t(lang, "copy_failed"), type="warning", position="bottom-right")


def _copy_handler(text: str, lang: str):
    async def handler() -> None:
        await _copy_text(text, lang)

    return handler


def _confirm(lang: str, title: str, body: str, on_confirm: Callable) -> None:
    with ui.dialog() as dialog, ui.card().classes("gap-3").style("min-width:360px"):
        ui.label(title).classes("text-lg font-bold")
        ui.label(body).classes("hx-tab-note")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(t(lang, "cancel"), on_click=dialog.close).props("flat")
            ui.button(
                t(lang, "confirm"),
                on_click=lambda: (dialog.close(), on_confirm()),
            ).props("unelevated color=primary")
    dialog.open()


def _download(state: dict, text: str, suffix: str = "bib") -> None:
    stem = Path(state["name"]).stem or "bibliography"
    ui.download(text.encode("utf-8"), f"{stem}.{suffix}")


def _busy(button, busy: bool) -> None:
    """Show / hide the spinner inside a button while a job runs."""
    try:
        if busy:
            button.props("loading")
        else:
            button.props(remove="loading")
    except Exception:  # noqa: BLE001 - widget may have been rebuilt
        pass


async def _run_preview_job(button, container, state: dict, lang: str,
                           key: str, compute: Callable, refresh_body,
                           opts: FormatOptions, label: str) -> None:
    """Compute from an immutable source; a newer document/job always wins."""
    before, revision = state["text"], state["revision"]
    token = object()
    state["preview_jobs"][key] = token
    _busy(button, True)
    try:
        result = await asyncio.to_thread(compute, before)
    except Exception as exc:  # surface failures without losing the working copy
        ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
        return
    finally:
        _busy(button, False)
    if not preview_is_current(state, revision, before):
        return
    if state["preview_jobs"].get(key) is not token:
        return
    state["previews"][key] = {
        "before": before, "result_text": result, "opts": opts, "label": label,
        "revision": revision,
    }
    if not container.is_deleted:
        _render_preview(container, state, lang, before, result, refresh_body,
                        opts=opts, label=label, revision=revision)


def _draft(widget, state: dict, key: str, default=""):
    """Keep unfinished input in memory across mode/language/history refreshes."""
    widget.value = state["drafts"].get(key, default)
    widget.on_value_change(lambda e: state["drafts"].update({key: e.value}))
    return widget


def _header(lang: str, active: str, on_lang: Callable, on_switch: Callable) -> None:
    """Sticky app header shared by both modes (“tools” or “table” active).

    The mode switch is a segmented control in the centre — BibTeX 工具 ⇄
    LaTeX 表格 — rendered as plain buttons that flip an in-page state flag,
    so switching never reloads the app (no splash reflash, no layout jump).
    """
    sub = t(lang, "table_title") if active == "table" else t(lang, "subtitle")
    with ui.element("header").classes("hx-header w-full"):
        with ui.row().classes(
            "hx-header-inner w-full max-w-6xl mx-auto px-4 items-center justify-between no-wrap gap-3"
        ):
            with ui.row().classes("items-center gap-3 no-wrap"):
                with ui.element("div").classes("hx-logo"):
                    ui.label("hx")
                with ui.column().classes("gap-0"):
                    with ui.element("h1").classes("m-0"):
                        ui.label("hx-bib").classes("hx-brand")
                    ui.label(sub).classes("hx-brand-sub")
            with ui.element("nav").classes("hx-seg").props(
                f'aria-label="{t(lang, "nav_switch")}"'
            ):
                for key, label_key, icon in (
                    ("tools", "nav_tools", "book"),
                    ("table", "nav_table", "grid_on"),
                ):
                    is_active = active == key
                    cls = "hx-seg-item" + (" hx-seg-active" if is_active else "")
                    btn = ui.element("button").classes(cls).mark("nav-" + key).props(
                        'type="button"' + (' aria-current="page"' if is_active else "")
                    )
                    btn.on("click", lambda k=key: on_switch(k))
                    with btn:
                        ui.icon(icon).classes("hx-seg-ico")
                        ui.label(t(lang, label_key))
            ui.button("中 / EN", on_click=on_lang).props(
                "flat dense no-caps "
                "aria-label='中 / EN — 切换语言 / Switch language'"
            ).classes("hx-lang")


def _panel_head(icon: str, title: str, note: str) -> None:
    with ui.row().classes("hx-panel-head items-start gap-3 no-wrap"):
        with ui.element("div").classes("hx-panel-icon"):
            ui.icon(icon, size="20px")
        with ui.column().classes("gap-0"):
            ui.label(title).classes("hx-panel-title")
            ui.label(note).classes("hx-tab-note")


def _flow_html(lang: str, keys: List[str]) -> str:
    """Pill-style ① → ② → ③ journey strip (upload page)."""
    arrow = '<span class="material-icons hx-flow-arrow" aria-hidden="true">arrow_forward</span>'
    steps = [
        f'<span class="hx-flow-step"><span class="hx-flow-num">{i}</span>'
        f"{_esc(t(lang, key))}</span>"
        for i, key in enumerate(keys, 1)
    ]
    return '<div class="hx-flow">' + arrow.join(steps) + "</div>"


def _steps_html(lang: str, keys: List[str]) -> str:
    """Compact numbered breadcrumb for operation panels (no → pills)."""
    chev = '<span class="material-icons" aria-hidden="true">chevron_right</span>'
    steps = [
        f'<span class="hx-step"><b>{i}</b>{_esc(t(lang, key))}</span>'
        for i, key in enumerate(keys, 1)
    ]
    return '<div class="hx-steps">' + chev.join(steps) + "</div>"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def register_pages() -> None:
    """Register the NiceGUI app — split from ``run_gui`` so tests can reuse it.

    Both ``/`` (BibTeX tools) and ``/table`` (LaTeX tables) build the same
    single-page app; the header switch flips ``state["mode"]`` in place.
    """

    @ui.page("/")
    def main_page() -> None:
        _build_page("tools")

    @ui.page("/table")
    def table_page() -> None:
        _build_page("table")


def _build_page(initial_mode: str) -> None:
    if initial_mode == "table":
        ui.add_head_html(_head_html(
            "hx-bib LaTeX table formatter — one row per line, tidy & and rules. "
            "LaTeX 表格格式化工具。"
        ))
    else:
        ui.add_head_html(_head_html(
            "hx-bib — format, clean, normalize, validate and verify BibTeX "
            "bibliographies. BibTeX 整理、格式校验与多源引用核查工具。"
        ))
    ui.add_body_html(_splash_html(initial_mode))
    # AA-compliant primary (WCAG contrast on white; NiceGUI's default fails).
    ui.colors(primary="#4338ca", secondary="#0f766e", accent="#7c3aed")
    ui.input.default_props("outlined dense")
    ui.select.default_props("outlined dense options-dense")
    ui.number.default_props("outlined dense")
    state = _new_state()
    state["mode"] = initial_mode
    ui.context.client.on_delete(lambda: invalidate_results(state))

    def _apply_lang_attr() -> None:
        if _IN_SIM:
            return
        try:
            code = "zh-CN" if state["lang"] == "zh" else "en"
            ui.run_javascript(f"document.documentElement.lang = {json.dumps(code)}")
        except Exception:  # noqa: BLE001
            pass

    async def load_settings() -> None:
        """Restore user preferences (language + widget options) from localStorage."""
        try:
            raw = await ui.run_javascript(
                f"localStorage.getItem({json.dumps(_SETTINGS_KEY)})"
            )
            if raw:
                state["settings"] = _merge_settings(DEFAULT_SETTINGS, json.loads(raw))
                state["lang"] = state["settings"].get("lang") or state["lang"]
                _apply_lang_attr()
                header.refresh()
                body.refresh()
        except Exception:  # noqa: BLE001 - no JS/browser: keep defaults
            pass

    async def flush_settings() -> None:
        if not state["settings_dirty"] or _IN_SIM:
            return
        state["settings_dirty"] = False
        try:
            encoded = json.dumps(json.dumps(state["settings"]))
            await ui.run_javascript(
                f"localStorage.setItem({json.dumps(_SETTINGS_KEY)}, {encoded})"
            )
        except Exception:  # noqa: BLE001
            state["settings_dirty"] = True

    if not _IN_SIM:
        ui.timer(0.15, load_settings, once=True)
    ui.timer(1.2, flush_settings)
    _apply_lang_attr()

    def toggle_lang() -> None:
        state["lang"] = "en" if state["lang"] == "zh" else "zh"
        state["settings"]["lang"] = state["lang"]
        state["settings_dirty"] = True
        _apply_lang_attr()
        header.refresh()
        body.refresh()

    def switch_mode(mode: str) -> None:
        """Flip between the BibTeX workspace and the LaTeX table formatter in place."""
        if state["mode"] == mode:
            return
        state["mode"] = mode
        if not _IN_SIM:
            try:
                path = "/table" if mode == "table" else "/"
                ui.run_javascript(
                    f"history.replaceState(null, '', {json.dumps(path)})"
                )
            except Exception:  # noqa: BLE001
                pass
        header.refresh()
        body.refresh()

    with ui.element("div").classes("hx-page w-full"):
        @ui.refreshable
        def header() -> None:
            _header(state["lang"], state["mode"], toggle_lang, switch_mode)
        header()

        with ui.element("main").classes("hx-main w-full mx-auto px-4 py-4"):
            # ---------------- body ----------------
            @ui.refreshable
            def body() -> None:
                lang = state["lang"]
                with ui.element("div").classes("hx-body w-full").props(
                    f'data-hx-scroll="root-{state["mode"]}"'
                ):
                    if state["mode"] == "table":
                        _render_table_page(state, lang, body)
                        return
                    if state["venues_only"]:
                        _render_venues_only(state, lang, body)
                        return
                    if not state["text"]:
                        _render_upload(state, lang, body)
                        return

                    doc = parse_bibtex(state["text"])
                    with ui.column().classes("hx-workspace w-full gap-3"):
                        _render_status_bar(state, lang, doc, body)

                        tab_defs = [
                            ("format", t(lang, "tab_format"), "brush"),
                            ("clean", t(lang, "tab_clean"), "cleaning_services"),
                            ("normalize", t(lang, "tab_normalize"), "auto_fix_high"),
                            ("check", t(lang, "tab_check"), "rule"),
                            ("verify", t(lang, "tab_verify"), "travel_explore"),
                            ("venues", t(lang, "tab_venues"), "stadium"),
                        ]
                        with ui.tabs(value=state["tab"]).classes(
                            "hx-tabs w-full"
                        ).props(
                            "align=left inline-label no-caps id=hx-tabs-bar"
                        ) as tabs:
                            for key, label, icon in tab_defs:
                                ui.tab(key, label, icon=icon).mark(f"tab-{key}")

                        tabs.on_value_change(lambda e: state.update(tab=e.value))

                        with ui.tab_panels(tabs, value=state["tab"], animated=False).classes(
                            "hx-workspace-panels w-full bg-transparent"
                        ):
                            for key, _, _ in tab_defs:
                                with ui.tab_panel(key).classes("hx-panel-scroll p-0").props(
                                    f'data-hx-scroll="panel-{key}"'
                                ):
                                    _build_panel(key, state, lang, doc, body)

            body()


def run_gui(host: str = "127.0.0.1", port: int = 8080, show: bool = True) -> None:
    register_pages()
    ui.run(host=host, port=port, show=show, title="hx-bib", favicon="📚", reload=False)


# ---------------------------------------------------------------------------
# Upload view
# ---------------------------------------------------------------------------
def _load_text(state: dict, body, lang: str, text: str, name: str, path: str = "") -> None:
    doc = parse_bibtex(text)
    if not doc.entries and not doc.strings and not doc.preamble:
        ui.notify(t(lang, "invalid_bib"), type="warning", position="bottom-right")
        return
    invalidate_results(state)
    if not _IN_SIM:
        ui.run_javascript("window.dispatchEvent(new Event('hx-reset-scroll'))")
    state.update(
        text=text, baseline=text, name=name, path=path,
        history=[], pointer=0, verify_results=None, check_report=None, tab="format",
        venues_only=False,
    )
    body.refresh()
    ui.notify(
        t(lang, "loaded").format(n=len(doc.entries), name=name),
        type="positive", position="bottom-right",
    )


def _render_upload(state: dict, lang: str, body) -> None:
    # ---- hero ----
    with ui.column().classes("w-full items-center gap-1 pt-8 pb-1"):
        ui.html(
            '<span class="hx-hero-badge"><span class="material-icons" '
            'style="font-size:14px;">lock</span> '
            f"{_esc(t(lang, 'hero_badge'))}</span>"
        )
        ui.label(t(lang, "upload_title")).classes("hx-hero-title")
        ui.label(t(lang, "upload_hint")).classes("hx-hero-sub")
        ui.html(_flow_html(lang, ["flow_load", "flow_operate", "flow_export"]))

    with ui.card().classes("hx-card hx-import w-full max-w-3xl mx-auto p-6 gap-4 mt-4"):
        with ui.tabs(value=state["drafts"].get("import_method", "file")).classes("w-full hx-import-tabs").props("align=justify no-caps inline-label") as methods:
            for key, icon in (("file", "upload_file"), ("paste", "content_paste"), ("path", "folder_open")):
                ui.tab(key, t(lang, "import_" + key), icon=icon).mark("import-" + key)
        methods.on_value_change(lambda e: state["drafts"].update(import_method=e.value))
        with ui.tab_panels(methods, value=methods.value, animated=False).classes("hx-import-panels w-full bg-transparent"):
            with ui.tab_panel("file").classes("p-0 gap-4"):
                async def on_upload(e: events.UploadEventArguments) -> None:
                    data = e.file.read()
                    if inspect.isawaitable(data):
                        data = await data
                    _load_text(state, body, lang, _read_file_bytes(data),
                               e.file.name or "uploaded.bib")

                ui.upload(
                    label=t(lang, "upload_button"),
                    on_upload=on_upload,
                on_rejected=lambda: ui.notify(t(lang, "upload_rejected"), type="warning"),
                    auto_upload=True,
                    max_file_size=20_000_000,
                ).props('accept=".bib,.bibtex,text/plain"').classes("hx-uploader")
                ui.label(t(lang, "import_file_hint")).classes("hx-tab-note")
            with ui.tab_panel("path").classes("p-0 gap-4"):
                ui.label(t(lang, "import_path_hint")).classes("hx-tab-note")
                with ui.row().classes("w-full items-stretch gap-2 no-wrap"):
                    path_input = ui.input(placeholder=t(lang, "path_placeholder")).classes(
                        "flex-1"
                    ).props(f"outlined aria-label='{t(lang, 'or_path')}'").mark("path-input")
                    _draft(path_input, state, "import_path")

                    def load_path() -> None:
                        raw = (path_input.value or "").strip().strip('"')
                        if not raw:
                            ui.notify(t(lang, "or_path"), type="warning", position="bottom-right")
                            return
                        try:
                            text = _read_file_bytes(Path(raw).expanduser().read_bytes())
                        except OSError as exc:
                            ui.notify(str(exc), type="negative", position="bottom-right")
                            return
                        _load_text(state, body, lang, text, Path(raw).name,
                                   str(Path(raw).expanduser()))

                    ui.button(t(lang, "load"), icon="folder_open",
                              on_click=load_path).props(
                        "outline color=primary no-caps"
                    ).mark("load-btn")
                    path_input.on("keydown.enter", load_path)
            with ui.tab_panel("paste").classes("p-0"):
                with ui.column().classes("w-full gap-2"):
                    ui.label(t(lang, "paste_title")).classes("text-sm font-semibold").style(
                        "color:#1e293b"
                    )
                    paste_area = ui.textarea(
                        placeholder=t(lang, "paste_placeholder")
                    ).classes("w-full").props(
                        f"outlined rows=5 aria-label='{t(lang, 'paste_placeholder')}'"
                    ).style("font-family:ui-monospace,monospace;font-size:12.5px").mark(
                        "paste-input"
                    )
                    _draft(paste_area, state, "import_text")

                    def import_paste() -> None:
                        raw = (paste_area.value or "").strip()
                        if not raw:
                            ui.notify(t(lang, "paste_empty"), type="warning", position="bottom-right")
                            return
                        _load_text(state, body, lang, raw + "\n", "pasted.bib")

                    with ui.row().classes("w-full justify-end"):
                        ui.button(t(lang, "paste_import"), icon="input",
                                  on_click=import_paste).props(
                            "unelevated color=primary no-caps"
                        ).mark("paste-load")
        with ui.row().classes("w-full items-center justify-between gap-2"):
            ui.label(t(lang, "import_safe")).classes("hx-tab-note")
            ui.button(t(lang, "try_example"), icon="science", on_click=lambda: _load_text(
                state, body, lang, BIB_EXAMPLE, "example.bib",
            )).props("flat color=primary no-caps").mark("bib-example")

    # ---- capability strip / secondary entrance ----
    with ui.column().classes("w-full items-center gap-3 mt-5"):
        pills = [
            ("brush", t(lang, "tab_format")),
            ("cleaning_services", t(lang, "tab_clean")),
            ("auto_fix_high", t(lang, "tab_normalize")),
            ("rule", t(lang, "tab_check")),
            ("travel_explore", t(lang, "tab_verify")),
        ]
        inner = "".join(
            f'<span class="hx-feature"><span class="material-icons" aria-hidden="true">'
            f"{icon}</span>{_esc(label)}</span>"
            for icon, label in pills
        )
        ui.html(f'<div class="hx-features">{inner}</div>')

        def browse_venues() -> None:
            state["venues_only"] = True
            body.refresh()

        ui.button(t(lang, "browse_venues"), on_click=browse_venues).props(
            "flat dense no-caps color=primary"
        ).mark("browse-venues")


def _render_venues_only(state: dict, lang: str, body) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.button(t(lang, "back"), icon="arrow_back", on_click=lambda: (
            state.update(venues_only=False), body.refresh(),
        )).props("flat dense").mark("venues-back")
    _panel_venues(state, lang, None, body)


# ---------------------------------------------------------------------------
# LaTeX table formatter page (/table)
# ---------------------------------------------------------------------------
def _render_table_page(state: dict, lang: str, body) -> None:
    result_widgets = {}

    def update_freshness() -> None:
        res = state["result"]
        fresh = res is not None and res[0] == state["source_text"] and (
            state.get("result_options") == (state["amp"], state["blank_lines"], state["row_indent"])
        )
        for key in ("copy", "download"):
            if key in result_widgets:
                result_widgets[key].set_enabled(fresh)
        if "status" in result_widgets:
            result_widgets["status"].set_text(t(lang, "table_current" if fresh else "table_stale"))

    def update_input(key: str, value) -> None:
        state[key] = value
        update_freshness()

    with ui.card().classes("hx-card w-full p-5 gap-4"):
        _panel_head("grid_on", t(lang, "table_title"), t(lang, "table_note"))
        ui.label(t(lang, "table_paste_title")).classes("hx-tab-note")
        source = ui.textarea(
            placeholder=t(lang, "table_paste_placeholder"),
        ).classes("w-full").props(
            f"outlined rows=8 aria-label='{t(lang, 'table_paste_title')}'"
        ).style(
            "font-family:ui-monospace,monospace;font-size:12.5px"
        ).mark("table-input")
        if state["source_text"]:
            source.value = state["source_text"]
        source.on_value_change(lambda e: update_input("source_text", e.value or ""))

        def run_format() -> None:
            text = state["source_text"] or (source.value or "")
            if not text.strip():
                ui.notify(t(lang, "table_empty"), type="warning", position="bottom-right")
                return
            opts = TableFormatOptions(
                amp_style=state["amp"],
                blank_lines_around_rules=state["blank_lines"],
                row_indent=state["row_indent"],
            )
            state["result"] = (text, format_tables(text, opts))
            state["result_options"] = (state["amp"], state["blank_lines"], state["row_indent"])
            output.refresh()

        with ui.element("div").classes("hx-optbar w-full"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.label(t(lang, "table_amp")).classes("hx-optlabel")
                amp = ui.toggle(
                    {
                        "space": t(lang, "table_amp_space"),
                        "align": t(lang, "table_amp_align"),
                        "none": t(lang, "table_amp_none"),
                    },
                    value=state["amp"],
                ).props(
                    "dense unelevated no-caps inline toggle-color=white "
                    "toggle-text-color=primary"
                ).mark("table-amp")
                amp.on_value_change(lambda e: update_input("amp", e.value or "space"))
            blank = ui.switch(
                t(lang, "table_blank"), value=state["blank_lines"],
            ).mark("table-blank")
            blank.on_value_change(lambda e: update_input("blank_lines", bool(e.value)))
            indent = ui.number(
                t(lang, "table_indent_label"), value=state["row_indent"],
                min=0, max=16, step=1,
            ).classes("w-52").props("dense outlined").mark("table-indent")
            indent.on_value_change(
                lambda e: update_input("row_indent", max(0, min(16, int(e.value or 0))))
            )

        def load_example() -> None:
            state["source_text"] = TABLE_EXAMPLE
            source.value = TABLE_EXAMPLE

        with ui.row().classes("w-full items-center gap-2"):
            ui.button(
                t(lang, "table_example"), icon="auto_awesome", on_click=load_example,
            ).props("flat color=primary no-caps").mark("table-example")
            ui.space()
            ui.label("Ctrl + Enter").classes("hx-kbd")
            fmt_button = ui.button(
                t(lang, "table_format_btn"), icon="bolt", on_click=run_format,
            ).props("unelevated color=primary no-caps").mark("table-format")
            fmt_button.tooltip("Ctrl + Enter")

        source.on("keydown.ctrl.enter", run_format)

    @ui.refreshable
    def output() -> None:
        with ui.card().classes("hx-card w-full p-5 gap-3"):
            res = state["result"]
            if res is None:
                ui.html(
                    f'<div class="hx-empty">{_esc(t(lang, "table_empty_preview"))}</div>'
                )
                return
            before, result = res
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.html(
                        f'<span class="hx-chip" style="color:#4338ca;background:#4338ca1a">'
                        f'{_esc(t(lang, "table_summary").format(tab=result.tables, rows=result.rows))}'
                        f'</span>'
                    )
                    if result.tables and result.text == before:
                        ui.html(
                            f'<span class="hx-chip" style="color:#475569;background:#f1f5f9">'
                            f'{_esc(t(lang, "table_unchanged"))}</span>'
                        )
                with ui.row().classes("items-center gap-2"):
                    result_widgets["copy"] = ui.button(
                        t(lang, "table_copy_result"), icon="content_copy",
                        on_click=_copy_handler(result.text, lang),
                    ).props("outline color=primary no-caps").mark("table-copy")
                    result_widgets["download"] = ui.button(
                        t(lang, "table_download"), icon="download",
                        on_click=lambda: ui.download(
                            result.text.encode("utf-8"), "tables.tex"
                        ),
                    ).props("outline no-caps").mark("table-download")
            result_widgets["status"] = ui.label("").classes("hx-tab-note").style("min-height:20px")
            update_freshness()
            if result.tables == 0:
                ui.html(f'<div class="hx-empty">{_esc(t(lang, "table_no_tables"))}</div>')
            else:
                # Result first: the formatted text is the main output…
                ui.html(table_result_html(result.text, lang)).classes("w-full")
                # …then an opt-in, per-table before/after view for checking.
                if result.text != before:
                    ui.label(t(lang, "table_compare_title")).classes(
                        "hx-tab-note font-bold"
                    )
                    ui.html(table_compare_html(before, result.text, lang)).classes("w-full")
            if result.warnings:
                with ui.expansion(
                    t(lang, "table_warnings").format(n=len(result.warnings)), icon="info",
                ).classes("w-full"):
                    for warning in result.warnings:
                        ui.label(f"• {warning}").classes("hx-tab-note")



    output()

# ---------------------------------------------------------------------------
# Status bar (shared working copy)
# ---------------------------------------------------------------------------
def _render_status_bar(state: dict, lang: str, doc, body) -> None:
    changed = _changed_count(state["baseline"], state["text"])

    def notify_unavailable(key: str) -> None:
        ui.notify(t(lang, key), type="warning", position="bottom-right")

    def do_undo() -> None:
        if state["pointer"] == 0:
            notify_unavailable("nothing_to_undo")
            return
        label = state["history"][state["pointer"] - 1]["label"]
        _jump_stage(state, state["pointer"] - 1)
        ui.notify(t(lang, "undo_done").format(label=label), position="bottom-right")
        body.refresh()

    def do_redo() -> None:
        if state["pointer"] >= len(state["history"]):
            notify_unavailable("nothing_to_redo")
            return
        label = state["history"][state["pointer"]]["label"]
        _jump_stage(state, state["pointer"] + 1)
        ui.notify(t(lang, "redo_done").format(label=label), position="bottom-right")
        body.refresh()

    def do_reset() -> None:
        _confirm(
            lang, t(lang, "reset_title"), t(lang, "reset_body"),
            lambda: (_jump_stage(state, 0), body.refresh(),
                     ui.notify(t(lang, "reset_done"), position="bottom-right")),
        )

    def do_close() -> None:
        def close() -> None:
            invalidate_results(state)
            state.update(text="", baseline="", name="", path="", history=[],
                         pointer=0, tab="format", venues_only=False)
            body.refresh()

        _confirm(lang, t(lang, "close_title"), t(lang, "close_body"), close)

    with ui.card().classes("hx-card hx-status w-full px-4 py-3 gap-2"):
        with ui.row().classes("hx-status-meta w-full items-center gap-3 flex-wrap"):
            ui.icon("description", size="20px").style("color:#4338ca")
            ui.label(state["name"]).classes("hx-filename font-semibold").tooltip(state["name"]).mark("file-name")
            ui.label(t(lang, "entries_loaded").format(n=len(doc.entries))).classes(
                "hx-tab-note text-sm"
            )
            if doc.warnings:
                ui.label(f"⚠ {len(doc.warnings)}").style(
                    "color:#b45309;font-size:13px"
                ).tooltip("\n".join(doc.warnings[:5]))
            ui.label(
                t(lang, "workcopy") + " · " + (
                    t(lang, "steps_staged").format(n=state["pointer"])
                    if state["pointer"] else t(lang, "no_steps")
                )
            ).classes("hx-chip").style("background:#eef2ff;color:#4338ca").mark("staged")
            if changed is None:
                pass
            elif changed:
                ui.label(t(lang, "vs_original").format(n=changed)).classes(
                    "hx-chip"
                ).style("background:#fef3c7;color:#92400e")
            elif state["pointer"]:
                ui.label(t(lang, "same_as_original" if state["text"] == state["baseline"]
                           else "nonfield_changes")).classes("hx-chip").style(
                    "background:#f1f5f9;color:#475569"
                )

        # clickable stage history: jump to any applied step (or the original)
        with ui.row().classes("hx-history w-full items-center gap-1 no-wrap"):
            ui.icon("history", size="16px").style("color:#94a3b8;flex:none")

            def jump(target: int) -> None:
                _jump_stage(state, target)
                body.refresh()

            stage_labels = [(0, t(lang, "no_steps"))] + [
                (i + 1, stage["label"]) for i, stage in enumerate(state["history"])
            ]
            for target, label in stage_labels:
                cls = "hx-stage"
                if state["pointer"] == target:
                    cls += " hx-stage-active"
                elif target > state["pointer"]:
                    cls += " hx-stage-future"
                ui.button(label, on_click=lambda tt=target: jump(tt)).props(
                    "flat dense no-caps"
                ).classes(cls).tooltip(t(lang, "stage_jump")).mark(f"stage-{target}")

        with ui.row().classes("w-full items-center gap-1 flex-wrap"):
            ui.button(t(lang, "undo"), icon="undo", on_click=do_undo).props(
                "flat dense"
            ).tooltip(t(lang, "undo")).mark("undo-btn").set_enabled(state["pointer"] > 0)
            ui.button(t(lang, "redo"), icon="redo", on_click=do_redo).props(
                "flat dense"
            ).tooltip(t(lang, "redo")).mark("redo-btn").set_enabled(
                state["pointer"] < len(state["history"])
            )
            ui.button(t(lang, "reset_original"), icon="restart_alt",
                      on_click=do_reset).props("flat dense").mark("reset-btn")
            ui.separator().props("vertical").classes("mx-1")
            ui.button(t(lang, "copy_all"), icon="content_copy",
                      on_click=_copy_handler(state["text"], lang)).props(
                "flat dense"
            ).mark("copy-all-btn")
            ui.button(t(lang, "download"), icon="download",
                      on_click=lambda: _download(state, state["text"], "bib")).props(
                "flat dense"
            ).mark("download-btn")

            def save_to_path() -> None:
                if not state["path"]:
                    ui.notify(t(lang, "no_path"), type="warning", position="bottom-right")
                    return
                try:
                    Path(state["path"]).write_text(state["text"], encoding="utf-8")
                    ui.notify(t(lang, "saved").format(path=state["path"]),
                              type="positive", position="bottom-right")
                except OSError as exc:
                    ui.notify(str(exc), type="negative", position="bottom-right")

            ui.button(t(lang, "save_file"), icon="save", on_click=save_to_path).props(
                "flat dense"
            ).set_enabled(bool(state["path"]))
            ui.space()
            ui.button(t(lang, "clear"), icon="close", on_click=do_close).props(
                "flat dense"
            ).style("color:#6b7280").mark("close-btn")


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
def _build_panel(key: str, state: dict, lang: str, doc, refresh_body: Callable) -> None:
    builders = {
        "format": _panel_format,
        "clean": _panel_clean,
        "normalize": _panel_normalize,
        "check": _panel_check,
        "verify": _panel_verify,
        "venues": _panel_venues,
    }
    builders[key](state, lang, doc, refresh_body)


def _preview_area(lang: str, state: dict, key: str, refresh_body) -> ui.column:
    col = ui.column().classes("hx-preview w-full gap-2").props(f'data-hx-scroll="preview-{key}"')
    saved = state["previews"].get(key)
    if saved and preview_is_current(state, saved["revision"], saved["before"]):
        _render_preview(col, state, lang, saved["before"], saved["result_text"],
                        refresh_body, opts=saved["opts"], label=saved["label"],
                        revision=saved["revision"])
        return col
    with col:
        with ui.card().classes("hx-card w-full p-5 gap-3"):
            _panel_head("preview", t(lang, "preview_title"), t(lang, "preview_hint"))
            with ui.element("div").classes("hx-empty"):
                ui.icon("difference", size="32px").style("color:#a5b4fc")
                ui.label(t(lang, "empty_preview"))
    return col


def _render_entry_cards(container: ui.column, result_text: str,
                        opts: Optional[FormatOptions], lang: str) -> None:
    container.clear()
    with container:
        try:
            doc = parse_bibtex(result_text)
        except Exception:  # noqa: BLE001 - display-only
            doc = None
        if doc is None or not doc.entries:
            ui.label(t(lang, "entries_view_empty")).classes("hx-tab-note")
            return
        ui.label(t(lang, "entries_view_note")).classes("hx-tab-note")
        for entry in doc.entries:
            try:
                text = render_entry(entry, opts or FormatOptions())
            except Exception:  # noqa: BLE001
                text = ""
            with ui.card().classes("hx-entry-card w-full p-3 gap-1"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(entry.key).style(
                        "font-family:ui-monospace,monospace;font-weight:700"
                    )
                    ui.label("@" + entry.type).classes("hx-tab-note")
                    title = (entry.get("title") or "")[:120]
                    if title:
                        ui.label(title).classes("text-sm").style("color:#334155")
                    ui.space()
                    ui.button(
                        icon="content_copy",
                        on_click=_copy_handler(text, lang),
                    ).props("flat dense size=sm").tooltip(
                        t(lang, "copy_entry")
                    ).mark(f"copy-{entry.key}")
                ui.html(f"<pre class='hx-pre'>{_esc(text)}</pre>")


def _render_preview(container: ui.column, state: dict, lang: str,
                    before: str, result_text: str, refresh_body=None,
                    opts: Optional[FormatOptions] = None,
                    label: str = "", revision: Optional[int] = None) -> None:
    revision = state["revision"] if revision is None else revision
    container.clear()
    with container:
        with ui.card().classes("hx-card w-full p-5 gap-3"):
            ui.label(t(lang, "preview_title")).classes("hx-panel-title").mark(
                "preview-title"
            )
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.html(_summary_line(before, result_text, lang))
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    view_toggle = ui.toggle(
                        {"list": t(lang, "view_list"),
                         "diff": t(lang, "view_diff"),
                         "entries": t(lang, "view_entries")},
                        value=state["drafts"].get("preview_view", "list"),
                    ).props(
                        "dense unelevated no-caps toggle-color=white "
                        "toggle-text-color=primary"
                    ).mark("view-mode")
                    mode_toggle = ui.toggle(
                        {"changed": t(lang, "changes_only"),
                         "all": t(lang, "show_all")},
                        value=state["drafts"].get("preview_mode", "changed"),
                    ).props(
                        "dense unelevated no-caps toggle-color=white "
                        "toggle-text-color=primary"
                    ).mark("diff-mode")

            content_container = ui.column().classes("hx-preview-content w-full")

            def render() -> None:
                view = view_toggle.value or "list"
                mode = mode_toggle.value or "changed"
                mode_toggle.set_enabled(view != "entries")
                content_container.clear()
                with content_container:
                    if view == "list":
                        ui.label(t(lang, "changed_entries")).style(
                            "font-size:12px;color:#4b5563;text-transform:uppercase;"
                            "letter-spacing:.05em;font-weight:600"
                        )
                        ui.html(changes_html(before, result_text, mode, lang))
                    elif view == "diff":
                        ui.label(t(lang, "diff_title")).style(
                            "font-size:12px;color:#4b5563;text-transform:uppercase;"
                            "letter-spacing:.05em;font-weight:600"
                        )
                        ui.html(entry_diff_html(before, result_text, mode, lang))
                    else:
                        _render_entry_cards(ui.column().classes("w-full gap-2"),
                                            result_text, opts, lang)

            view_toggle.on_value_change(lambda e: (state["drafts"].update(preview_view=e.value), render()))
            mode_toggle.on_value_change(lambda e: (state["drafts"].update(preview_mode=e.value), render()))
            render()

            with ui.row().classes("hx-preview-actions w-full justify-end gap-2 flex-wrap"):
                def apply_result() -> None:
                    if not preview_is_current(state, revision, before):
                        ui.notify(t(lang, "stale_preview"), type="warning")
                        return
                    if not _push_stage(state, label or t(lang, "applied"), result_text):
                        return
                    ui.notify(
                        t(lang, "step_applied").format(n=state["pointer"], label=label),
                        type="positive", position="bottom-right",
                    )
                    if refresh_body is not None:
                        refresh_body.refresh()

                def download_result() -> None:
                    _download(state, result_text, "edited.bib")

                ui.button(t(lang, "copy_result"), icon="content_copy",
                          on_click=_copy_handler(result_text, lang)).props(
                    "outline"
                ).mark("copy-result-btn")
                ui.button(t(lang, "download"), icon="download",
                          on_click=download_result).props("outline")
                ui.button(t(lang, "apply_btn"), icon="check",
                          on_click=apply_result).props(
                    "unelevated color=primary"
                ).mark("apply-btn").set_enabled(result_text != before)


# -- format -----------------------------------------------------------------
def _panel_format(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.element("div").classes("hx-operation-grid w-full"):
        with ui.card().classes("hx-card hx-options w-full p-5 gap-4").props(
            'data-hx-scroll="options-format"'
        ):
            _panel_head("brush", t(lang, "tab_format"), t(lang, "format_note"))
            ui.html(_steps_html(lang, ["steps_options", "steps_preview", "steps_apply"]))
            with ui.row().classes("gap-4 items-end flex-wrap"):
                indent = _bind(ui.select(
                    {"4": "4 spaces", "2": "2 spaces", "0": "0", "tab": "tab", "align": "align"},
                    value=_opt(state, "format", "indent") or "4",
                    label=t(lang, "opt_indent"),
                ).classes("w-36"), state, "format", "indent")
                field_order = _bind(ui.select(
                    {"standard": t(lang, "order_standard"),
                     "alphabetical": t(lang, "order_alpha"),
                     "keep": t(lang, "order_keep")},
                    value=_opt(state, "format", "field_order") or "standard",
                    label=t(lang, "opt_order"),
                ).classes("w-44"), state, "format", "field_order")
                sort = _bind(ui.select(
                    {"none": t(lang, "sort_none"), "key": "key", "year": "year",
                     "type": "type", "title": "title"},
                    value=_opt(state, "format", "sort") or "none",
                    label=t(lang, "opt_sort"),
                ).classes("w-36"), state, "format", "sort")
                align_equals = _bind(ui.switch(t(lang, "opt_align_equals"),
                                               value=bool(_opt(state, "format", "align_equals"))),
                                     state, "format", "align_equals")
                trailing = _bind(ui.switch(t(lang, "opt_trailing_comma"),
                                           value=bool(_opt(state, "format", "trailing_comma"))),
                                 state, "format", "trailing_comma")
                drop_comments = _bind(ui.switch(t(lang, "opt_drop_comments"),
                                                value=bool(_opt(state, "format", "drop_comments"))),
                                      state, "format", "drop_comments")

            ui.label(t(lang, "preview_hint")).classes("hx-tab-note")
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary"
            ).mark("format-preview")
        preview = _preview_area(lang, state, "format", refresh_body)

        async def run_format() -> None:
            opts = FormatOptions(
                indent=indent.value or "4",
                align_equals=bool(align_equals.value),
                field_order=field_order.value or "standard",
                sort_entries=sort.value or "none",
                trailing_comma=bool(trailing.value),
                drop_comments=bool(drop_comments.value),
            )
            await _run_preview_job(
                run_button, preview, state, lang, "format",
                lambda before: render_document(parse_bibtex(before), opts),
                refresh_body, opts, t(lang, "tab_format"),
            )

        run_button.on_click(run_format)


# -- clean ------------------------------------------------------------------
def _panel_clean(state: dict, lang: str, doc, refresh_body) -> None:
    common = ["abstract", "keywords", "url", "doi", "isbn", "issn", "month",
              "note", "address", "editor", "publisher", "eprint", "volume", "number"]
    with ui.element("div").classes("hx-operation-grid w-full"):
        with ui.card().classes("hx-card hx-options w-full p-5 gap-4").props(
            'data-hx-scroll="options-clean"'
        ):
            _panel_head("cleaning_services", t(lang, "tab_clean"), t(lang, "clean_note"))
            ui.html(_steps_html(lang, ["steps_options", "steps_preview", "steps_apply"]))

            ui.label(t(lang, "remove_fields")).classes("text-sm font-semibold mt-1")
            remove_select = _bind(ui.select(
                common, value=list(_opt(state, "clean", "remove") or []), multiple=True,
                label=t(lang, "remove_fields"),
            ).props("use-chips outlined dense").classes("w-full"),
                state, "clean", "remove")
            with ui.row().classes("gap-4 items-end flex-wrap"):
                custom_remove = ui.input(t(lang, "custom_remove")).classes("w-72")
                add_key = ui.input(t(lang, "add_key")).classes("w-44")
                add_value = ui.input(t(lang, "add_value")).classes("w-72")
                overwrite = ui.switch(t(lang, "overwrite"))
                for key, widget in (("custom_remove", custom_remove), ("add_key", add_key),
                                    ("add_value", add_value), ("overwrite", overwrite)):
                    _draft(widget, state, "clean_" + key, False if key == "overwrite" else "")
            with ui.row().classes("gap-6 flex-wrap mt-1"):
                drop_empty = _bind(ui.switch(t(lang, "drop_empty"),
                                             value=bool(_opt(state, "clean", "drop_empty"))),
                                   state, "clean", "drop_empty")
                drop_others = _bind(ui.switch(t(lang, "drop_others"),
                                              value=bool(_opt(state, "clean", "drop_others"))),
                                    state, "clean", "drop_others")
                months = _bind(ui.switch(t(lang, "months"),
                                         value=bool(_opt(state, "clean", "months"))),
                               state, "clean", "months")
                title_period = _bind(ui.switch(t(lang, "title_period"),
                                               value=bool(_opt(state, "clean", "title_period"))),
                                     state, "clean", "title_period")
                dedupe = _bind(ui.switch(t(lang, "dedupe"),
                                         value=bool(_opt(state, "clean", "dedupe"))),
                               state, "clean", "dedupe")
                keep_last = _bind(ui.switch(t(lang, "keep_last"),
                                            value=bool(_opt(state, "clean", "keep_last"))),
                                  state, "clean", "keep_last")

            ui.label(t(lang, "advanced")).classes("text-sm font-semibold mt-2")
            with ui.row().classes("gap-6 flex-wrap"):
                doi = _bind(ui.switch(t(lang, "opt_doi"),
                                      value=bool(_opt(state, "clean", "doi"))),
                            state, "clean", "doi")
                pages = _bind(ui.switch(t(lang, "opt_pages"),
                                        value=bool(_opt(state, "clean", "pages"))),
                              state, "clean", "pages")
                clean_urls = _bind(ui.switch(t(lang, "opt_clean_urls"),
                                             value=bool(_opt(state, "clean", "clean_urls"))),
                                   state, "clean", "clean_urls")
                dedupe_title = _bind(ui.switch(t(lang, "opt_dedupe_title"),
                                               value=bool(_opt(state, "clean", "dedupe_title"))),
                                     state, "clean", "dedupe_title")

            ui.label(t(lang, "preview_hint")).classes("hx-tab-note")
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary"
            ).mark("clean-preview")
        preview = _preview_area(lang, state, "clean", refresh_body)

        def compute(before: str, values: dict) -> str:
            work = parse_bibtex(before)
            names = list(values["remove_select"] or [])
            if values["custom_remove"]:
                names += [n.strip() for n in re.split(r"[,\s]+", values["custom_remove"]) if n.strip()]
            if names:
                cleaner.remove_fields(work, names)
            if values["add_key"] and values["add_value"]:
                cleaner.add_fields(work, {values["add_key"].strip(): values["add_value"]},
                                   overwrite=bool(values["overwrite"]))
            if values["drop_empty"]:
                cleaner.drop_empty_fields(work)
            if values["drop_others"]:
                cleaner.drop_others(work)
            if values["months"]:
                cleaner.normalize_months(work)
            if values["title_period"]:
                cleaner.strip_title_periods(work)
            if values["doi"]:
                cleaner.normalize_dois(work)
            if values["pages"]:
                cleaner.unify_page_ranges(work)
            if values["clean_urls"]:
                cleaner.strip_url_tracking(work)
            if values["dedupe"]:
                cleaner.dedupe_entries(work, keep="last" if values["keep_last"] else "first")
            if values["dedupe_title"]:
                cleaner.dedupe_by_title(work, keep="last" if values["keep_last"] else "first")
            return render_document(work, FormatOptions())

        async def run_clean() -> None:
            values = {key: widget.value for key, widget in {
                "remove_select": remove_select, "custom_remove": custom_remove,
                "add_key": add_key, "add_value": add_value, "overwrite": overwrite,
                "drop_empty": drop_empty, "drop_others": drop_others, "months": months,
                "title_period": title_period, "doi": doi, "pages": pages,
                "clean_urls": clean_urls, "dedupe": dedupe, "keep_last": keep_last,
                "dedupe_title": dedupe_title,
            }.items()}
            if bool((values["add_key"] or "").strip()) != bool(values["add_value"]):
                ui.notify(t(lang, "incomplete_field"), type="warning")
                return
            await _run_preview_job(
                run_button, preview, state, lang, "clean",
                lambda before: compute(before, values), refresh_body,
                FormatOptions(), t(lang, "tab_clean"),
            )

        run_button.on_click(run_clean)


# -- normalize --------------------------------------------------------------
def _panel_normalize(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.element("div").classes("hx-operation-grid w-full"):
        with ui.card().classes("hx-card hx-options w-full p-5 gap-4").props(
            'data-hx-scroll="options-normalize"'
        ):
            _panel_head("auto_fix_high", t(lang, "tab_normalize"), t(lang, "normalize_note"))
            ui.html(_steps_html(lang, ["steps_options", "steps_preview", "steps_apply"]))

            with ui.element("div").classes("hx-optbar w-full items-start"):
                with ui.column().classes("gap-1"):
                    ui.label(t(lang, "venue_style")).classes("hx-optlabel")
                    venue_style = _bind(ui.toggle(
                        {"abbr": t(lang, "style_abbr_short"),
                         "full": t(lang, "style_full_short"),
                         "family": t(lang, "style_family_short"),
                         "keep": t(lang, "style_keep_short")},
                        value=_opt(state, "normalize", "venue_style") or "abbr",
                    ).props(
                        "unelevated no-caps toggle-color=white toggle-text-color=primary"
                    ).mark("normalize-venue-style"),
                        state, "normalize", "venue_style")
                    example = ui.label("").classes("hx-style-ex")

                    def update_example() -> None:
                        style = venue_style.value or "abbr"
                        if style == "keep":
                            example.set_text(t(lang, "style_example_keep"))
                        else:
                            venue = get_venue("EMNLP")
                            example.set_text(t(lang, "style_example").format(
                                render_venue(venue, style=style)
                                if venue else "…"
                            ))

                    update_example()
                    venue_style.on_value_change(lambda _: update_example())

                    def goto_venues() -> None:
                        state["tab"] = "venues"
                        refresh_body.refresh()

                    ui.button(t(lang, "goto_venues"), icon="stadium",
                              on_click=goto_venues).props(
                        "flat dense no-caps"
                    ).classes("hx-lang self-start").style("margin-left:-8px")
                with ui.column().classes("gap-1"):
                    ui.label(t(lang, "arxiv_style")).classes("hx-optlabel")
                    arxiv_style = _bind(ui.toggle(
                        {"journal": t(lang, "arxiv_journal_short"),
                         "misc": t(lang, "arxiv_misc_short"),
                         "keep": t(lang, "style_keep_short")},
                        value=_opt(state, "normalize", "arxiv_style") or "journal",
                    ).props(
                        "unelevated no-caps toggle-color=white toggle-text-color=primary"
                    ).mark("normalize-arxiv-style"),
                        state, "normalize", "arxiv_style")
                with ui.column().classes("gap-2"):
                    ui.label(t(lang, "normalize_fixes")).classes("hx-optlabel")
                    fix_types = _bind(ui.switch(t(lang, "fix_types"),
                                                value=bool(_opt(state, "normalize", "fix_types"))),
                                      state, "normalize", "fix_types")
                    drop_volume = _bind(ui.switch(t(lang, "drop_arxiv_volume"),
                                                  value=bool(_opt(state, "normalize", "drop_arxiv_volume"))),
                                        state, "normalize", "drop_arxiv_volume")
                    add_url = _bind(ui.switch(t(lang, "add_arxiv_url"),
                                              value=bool(_opt(state, "normalize", "add_arxiv_url"))),
                                    state, "normalize", "add_arxiv_url")

            ui.label(t(lang, "preview_hint")).classes("hx-tab-note")
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary"
            ).mark("normalize-preview")
        preview = _preview_area(lang, state, "normalize", refresh_body)

        async def run_normalize() -> None:
            options = NormalizeOptions(
                venue_style=venue_style.value or "abbr",
                arxiv_style=arxiv_style.value or "journal",
                fix_entry_types=bool(fix_types.value),
                drop_arxiv_volume=bool(drop_volume.value),
                add_arxiv_url=bool(add_url.value),
            )

            def compute(before: str) -> str:
                work = parse_bibtex(before)
                normalize_document(work, options)
                return render_document(work, FormatOptions())

            await _run_preview_job(run_button, preview, state, lang, "normalize",
                                   compute, refresh_body, FormatOptions(),
                                   t(lang, "tab_normalize"))

        run_button.on_click(run_normalize)


# -- check ------------------------------------------------------------------
def _panel_check(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-5 gap-4"):
        _panel_head("rule", t(lang, "tab_check"), t(lang, "check_note"))
        ui.html(_steps_html(lang, ["steps_check_run", "steps_check_filter",
                                   "steps_check_detail"]))
        run_button = ui.button(t(lang, "run_check"), icon="rule").props(
            "unelevated color=primary"
        ).mark("run-check")
        filter_row = ui.row().classes("gap-2 items-center w-full")
        result_area = ui.column().classes("w-full")

        def render_report(report) -> None:
            result_area.clear()
            filter_row.clear()
            flt = state.get("check_filter", "ALL")
            counts = {
                "error": report.count("error"),
                "warning": report.count("warning"),
                "info": report.count("info"),
            }
            if not report.issues and flt != "ALL":
                flt = state["check_filter"] = "ALL"
            with filter_row:
                if report.issues:

                    def pick(value: str) -> None:
                        state["check_filter"] = value
                        render_report(report)

                    ui.button(
                        f"{t(lang, 'check_all')}  {len(report.issues)}",
                        on_click=lambda: pick("ALL"),
                    ).props("dense unelevated" if flt == "ALL" else "dense flat")
                    for sev in ("error", "warning", "info"):
                        if not counts[sev]:
                            continue
                        button = ui.button(
                            f"{t(lang, 'sev_' + sev)}  {counts[sev]}",
                            on_click=lambda v=sev: pick(v),
                        ).props("dense unelevated" if flt == sev else "dense flat")
                        button.style(f"color:{SEVERITY_COLOR[sev]}")

                    def download_json() -> None:
                        payload = {
                            "tool": "hx-bib",
                            "issues": [
                                {"severity": i.severity, "key": i.key,
                                 "field": i.field_name, "line": i.line,
                                 "message": i.message}
                                for i in report.sorted()
                            ],
                        }
                        ui.download(
                            json.dumps(payload, indent=2, ensure_ascii=False)
                            .encode("utf-8"),
                            f"{Path(state['name']).stem or 'bibliography'}.check.json",
                        )

                    ui.button(t(lang, "download_json"), icon="download",
                              on_click=download_json).props(
                        "dense outline"
                    ).style("margin-left:auto")
            with result_area:
                ui.html(check_html(report, lang, flt))

        if state.get("check_report") is not None:
            render_report(state["check_report"])
        else:
            with result_area:
                with ui.element("div").classes("hx-empty"):
                    ui.label(t(lang, "check_empty"))

        async def run_check() -> None:
            before, revision = state["text"], state["revision"]
            _busy(run_button, True)
            try:
                work = parse_bibtex(before)
                report = await asyncio.to_thread(
                    lambda: validate_document(work, ValidateOptions())
                )
                if preview_is_current(state, revision, before):
                    state["check_report"] = report
                    if not result_area.is_deleted:
                        render_report(report)
            except Exception as exc:
                ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
            finally:
                try:
                    _busy(run_button, False)
                except Exception:  # noqa: BLE001 - rebuilt while running
                    pass

        run_button.on_click(run_check)


# -- verify -----------------------------------------------------------------
def _panel_verify(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-5 gap-4"):
        _panel_head("travel_explore", t(lang, "tab_verify"), t(lang, "verify_note"))
        ui.html(_steps_html(lang, ["steps_verify_run", "steps_verify_review",
                                   "steps_verify_result"]))
        with ui.row().classes("gap-4 items-end flex-wrap"):
            only = _draft(ui.input(t(lang, "only_keys")).classes("w-80"),
                          state, "verify_keys")
            delay = _bind(ui.number(
                t(lang, "delay"), value=float(_opt(state, "verify", "delay")),
                min=0.0, step=0.1,
            ).classes("w-32"), state, "verify", "delay")
            workers = _bind(ui.number(
                t(lang, "workers"), value=int(_opt(state, "verify", "workers") or 1),
                min=1, max=8, step=1,
            ).classes("w-32").tooltip(t(lang, "workers_hint")),
                state, "verify", "workers")
            use_cache = _bind(ui.switch(t(lang, "use_cache"),
                                        value=bool(_opt(state, "verify", "use_cache"))),
                              state, "verify", "use_cache")

        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_label = ui.label(t(lang, "state_idle")).classes("hx-tab-note")
        with ui.row().classes("items-center gap-2"):
            run_button = ui.button(t(lang, "start_verify"), icon="travel_explore").props(
                "unelevated color=primary"
            ).mark("verify-start")
            pause_button = ui.button(t(lang, "pause"), icon="pause").props(
                "outline"
            ).mark("verify-pause")
            stop_button = ui.button(t(lang, "stop"), icon="stop").props(
                "outline color=red-7"
            ).mark("verify-stop")
        pause_button.set_enabled(state["progress"]["running"])
        stop_button.set_enabled(state["progress"]["running"])
        _busy(run_button, state["progress"]["running"])
        if state["progress"]["paused"]:
            pause_button.set_text(t(lang, "resume"))

        filter_row = ui.row().classes("gap-2 items-center")
        results_area = ui.column().classes("w-full")

        def show_results() -> None:
            results = state["verify_results"]
            results_area.clear()
            filter_row.clear()
            if not results:
                with results_area:
                    with ui.element("div").classes("hx-empty"):
                        ui.label(t(lang, "verify_empty"))
                return
            counts: Dict[str, int] = {}
            for res in results:
                counts[res.verdict] = counts.get(res.verdict, 0) + 1
            with filter_row:
                def pick(value: str) -> None:
                    state["verify_filter"] = value
                    show_results()

                ui.button(f"ALL {len(results)}", on_click=lambda: pick("ALL")).props(
                    "dense flat" if state["verify_filter"] != "ALL" else "dense unelevated"
                )
                for verdict in ("FAIL", "WARN", "MINOR", "INFO", "OK"):
                    if not counts.get(verdict):
                        continue
                    color = VERDICT_COLOR[verdict]
                    active = state["verify_filter"] == verdict
                    button = ui.button(
                        f"{t(lang, 'verdict_' + verdict)} {counts[verdict]}",
                        on_click=lambda v=verdict: pick(v),
                    ).props(
                        f"dense {'unelevated' if active else 'flat'} "
                        f"color={'red' if verdict == 'FAIL' else 'orange' if verdict == 'WARN' else 'blue-grey'}"
                    )
                    button.style(f"color:{color}")

                def download_report() -> None:
                    stem = Path(state["name"]).stem or "bibliography"
                    ui.download(
                        build_verify_html(results, state["name"] or "bibliography")
                        .encode("utf-8"),
                        f"{stem}.report.html",
                    )

                ui.button(t(lang, "download_report"), icon="download",
                          on_click=download_report).props(
                    "dense outline"
                ).style("margin-left:auto")
            with results_area:
                ui.html(verify_html(results, state["verify_filter"], lang))

        show_results()

        async def run_verify() -> None:
            if state["progress"]["running"]:
                return
            before, revision = state["text"], state["revision"]
            keys = [key for key in re.split(r"[,\s]+", (only.value or "").strip()) if key]
            entries = list(parse_bibtex(before).entries)
            if keys:
                missing = set(keys) - {e.key for e in entries}
                if missing:
                    ui.notify(t(lang, "unknown_keys").format(keys=", ".join(sorted(missing))),
                              type="warning")
                    return
                entries = [e for e in entries if e.key in set(keys)]
            if not entries:
                ui.notify(t(lang, "no_results"), type="warning", position="bottom-right")
                return

            n_workers = max(1, min(8, int(workers.value or 1)))
            control = RunControl()
            state["run_control"] = control
            progress = state["progress"] = {
                "done": 0, "total": len(entries), "key": "",
                "running": True, "paused": False,
            }
            state["verify_results"] = None
            results_area.clear()
            _busy(run_button, True)
            pause_button.enable()
            stop_button.enable()
            pause_button.set_text(t(lang, "pause"))

            net = Net(delay=max(0.0, float(delay.value if delay.value is not None else 0.8)),
                      use_cache=bool(use_cache.value))

            def cb(done: int, total: int, citekey: str) -> None:
                progress.update(done=done, total=total, key=citekey)

            def work() -> list:
                prefetch_arxiv(net, entries)
                results = verify_entries(net, entries, progress=cb,
                                         workers=n_workers, control=control)
                pending = [r for r in results if r.any_error and r.verdict != "OK"]
                if pending and not control.cancelled.is_set():
                    results = retry_failed(net, results, control=control)
                return results

            try:
                results = await asyncio.to_thread(work)
            except Exception as exc:
                if preview_is_current(state, revision, before):
                    ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
                return
            finally:
                progress["running"] = False
                progress["paused"] = False
                if state["run_control"] is control:
                    state["run_control"] = None
                try:  # widgets may have been rebuilt (language switch) mid-run
                    _busy(run_button, False)
                    pause_button.disable()
                    stop_button.disable()
                    pause_button.set_text(t(lang, "pause"))
                except Exception:  # noqa: BLE001
                    pass

            if not preview_is_current(state, revision, before):
                return
            state["verify_results"] = results
            state["verify_filter"] = "ALL"
            if control.cancelled.is_set():
                ui.notify(t(lang, "stopped_partial").format(n=len(results)),
                          type="warning", position="bottom-right")
            else:
                counts: Dict[str, int] = {}
                for res in results:
                    counts[res.verdict] = counts.get(res.verdict, 0) + 1
                color = "negative" if counts.get("FAIL") else "positive"
                ui.notify(
                    t(lang, "verify_done").format(
                        n=len(results),
                        bad=counts.get("FAIL", 0) + counts.get("WARN", 0),
                    ),
                    type=color, position="bottom-right",
                )
            show_results_safe()

        def show_results_safe() -> None:
            if not results_area.is_deleted:
                show_results()
            else:
                refresh_body.refresh()

        run_button.on_click(run_verify)

        def toggle_pause() -> None:
            control = state["run_control"]
            if control is None:
                return
            if control.paused.is_set():
                control.resume()
                state["progress"]["paused"] = False
                pause_button.set_text(t(lang, "pause"))
            else:
                control.pause()
                state["progress"]["paused"] = True
                pause_button.set_text(t(lang, "resume"))

        def stop_run() -> None:
            control = state["run_control"]
            if control is not None:
                control.cancel()

        pause_button.on_click(toggle_pause)
        stop_button.on_click(stop_run)

        # Progress polling (network work runs in worker threads).
        def poll_progress() -> None:
            progress = state["progress"]
            pause_button.set_enabled(progress["running"])
            stop_button.set_enabled(progress["running"])
            pause_button.set_text(t(lang, "resume" if progress["paused"] else "pause"))
            _busy(run_button, progress["running"])
            if progress["total"]:
                progress_bar.set_value(progress["done"] / progress["total"])
            if progress["running"]:
                state_label = t(lang, "state_paused" if progress["paused"] else "state_running")
                progress_label.set_text(
                    f"{progress['done']}/{progress['total']} · {state_label}"
                    + (f" · {progress['key']}" if progress.get("key") else "")
                )
            elif state["verify_results"] is not None:
                progress_label.set_text(t(lang, "state_idle"))
            else:
                progress_bar.set_value(0)
                progress_label.set_text(t(lang, "state_idle"))

        ui.timer(0.4, poll_progress)


# -- venues -----------------------------------------------------------------
def _venue_usage(doc) -> dict:
    """Count how many entries in the loaded file use each venue preset."""
    counts: dict = {}
    if doc is None:
        return counts
    for entry in doc.entries:
        for field in ("booktitle", "journal"):
            hit = match_venue(entry.get(field) or "")
            if hit:
                counts[hit[0].abbr] = counts.get(hit[0].abbr, 0) + 1
                break
    return counts


def _venue_card(venue, lang: str, used: int = 0) -> None:
    with ui.card().classes("hx-card hx-venue-card p-3 gap-2"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.label(venue.abbr).style(
                "font-family:ui-monospace,monospace;font-weight:800;font-size:15px"
            )
            if used:
                ui.label(t(lang, "venue_in_doc").format(n=used)).classes(
                    "hx-chip"
                ).style("background:#ecfdf5;color:#047857")
            ui.space()
            ui.label(venue.family).classes("hx-chip").style(
                "background:#eef2ff;color:#4338ca"
            )
            ui.label(venue.kind).classes("hx-chip").style(
                "background:#f1f5f9;color:#475569"
            )
        ui.label(venue.name).classes("text-xs").style("color:#475569;line-height:1.45")
        for style, short_key, copy_key, mk in (
            ("abbr", "style_abbr_short", "venue_copy_abbr", "abbr"),
            ("full", "style_full_short", "venue_copy_full", "full"),
            ("family", "style_family_short", "venue_copy_family", "family"),
        ):
            rendered = render_venue(venue, style=style)
            with ui.row().classes("hx-vrow w-full no-wrap"):
                ui.label(t(lang, short_key)).classes("hx-vrow-label")
                ui.label(rendered).classes("hx-vrow-val truncate")
                ui.space()
                ui.button(
                    icon="content_copy",
                    on_click=_copy_handler(rendered, lang),
                ).props("flat dense size=sm round").tooltip(t(lang, copy_key)).mark(
                    f"venue-{venue.abbr}-{mk}"
                )


def _panel_venues(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-5 gap-4"):
        _panel_head("event", t(lang, "tab_venues"), t(lang, "venues_note"))

        usage = _venue_usage(doc)
        offline = state.get("venues_only") or doc is None
        if offline:
            with ui.element("div").classes("hx-vaction w-full"):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.icon("auto_fix_high").style("font-size:18px")
                    ui.label(t(lang, "venues_norm_offline")).classes(
                        "text-sm"
                    ).style("color:#3730a3")
        else:
            with ui.element("div").classes("hx-vaction w-full"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.icon("auto_fix_high").style("font-size:18px")
                    ui.label(t(lang, "venues_norm_title")).style(
                        "font-weight:700;color:#312e81;font-size:14px"
                    )
                    ui.space()

                    def goto_normalize() -> None:
                        state["tab"] = "normalize"
                        refresh_body.refresh()

                    ui.button(t(lang, "goto_normalize"), icon="tune",
                              on_click=goto_normalize).props(
                        "flat dense no-caps"
                    ).classes("hx-lang")
                ui.label(t(lang, "venues_norm_note")).classes("text-xs").style(
                    "color:#4c4f8a"
                )
                with ui.row().classes("w-full items-center gap-3 flex-wrap"):
                    ui.label(t(lang, "venue_style")).classes("hx-optlabel")
                    venue_style = _bind(ui.toggle(
                        {"abbr": t(lang, "style_abbr_short"),
                         "full": t(lang, "style_full_short"),
                         "family": t(lang, "style_family_short")},
                        value=_opt(state, "normalize", "venue_style") or "abbr",
                    ).props(
                        "unelevated no-caps toggle-color=white toggle-text-color=primary"
                    ).mark("venue-norm-style"),
                        state, "normalize", "venue_style")
                    ui.space()
                    run_button = ui.button(
                        t(lang, "venues_norm_btn"), icon="preview",
                    ).props("unelevated color=primary").mark("venue-normalize-preview")
            normalize_preview = _preview_area(lang, state, "venues", refresh_body)

            async def run_venue_normalize() -> None:
                style = venue_style.value or "abbr"

                def compute(before: str) -> str:
                    work = parse_bibtex(before)
                    normalize_document(work, NormalizeOptions(
                        venue_style=style, arxiv_style="keep", fix_entry_types=True,
                        drop_arxiv_volume=False, add_arxiv_url=False,
                    ))
                    return render_document(work, FormatOptions())

                await _run_preview_job(
                    run_button, normalize_preview, state, lang, "venues", compute,
                    refresh_body, FormatOptions(), t(lang, "venues_norm_label"),
                )

            run_button.on_click(run_venue_normalize)

        with ui.row().classes("gap-3 items-end flex-wrap"):
            query = _draft(ui.input(t(lang, "search_venues")).classes("w-64").mark("venue-search"),
                           state, "venue_query")
            family = _draft(ui.select(["ALL", *families()], value="ALL",
                                     label=t(lang, "family")).classes("w-40"),
                            state, "venue_family", "ALL")
        count_label = ui.label("").classes("hx-tab-note")
        grid_area = ui.column().classes("w-full")

        def render() -> None:
            venues = search_venues(query.value) if query.value else list(VENUES)
            if family.value and family.value != "ALL":
                venues = [v for v in venues if v.family == family.value]
            count_label.set_text(t(lang, "venues_count").format(n=len(venues)))
            grid_area.clear()
            with grid_area:
                if not venues:
                    with ui.element("div").classes("hx-empty"):
                        ui.label(t(lang, "no_venues"))
                    return
                with ui.element("div").classes("w-full").style(
                    "display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));"
                    "gap:12px"
                ):
                    for venue in venues:
                        _venue_card(venue, lang, usage.get(venue.abbr, 0))

        query.on_value_change(lambda _: render())
        family.on_value_change(lambda _: render())
        render()
