"""Before/after comparison helpers (GitHub-style diffs, itemized changes).

Two levels of granularity are provided:

* :func:`diff_documents` — *itemized* comparison: per entry, which fields
  were added / removed / modified, so the GUI and CLI can show a clean
  "what changed" checklist.
* :func:`diff_lines`     — line-level side-by-side rows (equal / insert /
  delete / replace) that render like a GitHub diff.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .model import Document, Entry
from .parser import parse_bibtex
from .writer import clean_value

CHANGE_ICONS = {"added": "+", "removed": "-", "modified": "~", "unchanged": " "}


# ---------------------------------------------------------------------------
# Itemized (field-level) diff
# ---------------------------------------------------------------------------
@dataclass
class FieldChange:
    field_name: str
    kind: str  # added | removed | modified
    old: Optional[str] = None
    new: Optional[str] = None


@dataclass
class EntryDiff:
    key: str
    kind: str  # added | removed | modified | unchanged
    before: Optional[Entry] = None
    after: Optional[Entry] = None
    type_changed: bool = False
    field_changes: List[FieldChange] = field(default_factory=list)


@dataclass
class DocDiff:
    entries: List[EntryDiff] = field(default_factory=list)

    @property
    def changed(self) -> List[EntryDiff]:
        return [d for d in self.entries if d.kind != "unchanged"]

    def count(self, kind: str) -> int:
        return sum(1 for d in self.entries if d.kind == kind)


def _diff_entries(before: Optional[Entry], after: Optional[Entry]) -> EntryDiff:
    if before is None and after is not None:
        return EntryDiff(key=after.key, kind="added", after=after)
    if after is None and before is not None:
        return EntryDiff(key=before.key, kind="removed", before=before)
    assert before is not None and after is not None

    changes: List[FieldChange] = []
    b_fields, a_fields = before.fields, after.fields
    for name in a_fields:
        if name not in b_fields:
            changes.append(FieldChange(name, "added", new=a_fields[name]))
    for name in b_fields:
        if name not in a_fields:
            changes.append(FieldChange(name, "removed", old=b_fields[name]))
        elif clean_value(b_fields[name]) != clean_value(a_fields[name]):
            changes.append(
                FieldChange(name, "modified", old=b_fields[name], new=a_fields[name])
            )
    type_changed = before.type != after.type
    kind = "modified" if (changes or type_changed) else "unchanged"
    return EntryDiff(
        key=after.key,
        kind=kind,
        before=before,
        after=after,
        type_changed=type_changed,
        field_changes=changes,
    )


def diff_documents(before: Document, after: Document) -> DocDiff:
    """Compare two parsed documents entry-by-entry (matched on cite key)."""
    result = DocDiff()
    after_by_key = {}
    for e in after.entries:
        after_by_key.setdefault(e.key, e)
    seen = set()
    for b in before.entries:
        a = after_by_key.get(b.key)
        if a is not None:
            seen.add(b.key)
        result.entries.append(_diff_entries(b, a))
    for a in after.entries:
        if a.key not in seen:
            result.entries.append(_diff_entries(None, a))
    return result


def diff_texts(before_text: str, after_text: str) -> DocDiff:
    return diff_documents(parse_bibtex(before_text), parse_bibtex(after_text))


# ---------------------------------------------------------------------------
# Line-level side-by-side rows
# ---------------------------------------------------------------------------
@dataclass
class DiffRow:
    kind: str  # equal | replace | delete | insert
    left_no: Optional[int]
    left: Optional[str]
    right_no: Optional[int]
    right: Optional[str]


def diff_lines(
    before_text: str,
    after_text: str,
    left_offset: int = 0,
    right_offset: int = 0,
) -> List[DiffRow]:
    """Side-by-side rows. ``*_offset`` shifts the displayed line numbers (so a
    slice of a file can diff with its original absolute line numbers)."""
    left = before_text.splitlines()
    right = after_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    rows: List[DiffRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(DiffRow(
                    "equal",
                    left_offset + i1 + k + 1, left[i1 + k],
                    right_offset + j1 + k + 1, right[j1 + k],
                ))
            continue
        m, n = i2 - i1, j2 - j1
        paired = min(m, n)
        for k in range(paired):
            rows.append(DiffRow(
                "replace",
                left_offset + i1 + k + 1, left[i1 + k],
                right_offset + j1 + k + 1, right[j1 + k],
            ))
        for k in range(paired, m):
            rows.append(DiffRow("delete", left_offset + i1 + k + 1, left[i1 + k], None, None))
        for k in range(paired, n):
            rows.append(DiffRow("insert", None, None, right_offset + j1 + k + 1, right[j1 + k]))
    return rows


# ---------------------------------------------------------------------------
# Per-entry slices (for entry-by-entry diffs)
# ---------------------------------------------------------------------------
@dataclass
class EntrySpan:
    """One bib entry (or the loose lines between entries) as text slices."""

    key: Optional[str]   # None → lines outside any entry (@string, comments, blanks)
    lines: List[str]
    start: int           # 0-based line index of the first line in the source text


_ENTRY_RE = re.compile(r"^\s*@([A-Za-z]+)\s*[{(]\s*([^,\s{}()]*)")


def _brace_delta(line: str) -> int:
    return (line.count("{") + line.count("(")) - (line.count("}") + line.count(")"))


def split_entries(text: str) -> List[EntrySpan]:
    """Split a .bib text into per-entry slices, keyed by cite key.

    Loose lines between entries (blank lines, ``@string``, comments …) are
    collected into ``key=None`` slices so callers can diff whole documents
    entry by entry while still catching header-level changes.
    """
    lines = text.splitlines()
    spans: List[EntrySpan] = []
    pending: List[str] = []
    pending_start = 0
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        m = _ENTRY_RE.match(line)
        if m and m.group(1).lower() not in ("string", "preamble"):
            if pending:
                spans.append(EntrySpan(None, pending, pending_start))
                pending = []
            depth = _brace_delta(line)
            buf = [line]
            j = i
            while depth > 0 and j + 1 < n:
                j += 1
                buf.append(lines[j])
                depth += _brace_delta(lines[j])
            spans.append(EntrySpan(m.group(2) or None, buf, i))
            i = j + 1
        else:
            if not pending:
                pending_start = i
            pending.append(line)
            i += 1
    if pending:
        spans.append(EntrySpan(None, pending, pending_start))
    return spans
