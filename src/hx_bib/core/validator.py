"""Structural validation of a parsed BibTeX document.

This module is *offline*: it checks field presence, value shapes, entry
types, duplicate keys, placeholder venues and common style traps (trailing
"and others", full month names, dangling title periods, …).  Network-based
verification of whether a citation actually exists lives in
:mod:`hx_bib.verify`.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .identifiers import extract_arxiv_id, is_arxiv_like
from .model import Document, Entry
from .normalize import unmatched_venues
from .venues import JOURNAL, match_venue

ERROR = "error"
WARNING = "warning"
INFO = "info"

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

REQUIRED_FIELDS: Dict[str, Sequence[str]] = {
    "article": ("author", "title", "journal", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "incollection": ("author", "title", "booktitle", "year"),
    "book": ("title", "publisher", "year"),
    "phdthesis": ("author", "title", "school", "year"),
    "mastersthesis": ("author", "title", "school", "year"),
    "techreport": ("author", "title", "institution", "year"),
}

_PAGES_RE = re.compile(r"^\s*[A-Za-z]?\d+(?::\d+)?\s*(--|-{1,2}|–)\s*[A-Za-z]?\d+(?::\d+)?\s*$")
_SINGLE_PAGE_RE = re.compile(r"^\s*[A-Za-z]?\d+(?::\d+)?\s*$")
_YEAR_RE = re.compile(r"^\d{4}$")
_PLACEHOLDER_VENUES = {"tbd", "todo", "unknown", "n/a", "none", "placeholder", "to be determined"}
_FULL_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}


@dataclass
class Issue:
    severity: str
    key: str
    message: str
    field_name: Optional[str] = None
    line: int = 0

    def sort_key(self) -> tuple:
        return (_SEVERITY_ORDER.get(self.severity, 9), self.line, self.key)


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i.severity == severity)

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def infos(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == INFO]

    def sorted(self) -> List[Issue]:
        return sorted(self.issues, key=lambda i: i.sort_key())


@dataclass
class ValidateOptions:
    required_fields: bool = True
    check_venues: bool = True
    check_months: bool = True
    check_title_period: bool = True
    check_key_style: bool = True
    year_range: tuple = (1900, _dt.date.today().year + 2)


def _add(report: ValidationReport, severity: str, entry: Entry, message: str,
         field_name: Optional[str] = None) -> None:
    report.issues.append(
        Issue(severity, entry.key, message, field_name, entry.line)
    )


def _check_duplicates(doc: Document, report: ValidationReport) -> None:
    for key, count in doc.duplicate_keys().items():
        report.issues.append(
            Issue(ERROR, key, f"duplicate cite key appears {count} times")
        )


def _check_parsing(doc: Document, report: ValidationReport) -> None:
    for warning in doc.warnings:
        report.issues.append(Issue(ERROR, "-", warning))


def _check_required(entry: Entry, opts: ValidateOptions, report: ValidationReport) -> None:
    if not opts.required_fields:
        return
    required = REQUIRED_FIELDS.get(entry.type)
    if required is None:
        if not entry.fields:
            _add(report, WARNING, entry, f"@{entry.type} entry has no fields")
        return
    for name in required:
        if not entry.has(name):
            _add(report, ERROR, entry, f"missing required field '{name}'", name)
        elif not (entry.get(name) or "").strip():
            _add(report, ERROR, entry, f"required field '{name}' is empty", name)
    if entry.type == "book" and not (entry.has("author") or entry.has("editor")):
        _add(report, ERROR, entry, "book has neither 'author' nor 'editor'")


def _check_values(entry: Entry, opts: ValidateOptions, report: ValidationReport) -> None:
    for name, value in entry.items():
        if not value.strip():
            _add(report, WARNING, entry, f"field '{name}' is empty", name)
        if value.count("{") != value.count("}"):
            _add(report, ERROR, entry,
                 f"unbalanced braces in field '{name}'", name)

    year = entry.get("year")
    if year:
        stripped = year.strip()
        if not _YEAR_RE.match(stripped):
            _add(report, WARNING, entry, f"year '{stripped}' is not a 4-digit year", "year")
        else:
            lo, hi = opts.year_range
            if not (lo <= int(stripped) <= hi):
                _add(report, WARNING, entry,
                     f"year {stripped} outside plausible range {lo}–{hi}", "year")

    pages = entry.get("pages")
    if pages and not (_PAGES_RE.match(pages) or _SINGLE_PAGE_RE.match(pages)):
        _add(report, WARNING, entry,
             f"pages '{pages}' is not in 'start--end' form", "pages")

    author = entry.get("author") or ""
    if re.search(r"\s+and\s+others\s*$", author, re.I) or author.strip().lower() in ("others",):
        _add(report, INFO, entry,
             "author list ends with 'and others' (incomplete by design?)", "author")

    month = entry.get("month")
    if opts.check_months and month:
        m = month.strip().strip("{}").strip().lower()
        if m in _FULL_MONTHS or re.fullmatch(r"\d{1,2}", m):
            _add(report, INFO, entry,
                 f"month '{month.strip()}' — consider 'jan'…'dec' macro form", "month")

    if opts.check_title_period:
        title = (entry.get("title") or "").strip()
        if title.endswith("."):
            _add(report, INFO, entry, "title ends with a period", "title")

    url = entry.get("url")
    if url and not re.match(r"\s*(https?://|\\url\{)", url):
        _add(report, WARNING, entry, f"url '{url}' has no http(s) scheme", "url")


def _check_venues(entry: Entry, opts: ValidateOptions, report: ValidationReport) -> None:
    if not opts.check_venues or is_arxiv_like(entry):
        return
    for name in ("booktitle", "journal"):
        value = entry.get(name)
        if not value:
            continue
        stripped = value.strip().strip("{}").strip()
        known = match_venue(stripped, expected_kind=JOURNAL if name == "journal" else None)
        # TBD is both a placeholder and IEEE Transactions on Big Data. The
        # journal field provides enough context to recognize the publication.
        if stripped.lower() in _PLACEHOLDER_VENUES and not (name == "journal" and known):
            _add(report, WARNING, entry,
                 f"{name} is a placeholder ('{stripped}') — fill in the real venue", name)
        elif known is None and match_venue(stripped) is None:
            _add(report, INFO, entry,
                 f"{name} '{stripped}' is not in the venue presets", name)


def _check_entry_shape(entry: Entry, opts: ValidateOptions, report: ValidationReport) -> None:
    if entry.type == "article" and entry.has("booktitle") and not entry.has("journal"):
        _add(report, WARNING, entry,
             "article with a booktitle — did you mean @inproceedings?", "booktitle")
    if entry.type in ("inproceedings", "conference") and entry.has("journal") and not entry.has("booktitle"):
        _add(report, WARNING, entry,
             f"@{entry.type} with a journal — did you mean @article?", "journal")

    if is_arxiv_like(entry):
        arxiv_id = extract_arxiv_id(entry)
        if arxiv_id and not entry.get("eprint"):
            _add(report, INFO, entry,
                 f"arXiv id {arxiv_id} found but no 'eprint' field", "eprint")
        if entry.get("eprint") and not entry.get("archiveprefix"):
            _add(report, INFO, entry,
                 "eprint present without 'archiveprefix = {arXiv}'", "archiveprefix")
        volume = entry.get("volume") or ""
        if re.match(r"^\s*abs/", volume, re.I) or volume.strip() == (arxiv_id or ""):
            _add(report, INFO, entry,
                 f"pseudo-volume '{volume.strip()}' — preprints have no volume", "volume")

    if entry.type == "misc" and not any(
        entry.has(name) for name in ("howpublished", "url", "note", "eprint", "doi")
    ):
        _add(report, WARNING, entry, "@misc entry has no url/howpublished/note/doi")

    if opts.check_key_style:
        if re.search(r"\s", entry.key):
            _add(report, WARNING, entry, f"cite key '{entry.key}' contains whitespace")
        elif entry.key != entry.key.lower():
            _add(report, INFO, entry, f"cite key '{entry.key}' is not lowercase")


def validate_document(doc: Document, opts: Optional[ValidateOptions] = None) -> ValidationReport:
    opts = opts or ValidateOptions()
    report = ValidationReport()
    _check_duplicates(doc, report)
    _check_parsing(doc, report)
    for entry in doc.entries:
        _check_required(entry, opts, report)
        _check_values(entry, opts, report)
        _check_venues(entry, opts, report)
        _check_entry_shape(entry, opts, report)
    return report


def validate_text(text: str, opts: Optional[ValidateOptions] = None) -> ValidationReport:
    from .parser import parse_bibtex

    return validate_document(parse_bibtex(text), opts)


def summary(report: ValidationReport) -> str:
    return (
        f"{report.count(ERROR)} error(s), "
        f"{report.count(WARNING)} warning(s), "
        f"{report.count(INFO)} suggestion(s)"
    )


def has_errors(report: ValidationReport) -> bool:
    return bool(report.errors)
