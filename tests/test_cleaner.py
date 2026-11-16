"""Cleaner tests: field edits, month macros, 'and others', dedupe."""

import pytest

from hx_bib.core import cleaner
from hx_bib.core.parser import parse_bibtex

TEXT = """@article{a,
  author = {Alice and Bob and others},
  title = {A title.},
  month = {March},
  abstract = {},
  pagetotal = {12}
}
@article{b,
  title = {B},
  year = {2020}
}
"""


def _doc():
    return parse_bibtex(TEXT)


def test_remove_fields():
    doc = _doc()
    notes = cleaner.remove_fields(doc, ["PAGETOTAL"])
    assert doc.entries[0].get("pagetotal") is None
    assert doc.entries[1].get("year") == "2020"
    assert notes == ["a: removed field 'pagetotal'"]


def test_keep_fields_allowlist():
    doc = _doc()
    cleaner.keep_fields(doc, ["title", "year"])
    assert set(doc.entries[0].fields) == {"title"}
    assert set(doc.entries[1].fields) == {"title", "year"}


def test_add_fields_without_overwrite():
    doc = _doc()
    cleaner.add_fields(doc, {"year": "1999"}, overwrite=False)
    assert doc.entries[0].get("year") == "1999"   # missing → added
    assert doc.entries[1].get("year") == "2020"   # present → untouched
    cleaner.add_fields(doc, {"year": "2001"}, overwrite=True)
    assert doc.entries[1].get("year") == "2001"


def test_add_fields_only_types():
    doc = _doc()
    cleaner.add_fields(doc, {"note": "x"}, only_types=["article"])
    assert all(e.get("note") == "x" for e in doc.entries)
    doc = _doc()
    cleaner.add_fields(doc, {"note": "x"}, only_types=["inproceedings"])
    assert all(e.get("note") is None for e in doc.entries)


def test_rename_fields():
    doc = _doc()
    notes = cleaner.rename_fields(doc, {"pagetotal": "pages"})
    assert doc.entries[0].get("pages") == "12"
    assert len(notes) == 1


def test_drop_empty_fields():
    doc = _doc()
    notes = cleaner.drop_empty_fields(doc)
    assert doc.entries[0].get("abstract") is None
    assert any("empty" in n for n in notes)


def test_drop_others():
    doc = _doc()
    notes = cleaner.drop_others(doc)
    assert doc.entries[0].get("author") == "Alice and Bob"
    assert len(notes) == 1
    # Idempotent
    assert cleaner.drop_others(doc) == []


def test_normalize_months():
    doc = _doc()
    cleaner.normalize_months(doc)
    assert doc.entries[0].get("month") == "mar"
    assert "month" in doc.entries[0].bare_fields
    # Numeric and full-name forms
    doc = parse_bibtex("@article{a, month = {12}}\n@misc{b, month = September}")
    cleaner.normalize_months(doc)
    assert doc.entries[0].get("month") == "dec"
    assert doc.entries[1].get("month") == "sep"


def test_strip_title_periods():
    doc = _doc()
    cleaner.strip_title_periods(doc)
    assert doc.entries[0].get("title") == "A title"
    # '?' and '!' are kept
    doc = parse_bibtex("@article{a, title = {Really?}}")
    cleaner.strip_title_periods(doc)
    assert doc.entries[0].get("title") == "Really?"


def test_dedupe_keep_first_and_last():
    text = "@article{a, title = {One}}\n@article{b, x = {1}}\n@article{a, title = {Two}}"

    doc = parse_bibtex(text)
    cleaner.dedupe_entries(doc, keep="first")
    assert [e.key for e in doc.entries] == ["a", "b"]
    assert doc.find("a").get("title") == "One"

    doc = parse_bibtex(text)
    cleaner.dedupe_entries(doc, keep="last")
    assert [e.key for e in doc.entries] == ["b", "a"]
    assert doc.find("a").get("title") == "Two"

    with pytest.raises(ValueError):
        cleaner.dedupe_entries(parse_bibtex(text), keep="middle")


def test_normalize_dois():
    doc = parse_bibtex(
        "@article{a, doi = {https://doi.org/10.1234/ABC}}\n"
        "@article{b, doi = {DOI: 10.5678/xyz}}\n"
        "@article{c, doi = {10.9999/ok}}"
    )
    notes = cleaner.normalize_dois(doc)
    assert doc.find("a").get("doi") == "10.1234/ABC"
    assert doc.find("b").get("doi") == "10.5678/xyz"
    assert doc.find("c").get("doi") == "10.9999/ok"
    assert len(notes) == 2
    # Idempotent
    assert cleaner.normalize_dois(doc) == []


def test_unify_page_ranges():
    doc = parse_bibtex(
        "@article{a, pages = {12-34}}\n"
        "@article{b, pages = {5–9}}\n"
        "@article{c, pages = {100--200}}"
    )
    notes = cleaner.unify_page_ranges(doc)
    assert doc.find("a").get("pages") == "12--34"
    assert doc.find("b").get("pages") == "5--9"
    assert doc.find("c").get("pages") == "100--200"
    assert len(notes) == 2
    assert cleaner.unify_page_ranges(doc) == []


def test_strip_url_tracking():
    doc = parse_bibtex(
        "@article{a, url = {https://ex.org/p?utm_source=x&utm_medium=y&id=7}}\n"
        "@article{b, url = {https://ex.org/q?fbclid=abc}}\n"
        "@article{c, url = {https://ex.org/r?page=2}}"
    )
    notes = cleaner.strip_url_tracking(doc)
    assert doc.find("a").get("url") == "https://ex.org/p?id=7"
    assert doc.find("b").get("url") == "https://ex.org/q"
    assert doc.find("c").get("url") == "https://ex.org/r?page=2"  # untouched
    assert len(notes) == 2


def test_dedupe_by_title():
    text = (
        "@article{a, title = {The Same Paper}}\n"
        "@article{b, title = {Another}}\n"
        "@article{c, title = {the same paper!}}"
    )
    doc = parse_bibtex(text)
    notes = cleaner.dedupe_by_title(doc, keep="first")
    assert [e.key for e in doc.entries] == ["a", "b"]
    assert len(notes) == 1

    doc = parse_bibtex(text)
    cleaner.dedupe_by_title(doc, keep="last")
    assert [e.key for e in doc.entries] == ["b", "c"]

    with pytest.raises(ValueError):
        cleaner.dedupe_by_title(parse_bibtex(text), keep="middle")
