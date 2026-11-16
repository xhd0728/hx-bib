"""Core data model for BibTeX documents.

The model is deliberately small and dependency-free:

* :class:`Entry`      — one BibTeX record (type, cite key, ordered fields).
* :class:`Document`   — a parsed ``.bib`` file: entries plus opaque
  ``@string`` / ``@preamble`` / ``@comment`` blocks that must survive a
  round-trip.

Field names are handled case-insensitively (BibTeX semantics) while the
original insertion order is preserved, which lets the formatter reorder
fields without losing information.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional


def canonical_field_name(name: str) -> str:
    """BibTeX field names are case-insensitive; we store them lowercase."""
    return name.strip().lower()


@dataclass
class Entry:
    """A single BibTeX record."""

    type: str  # e.g. "inproceedings" (always lowercase)
    key: str
    fields: Dict[str, str] = field(default_factory=dict)
    # Values that were written without braces/quotes (e.g. numeric years,
    # @string macros) are flagged here so the writer does not wrap them.
    bare_fields: set = field(default_factory=set)
    line: int = 0  # 1-based line number of the "@" that opened the entry

    # -- field access (case-insensitive) -----------------------------------
    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.fields.get(canonical_field_name(name), default)

    def has(self, name: str) -> bool:
        return canonical_field_name(name) in self.fields

    def set(self, name: str, value: str) -> None:
        cname = canonical_field_name(name)
        self.fields[cname] = value
        self.bare_fields.discard(cname)  # new value is a string we control

    def remove(self, name: str) -> bool:
        cname = canonical_field_name(name)
        self.bare_fields.discard(cname)
        return self.fields.pop(cname, None) is not None

    def rename(self, old: str, new: str) -> bool:
        cname_old, cname_new = canonical_field_name(old), canonical_field_name(new)
        if cname_old not in self.fields:
            return False
        value = self.fields.pop(cname_old)
        self.fields[cname_new] = value
        if cname_old in self.bare_fields:
            self.bare_fields.discard(cname_old)
            self.bare_fields.add(cname_new)
        return True

    def items(self) -> Iterator[tuple]:
        return iter(self.fields.items())

    def __bool__(self) -> bool:  # an entry with a key is always truthy
        return bool(self.key)


@dataclass
class Document:
    """A parsed ``.bib`` file."""

    entries: List[Entry] = field(default_factory=list)
    # Raw ``@string{...}`` blocks, kept verbatim and re-emitted at the top.
    strings: List[str] = field(default_factory=list)
    preamble: List[str] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def keys(self) -> List[str]:
        return [e.key for e in self.entries]

    def duplicate_keys(self) -> Dict[str, int]:
        """Return ``{key: count}`` for keys that appear more than once."""
        counts: Dict[str, int] = {}
        for e in self.entries:
            counts[e.key] = counts.get(e.key, 0) + 1
        return {k: c for k, c in counts.items() if c > 1}

    def find(self, key: str) -> Optional[Entry]:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)
