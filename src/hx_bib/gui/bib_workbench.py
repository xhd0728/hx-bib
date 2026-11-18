"""Apply selected BibTeX operations and compare the resulting entry text."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import zip_longest

from ..core import cleaner
from ..core.diff import DiffRow, entry_identity, match_by_key
from ..core.model import Document, Entry
from ..core.normalize import (
    NormalizeOptions,
    entry_venue_presets,
    normalize_document,
    normalize_entry,
)
from ..core.parser import parse_bibtex
from ..core.validator import ValidateOptions, ValidationReport, validate_document
from ..core.writer import FormatOptions, render_document, render_entry


def clean_document(doc: Document, options: dict, drafts: dict) -> None:
    names = options["remove"] + re.split(r"[,\s]+", drafts.get("clean_custom_remove", "").strip())
    cleaner.remove_fields(doc, names)
    key = drafts.get("clean_add_key", "").strip()
    value = drafts.get("clean_add_value", "")
    if bool(key) != bool(value):
        raise ValueError("incomplete_field")
    if key:
        if not re.fullmatch(r"[\w-]+", key):
            raise ValueError("bib_invalid_field")
        cleaner.add_fields(doc, {key: value}, overwrite=drafts.get("clean_overwrite", False))
    for name, transform in (
        ("drop_empty", cleaner.drop_empty_fields), ("drop_others", cleaner.drop_others),
        ("months", cleaner.normalize_months), ("title_period", cleaner.strip_title_periods),
        ("doi", cleaner.normalize_dois), ("pages", cleaner.unify_page_ranges),
        ("clean_urls", cleaner.strip_url_tracking),
    ):
        if options[name]:
            transform(doc)
    keep = "last" if options["keep_last"] else "first"
    if options["dedupe"]:
        cleaner.dedupe_entries(doc, keep=keep)
    if options["dedupe_title"]:
        cleaner.dedupe_by_title(doc, keep=keep)


def _reuse_unmodified_text(source: str, original: list[Entry], updated: list[Entry]) -> str:
    retained = {entry.source_span: entry for entry in updated}
    pieces = []
    cursor = 0
    newline = "\r\n" if "\r\n" in source else "\n"
    for old in original:
        start, stop = old.source_span
        pieces.append(source[cursor:start])
        new = retained.get(old.source_span)
        if new == old:
            pieces.append(source[start:stop])
        elif new is not None:
            indent = re.search(r"(?m)^([ \t]*)\w[\w-]*\s*=", source[start:stop])
            whitespace = indent[1] if indent else "    "
            opts = FormatOptions(indent="tab" if "\t" in whitespace else str(len(whitespace)), field_order="keep")
            pieces.append(render_entry(new, opts).replace("\n", newline))
        cursor = stop
    pieces.append(source[cursor:])
    return "".join(pieces)


def apply_options(source: str, settings: dict, drafts: dict) -> tuple[str, ValidationReport | None]:
    """Apply cleanup, normalization and venue style, then format and validate."""
    enabled = settings["enabled"]
    edit = any(enabled[key] for key in ("clean", "normalize", "venues", "format"))
    if not edit and not enabled["check"]:
        return source, None
    document = parse_bibtex(source)
    if edit and document.warnings:
        raise ValueError("bib_parse_first")
    original = parse_bibtex(source).entries if edit and not enabled["format"] else []
    if enabled["clean"]:
        clean_document(document, settings["clean"], drafts)
    if enabled["normalize"]:
        cfg = settings["normalize"]
        normalize_document(document, NormalizeOptions(
            venue_style="keep" if enabled["venues"] else cfg["venue_style"], arxiv_style=cfg["arxiv_style"],
            fix_entry_types=cfg["fix_types"], drop_arxiv_volume=cfg["drop_arxiv_volume"],
            add_arxiv_url=cfg["add_arxiv_url"],
        ))
    if enabled["venues"]:
        options = NormalizeOptions(venue_style=settings["venues"]["preset_style"], arxiv_style="keep")
        for entry in document.entries:
            if entry_venue_presets(entry):
                normalize_entry(entry, options)
    if enabled["format"]:
        cfg = dict(settings["format"])
        cfg["sort_entries"] = cfg.pop("sort")
        result = render_document(document, FormatOptions(**cfg))
    elif edit:
        result = _reuse_unmodified_text(source, original, document.entries)
    else:
        result = source
    updated = parse_bibtex(result) if result != source else document
    if edit and (updated.warnings or Counter((e.key, entry_identity(e)) for e in updated.entries)
                 != Counter((e.key, entry_identity(e)) for e in document.entries)):
        raise ValueError("bib_result_invalid")
    report = validate_document(updated, ValidateOptions(**settings["check"])) if enabled["check"] else None
    return result, report


@dataclass
class BibComparison:
    before: Entry | None
    after: Entry | None
    left: str | None
    right: str | None
    kind: str
    moved: bool = False


def compare_entries(before: str, after: str) -> list[BibComparison]:
    """Match occurrences using parser offsets, including multiple entries on one line."""
    old, new = parse_bibtex(before), parse_bibtex(after)
    pairs = match_by_key(old.entries, new.entries, key=lambda e: e.key, identity=entry_identity)
    counterparts = [right.source_span for left, right in pairs if left is not None and right is not None]
    reordered = {position for position, ordered in zip(counterparts, sorted(counterparts)) if position != ordered}

    def excerpt(text: str, entry: Entry | None) -> str | None:
        if entry is None:
            return None
        start, stop = entry.source_span
        return text[start:stop]

    comparisons = []
    for left, right in pairs:
        old_text, new_text = excerpt(before, left), excerpt(after, right)
        moved = right is not None and right.source_span in reordered
        if left is None:
            kind = "added"
        elif right is None:
            kind = "removed"
        else:
            kind = "modified" if old_text != new_text or moved else "unchanged"
        comparisons.append(BibComparison(left, right, old_text, new_text, kind, moved))

    def loose_text(text: str, doc: Document) -> str:
        chunks = []
        cursor = 0
        for entry in doc.entries:
            start, stop = entry.source_span
            chunks.append(text[cursor:start].strip())
            cursor = stop
        chunks.append(text[cursor:].strip())
        return "\n".join(chunk for chunk in chunks if chunk)

    left_free, right_free = loose_text(before, old), loose_text(after, new)
    if left_free != right_free or (before != after and all(c.kind == "unchanged" for c in comparisons)):
        comparisons.insert(0, BibComparison(None, None, left_free, right_free, "modified"))
    return comparisons


def comparison_rows(comparison: BibComparison, full: bool) -> list[DiffRow]:
    left, right = (comparison.left or "").splitlines(), (comparison.right or "").splitlines()
    old_offset = comparison.before.line - 1 if comparison.before is not None else 0
    new_offset = comparison.after.line - 1 if comparison.after is not None else 0
    rows = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        for i, j in zip_longest(range(i1, i2), range(j1, j2)):
            kind = "insert" if i is None else "delete" if j is None else tag
            rows.append(DiffRow(kind, None if i is None else old_offset + i + 1, None if i is None else left[i],
                                None if j is None else new_offset + j + 1, None if j is None else right[j]))
    if full or not any(row.kind != "equal" for row in rows):
        return rows
    selected = {neighbor for index, row in enumerate(rows) if row.kind != "equal"
                for neighbor in range(max(0, index - 1), min(len(rows), index + 2))}
    visible = []
    previous = -1
    for index in sorted(selected):
        if index > previous + 1:
            visible.append(DiffRow("equal", None, "…", None, "…"))
        visible.append(rows[index])
        previous = index
    if previous < len(rows) - 1:
        visible.append(DiffRow("equal", None, "…", None, "…"))
    return visible
