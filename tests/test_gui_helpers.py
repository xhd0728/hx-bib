"""GUI rendering helpers and comparisons for BibTeX and LaTeX."""

from hx_bib.core.parser import parse_bibtex
from hx_bib.core.validator import Issue, ValidationReport, validate_text
from hx_bib.gui.app import (
    _comparison_code,
    _diff_rows_html,
    check_html,
    table_compare_html,
    table_result_html,
)
from hx_bib.gui.bib_workbench import apply_options, compare_entries, comparison_rows
from hx_bib.gui.diagnostics import issue_text
from hx_bib.gui.workspace import DEFAULT_SETTINGS, merge_settings

TABLE_BEFORE = (
    "\\begin{tabular}{cc}\n"
    "a & b \\\\ c & d \\\\\n"
    "\\end{tabular}\n"
)
TABLE_AFTER = (
    "\\begin{tabular}{cc}\n"
    "a & b \\\\\n"
    "c & d \\\\\n"
    "\\end{tabular}\n"
)

BEFORE = """@article{a,
  title = {Old title.},
  year = {2020}
}
@article{b,
  title = {Stable}
}
"""

AFTER = """@article{a,
  title = {New title.},
  year = {2020}
}
@article{b,
  title = {Stable}
}
"""


def test_comparison_highlights_changes_and_escapes_literal_code():
    before, after = _comparison_code("unchanged\n<old>", "unchanged\n<new>")
    assert 'hx-code-removed" data-line="2">&lt;old&gt;' in before
    assert 'hx-code-added" data-line="2">&lt;new&gt;' in after
    assert 'hx-code-line" data-line="1">unchanged' in before


def test_entry_copies_preserve_inline_blocks_and_duplicate_occurrences():
    retained = '@article{same,title="Last (one)"}'
    removed = "@article{same,title={First}}"
    other = "@misc{other,title={Another}}"
    before = f"{removed} {retained}\r\n{other}"
    pairs = compare_entries(before, f"{retained}\n{other}")
    assert [(pair.left, pair.right) for pair in pairs] == [
        (removed, None), (retained, retained), (other, other),
    ]


def test_venue_preset_only_changes_recognized_entries():
    unchanged = "@article{unknown, booktitle={Independent Workshop}}"
    before = "@article{known, journal={TBD}}\n" + unchanged
    settings = merge_settings(DEFAULT_SETTINGS, {
        "enabled": {"format": False, "venues": True},
        "venues": {"preset_style": "full"},
    })
    after, _ = apply_options(before, settings, {})
    known, unknown = parse_bibtex(after).entries
    assert known.get("journal") == "IEEE Transactions on Big Data"
    assert unknown.type == "article" and after.endswith(unchanged)
    settings["venues"]["preset_style"] = "abbr"
    assert apply_options(before, settings, {})[0] == before


def test_diff_compares_entries_and_counts_formatting_only_changes():
    comparisons = compare_entries(BEFORE, AFTER)
    assert [(pair.after.key, pair.kind) for pair in comparisons] == [
        ("a", "modified"), ("b", "unchanged"),
    ]
    assert [pair.kind for pair in compare_entries(BEFORE, BEFORE)] == ["unchanged", "unchanged"]
    formatting = BEFORE.replace("  title = {Old", "    title = {Old")
    change = compare_entries(BEFORE, formatting)[0]
    assert change.kind == "modified"
    assert "replace" in [row.kind for row in comparison_rows(change, full=False)]


def test_diff_tracks_order_without_marking_a_survivor_moved_by_deletion():
    first, second = "@article{a,title={A}}", "@article{b,title={B}}"
    before = first + "\n" + second
    assert all(pair.moved and pair.kind == "modified" for pair in compare_entries(before, second + "\n" + first))
    assert [pair.kind for pair in compare_entries(before, second)] == ["removed", "unchanged"]


def test_diff_added_removed_and_escaped_code():
    before, after = "@article{a,title={<script>}}", "@article{b,title={<b>}}"
    removed, added = compare_entries(before, after)
    assert removed.kind == "removed" and added.kind == "added"
    html = _diff_rows_html(comparison_rows(removed, full=False) + comparison_rows(added, full=False))
    assert 'class="delete"' in html and 'class="insert"' in html
    assert "&lt;script&gt;" in html and "&lt;b&gt;" in html


def test_diff_only_trims_unchanged_lines_but_full_keeps_them():
    before = "@article{k,\n  title={A},\n  author={A},\n  journal={J},\n  year={2020},\n  pages={3--8}\n}"
    after = before.replace("2020", "2021")
    change = compare_entries(before, after)[0]
    compact = comparison_rows(change, full=False)
    full = comparison_rows(change, full=True)
    assert any(row.kind == "replace" and row.right_no == 5 for row in compact)
    assert not any(row.left and "title" in row.left for row in compact)
    assert len(full) == 7 and full[1].left_no == 2


def test_table_compare_per_table_blocks():
    html = table_compare_html(TABLE_BEFORE, TABLE_AFTER, "en")
    assert html.count("<details") == 1
    assert "open" in html.split("<summary")[0]      # modified table opens by default
    assert "#1 · tabular{cc}" in html
    assert "Before" in html and "After" in html
    assert "Layout only" in html                    # split rows don't change content


def test_table_compare_unchanged_collapses():
    html = table_compare_html(TABLE_BEFORE, TABLE_BEFORE, "en")
    assert html.startswith('<details class="hx-tblock hx-tcompare">')
    assert html.count("<pre") == 2                   # available on expansion
    assert "hx-kind-unchanged" in html


def test_table_compare_content_change_flagged():
    before = "\\begin{tabular}{cc}\na & b \\\\ % note\n\\end{tabular}\n"
    after = "\\begin{tabular}{cc}\na & b \\\\\n\\end{tabular}\n"
    html = table_compare_html(before, after, "en")
    assert "Content changed" in html
    assert "Layout only" not in html


def test_table_compare_two_tables_numbered():
    before = TABLE_BEFORE + "text\n" + TABLE_BEFORE
    after = TABLE_AFTER + "text\n" + TABLE_AFTER
    html = table_compare_html(before, after, "zh")
    assert "#1 · tabular{cc}" in html and "#2 · tabular{cc}" in html
    assert "修改前" in html and "格式化后" in html


def test_table_compare_escapes_markup():
    before = "\\begin{tabular}{c}\n<b>x</b> \\\\\n\\end{tabular}\n"
    html = table_compare_html(before, before, "en")
    assert "<b>x</b>" not in html


def test_table_result_html_shows_output():
    html = table_result_html("a & b \\\\", "en")
    assert "Formatted result" in html
    assert "<pre" in html and "a &amp; b" in html


def test_check_html_uses_chinese_messages_and_actionable_hints():
    report = validate_text("@article{x,title={T},year={2020}}")
    html = check_html(report, "zh")
    assert "缺少必需字段「author」" in html
    assert "对照文献的正式出版信息补全字段" in html
    assert "缺少必需字段「journal」" in html


def test_check_html_filters_and_escapes_diagnostics():
    report = ValidationReport([Issue("error", "<script>", "<unsafe>", "author", 3),
                               Issue("info", "x", "optional", code="key_case")])
    html = check_html(report, "zh", "error")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&lt;unsafe&gt;" in html and "optional" not in html


def test_unknown_venue_hint_explains_that_missing_presets_are_not_errors():
    report = validate_text("@article{x,author={A},title={T},journal={Unlisted Journal},year={2020}}")
    issue = next(i for i in report.issues if i.code == "unknown_venue")
    message, hint = issue_text(issue, "zh")
    assert "不代表引用有误" in message and "确认名称正确即可保留" in hint
    assert issue_text(issue, "en")[0] == issue.message


def test_validation_diagnostics_have_stable_codes():
    report = validate_text("@article{x,year={bad},journal={TODO},url={example.com},title={T.}}")
    assert report.issues and all(issue.code for issue in report.issues)
