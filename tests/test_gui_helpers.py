"""GUI rendering helper tests: itemized list, red/green diff, mode toggle."""

from hx_bib.gui.app import changes_html, diff_html

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


def test_diff_html_red_green_rows():
    html = diff_html(BEFORE, AFTER, "all", "en")
    assert 'class="hx-diff"' in html
    assert 'class="replace"' in html    # changed line pair
    assert 'class="old"' in html and 'class="new"' in html


def test_diff_html_changed_mode_drops_equal_rows():
    everything = diff_html(BEFORE, AFTER, "all", "en")
    changed = diff_html(BEFORE, AFTER, "changed", "en")
    assert changed.count("<tr") < everything.count("<tr")
    assert 'class="equal"' not in changed


def test_diff_html_escapes_markup():
    html = diff_html("@article{a, title = {<b>bold</b>}}", "@article{a, title = {plain}}", "all", "en")
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
