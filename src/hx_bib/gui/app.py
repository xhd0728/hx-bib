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
import difflib
import html
import inspect
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from itertools import zip_longest
from pathlib import Path
from threading import Lock
from typing import Callable, List, Optional

from nicegui import events, ui

from ..core import cleaner
from ..core.diff import (
    diff_documents,
    diff_lines,
    entry_identity,
    match_by_key,
    split_entries,
)
from ..core.identifiers import extract_arxiv_id
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
from ..core.normalize import (
    NormalizeOptions,
    entry_venue_presets,
    normalize_document,
    normalize_entry,
)
from ..core.parser import parse_bibtex
from ..core.validator import ValidateOptions, validate_document
from ..core.venues import (
    CONFERENCE,
    JOURNAL,
    VENUES,
    families,
    get_venue,
    render_venue,
    search_venues,
)
from ..core.writer import FormatOptions, render_document
from ..verify import Net, RunControl, prefetch_arxiv, retry_failed, verify_entries
from ..verify.report import build_html as build_verify_html
from .diagnostics import issue_text
from .i18n import t
from .styles import CSS
from .workspace import (
    DEFAULT_SETTINGS,
    invalidate_results,
    preview_is_current,
)
from .workspace import (
    jump_stage as _jump_stage,
)
from .workspace import (
    merge_settings as _merge_settings,
)
from .workspace import (
    new_state as _new_state,
)
from .workspace import (
    push_stage as _push_stage,
)

SEVERITY_COLOR = {"error": "#dc2626", "warning": "#d97706", "info": "#2563eb"}
VERDICT_COLOR = {
    "FAIL": "#b91c1c", "WARN": "#92400e", "MINOR": "#854d0e",
    "INFO": "#1d4ed8", "OK": "#166534",
}

_IN_SIM = bool(os.environ.get("NICEGUI_USER_SIMULATION"))
_SETTINGS_KEY = "hx-bib-settings"
_TABLE_UPLOAD_LIMIT = 2_000_000
_CODE_PREVIEW_LINES = 500

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
        i.setAttribute('aria-label', i.closest('.hx-table-upload')
          ? '选择 .tex 文件 / Choose a .tex file'
          : '选择 BibTeX 文件 / Choose BibTeX file');
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
    if (el.matches?.('.hx-code')) {
      for (const peer of el.closest('.hx-code-pair').querySelectorAll('.hx-code')) {
        if (peer !== el && peer.scrollTop !== el.scrollTop) peer.scrollTop = el.scrollTop;
      }
    }
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
    _update_preview_controls(state)


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


def _preview_config(state: dict, key: str) -> dict:
    config = deepcopy(state["settings"].get(key, {}))
    if key == "clean":
        config["drafts"] = {k: v for k, v in state["drafts"].items() if k.startswith("clean_")}
    return config


def _update_preview_controls(state: dict) -> None:
    for update in state.get("preview_controls", {}).values():
        update()


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
    def span_identity(span):
        text = "\n".join(span.lines)
        entries = parse_bibtex(text).entries
        return entry_identity(entries[0]) if entries else text

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

    pairs = match_by_key(
        [sp for sp in b_spans if sp.key is not None],
        [sp for sp in a_spans if sp.key is not None],
        key=lambda sp: sp.key, identity=span_identity,
    )
    for sp, a in pairs:
        if sp is None:
            blocks.append(block(_esc(a.key), "added",
                                diff_lines("", "\n".join(a.lines), 0, a.start)))
            continue
        left_text = "\n".join(sp.lines)
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
    return sig_l is not None and sig_l == sig_r


def table_result_html(result_text: str, lang: str) -> str:
    return (
        f'<pre class="hx-pre hx-table-code" tabindex="0" '
        f'aria-label="{_esc(t(lang, "table_result_title"))}">{_esc(result_text)}</pre>'
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
            note = t(lang, "table_same_note")
        else:
            note = t(lang, "table_layout_note" if layout_only else "table_content_note")
        opened = " open" if changed else ""
        blocks.append(
            f'<details class="hx-tblock hx-tcompare"{opened}>{head}'
            f'<div class="hx-tblock-body"><div class="hx-tpanes">'
            f'<div class="hx-tpane">'
            f'<span class="hx-tpane-label hx-tpane-before">'
            f'<span class="material-icons">remove</span>'
            f'{_esc(t(lang, "table_before"))}</span>'
            f'<pre class="hx-pre" tabindex="0">{_esc(left)}</pre></div>'
            f'<div class="hx-tpane">'
            f'<span class="hx-tpane-label hx-tpane-after">'
            f'<span class="material-icons">check</span>'
            f'{_esc(t(lang, "table_after"))}</span>'
            f'<pre class="hx-pre" tabindex="0">{_esc(right)}</pre></div>'
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
        message, hint = issue_text(issue, lang)
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
            f'<span class="hx-issue-msg" title="{_esc(issue.message)}">{_esc(message)}</span>'
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
            f'{_esc(hint)}</div>'
            f"</div></details>"
        )
    return "".join(cards)


def verify_html(results, verdict_filter: str, lang: str) -> str:
    order = {verdict: rank for rank, verdict in enumerate(VERDICT_COLOR)}
    shown = [r for r in results if verdict_filter in ("ALL", r.verdict)]
    shown.sort(key=lambda r: (order[r.verdict], r.entry.line))
    if not shown:
        return f'<div class="hx-tab-note">{_esc(t(lang, "verify_no_match"))}</div>'
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
                f"{_esc(r.title)}{f' ({_esc(r.year)})' if r.year else ''}"
                f"{(' · ' + _esc(r.venue)) if r.venue and r.source != 'arXiv' else ''}</div>"
                for r in records
            )
            evidence = f'<div class="hx-evidence"><b>{_esc(t(lang, "verify_evidence"))}</b>{items}</div>'
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
                + '</b><div class="hx-verify-authors">'
                f"<div><div class='hx-tab-note'>{_esc(t(lang, 'your_bib'))}</div>{names(ad['bib'])}</div>"
                f"<div><div class='hx-tab-note'>{_esc(ad['source'])}</div>{names(ad['src'])}</div>"
                "</div></div>"
            )
        publication = _arxiv_publication_item(res, lang)
        blocks.append(
            f'<details class="hx-issue-card hx-verify-entry" style="border-left-color:{color}">'
            f"<summary>"
            f'<span class="hx-chip" style="color:{color};background:{color}18">{_esc(label)}</span>'
            f'<code class="hx-issue-key">{_esc(res.entry.key)}</code>'
            f'<span class="hx-issue-meta">@{_esc(res.entry.type)} · L{res.entry.line}</span>'
            f'<span class="material-icons hx-issue-caret" aria-hidden="true">expand_more</span>'
            f'<span class="hx-verify-entry-title">{_esc(res.entry.get("title") or "")}</span>'
            f'</summary><div class="hx-verify-details">{publication}{issues}{diff_block}{evidence}</div></details>'
        )
    return "".join(blocks)


def _arxiv_publication_item(result, lang: str) -> str:
    if not result.arxiv_id:
        return ""
    status, evidence = result.publication or ("unavailable", "")
    return (
        '<div class="hx-publication-item">'
        f'<code>{_esc(result.entry.key)}</code> · arXiv:{_esc(result.arxiv_id)}'
        f'<div><strong>{_esc(t(lang, "arxiv_status_" + status))}</strong>'
        f'{(" · " + _esc(evidence)) if evidence else ""}</div></div>'
    )


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
    if button.is_deleted:
        return
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
    config = _preview_config(state, key)
    token = object()
    state["preview_jobs"][key] = token
    _busy(button, True)
    try:
        result = await asyncio.to_thread(compute, before)
    except Exception as exc:  # surface failures without losing the working copy
        if preview_is_current(state, revision, before):
            ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
        return
    finally:
        current_job = state["preview_jobs"].get(key) is token
        if current_job:
            state["preview_jobs"].pop(key)
            _busy(button, False)
    if not current_job or not preview_is_current(state, revision, before):
        return
    state["previews"][key] = {
        "before": before, "result_text": result, "opts": opts, "label": label,
        "revision": revision,
        "key": key, "config": config,
    }
    if not container.is_deleted:
        _render_preview(container, state, lang, before, result, refresh_body,
                        opts=opts, label=label, revision=revision, key=key, config=config)
    elif state["mode"] == "tools" and state["tab"] == key:
        refresh_body.refresh()


def _draft(widget, state: dict, key: str, default=""):
    """Keep unfinished input in memory across mode/language/history refreshes."""
    widget.value = state["drafts"].get(key, default)
    def changed(e) -> None:
        state["drafts"][key] = e.value
        if key.startswith("clean_"):
            _update_preview_controls(state)

    widget.on_value_change(changed)
    return widget


def _header(lang: str, active: str, on_lang: Callable, on_switch: Callable) -> dict:
    """Sticky app header shared by both modes (“tools” or “table” active).

    The mode switch is a segmented control in the centre — BibTeX 工具 ⇄
    LaTeX 表格 — rendered as plain buttons that flip an in-page state flag,
    so switching never reloads the app (no splash reflash, no layout jump).
    """
    sub = t(lang, "table_title") if active == "table" else t(lang, "subtitle")
    controls = {"buttons": {}}
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
                    controls["subtitle"] = ui.label(sub).classes("hx-brand-sub")
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
                    controls["buttons"][key] = btn
                    btn.on("click", lambda k=key: on_switch(k))
                    with btn:
                        ui.icon(icon).classes("hx-seg-ico")
                        ui.label(t(lang, label_key))
            ui.button("中 / EN", on_click=on_lang).props(
                "flat dense no-caps "
                "aria-label='中 / EN — 切换语言 / Switch language'"
            ).classes("hx-lang")
    return controls


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
    workspaces = {}
    mounted = set()
    ui.context.client.on_delete(lambda: invalidate_results(state))

    def refresh_workspaces() -> None:
        for mode in mounted:
            _, render = workspaces[mode]
            render.refresh()

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
                refresh_workspaces()
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
        refresh_workspaces()

    def show_workspace(mode: str) -> None:
        if mode not in mounted:
            slot, render = workspaces[mode]
            with slot:
                render()
            mounted.add(mode)
        for name, (slot, _) in workspaces.items():
            slot.set_visibility(name == mode)

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
        header_widgets["subtitle"].set_text(t(
            state["lang"], "table_title" if mode == "table" else "subtitle",
        ))
        for key, button in header_widgets["buttons"].items():
            button.classes(add="hx-seg-active" if key == mode else "",
                           remove="hx-seg-active" if key != mode else "")
            if key == mode:
                button.props('aria-current="page"')
            else:
                button.props(remove="aria-current")
        show_workspace(mode)

    with ui.element("div").classes("hx-page w-full"):
        header_widgets = {}

        @ui.refreshable
        def header() -> None:
            header_widgets.update(_header(state["lang"], state["mode"], toggle_lang, switch_mode))
        header()

        with ui.element("main").classes("hx-main w-full mx-auto px-4 py-4"):
            @ui.refreshable
            def body() -> None:
                lang = state["lang"]
                with ui.element("div").classes("hx-body w-full").props(
                    'data-hx-scroll="root-tools"'
                ):
                    if state["venues_only"]:
                        _render_venues_only(state, lang, body)
                        return
                    if not state["text"]:
                        _render_upload(state, lang, body)
                        return

                    _render_bib_workbench(state, lang, parse_bibtex(state["text"]), body)

            @ui.refreshable
            def table_body() -> None:
                with ui.element("div").classes("hx-body w-full").props('data-hx-scroll="root-table"'):
                    _render_table_page(state, state["lang"])

            workspaces.update(
                tools=(ui.element("div").classes("hx-mode-slot"), body),
                table=(ui.element("div").classes("hx-mode-slot"), table_body),
            )
            show_workspace(initial_mode)


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
    with ui.card().classes("hx-card hx-workbench hx-import-workbench w-full"):
        _panel_head("description", t(lang, "upload_title"), t(lang, "import_safe"))
        with ui.element("div").classes("hx-workbench-grid w-full"):
            with ui.element("aside").classes("hx-workbench-rail"):
                async def on_upload(e: events.UploadEventArguments) -> None:
                    data = e.file.read()
                    if inspect.isawaitable(data):
                        data = await data
                    _load_text(state, body, lang, _read_file_bytes(data), e.file.name or "uploaded.bib")

                ui.upload(
                    label=t(lang, "upload_button"), on_upload=on_upload,
                    on_rejected=lambda: ui.notify(t(lang, "upload_rejected"), type="warning"),
                    auto_upload=True, max_file_size=20_000_000,
                ).props('accept=".bib,.bibtex,text/plain" flat').classes("hx-uploader hx-compact-upload")
                ui.button(t(lang, "try_example"), icon="science", on_click=lambda: _load_text(
                    state, body, lang, BIB_EXAMPLE, "example.bib",
                )).props("outline color=primary no-caps").mark("bib-example")

                with ui.expansion(t(lang, "import_path"), icon="folder_open").classes(
                    "hx-workbench-disclosure w-full"
                ).mark("import-path"):
                    ui.label(t(lang, "import_path_hint")).classes("hx-tab-note")
                    path_input = _draft(ui.input(placeholder=t(lang, "path_placeholder")).props(
                        f'aria-label="{t(lang, "or_path")}"'
                    ).classes("w-full").mark("path-input"), state, "import_path")

                    def load_path() -> None:
                        raw = (path_input.value or "").strip().strip('"')
                        if not raw:
                            ui.notify(t(lang, "or_path"), type="warning", position="bottom-right")
                            return
                        try:
                            path = Path(raw).expanduser()
                            text = _read_file_bytes(path.read_bytes())
                        except OSError as exc:
                            ui.notify(str(exc), type="negative", position="bottom-right")
                            return
                        _load_text(state, body, lang, text, path.name, str(path))

                    ui.button(t(lang, "load"), icon="folder_open", on_click=load_path).props(
                        "outline color=primary no-caps"
                    ).mark("load-btn")
                    path_input.on("keydown.enter", load_path)

                ui.button(t(lang, "browse_venues"), icon="stadium", on_click=lambda: (
                    state.update(venues_only=True), body.refresh(),
                )).props("flat color=primary no-caps").mark("browse-venues")

            with ui.element("section").classes("hx-workbench-output"):
                ui.label(t(lang, "paste_title")).classes("hx-panel-title")
                paste_area = _draft(ui.textarea(
                    placeholder=t(lang, "paste_placeholder"),
                ).props(f'outlined rows=10 aria-label="{t(lang, "paste_placeholder")}"').classes(
                    "hx-table-editor w-full"
                ).mark("paste-input"), state, "import_text")

                def import_paste() -> None:
                    raw = (paste_area.value or "").strip()
                    if not raw:
                        ui.notify(t(lang, "paste_empty"), type="warning", position="bottom-right")
                        return
                    _load_text(state, body, lang, raw + "\n", "pasted.bib")

                ui.button(t(lang, "paste_import"), icon="input", on_click=import_paste).props(
                    "unelevated color=primary no-caps"
                ).classes("self-start").mark("paste-load")
                paste_area.on("keydown.ctrl.enter.prevent", import_paste)


def _render_venues_only(state: dict, lang: str, body) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.button(t(lang, "back"), icon="arrow_back", on_click=lambda: (
            state.update(venues_only=False), body.refresh(),
        )).props("flat dense").mark("venues-back")
    with ui.element("section").classes("hx-panel-scroll hx-venue-standalone w-full").props(
        'data-hx-scroll="panel-venues-only"'
    ):
        with ui.card().classes("hx-card w-full p-4 gap-3"):
            _panel_venues(state, lang, None, body)


# ---------------------------------------------------------------------------
# LaTeX table formatter page (/table)
# ---------------------------------------------------------------------------
def _render_table_page(state: dict, lang: str) -> None:
    running = False
    review_controls = []

    def options() -> TableFormatOptions:
        return TableFormatOptions(
            amp_style=state["amp"],
            blank_lines_around_rules=state["blank_lines"],
            row_indent=state["row_indent"],
        )

    def matches_input() -> bool:
        result = state["result"]
        return (
            result is not None and result[0] == state["source_text"]
            and state["result_options"] == options()
        )

    def export_text() -> Optional[str]:
        if matches_input() and not running and state["result"][1].tables:
            return state["result"][1].text
        return None

    async def copy_result() -> None:
        if (text := export_text()) is not None:
            await _copy_text(text, lang)

    def download_result() -> None:
        if (text := export_text()) is not None:
            name = state["table_source_name"]
            filename = f"{Path(name).stem}.formatted.tex" if name else "tables.tex"
            ui.download(text.encode("utf-8"), filename)

    def update_controls() -> None:
        fmt_button.set_enabled(not running and bool(state["source_text"].strip()))
        allowed = export_text() is not None
        copy.set_enabled(allowed)
        download.set_enabled(allowed)
        for update in review_controls:
            update()
        stored = state["result"]
        if running:
            key = "table_running"
        elif stored is None:
            key = "table_empty_preview"
        elif not matches_input():
            key = "table_stale"
        elif not stored[1].tables:
            key = "table_no_tables"
        else:
            key = "table_unchanged" if stored[0] == stored[1].text else "table_current"
        status.set_text(t(lang, key))

    def update_input(key: str, value) -> None:
        state[key] = value
        update_controls()

    def replace_source(text: str, name: str = "") -> None:
        state.update(source_text=text, table_source_name=name)
        source.value = text
        source_name.set_text(t(lang, "table_imported").format(name=name) if name else t(lang, "table_source_hint"))
        update_controls()

    async def import_tex(e: events.UploadEventArguments) -> None:
        value_before = source.value
        try:
            if Path(e.file.name).suffix.lower() != ".tex" or e.file.size() > _TABLE_UPLOAD_LIMIT:
                ui.notify(t(lang, "table_upload_rejected"), type="warning")
                return
            data = e.file.read()
            if inspect.isawaitable(data):
                data = await data
            if source.is_deleted or source.value != value_before:
                return
            text = _read_file_bytes(data)
            if not text.strip() or "\x00" in text:
                ui.notify(t(lang, "table_bad_file"), type="warning")
                return
            replace_source(text, e.file.name)
        except OSError:
            ui.notify(t(lang, "table_read_failed"), type="negative")
        finally:
            if not e.sender.is_deleted:
                e.sender.reset()

    async def run_format() -> None:
        nonlocal running
        text = state["source_text"]
        if running or not text.strip():
            return
        opts = options()
        running = True
        _busy(fmt_button, True)
        update_controls()
        try:
            result = await asyncio.to_thread(format_tables, text, opts)
            if not fmt_button.is_deleted:
                state.update(result=(text, result), result_options=opts)
                render_result()
        except Exception as exc:
            if not fmt_button.is_deleted:
                ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
        finally:
            running = False
            if not fmt_button.is_deleted:
                _busy(fmt_button, False)
                update_controls()

    with ui.card().classes("hx-card hx-workbench w-full"):
        _panel_head("grid_on", t(lang, "table_title"), t(lang, "table_note"))
        with ui.element("div").classes("hx-workbench-grid w-full"):
            with ui.element("aside").classes("hx-workbench-rail").props('data-hx-scroll="options-table"'):
                ui.label(t(lang, "table_paste_title")).classes("hx-optlabel")
                with ui.element("div").classes("hx-table-source-actions w-full"):
                    ui.upload(
                        label=t(lang, "table_import"),
                        on_upload=import_tex,
                        on_rejected=lambda: ui.notify(t(lang, "table_upload_rejected"), type="warning"),
                        auto_upload=True, max_file_size=_TABLE_UPLOAD_LIMIT,
                    ).props('accept=".tex" flat').classes("hx-uploader hx-table-upload").mark("table-import")
                    ui.button(t(lang, "table_example"), icon="auto_awesome",
                              on_click=lambda: replace_source(TABLE_EXAMPLE)).props(
                        "outline color=primary no-caps"
                    ).mark("table-example")
                imported = state["table_source_name"]
                source_name = ui.label(
                    t(lang, "table_imported").format(name=imported) if imported else t(lang, "table_source_hint")
                ).classes("hx-tab-note hx-table-source").mark("table-source")
                source = ui.textarea(
                    placeholder=t(lang, "table_paste_placeholder"),
                    value=state["source_text"],
                ).classes("hx-table-editor w-full").props(
                    f'outlined rows=7 wrap=off aria-label="{t(lang, "table_paste_title")}"'
                ).mark("table-input")
                source.on_value_change(lambda e: update_input("source_text", e.value or ""))
                source.on("keydown.ctrl.enter.prevent", run_format)

                ui.select(
                    {"space": t(lang, "table_amp_space"),
                     "align": t(lang, "table_amp_align"),
                     "none": t(lang, "table_amp_none")},
                    value=state["amp"], label=t(lang, "table_amp"),
                    on_change=lambda e: update_input("amp", e.value or "space"),
                ).classes("w-full").mark("table-amp")
                fmt_button = ui.button(
                    t(lang, "table_format_btn"), icon="auto_fix_high", on_click=run_format,
                ).props("unelevated color=primary no-caps").classes("w-full").mark("table-format")
                fmt_button.tooltip("Ctrl + Enter")
                with ui.expansion(t(lang, "workbench_advanced"), icon="tune").classes(
                    "hx-workbench-disclosure w-full"
                ).mark("table-advanced"), ui.column().classes("w-full gap-2"):
                    ui.number(
                        t(lang, "table_indent_label"), value=state["row_indent"],
                        min=0, max=16, step=1, precision=0,
                        on_change=lambda e: update_input("row_indent", max(0, min(16, int(e.value or 0)))),
                    ).classes("w-full").mark("table-indent")
                    ui.switch(
                        t(lang, "table_blank"), value=state["blank_lines"],
                        on_change=lambda e: update_input("blank_lines", bool(e.value)),
                    ).mark("table-blank")

            with ui.element("section").classes("hx-workbench-output").props('data-hx-scroll="table-results"'):
                with ui.row().classes("hx-result-head w-full items-center gap-2"):
                    ui.label(t(lang, "table_review_title")).classes("hx-panel-title")
                    summary = ui.label("").classes("hx-chip hx-table-summary").mark("table-summary")
                    ui.space()
                    copy = ui.button(t(lang, "table_copy_result"), icon="content_copy", on_click=copy_result).props(
                        "outline color=primary no-caps"
                    ).mark("table-copy")
                    download = ui.button(t(lang, "table_download"), icon="download", on_click=download_result).props(
                        "outline color=primary no-caps"
                    ).mark("table-download")
                status = ui.label("").classes("hx-tab-note hx-result-status").props("role=status").mark("table-status")
                result_area = ui.column().classes("w-full gap-3").mark("table-compare")

    def render_result() -> None:
        stored = state["result"]
        result = stored[1] if stored else None
        available = result is not None and bool(result.tables)
        summary.set_text(t(lang, "table_summary").format(
            tab=result.tables, rows=result.rows,
        ) if result else t(lang, "table_summary_empty"))
        review_controls.clear()
        result_area.clear()
        with result_area:
            after = _code_pair(
                stored[0] if stored else None, result.text if available else None, lang, "table",
                allow_after=lambda: export_text() is not None,
            )
            if after is not None:
                review_controls.append(lambda: after.set_enabled(export_text() is not None))
            if available:
                def table_parts() -> list:
                    return [
                        (f"#{index} · {old.name} · L{old.start_line + 1}",
                         stored[0][old.begin:old.end], result.text[new.begin:new.end])
                        for index, (old, new) in enumerate(
                            zip(find_tables(stored[0])[0], find_tables(result.text)[0]), start=1,
                        )
                    ]

                review_controls.append(_review_parts(
                    lang, "table", table_parts, allow_after=lambda: export_text() is not None,
                ))
            if result and result.warnings:
                with ui.expansion(t(lang, "table_warnings").format(n=len(result.warnings)), icon="info").classes(
                    "hx-workbench-disclosure w-full"
                ):
                    for note in result.warnings:
                        ui.label(note).classes("hx-tab-note")

    render_result()
    update_controls()

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

    with ui.element("div").classes("hx-workbench-file w-full"):
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

        with ui.dialog() as history, ui.card().classes("hx-card w-full gap-3"):
            ui.label(t(lang, "workbench_history")).classes("hx-panel-title")

            def jump(target: int) -> None:
                history.close()
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

        with ui.row().classes("hx-file-actions items-center gap-1"):
            ui.button(t(lang, "undo"), icon="undo", on_click=do_undo).props(
                "flat dense"
            ).tooltip(t(lang, "undo")).mark("undo-btn").set_enabled(state["pointer"] > 0)
            ui.button(t(lang, "redo"), icon="redo", on_click=do_redo).props(
                "flat dense"
            ).tooltip(t(lang, "redo")).mark("redo-btn").set_enabled(
                state["pointer"] < len(state["history"])
            )
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

            with ui.button(t(lang, "workbench_file_menu"), icon="more_horiz").props(
                "flat dense no-caps"
            ).mark("file-actions"):
                with ui.menu():
                    ui.menu_item(t(lang, "workbench_history"), on_click=history.open)
                    ui.menu_item(t(lang, "reset_original"), on_click=do_reset).mark("reset-btn")
                    if state["path"]:
                        ui.menu_item(t(lang, "save_file"), on_click=save_to_path)
                    ui.menu_item(t(lang, "clear"), on_click=do_close).mark("close-btn")


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------
def _render_bib_workbench(state: dict, lang: str, doc, refresh_body) -> None:
    builders = {
        "format": _panel_format,
        "clean": _panel_clean,
        "normalize": _panel_normalize,
        "check": _panel_check,
        "verify": _panel_verify,
        "venues": _panel_venues,
    }
    actions = {}
    quick = {}

    def has_result(key: str) -> bool:
        if key == "check":
            return state["check_report"] is not None or state["check_job"] is not None
        if key == "verify":
            return state["verify_results"] is not None or state["run_control"] is not None
        return key in state["previews"] or key in state["preview_jobs"]

    def update_actions() -> None:
        for key, button in quick.items():
            active = state["tab"] == key
            button.props(add="aria-pressed=true" if active else "aria-pressed=false")
            button.classes(add="hx-action-active" if active else "",
                           remove="" if active else "hx-action-active")
            _busy(button, active and (
                key in state["preview_jobs"] if key == "format" else
                state["check_job"] is not None if key == "check" else
                state["run_control"] is not None
            ))

    async def select(key: str, configure: bool = False):
        if key != state["tab"]:
            state["tab"] = key
            await result.refresh()
            update_actions()
        if actions.get("key") != key:
            return None
        if configure and "options" in actions:
            actions["options"]()
        return actions.get("run")

    def run_action(key: str):
        async def run() -> None:
            use_cached = key != state["tab"] and has_result(key)
            command = await select(key)
            if command is not None and not use_cached:
                await command()

        return run

    async def run_arxiv() -> None:
        command = await select("verify")
        if command is not None:
            await command(scope="arxiv")

    with ui.card().classes("hx-card hx-workbench hx-bib-workbench w-full"):
        _render_status_bar(state, lang, doc, refresh_body)
        with ui.element("div").classes("hx-workbench-grid w-full"):
            with ui.element("aside").classes("hx-workbench-rail").props('data-hx-scroll="bib-actions"'):
                ui.label(t(lang, "workbench_actions")).classes("hx-optlabel")
                with ui.column().classes("hx-workbench-actions w-full gap-2"):
                    for key, label, icon in (
                        ("format", "workbench_organize", "auto_fix_high"),
                        ("check", "tab_check", "rule"),
                        ("verify", "workbench_verify", "travel_explore"),
                    ):
                        quick[key] = ui.button(t(lang, label), icon=icon, on_click=run_action(key)).props(
                            "outline color=primary no-caps"
                        ).mark(f"action-{key}")
                    with ui.button(t(lang, "workbench_more"), icon="expand_more").props("flat no-caps").mark(
                        "more-tools"
                    ):
                        with ui.menu():
                            for key in ("clean", "normalize", "venues"):
                                ui.menu_item(t(lang, "tab_" + key), on_click=lambda k=key: select(
                                    k, configure=k in {"clean", "normalize"} and not has_result(k),
                                )).mark(f"action-{key}")
                            ui.menu_item(t(lang, "arxiv_publication_action"), on_click=run_arxiv).mark("action-arxiv")
                settings_host = ui.column().classes("w-full gap-2")

            with ui.element("section").classes("hx-workbench-output w-full").props(
                'data-hx-scroll="bib-results"'
            ):
                @ui.refreshable
                def result() -> None:
                    key = state["tab"]
                    settings_host.clear()
                    actions.clear()
                    actions["key"] = key
                    actions["settings_host"] = settings_host
                    builders[key](state, lang, doc, refresh_body, quick.get(key), actions, select)

                result()
        update_actions()


@contextmanager
def _workbench_settings(actions: dict, lang: str, note: str):
    with actions["settings_host"], ui.expansion(
        t(lang, "workbench_advanced"), icon="tune",
    ).classes("hx-workbench-disclosure w-full").mark("bib-advanced") as expansion:
        actions["options"] = expansion.open
        with ui.column().classes("hx-workbench-settings w-full gap-3"):
            ui.label(note).classes("hx-tab-note")
            yield


def _comparison_code(before: str, after: Optional[str]) -> tuple[str, str]:
    left = before.splitlines()[:_CODE_PREVIEW_LINES]
    right = after.splitlines()[:_CODE_PREVIEW_LINES] if after is not None else []

    def line(lines, index, kind=""):
        if index is None:
            return '<span class="hx-code-line hx-code-gap"></span>'
        return (f'<span class="hx-code-line{kind}" data-line="{index + 1}">'
                f"{_esc(lines[index])}</span>")

    if after is None:
        return "".join(line(left, index) for index in range(len(left))), ""
    old_rows, new_rows = [], []
    matcher = difflib.SequenceMatcher(None, left, right)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        for old, new in zip_longest(range(old_start, old_end), range(new_start, new_end)):
            old_rows.append(line(left, old, " hx-code-removed" if tag != "equal" else ""))
            new_rows.append(line(right, new, " hx-code-added" if tag != "equal" else ""))
    return "".join(old_rows), "".join(new_rows)


def _code_pair(before: Optional[str], after: Optional[str], lang: str, key: str,
               *, allow_after: Optional[Callable] = None) -> Optional[ui.button]:
    left, right = _comparison_code(before or "", after)
    after_button = None
    with ui.element("div").classes("hx-code-pair w-full"):
        for side, text, code in (("before", before, left), ("after", after, right)):
            with ui.column().classes("hx-code-side w-full gap-0"):
                label = t(lang, "code_" + side)
                copy_label = t(lang, "copy_" + side)
                with ui.row().classes("hx-code-head w-full items-center gap-2"):
                    ui.label(label).classes("font-semibold")
                    ui.space()

                    async def copy(value=text, part=side) -> None:
                        if value is not None and (part != "after" or allow_after is None or allow_after()):
                            await _copy_text(value, lang)

                    button = ui.button(copy_label, icon="content_copy", on_click=copy).props(
                        "flat dense no-caps color=primary"
                    ).mark(f"{key}-copy-{side}").set_enabled(text is not None)
                    if side == "after":
                        after_button = button
                if text is not None:
                    ui.html(f'<pre class="hx-code" tabindex="0" '
                            f'aria-label="{_esc(label)}" data-hx-scroll="{key}-{side}">{code}</pre>'
                            ).classes("w-full")
                else:
                    ui.label(t(lang, "code_unavailable")).classes("hx-tab-note hx-code-placeholder")
    if any(text is not None and len(text.splitlines()) > _CODE_PREVIEW_LINES for text in (before, after)):
        ui.label(t(lang, "code_preview_limit").format(n=_CODE_PREVIEW_LINES)).classes("hx-tab-note")
    return after_button


def _entry_comparisons(before: str, after: str) -> list:
    pairs = match_by_key(
        parse_bibtex(before).entries, parse_bibtex(after).entries,
        key=lambda entry: entry.key, identity=entry_identity,
    )

    def excerpt(source: str, entry: Optional[Entry]) -> Optional[str]:
        if entry is None or entry.source_span is None:
            return None
        start, stop = entry.source_span
        return source[start:stop]

    return [
        (f"{(old or new).key} · L{(old or new).line}", excerpt(before, old), excerpt(after, new))
        for old, new in pairs
    ]


def _review_parts(lang: str, key: str, get_parts: Callable,
                  allow_after: Optional[Callable] = None) -> Callable:
    after_button = None

    def update_after() -> None:
        if after_button is not None and not after_button.is_deleted and allow_after is not None:
            after_button.set_enabled(allow_after())

    with ui.expansion(t(lang, "workbench_details"), icon="difference").classes(
        "hx-workbench-disclosure w-full"
    ).mark(f"{key}-details") as details:
        slot = ui.column().classes("w-full gap-2")

    def load(e) -> None:
        if not e.value or slot.default_slot.children:
            return
        entries = get_parts()
        with slot:
            if not entries:
                ui.label(t(lang, "no_results")).classes("hx-tab-note")
                return
            picker = ui.select(
                {index: name for index, (name, _, _) in enumerate(entries)},
                value=0, label=t(lang, "workbench_pick_item"),
            ).props("use-input hide-selected fill-input").classes("w-full").mark(f"{key}-pick-item")
            pair_slot = ui.column().classes("w-full gap-2")

            def display() -> None:
                nonlocal after_button
                pair_slot.clear()
                after_button = None
                if picker.value is None:
                    return
                _, before, after = entries[picker.value]
                with pair_slot:
                    button = _code_pair(before, after, lang, f"{key}-item", allow_after=allow_after)
                    if after is not None:
                        after_button = button
                    update_after()

            picker.on_value_change(lambda _: display())
            display()

    details.on_value_change(load)
    return update_after


def _preview_area(lang: str, state: dict, key: str, refresh_body) -> ui.column:
    col = ui.column().classes("hx-workbench-result w-full gap-3")
    saved = state["previews"].get(key)
    if saved and preview_is_current(state, saved["revision"], saved["before"]):
        _render_preview(col, state, lang, saved["before"], saved["result_text"],
                        refresh_body, opts=saved["opts"], label=saved["label"],
                        revision=saved["revision"], key=key, config=saved["config"])
    else:
        with col:
            _panel_head("difference", t(lang, "preview_title"), t(lang, "workbench_bib_ready"))
            _code_pair(state["text"], None, lang, "bib")
    return col


def _render_preview(container: ui.column, state: dict, lang: str,
                    before: str, result_text: str, refresh_body=None,
                    opts: Optional[FormatOptions] = None,
                    label: str = "", revision: Optional[int] = None,
                    key: str = "format", config: Optional[dict] = None) -> None:
    revision = state["revision"] if revision is None else revision
    config = _preview_config(state, key) if config is None else config
    container.clear()

    def fresh() -> bool:
        return preview_is_current(state, revision, before) and config == _preview_config(state, key)

    def apply_result() -> None:
        if not fresh():
            ui.notify(t(lang, "preview_options_changed"), type="warning")
            return
        if _push_stage(state, label or t(lang, "applied"), result_text):
            ui.notify(t(lang, "step_applied").format(n=state["pointer"], label=label),
                      type="positive", position="bottom-right")
            if refresh_body is not None:
                refresh_body.refresh()

    with container:
        changes = _summary_line(before, result_text, lang)
        with ui.row().classes("w-full items-center gap-2 hx-result-head"):
            ui.label(f'{label} · {t(lang, "preview_title")}').classes("hx-panel-title").mark("preview-title")
            ui.html(changes)
            ui.space()
            apply_button = ui.button(t(lang, "apply_btn"), icon="check", on_click=apply_result).props(
                "unelevated color=primary no-caps"
            ).mark("apply-btn")
        status = ui.label("").classes("hx-tab-note").props("role=status")
        after_button = _code_pair(before, result_text, lang, "bib", allow_after=fresh)
        update_part = _review_parts(lang, "bib", lambda: _entry_comparisons(before, result_text), allow_after=fresh)
        with ui.expansion(t(lang, "view_list"), icon="list").classes("hx-workbench-disclosure w-full"):
            ui.html(changes_html(before, result_text, "changed", lang)).classes("w-full")

    def update_status() -> None:
        if apply_button.is_deleted:
            return
        current = preview_is_current(state, revision, before)
        apply_button.set_enabled(fresh() and result_text != before)
        after_button.set_enabled(fresh())
        update_part()
        message = ("stale_preview" if not current else "preview_options_changed" if not fresh()
                   else "no_changes" if result_text == before else "preview_current")
        status.set_text(t(lang, message))

    state.setdefault("preview_controls", {})[key] = update_status
    update_status()


# -- format -----------------------------------------------------------------
def _panel_format(state: dict, lang: str, _doc, refresh_body, primary, actions, _navigate) -> None:
    with _workbench_settings(actions, lang, t(lang, "format_note")):
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
        run_button = primary
        if run_button is None:
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary no-caps"
            ).classes("w-full").mark("format-preview")
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

    actions["run"] = run_format
    if primary is None:
        run_button.on_click(run_format)


# -- clean ------------------------------------------------------------------
def _panel_clean(state: dict, lang: str, _doc, refresh_body, primary, actions, _navigate) -> None:
    common = ["abstract", "keywords", "url", "doi", "isbn", "issn", "month",
              "note", "address", "editor", "publisher", "eprint", "volume", "number"]
    with _workbench_settings(actions, lang, t(lang, "clean_note")):

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
        run_button = primary
        if run_button is None:
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary no-caps"
            ).classes("w-full").mark("clean-preview")
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

    actions["run"] = run_clean
    if primary is None:
        run_button.on_click(run_clean)


# -- normalize --------------------------------------------------------------
def _panel_normalize(state: dict, lang: str, _doc, refresh_body, primary, actions, _navigate) -> None:
    with _workbench_settings(actions, lang, t(lang, "normalize_note")):

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
        run_button = primary
        if run_button is None:
            run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
                "unelevated color=primary no-caps"
            ).classes("w-full").mark("normalize-preview")
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

    actions["run"] = run_normalize
    if primary is None:
        run_button.on_click(run_normalize)


# -- check ------------------------------------------------------------------
def _panel_check(state: dict, lang: str, doc, refresh_body, run_button, actions, navigate) -> None:
    controls = {}
    with _workbench_settings(actions, lang, t(lang, "check_note")):
        for key in DEFAULT_SETTINGS["check"]:
            _bind(ui.switch(
                t(lang, "check_opt_" + key), value=bool(_opt(state, "check", key)),
            ).mark("check-option-" + key), state, "check", key)
        ui.label(t(lang, "check_always")).classes("hx-tab-note")

    _panel_head("rule", t(lang, "check_results"), t(lang, "check_results_note"))
    status = ui.label("").classes("hx-tab-note").props("role=status").mark("check-status")
    result_area = ui.column().classes("w-full gap-3")

    def update_status() -> None:
        if status.is_deleted:
            return
        report = state["check_report"]
        running = state["check_job"] is not None
        _busy(run_button, running)
        stale = report is not None and state["check_config"] != _preview_config(state, "check")
        if running:
            text = t(lang, "check_running")
        elif stale:
            text = t(lang, "check_stale")
        elif report is None:
            text = t(lang, "check_empty")
        else:
            text = t(lang, "check_summary").format(entries=len(doc.entries), issues=len(report.issues))
        status.set_text(text)
        status.style("color:#b45309" if stale else "color:var(--hx-muted)")
        button = controls.get("download")
        if button is not None and not button.is_deleted:
            button.set_enabled(not stale and not running)

    def render_report(report) -> None:
        result_area.clear()
        with result_area:
            with ui.element("div").classes("hx-check-metrics w-full"):
                for severity in ("error", "warning", "info"):
                    color = SEVERITY_COLOR[severity]
                    ui.html(
                        f'<div class="hx-check-metric" style="--metric-color:{color}">'
                        f'<span class="material-icons" aria-hidden="true">{SEVERITY_ICON[severity]}</span>'
                        f'<strong>{report.count(severity)}</strong>'
                        f'<span>{_esc(t(lang, "sev_" + severity))}</span></div>'
                    ).classes("w-full").tooltip(t(lang, "sev_" + severity + "_hint"))

            def download_json() -> None:
                if state["check_report"] is not report or state["check_config"] != _preview_config(state, "check"):
                    ui.notify(t(lang, "check_stale"), type="warning")
                    return
                payload = {
                    "tool": "hx-bib", "options": state["check_config"],
                    "issues": [
                        {"severity": i.severity, "key": i.key, "code": i.code,
                         "field": i.field_name, "line": i.line, "message": i.message}
                        for i in report.sorted()
                    ],
                }
                ui.download(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
                            f"{Path(state['name']).stem or 'bibliography'}.check.json")

            with ui.row().classes("hx-preview-actions w-full items-center gap-2"):
                ui.label(t(lang, "check_readonly")).classes("hx-tab-note")
                ui.space()
                controls["download"] = ui.button(t(lang, "download_json"), icon="download",
                                                  on_click=download_json).props(
                    "outline no-caps"
                ).mark("check-download")
            with ui.expansion(t(lang, "workbench_issues"), icon="list").classes(
                "hx-workbench-disclosure w-full"
            ).mark("check-details"):
                labels = {"ALL": f'{t(lang, "check_all")} {len(report.issues)}'}
                labels.update({severity: f'{t(lang, "sev_" + severity)} {report.count(severity)}'
                               for severity in ("error", "warning", "info")})
                filters = ui.toggle(labels, value=state["check_filter"]).props(
                    "dense unelevated no-caps toggle-color=white toggle-text-color=primary"
                ).mark("check-filter")
                ui.label(t(lang, "check_expand_hint")).classes("hx-tab-note")
                issues_area = ui.column().classes("hx-check-issues w-full").props(
                    'data-hx-scroll="check-issues"'
                )

                def show_issues() -> None:
                    issues_area.clear()
                    with issues_area:
                        ui.html(check_html(report, lang, filters.value or "ALL")).classes("w-full")

                def pick(e) -> None:
                    state["check_filter"] = e.value or "ALL"
                    show_issues()

                filters.on_value_change(pick)
                show_issues()

                with ui.row().classes("w-full gap-2"):
                    for key in ("clean", "normalize"):
                        ui.button(t(lang, "check_goto_" + key), on_click=lambda k=key: navigate(
                            k, configure=True,
                        )).props("flat color=primary no-caps").mark("check-goto-" + key)
        update_status()

    state.setdefault("preview_controls", {})["check"] = update_status
    if state["check_report"] is not None:
        render_report(state["check_report"])
    update_status()

    async def run_check() -> None:
        if state["check_job"] is not None:
            return
        before, revision = state["text"], state["revision"]
        config = _preview_config(state, "check")
        options = ValidateOptions(**config)
        token = object()
        state["check_job"] = token
        _busy(run_button, True)
        update_status()
        try:
            report = await asyncio.to_thread(lambda: validate_document(parse_bibtex(before), options))
            if preview_is_current(state, revision, before) and state["check_job"] is token:
                state.update(check_report=report, check_config=config, check_filter="ALL", check_job=None)
                if not result_area.is_deleted:
                    render_report(report)
                elif state["mode"] == "tools" and state["tab"] == "check":
                    refresh_body.refresh()
        except Exception as exc:
            if preview_is_current(state, revision, before):
                ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
        finally:
            if state["check_job"] is token:
                state["check_job"] = None
                if status.is_deleted and state["mode"] == "tools" and state["tab"] == "check":
                    refresh_body.refresh()
            _busy(run_button, False)
            update_status()

    actions["run"] = run_check

# -- verify -----------------------------------------------------------------
def _panel_verify(state: dict, lang: str, doc, _refresh_body, run_button, actions, _navigate) -> None:
    with _workbench_settings(actions, lang, t(lang, "verify_note")):
        scope_setting = _draft(ui.select(
            {"all": t(lang, "verify_scope_all"), "arxiv": t(lang, "arxiv_publication_action")},
            label=t(lang, "verify_scope"),
        ).mark("verify-scope"), state, "verify_scope", "all")
        ui.label(t(lang, "verify_scope_hint").format(n=len(doc.entries))).classes("hx-tab-note")
        only = _draft(ui.input(t(lang, "only_keys")).props("outlined dense").mark("verify-keys"),
                      state, "verify_keys")
        ui.label(t(lang, "verify_network")).classes("hx-optlabel")
        ui.label(t(lang, "verify_network_hint")).classes("hx-tab-note")
        with ui.element("div").classes("hx-verify-controls w-full"):
            delay = _bind(ui.number(
                t(lang, "delay"), value=float(_opt(state, "verify", "delay")), min=0, step=0.1,
            ).props("outlined dense"), state, "verify", "delay")
            workers = _bind(ui.number(
                t(lang, "workers"), value=int(_opt(state, "verify", "workers")),
                min=1, max=8, step=1, precision=0,
            ).props("outlined dense").tooltip(t(lang, "workers_hint")), state, "verify", "workers")
        use_cache = _bind(ui.switch(t(lang, "use_cache"), value=bool(_opt(state, "verify", "use_cache"))),
                          state, "verify", "use_cache")

    _panel_head("rule", t(lang, "verify_results"), t(lang, "verify_results_note"))
    status = ui.label("").classes("hx-tab-note hx-verify-status").props(
        'role=status aria-live=polite'
    ).mark("verify-status")
    progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full").props(
        f'aria-label="{t(lang, "steps_verify_run")}"'
    )
    with ui.element("div").classes("hx-check-metrics w-full") as metrics:
        summaries = [
            ("verify_attention", "warning_amber", "WARN"),
            ("verdict_INFO", "help_outline", "INFO"),
            ("verdict_OK", "check_circle_outline", "OK"),
        ]
        counters = []
        for label, icon, verdict in summaries:
            with ui.element("div").classes("hx-check-metric").style(
                f"--metric-color:{VERDICT_COLOR[verdict]}"
            ):
                ui.icon(icon).props("aria-hidden=true")
                counters.append(ui.label("—").classes("hx-metric-number"))
                ui.label(t(lang, label)).classes("hx-metric-label")
    publication_report = ui.html("").classes("w-full").mark("arxiv-report")
    with ui.row().classes("hx-preview-actions w-full items-center gap-2"):
        ui.label(t(lang, "verify_readonly")).classes("hx-tab-note")
        ui.space()
        download_button = ui.button(t(lang, "download_report"), icon="download").props(
            "outline color=primary no-caps"
        ).mark("verify-download")

    with ui.row().classes("w-full items-center gap-2"):
        pause_button = ui.button(t(lang, "pause"), icon="pause").props(
            "outline color=primary no-caps"
        ).mark("verify-pause")
        stop_button = ui.button(t(lang, "stop"), icon="stop").props(
            "outline color=red-7 no-caps"
        ).mark("verify-stop")
    with ui.expansion(t(lang, "workbench_issues"), icon="list").classes(
        "hx-workbench-disclosure w-full"
    ).mark("verify-details") as details_expansion:
        filters = ui.select({"ALL": t(lang, "check_all")}, value="ALL", label=t(lang, "verify_filter")).props(
            "outlined dense options-dense"
        ).classes("w-full").mark("verify-filter")
        with ui.element("div").classes("hx-check-issues hx-verify-list w-full").props(
            'data-hx-scroll="verify-issues"'
        ):
            result_area = ui.html("").classes("w-full").mark("verify-list")

    rendered = None
    last_status = None

    def pick(e) -> None:
        results = state["verify_results"]
        if results and e.value in filters.options and e.value != state["verify_filter"]:
            state["verify_filter"] = e.value
            result_area.set_content(verify_html(results, e.value, lang))

    def sync_view() -> None:
        nonlocal rendered, last_status
        if status.is_deleted:
            return
        progress = state["progress"]
        control = state["run_control"]
        running = control is not None
        stopping = running and control.cancelled.is_set()
        paused = running and progress["paused"]
        results = state["verify_results"]
        outcome = progress.get("outcome")
        done, total = progress["done"], progress["total"]
        snapshot = (running, stopping, paused, done, total, outcome, id(results))
        if snapshot == last_status:
            return
        last_status = snapshot
        _busy(run_button, running)
        pause_button.set_enabled(running and not stopping)
        pause_button.set_text(t(lang, "resume" if paused else "pause"))
        pause_button.set_icon("play_arrow" if paused else "pause")
        stop_button.set_enabled(running and not stopping)
        details_expansion.set_enabled(bool(results))
        download_button.set_enabled(bool(results))
        for widget in (only, delay, workers, use_cache, scope_setting):
            widget.set_enabled(not running)
        progress_bar.set_value(done / total if total else 0)
        if running:
            phase = "verify_stopping" if stopping else "state_paused" if paused else "state_running"
            status.set_text(f"{done}/{total} · {t(lang, phase)}")
        elif outcome == "failed":
            status.set_text(t(lang, "verify_failed"))
        elif outcome == "stopped":
            status.set_text(t(lang, "stopped_partial").format(n=len(results or [])))
        elif results is not None:
            status.set_text(t(lang, "verify_done").format(n=len(results)))
        else:
            status.set_text(t(lang, "verify_ready").format(n=len(doc.entries)))

        report_view = (id(results), running, outcome)
        if report_view == rendered:
            return
        rendered = report_view
        counts = Counter(result.verdict for result in results or [])
        totals = (sum(counts[key] for key in ("FAIL", "WARN", "MINOR")), counts["INFO"], counts["OK"])
        for label, count in zip(counters, totals):
            label.set_text(str(count) if results is not None else "—")
        options = {"ALL": t(lang, "check_all")}
        arxiv_scope = progress.get("scope") == "arxiv"
        metrics.set_visibility(not arxiv_scope)
        publication_report.set_visibility(arxiv_scope and bool(results))
        if arxiv_scope and results:
            publication_report.set_content(
                f'<strong>{_esc(t(lang, "arxiv_status_title"))}</strong>'
                f'<div class="hx-tab-note">{_esc(t(lang, "arxiv_status_note"))}</div>'
                + "".join(_arxiv_publication_item(item, lang) for item in results)
            )
        if results:
            options["ALL"] += f" · {len(results)}"
            options.update({key: f'{t(lang, "verdict_" + key)} · {counts[key]}' for key in VERDICT_COLOR})
        filters.set_options(options, value=state["verify_filter"])
        filters.set_enabled(bool(results))
        if results:
            result_area.set_content(verify_html(results, state["verify_filter"], lang))
        else:
            message = "verify_pending" if running else "verify_empty"
            if outcome == "stopped":
                message = "verify_no_completed"
            title = (f'<div class="hx-panel-title">{_esc(t(lang, "verify_empty_title"))}</div>'
                     if not running and not outcome else "")
            result_area.set_content(
                '<div class="hx-empty hx-verify-empty">'
                '<span class="material-icons" aria-hidden="true">travel_explore</span>'
                f'{title}<div class="hx-tab-note">{_esc(t(lang, message))}</div></div>'
            )

    async def run_verify(scope=None) -> None:
        if state["run_control"] is not None:
            return
        before, revision = state["text"], state["revision"]
        keys = set() if scope == "arxiv" else set((only.value or "").replace(",", " ").split())
        scope = scope or scope_setting.value
        entries = list(doc.entries)
        if keys:
            missing = keys - {entry.key for entry in entries}
            if missing:
                ui.notify(t(lang, "unknown_keys").format(keys=", ".join(sorted(missing))), type="warning")
                return
            entries = [entry for entry in entries if entry.key in keys]
        if scope == "arxiv":
            entries = [entry for entry in entries if extract_arxiv_id(entry)]
        if not entries:
            ui.notify(t(lang, "arxiv_no_entries" if scope == "arxiv" else "no_results"), type="warning")
            return

        n_workers = max(1, min(8, int(workers.value or 1)))
        wait = max(0.0, float(delay.value if delay.value is not None else 0.8))
        use_cached = bool(use_cache.value)
        control = RunControl()
        progress = {"done": 0, "total": len(entries), "key": "", "running": True, "paused": False}
        state.update(run_control=control, progress=progress, verify_results=None, verify_filter="ALL")
        progress["scope"] = scope
        sync_view()

        progress_lock = Lock()

        def update_progress(done: int, total: int, citekey: str) -> None:
            with progress_lock:
                if done > progress["done"]:
                    progress.update(done=done, total=total, key=citekey)

        def work() -> list:
            net = Net(delay=wait, use_cache=use_cached)
            if not control.wait_if_paused():
                return []
            prefetch_arxiv(net, entries)
            results = verify_entries(net, entries, progress=update_progress, workers=n_workers, control=control)
            if not control.cancelled.is_set() and any(r.any_error and r.verdict != "OK" for r in results):
                results = retry_failed(net, results, control=control)
            return results

        try:
            results = await asyncio.to_thread(work)
            outcome = "stopped" if control.cancelled.is_set() else "completed"
        except Exception as exc:
            results, outcome = None, "failed"
            if state["run_control"] is control:
                ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
        finally:
            if state["run_control"] is control:
                progress.update(running=False, paused=False)
                state["run_control"] = None
        if not preview_is_current(state, revision, before) or state["progress"] is not progress:
            return
        progress.update(outcome=outcome, done=len(results) if results is not None else progress["done"])
        state["verify_results"] = results
        sync_view()

    def toggle_pause() -> None:
        control = state["run_control"]
        if control is None or control.cancelled.is_set():
            return
        if control.paused.is_set():
            control.resume()
        else:
            control.pause()
        state["progress"]["paused"] = control.paused.is_set()
        sync_view()

    def stop_run() -> None:
        control = state["run_control"]
        if control is not None:
            control.cancel()
            state["progress"]["paused"] = False
            sync_view()

    def download_report() -> None:
        results = state["verify_results"]
        if results and state["run_control"] is None:
            stem = Path(state["name"]).stem or "bibliography"
            ui.download(
                build_verify_html(results, state["name"] or "bibliography").encode("utf-8"),
                f"{stem}.report.html",
            )

    filters.on_value_change(pick)
    actions["run"] = run_verify
    pause_button.on_click(toggle_pause)
    stop_button.on_click(stop_run)
    download_button.on_click(download_report)
    sync_view()
    ui.timer(0.25, sync_view)


# -- venues -----------------------------------------------------------------
_VENUE_STYLES = ("abbr", "full", "family")


def _venue_card(venue, lang: str, used: int = 0) -> None:
    with ui.card().classes("hx-card hx-venue-card w-full p-3 gap-2"):
        with ui.row().classes("hx-venue-tags w-full items-center gap-2"):
            ui.label(venue.abbr).classes("hx-venue-abbr")
            ui.space()
            ui.label(venue.family).classes("hx-chip hx-venue-family")
            ui.label(t(lang, "venue_kind_" + venue.kind)).classes("hx-chip hx-venue-kind")
            if used:
                ui.label(t(lang, "venue_in_doc").format(n=used)).classes("hx-chip hx-venue-used")
        ui.label(venue.name).classes("hx-venue-name")
        for style in _VENUE_STYLES:
            rendered = render_venue(venue, style=style)
            copy_label = t(lang, "venue_copy_" + style)
            with ui.row().classes("hx-vrow w-full no-wrap"):
                ui.label(t(lang, "style_" + style + "_short")).classes("hx-vrow-label")
                ui.label(rendered).classes("hx-vrow-val")
                ui.button(icon="content_copy", on_click=_copy_handler(rendered, lang)).props(
                    f'flat dense size=sm round aria-label="{copy_label}: {venue.abbr}"'
                ).tooltip(copy_label).mark(f"venue-{venue.abbr}-{style}")


def _normalize_venue_preview(before: str, style: str) -> str:
    work = parse_bibtex(before)
    opts = NormalizeOptions(venue_style=style, arxiv_style="keep")
    changed = False
    for entry in work.entries:
        if entry_venue_presets(entry) and normalize_entry(entry, opts):
            changed = True
    return render_document(work, FormatOptions()) if changed else before


def _panel_venues(state: dict, lang: str, doc, refresh_body, _primary=None, actions=None, _navigate=None) -> None:
    usage = Counter()
    matched = 0
    if doc is not None:
        for entry in doc.entries:
            presets = entry_venue_presets(entry)
            matched += bool(presets)
            usage.update(presets)

    _panel_head("stadium", t(lang, "venues_catalog"), t(lang, "venues_catalog_note"))

    if actions is not None:
        with _workbench_settings(actions, lang, t(lang, "venues_norm_note").format(n=matched)):
            _bind(ui.select(
                {key: t(lang, "style_" + key + "_short") for key in _VENUE_STYLES},
                value=_opt(state, "venues", "preset_style"), label=t(lang, "venue_style"),
            ).mark("venue-norm-style"), state, "venues", "preset_style")
            run_button = ui.button(t(lang, "venues_norm_btn"), icon="preview").props(
                "unelevated color=primary no-caps"
            ).mark("venue-normalize-preview").set_enabled(matched > 0)

        with ui.expansion(t(lang, "venues_apply"), icon="auto_fix_high", value="venues" in state["previews"]).classes(
            "hx-workbench-disclosure w-full"
        ).mark("venue-apply") as show_preview:
            preview = _preview_area(lang, state, "venues", refresh_body)

        async def run_venue_normalize() -> None:
            if matched:
                show_preview.open()
                style = _opt(state, "venues", "preset_style")
                await _run_preview_job(
                    run_button, preview, state, lang, "venues", lambda before: _normalize_venue_preview(before, style),
                    refresh_body, FormatOptions(), t(lang, "venues_norm_label"),
                )

        run_button.on_click(run_venue_normalize)

    query = _draft(ui.input(t(lang, "search_venues"), placeholder=t(lang, "venues_search_hint"))
                   .classes("w-full").mark("venue-search"), state, "venue_query")
    with ui.expansion(t(lang, "workbench_filters"), icon="filter_alt").classes(
        "hx-workbench-disclosure w-full"
    ).mark("venue-filters"):
        with ui.element("div").classes("hx-venue-filters w-full"):
            kind = _draft(ui.select(
                {"ALL": t(lang, "venue_all_kinds"),
                 **{key: t(lang, "venue_kind_" + key) for key in (CONFERENCE, JOURNAL)}},
                label=t(lang, "venue_kind"),
            ).mark("venue-kind"), state, "venue_kind", "ALL")
            family = _draft(ui.select(
                {"ALL": t(lang, "venue_all_families"), **{name: name for name in families()}},
                label=t(lang, "family"),
            ).mark("venue-family"), state, "venue_family", "ALL")
        used_only = None
        if doc is not None:
            used_only = _draft(ui.switch(t(lang, "venues_used_only")).mark("venue-used-only"),
                               state, "venue_used_only", False)
    with ui.row().classes("w-full items-center gap-2"):
        count = ui.label("").classes("hx-tab-note").props("role=status").mark("venue-count")
        reset_button = ui.button(t(lang, "venues_clear_filters"), icon="filter_alt_off").props(
            "flat dense color=primary no-caps"
        ).mark("venue-clear-filters")
    grid_area = ui.column().classes("w-full gap-2").mark("venue-results")

    def render_catalog() -> None:
        matches = [
            venue for venue in search_venues(query.value or "")
            if (kind.value == "ALL" or venue.kind == kind.value)
            and (family.value == "ALL" or venue.family == family.value)
            and (used_only is None or not used_only.value or usage[venue.abbr])
        ]
        count.set_text(t(lang, "venues_count").format(n=len(matches), total=len(VENUES)))
        reset_button.set_enabled(bool(query.value or kind.value != "ALL" or family.value != "ALL"
                                      or (used_only is not None and used_only.value)))
        grid_area.clear()
        with grid_area:
            if matches:
                with ui.element("div").classes("hx-venue-grid w-full"):
                    for venue in matches:
                        _venue_card(venue, lang, usage[venue.abbr])
            else:
                ui.label(t(lang, "no_venues")).classes("hx-tab-note")

    def reset_filters() -> None:
        query.set_value("")
        kind.set_value("ALL")
        family.set_value("ALL")
        if used_only is not None:
            used_only.set_value(False)

    for widget in (query, kind, family, used_only):
        if widget is not None:
            widget.on_value_change(lambda _: render_catalog())
    reset_button.on_click(reset_filters)
    render_catalog()
