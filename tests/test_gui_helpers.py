"""GUI rendering helper tests: itemized list, per-entry diff, table comparison."""

from hx_bib.gui.app import (
    changes_html,
    entry_diff_html,
    table_compare_html,
    table_result_html,
)

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
    assert "<details" in html
    assert "<pre" not in html                       # header-only card, closed
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
