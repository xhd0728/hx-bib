"""Writer tests: indentation, field order, sorting, idempotency."""

import pytest

from hx_bib.core.parser import parse_bibtex
from hx_bib.core.writer import FormatOptions, format_text, render_entry

SRC = """@article{key,
  title = {A  very
    spaced   title},
  year = {2020},
  author = {Alice and Bob}
}
"""


def _entry(text=SRC):
    return parse_bibtex(text).entries[0]


def test_default_render_shape():
    out = render_entry(_entry(), FormatOptions())
    lines = out.splitlines()
    assert lines[0] == "@article{key,"
    # Standard field order puts 'author' first; spacing is collapsed.
    assert lines[1] == "    author = {Alice and Bob},"
    assert any(line.strip() == "title = {A very spaced title}," for line in lines)
    assert lines[-1] == "}"


def test_indent_variants():
    assert render_entry(_entry(), FormatOptions(indent="2")).splitlines()[1].startswith("  author")
    assert render_entry(_entry(), FormatOptions(indent="tab")).splitlines()[1].startswith("\tauthor")
    assert render_entry(_entry(), FormatOptions(indent="0")).splitlines()[1].startswith("author")
    aligned = render_entry(_entry(), FormatOptions(indent="align")).splitlines()[1]
    # "@article{" is 9 chars → fields align under the opening brace.
    assert aligned.startswith(" " * 9 + "author")


def test_align_equals():
    out = render_entry(_entry(), FormatOptions(align_equals=True))
    eqs = {line.index("=") for line in out.splitlines()[1:-1]}
    assert len(eqs) == 1


def test_field_order_standard_alpha_keep():
    text = "@article{a, year = {2020}, title = {T}, author = {A}}"
    entry = parse_bibtex(text).entries[0]

    std = render_entry(entry, FormatOptions(field_order="standard")).splitlines()
    names = [line.strip().split(" =")[0] for line in std[1:-1]]
    assert names.index("author") < names.index("title") < names.index("year")

    alpha = render_entry(entry, FormatOptions(field_order="alphabetical")).splitlines()
    names = [line.strip().split(" =")[0] for line in alpha[1:-1]]
    assert names == ["author", "title", "year"]

    keep = render_entry(entry, FormatOptions(field_order="keep")).splitlines()
    names = [line.strip().split(" =")[0] for line in keep[1:-1]]
    assert names == ["year", "title", "author"]


def test_trailing_comma():
    out = render_entry(_entry(), FormatOptions(trailing_comma=True))
    assert out.splitlines()[-2].endswith(",")


def test_sort_entries_by_key_and_year():
    text = "@article{b, year = {2019}}\n@article{a, year = {2021}}\n@article{c, year = {2020}}"
    by_key = format_text(text, FormatOptions(sort_entries="key"))
    assert by_key.index("@article{a,") < by_key.index("@article{b,") < by_key.index("@article{c,")
    by_year = format_text(text, FormatOptions(sort_entries="year"))
    assert by_year.index("@article{b,") < by_year.index("@article{c,") < by_year.index("@article{a,")


def test_bare_fields_render_unbraced():
    out = render_entry(_entry("@article{a, month = jan}"), FormatOptions())
    assert "month = jan" in out


def test_format_is_idempotent():
    once = format_text(SRC)
    twice = format_text(once)
    assert once == twice


def test_drop_comments():
    text = "@comment{note}\n@article{a, title = {T}}"
    assert "@comment" in format_text(text)
    assert "@comment" not in format_text(text, FormatOptions(drop_comments=True))


def test_invalid_option_rejected():
    with pytest.raises(ValueError):
        format_text(SRC, FormatOptions(indent="five"))
    with pytest.raises(ValueError):
        format_text(SRC, FormatOptions(field_order="random"))
