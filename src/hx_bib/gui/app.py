"""NiceGUI front-end for hx-bib.

One page, one job per tab:

   上传  →  load a .bib file (drag & drop or local path)
   格式化 / Format       indentation, equals alignment, field order, sorting
   清理 / Clean          remove/add fields, months, "others", dedupe
   归一化 / Normalize    venue style (abbr/full/family) and arXiv preset
   校验 / Check          offline structural validation
   校验引用 / Verify     multi-source hallucination verification
   预设 / Venues         browse venue presets

Every transform previews a **GitHub-style side-by-side red/green diff**
plus an itemized change list, switchable between "changes only" and "all".
"""

from __future__ import annotations

import asyncio
import html
import inspect
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from nicegui import events, ui

from ..core import cleaner
from ..core.diff import diff_documents, diff_lines
from ..core.model import Entry
from ..core.normalize import NormalizeOptions, normalize_document
from ..core.parser import parse_bibtex
from ..core.validator import ValidateOptions, validate_document
from ..core.venues import VENUES, families, search_venues
from ..core.writer import FormatOptions, render_document
from ..verify import Net, prefetch_arxiv, retry_failed, verify_entries
from ..verify.report import build_html as build_verify_html

from .i18n import I18N, t

SEVERITY_COLOR = {"error": "#dc2626", "warning": "#d97706", "info": "#2563eb"}
VERDICT_COLOR = {
    "FAIL": "#dc2626", "WARN": "#d97706", "MINOR": "#ca8a04",
    "INFO": "#2563eb", "OK": "#16a34a",
}

CSS = """
body { font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
       background: #f5f6f8; }
.hx-title { font-weight: 800; letter-spacing: -0.02em; }
.hx-card { border-radius: 14px; box-shadow: 0 1px 3px rgba(15,23,42,.06);
           border: 1px solid #e5e7eb; }
.hx-chip { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
           font-weight:700; white-space:nowrap; }
.hx-tab-note { color:#6b7280; font-size:13px; }
.hx-diff { width:100%; min-width:760px; border-collapse:collapse; font:12.5px/1.55 ui-monospace,
           SFMono-Regular, Menlo, Consolas, monospace; table-layout: fixed; }
.hx-diff td { padding:1px 8px; vertical-align:top; white-space:pre-wrap; word-break:break-all; }
.hx-diff td.ln { width:44px; color:#9ca3af; text-align:right; user-select:none; }
.hx-diff td.old, .hx-diff td.new { width: calc(50% - 44px); }
.hx-diff tr.del td.old { background:#fee2e2; }
.hx-diff tr.ins td.new { background:#dcfce7; }
.hx-diff tr.replace td.old { background:#fee2e2; }
.hx-diff tr.replace td.new { background:#dcfce7; }
.hx-diff tr.equal td { color:#6b7280; }
.hx-changes { width:100%; border-collapse:collapse; font-size:13.5px; }
.hx-changes th { text-align:left; font-size:12px; color:#6b7280; text-transform:uppercase;
                 letter-spacing:.05em; border-bottom:1px solid #e5e7eb; padding:6px 8px; }
.hx-changes td { padding:6px 8px; border-bottom:1px solid #f1f5f9; vertical-align:top; }
.hx-field-add { color:#15803d; background:#dcfce7; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-del { color:#b91c1c; background:#fee2e2; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-field-mod { color:#a16207; background:#fef9c3; border-radius:5px; padding:0 5px; font-size:12px; }
.hx-issue { display:flex; gap:8px; padding:4px 0; font-size:13.5px; }
.hx-result { border:1px solid #e5e7eb; border-radius:10px; padding:12px 14px; margin:10px 0;
             background:#fff; }
.hx-evidence { background:#f8fafc; border:1px dashed #e2e8f0; border-radius:8px;
               padding:8px 10px; margin-top:8px; font-size:12.5px; color:#475569; }
.hx-names { margin:4px 0 0; padding-left:20px; }
.hx-names li.bad { background:#fee2e2; color:#b91c1c; font-weight:600; border-radius:4px; }
"""


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
            "modified": "#d97706", "unchanged": "#9ca3af",
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
        f"<span style='color:#d97706'>{n_mod} {_esc(t(lang, 'kind_modified'))}</span> · "
        f"<span style='color:#16a34a'>{n_add} {_esc(t(lang, 'kind_added'))}</span> · "
        f"<span style='color:#dc2626'>{n_del} {_esc(t(lang, 'kind_removed'))}</span>"
    )


def check_html(report, lang: str) -> str:
    issues = report.sorted()
    if not issues:
        return f'<div style="color:#16a34a;font-size:14px">✔ {_esc(t(lang, "check_clean"))}</div>'
    rows = []
    for issue in issues:
        color = SEVERITY_COLOR[issue.severity]
        rows.append(
            f'<div class="hx-issue"><span class="hx-chip" style="color:{color};'
            f'background:{color}18;min-width:64px;text-align:center">'
            f'{_esc(t(lang, "sev_" + issue.severity))}</span>'
            f'<span style="font-family:ui-monospace,monospace;color:#334155">{_esc(issue.key)}</span>'
            f'<span style="color:#64748b">{_esc(issue.message)}</span></div>'
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
            f'<div class="hx-issue"><span style="color:{SEVERITY_COLOR.get("info", "#64748b")}">'
            f"•</span><span>{_esc(msg)}</span></div>"
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


def venues_html(query: str, family: str, lang: str) -> str:
    venues = search_venues(query) if query else list(VENUES)
    if family and family != "ALL":
        venues = [v for v in venues if v.family == family]
    if not venues:
        return f'<div class="hx-tab-note">{_esc(t(lang, "no_results"))}</div>'
    rows = "".join(
        f"<tr><td style='font-weight:700;font-family:ui-monospace,monospace'>{_esc(v.abbr)}</td>"
        f"<td><span class='hx-chip' style='background:#eef2ff;color:#4338ca'>{_esc(v.family)}</span></td>"
        f"<td class='hx-tab-note'>{_esc(v.kind)}</td>"
        f"<td>{_esc(v.name)}</td></tr>"
        for v in venues
    )
    return (
        '<table class="hx-changes"><thead><tr><th>abbr</th><th>family</th>'
        f"<th>kind</th><th>name</th></tr></thead><tbody>{rows}</tbody></table>"
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def _new_state() -> dict:
    return {
        "lang": "zh",
        "text": "",
        "name": "",
        "path": "",
        "verify_results": None,
        "verify_filter": "ALL",
        "progress": {"done": 0, "total": 0, "key": "", "running": False},
        "tab": "format",
    }


def _read_file_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def register_pages() -> None:
    """Register the NiceGUI page(s) — split from ``run_gui`` so tests can reuse it."""

    @ui.page("/")
    def main_page() -> None:
        ui.add_head_html(f"<style>{CSS}</style>")
        state = _new_state()

        # ---------------- header ----------------
        def toggle_lang() -> None:
            state["lang"] = "en" if state["lang"] == "zh" else "zh"
            body.refresh()

        with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-baseline gap-3"):
                    ui.label("hx-bib").classes("hx-title text-3xl").style("color:#1e293b")
                    ui.label(t(state["lang"], "subtitle")).classes("hx-tab-note text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.button("中 / EN", on_click=toggle_lang).props("flat dense").style(
                        "color:#4338ca;font-weight:700"
                    )

            # ---------------- body ----------------
            @ui.refreshable
            def body() -> None:
                lang = state["lang"]

                def load_text(text: str, name: str, path: str = "") -> None:
                    doc = parse_bibtex(text)
                    state.update(text=text, name=name, path=path,
                                 verify_results=None, tab="format")
                    body.refresh()
                    ui.notify(
                        t(lang, "loaded").format(n=len(doc.entries), name=name),
                        type="positive", position="top",
                    )

                if not state["text"]:
                    # -------- upload card --------
                    with ui.card().classes("hx-card w-full p-8 items-center gap-4"):
                        ui.label(t(lang, "upload_title")).classes("text-xl font-bold")
                        ui.label(t(lang, "upload_hint")).classes("hx-tab-note")

                        async def on_upload(e: events.UploadEventArguments) -> None:
                            data = e.file.read()
                            if inspect.isawaitable(data):
                                data = await data
                            load_text(_read_file_bytes(data), e.file.name or "uploaded.bib")

                        ui.upload(
                            label=t(lang, "upload_button"),
                            on_upload=on_upload,
                            auto_upload=True,
                            max_file_size=20_000_000,
                        ).props('accept=".bib,.bibtex,text/plain"').classes("w-96")

                        ui.label(t(lang, "or_path")).classes("hx-tab-note mt-2")
                        with ui.row().classes("items-center gap-2"):
                            path_input = ui.input(
                                placeholder=t(lang, "path_placeholder")
                            ).classes("w-96").mark("path-input")

                            def load_path() -> None:
                                raw = (path_input.value or "").strip().strip('"')
                                if not raw:
                                    return
                                try:
                                    text = Path(raw).expanduser().read_text(
                                        encoding="utf-8"
                                    )
                                except UnicodeDecodeError:
                                    text = Path(raw).expanduser().read_text(
                                        encoding="latin-1"
                                    )
                                except OSError as exc:
                                    ui.notify(str(exc), type="negative", position="top")
                                    return
                                load_text(text, Path(raw).name, str(Path(raw).expanduser()))

                            ui.button(t(lang, "load"), on_click=load_path).props(
                                "outline color=indigo"
                            ).mark("load-btn")
                    return

                doc = parse_bibtex(state["text"])

                # -------- file summary --------
                with ui.card().classes("hx-card w-full px-5 py-3"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.row().classes("items-center gap-3"):
                            ui.icon("description", size="20px").style("color:#4338ca")
                            ui.label(state["name"]).classes("font-semibold")
                            ui.label(
                                t(lang, "entries_loaded").format(n=len(doc.entries))
                            ).classes("hx-tab-note text-sm")
                            if doc.warnings:
                                ui.label(f"⚠ {len(doc.warnings)}").style(
                                    "color:#d97706;font-size:13px"
                                )
                        with ui.row().classes("gap-2"):
                            def save_to_path() -> None:
                                if not state["path"]:
                                    ui.notify(t(lang, "no_path"), type="warning")
                                    return
                                try:
                                    Path(state["path"]).write_text(
                                        state["text"], encoding="utf-8"
                                    )
                                    ui.notify(
                                        t(lang, "saved").format(path=state["path"]),
                                        type="positive",
                                    )
                                except OSError as exc:
                                    ui.notify(str(exc), type="negative")

                            ui.button(t(lang, "save_file"), on_click=save_to_path).props(
                                "flat dense color=indigo"
                            ).set_enabled(bool(state["path"]))
                            ui.button(
                                t(lang, "reload"),
                                on_click=lambda: body.refresh(),
                            ).props("flat dense")
                            ui.button(
                                t(lang, "clear"),
                                on_click=lambda: (state.clear(), state.update(_new_state()),
                                                  body.refresh()),
                            ).props("flat dense color=grey")

                # -------- tabs --------
                tab_defs = [
                    ("format", t(lang, "tab_format")),
                    ("clean", t(lang, "tab_clean")),
                    ("normalize", t(lang, "tab_normalize")),
                    ("check", t(lang, "tab_check")),
                    ("verify", t(lang, "tab_verify")),
                    ("venues", t(lang, "tab_venues")),
                ]
                with ui.tabs(value=state["tab"]).classes("w-full").props(
                    "align=left inline-label no-caps active-color=indigo-7 indicator-color=indigo-7"
                ) as tabs:
                    tab_widgets = {key: ui.tab(key, label) for key, label in tab_defs}

                tabs.on_value_change(lambda e: state.update(tab=e.value))

                with ui.tab_panels(tabs, value=state["tab"]).classes("w-full bg-transparent"):
                    for key, _ in tab_defs:
                        with ui.tab_panel(key).classes("p-0"):
                            _build_panel(key, state, lang, doc, body)

            body()


def run_gui(host: str = "127.0.0.1", port: int = 8080, show: bool = True) -> None:
    register_pages()
    ui.run(host=host, port=port, show=show, title="hx-bib", favicon="📚", reload=False)


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


def _preview_area(state: dict, lang: str) -> ui.column:
    return ui.column().classes("w-full gap-2")


def _render_preview(container: ui.column, state: dict, lang: str,
                    before: str, result_text: str, refresh_body=None) -> None:
    container.clear()
    with container:
        handle = {"text": result_text}

        with ui.card().classes("hx-card w-full p-4 gap-3"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.html(_summary_line(before, result_text, lang))
                mode_toggle = ui.toggle(
                    {"changed": t(lang, "changes_only"), "all": t(lang, "show_all")},
                    value="changed",
                ).props("dense unelevated").mark("diff-mode")

            changes_container = ui.column().classes("w-full")
            diff_container = ui.column().classes("w-full")

            def render() -> None:
                mode = mode_toggle.value or "changed"
                changes_container.clear()
                with changes_container:
                    ui.label(t(lang, "changed_entries")).style(
                        "font-size:12px;color:#6b7280;text-transform:uppercase;"
                        "letter-spacing:.05em;font-weight:600"
                    )
                    ui.html(changes_html(before, result_text, mode, lang))
                diff_container.clear()
                with diff_container:
                    ui.label(t(lang, "diff_title")).style(
                        "font-size:12px;color:#6b7280;text-transform:uppercase;"
                        "letter-spacing:.05em;font-weight:600"
                    )
                    ui.html(diff_html(before, result_text, mode, lang))

            mode_toggle.on_value_change(lambda _: render())
            render()

            with ui.row().classes("w-full justify-end gap-2"):
                def apply_result() -> None:
                    state["text"] = handle["text"]
                    ui.notify(t(lang, "applied"), type="positive", position="top")
                    if refresh_body is not None:
                        refresh_body.refresh()

                def download_result() -> None:
                    stem = Path(state["name"]).stem or "bibliography"
                    ui.download(handle["text"].encode("utf-8"),
                                f"{stem}.formatted.bib")

                ui.button(t(lang, "apply_btn"), on_click=apply_result).props(
                    "unelevated color=indigo"
                ).mark("apply-btn")
                ui.button(t(lang, "download"), on_click=download_result).props("outline")


def _panel_format(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_format")).classes("font-bold text-lg")
        ui.label(t(lang, "format_note")).classes("hx-tab-note")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            indent = ui.select(
                {"4": "4 spaces", "2": "2 spaces", "0": "0", "tab": "tab", "align": "align"},
                value="4", label=t(lang, "opt_indent"),
            ).classes("w-36")
            field_order = ui.select(
                {"standard": t(lang, "order_standard"),
                 "alphabetical": t(lang, "order_alpha"),
                 "keep": t(lang, "order_keep")},
                value="standard", label=t(lang, "opt_order"),
            ).classes("w-44")
            sort = ui.select(
                {"none": t(lang, "sort_none"), "key": "key", "year": "year",
                 "type": "type", "title": "title"},
                value="none", label=t(lang, "opt_sort"),
            ).classes("w-36")
            align_equals = ui.switch(t(lang, "opt_align_equals"))
            trailing = ui.switch(t(lang, "opt_trailing_comma"))
            drop_comments = ui.switch(t(lang, "opt_drop_comments"))

        preview = _preview_area(state, lang)

        def run_format() -> None:
            opts = FormatOptions(
                indent=indent.value or "4",
                align_equals=bool(align_equals.value),
                field_order=field_order.value or "standard",
                sort_entries=sort.value or "none",
                trailing_comma=bool(trailing.value),
                drop_comments=bool(drop_comments.value),
            )
            result = render_document(parse_bibtex(state["text"]), opts)
            _render_preview(preview, state, lang, state["text"], result, refresh_body)

        ui.button(t(lang, "preview_btn"), on_click=run_format).props(
            "unelevated color=indigo"
        ).mark("format-preview")


def _panel_clean(state: dict, lang: str, doc, refresh_body) -> None:
    common = ["abstract", "keywords", "url", "doi", "isbn", "issn", "month",
              "note", "address", "editor", "publisher", "eprint", "volume", "number"]
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_clean")).classes("font-bold text-lg")
        ui.label(t(lang, "clean_note")).classes("hx-tab-note")
        ui.label(t(lang, "remove_fields")).classes("text-sm font-semibold mt-1")
        remove_select = ui.select(
            common, value=[], multiple=True, label=t(lang, "remove_fields"),
        ).props("use-chips outlined dense").classes("w-full")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            custom_remove = ui.input(t(lang, "custom_remove")).classes("w-72")
            add_key = ui.input(t(lang, "add_key")).classes("w-44")
            add_value = ui.input(t(lang, "add_value")).classes("w-72")
            overwrite = ui.switch(t(lang, "overwrite"))
        with ui.row().classes("gap-6 flex-wrap mt-1"):
            drop_empty = ui.switch(t(lang, "drop_empty"))
            drop_others = ui.switch(t(lang, "drop_others"))
            months = ui.switch(t(lang, "months"))
            title_period = ui.switch(t(lang, "title_period"))
            dedupe = ui.switch(t(lang, "dedupe"))

        preview = _preview_area(state, lang)

        def run_clean() -> None:
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
            if dedupe.value:
                cleaner.dedupe_entries(work)
            result = render_document(work, FormatOptions())
            _render_preview(preview, state, lang, state["text"], result, refresh_body)

        ui.button(t(lang, "preview_btn"), on_click=run_clean).props(
            "unelevated color=indigo"
        ).mark("clean-preview")


def _panel_normalize(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_normalize")).classes("font-bold text-lg")
        ui.label(t(lang, "normalize_note")).classes("hx-tab-note")
        with ui.row().classes("gap-6 items-start flex-wrap"):
            with ui.column().classes("gap-1"):
                ui.label(t(lang, "venue_style")).classes("text-sm font-semibold")
                venue_style = ui.radio(
                    {"abbr": t(lang, "style_abbr"),
                     "full": t(lang, "style_full"),
                     "family": t(lang, "style_family"),
                     "keep": t(lang, "style_keep")},
                    value="abbr",
                )
            with ui.column().classes("gap-1"):
                ui.label(t(lang, "arxiv_style")).classes("text-sm font-semibold")
                arxiv_style = ui.radio(
                    {"journal": t(lang, "arxiv_journal"),
                     "misc": t(lang, "arxiv_misc"),
                     "keep": t(lang, "style_keep")},
                    value="journal",
                )
            with ui.column().classes("gap-2 mt-6"):
                fix_types = ui.switch(t(lang, "fix_types"), value=True)
                drop_volume = ui.switch(t(lang, "drop_arxiv_volume"), value=True)
                add_url = ui.switch(t(lang, "add_arxiv_url"), value=True)

        preview = _preview_area(state, lang)

        def run_normalize() -> None:
            work = parse_bibtex(state["text"])
            normalize_document(work, NormalizeOptions(
                venue_style=venue_style.value or "abbr",
                arxiv_style=arxiv_style.value or "journal",
                fix_entry_types=bool(fix_types.value),
                drop_arxiv_volume=bool(drop_volume.value),
                add_arxiv_url=bool(add_url.value),
            ))
            result = render_document(work, FormatOptions())
            _render_preview(preview, state, lang, state["text"], result, refresh_body)

        ui.button(t(lang, "preview_btn"), on_click=run_normalize).props(
            "unelevated color=indigo"
        ).mark("normalize-preview")


def _panel_check(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_check")).classes("font-bold text-lg")
        ui.label(t(lang, "check_note")).classes("hx-tab-note")
        result_area = ui.column().classes("w-full")

        def run_check() -> None:
            work = parse_bibtex(state["text"])
            report = validate_document(work, ValidateOptions())
            result_area.clear()
            with result_area:
                ui.html(check_html(report, lang))

            if report.issues:
                def download_json() -> None:
                    import json as _json
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
                        _json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
                        f"{Path(state['name']).stem or 'bibliography'}.check.json",
                    )

                ui.button(t(lang, "download_json"), on_click=download_json).props("outline")

        ui.button(t(lang, "run_check"), on_click=run_check).props(
            "unelevated color=indigo"
        )


def _panel_verify(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_verify")).classes("font-bold text-lg")
        ui.label(t(lang, "verify_note")).classes("hx-tab-note")
        with ui.row().classes("gap-4 items-end flex-wrap"):
            only = ui.input(t(lang, "only_keys")).classes("w-80")
            delay = ui.number(t(lang, "delay"), value=0.8, min=0.0, step=0.1).classes("w-32")
            use_cache = ui.switch(t(lang, "use_cache"), value=True)

        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress_label = ui.label("").classes("hx-tab-note")
        run_button = ui.button(t(lang, "start_verify")).props("unelevated color=indigo")

        filter_row = ui.row().classes("gap-2 items-center")
        results_area = ui.column().classes("w-full")

        def show_results() -> None:
            results = state["verify_results"]
            results_area.clear()
            filter_row.clear()
            if not results:
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

                ui.button(t(lang, "download_report"), on_click=download_report).props(
                    "dense outline"
                ).style("margin-left:auto")
            with results_area:
                ui.html(verify_html(results, state["verify_filter"], lang))

        show_results()

        async def run_verify() -> None:
            if state["progress"]["running"]:
                return
            keys = [k.strip() for k in (only.value or "").split(",") if k.strip()]
            entries = doc.entries
            if keys:
                entries = [e for e in entries if e.key in set(keys)]
            if not entries:
                ui.notify(t(lang, "no_results"), type="warning")
                return

            state["progress"] = {"done": 0, "total": len(entries), "key": "", "running": True}
            state["verify_results"] = None
            run_button.disable()
            results_area.clear()

            net = Net(delay=float(delay.value or 0.8), use_cache=bool(use_cache.value))

            def work() -> list:
                prefetch_arxiv(net, entries)

                def cb(idx: int, total: int, citekey: str) -> None:
                    state["progress"].update(done=idx, total=total, key=citekey)

                results = verify_entries(net, entries, progress=cb)
                pending = [r for r in results if r.any_error and r.verdict != "OK"]
                if pending:
                    results = retry_failed(net, results)
                return results

            try:
                results = await asyncio.to_thread(work)
                state["verify_results"] = results
                state["verify_filter"] = "ALL"
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
            finally:
                state["progress"]["running"] = False
                run_button.enable()

        run_button.on_click(run_verify)

        # Progress polling (network work runs in a thread).
        def poll_progress() -> None:
            progress = state["progress"]
            if progress["total"]:
                progress_bar.set_value(progress["done"] / progress["total"])
                progress_label.set_text(
                    f"{progress['done']}/{progress['total']}  {progress['key']}"
                )

        ui.timer(0.4, poll_progress)


def _panel_venues(state: dict, lang: str, doc, refresh_body) -> None:
    with ui.card().classes("hx-card w-full p-4 gap-3"):
        ui.label(t(lang, "tab_venues")).classes("font-bold text-lg")
        ui.label(t(lang, "venues_note")).classes("hx-tab-note")
        with ui.row().classes("gap-3 items-end"):
            query = ui.input(t(lang, "search_venues")).classes("w-64")
            family = ui.select(["ALL", *families()], value="ALL",
                               label=t(lang, "family")).classes("w-40")
        table_area = ui.column().classes("w-full")

        def render() -> None:
            table_area.clear()
            with table_area:
                ui.html(venues_html(query.value or "", family.value or "ALL", lang))

        query.on_value_change(lambda _: render())
        family.on_value_change(lambda _: render())
        render()
