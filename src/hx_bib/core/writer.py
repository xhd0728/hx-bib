"""Render :class:`~hx_bib.core.model.Document` back to BibTeX text.

Formatting knobs live in :class:`FormatOptions`:

* ``indent``          — ``"2"``/``"4"``/… spaces, ``"tab"``, ``"align"``
  (align fields under the opening brace of the entry) or ``"0"``.
* ``align_equals``    — pad field names so the ``=`` signs line up.
* ``field_order``     — ``"standard"`` (curated order), ``"alphabetical"``,
  ``"keep"`` (as parsed).
* ``next_line_brace`` — put the body of each entry on its own lines
  (always on for readability; kept as an option for future use).
* ``sort_entries``    — ``"none"``, ``"key"``, ``"year"``, ``"type"``,
  ``"title"``.
* ``drop_comments``   — discard free-text comments / @comment blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .model import Document, Entry

# Curated field order used by ``field_order="standard"``. Fields not listed
# here keep their relative order *after* the known ones.
STANDARD_FIELD_ORDER: List[str] = [
    "author",
    "title",
    "booktitle",
    "journal",
    "year",
    "volume",
    "number",
    "pages",
    "month",
    "editor",
    "series",
    "publisher",
    "address",
    "organization",
    "school",
    "institution",
    "edition",
    "chapter",
    "howpublished",
    "type",
    "note",
    "doi",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "url",
    "issn",
    "isbn",
    "language",
    "abstract",
    "keywords",
]

_WS_RE = re.compile(r"\s+")


@dataclass
class FormatOptions:
    indent: str = "4"
    align_equals: bool = False
    field_order: str = "standard"
    sort_entries: str = "none"
    trailing_comma: bool = False
    drop_comments: bool = False
    blank_lines_between_entries: int = 1

    def validate(self) -> "FormatOptions":
        if self.indent not in ("align", "0", "tab") and not self.indent.isdigit():
            raise ValueError(
                f"invalid indent {self.indent!r}: use a number of spaces, 'tab' or 'align'"
            )
        if self.field_order not in ("standard", "alphabetical", "keep"):
            raise ValueError(f"invalid field_order {self.field_order!r}")
        if self.sort_entries not in ("none", "key", "year", "type", "title"):
            raise ValueError(f"invalid sort_entries {self.sort_entries!r}")
        return self


# ---------------------------------------------------------------------------
# Value / field rendering
# ---------------------------------------------------------------------------
def clean_value(value: str) -> str:
    """Collapse newlines and repeated whitespace inside a field value."""
    return _WS_RE.sub(" ", value).strip()


def _indent_string(opts: FormatOptions, entry: Entry) -> str:
    if opts.indent == "tab":
        return "\t"
    if opts.indent == "align":
        # Align fields under the "{" that opens the entry body.
        return " " * (len(entry.type) + 2)
    return " " * int(opts.indent)


def ordered_fields(entry: Entry, opts: FormatOptions) -> List[str]:
    names = list(entry.fields.keys())
    if opts.field_order == "keep":
        return names
    if opts.field_order == "alphabetical":
        return sorted(names)
    rank = {name: i for i, name in enumerate(STANDARD_FIELD_ORDER)}
    fallback = len(rank)
    return sorted(names, key=lambda n: (rank.get(n, fallback), names.index(n)))


def render_entry(entry: Entry, opts: FormatOptions) -> str:
    indent = _indent_string(opts, entry)
    names = ordered_fields(entry, opts)
    if opts.align_equals and names:
        width = max(len(n) for n in names)
    else:
        width = 0

    lines = [f"@{entry.type}{{{entry.key},"]
    for idx, name in enumerate(names):
        value = entry.fields[name]
        rendered = value if name in entry.bare_fields else "{" + clean_value(value) + "}"
        pad = " " * (width - len(name)) if width else ""
        comma = "," if (opts.trailing_comma or idx < len(names) - 1) else ""
        lines.append(f"{indent}{name}{pad} = {rendered}{comma}")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Document rendering
# ---------------------------------------------------------------------------
def _sorted_entries(entries: List[Entry], mode: str) -> List[Entry]:
    if mode == "none":
        return list(entries)
    if mode == "key":
        return sorted(entries, key=lambda e: e.key.lower())
    if mode == "type":
        return sorted(entries, key=lambda e: (e.type, e.key.lower()))
    if mode == "title":
        return sorted(entries, key=lambda e: (e.get("title") or "").lower())
    if mode == "year":
        def year_key(e: Entry) -> tuple:
            year = re.sub(r"\D", "", e.get("year") or "")
            return (year or "9999", e.key.lower())

        return sorted(entries, key=year_key)
    return list(entries)


def render_document(doc: Document, opts: FormatOptions) -> str:
    opts.validate()
    blocks: List[str] = []

    for raw in doc.preamble:
        blocks.append(raw)
    for raw in doc.strings:
        blocks.append(raw)

    if not opts.drop_comments:
        for raw in doc.comments:
            blocks.append(raw.strip())

    for entry in _sorted_entries(doc.entries, opts.sort_entries):
        blocks.append(render_entry(entry, opts))

    sep = "\n" * (opts.blank_lines_between_entries + 1)
    return sep.join(blocks).rstrip() + "\n"


def format_text(text: str, opts: Optional[FormatOptions] = None) -> str:
    """Convenience: parse + render in one call."""
    from .parser import parse_bibtex

    doc = parse_bibtex(text)
    return render_document(doc, opts or FormatOptions())
