"""GUI rendering helper tests: itemized list, per-entry diff, table comparison."""

from hx_bib.core.parser import parse_bibtex
from hx_bib.core.validator import Issue, ValidationReport, validate_text
from hx_bib.gui.app import (
    _comparison_code,
    _entry_comparisons,
    _normalize_venue_preview,
    changes_html,
    check_html,
    entry_diff_html,
    table_compare_html,
    table_result_html,
)
from hx_bib.gui.diagnostics import issue_text

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


def test_changes_html_changed_mode_filters_unchanged():
    changed = changes_html(BEFORE, AFTER, "changed", "en")
    everything = changes_html(BEFORE, AFTER, "all", "en")
    assert ">a<" in changed and ">b<" not in changed
    assert ">a<" in everything and ">b<" in everything


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
    pairs = _entry_comparisons(before, f"{retained}\n{other}")
    assert [(old, new) for _, old, new in pairs] == [
        (removed, None), (retained, retained), (other, other),
    ]


def test_venue_preview_only_normalizes_recognized_entries():
    before = (
        "@article{known, journal={TBD}}\n"
        "@article{unknown, booktitle={Independent Workshop}}"
    )
    known, unknown = parse_bibtex(_normalize_venue_preview(before, "full")).entries
    assert known.get("journal") == "IEEE Transactions on Big Data"
    assert unknown.type == "article"
    assert unknown.get("booktitle") == "Independent Workshop"
    assert _normalize_venue_preview(before, "abbr") == before


def test_changes_html_badges():
    html = changes_html(BEFORE, AFTER, "changed", "en")
    assert "hx-field-mod" in html       # modified badge
    assert "~title" in html             # sign + field name


def test_changes_html_no_changes():
    html = changes_html(BEFORE, BEFORE, "changed", "en")
    assert "No changes" in html


def test_entry_diff_groups_per_entry():
    html = entry_diff_html(BEFORE, AFTER, "all", "en")
    assert 'class="hx-ediff hx-ediff-modified"' in html
    assert 'class="hx-ediff hx-ediff-unchanged"' in html
    assert html.count('class="hx-ediff-key"') == 2   # a and b, one block each
    assert 'class="replace"' in html
    assert 'class="old' in html and 'class="new' in html


def test_entry_diff_changed_mode_only_changed_entries():
    changed = entry_diff_html(BEFORE, AFTER, "changed", "en")
    assert "hx-ediff-modified" in changed
    assert "hx-ediff-unchanged" not in changed
    assert '>b</span>' not in changed


def test_entry_diff_added_and_removed_entries():
    added = entry_diff_html(
        "@article{a, title={A}}",
        "@article{a, title={A}}\n@article{b, title={B}}",
        "changed", "en",
    )
    assert 'class="hx-ediff hx-ediff-added"' in added
    assert 'class="insert"' in added
    removed = entry_diff_html(
        "@article{a, title={A}}\n@article{b, title={B}}",
        "@article{a, title={A}}",
        "changed", "en",
    )
    assert 'class="hx-ediff hx-ediff-removed"' in removed
    assert 'class="delete"' in removed


def test_entry_diff_escapes_markup():
    html = entry_diff_html(
        "@article{a, title = {<b>bold</b>}}",
        "@article{a, title = {plain}}",
        "all", "en",
    )
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


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


def test_entry_diff_dedupe_keeps_last_occurrence():
    before = "@article{same,title={First}}\n@article{same,title={Last}}"
    after = "@article{same,title={Last}}"
    html = entry_diff_html(before, after, "changed", "en")
    assert html.count('class="hx-ediff hx-ediff-removed"') == 1
    assert "First" in html and "Last" not in html
    assert "hx-ediff-modified" not in html


def test_entry_diff_duplicate_addition_is_visible():
    html = entry_diff_html("@article{same,title={A}}",
                           "@article{same,title={A}}\n@article{same,title={B}}", "changed", "en")
    assert html.count('class="hx-ediff hx-ediff-added"') == 1
    assert "title={B}" in html


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
