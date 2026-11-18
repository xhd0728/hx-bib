"""hx-bib command line interface (Typer + Rich).

Commands
--------
format     re-render a .bib file with configurable indentation/order/sort
clean      remove/add/rename fields, dedupe, drop "others", fix months
normalize  canonicalize venues (abbr/full/family) and arXiv entries
check      offline structural validation
verify     multi-source hallucination verification (arXiv/Crossref/DBLP/OpenReview)
venues     browse the built-in venue presets
table      format LaTeX tabular environments (one row per line, tidy &/rules)
gui        launch the local NiceGUI app
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.style import Style
from rich.table import Table
from rich.text import Text

from . import __version__
from .core import cleaner
from .core.diff import diff_documents, diff_lines
from .core.model import Document
from .core.normalize import NormalizeOptions, normalize_document
from .core.parser import parse_bibtex
from .core.validator import (
    ValidateOptions,
    validate_document,
)
from .core.venues import VENUES, families, search_venues
from .core.writer import FormatOptions, render_document
from .verify import SEVERITY_LABEL, Net, prefetch_arxiv, retry_failed, verify_entries
from .verify.report import write_reports

app = typer.Typer(
    name="hx-bib",
    help="hx-bib — format, clean, normalize, validate and verify BibTeX files.",
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=False,
)
venues_app = typer.Typer(help="Browse the built-in venue presets.", no_args_is_help=True)
app.add_typer(venues_app, name="venues")

console = Console()
err_console = Console(stderr=True)

SEV_STYLE = {"error": "bold red", "warning": "yellow", "info": "cyan"}
VERDICT_STYLE = {
    "FAIL": "bold red",
    "WARN": "yellow",
    "MINOR": "dark_orange3",
    "INFO": "cyan",
    "OK": "green",
}
VERDICT_ICON = {"FAIL": "✖", "WARN": "▲", "MINOR": "●", "INFO": "ℹ", "OK": "✔"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _version_callback(value: bool) -> None:
    if value:
        console.print(f"hx-bib {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback,
        is_eager=True, help="Show version and exit.",
    ),
) -> None:
    """hx-bib — a tidy toolbox for BibTeX."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc


def _load(path: Path) -> Tuple[str, Document]:
    text = _read_text(path)
    doc = parse_bibtex(text)
    for warning in doc.warnings:
        err_console.print(f"[yellow]⚠ {warning}[/]")
    if not doc.entries:
        err_console.print(f"[yellow]⚠ no BibTeX entries found in {path}[/]")
    return text, doc


def _parse_pairs(items: List[str], flag: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise typer.BadParameter(f"{flag} expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        pairs[key.strip().lower()] = value.strip()
    return pairs


def _format_options(
    indent: str,
    align_equals: bool,
    field_order: str,
    sort: str,
    trailing_comma: bool,
    drop_comments: bool,
) -> FormatOptions:
    opts = FormatOptions(
        indent=indent,
        align_equals=align_equals,
        field_order=field_order,
        sort_entries=sort,
        trailing_comma=trailing_comma,
        drop_comments=drop_comments,
    )
    try:
        return opts.validate()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _print_itemized_changes(before_text: str, after_text: str) -> None:
    changes = diff_documents(parse_bibtex(before_text), parse_bibtex(after_text))
    if not changes.changed:
        console.print("[dim]No entry-level changes.[/]")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("entry", style="bold cyan", no_wrap=True)
    table.add_column("change", no_wrap=True)
    table.add_column("details", overflow="fold")
    for d in changes.changed:
        detail = []
        if d.type_changed and d.before is not None and d.after is not None:
            detail.append(f"type {d.before.type} → {d.after.type}")
        for fc in d.field_changes:
            if fc.kind == "added":
                detail.append(f"[green]+{fc.field_name}[/]")
            elif fc.kind == "removed":
                detail.append(f"[red]-{fc.field_name}[/]")
            else:
                detail.append(f"[yellow]~{fc.field_name}[/]")
        kind_style = {"added": "green", "removed": "red", "modified": "yellow"}[d.kind]
        table.add_row(d.key, f"[{kind_style}]{d.kind}[/]", "  ".join(detail))
    console.print(Panel(table, title="Changed entries", border_style="cyan", expand=False))


def _print_line_diff(before_text: str, after_text: str, show_all: bool = False) -> None:
    rows = diff_lines(before_text, after_text)
    if not show_all:
        rows = [r for r in rows if r.kind != "equal"]
    if not rows:
        console.print("[green]No textual changes.[/]")
        return
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1)
    grid.add_column(justify="right", style="dim", width=5)
    grid.add_column(overflow="fold", ratio=1)
    grid.add_column(justify="right", style="dim", width=5)
    grid.add_column(overflow="fold", ratio=1)
    grid.add_column(width=1)
    for row in rows:
        old_no = str(row.left_no or "")
        new_no = str(row.right_no or "")
        old_text = row.left if row.left is not None else ""
        new_text = row.right if row.right is not None else ""
        if row.kind == "equal":
            grid.add_row("", old_no, Text(old_text, style="dim"), new_no, Text(new_text, style="dim"), "")
        elif row.kind == "delete":
            grid.add_row("", old_no, Text(old_text, style="on #4c1d1f"), "", "", "[red]-[/]")
        elif row.kind == "insert":
            grid.add_row("", "", "", new_no, Text(new_text, style="on #14321e"), "[green]+[/]")
        else:
            grid.add_row("", old_no, Text(old_text, style="on #4c1d1f"), new_no,
                         Text(new_text, style="on #14321e"), "[yellow]~[/]")
    console.print(Panel(grid, title="Diff (old ⟶ new)", border_style="dim", expand=True))


def _finish(
    original_path: Path,
    before_text: str,
    after_text: str,
    output: Optional[Path],
    dry_run: bool,
    to_stdout: bool,
    notes: Optional[List[str]] = None,
) -> None:
    if notes:
        console.print(f"[bold]{len(notes)} change(s):[/]")
        for note in notes[:50]:
            console.print(f"  [dim]·[/] {note}")
        if len(notes) > 50:
            console.print(f"  [dim]… and {len(notes) - 50} more[/]")

    if to_stdout:
        sys.stdout.write(after_text)
        return
    if dry_run:
        _print_itemized_changes(before_text, after_text)
        _print_line_diff(before_text, after_text)
        return

    target = output or original_path
    target.write_text(after_text, encoding="utf-8")
    doc = parse_bibtex(after_text)
    console.print(
        f"[green]✔[/] wrote [bold]{target}[/] "
        f"[dim]({len(doc.entries)} entries)[/]"
    )


# ---------------------------------------------------------------------------
# format
# ---------------------------------------------------------------------------
@app.command("format")
def format_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input .bib file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to this file instead of in place."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the result to stdout instead of writing."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show itemized + line-level diff, write nothing."),
    indent: str = typer.Option("4", "--indent", "-i", help="Indent: spaces (2, 4, …), 'tab' or 'align'."),
    align_equals: bool = typer.Option(False, "--align-equals/--no-align-equals", help="Align '=' signs."),
    field_order: str = typer.Option("standard", "--field-order", "-f", help="standard | alphabetical | keep."),
    sort: str = typer.Option("none", "--sort", "-s", help="none | key | year | type | title."),
    trailing_comma: bool = typer.Option(False, "--trailing-comma", help="Keep a comma after the last field."),
    drop_comments: bool = typer.Option(False, "--drop-comments", help="Remove comments."),
) -> None:
    """Re-render a .bib file with a consistent style."""
    opts = _format_options(indent, align_equals, field_order, sort, trailing_comma, drop_comments)
    text, doc = _load(file)
    after = render_document(doc, opts)
    _finish(file, text, after, output, dry_run, stdout)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------
@app.command("clean")
def clean_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input .bib file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    stdout: bool = typer.Option(False, "--stdout"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show diff, write nothing."),
    remove: List[str] = typer.Option([], "--remove", "-r", help="Field to remove (repeatable)."),
    keep: List[str] = typer.Option([], "--keep", "-k", help="Only keep these fields (repeatable)."),
    add: List[str] = typer.Option([], "--add", "-a", help="field=value to add (repeatable)."),
    overwrite: bool = typer.Option(False, "--overwrite", help="With --add, overwrite existing values."),
    rename: List[str] = typer.Option([], "--rename", help="old=new field rename (repeatable)."),
    drop_empty: bool = typer.Option(False, "--drop-empty", help="Remove empty fields."),
    drop_others: bool = typer.Option(False, "--drop-others", help="Strip trailing 'and others'."),
    months: bool = typer.Option(False, "--months", help="Normalize months to jan…dec macros."),
    title_period: bool = typer.Option(False, "--title-period", help="Strip a trailing '.' from titles."),
    dedupe: bool = typer.Option(False, "--dedupe", help="Drop duplicate cite keys."),
    keep_last: bool = typer.Option(False, "--keep-last", help="With --dedupe, keep the last duplicate."),
    doi: bool = typer.Option(False, "--doi", help="Strip https://doi.org/ & doi: prefixes from DOIs."),
    pages: bool = typer.Option(False, "--pages", help="Normalize page ranges to 'start--end'."),
    dedupe_title: bool = typer.Option(False, "--dedupe-title", help="Drop entries with a repeated title."),
    clean_urls: bool = typer.Option(False, "--clean-urls", help="Remove utm_*/fbclid/gclid tracking params from URLs."),
) -> None:
    """Clean fields and entries (composable with --dry-run diff preview)."""
    text, doc = _load(file)
    notes: List[str] = []
    if remove:
        notes += cleaner.remove_fields(doc, remove)
    if keep:
        notes += cleaner.keep_fields(doc, keep)
    if add:
        notes += cleaner.add_fields(doc, _parse_pairs(add, "--add"), overwrite=overwrite)
    if rename:
        notes += cleaner.rename_fields(doc, _parse_pairs(rename, "--rename"))
    if drop_empty:
        notes += cleaner.drop_empty_fields(doc)
    if drop_others:
        notes += cleaner.drop_others(doc)
    if months:
        notes += cleaner.normalize_months(doc)
    if title_period:
        notes += cleaner.strip_title_periods(doc)
    if doi:
        notes += cleaner.normalize_dois(doc)
    if pages:
        notes += cleaner.unify_page_ranges(doc)
    if clean_urls:
        notes += cleaner.strip_url_tracking(doc)
    if dedupe:
        notes += cleaner.dedupe_entries(doc, keep="last" if keep_last else "first")
    if dedupe_title:
        notes += cleaner.dedupe_by_title(doc, keep="last" if keep_last else "first")
    after = render_document(doc, FormatOptions())
    _finish(file, text, after, output, dry_run, stdout, notes)


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------
@app.command("normalize")
def normalize_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input .bib file."),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    stdout: bool = typer.Option(False, "--stdout"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show diff, write nothing."),
    venue_style: str = typer.Option(
        "abbr", "--venue-style",
        help="abbr ('Proceedings of EMNLP') | full | family (ARR/ACM/…) | keep.",
    ),
    arxiv_style: str = typer.Option(
        "journal", "--arxiv-style",
        help="journal ('arXiv preprint arXiv:…') | misc ('@misc' + eprint) | keep.",
    ),
    fix_types: bool = typer.Option(True, "--fix-types/--no-fix-types", help="Fix article↔inproceedings mismatches."),
    drop_arxiv_volume: bool = typer.Option(True, "--drop-arxiv-volume/--keep-arxiv-volume"),
    add_arxiv_url: bool = typer.Option(True, "--add-arxiv-url/--no-arxiv-url"),
) -> None:
    """Canonicalize venue names and arXiv entries."""
    opts = NormalizeOptions(
        venue_style=venue_style,
        arxiv_style=arxiv_style,
        fix_entry_types=fix_types,
        drop_arxiv_volume=drop_arxiv_volume,
        add_arxiv_url=add_arxiv_url,
    )
    try:
        opts.validate()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    text, doc = _load(file)
    notes = normalize_document(doc, opts)
    after = render_document(doc, FormatOptions())
    _finish(file, text, after, output, dry_run, stdout, notes)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
@app.command("check")
def check_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input .bib file."),
    as_json: Optional[Path] = typer.Option(None, "--json", help="Also write the report as JSON."),
    strict: bool = typer.Option(False, "--strict", help="Treat warnings as failures too."),
    show: str = typer.Option("all", "--show", help="all | issues (hide suggestions)."),
) -> None:
    """Offline structural validation (no network)."""
    _, doc = _load(file)
    report = validate_document(doc, ValidateOptions())

    issues = report.sorted()
    if show == "issues":
        issues = [i for i in issues if i.severity != "info"]

    if issues:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold", show_lines=False)
        table.add_column("severity", no_wrap=True)
        table.add_column("entry", style="bold cyan", no_wrap=True)
        table.add_column("field", style="dim", no_wrap=True)
        table.add_column("message", overflow="fold")
        for issue in issues:
            table.add_row(
                f"[{SEV_STYLE[issue.severity]}]{issue.severity}[/]",
                issue.key,
                issue.field_name or "",
                issue.message,
            )
        console.print(table)
    else:
        console.print("[green]No issues found.[/]")

    counts = (
        f"[red]{report.count('error')} error(s)[/], "
        f"[yellow]{report.count('warning')} warning(s)[/], "
        f"[cyan]{report.count('info')} suggestion(s)[/]"
    )
    console.print(Panel(counts, title=f"check · {file.name}", border_style="cyan", expand=False))

    if as_json:
        payload = {
            "tool": "hx-bib",
            "file": str(file),
            "issues": [
                {
                    "severity": i.severity,
                    "key": i.key,
                    "field": i.field_name,
                    "line": i.line,
                    "message": i.message,
                }
                for i in report.sorted()
            ],
        }
        as_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[dim]JSON → {as_json}[/]")

    failed = bool(report.errors) or (strict and bool(report.warnings))
    raise typer.Exit(code=1 if failed else 0)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------
@app.command("verify")
def verify_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Input .bib file."),
    html: Optional[Path] = typer.Option(None, "--html", help="Write an HTML report here."),
    as_json: Optional[Path] = typer.Option(None, "--json", help="Write a JSON report here."),
    only: List[str] = typer.Option([], "--only", help="Only verify these cite keys (repeatable)."),
    delay: float = typer.Option(0.8, "--delay", min=0.0, help="Seconds between requests (politeness)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the on-disk HTTP cache."),
    verbose: bool = typer.Option(False, "--verbose", help="Print every HTTP request."),
    retries: int = typer.Option(1, "--retries", min=0, max=3, help="Retry passes for rate-limited entries."),
    workers: int = typer.Option(1, "--workers", "-j", min=1, max=8, help="Concurrent workers (per-host politeness is still enforced)."),
) -> None:
    """Verify citations against arXiv / Crossref / DBLP / OpenReview."""
    _, doc = _load(file)
    entries = doc.entries
    if only:
        wanted = set(only)
        entries = [e for e in entries if e.key in wanted]
        missing = wanted - {e.key for e in entries}
        if missing:
            err_console.print(f"[red]unknown cite key(s): {', '.join(sorted(missing))}[/]")
    if not entries:
        err_console.print("[red]nothing to verify[/]")
        raise typer.Exit(2)

    net = Net(delay=delay, use_cache=not no_cache, verbose=verbose)
    console.print(f"Verifying [bold]{len(entries)}[/] entr{'y' if len(entries)==1 else 'ies'} from [bold]{file}[/]")
    prefetch_arxiv(net, entries)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("verifying", total=len(entries))

        def on_progress(idx: int, total: int, key: str) -> None:
            progress.update(task, completed=idx, description=f"checking {key}")

        results = verify_entries(net, entries, progress=on_progress, workers=workers)

    for attempt in range(retries):
        pending = [r for r in results if r.any_error and r.verdict != "OK"]
        if not pending:
            break
        console.print(f"[dim]retry pass {attempt + 1}: {len(pending)} rate-limited entr"
                      f"{'y' if len(pending)==1 else 'ies'}…[/]")
        results = retry_failed(net, results)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("verdict", no_wrap=True)
    table.add_column("entry", style="bold cyan", no_wrap=True)
    table.add_column("summary", overflow="fold")
    for res in results:
        style = VERDICT_STYLE[res.verdict]
        first = res.issues[0][1] if res.issues else "confirmed"
        table.add_row(
            f"[{style}]{VERDICT_ICON[res.verdict]} {SEVERITY_LABEL[res.verdict]}[/]",
            res.entry.key, first,
        )
    console.print(table)

    counts: Dict[str, int] = {}
    for res in results:
        counts[res.verdict] = counts.get(res.verdict, 0) + 1
    summary_bits = []
    for verdict in ("FAIL", "WARN", "MINOR", "INFO", "OK"):
        if counts.get(verdict):
            summary_bits.append(f"[{VERDICT_STYLE[verdict]}]{counts[verdict]} {SEVERITY_LABEL[verdict]}[/]")
    console.print(Panel(" · ".join(summary_bits), title="verify summary", border_style="cyan", expand=False))

    flagged = [r for r in results if r.verdict in ("FAIL", "WARN")]
    if flagged:
        console.print("\n[bold]Needs attention:[/]")
        for res in flagged:
            style = VERDICT_STYLE[res.verdict]
            console.print(f"  [{style}]{VERDICT_ICON[res.verdict]} {res.entry.key}[/]")

    written = write_reports(
        results, str(file),
        html_path=str(html) if html else "",
        json_path=str(as_json) if as_json else "",
    )
    for path in written:
        console.print(f"[dim]report → {path}[/]")

    raise typer.Exit(code=1 if counts.get("FAIL") else 0)


# ---------------------------------------------------------------------------
# venues
# ---------------------------------------------------------------------------
@venues_app.command("list")
def venues_list(
    family: Optional[str] = typer.Option(None, "--family", help="Filter by family (ARR, ACM, IEEE, …)."),
    kind: Optional[str] = typer.Option(None, "--kind", help="conference | journal."),
) -> None:
    """List the built-in venue presets."""
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("abbr", style="bold cyan", no_wrap=True)
    table.add_column("family", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("name", overflow="fold")
    shown = 0
    for venue in VENUES:
        if family and venue.family.lower() != family.lower():
            continue
        if kind and venue.kind != kind.lower():
            continue
        table.add_row(venue.abbr, venue.family, venue.kind, venue.name)
        shown += 1
    console.print(table)
    fams = ", ".join(families())
    console.print(f"[dim]{shown} venue(s) · families: {fams}[/]")


@venues_app.command("search")
def venues_search(
    query: str = typer.Argument(..., help="Free-text query (abbr, name or family)."),
) -> None:
    """Search venue presets."""
    results = search_venues(query)
    if not results:
        console.print("[yellow]No matching venue.[/]")
        raise typer.Exit(code=1)
    for venue in results:
        console.print(
            f"[bold cyan]{venue.abbr}[/]  [dim]{venue.family} · {venue.kind}[/]  {venue.name}"
        )


# ---------------------------------------------------------------------------
# table
# ---------------------------------------------------------------------------
@app.command("table")
def table_cmd(
    file: str = typer.Argument(..., help="Input .tex file, or '-' for stdin."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to this file instead of in place."),
    stdout: bool = typer.Option(False, "--stdout", help="Print the result to stdout instead of writing."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show diff, write nothing."),
    amp: str = typer.Option(
        "space", "--amp",
        help="Column separator style: space (a & b) | align (pad cells) | none (a&b).",
    ),
    blank_lines: bool = typer.Option(
        True, "--blank-lines/--no-blank-lines",
        help="Blank line around rules (\\toprule, \\midrule, …).",
    ),
    indent: int = typer.Option(
        0, "--indent", "-i", min=0,
        help="Extra spaces before each formatted row.",
    ),
) -> None:
    """Format LaTeX tabular environments: one row per line, tidy & and rules."""
    from .core.latex_table import TableFormatOptions, format_tables

    opts = TableFormatOptions(
        amp_style=amp,
        blank_lines_around_rules=blank_lines,
        row_indent=indent,
    )
    try:
        opts.validate()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if file == "-":
        text = sys.stdin.read()
        input_path: Optional[Path] = None
        label = "<stdin>"
    else:
        input_path = Path(file)
        if not input_path.is_file():
            raise typer.BadParameter(f"not a file: {file}")
        text = _read_text(input_path)
        label = str(input_path)

    result = format_tables(text, opts)
    for warning in result.warnings:
        err_console.print(f"[yellow]⚠ {warning}[/]")

    if result.tables == 0:
        console.print(f"[yellow]No tabular-like environment found in {label}.[/]")

    if stdout:
        sys.stdout.write(result.text)
        return
    if dry_run:
        if result.tables:
            _print_line_diff(text, result.text)
        console.print(Panel(
            f"{result.tables} table(s) · {result.rows} row(s) · "
            f"amp={opts.amp_style} · blank-lines={'on' if blank_lines else 'off'}",
            title=f"table · {label}", border_style="cyan", expand=False,
        ))
        return

    if result.text == text:
        console.print(f"[green]✔[/] {label} is already tidy [dim]({result.tables} table(s), {result.rows} row(s))[/]")
        return
    target = output or input_path
    if target is None:                                # stdin without -o
        sys.stdout.write(result.text)
        return
    target.write_text(result.text, encoding="utf-8")
    console.print(
        f"[green]✔[/] wrote [bold]{target}[/] "
        f"[dim]({result.tables} table(s), {result.rows} row(s))[/]"
    )


# ---------------------------------------------------------------------------
# gui
# ---------------------------------------------------------------------------
@app.command("gui")
def gui_cmd(
    port: int = typer.Option(8080, "--port", "-p", help="Local port to listen on."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host/interface to bind."),
    show: bool = typer.Option(True, "--show/--no-show", help="Open the browser automatically."),
    debug: bool = typer.Option(False, "--debug", help="Print detailed server and HTTP logs to the terminal."),
) -> None:
    """Launch the local NiceGUI app (中文 / English)."""
    try:
        from .gui.app import run_gui
    except ImportError as exc:  # pragma: no cover
        err_console.print(f"[red]GUI dependencies missing:[/] {exc}")
        err_console.print("[dim]Install them with: pip install nicegui[/]")
        raise typer.Exit(2) from exc
    _print_gui_banner(host, port, show, debug)
    run_gui(host=host, port=port, show=show, debug=debug)


def _print_gui_banner(host: str, port: int, show: bool, debug: bool = False) -> None:
    bind_host = host.strip("[]")
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(bind_host, bind_host)
    address = f"[{browser_host}]" if ":" in browser_host else browser_host
    url = f"http://{address}:{port}"
    if not console.is_terminal:
        console.print(Text(f"hx-bib v{__version__} | Web workspace | {url}" + (" | DEBUG" if debug else "")))
        return
    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column()
    details.add_row("Workspace", "BibTeX / LaTeX Tables")
    details.add_row("Address", Text(url, style=Style(color="cyan", underline=True, link=url)))
    if browser_host != bind_host:
        details.add_row("Listening", Text(f"{host} (all interfaces)"))
    details.add_row("Browser", "Opens automatically" if show else "Open the address above")
    if debug:
        details.add_row("Logging", "DEBUG (terminal)")
    details.add_row("Stop", Text("Ctrl+C", style="bold"))
    console.print(Panel(
        details, title=f"[bold]hx-bib[/] [dim]v{__version__}[/]", title_align="left",
        box=box.ROUNDED, border_style="blue", padding=(1, 2), expand=False,
    ))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
