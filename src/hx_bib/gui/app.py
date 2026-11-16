"""NiceGUI front-end for hx-bib.

One page, one job per tab:

   上传  →  load a .bib file (drag & drop or local path)
   格式化 / Format       indentation, equals alignment, field order, sorting
   清理 / Clean          remove/add fields, months, "others", dedupe, advanced fixes
   归一化 / Normalize    venue style (abbr/full/family) and arXiv preset
   校验 / Check          offline structural validation
   校验引用 / Verify     multi-source hallucination verification
   预设 / Venues         browse venue presets (each rendering copyable)

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
from ..core.diff import diff_documents, diff_lines
from ..core.model import Entry
from ..core.normalize import NormalizeOptions, normalize_document
from ..core.parser import parse_bibtex
from ..core.validator import ValidateOptions, validate_document
from ..core.venues import VENUES, families, render_venue, search_venues
from ..core.writer import FormatOptions, render_document, render_entry
from ..verify import Net, RunControl, prefetch_arxiv, retry_failed, verify_entries
from ..verify.report import build_html as build_verify_html

from .i18n import t

SEVERITY_COLOR = {"error": "#dc2626", "warning": "#d97706", "info": "#2563eb"}
VERDICT_COLOR = {
    "FAIL": "#dc2626", "WARN": "#d97706", "MINOR": "#ca8a04",
    "INFO": "#2563eb", "OK": "#16a34a",
}

_IN_SIM = bool(os.environ.get("NICEGUI_USER_SIMULATION"))
_SETTINGS_KEY = "hx-bib-settings"

DEFAULT_SETTINGS: dict = {
    "lang": "zh",
    "format": {
        "indent": "4", "field_order": "standard", "sort": "none",
        "align_equals": False, "trailing_comma": False, "drop_comments": False,
    },
    "clean": {
        "remove": [], "drop_empty": False, "drop_others": False, "months": False,
        "title_period": False, "dedupe": False, "keep_last": False,
        "doi": False, "pages": False, "clean_urls": False, "dedupe_title": False,
    },
    "normalize": {
        "venue_style": "abbr", "arxiv_style": "journal", "fix_types": True,
        "drop_arxiv_volume": True, "add_arxiv_url": True,
    },
    "verify": {"delay": 0.8, "workers": 1, "use_cache": True},
}

CSS = """
body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
       background: #f5f6f8; }
.hx-title { font-weight: 800; letter-spacing: -0.02em; }
.hx-card { border-radius: 14px; box-shadow: 0 1px 3px rgba(15,23,42,.06);
           border: 1px solid #e5e7eb; }
.hx-chip { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
           font-weight:700; white-space:nowrap; }
.hx-tab-note { color:#4b5563; font-size:13px; }
.hx-empty { border:1.5px dashed #cbd5e1; border-radius:12px; padding:20px 16px;
            text-align:center; color:#64748b; font-size:13.5px; background:#fafbfc; }
.hx-diff { width:100%; min-width:760px; border-collapse:collapse; font:12.5px/1.55 ui-monospace,
           SFMono-Regular, Menlo, Consolas, monospace; table-layout: fixed; }
.hx-diff td { padding:1px 8px; vertical-align:top; white-space:pre-wrap; word-break:break-all; }
.hx-diff td.ln { width:44px; color:#6b7280; text-align:right; user-select:none; }
.hx-diff td.old, .hx-diff td.new { width: calc(50% - 44px); }
.hx-diff tr.del td.old { background:#fee2e2; }
.hx-diff tr.ins td.new { background:#dcfce7; }
.hx-diff tr.replace td.old { background:#fee2e2; }
.hx-diff tr.replace td.new { background:#dcfce7; }
.hx-diff tr.equal td { color:#6b7280; }
.hx-changes { width:100%; border-collapse:collapse; font-size:13.5px; }
.hx-changes th { text-align:left; font-size:12px; color:#4b5563; text-transform:uppercase;
                 letter-spacing:.05em; border-bottom:1px solid #e5e7eb; padding:6px 8px; }
.hx-changes td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.hx-field-add { color:#15803d; background:#dcfce7; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-del { color:#b91c1c; background:#fee2e2; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-mod { color:#92400e; background:#fef3c7; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-issue { display:flex; gap:8px; padding:4px 0; font-size:13.5px; }
.hx-result { border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; margin:10px 0;
             background:#fff; }
.hx-evidence { background:#f8fafc; border:1px dashed #e2e8f0; border-radius:8px;
               padding:8px 10px; margin-top:8px; font-size:12.5px; color:#475569; }
.hx-names { margin:4px 0 0; padding-left:20px; }
.hx-names li.bad { background:#fee2e2; color:#b91c1c; font-weight:600; border-radius:4px; }
.hx-pre { margin:0; padding:8px 10px; background:#f8fafc; border:1px solid #eef2f7;
          border-radius:8px; font:12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas,
          monospace; max-height:220px; overflow:auto; white-space:pre; }
.hx-entry-card { border:1px solid #e5e7eb !important; border-radius:10px !important;
                 box-shadow:none !important; }
.hx-status { position:sticky; top:8px; z-index:20; background:#ffffff; }
.q-tab { text-transform: none; }
.q-uploader__subtitle { color: rgba(255,255,255,.92) !important; }
button:focus-visible, .q-field:focus-within { outline: 2px solid #6366f1; outline-offset: 1px; }
"""

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


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
def _merge_settings(base: dict, incoming: object) -> dict:
    if not isinstance(incoming, dict):
        return base
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in incoming.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge_settings(out[key], value)
        elif key in out:
            out[key] = value
    return out


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
def _new_state() -> dict:
    return {
        "lang": "zh",
        "text": "",
        "baseline": "",
        "name": "",
        "path": "",
        "history": [],      # [{"label": str, "text": str}]
        "pointer": 0,       # number of applied stages (index into history tail)
        "verify_results": None,
        "verify_filter": "ALL",
        "progress": {"done": 0, "total": 0, "key": "", "running": False, "paused": False},
        "run_control": None,
        "tab": "format",
        "venues_only": False,
        "settings": _merge_settings(DEFAULT_SETTINGS, {}),
        "settings_dirty": False,
    }


def _push_stage(state: dict, label: str, text: str) -> None:
    history = state["history"][: state["pointer"]]
    history.append({"label": label, "text": text})
    state["history"] = history
    state["pointer"] = len(history)
    state["text"] = text


def _jump_stage(state: dict, pointer: int) -> None:
    state["pointer"] = max(0, min(pointer, len(state["history"])))
    state["text"] = (
        state["history"][state["pointer"] - 1]["text"]
        if state["pointer"] else state["baseline"]
    )


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
        return f'<div class="hx-tab-note" style="padding:12px 0">{_esc(t(lang, "no_changes"))}</div>'
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


def diff_html(before: str, after: str, mode: str, lang: str) -> str:
    rows = diff_lines(before, after)
    if mode == "changed":
        rows = [r for r in rows if r.kind != "equal"]
    if not rows:
        return f'<div class="hx-tab-note" style="padding:12px 0">{_esc(t(lang, "no_changes"))}</div>'
    body = []
    for r in rows:
        old_no = str(r.left_no or "")
        new_no = str(r.right_no or "")
        old_text = _esc(r.left if r.left is not None else "")
        new_text = _esc(r.right if r.right is not None else "")
        body.append(
            f'<tr class="{r.kind}"><td class="ln">{old_no}</td><td class="old">{old_text}</td>'
            f'<td class="ln">{new_no}</td><td class="new">{new_text}</td></tr>'
        )
    return (
        '<div style="overflow-x:auto"><table class="hx-diff"><tbody>'
        + "".join(body) + "</tbody></table></div>"
    )


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


def check_html(report, lang: str) -> str:
    issues = report.sorted()
    if not issues:
        return f'<div style="color:#15803d;font-size:14px">✔ {_esc(t(lang, "check_clean"))}</div>'
    rows = []
    for issue in issues:
        color = SEVERITY_COLOR[issue.severity]
        rows.append(
            f'<div class="hx-issue"><span class="hx-chip" style="color:{color};'
            f'background:{color}18;min-width:64px;text-align:center">'
            f'{_esc(t(lang, "sev_" + issue.severity))}</span>'
            f'<span style="font-family:ui-monospace,monospace;color:#334155">{_esc(issue.key)}</span>'
            f'<span style="color:#475569">{_esc(issue.message)}</span></div>'
        )
    return (
        f'<div class="hx-tab-note" style="margin-bottom:6px">'
        f'{report.count("error")} errors · {report.count("warning")} warnings · '
        f'{report.count("info")} suggestions</div>' + "".join(rows)
    )


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
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _copy_text(text: str, lang: str) -> None:
    try:
        ui.clipboard.write(text)
        ui.notify(t(lang, "copied"), type="positive", position="top")
    except Exception:  # noqa: BLE001 - clipboard may be unavailable
        ui.notify(t(lang, "copy_failed"), type="warning", position="top")


def _copy_handler(text: str, lang: str):
    def handler() -> None:
        _copy_text(text, lang)

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
            ).props("unelevated color=indigo")
    dialog.open()


def _download(state: dict, text: str, suffix: str = "bib") -> None:
    stem = Path(state["name"]).stem or "bibliography"
    ui.download(text.encode("utf-8"), f"{stem}.{suffix}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def register_pages() -> None:
    """Register the NiceGUI page(s) — split from ``run_gui`` so tests can reuse it."""

    @ui.page("/")
    def main_page() -> None:
        ui.add_head_html(
            "<meta name='description' content='hx-bib — format, clean, normalize, "
            "validate and verify BibTeX bibliographies. BibTeX 整理、格式校验与多源引用核查工具。'>"
            f"<style>{CSS}</style>{_QUASAR_A11Y_PATCH}"
        )
        # AA-compliant primary (NiceGUI's default #5898d4 fails contrast on white).
        ui.colors(primary="#1d4ed8", secondary="#0f766e", accent="#6d28d9")
        state = _new_state()

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
                pass

        if not _IN_SIM:
            ui.timer(0.15, load_settings, once=True)
        ui.timer(1.2, flush_settings)
        _apply_lang_attr()

        def toggle_lang() -> None:
            state["lang"] = "en" if state["lang"] == "zh" else "zh"
            state["settings"]["lang"] = state["lang"]
            state["settings_dirty"] = True
            _apply_lang_attr()
            body.refresh()

        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
            with ui.element("header").classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-baseline gap-3"):
                        with ui.element("h1").classes("m-0"):
                            ui.label("hx-bib").classes(
                                "hx-title text-3xl"
                            ).style("color:#1e293b")
                        ui.label(t(state["lang"], "subtitle")).classes("hx-tab-note text-sm")
                    with ui.row().classes("items-center gap-2"):
                        ui.button("中 / EN", on_click=toggle_lang).props(
                            "flat dense no-caps "
                            "aria-label='中 / EN — 切换语言 / Switch language'"
                        ).style("color:#3730a3;font-weight:700")

            # ---------------- body ----------------
            @ui.refreshable
            def body() -> None:
                lang = state["lang"]
                with ui.element("main").classes("w-full"):
                    if state["venues_only"]:
                        _render_venues_only(state, lang, body)
                        return
                    if not state["text"]:
                        _render_upload(state, lang, body)
                        return

                    doc = parse_bibtex(state["text"])
                    _render_status_bar(state, lang, doc, body)

                    tab_defs = [
                        ("format", t(lang, "tab_format")),
                        ("clean", t(lang, "tab_clean")),
                        ("normalize", t(lang, "tab_normalize")),
                        ("check", t(lang, "tab_check")),
                        ("verify", t(lang, "tab_verify")),
                        ("venues", t(lang, "tab_venues")),
                    ]
                    with ui.tabs(value=state["tab"]).classes("w-full").props(
                        "align=left inline-label no-caps active-color=indigo-7 "
                        "indicator-color=indigo-7"
                    ) as tabs:
                        for key, label in tab_defs:
                            ui.tab(key, label).mark(f"tab-{key}")

                    tabs.on_value_change(lambda e: state.update(tab=e.value))

                    with ui.tab_panels(tabs, value=state["tab"]).classes(
                        "w-full bg-transparent"
                    ):
                        for key, _ in tab_defs:
                            with ui.tab_panel(key).classes("p-0"):
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
    state.update(
        text=text, baseline=text, name=name, path=path,
        history=[], pointer=0, verify_results=None, tab="format",
        venues_only=False,
    )
    body.refresh()
    ui.notify(
        t(lang, "loaded").format(n=len(doc.entries), name=name),
        type="positive", position="top",
    )


def _render_upload(state: dict, lang: str, body) -> None:
    with ui.card().classes("hx-card w-full p-8 items-center gap-4"):
        ui.label(t(lang, "upload_title")).classes("text-xl font-bold")
        ui.label(t(lang, "upload_hint")).classes("hx-tab-note")

        async def on_upload(e: events.UploadEventArguments) -> None:
            data = e.file.read()
            if inspect.isawaitable(data):
                data = await data
            _load_text(state, body, lang, _read_file_bytes(data),
                       e.file.name or "uploaded.bib")

        ui.upload(
            label=t(lang, "upload_button"),
            on_upload=on_upload,
            auto_upload=True,
            max_file_size=20_000_000,
        ).props('accept=".bib,.bibtex,text/plain"').classes("w-96")

        ui.label(t(lang, "or_path")).classes("hx-tab-note mt-2")
        with ui.row().classes("items-center gap-2"):
            path_input = ui.input(placeholder=t(lang, "path_placeholder")).classes(
                "w-96"
            ).props(f"aria-label='{t(lang, 'or_path')}'").mark("path-input")

            def load_path() -> None:
                raw = (path_input.value or "").strip().strip('"')
                if not raw:
                    ui.notify(t(lang, "or_path"), type="warning", position="top")
                    return
                try:
                    text = Path(raw).expanduser().read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = Path(raw).expanduser().read_text(encoding="latin-1")
                except OSError as exc:
                    ui.notify(str(exc), type="negative", position="top")
                    return
                _load_text(state, body, lang, text, Path(raw).name,
                           str(Path(raw).expanduser()))

            ui.button(t(lang, "load"), on_click=load_path).props(
                "outline color=indigo"
            ).mark("load-btn")

        def browse_venues() -> None:
            state["venues_only"] = True
            body.refresh()

        ui.button(t(lang, "browse_venues"), on_click=browse_venues).props(
            "flat dense"
        ).style("color:#4338ca").mark("browse-venues")


def _render_venues_only(state: dict, lang: str, body) -> None:
    with ui.row().classes("w-full items-center gap-2"):
        ui.button(t(lang, "back"), icon="arrow_back", on_click=lambda: (
            state.update(venues_only=False), body.refresh(),
        )).props("flat dense").mark("venues-back")
    _panel_venues(state, lang, None, body)


# ---------------------------------------------------------------------------
# Status bar (shared working copy)
# ---------------------------------------------------------------------------
def _render_status_bar(state: dict, lang: str, doc, body) -> None:
    changed = _changed_count(state["baseline"], state["text"])

    def notify_unavailable(key: str) -> None:
        ui.notify(t(lang, key), type="warning", position="top")

    def do_undo() -> None:
        if state["pointer"] == 0:
            notify_unavailable("nothing_to_undo")
            return
        label = state["history"][state["pointer"] - 1]["label"]
        _jump_stage(state, state["pointer"] - 1)
        ui.notify(t(lang, "undo_done").format(label=label), position="top")
        body.refresh()

    def do_redo() -> None:
        if state["pointer"] >= len(state["history"]):
            notify_unavailable("nothing_to_redo")
            return
        label = state["history"][state["pointer"]]["label"]
        _jump_stage(state, state["pointer"] + 1)
        ui.notify(t(lang, "redo_done").format(label=label), position="top")
        body.refresh()

    def do_reset() -> None:
        _confirm(
            lang, t(lang, "reset_title"), t(lang, "reset_body"),
            lambda: (_jump_stage(state, 0), body.refresh(),
                     ui.notify(t(lang, "reset_done"), position="top")),
        )

    def do_close() -> None:
        def close() -> None:
            fresh = _new_state()
            fresh["settings"] = state["settings"]
            fresh["lang"] = state["lang"]
            state.clear()
            state.update(fresh)
            body.refresh()

        _confirm(lang, t(lang, "close_title"), t(lang, "close_body"), close)

    with ui.card().classes("hx-card hx-status w-full px-4 py-2 gap-1"):
        with ui.row().classes("w-full items-center gap-3 flex-wrap"):
            ui.icon("description", size="20px").style("color:#4338ca")
            ui.label(state["name"]).classes("font-semibold").mark("file-name")
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
                ui.label(t(lang, "same_as_original")).classes("hx-chip").style(
                    "background:#f1f5f9;color:#475569"
                )
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
                    ui.notify(t(lang, "no_path"), type="warning", position="top")
                    return
                try:
                    Path(state["path"]).write_text(state["text"], encoding="utf-8")
                    ui.notify(t(lang, "saved").format(path=state["path"]),
                              type="positive", position="top")
                except OSError as exc:
                    ui.notify(str(exc), type="negative", position="top")

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


def _preview_area(lang: str) -> ui.column:
    col = ui.column().classes("w-full gap-2")
    with col:
        with ui.element("div").classes("hx-empty"):
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
                    label: str = "") -> None:
    container.clear()
    with container:
        with ui.card().classes("hx-card w-full p-4 gap-3"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                ui.html(_summary_line(before, result_text, lang))
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    view_toggle = ui.toggle(
                        {"list": t(lang, "view_list"),
                         "diff": t(lang, "view_diff"),
                         "entries": t(lang, "view_entries")},
                        value="list",
                    ).props("dense unelevated no-caps").mark("view-mode")
                    mode_toggle = ui.toggle(
                        {"changed": t(lang, "changes_only"),
                         "all": t(lang, "show_all")},
                        value="changed",
                    ).props("dense outlined no-caps").mark("diff-mode")

            content_container = ui.column().classes("w-full")

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
                        ui.html(diff_html(before, result_text, mode, lang))
                    else:
                        _render_entry_cards(ui.column().classes("w-full gap-2"),
                                            result_text, opts, lang)

            view_toggle.on_value_change(lambda _: render())
            mode_toggle.on_value_change(lambda _: render())
            render()

            with ui.row().classes("w-full justify-end gap-2 flex-wrap"):
                def apply_result() -> None:
                    _push_stage(state, label or t(lang, "applied"), result_text)
                    ui.notify(
                        t(lang, "step_applied").format(n=state["pointer"], label=label),
                        type="positive", position="top",
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
                    "unelevated color=indigo"
                ).mark("apply-btn")


# -- format -----------------------------------------------------------------
def _panel_format(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_format")).classes("font-bold text-lg")
        ui.label(t(lang, "format_note")).classes("hx-tab-note")
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
        preview = _preview_area(lang)
        run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
            "unelevated color=indigo"
        ).mark("format-preview")

        async def run_format() -> None:
            opts = FormatOptions(
                indent=indent.value or "4",
                align_equals=bool(align_equals.value),
                field_order=field_order.value or "standard",
                sort_entries=sort.value or "none",
                trailing_comma=bool(trailing.value),
                drop_comments=bool(drop_comments.value),
            )
            run_button.disable()
            try:
                result = await asyncio.to_thread(
                    lambda: render_document(parse_bibtex(state["text"]), opts)
                )
            finally:
                run_button.enable()
            _render_preview(preview, state, lang, state["text"], result,
                            refresh_body, opts=opts, label=t(lang, "tab_format"))

        run_button.on_click(run_format)


# -- clean ------------------------------------------------------------------
def _panel_clean(state: dict, lang: str, doc, refresh_body) -> None:
    common = ["abstract", "keywords", "url", "doi", "isbn", "issn", "month",
              "note", "address", "editor", "publisher", "eprint", "volume", "number"]
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_clean")).classes("font-bold text-lg")
        ui.label(t(lang, "clean_note")).classes("hx-tab-note")

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
        preview = _preview_area(lang)
        run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
            "unelevated color=indigo"
        ).mark("clean-preview")

        def compute() -> str:
            work = parse_bibtex(state["text"])
            names = list(remove_select.value or [])
            if custom_remove.value:
                names += [n.strip() for n in re.split(r"[,\s]+", custom_remove.value) if n.strip()]
            if names:
                cleaner.remove_fields(work, names)
            if add_key.value and add_value.value:
                cleaner.add_fields(work, {add_key.value: add_value.value},
                                   overwrite=bool(overwrite.value))
            if drop_empty.value:
                cleaner.drop_empty_fields(work)
            if drop_others.value:
                cleaner.drop_others(work)
            if months.value:
                cleaner.normalize_months(work)
            if title_period.value:
                cleaner.strip_title_periods(work)
            if doi.value:
                cleaner.normalize_dois(work)
            if pages.value:
                cleaner.unify_page_ranges(work)
            if clean_urls.value:
                cleaner.strip_url_tracking(work)
            if dedupe.value:
                cleaner.dedupe_entries(work, keep="last" if keep_last.value else "first")
            if dedupe_title.value:
                cleaner.dedupe_by_title(work, keep="last" if keep_last.value else "first")
            return render_document(work, FormatOptions())

        async def run_clean() -> None:
            run_button.disable()
            try:
                result = await asyncio.to_thread(compute)
            finally:
                run_button.enable()
            _render_preview(preview, state, lang, state["text"], result,
                            refresh_body, opts=FormatOptions(),
                            label=t(lang, "tab_clean"))

        run_button.on_click(run_clean)


# -- normalize --------------------------------------------------------------
def _panel_normalize(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_normalize")).classes("font-bold text-lg")
        ui.label(t(lang, "normalize_note")).classes("hx-tab-note")
        with ui.row().classes("gap-6 items-start flex-wrap"):
            with ui.column().classes("gap-1"):
                ui.label(t(lang, "venue_style")).classes("text-sm font-semibold")
                venue_style = _bind(ui.radio(
                    {"abbr": t(lang, "style_abbr"),
                     "full": t(lang, "style_full"),
                     "family": t(lang, "style_family"),
                     "keep": t(lang, "style_keep")},
                    value=_opt(state, "normalize", "venue_style") or "abbr",
                ), state, "normalize", "venue_style")
            with ui.column().classes("gap-1"):
                ui.label(t(lang, "arxiv_style")).classes("text-sm font-semibold")
                arxiv_style = _bind(ui.radio(
                    {"journal": t(lang, "arxiv_journal"),
                     "misc": t(lang, "arxiv_misc"),
                     "keep": t(lang, "style_keep")},
                    value=_opt(state, "normalize", "arxiv_style") or "journal",
                ), state, "normalize", "arxiv_style")
            with ui.column().classes("gap-2 mt-6"):
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
        preview = _preview_area(lang)
        run_button = ui.button(t(lang, "preview_btn"), icon="preview").props(
            "unelevated color=indigo"
        ).mark("normalize-preview")

        def compute() -> str:
            work = parse_bibtex(state["text"])
            normalize_document(work, NormalizeOptions(
                venue_style=venue_style.value or "abbr",
                arxiv_style=arxiv_style.value or "journal",
                fix_entry_types=bool(fix_types.value),
                drop_arxiv_volume=bool(drop_volume.value),
                add_arxiv_url=bool(add_url.value),
            ))
            return render_document(work, FormatOptions())

        async def run_normalize() -> None:
            run_button.disable()
            try:
                result = await asyncio.to_thread(compute)
            finally:
                run_button.enable()
            _render_preview(preview, state, lang, state["text"], result,
                            refresh_body, opts=FormatOptions(),
                            label=t(lang, "tab_normalize"))

        run_button.on_click(run_normalize)


# -- check ------------------------------------------------------------------
def _panel_check(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_check")).classes("font-bold text-lg")
        ui.label(t(lang, "check_note")).classes("hx-tab-note")
        result_area = ui.column().classes("w-full")
        with result_area:
            with ui.element("div").classes("hx-empty"):
                ui.label(t(lang, "check_empty"))
        run_button = ui.button(t(lang, "run_check"), icon="rule").props(
            "unelevated color=indigo"
        ).mark("run-check")

        async def run_check() -> None:
            run_button.disable()
            try:
                work = parse_bibtex(state["text"])
                report = await asyncio.to_thread(
                    lambda: validate_document(work, ValidateOptions())
                )
            finally:
                run_button.enable()
            result_area.clear()
            with result_area:
                ui.html(check_html(report, lang))

            if report.issues:
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
                        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
                        f"{Path(state['name']).stem or 'bibliography'}.check.json",
                    )

                ui.button(t(lang, "download_json"), icon="download",
                          on_click=download_json).props("outline")

        run_button.on_click(run_check)


# -- verify -----------------------------------------------------------------
def _panel_verify(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_verify")).classes("font-bold text-lg")
        ui.label(t(lang, "verify_note")).classes("hx-tab-note")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            only = ui.input(t(lang, "only_keys")).classes("w-80")
            delay = _bind(ui.number(
                t(lang, "delay"), value=float(_opt(state, "verify", "delay") or 0.8),
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
                "unelevated color=indigo"
            ).mark("verify-start")
            pause_button = ui.button(t(lang, "pause"), icon="pause").props(
                "outline"
            ).mark("verify-pause")
            stop_button = ui.button(t(lang, "stop"), icon="stop").props(
                "outline color=red-7"
            ).mark("verify-stop")
        pause_button.disable()
        stop_button.disable()

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
            keys = [k.strip() for k in (only.value or "").split(",") if k.strip()]
            entries = list(doc.entries)
            if keys:
                entries = [e for e in entries if e.key in set(keys)]
            if not entries:
                ui.notify(t(lang, "no_results"), type="warning", position="top")
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
            run_button.disable()
            pause_button.enable()
            stop_button.enable()
            pause_button.set_text(t(lang, "pause"))

            net = Net(delay=float(delay.value or 0.8), use_cache=bool(use_cache.value))

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
            finally:
                progress["running"] = False
                progress["paused"] = False
                state["run_control"] = None
                run_button.enable()
                pause_button.disable()
                stop_button.disable()
                pause_button.set_text(t(lang, "pause"))

            state["verify_results"] = results
            state["verify_filter"] = "ALL"
            if control.cancelled.is_set():
                ui.notify(t(lang, "stopped_partial").format(n=len(results)),
                          type="warning", position="top")
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
                    type=color, position="top",
                )
            show_results()

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

        ui.timer(0.4, poll_progress)


# -- venues -----------------------------------------------------------------
def _venue_card(venue, lang: str) -> None:
    with ui.card().classes("hx-card p-3 gap-1"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.label(venue.abbr).style(
                "font-family:ui-monospace,monospace;font-weight:800;font-size:15px"
            )
            ui.space()
            ui.label(venue.family).classes("hx-chip").style(
                "background:#eef2ff;color:#4338ca"
            )
            ui.label(venue.kind).classes("hx-chip").style(
                "background:#f1f5f9;color:#475569"
            )
        ui.label(venue.name).classes("text-xs").style("color:#475569")
        for style, copy_key, mk in (
            ("abbr", "venue_copy_abbr", "abbr"),
            ("full", "venue_copy_full", "full"),
            ("family", "venue_copy_family", "family"),
        ):
            rendered = render_venue(venue, style=style)
            with ui.row().classes("w-full items-center gap-1 no-wrap"):
                ui.label(rendered).style(
                    "font-family:ui-monospace,monospace;font-size:11.5px;color:#334155"
                ).classes("truncate")
                ui.space()
                ui.button(
                    icon="content_copy",
                    on_click=_copy_handler(rendered, lang),
                ).props("flat dense size=sm").tooltip(t(lang, copy_key)).mark(
                    f"venue-{venue.abbr}-{mk}"
                )


def _panel_venues(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_venues")).classes("font-bold text-lg")
        ui.label(t(lang, "venues_note")).classes("hx-tab-note")
        with ui.row().classes("gap-3 items-end"):
            query = ui.input(t(lang, "search_venues")).classes("w-64").mark("venue-search")
            family = ui.select(["ALL", *families()], value="ALL",
                               label=t(lang, "family")).classes("w-40")
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
                        _venue_card(venue, lang)

        query.on_value_change(lambda _: render())
        family.on_value_change(lambda _: render())
        render()
