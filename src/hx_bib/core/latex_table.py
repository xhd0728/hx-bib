"""LaTeX table formatter (tabular / booktabs aware).

Formats the *body* of tabular-like environments without touching anything
outside them:

* one data row per physical line (cells never split across lines);
* ``&`` separated by single spaces, or optionally column-aligned with
  padded cells so every ``&`` lines up;
* every rule (``\\toprule``, ``\\midrule``, ``\\hline``, ``\\cmidrule``…)
  on its own line — multiple rules crammed on one source line are split;
* optional blank lines around rules (consecutive rules stay grouped, and
  no blank line is inserted right after ``\\begin`` or before ``\\end``).

The tokenizer is deliberately conservative: cells are only split on ``&``
and rows only on ``\\\\`` when they are outside braces, outside nested
environments (``\\begin{aligned}``…``\\end{aligned}``, ``\\makecell{…\\\\…}``)
and not escaped (``\\&``), so ``\\multicolumn``, ``$…$`` math and friends
survive untouched.  Dropped ``%`` comments, unterminated rows and malformed
argument groups are reported as warnings instead of raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

TABULAR_ENVS = ("tabular", "tabular*", "tabularx", "tabulary", "longtable",
                "array")

#: Number of ``{…}`` groups after ``\begin{name}``; the last is the colspec.
_ENV_ARG_COUNT = {"tabular": 1, "tabular*": 2, "tabularx": 2, "tabulary": 2,
                  "longtable": 1, "array": 1}

#: Commands that count as horizontal rules / structural lines.
_RULE_NAMES = (
    "toprule", "midrule", "bottomrule", "hline", "cline", "cmidrule",
    "specialrule", "addlinespace", "morecmidrules",
    "endfirsthead", "endhead", "endfoot", "endlastfoot",
)

_ENV_BEGIN_RE = re.compile(r"\\begin\s*\{([A-Za-z*]+)\}")
_ENV_END_RE = re.compile(r"\\end\s*\{([A-Za-z*]+)\}")
_CONTROL_RE = re.compile(r"[A-Za-z]+")
_MULTICOLUMN_RE = re.compile(r"^\\multicolumn\s*\{\s*(\d+)\s*\}")


@dataclass
class TableFormatOptions:
    """How the table body should be re-rendered."""

    amp_style: str = "space"        # "space" | "align" | "none"
    blank_lines_around_rules: bool = True
    row_indent: int = 0             # extra spaces relative to \begin line
    indent_unit: str = " "          # character used for row_indent

    def validate(self) -> None:
        if self.amp_style not in ("space", "align", "none"):
            raise ValueError(f"unknown amp_style: {self.amp_style}")
        if self.row_indent < 0:
            raise ValueError("row_indent must be >= 0")


# ---------------------------------------------------------------------------
# Table elements
# ---------------------------------------------------------------------------
@dataclass
class Cell:
    text: str
    span: int = 1                   # >1 for \multicolumn{n}…{…}


@dataclass
class Row:
    cells: List[Cell]
    end: str = "\\\\"               # "\\", "\\[2pt]", "\tabularnewline", ""


@dataclass
class Rule:
    text: str


@dataclass
class CommentLine:
    text: str


Element = object  # Row | Rule | CommentLine


@dataclass
class TableSlice:
    name: str
    begin: int                      # index of "\begin{...}" in the source text
    body_start: int                 # index right after the colspec arguments
    body_end: int                   # index of the closing "\end{...}"
    end: int                        # index right after "\end{name}"
    colspec: str
    start_line: int = 0             # 0-based line of "\begin"


@dataclass
class TableFormatResult:
    text: str
    tables: int
    rows: int
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level scanning helpers
# ---------------------------------------------------------------------------
def _skip_group(text: str, i: int) -> int:
    """``text[i]`` is '{' — return the index just past its matching '}'."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _skip_optional(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """``text[i]`` is ``open_ch`` — return the index just past its match."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _control_word(text: str, i: int) -> Tuple[str, int]:
    """``text[i]`` is a backslash; return (full control sequence, next index)."""
    m = _CONTROL_RE.match(text, i + 1)
    if m:
        return "\\" + m.group(0), m.end()
    if i + 1 < len(text):
        return text[i:i + 2], i + 2
    return "\\", i + 1


def _consume_rule_args(text: str, i: int) -> int:
    """Consume ``(…)``/``[…]``/``{…}`` argument groups right after a rule."""
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
            continue
        if c == "(":
            i = _skip_optional(text, i, "(", ")")
        elif c == "[":
            i = _skip_optional(text, i, "[", "]")
        elif c == "{":
            i = _skip_group(text, i)
        else:
            break
    return i


def _consume_env_args(name: str, text: str, i: int,
                      warnings: List[str]) -> Tuple[int, str]:
    """Skip the arguments of a tabular opening; return (body_start, colspec)."""
    want = _ENV_ARG_COUNT[name]
    colspec = ""
    groups: List[str] = []
    while len(groups) < want and i < len(text):
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "[" and not groups:                 # e.g. longtable [c]
            i = _skip_optional(text, i, "[", "]")
        elif c == "{":
            end = _skip_group(text, i)
            groups.append(text[i:end])
            i = end
        else:
            warnings.append(f"{name}: malformed opening arguments")
            break
    if groups:
        colspec = groups[-1]
    return i, colspec


# ---------------------------------------------------------------------------
# Body tokenizer
# ---------------------------------------------------------------------------
def _scan_body(body: str, warnings: List[str]) -> List[Element]:
    elements: List[Element] = []
    cells: List[Cell] = []
    buf: List[str] = []
    env_stack: List[str] = []
    depth = 0
    i, n = 0, len(body)

    def cell_text() -> str:
        raw = "".join(buf)
        raw = re.sub(r"[ \t]*\r?\n\s*", " ", raw)      # join physical lines
        return raw.strip()

    def flush_cell() -> None:
        text = cell_text()
        span = 1
        m = _MULTICOLUMN_RE.match(text)
        if m:
            try:
                span = max(1, int(m.group(1)))
            except ValueError:
                span = 1
        cells.append(Cell(text=text, span=span))
        buf.clear()

    def flush_row(end: str) -> None:
        nonlocal cells
        flush_cell()
        # A bare "\\" with nothing before it is just a blank source line;
        # rows with several cells (even if all empty, e.g. "& & \\") count.
        if len(cells) > 1 or any(c.text for c in cells):
            elements.append(Row(cells=cells, end=end))
        cells = []

    while i < n:
        c = body[i]
        if c == "\\":
            word, nxt = _control_word(body, i)
            top = depth == 0 and not env_stack
            if word == "\\\\" and top:                    # row separator
                end = "\\\\"
                j = nxt
                if j < n and body[j] == "*":
                    end += "*"
                    j += 1
                if j < n and body[j] == "[":
                    k = _skip_optional(body, j, "[", "]")
                    end += body[j:k]
                    j = k
                flush_row(end)
                i = j
                continue
            if word == "\\tabularnewline" and top:
                end = word
                j = nxt
                if j < n and body[j] == "[":
                    k = _skip_optional(body, j, "[", "]")
                    end += body[j:k]
                    j = k
                flush_row(end)
                i = j
                continue
            if word == "\\cr" and top:
                flush_row("\\cr")
                i = nxt
                continue
            if word == "\\begin":
                m = _ENV_BEGIN_RE.match(body, i)
                if m:
                    env_stack.append(m.group(1))
                    buf.append(m.group(0))
                    i = m.end()
                    continue
            if word == "\\end":
                m = _ENV_END_RE.match(body, i)
                if m:
                    if env_stack and env_stack[-1] == m.group(1):
                        env_stack.pop()
                    elif env_stack:
                        warnings.append(f"mismatched \\end{{{m.group(1)}}}")
                        env_stack.pop()
                    buf.append(m.group(0))
                    i = m.end()
                    continue
            bare = word[1:]
            if (bare in _RULE_NAMES and top
                    and not "".join(buf).strip() and not cells):
                j = _consume_rule_args(body, nxt)
                elements.append(Rule(text=body[i:j].strip()))
                i = j
                continue
            buf.append(word)
            i = nxt
            continue
        if c == "{":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c == "}":
            depth = max(0, depth - 1)
            buf.append(c)
            i += 1
            continue
        if c == "&" and depth == 0 and not env_stack:
            flush_cell()
            i += 1
            continue
        if c == "%" and not env_stack:
            j = body.find("\n", i)
            j = n if j == -1 else j
            comment = body[i:j].strip()
            if cells or "".join(buf).strip():
                warnings.append(f"dropped comment: {comment[:60]}")
            elif comment:
                elements.append(CommentLine(text=comment))
            i = j
            continue
        buf.append(c)
        i += 1

    if "".join(buf).strip() or any(c.text for c in cells):
        flush_cell()
        if any(c.text for c in cells):
            elements.append(Row(cells=cells, end=""))
            warnings.append("unterminated row (missing \\\\)")
        cells = []
    if env_stack:
        warnings.append(f"unclosed environment(s): {', '.join(env_stack)}")
    return elements


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
def _column_widths(rows: List[Row]) -> List[int]:
    widths: List[int] = []
    for row in rows:
        col = 0
        for cell in row.cells:
            if cell.span == 1:
                while len(widths) <= col:
                    widths.append(0)
                widths[col] = max(widths[col], len(cell.text))
            col += cell.span
    return widths


def _span_content(text: str) -> str:
    r"""Inner content of a ``\multicolumn`` cell (for width estimation)."""
    if not text.endswith("}"):
        return text
    depth = 0
    for j in range(len(text) - 1, -1, -1):
        c = text[j]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                return text[j + 1:len(text) - 1]
    return text


def _render_row(row: Row, widths: List[int], opts: TableFormatOptions) -> str:
    if opts.amp_style == "none":
        line = "&".join(c.text for c in row.cells)
        return (line + " " + row.end) if row.end else line
    if opts.amp_style == "align":
        parts = []
        col = 0
        for cell in row.cells:
            if cell.span == 1:
                width = widths[col] if col < len(widths) else len(cell.text)
                pad = max(0, width - len(cell.text))
            else:
                # A multicolumn cell sits across `span` columns joined by
                # " & " separators (3 chars each); its visual width is the
                # inner content, the command wrapper is not printed.
                width = sum(widths[col:col + cell.span]) + 3 * (cell.span - 1)
                pad = max(0, width - len(_span_content(cell.text)))
            parts.append(cell.text + " " * pad)
            col += cell.span
        line = " & ".join(parts).rstrip()
    else:                                              # "space"
        line = " & ".join(c.text for c in row.cells)
    if row.end:
        line += " " + row.end
    return line


def _render_elements(elements: List[Element], widths: List[int],
                     opts: TableFormatOptions) -> List[str]:
    out: List[str] = []
    blank = opts.blank_lines_around_rules

    for idx, el in enumerate(elements):
        if isinstance(el, Rule):
            prev = elements[idx - 1] if idx > 0 else None
            nxt = elements[idx + 1] if idx + 1 < len(elements) else None
            if blank and out and out[-1] != "" and not isinstance(prev, Rule):
                out.append("")
            out.append(el.text)
            if (blank and nxt is not None and not isinstance(nxt, Rule)
                    and not isinstance(nxt, CommentLine)):
                out.append("")
        elif isinstance(el, Row):
            out.append(_render_row(el, widths, opts))
        elif isinstance(el, CommentLine):
            out.append(el.text)
    while out and out[-1] == "":
        out.pop()
    return out


# ---------------------------------------------------------------------------
# Table discovery
# ---------------------------------------------------------------------------
def find_tables(text: str) -> Tuple[List[TableSlice], List[str]]:
    """Locate every outermost tabular-like environment in ``text``."""
    warnings: List[str] = []
    slices: List[TableSlice] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "\\":
            i += 1
            continue
        m = _ENV_BEGIN_RE.match(text, i)
        if not m or m.group(1) not in TABULAR_ENVS:
            i += 1
            continue
        name = m.group(1)
        body_start, colspec = _consume_env_args(name, text, m.end(), warnings)
        depth = 1
        j = body_start
        found_end = -1
        end_match = None
        while j < n:
            m_end = _ENV_END_RE.search(text, j)
            if m_end is None:
                break
            m_beg = _ENV_BEGIN_RE.search(text, j)
            if m_beg is not None and m_beg.start() < m_end.start():
                if m_beg.group(1) == name:
                    depth += 1
                j = m_beg.end()
                continue
            if m_end.group(1) == name:
                depth -= 1
                if depth == 0:
                    found_end = m_end.start()
                    end_match = m_end
                    break
            j = m_end.end()
        if end_match is None:
            warnings.append(f"unclosed {name} (skipped)")
            i = m.end()
            continue
        slices.append(TableSlice(
            name=name, begin=i, body_start=body_start,
            body_end=found_end, end=end_match.end(), colspec=colspec,
            start_line=text.count("\n", 0, i),
        ))
        i = end_match.end()
    return slices, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_table_body(body: str,
                     warnings: Optional[List[str]] = None) -> List[Element]:
    """Tokenize a table body into Row / Rule / CommentLine elements."""
    return _scan_body(body, warnings if warnings is not None else [])


def format_table_body(body: str,
                      opts: TableFormatOptions) -> Tuple[List[str], int, List[str]]:
    """Format one table body; returns (rendered lines, row count, warnings)."""
    opts.validate()
    warnings: List[str] = []
    elements = _scan_body(body, warnings)
    rows = [el for el in elements if isinstance(el, Row)]
    widths = _column_widths(rows)
    lines = _render_elements(elements, widths, opts)
    return lines, len(rows), warnings


def format_tables(text: str,
                  opts: Optional[TableFormatOptions] = None) -> TableFormatResult:
    """Format every tabular-like environment in ``text``."""
    opts = opts or TableFormatOptions()
    opts.validate()
    slices, warnings = find_tables(text)
    if not slices:
        return TableFormatResult(text=text, tables=0, rows=0, warnings=warnings)
    out: List[str] = []
    pos = 0
    rows_total = 0
    for sl in slices:
        line_start = text.rfind("\n", 0, sl.begin) + 1
        indent = text[line_start:sl.begin]
        if indent.strip():                    # \begin shares the line
            indent = ""
        begin_str = text[sl.begin:sl.body_start].rstrip()
        body = text[sl.body_start:sl.body_end]
        lines, n_rows, body_warnings = format_table_body(body, opts)
        warnings.extend(body_warnings)
        rows_total += n_rows
        row_prefix = indent + opts.indent_unit * opts.row_indent
        rendered = "\n".join((row_prefix + ln) if ln else "" for ln in lines)
        piece = begin_str
        if rendered:
            piece += "\n" + rendered
        piece += "\n" + indent + text[sl.body_end:sl.end].strip()
        out.append(text[pos:sl.begin])
        out.append(piece)
        pos = sl.end
    out.append(text[pos:])
    return TableFormatResult(text="".join(out), tables=len(slices),
                             rows=rows_total, warnings=warnings)
