"""Normalization tests: arXiv preset, venue rewriting, entry-type fixes."""

from hx_bib.core.normalize import (
    NormalizeOptions,
    entry_venue_presets,
    normalize_document,
)
from hx_bib.core.parser import parse_bibtex


def _normalize(text, **kw):
    doc = parse_bibtex(text)
    notes = normalize_document(doc, NormalizeOptions(**kw))
    return doc, notes


def test_tbd_journal_renders_in_full_and_abbreviated_styles():
    doc, _ = _normalize("@article{x, journal={TBD}}", venue_style="full")
    assert doc.entries[0].get("journal") == "IEEE Transactions on Big Data"
    doc, _ = _normalize("@article{x, journal={IEEE Transactions on Big Data}}", venue_style="abbr")
    assert doc.entries[0].get("journal") == "TBD"


def test_ambiguous_tbd_conference_placeholder_is_not_rewritten_as_a_journal():
    doc, _ = _normalize("@inproceedings{x, booktitle={TBD}}", venue_style="full")
    assert doc.entries[0].type == "inproceedings"
    assert doc.entries[0].get("booktitle") == "TBD"
    assert not doc.entries[0].has("journal")


def test_entry_venue_presets_excludes_placeholders_and_arxiv():
    doc = parse_bibtex(
        "@inproceedings{placeholder, booktitle={TBD}}\n"
        "@article{journal, journal={TBD}}\n"
        "@article{preprint, booktitle={CVPR}, journal={arXiv preprint arXiv:2411.13154}}"
    )
    assert [entry_venue_presets(entry) for entry in doc.entries] == [set(), {"TBD"}, set()]


def test_venue_normalization_preserves_conflicting_destination_field():
    doc, notes = _normalize(
        "@article{conflict, journal={ICLR}, booktitle={Independent Workshop}}"
    )
    assert doc.entries[0].get("journal") == "ICLR"
    assert doc.entries[0].get("booktitle") == "Independent Workshop"
    assert notes == []


def test_arxiv_journal_style():
    doc, notes = _normalize(
        "@inproceedings{p, title = {T}, booktitle = {ICML}, "
        "journal = {arXiv preprint arXiv:2411.13154}, volume = {abs/2411.13154}, year = {2024}}"
    )
    e = doc.entries[0]
    assert e.type == "article"
    assert e.get("booktitle") is None
    assert e.get("journal") == "arXiv preprint arXiv:2411.13154"
    assert e.get("volume") is None                      # pseudo-volume dropped
    assert e.get("url") == "https://arxiv.org/abs/2411.13154"
    assert e.get("eprint") == "2411.13154"
    assert any("pseudo-volume" in n for n in notes)


def test_arxiv_misc_style():
    doc, _ = _normalize(
        "@article{p, title = {T}, journal = {arXiv preprint arXiv:2001.00001}, year = {2020}}",
        arxiv_style="misc",
    )
    e = doc.entries[0]
    assert e.type == "misc"
    assert e.get("journal") is None
    assert e.get("eprint") == "2001.00001"
    assert e.get("archiveprefix") == "arXiv"
    assert e.get("howpublished") == "arXiv:2001.00001"


def test_venue_abbr_rewrite():
    doc, notes = _normalize(
        "@inproceedings{k, title = {T}, booktitle = {Proceedings ofKDD 2024}, year = {2024}}",
        venue_style="abbr",
    )
    assert doc.entries[0].get("booktitle") == "Proceedings of KDD"
    assert notes


def test_venue_family_rewrite():
    doc, _ = _normalize(
        "@inproceedings{a, title = {T}, booktitle = {EMNLP}, year = {2023}}\n"
        "@inproceedings{k, title = {T}, booktitle = {KDD}, year = {2024}}",
        venue_style="family",
    )
    assert doc.entries[0].get("booktitle") == "ARR"
    assert doc.entries[1].get("booktitle") == "ACM"


def test_journal_in_booktitle_moves_to_journal():
    doc, _ = _normalize(
        "@inproceedings{x, title = {T}, booktitle = "
        "{Transactions of the Association for Computational Linguistics}, year = {2021}}"
    )
    e = doc.entries[0]
    assert e.type == "article"
    assert e.get("booktitle") is None
    assert e.get("journal") == "TACL"


def test_conference_in_journal_moves_to_booktitle():
    doc, _ = _normalize(
        "@article{x, title = {T}, journal = {ICLR}, year = {2022}}"
    )
    e = doc.entries[0]
    assert e.type == "inproceedings"
    assert e.get("journal") is None
    assert e.get("booktitle") == "Proceedings of ICLR"


def test_venue_style_keep():
    doc, _ = _normalize(
        "@inproceedings{k, title = {T}, booktitle = {Proceedings ofKDD 2024}}",
        venue_style="keep",
    )
    assert doc.entries[0].get("booktitle") == "Proceedings ofKDD 2024"


def test_unknown_venue_left_untouched():
    doc, notes = _normalize(
        "@inproceedings{x, title = {T}, booktitle = {Journal of Nothing}}"  # noqa: E501
    )
    assert doc.entries[0].get("booktitle") == "Journal of Nothing"


def test_invalid_options_rejected():
    import pytest

    with pytest.raises(ValueError):
        normalize_document(parse_bibtex(""), NormalizeOptions(venue_style="weird"))
    with pytest.raises(ValueError):
        normalize_document(parse_bibtex(""), NormalizeOptions(arxiv_style="weird"))
