"""Identifier extraction tests (arXiv / DOI / OpenReview)."""

from hx_bib.core.identifiers import (
    extract_arxiv_id,
    extract_doi,
    extract_openreview_id,
    is_arxiv_like,
)
from hx_bib.core.parser import parse_bibtex


def _entry(text):
    return parse_bibtex(text).entries[0]


def test_arxiv_from_eprint():
    e = _entry("@article{a, eprint = {2411.13154v2}}")
    assert extract_arxiv_id(e) == "2411.13154"


def test_arxiv_from_journal_and_url():
    e = _entry("@article{a, journal = {arXiv preprint arXiv:2105.00001}}")
    assert extract_arxiv_id(e) == "2105.00001"
    e = _entry("@misc{a, url = {https://arxiv.org/abs/2301.00002}}")
    assert extract_arxiv_id(e) == "2301.00002"


def test_arxiv_from_doi():
    e = _entry("@article{a, doi = {10.48550/arXiv.2401.00003}}")
    assert extract_arxiv_id(e) == "2401.00003"


def test_arxiv_old_style_id():
    e = _entry("@article{a, journal = {arXiv preprint cs/0501001}}")
    assert extract_arxiv_id(e) == "cs/0501001"


def test_no_false_arxiv():
    assert extract_arxiv_id(_entry("@article{a, year = {2020}}")) is None
    # A year-like number must not be mistaken for a modern id.
    assert extract_arxiv_id(_entry("@article{a, title = {2020 vision}}")) is None


def test_doi_extraction_excludes_arxiv_doi():
    e = _entry("@article{a, doi = {10.1145/3368089.3409697}}")
    assert extract_doi(e) == "10.1145/3368089.3409697"
    e = _entry("@article{a, doi = {10.48550/arXiv.2401.00003}}")
    assert extract_doi(e) is None


def test_openreview_id():
    e = _entry("@inproceedings{a, note = {Accepted at ICLR; see https://openreview.net/forum?id=AbCd123}}")
    assert extract_openreview_id(e) == "AbCd123"


def test_is_arxiv_like():
    assert is_arxiv_like(_entry("@article{a, journal = {arXiv preprint arXiv:2411.13154}}"))
    assert not is_arxiv_like(_entry("@article{a, journal = {Journal of Things}}"))
