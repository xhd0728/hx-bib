"""A tolerant, brace-aware BibTeX parser.

The parser is intentionally written from scratch (no third-party BibTeX
library) because hx-bib is a *formatter*: it must keep field order, tolerate
slightly broken input, and report what it could not parse instead of
silently dropping it.

Supported surface syntax:

* ``@type{key, field = value, ...}`` for arbitrary entry types;
* field values in braces ``{...}``, in double quotes ``"..."`` or bare
  tokens (numbers, ``@string`` macros);
* ``#`` concatenation of value parts;
* ``@string{name = "value"}`` definitions (kept verbatim);
* ``@preamble{...}`` and ``@comment{...}`` blocks (kept verbatim);
* everything between entries that is not markup (free comments) is kept as
  a comment block so it survives round-trips.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .model import Document, Entry

_ENTRY_HEAD_RE = re.compile(r"@\s*([A-Za-z]+)\s*([{(])")


class ParseError(Exception):
    """Raised only for input that contains no recoverable entries."""


class _Cursor:
    __slots__ = ("text", "pos", "n")

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    def eof(self) -> bool:
        return self.pos >= self.n

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < self.n else ""

    def skip_ws(self) -> None:
        while self.pos < self.n and self.text[self.pos].isspace():
            self.pos += 1

    def line_of(self, pos: int) -> int:
        return self.text.count("\n", 0, pos) + 1


def parse_bibtex(text: str) -> Document:
    """Parse ``text`` into a :class:`Document` (never raises on bad input)."""
    doc = Document()
    cur = _Cursor(text)
    pending_free_text_start = 0  # start of the run of plain text since last block

    while not cur.eof():
        at = text.find("@", cur.pos)
        if at == -1:
            rest = text[cur.pos:].strip()
            if rest:
                doc.comments.append(rest)
            break
        # Plain text between this @ and the previous block is a free comment.
        free = text[cur.pos:at].strip()
        if free:
            doc.comments.append(free)

        m = _ENTRY_HEAD_RE.match(text, at)
        if not m:
            # A bare "@" (e.g. in an email address) — treat as text and move on.
            cur.pos = at + 1
            continue

        etype = m.group(1).lower()
        open_char = m.group(2)
        close_char = "}" if open_char == "{" else ")"

        if etype in ("comment", "preamble"):
            block, cur.pos, ok = _read_balanced(text, m.end(), open_char, close_char)
            if not ok:
                doc.warnings.append(
                    f"line {cur.line_of(at)}: unterminated @{etype} block"
                )
            raw = text[at : cur.pos].strip()
            (doc.comments if etype == "comment" else doc.preamble).append(raw)
            continue

        if etype == "string":
            block, cur.pos, ok = _read_balanced(text, m.end(), open_char, close_char)
            if not ok:
                doc.warnings.append(
                    f"line {cur.line_of(at)}: unterminated @string block"
                )
            doc.strings.append(text[at : cur.pos].strip())
            continue

        entry, cur.pos, error = _parse_entry(
            text, at, m.end(), etype, open_char, close_char, cur.line_of(at)
        )
        if entry is not None:
            doc.entries.append(entry)
        if error:
            doc.warnings.append(error)

    return doc


def _read_balanced(
    text: str, start: int, open_char: str, close_char: str
) -> Tuple[str, int, bool]:
    """Read from ``start`` until the matching close char. Returns (body, end, ok)."""
    depth = 1
    i = start
    n = len(text)
    quote_depth = 0
    while i < n:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if open_char == "{" and depth == 0:
                return text[start:i], i + 1, True
        elif open_char == "(" and depth == 1 and ch == ")":
            return text[start:i], i + 1, True
        i += 1
    return text[start:n], n, False


def _parse_entry(
    text: str,
    at: int,
    body_start: int,
    etype: str,
    open_char: str,
    close_char: str,
    line: int,
) -> Tuple[Optional[Entry], int, Optional[str]]:
    # Locate the matching closing brace first so a broken entry does not eat
    # the rest of the file.
    body, end, ok = _read_balanced(text, body_start, open_char, close_char)
    error = None
    if not ok:
        error = f"line {line}: unterminated entry @{etype}{open_char}…"
        body = body  # best effort

    # Cite key: everything before the first top-level comma.
    comma = _find_top_level(body, ",")
    if comma == -1:
        key = body.strip()
        rest = ""
    else:
        key = body[:comma].strip()
        rest = body[comma + 1 :]

    key = key.strip().rstrip(",")
    if not key:
        return None, end, error or f"line {line}: entry @{etype} has no cite key"

    entry = Entry(type=etype, key=key, line=line)
    _parse_fields(rest, entry)
    return entry, end, error


def _find_top_level(s: str, needle: str) -> int:
    depth = 0
    in_quote = False
    for i, ch in enumerate(s):
        if ch == '"' and depth == 0:
            in_quote = not in_quote
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == needle and depth == 0 and not in_quote:
            return i
    return -1


def _parse_fields(rest: str, entry: Entry) -> None:
    i, n = 0, len(rest)
    while i < n:
        # Skip separators / whitespace.
        while i < n and rest[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        eq = rest.find("=", i)
        if eq == -1:
            break
        name = rest[i:eq].strip().lower()
        # A "name" containing commas/braces is not a field — stop.
        if not name or "," in name or "{" in name or "}" in name:
            break
        i = eq + 1

        parts: List[str] = []
        bare = True
        while True:
            while i < n and rest[i] in " \t\r\n":
                i += 1
            if i >= n:
                break
            ch = rest[i]
            if ch == "#":
                i += 1
                continue
            if ch == "{":
                value, i = _read_value_braced(rest, i + 1)
                parts.append(value)
                bare = False
            elif ch == '"':
                value, i = _read_value_quoted(rest, i + 1)
                parts.append(value)
                bare = False
            else:
                # Bare token: @string macro name or number.
                j = i
                while j < n and rest[j] not in ",#} \t\r\n":
                    j += 1
                token = rest[i:j].strip()
                parts.append(token)
                i = j
            # After a value part: another '#' continues, ',' ends the field.
            k = i
            while k < n and rest[k] in " \t\r\n":
                k += 1
            if k < n and rest[k] == "#":
                i = k + 1
                continue
            i = k
            break

        if name:
            entry.fields[name] = "".join(parts).strip()
            if bare:
                entry.bare_fields.add(name)
        # Skip the comma after the field (if any).
        while i < n and rest[i] in " \t\r\n,":
            i += 1


def _read_value_braced(s: str, i: int) -> Tuple[str, int]:
    depth = 1
    n = len(s)
    start = i
    while i < n:
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
        i += 1
    return s[start:n], n


def _read_value_quoted(s: str, i: int) -> Tuple[str, int]:
    depth = 0
    n = len(s)
    start = i
    while i < n:
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == '"' and depth == 0:
            return s[start:i], i + 1
        i += 1
    return s[start:n], n
