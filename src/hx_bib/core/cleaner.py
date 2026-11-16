"""Field / entry clean-up operations.

Every function mutates the document in place and returns a list of
human-readable change notes (``["key: removed field 'abstract'", …]``),
so callers (CLI, GUI, diff view) can report exactly what happened.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

from .model import Document, Entry

COMMON_FIELDS = [
    "abstract",
    "keywords",
    "url",
    "doi",
    "issn",
    "isbn",
    "month",
    "note",
    "address",
    "editor",
    "publisher",
    "organization",
    "series",
    "volume",
    "number",
    "eprint",
    "archiveprefix",
    "primaryclass",
]

_MONTHS = {
    "1": "jan", "01": "jan", "jan": "jan", "january": "jan", "jan.": "jan",
    "2": "feb", "02": "feb", "feb": "feb", "february": "feb", "feb.": "feb",
    "3": "mar", "03": "mar", "mar": "mar", "march": "mar", "mar.": "mar",
    "4": "apr", "04": "apr", "apr": "apr", "april": "apr", "apr.": "apr",
    "5": "may", "05": "may", "may": "may",
    "6": "jun", "06": "jun", "jun": "jun", "june": "jun", "jun.": "jun",
    "7": "jul", "07": "jul", "jul": "jul", "july": "jul", "jul.": "jul",
    "8": "aug", "08": "aug", "aug": "aug", "august": "aug", "aug.": "aug",
    "9": "sep", "09": "sep", "sep": "sep", "sept": "sep", "sept.": "sep",
    "september": "sep", "sep.": "sep",
    "10": "oct", "oct": "oct", "october": "oct", "oct.": "oct",
    "11": "nov", "nov": "nov", "november": "nov", "nov.": "nov",
    "12": "dec", "dec": "dec", "december": "dec", "dec.": "dec",
}

_OTHERS_RE = re.compile(r"(?:\s+and)?\s+others\s*$", re.IGNORECASE)


def remove_fields(doc: Document, names: Iterable[str]) -> List[str]:
    names = {n.strip().lower() for n in names if n.strip()}
    notes: List[str] = []
    for entry in doc.entries:
        for name in names:
            if entry.remove(name):
                notes.append(f"{entry.key}: removed field '{name}'")
    return notes


def keep_fields(doc: Document, names: Iterable[str]) -> List[str]:
    """Drop every field NOT in ``names`` (allowlist)."""
    keep = {n.strip().lower() for n in names if n.strip()}
    notes: List[str] = []
    for entry in doc.entries:
        for name in list(entry.fields):
            if name not in keep:
                entry.remove(name)
                notes.append(f"{entry.key}: removed field '{name}'")
    return notes


def add_fields(
    doc: Document,
    fields: Dict[str, str],
    overwrite: bool = False,
    only_types: Optional[Sequence[str]] = None,
) -> List[str]:
    notes: List[str] = []
    types = {t.lower() for t in only_types} if only_types else None
    for entry in doc.entries:
        if types is not None and entry.type not in types:
            continue
        for name, value in fields.items():
            if entry.has(name) and not overwrite:
                continue
            entry.set(name, value)
            notes.append(f"{entry.key}: {'set' if overwrite else 'added'} field '{name}'")
    return notes


def rename_fields(doc: Document, mapping: Dict[str, str]) -> List[str]:
    notes: List[str] = []
    for entry in doc.entries:
        for old, new in mapping.items():
            if entry.rename(old, new):
                notes.append(f"{entry.key}: renamed '{old}' -> '{new}'")
    return notes


def drop_empty_fields(doc: Document) -> List[str]:
    notes: List[str] = []
    for entry in doc.entries:
        for name in list(entry.fields):
            if not entry.fields[name].strip():
                entry.remove(name)
                notes.append(f"{entry.key}: removed empty field '{name}'")
    return notes


def drop_others(doc: Document) -> List[str]:
    """Remove a trailing ``and others`` from author/editor lists."""
    notes: List[str] = []
    for entry in doc.entries:
        for name in ("author", "editor"):
            value = entry.get(name)
            if not value:
                continue
            new = _OTHERS_RE.sub("", value.strip()).strip().rstrip(",")
            if new != value.strip():
                entry.set(name, new)
                notes.append(f"{entry.key}: dropped trailing 'others' from '{name}'")
    return notes


def normalize_months(doc: Document) -> List[str]:
    notes: List[str] = []
    for entry in doc.entries:
        value = entry.get("month")
        if value is None:
            continue
        key = value.strip().strip("{}").strip().lower()
        month = _MONTHS.get(key)
        if month and (entry.fields["month"] != month or "month" not in entry.bare_fields):
            entry.set("month", month)
            entry.bare_fields.add("month")  # BibTeX month macros are bare
            if value.strip().strip("{}") != month:
                notes.append(f"{entry.key}: month '{value.strip()}' -> '{month}'")
        elif not month and value.strip():
            notes.append(f"{entry.key}: unrecognized month '{value.strip()}' (left as is)")
    return notes


def strip_title_periods(doc: Document) -> List[str]:
    """Remove a dangling '.' at the end of titles (keeps '?' and '!' intact)."""
    notes: List[str] = []
    for entry in doc.entries:
        title = entry.get("title")
        if not title:
            continue
        new = re.sub(r"\.\s*$", "", title.strip())
        if new != title.strip() and new:
            entry.set("title", new)
            notes.append(f"{entry.key}: removed trailing period in title")
    return notes


def dedupe_entries(doc: Document, keep: str = "first") -> List[str]:
    """Drop entries with a repeated cite key (keep first or last occurrence)."""
    notes: List[str] = []
    if keep not in ("first", "last"):
        raise ValueError("keep must be 'first' or 'last'")
    seen: Dict[str, Entry] = {}
    order: List[Entry] = []
    for entry in doc.entries:
        if entry.key in seen:
            if keep == "last":
                prev = seen[entry.key]
                order.remove(prev)
                order.append(entry)
                seen[entry.key] = entry
            notes.append(
                f"{entry.key}: removed duplicate entry (kept {keep}, line {entry.line})"
            )
        else:
            seen[entry.key] = entry
            order.append(entry)
    doc.entries = order
    return notes


# ---------------------------------------------------------------------------
# Advanced clean-ups
# ---------------------------------------------------------------------------
_DOI_PREFIX_RE = re.compile(r"^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:?\s*)", re.I)
_PAGE_RANGE_RE = re.compile(r"(?<=\d)\s*[-–—]\s*(?=\d)")
_TRACKING_PARAMS = ("fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref_src", "ref_url")


def normalize_dois(doc: Document) -> List[str]:
    """Strip ``https://doi.org/`` / ``doi:`` prefixes from DOI values."""
    notes: List[str] = []
    for entry in doc.entries:
        value = entry.get("doi")
        if not value:
            continue
        new = _DOI_PREFIX_RE.sub("", value).strip()
        if new != value.strip() and new:
            entry.set("doi", new)
            notes.append(f"{entry.key}: doi '{value.strip()}' -> '{new}'")
    return notes


def unify_page_ranges(doc: Document) -> List[str]:
    """Normalize page ranges to BibTeX's ``start--end`` form."""
    notes: List[str] = []
    for entry in doc.entries:
        value = entry.get("pages")
        if not value:
            continue
        new = _PAGE_RANGE_RE.sub("--", value)
        if new != value:
            entry.set("pages", new)
            notes.append(f"{entry.key}: pages '{value}' -> '{new}'")
    return notes


def strip_url_tracking(doc: Document) -> List[str]:
    """Remove utm_* / fbclid / gclid style tracking parameters from URLs."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    notes: List[str] = []
    for entry in doc.entries:
        value = entry.get("url")
        if not value or "?" not in value:
            continue
        try:
            parts = urlsplit(value.strip())
        except ValueError:
            continue
        params = parse_qsl(parts.query, keep_blank_values=True)
        kept = [
            (k, v) for k, v in params
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        ]
        if len(kept) == len(params):
            continue
        new = urlunsplit(parts._replace(query=urlencode(kept)))
        entry.set("url", new)
        notes.append(f"{entry.key}: cleaned tracking parameters from url")
    return notes


def _title_key(entry: Entry) -> str:
    title = (entry.get("title") or "").lower()
    return re.sub(r"[^a-z0-9]+", "", title)


def dedupe_by_title(doc: Document, keep: str = "first") -> List[str]:
    """Drop entries whose normalized title repeats an earlier/later entry."""
    if keep not in ("first", "last"):
        raise ValueError("keep must be 'first' or 'last'")
    notes: List[str] = []
    seen: Dict[str, Entry] = {}
    order: List[Entry] = []
    for entry in doc.entries:
        key = _title_key(entry)
        if key and key in seen:
            if keep == "last":
                prev = seen[key]
                order.remove(prev)
                order.append(entry)
                seen[key] = entry
                notes.append(
                    f"{entry.key}: removed duplicate title entry "
                    f"(kept later '{entry.key}')"
                )
            else:
                notes.append(
                    f"{entry.key}: removed duplicate title entry "
                    f"(same title as '{seen[key].key}')"
                )
        else:
            if key:
                seen[key] = entry
            order.append(entry)
    doc.entries = order
    return notes
