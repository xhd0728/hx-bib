"""NiceGUI workspaces for BibTeX and LaTeX tables."""

from __future__ import annotations

import asyncio
import difflib
import html
import inspect
import json
import logging
import os
import re
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from itertools import zip_longest
from pathlib import Path
from threading import Lock
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from nicegui import events, ui

from ..core import cleaner
from ..core.diff import diff_documents
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
from ..core.normalize import entry_venue_presets
from ..core.parser import parse_bibtex
from ..core.validator import ValidationReport
from ..core.venues import (
    CONFERENCE,
    JOURNAL,
    VENUES,
    families,
    render_venue,
    search_venues,
)
from ..verify import Net, RunControl, prefetch_arxiv, retry_failed, verify_entries
from ..verify.checker import merge_attempts, verify_arxiv_entry
from ..verify.report import build_html as build_verify_html
from .bib_workbench import (
    BibComparison,
    apply_options,
    compare_entries,
    comparison_rows,
)
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
logger = logging.getLogger(__name__)
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
    if not isinstance(node, dict) or node.get(key) == value:
        return
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
    link = ""
    if result.publication_url:
        try:
            target = urlsplit(result.publication_url)
        except ValueError:
            target = None
        if target and target.scheme in {"http", "https"} and target.hostname:
            link = (f'<a class="hx-publication-link" href="{_esc(result.publication_url)}" '
                    f'target="_blank" rel="noopener noreferrer">{_esc(t(lang, "arxiv_source_link"))}</a>')
    failures = "".join(
        f'<li>{_esc(t(lang, "arxiv_attempt_error").format(n=number, detail=detail))}</li>'
        for number, detail in result.attempt_errors
    )
    history = (
        '<details class="hx-publication-attempts">'
        f'<summary>{_esc(t(lang, "arxiv_attempt_log"))}</summary><ul>{failures}</ul></details>'
        if failures else ""
    )
    attempts = (
        f'<div class="hx-tab-note">{_esc(t(lang, "arxiv_attempts").format(n=result.attempts))}</div>'
        if result.attempts > 1 or result.any_error else ""
    )
    return (
        '<div class="hx-publication-item">'
        f'arXiv:{_esc(result.arxiv_id)}'
        f'<div><strong>{_esc(t(lang, "arxiv_status_" + status))}</strong>'
        f'{(" · " + _esc(evidence)) if evidence else ""}{link}</div>{attempts}{history}</div>'
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


def _draft(widget, state: dict, key: str, default=""):
    """Keep unfinished input in memory across mode/language/history refreshes."""
    widget.value = state["drafts"].get(key, default)
    def changed(e) -> None:
        state["drafts"][key] = e.value

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
                    if not state["name"]:
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


def run_gui(host: str = "127.0.0.1", port: int = 8080, show: bool = True, debug: bool = False) -> None:
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    register_pages()
    ui.run(
        host=host, port=port, show=show, title="hx-bib", favicon="📚",
        reload=False, show_welcome_message=False, uvicorn_logging_level="debug" if debug else "warning",
    )


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
        history=[], pointer=0,
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
            _panel_venues(state, lang)


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
                         pointer=0, venues_only=False)
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
    arxiv_ids = {extract_arxiv_id(entry) for entry in doc.entries}

    async def retry_arxiv(only: Optional[str] = None) -> None:
        ids = _failed_arxiv_ids(state) & arxiv_ids
        if only is not None:
            ids &= {only}
        if ids:
            await _verify_bib(state, lang, "arxiv", sync_online, retry_ids=ids)

    with ui.card().classes("hx-card hx-workbench hx-bib-workbench w-full"):
        _render_status_bar(state, lang, doc, refresh_body)
        with ui.element("div").classes("hx-workbench-grid w-full"):
            with ui.element("aside").classes("hx-workbench-rail hx-bib-rail"):
                with ui.column().classes("hx-bib-options-scroll w-full gap-2").props('data-hx-scroll="bib-options"'):
                    _bib_settings(state, lang, doc)
                    with _bib_section(state, lang, "verify", "workbench_verify", "travel_explore"):
                        _draft(ui.input(t(lang, "only_keys")).mark("verify-keys"), state, "verify_keys")
                        _bind(ui.number(t(lang, "delay"), value=_opt(state, "verify", "delay"),
                                        min=0, step=0.1), state, "verify", "delay")
                        _bind(ui.number(t(lang, "workers"), value=_opt(state, "verify", "workers"),
                                        min=1, max=8, step=1), state, "verify", "workers")
                        _bib_switch(state, lang, "verify", "use_cache", "use_cache")
                        verify = ui.button(t(lang, "workbench_verify"), icon="travel_explore").props(
                            "outline no-caps",
                        ).classes("w-full").mark("action-verify")
                        download = ui.button(t(lang, "download_report"), icon="download").props(
                            "flat dense no-caps",
                        ).mark("verify-download")
                with ui.column().classes("hx-bib-footer w-full gap-2"):
                    apply = ui.button(t(lang, "bib_apply"), icon="check").props(
                        "unelevated no-caps",
                    ).classes("w-full").mark("apply-btn")
                    arxiv = ui.button(t(lang, "arxiv_publication_action"), icon="school").props(
                        "outline no-caps",
                    ).classes("w-full").mark("action-arxiv")
                    retry = ui.button(icon="refresh", on_click=lambda: retry_arxiv()).props(
                        "flat dense no-caps",
                    ).classes("w-full").mark("arxiv-retry-failed")
                    status = ui.label("").classes("hx-tab-note").props("role=status").mark("verify-status")
                    with ui.row().classes("w-full gap-1") as run_controls:
                        pause = ui.button(t(lang, "pause"), icon="pause").props(
                            "flat dense no-caps",
                        ).mark("verify-pause")
                        stop = ui.button(t(lang, "stop"), icon="stop").props(
                            "flat dense no-caps color=red-7",
                        ).mark("verify-stop")
            with ui.element("section").classes("hx-workbench-output hx-bib-results"):
                render_results = _bib_results(state, lang, retry_arxiv)

        async def run_apply() -> None:
            _busy(apply, True)
            try:
                if await _apply_bib_options(state, lang):
                    refresh_body.refresh()
            finally:
                _busy(apply, False)

        apply.on_click(run_apply)

        last = None

        def sync_online() -> None:
            nonlocal last
            if status.is_deleted:
                return
            progress = state["progress"]
            control = state["run_control"]
            scope = progress.get("scope")
            done, total = progress["done"], progress["total"]
            paused = progress["paused"]
            stopping = control is not None and control.cancelled.is_set()
            outcome = progress.get("outcome")
            reports = state["verify_results"], state["arxiv_results"]
            snapshot = (scope, done, total, paused, stopping, outcome, control, *(id(report) for report in reports))
            if snapshot == last:
                return
            if last is not None and (snapshot[-2:] != last[-2:] or control is not last[-3]):
                render_results.refresh()
            last = snapshot
            for button, kind in ((verify, "all"), (arxiv, "arxiv")):
                button.set_enabled(control is None)
                _busy(button, control is not None and scope == kind)
            download.set_enabled(any(reports))
            failed = len(_failed_arxiv_ids(state) & arxiv_ids)
            retry.set_visibility(bool(failed))
            retry.set_enabled(control is None)
            retry.set_text(t(lang, "arxiv_retry_failed").format(n=failed))
            run_controls.set_visibility(control is not None)
            pause.set_enabled(not stopping)
            stop.set_enabled(not stopping)
            pause.set_text(t(lang, "resume" if paused else "pause"))
            pause.set_icon("play_arrow" if paused else "pause")
            status.set_visibility(bool(scope))
            if control is not None:
                phase = "verify_stopping" if stopping else "state_paused" if paused else "state_running"
                status.set_text(f"{done}/{total} · {t(lang, phase)}")
            elif outcome == "failed":
                status.set_text(t(lang, "verify_failed"))
            elif outcome == "stopped":
                status.set_text(t(lang, "stopped_partial").format(n=done))
            elif scope:
                key = "arxiv_done_incomplete" if scope == "arxiv" and failed else "verify_done"
                checked = len(state["arxiv_results"] or []) if scope == "arxiv" else done
                status.set_text(t(lang, key).format(n=checked, failed=failed))

        async def run_online(scope: str) -> None:
            await _verify_bib(state, lang, scope, sync_online)

        def toggle_pause() -> None:
            control = state["run_control"]
            if control is not None and not control.cancelled.is_set():
                if control.paused.is_set():
                    control.resume()
                else:
                    control.pause()
                state["progress"]["paused"] = control.paused.is_set()
                sync_online()

        def stop_run() -> None:
            if (control := state["run_control"]) is not None:
                control.cancel()
                state["progress"]["paused"] = False
                sync_online()

        def download_report() -> None:
            preferred = "arxiv_results" if state["progress"].get("scope") == "arxiv" else "verify_results"
            results = state[preferred] or state["verify_results"] or state["arxiv_results"]
            if results:
                ui.download(build_verify_html(results, state["name"]).encode("utf-8"),
                            f"{Path(state['name']).stem}.report.html")

        verify.on_click(lambda: run_online("all"))
        arxiv.on_click(lambda: run_online("arxiv"))
        pause.on_click(toggle_pause)
        stop.on_click(stop_run)
        download.on_click(download_report)
        sync_online()
        ui.timer(0.25, sync_online)


@contextmanager
def _bib_section(state: dict, lang: str, key: str, title: str, icon: str):
    draft = f"bib_open_{key}"
    toggled = key in state["settings"]["enabled"]

    def label() -> str:
        status = " · " + t(lang, "bib_enabled") if toggled and _opt(state, "enabled", key) else ""
        return t(lang, title) + status

    with ui.expansion(label(), icon=icon, value=state["drafts"].get(draft, False)).props(
        "dense",
    ).classes("hx-bib-section w-full").mark(f"options-{key}") as section:
        section.on_value_change(lambda e: state["drafts"].update({draft: e.value}))
        with ui.column().classes("hx-workbench-settings w-full gap-2"):
            if toggled:
                _bind(ui.switch(t(lang, "bib_include"), value=_opt(state, "enabled", key)).mark(f"enable-{key}"),
                      state, "enabled", key, after=lambda _: section.props(f'label="{label()}"'))
            yield


def _bib_switch(state: dict, lang: str, section: str, key: str, text: str) -> None:
    _bind(ui.switch(t(lang, text), value=_opt(state, section, key)).mark(f"{section}-option-{key}"),
          state, section, key)


def _bib_select(state: dict, lang: str, section: str, key: str, label: str, choices: dict) -> None:
    _bind(ui.select(choices, value=_opt(state, section, key), label=t(lang, label)).mark(f"{section}-{key}"),
          state, section, key)


def _bib_settings(state: dict, lang: str, doc) -> None:
    with _bib_section(state, lang, "clean", "bib_cleanup", "cleaning_services"):
        _bind(ui.select(cleaner.COMMON_FIELDS, value=list(_opt(state, "clean", "remove")), multiple=True,
                        label=t(lang, "remove_fields")).props("use-chips"), state, "clean", "remove")
        for key in ("custom_remove", "add_key", "add_value"):
            _draft(ui.input(t(lang, key)).mark(f"clean-{key}"), state, f"clean_{key}")
        _draft(ui.switch(t(lang, "overwrite")).mark("clean-overwrite"), state, "clean_overwrite", False)
        for key, label in (
            ("drop_empty", "drop_empty"), ("drop_others", "drop_others"), ("months", "months"),
            ("title_period", "title_period"), ("doi", "opt_doi"), ("pages", "opt_pages"),
            ("clean_urls", "opt_clean_urls"), ("dedupe", "dedupe"), ("dedupe_title", "opt_dedupe_title"),
            ("keep_last", "keep_last"),
        ):
            _bib_switch(state, lang, "clean", key, label)

    with _bib_section(state, lang, "format", "tab_format", "format_align_left"):
        _bib_select(state, lang, "format", "indent", "opt_indent",
                    {"4": "4 spaces", "2": "2 spaces", "0": "0", "tab": "Tab", "align": t(lang, "bib_indent_align")})
        _bib_select(state, lang, "format", "field_order", "opt_order",
                    {key: t(lang, label) for key, label in
                     (("standard", "order_standard"), ("alphabetical", "order_alpha"), ("keep", "order_keep"))})
        _bib_select(state, lang, "format", "sort", "opt_sort",
                    {key: t(lang, f"bib_sort_{key}") if key != "none" else t(lang, "sort_none")
                     for key in ("none", "key", "year", "type", "title")})
        for key in ("align_equals", "trailing_comma", "drop_comments"):
            _bib_switch(state, lang, "format", key, "opt_" + key)

    with _bib_section(state, lang, "normalize", "tab_normalize", "tune"):
        _bib_select(state, lang, "normalize", "venue_style", "venue_style",
                    {key: t(lang, "style_" + key + "_short") for key in (*_VENUE_STYLES, "keep")})
        _bib_select(state, lang, "normalize", "arxiv_style", "arxiv_style",
                    {"journal": t(lang, "arxiv_journal_short"), "misc": t(lang, "arxiv_misc_short"),
                     "keep": t(lang, "style_keep_short")})
        for key in ("fix_types", "drop_arxiv_volume", "add_arxiv_url"):
            _bib_switch(state, lang, "normalize", key, key)

    with _bib_section(state, lang, "check", "tab_check", "rule"):
        for key in DEFAULT_SETTINGS["check"]:
            _bib_switch(state, lang, "check", key, "check_opt_" + key)
        report = state["check_report"]
        if report is not None:
            def download_json() -> None:
                payload = {"tool": "hx-bib", "options": state["check_config"], "issues": [
                    {"severity": i.severity, "key": i.key, "code": i.code, "field": i.field_name,
                     "line": i.line, "message": i.message} for i in report.sorted()
                ]}
                ui.download(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
                            f"{Path(state['name']).stem}.check.json")

            ui.button(t(lang, "download_json"), icon="download", on_click=download_json).props(
                "flat dense no-caps",
            ).mark("check-download")

    with _bib_section(state, lang, "venues", "tab_venues", "stadium"):
        _bib_select(state, lang, "venues", "preset_style", "venue_style",
                    {key: t(lang, "style_" + key + "_short") for key in _VENUE_STYLES})
        ui.label(t(lang, "bib_venue_precedence")).classes("hx-tab-note")
        _panel_venues(state, lang, doc, compact=True)


async def _apply_bib_options(state: dict, lang: str) -> bool:
    if state["apply_job"] is not None:
        return False
    before, revision = state["text"], state["revision"]
    settings = deepcopy(state["settings"])
    drafts = state["drafts"].copy()
    token = state["apply_job"] = object()
    try:
        after, report = await asyncio.to_thread(apply_options, before, settings, drafts)
    except Exception as exc:
        if preview_is_current(state, revision, before):
            message = t(lang, str(exc)) if str(exc) in {
                "incomplete_field", "bib_invalid_field", "bib_parse_first", "bib_result_invalid",
            } else t(lang, "operation_failed").format(error=exc)
            ui.notify(message, type="warning", position="bottom-right")
        return False
    finally:
        if state["apply_job"] is token:
            state["apply_job"] = None
    if not preview_is_current(state, revision, before):
        return False
    _push_stage(state, t(lang, "bib_apply_stage"), after)
    state.update(diff_before=before, check_report=report, check_config=settings["check"])
    return True


def _failed_arxiv_ids(state: dict) -> set[str]:
    return {result.arxiv_id for result in state["arxiv_results"] or [] if result.any_error and result.arxiv_id}


async def _verify_bib(state: dict, lang: str, scope: str, sync_view, *, retry_ids: Optional[set[str]] = None) -> None:
    if state["run_control"] is not None:
        return
    before, revision = state["text"], state["revision"]
    entries = parse_bibtex(before).entries
    if scope == "arxiv":
        unique = {}
        for entry in entries:
            if (arxiv_id := extract_arxiv_id(entry)) and (retry_ids is None or arxiv_id in retry_ids):
                unique.setdefault(arxiv_id, entry)
        entries = list(unique.values())
    else:
        keys = set((state["drafts"].get("verify_keys") or "").replace(",", " ").split())
        if missing := keys - {entry.key for entry in entries}:
            ui.notify(t(lang, "unknown_keys").format(keys=", ".join(sorted(missing))), type="warning")
            return
        if keys:
            entries = [entry for entry in entries if entry.key in keys]
    if not entries:
        ui.notify(t(lang, "arxiv_no_entries" if scope == "arxiv" else "no_results"), type="warning")
        return

    settings = _merge_settings(DEFAULT_SETTINGS, state["settings"])["verify"]
    control = RunControl()
    progress = dict(done=0, total=len(entries), paused=False, running=True, scope=scope)
    state.update(run_control=control, progress=progress)
    sync_view()
    progress_lock = Lock()

    def update_progress(done: int, total: int, _key: str) -> None:
        with progress_lock:
            if done > progress["done"]:
                progress.update(done=done, total=total)

    def work() -> list:
        net = Net(delay=settings["delay"], use_cache=settings["use_cache"] and retry_ids is None)
        if not control.wait_if_paused():
            return []
        try:
            prefetch_arxiv(net, entries)
        except Exception:  # noqa: BLE001 - individual lookups still work
            logger.exception("Could not prefetch arXiv records")
        if scope == "arxiv":
            return verify_entries(net, entries, progress=update_progress, workers=settings["workers"],
                                  control=control, check=verify_arxiv_entry, retries=2)
        results = verify_entries(net, entries, progress=update_progress, workers=settings["workers"], control=control)
        if not control.cancelled.is_set() and any(r.any_error and r.verdict != "OK" for r in results):
            results = retry_failed(net, results, control=control)
        return results

    try:
        results = await asyncio.to_thread(work)
        outcome = "stopped" if control.cancelled.is_set() else "completed"
    except Exception as exc:
        results, outcome = None, "failed"
        logger.exception("Verification batch failed (%s)", scope)
        if state["run_control"] is control:
            ui.notify(t(lang, "operation_failed").format(error=exc), type="negative")
    if state["run_control"] is not control:
        return
    state["run_control"] = None
    progress.update(running=False, paused=False, outcome=outcome, done=len(results or []))
    if results is not None and (scope == "arxiv" or preview_is_current(state, revision, before)):
        if scope == "arxiv" and retry_ids is not None:
            updated = {result.arxiv_id: result for result in results}
            results = [merge_attempts(old, updated[old.arxiv_id]) if old.arxiv_id in updated else old
                       for old in state["arxiv_results"] or []]
        state["arxiv_results" if scope == "arxiv" else "verify_results"] = results
    sync_view()


def _bib_results(state: dict, lang: str, retry_arxiv):
    before = state["diff_before"]
    after = state["text"]
    comparisons = compare_entries(after if before is None else before, after)
    report = state["check_report"]

    with ui.row().classes("hx-bib-result-header w-full items-center justify-between gap-2"):
        with ui.column().classes("gap-0"):
            ui.label(t(lang, "bib_diff")).classes("hx-panel-title")
            counts = Counter(c.kind for c in comparisons if c.before is not None or c.after is not None)
            ui.label(t(lang, "bib_diff_count").format(
                modified=counts["modified"], added=counts["added"], removed=counts["removed"],
            )).classes("hx-tab-note").mark("diff-count")
        mode = ui.toggle({"changed": t(lang, "bib_diff_only"), "all": t(lang, "bib_diff_all")},
                         value=state["diff_view"]).props("unelevated no-caps").mark("bib-diff-mode")

    with ui.column().classes("hx-bib-diff-list w-full gap-3").props('data-hx-scroll="bib-results"'):
        @ui.refreshable
        def render() -> None:
            full = state["diff_view"] == "all"
            issues = {}
            document_issues = []
            for issue in report.sorted() if report is not None else ():
                if issue.code == "parse":
                    document_issues.append(issue)
                else:
                    issues.setdefault((issue.key, issue.line), []).append(issue)
            if document_issues:
                ui.html(check_html(ValidationReport(document_issues), lang)).classes("w-full")
            if report is not None and not report.issues:
                ui.label(t(lang, "check_clean")).classes("hx-tab-note").mark("check-clean")
            arxiv = {item.arxiv_id: item for item in state["arxiv_results"] or []}
            verified = {item.entry.source_span: item for item in state["verify_results"] or []}
            visible = 0
            for index, comparison in enumerate(comparisons):
                entry = comparison.after
                local = []
                online = None
                publication = None
                if entry is not None:
                    local = issues.get((entry.key, entry.line), []) + issues.get((entry.key, 0), [])
                    online = verified.get(entry.source_span)
                    publication = arxiv.get(extract_arxiv_id(entry)) if arxiv else None
                needs_attention = online is not None and online.verdict != "OK"
                if not full and comparison.kind == "unchanged" and not (local or needs_attention or publication):
                    continue
                visible += 1
                _bib_diff_card(lang, comparison, index, full, local, online, publication,
                               retry_arxiv if state["run_control"] is None else None)
            if not visible and not (report and report.issues):
                message = "bib_diff_ready" if before is None else "no_changes"
                with ui.column().classes("hx-bib-diff-empty w-full items-center gap-2"):
                    ui.icon("difference", size="26px")
                    ui.label(t(lang, message)).classes("hx-tab-note").mark("diff-empty")

        render()

    def change(e) -> None:
        state["diff_view"] = e.value
        render.refresh()

    mode.on_value_change(change)
    return render


def _bib_diff_card(lang: str, comparison: BibComparison, index: int, full: bool, issues, online, publication,
                   retry_arxiv) -> None:
    entry = comparison.after or comparison.before
    with ui.element("article").classes("hx-bib-diff-card w-full").mark("bib-diff-entry"):
        with ui.row().classes("hx-bib-diff-heading w-full items-center gap-2"):
            ui.label(entry.key if entry is not None else t(lang, "free_block")).classes("hx-ediff-key")
            kind = comparison.kind
            ui.label(t(lang, "kind_" + kind)).classes(f"hx-kind hx-kind-{kind}")
            if comparison.moved:
                ui.label(t(lang, "bib_moved")).classes("hx-kind")
        with ui.element("div").classes("hx-bib-diff-sides w-full"):
            for side, value in (("before", comparison.left), ("after", comparison.right)):
                with ui.row().classes("hx-bib-diff-side items-center gap-2"):
                    ui.label(t(lang, "code_" + side)).classes("hx-tab-note")
                    ui.space()
                    label = t(lang, "copy_" + side)
                    ui.button(icon="content_copy", on_click=_copy_handler(value or "", lang)).props(
                        f'flat dense no-caps aria-label="{label}"',
                    ).tooltip(label).mark(
                        f"diff-{index}-copy-{side}",
                    ).set_enabled(value is not None and bool(value))
        if full or kind != "unchanged":
            rows = comparison_rows(comparison, full)
            if rows:
                ui.html(f'<table class="hx-diff"><tbody>{_diff_rows_html(rows)}</tbody></table>').classes("w-full")
            else:
                ui.label(t(lang, "bib_spacing_only")).classes("hx-bib-note hx-tab-note")
        if issues or online or publication:
            with ui.column().classes("hx-bib-note w-full gap-1"):
                if issues:
                    ui.html(check_html(ValidationReport(issues), lang)).classes("w-full")
                if publication:
                    ui.html(_arxiv_publication_item(publication, lang)).classes("w-full").mark("arxiv-result")
                    if publication.any_error:
                        async def retry_one() -> None:
                            await retry_arxiv(publication.arxiv_id)

                        ui.button(t(lang, "arxiv_retry_entry"), icon="refresh", on_click=retry_one).props(
                            "flat dense no-caps",
                        ).mark("arxiv-retry-entry").set_enabled(retry_arxiv is not None)
                if online:
                    ui.html(verify_html([online], "ALL", lang)).classes("w-full")


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

    def load() -> None:
        if not details.value or slot.default_slot.children:
            return
        entries = get_parts()
        with slot:
            if not entries:
                ui.label(t(lang, "no_results")).classes("hx-tab-note")
                return
            labels = {index: name for index, (name, _, _) in enumerate(entries)}
            picker = ui.select(labels, value=0, label=t(lang, "workbench_pick_item")).props(
                "use-input hide-selected fill-input"
            ).classes("w-full").mark(f"{key}-pick-item")
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

    details.on_value_change(lambda _: load())
    return update_after


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


def _panel_venues(state: dict, lang: str, doc=None, *, compact: bool = False) -> None:
    usage = Counter(preset for entry in doc.entries for preset in entry_venue_presets(entry)) if doc else Counter()
    if not compact:
        _panel_head("stadium", t(lang, "venues_catalog"), t(lang, "venues_catalog_note"))

    query = _draft(ui.input(t(lang, "search_venues"), placeholder=t(lang, "venues_search_hint"))
                   .classes("w-full").mark("venue-search"), state, "venue_query")
    filters = ui.column().classes("w-full gap-2") if compact else ui.expansion(
        t(lang, "workbench_filters"), icon="filter_alt",
    ).classes("hx-workbench-disclosure w-full").mark("venue-filters")
    with filters:
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
                    for venue in matches[:8] if compact else matches:
                        _venue_card(venue, lang, usage[venue.abbr])
                if compact and len(matches) > 8:
                    ui.label(t(lang, "bib_venue_more")).classes("hx-tab-note")
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
