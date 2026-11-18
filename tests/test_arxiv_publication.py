"""Offline checks for publication evidence and ambiguous author comments."""

import json
import xml.etree.ElementTree as ET

import pytest

from hx_bib.core.parser import parse_bibtex
from hx_bib.verify import checker
from hx_bib.verify.checker import _arxiv_publication
from hx_bib.verify.sources import (
    SourceRecord,
    _arxiv_entry_to_record,
    arxiv_batch,
    arxiv_lookup,
    crossref_publication,
    openreview_publication,
)


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


TITLE = "ExampleBench: A Reliable Benchmark"
REFERENCE = SourceRecord("arXiv", True, title=TITLE, authors=["team", "doe", "singh"], venue="arXiv")


class StubNet:
    def __init__(self, body, status=200):
        self.body = json.dumps(body)
        self.status = status
        self.requests = []

    def get(self, url, **_kwargs):
        self.requests.append(url)
        return self.status, self.body


def test_arxiv_atom_reads_namespaced_journal_and_comment():
    entry = ET.fromstring(
        '<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        '<title>Example paper</title><arxiv:journal_ref>Example Journal 2025</arxiv:journal_ref>'
        '<arxiv:comment>Accepted at Example Conference</arxiv:comment></entry>'
    )
    record = _arxiv_entry_to_record(entry)
    assert record and record.venue == "Example Journal 2025"
    assert "Accepted at Example Conference" in record.note


def test_arxiv_empty_prefetch_does_not_hide_abs_page_error(monkeypatch):
    net = StubNet({})
    net.arxiv_prefetch = {"2502.14739": None}
    from hx_bib.verify import sources

    monkeypatch.setattr(sources, "arxiv_abs_lookup", lambda *_: SourceRecord("arXiv", False, error=True))
    assert arxiv_lookup(net, "2502.14739").error


def test_failed_batch_marks_each_id_for_direct_fallback():
    net = StubNet({}, status=503)
    assert arxiv_batch(net, ["2502.14739", "2502.14740"]) == {"2502.14739": None, "2502.14740": None}
    assert len(net.requests) == 1


def test_openreview_search_uses_a_public_venue_decision_not_a_submission():
    accepted = {
        "id": "paper", "forum": "paper", "domain": "Example/2025/Conference", "pdate": 1758215815218,
        "content": {
            "title": {"value": TITLE}, "authors": {"value": [
                {"fullname": "Jane Doe", "username": "~Jane_Doe1"}, "Alex Singh", {"username": "~Unknown1"},
            ]},
            "venueid": {"value": "Example/2025/Conference"}, "venue": {"value": "Example 2025 poster"},
        },
    }
    submission = {
        **accepted, "id": "submission", "forum": "submission", "pdate": None,
        "content": {**accepted["content"], "venueid": {"value": "Example/2025/Conference/Submission"}},
    }
    reply = {**accepted, "id": "reply"}
    net = StubNet({"notes": [submission, reply, accepted]})
    record = openreview_publication(net, REFERENCE)
    assert record.raw_authors == ["Jane Doe", "Alex Singh"]
    assert record.url == "https://openreview.net/forum?id=paper"
    assert _arxiv_publication(REFERENCE, record) == ("accepted", "OpenReview · Example 2025 poster")
    assert not openreview_publication(StubNet({"notes": [submission, reply]}), REFERENCE).found


def test_crossref_search_skips_preprints_and_mismatched_authors():
    def work(kind, author, doi):
        return {
            "title": [TITLE], "type": kind, "author": [{"family": author}],
            "DOI": doi, "container-title": ["Example Conference"], "URL": f"https://doi.org/{doi}",
        }

    net = StubNet({"message": {"items": [
        work("posted-content", "Doe", "10.1000/preprint"),
        work("journal-article", "Nobody", "10.1000/wrong"),
        work("proceedings-article", "Singh", "10.1000/right"),
    ]}})
    record = crossref_publication(net, REFERENCE)
    assert record.doi == "10.1000/right"
    assert _arxiv_publication(REFERENCE, record)[0] == "record"


def test_arxiv_id_search_survives_abbreviated_bib_title_and_provider_error(monkeypatch):
    entry = parse_bibtex("@misc{paper, title={ExampleBench}, eprint={2502.14739}, archivePrefix={arXiv}}").entries[0]
    record = SourceRecord(
        "Crossref", True, title=TITLE, authors=["doe"], venue="Example Journal",
        publication_type="journal-article", url="https://doi.org/10.1000/right",
    )
    monkeypatch.setattr(checker, "arxiv_lookup", lambda *_: REFERENCE)
    monkeypatch.setattr(checker, "openreview_publication", lambda *_: SourceRecord("OpenReview", False, error=True))
    monkeypatch.setattr(checker, "crossref_publication", lambda *_: record)
    result = checker.verify_entry(StubNet({}), entry)
    assert result.publication == ("record", "Crossref · Example Journal")
    assert result.publication_url == record.url


def test_arxiv_check_retries_only_incomplete_publication_evidence(monkeypatch):
    entry = parse_bibtex("@misc{paper, eprint={2502.14739}}").entries[0]
    missing = SourceRecord("arXiv", False, note="abs page 404")
    monkeypatch.setattr(checker, "arxiv_lookup", lambda *_: missing)
    result = checker.verify_arxiv_entry(StubNet({}), entry)
    assert result.publication == ("missing", "") and not result.any_error

    monkeypatch.setattr(checker, "arxiv_lookup", lambda *_: REFERENCE)
    timeout = SourceRecord("OpenReview", False, error=True, note="HTTP None")
    monkeypatch.setattr(checker, "openreview_publication", lambda *_: timeout)
    monkeypatch.setattr(checker, "crossref_publication", lambda *_: SourceRecord("Crossref", False))
    monkeypatch.setattr(checker, "dblp_publication", lambda *_: SourceRecord("DBLP", False))
    result = checker.verify_arxiv_entry(StubNet({}), entry)
    assert result.publication == ("unavailable", "OpenReview") and result.any_error

    record = SourceRecord("DBLP", True, title=TITLE, authors=["doe"], venue="Example Conference",
                          publication_type="Conference and Workshop Papers")
    monkeypatch.setattr(checker, "dblp_publication", lambda *_: record)
    result = checker.verify_arxiv_entry(StubNet({}), entry)
    assert result.publication[0] == "record" and not result.any_error
