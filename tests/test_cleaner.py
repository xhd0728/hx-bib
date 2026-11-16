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
