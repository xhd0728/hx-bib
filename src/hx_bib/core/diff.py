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


def diff_lines(before_text: str, after_text: str) -> List[DiffRow]:
    left = before_text.splitlines()
    right = after_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    rows: List[DiffRow] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(DiffRow("equal", i1 + k + 1, left[i1 + k], j1 + k + 1, right[j1 + k]))
            continue
        m, n = i2 - i1, j2 - j1
        paired = min(m, n)
        for k in range(paired):
            rows.append(DiffRow("replace", i1 + k + 1, left[i1 + k], j1 + k + 1, right[j1 + k]))
        for k in range(paired, m):
            rows.append(DiffRow("delete", i1 + k + 1, left[i1 + k], None, None))
        for k in range(paired, n):
            rows.append(DiffRow("insert", None, None, j1 + k + 1, right[j1 + k]))
    return rows
