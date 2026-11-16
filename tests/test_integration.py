"""End-to-end tests on the bundled sample (examples/custom.bib)."""

from pathlib import Path

from hx_bib.core import cleaner
from hx_bib.core.diff import diff_texts
from hx_bib.core.normalize import NormalizeOptions, normalize_document
from hx_bib.core.parser import parse_bibtex
from hx_bib.core.validator import validate_text
from hx_bib.core.writer import FormatOptions, format_text, render_document

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "custom.bib"


def _text():
    return SAMPLE.read_text(encoding="utf-8")


def test_sample_parses_cleanly():
    doc = parse_bibtex(_text())
    assert len(doc.entries) >= 40
    assert doc.warnings == []
    assert not doc.duplicate_keys()


def test_sample_formats_idempotently():
    once = format_text(_text())
    assert once == format_text(once)
    assert len(parse_bibtex(once).entries) == len(parse_bibtex(_text()).entries)


def test_sample_validates_without_errors():
    report = validate_text(_text())
    assert report.errors == []


def test_sample_cleanup_pipeline():
    doc = parse_bibtex(_text())
    notes = cleaner.drop_others(doc)
    cleaner.strip_title_periods(doc)
    cleaner.normalize_months(doc)
    assert isinstance(notes, list)
    for entry in doc.entries:
        assert "and others" not in (entry.get("author") or "").lower()
        title = entry.get("title") or ""
        assert not title.endswith(".")


def test_sample_normalize_pipeline():
    before = _text()
    doc = parse_bibtex(before)
    normalize_document(doc, NormalizeOptions(venue_style="family"))
    rendered = render_document(doc, FormatOptions())

    # The family rule: ACL-series → ARR, ACM-series → ACM.
    assert "ARR" in rendered
    assert "ACM" in rendered
    # The camelCase bug ("Proceedings ofKDD") is fixed by the presets.
    assert "Proceedings ofKDD" not in rendered
    # arXiv pseudo-volumes are gone (the legit https URL stays).
    assert "volume = {abs/" not in rendered


def test_diff_pipeline_reports_changes():
    before = _text()
    doc = parse_bibtex(before)
    cleaner.drop_others(doc)
    after = render_document(doc, FormatOptions())
    diff = diff_texts(before, after)
    # Cleaning the raw sample changes at least some entries.
    assert diff.changed
    for entry_diff in diff.changed:
        assert entry_diff.kind in ("added", "removed", "modified")
