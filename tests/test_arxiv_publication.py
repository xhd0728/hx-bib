"""Offline checks for publication evidence and ambiguous author comments."""

import pytest

from hx_bib.verify.checker import _arxiv_publication
from hx_bib.verify.sources import SourceRecord


@pytest.mark.parametrize(("note", "status"), [
    ("comment: Accepted at Example Conference", "comment"),
    ("comment: Not yet accepted at Example Conference", "not_found"),
    ("comment: Submitted to Example Conference", "not_found"),
    ("comment: To be revised if accepted at Example Conference", "not_found"),
    ("comment: Code published in the repository", "not_found"),
])
def test_arxiv_comments_are_leads_only(note, status):
    assert _arxiv_publication(SourceRecord("arXiv", True, venue="arXiv", note=note), None)[0] == status


def test_arxiv_reference_and_dblp_match_are_distinct():
    arxiv = SourceRecord("arXiv", True, title="The Same Scientific Title", authors=["Example"], venue="arXiv")
    dblp = SourceRecord(
        "DBLP", True, title=arxiv.title, authors=["Example"], venue="CVPR", year="2025",
        publication_type="Conference and Workshop Papers",
    )
    assert _arxiv_publication(arxiv, dblp) == ("record", "DBLP · CVPR 2025")
    dblp.authors = ["Someone Else"]
    assert _arxiv_publication(arxiv, dblp) == ("not_found", "")
    arxiv.venue = "Example journal, volume 4"
    assert _arxiv_publication(arxiv, None) == ("reference", arxiv.venue)


def test_preprint_only_and_network_error_are_not_proof_of_publication():
    arxiv = SourceRecord("arXiv", True, title="A Scientific Title", authors=["Example"])
    dblp = SourceRecord(
        "DBLP", True, title=arxiv.title, authors=arxiv.authors, venue="CoRR",
        publication_type="Informal and Other Publications",
    )
    assert _arxiv_publication(arxiv, dblp)[0] == "not_found"
    assert _arxiv_publication(arxiv, SourceRecord("DBLP", False, error=True))[0] == "unavailable"
