"""Venue & arXiv normalization (inspired by rebiber's abbreviation tables).

``normalize_document`` rewrites each entry so that venues use one chosen
style — canonical abbreviation ("Proceedings of EMNLP"), full name, or the
venue *family* the user groups by ("ARR", "ACM") — and so that arXiv
preprints use a consistent shape.

Everything is deterministic and offline; nothing is guessed beyond the
built-in :mod:`hx_bib.core.venues` presets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .identifiers import extract_arxiv_id, is_arxiv_like
from .model import Document, Entry
from .venues import CONFERENCE, JOURNAL, match_venue, render_venue

CONFERENCE_TYPES = {"inproceedings", "incollection", "conference"}

_VENUE_FIELDS = ("booktitle", "journal")
_ABS_VOLUME_RE = re.compile(r"^\s*abs/\d{4}\.\d{4,5}\s*$", re.I)


@dataclass
class NormalizeOptions:
    venue_style: str = "abbr"     # abbr | full | family | keep
    arxiv_style: str = "journal"  # journal | misc | keep
    fix_entry_types: bool = True
    drop_arxiv_volume: bool = True
    add_arxiv_url: bool = True

    def validate(self) -> "NormalizeOptions":
        if self.venue_style not in ("abbr", "full", "family", "keep"):
            raise ValueError(f"invalid venue_style {self.venue_style!r}")
        if self.arxiv_style not in ("journal", "misc", "keep"):
            raise ValueError(f"invalid arxiv_style {self.arxiv_style!r}")
        return self


# ---------------------------------------------------------------------------
# arXiv preset
# ---------------------------------------------------------------------------
def normalize_arxiv(entry: Entry, opts: NormalizeOptions) -> List[str]:
    notes: List[str] = []
    if opts.arxiv_style == "keep" or not is_arxiv_like(entry):
        return notes
    arxiv_id = extract_arxiv_id(entry)
    if not arxiv_id:
        return notes

    if opts.drop_arxiv_volume:
        volume = entry.get("volume") or ""
        if _ABS_VOLUME_RE.match(volume) or volume.strip() == arxiv_id:
            entry.remove("volume")
            notes.append(f"{entry.key}: dropped pseudo-volume '{volume.strip()}'")

    if opts.arxiv_style == "misc":
        if entry.type != "misc":
            notes.append(f"{entry.key}: entry type {entry.type} -> misc (arXiv preset)")
            entry.type = "misc"
        for name in _VENUE_FIELDS:
            if entry.remove(name):
                notes.append(f"{entry.key}: removed field '{name}' (arXiv preset)")
        if not entry.get("howpublished"):
            entry.set("howpublished", f"arXiv:{arxiv_id}")
        entry.set("eprint", arxiv_id)
        entry.set("archiveprefix", "arXiv")
    else:  # journal style
        journal = f"arXiv preprint arXiv:{arxiv_id}"
        if entry.type != "article":
            notes.append(f"{entry.key}: entry type {entry.type} -> article (arXiv preset)")
            entry.type = "article"
        if entry.remove("booktitle"):
            notes.append(f"{entry.key}: removed field 'booktitle' (arXiv preset)")
        if entry.get("journal") != journal:
            entry.set("journal", journal)
            notes.append(f"{entry.key}: journal -> '{journal}'")
        entry.set("eprint", arxiv_id)
        entry.set("archiveprefix", "arXiv")

    if opts.add_arxiv_url and not entry.get("url"):
        entry.set("url", f"https://arxiv.org/abs/{arxiv_id}")
        notes.append(f"{entry.key}: added arXiv url")
    return notes


# ---------------------------------------------------------------------------
# Venue normalization
# ---------------------------------------------------------------------------
def _rewrite_venue_field(entry: Entry, field_name: str, opts: NormalizeOptions) -> List[str]:
    notes: List[str] = []
    value = entry.get(field_name)
    if not value or opts.venue_style == "keep":
        return notes
    matched = match_venue(value)
    if not matched:
        return notes
    venue, findings = matched

    if field_name == "booktitle" and venue.kind == JOURNAL:
        # A journal listed as booktitle: move it to the journal field.
        if opts.fix_entry_types and entry.type in CONFERENCE_TYPES:
            old_type = entry.type
            entry.type = "article"
            notes.append(f"{entry.key}: entry type {old_type} -> article ('{venue.abbr}')")
        entry.remove("booktitle")
        rendered = render_venue(venue, findings, opts.venue_style)
        entry.set("journal", rendered)
        notes.append(f"{entry.key}: journal -> '{rendered}'")
        return notes

    if field_name == "journal" and venue.kind == CONFERENCE:
        if opts.fix_entry_types and entry.type == "article":
            entry.type = "inproceedings"
            notes.append(f"{entry.key}: entry type article -> inproceedings ('{venue.abbr}')")
        entry.remove("journal")
        rendered = render_venue(venue, findings, opts.venue_style)
        entry.set("booktitle", rendered)
        notes.append(f"{entry.key}: booktitle -> '{rendered}'")
        return notes

    rendered = render_venue(venue, findings, opts.venue_style)
    if rendered != value.strip():
        entry.set(field_name, rendered)
        notes.append(f"{entry.key}: {field_name} '{value.strip()}' -> '{rendered}'")
    return notes


def _fix_entry_type(entry: Entry) -> List[str]:
    notes: List[str] = []
    if entry.type == "article" and entry.has("booktitle") and not entry.has("journal"):
        entry.type = "inproceedings"
        notes.append(f"{entry.key}: entry type article -> inproceedings")
    elif entry.type in CONFERENCE_TYPES and entry.has("journal") and not entry.has("booktitle"):
        entry.type = "article"
        notes.append(f"{entry.key}: entry type {entry.type} -> article")
    return notes


def normalize_entry(entry: Entry, opts: NormalizeOptions) -> List[str]:
    notes = normalize_arxiv(entry, opts)
    if is_arxiv_like(entry) and extract_arxiv_id(entry):
        # An arXiv preprint has no meaningful venue to normalize.
        return notes
    for field_name in _VENUE_FIELDS:
        notes.extend(_rewrite_venue_field(entry, field_name, opts))
    if opts.fix_entry_types:
        notes.extend(_fix_entry_type(entry))
    return notes


def normalize_document(doc: Document, opts: Optional[NormalizeOptions] = None) -> List[str]:
    opts = (opts or NormalizeOptions()).validate()
    notes: List[str] = []
    for entry in doc.entries:
        notes.extend(normalize_entry(entry, opts))
    return notes


# ---------------------------------------------------------------------------
# Validation helper (used by the validator module)
# ---------------------------------------------------------------------------
def unmatched_venues(doc: Document) -> List[Entry]:
    """Entries whose venue text matches none of the presets."""
    out: List[Entry] = []
    for entry in doc.entries:
        if is_arxiv_like(entry) and extract_arxiv_id(entry):
            continue
        for field_name in _VENUE_FIELDS:
            value = entry.get(field_name)
            if value and match_venue(value) is None:
                out.append(entry)
                break
    return out
