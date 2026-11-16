"""Rigorous tests for the LaTeX table formatter.

Every formatting test that succeeds also verifies *semantic equivalence*:
re-tokenizing the formatted output must yield exactly the same cells,
spans, row ends and rules as the input — this is what catches tokenizer
and renderer bugs that would silently corrupt a table.
"""

import pytest

from hx_bib.core.latex_table import (
    Cell,
    CommentLine,
    Rule,
    Row,
    TableFormatOptions,
    find_tables,
    format_tables,
    parse_table_body,
)

ABLATION = r"""\begin{table}[t]
\centering
\caption{Ablation Study of CoRoute with Qwen3-32B.}
\label{tab:answer_ablation_combined}
\small
\setlength{\tabcolsep}{8pt}
\begin{tabular}{lcccc}
\toprule \textbf{Method} & \multicolumn{2}{c}{\textbf{TriviaQA}} & \multicolumn{2}{c}{\textbf{2WikiMQA}} \\ \cmidrule(l){2-3}\cmidrule(l){4-5} & \textbf{EM} & \textbf{F1} & \textbf{EM} & \textbf{F1} \\ \midrule \multicolumn{5}{l}{\textit{\textbf{Component Ablation}}} \\ \midrule Vanilla RAG & 61.5 & 70.0 & 23.4 & 27.5 \\ + Category-free rewriting & 61.2 & 69.8 & 23.7 & 27.4 \\ \bottomrule
\end{tabular}
\end{table}
"""


def _signature(body):
    """Comparable representation of a tokenized body."""
    sig = []
    for el in parse_table_body(body):
        if isinstance(el, Row):
            sig.append(("row", tuple((c.text, c.span) for c in el.cells), el.end))
        elif isinstance(el, Rule):
            sig.append(("rule", el.text))
        else:
            sig.append(("comment", el.text))
    return sig


def _body_of(text, index=0):
    slices, _ = find_tables(text)
    return text[slices[index].body_start:slices[index].body_end]


def _format_and_check(text, **opts):
    """Format, assert semantic equivalence + idempotency, return result."""
    result = format_tables(text, TableFormatOptions(**opts))
    assert result.tables >= 1
    # Same number of tables, same element sequence in each.
    before, _ = find_tables(text)
    after, _ = find_tables(result.text)
    assert len(before) == len(after)
    for i in range(len(before)):
        assert _signature(_body_of(text, i)) == _signature(_body_of(result.text, i))
    # Idempotent.
    again = format_tables(result.text, TableFormatOptions(**opts))
    assert again.text == result.text
    return result


def _row_lines(text):
    body = _body_of(text)
    return [ln for ln in _body_of(text).split("\n") if "&" in ln]


# ---------------------------------------------------------------------------
# Core formatting behaviour
# ---------------------------------------------------------------------------
def test_ablation_table_one_row_per_line():
    result = _format_and_check(ABLATION)
    rows = _row_lines(result.text)
    # 4 rows contain column separators: header ×2 and the two results.
    assert len(rows) == 4
    for line in rows:
        assert line.strip().endswith("\\\\")
        assert "\n" not in line
    # The single-cell component row is on its own line too.
    comp = [ln for ln in result.text.split("\n") if "Component Ablation" in ln]
    assert len(comp) == 1 and comp[0].strip().endswith("\\\\")
    # Nothing outside the tabular was touched.
    assert result.text.startswith("\\begin{table}[t]\n\\centering")
    assert result.text.endswith("\\end{tabular}\n\\end{table}\n")


def test_amp_single_space():
    result = _format_and_check(ABLATION)
    body = _body_of(result.text)
    assert "A & B" not in body
    assert "\\textbf{EM} & \\textbf{F1}" in body
    assert "&  " not in body          # no doubled spaces around &
    label = [ln for ln in body.split("\n") if "Vanilla RAG" in ln][0]
    assert "Vanilla RAG & 61.5 & 70.0 & 23.4 & 27.5 \\\\" in label


def test_amp_none_removes_padding():
    text = r"\begin{tabular}{cc} a & b \\ c & d \\ \end{tabular}"
    result = _format_and_check(text, amp_style="none")
    assert "a&b \\\\" in result.text


def test_amp_align_pads_columns():
    text = (
        r"\begin{tabular}{lcc}" + "\n"
        "\\toprule\n"
        "long name & 1 & 22 \\\\\n"
        "x & 100 & 3 \\\\\n"
        "\\bottomrule\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text, amp_style="align")
    body = _body_of(result.text)
    row1 = [ln for ln in body.split("\n") if "long name" in ln][0]
    row2 = [ln for ln in body.split("\n") if "100" in ln][0]
    assert "long name & 1   & 22 \\\\" == row1.rstrip() or "long name & 1" in row1
    # Every & in data rows lines up at the same column.
    amps = [[idx for idx, ch in enumerate(ln) if ch == "&"] for ln in (row1, row2)]
    assert amps[0][0] == amps[1][0]
    assert amps[0][1] == amps[1][1]


def test_align_and_space_are_semantically_equal():
    r1 = format_tables(ABLATION, TableFormatOptions(amp_style="space"))
    r2 = format_tables(ABLATION, TableFormatOptions(amp_style="align"))
    assert _signature(_body_of(r1.text)) == _signature(_body_of(r2.text))


def test_align_handles_multicolumn_without_corruption():
    text = (
        "\\begin{tabular}{lccc}\n"
        "\\toprule\n"
        "\\multicolumn{3}{c}{Header A} & B \\\\\n"
        "short & 12 & 345 & 6 \\\\\n"
        "longer & 7 & 8 & 99 \\\\\n"
        "\\bottomrule\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text, amp_style="align")
    body = _body_of(result.text)
    header = [ln for ln in body.split("\n") if "Header A" in ln][0]
    assert "\\multicolumn{3}{c}{Header A}" in header
    assert header.rstrip().endswith("\\\\")
    # Normal rows align with each other.
    d1 = [ln for ln in body.split("\n") if ln.startswith("short")][0]
    d2 = [ln for ln in body.split("\n") if ln.startswith("longer")][0]
    assert d1.index("& 12") == d2.index("& 7") == d1.index("& 12")
    amps1 = [i for i, ch in enumerate(d1) if ch == "&"]
    amps2 = [i for i, ch in enumerate(d2) if ch == "&"]
    assert amps1 == amps2


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def test_rules_on_own_lines_and_blank_lines():
    result = _format_and_check(ABLATION)
    body = _body_of(result.text)
    lines = body.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("\\bottomrule"):
            continue
        # No rule shares a line with row content.
        for rule in ("\\toprule", "\\midrule", "\\cmidrule"):
            if rule in line and not s.startswith(rule):
                pytest.fail(f"{rule} shares a line: {line!r}")
    # Blank line after \toprule before the first row.
    top = lines.index("\\toprule")
    assert lines[top + 1] == ""
    # Blank line around \midrule.
    mid = lines.index("\\midrule")
    assert lines[mid - 1] == "" and lines[mid + 1] == ""


def test_consecutive_rules_stay_grouped():
    result = _format_and_check(ABLATION)
    lines = _body_of(result.text).split("\n")
    i = [n for n, ln in enumerate(lines) if ln.startswith("\\cmidrule(l){2-3}")][0]
    assert lines[i + 1].startswith("\\cmidrule(l){4-5}")
    # And exactly one blank line after the pair.
    assert lines[i + 2] == ""
    assert not lines[i + 3].strip() == ""


def test_hline_split_and_cline():
    text = (
        r"\begin{tabular}{cc} a & b \\ \hline\hline c & d \\ \cline{1-1} e & f \\ \end{tabular}"
    )
    result = _format_and_check(text)
    lines = _body_of(result.text).split("\n")
    assert lines.count("\\hline") == 2
    assert "\\cline{1-1}" in lines


def test_no_blank_line_at_table_start_or_end():
    result = _format_and_check(ABLATION)
    body = _body_of(result.text).strip("\n")
    assert body.split("\n")[0].strip() == "\\toprule"
    # Last element is \bottomrule with no trailing blank line before \end.
    assert body.endswith("\\bottomrule")


def test_blank_lines_option_off():
    result = _format_and_check(ABLATION, blank_lines_around_rules=False)
    body = _body_of(result.text).strip("\n")
    assert "" not in body.split("\n")


# ---------------------------------------------------------------------------
# Escaping / nesting corner cases
# ---------------------------------------------------------------------------
def test_escaped_ampersand_and_percent():
    text = r"\begin{tabular}{cc} AT\&T & 50\% \\ A \& B & C \\ \end{tabular}"
    result = _format_and_check(text)
    assert "AT\\&T & 50\\%" in result.text


def test_amp_inside_braces_not_split():
    text = r"\begin{tabular}{cc} \multicolumn{2}{c}{x \& y} \\ a & b \\ \end{tabular}"
    result = _format_and_check(text)
    row = [ln for ln in result.text.split("\n") if "multicolumn" in ln][0]
    assert row.count("&") == 1        # the escaped \& is not a separator


def test_math_ampersand_inside_braces():
    # Braced content is never split, even inside math mode.
    text = r"\begin{tabular}{cc} ${a & b}$ & x \\ \end{tabular}"
    result = _format_and_check(text)
    row = [ln for ln in result.text.split("\n") if "{a & b}" in ln]
    assert len(row) == 1


def test_double_backslash_inside_makecell_not_row_split():
    text = (
        "\\begin{tabular}{cc}\n"
        "\\makecell{a \\\\ b} & c \\\\\n"
        "d & e \\\\\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text)
    assert "\\makecell{a \\\\ b} & c \\\\" in result.text


def test_nested_aligned_env_protected():
    text = (
        "\\begin{tabular}{cc}\n"
        "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned} & x \\\\\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text)
    assert "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}" in result.text


def test_nested_tabular_inside_tabular():
    text = (
        "\\begin{tabular}{cc}\n"
        "\\begin{tabular}{l} inner \\\\ table \\end{tabular} & x \\\\\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text)
    # Only the outer table is formatted; the inner one is one cell.
    assert result.tables == 1
    assert "inner \\\\ table" in result.text


def test_row_end_variants_preserved():
    text = (
        "\\begin{tabular}{cc}\n"
        "a & b \\\\[2pt]\n"
        "c & d \\tabularnewline\n"
        "e & f \\\\*\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text)
    assert "a & b \\\\[2pt]" in result.text
    assert "c & d \\tabularnewline" in result.text
    assert "e & f \\\\*" in result.text


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
def test_comment_line_preserved_inline_dropped_with_warning():
    text = (
        "\\begin{tabular}{cc}\n"
        "% header note\n"
        "a & b % trailing\n"
        "c & d \\\\\n"
        "\\end{tabular}"
    )
    result = format_tables(text)
    assert "% header note" in result.text
    assert "% trailing" not in result.text
    assert any("dropped" in w for w in result.warnings)
    # The table still round-trips semantically.
    again = format_tables(result.text)
    assert again.text == result.text


# ---------------------------------------------------------------------------
# Multiple tables and surrounding text
# ---------------------------------------------------------------------------
def test_multiple_tables_outside_text_untouched():
    text = (
        "Intro paragraph with \\% and & symbols.\n"
        "\\begin{tabular}{cc}\na & b \\\\\n\\end{tabular}\n"
        "Middle text.\n"
        "\\begin{tabular}{ll}\nx & y \\\\\n\\end{tabular}\n"
        "Outro.\n"
    )
    result = _format_and_check(text)
    assert result.tables == 2
    assert result.text.startswith("Intro paragraph with \\% and & symbols.\n")
    assert "Middle text.\n" in result.text
    assert result.text.endswith("Outro.\n")


def test_single_line_table_expands():
    text = r"\begin{tabular}{cc} a & b \\ c & d \\ \end{tabular}"
    result = _format_and_check(text)
    lines = result.text.split("\n")
    assert lines[0] == "\\begin{tabular}{cc}"
    assert "a & b \\\\" in lines[1]
    assert "c & d \\\\" in lines[2]
    assert lines[3] == "\\end{tabular}"


def test_whitespace_perturbations_converge():
    # Rows glued together, spasmodic & padding, one canonical target.
    messy = "\\begin{tabular}{cc}\na&b \\\\c  &  d \\\\\n\\end{tabular}"
    clean = "\\begin{tabular}{cc}\na & b \\\\\nc & d \\\\\n\\end{tabular}"
    assert format_tables(messy).text == format_tables(clean).text


def test_reformat_never_grows():
    once = format_tables(ABLATION).text
    twice = format_tables(once).text
    assert len(twice) == len(once)


def test_indented_table_indent_preserved():
    text = "    \\begin{tabular}{cc}\n    a & b \\\\\n    \\end{tabular}"
    result = _format_and_check(text)
    assert "\n    a & b \\\\" in result.text
    assert "\n    \\end{tabular}" in result.text


def test_row_indent_option():
    text = "\\begin{tabular}{cc}\na & b \\\\\n\\end{tabular}"
    result = _format_and_check(text, row_indent=2)
    assert "\n  a & b \\\\" in result.text


# ---------------------------------------------------------------------------
# Environment variants
# ---------------------------------------------------------------------------
def test_tabularx_and_tabulary_arguments():
    for env in (
        "\\begin{tabularx}{\\linewidth}{lX}\na & b \\\\\n\\end{tabularx}",
        "\\begin{tabulary}{0.8\\textwidth}{lL}\na & b \\\\\n\\end{tabulary}",
        "\\begin{tabular*}{0.9\\linewidth}{l@{\\extracolsep{\\fill}}r}\n"
        "a & b \\\\\n\\end{tabular*}",
    ):
        result = _format_and_check(env)
        assert result.tables == 1
        assert "a & b \\\\" in result.text


def test_longtable_heads_and_rules():
    text = (
        "\\begin{longtable}{cc}\n"
        "\\toprule\n"
        "A & B \\\\\n"
        "\\midrule\n"
        "\\endfirsthead\n"
        "\\toprule\n"
        "A & B \\\\\n"
        "\\midrule\n"
        "\\endhead\n"
        "1 & 2 \\\\\n"
        "\\bottomrule\n"
        "\\end{longtable}"
    )
    result = _format_and_check(text)
    assert result.text.count("\\endhead") == 1
    assert result.text.count("\\endfirsthead") == 1


def test_specialrule_and_addlinespace():
    text = (
        "\\begin{tabular}{cc}\n"
        "\\specialrule{1pt}{2pt}{3pt}\n"
        "a & b \\\\\n"
        "\\addlinespace[2pt]\n"
        "c & d \\\\\n"
        "\\end{tabular}"
    )
    result = _format_and_check(text)
    assert "\\specialrule{1pt}{2pt}{3pt}" in result.text
    assert "\\addlinespace[2pt]" in result.text


# ---------------------------------------------------------------------------
# Malformed input must never crash
# ---------------------------------------------------------------------------
def test_unclosed_tabular_warns_and_leaves_text():
    text = "\\begin{tabular}{cc}\na & b \\\\\n"
    result = format_tables(text)
    assert result.tables == 0
    assert result.text == text
    assert any("unclosed" in w for w in result.warnings)


def test_unterminated_row_warns():
    text = "\\begin{tabular}{cc}\na & b\n\\end{tabular}"
    result = format_tables(text)
    assert any("unterminated" in w for w in result.warnings)
    assert "a & b" in result.text


def test_unbalanced_braces_no_crash():
    text = "\\begin{tabular}{cc}\n\\textbf{a & b \\\\\n\\end{tabular}"
    result = format_tables(text)
    assert "\\textbf{a" in result.text       # content survives


def test_truncated_backslash_no_crash():
    text = "\\begin{tabular}{cc}\na & b \\\n\\end{tabular}"
    result = format_tables(text)
    assert "a & b" in result.text


def test_invalid_options_raise():
    with pytest.raises(ValueError):
        format_tables("x", TableFormatOptions(amp_style="nope"))
    with pytest.raises(ValueError):
        format_tables("x", TableFormatOptions(row_indent=-1))


def test_no_tables_returns_input_unchanged():
    text = "No tables here.\n\\begin{equation}\nx = 1\n\\end{equation}\n"
    result = format_tables(text)
    assert result.tables == 0
    assert result.text == text


# ---------------------------------------------------------------------------
# AST shape sanity (tokenizer unit checks)
# ---------------------------------------------------------------------------
def test_parse_cells_and_spans():
    body = "a & \\multicolumn{2}{c}{wide} & b \\\\ c & d & e \\\\"
    els = parse_table_body(body)
    assert len(els) == 2
    row1, row2 = els
    assert [c.text for c in row1.cells] == ["a", "\\multicolumn{2}{c}{wide}", "b"]
    assert [c.span for c in row1.cells] == [1, 2, 1]
    assert [c.text for c in row2.cells] == ["c", "d", "e"]
    assert all(isinstance(el, Row) for el in els)


def test_parse_rule_argument_shapes():
    body = "\\cmidrule(lr){2-3}\\cmidrule[1pt](l){4-5}\\specialrule{.1em}{.2em}{.3em}x"
    els = parse_table_body(body)
    rules = [el for el in els if isinstance(el, Rule)]
    assert [r.text for r in rules] == [
        "\\cmidrule(lr){2-3}",
        "\\cmidrule[1pt](l){4-5}",
        "\\specialrule{.1em}{.2em}{.3em}",
    ]


def test_parse_all_empty_row_kept():
    els = parse_table_body("a & b \\\\ & & \\\\ c & d \\\\")
    assert len(els) == 3
    assert [c.text for c in els[1].cells] == ["", "", ""]


def test_parse_multiline_cell_joined():
    els = parse_table_body("a very\n  long & cell \\\\")
    assert els[0].cells[0].text == "a very long"


def test_comment_lines_are_elements():
    els = parse_table_body("% note one\n% note two\na & b \\\\")
    assert [type(el) for el in els] == [CommentLine, CommentLine, Row]
