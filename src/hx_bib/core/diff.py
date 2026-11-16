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


def _move_key(line: str) -> str:
    """Normalization used to recognize *moved* lines: ignore a trailing comma
    (it belongs to the position, not the value)."""
    return line.rstrip().rstrip(",")


def diff_lines(
    before_text: str,
    after_text: str,
    left_offset: int = 0,
    right_offset: int = 0,
) -> List[DiffRow]:
    """Side-by-side rows. ``*_offset`` shifts the displayed line numbers (so a
    slice of a file can diff with its original absolute line numbers).

    The diff is *semantic about moves*: a line that exists verbatim on both
    sides but at a different position (e.g. a field reshuffled by the
    formatter) is paired as ``equal`` instead of showing up as delete +
    insert. Only lines whose content actually changed are reported as
    ``replace`` / ``delete`` / ``insert`` —
    “行位置变了不算修改，值变了才算”.
    """
    left = before_text.splitlines()
    right = after_text.splitlines()
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)

    left_rows: List[DiffRow] = []
    changed_l: List[tuple] = []  # (0-based line index, text)
    changed_r: List[tuple] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                left_rows.append(DiffRow(
                    "equal",
                    left_offset + i1 + k + 1, left[i1 + k],
                    right_offset + j1 + k + 1, right[j1 + k],
                ))
        else:
            changed_l.extend((i, left[i]) for i in range(i1, i2))
            changed_r.extend((j, right[j]) for j in range(j1, j2))

    # 1) pair identical lines across the whole call (moves → context rows).
    #    Trailing commas are ignored when matching: when the formatter moves a
    #    field, the comma moves with the position, not with the value.
    pool: dict = {}
    for pos, (_, text) in enumerate(changed_r):
        pool.setdefault(_move_key(text), []).append(pos)
    used_r: set = set()
    rest_l: List[tuple] = []
    for i, text in changed_l:
        matches = pool.get(_move_key(text))
        if matches:
            pos = matches.pop(0)
            used_r.add(pos)
            j = changed_r[pos][0]
            left_rows.append(DiffRow(
                "equal", left_offset + i + 1, text,
                right_offset + j + 1, right[j],
            ))
        else:
            rest_l.append((i, text))
    rest_r = [(j, t) for pos, (j, t) in enumerate(changed_r) if pos not in used_r]

    # 2) what is left: align positionally as replace rows, then delete / insert
    paired = min(len(rest_l), len(rest_r))
    for p in range(paired):
        i, ltxt = rest_l[p]
        j, rtxt = rest_r[p]
        left_rows.append(DiffRow(
            "replace", left_offset + i + 1, ltxt, right_offset + j + 1, rtxt,
        ))
    for i, ltxt in rest_l[paired:]:
        left_rows.append(DiffRow("delete", left_offset + i + 1, ltxt, None, None))

    left_rows.sort(key=lambda r: r.left_no)
    tail = [
        DiffRow("insert", None, None, right_offset + j + 1, rtxt)
        for j, rtxt in rest_r[paired:]
    ]
    return left_rows + tail


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
