"""Regenerate deterministic HTTP/checker snapshots from the Python implementation."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hx_bib.core.parser import parse_bibtex
from hx_bib.verify import checker, report


def response(status=200, body="", error=None):
    return {"status": status, "body": body if isinstance(body, str) else json.dumps(body), "error": error}


def atom(comment="", journal=""):
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            '<entry><id>https://arxiv.org/abs/2401.12345</id><title>Example Paper</title>'
            '<author><name>Jane Doe</name></author><published>2024-01-02</published>'
            f'<arxiv:comment>{comment}</arxiv:comment><arxiv:journal_ref>{journal}</arxiv:journal_ref>'
            '</entry></feed>')


class OfflineNet:
    arxiv_prefetch = None
    last_error = None

    def __init__(self, responses):
        self.responses = deepcopy(responses)

    def get(self, url, **_):
        prefix = max((key for key in self.responses if url.startswith(key)), key=len)
        sequence = self.responses[prefix]
        result = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        self.last_error = result["error"]
        return result["status"], result["body"]

    def save(self):
        pass


def fixtures():
    base = {
        "https://export.arxiv.org/api/query": [response(body=atom())],
        "https://arxiv.org/abs/": [response(404)],
        "https://api2.openreview.net/notes": [response(body={"notes": []})],
        "https://api.crossref.org/works": [response(body={"message": {"items": []}})],
        "https://dblp.org/search/publ/api": [response(body={"result": {"hits": {"@total": "0"}}})],
        "https://doi.org/": [response(404)],
        "https://example.com": [response()],
    }
    entry = "@article{example,title={Example Paper},author={Doe, Jane},year={2024},eprint={2401.12345}}"
    note = {"id": "accepted", "forum": "accepted", "domain": "Example/2025/Conference", "pdate": 1740000000000,
            "content": {"title": {"value": "Example Paper"}, "authors": {"value": [{"fullname": "Jane Doe"}]},
                        "venue": {"value": "Example 2025 poster"}, "venueid": {"value": "Example/2025/Conference"}}}
    crossref = {"title": ["Example Paper"], "author": [{"family": "Doe", "given": "Jane"}],
                "issued": {"date-parts": [[2025]]}, "container-title": ["Example Journal"],
                "type": "journal-article", "DOI": "10.1234/example", "URL": "https://doi.org/10.1234/example"}
    scenarios = []

    def add(name, overrides=None, text=entry, retries=0):
        responses = deepcopy(base)
        responses.update({key: value if isinstance(value, list) else [value] for key, value in (overrides or {}).items()})
        scenarios.append((name, text, responses, retries))

    add("no-evidence")
    for comment in ("Accepted at Example Conference 2025", "Not yet accepted at Example Conference",
                    "Submitted to Example Conference", "Published in Example Journal", "Code published in a repository"):
        add(comment, {"https://export.arxiv.org/api/query": response(body=atom(comment))})
    add("journal-reference", {"https://export.arxiv.org/api/query": response(body=atom(journal="Example Journal 2025"))})
    add("public-acceptance", {"https://api2.openreview.net/notes": response(body={"notes": [note]})})
    submission = deepcopy(note)
    submission["content"]["venueid"]["value"] += "/Submission"
    add("submission-is-not-acceptance", {"https://api2.openreview.net/notes": response(body={"notes": [submission]})})
    add("published-crossref", {"https://api.crossref.org/works": response(body={"message": {"items": [crossref]}})})
    preprint = dict(crossref, type="posted-content")
    add("crossref-preprint", {"https://api.crossref.org/works": response(body={"message": {"items": [preprint]}})})
    mismatch = dict(crossref, author=[{"family": "Other"}])
    add("same-title-other-author", {"https://api.crossref.org/works": response(body={"message": {"items": [mismatch]}})})
    dblp = {"result": {"hits": {"@total": "2", "hit": [
        {"info": {"title": "Example Paper", "venue": "CoRR", "year": "2024", "authors": {"author": "Jane Doe"}}},
        {"info": {"title": "Example Paper", "venue": "Example Journal", "type": "Journal Articles", "doi": "10.1234/example", "ee": "https://doi.org/10.1234/example", "authors": {"author": {"text": "Jane Doe"}}}},
    ]}}}
    add("dblp-prefers-publication", {"https://dblp.org/search/publ/api": response(body=dblp)})
    for status in (404, 410, 429, 503, None):
        add(f"arxiv-http-{status}", {"https://export.arxiv.org/api/query": response(status),
                                    "https://arxiv.org/abs/": response(status)}, retries=2)
    add("malformed-services", {"https://api2.openreview.net/notes": response(body="bad json"),
                               "https://api.crossref.org/works": response(body="bad json"),
                               "https://dblp.org/search/publ/api": response(body="bad json")}, retries=2)
    add("retry-then-success", {"https://export.arxiv.org/api/query": [response(503), response(503), response(body=atom(journal="Example Journal 2025"))],
                               "https://arxiv.org/abs/": response(None)}, retries=2)
    add("url-reference", text="@misc{link,title={Example Website},url={https://example.com}}")
    add("empty-entry", text="@misc{empty}")
    add("doi-not-found", {"https://api.crossref.org/works/10.1234/example": response(404)},
        text="@article{doi,title={Example Paper},author={Doe, Jane},year={2025},doi={10.1234/example}}")
    add("doi-found", {"https://api.crossref.org/works/10.1234/example": response(body={"message": crossref})},
        text="@article{doi,title={Example Paper},author={Doe, Jane},year={2025},doi={10.1234/example}}")
    add("openreview-id", {"https://api2.openreview.net/notes": response(body={"notes": [note]})},
        text="@inproceedings{or,title={Example Paper},author={Doe, Jane},year={2025},url={https://openreview.net/forum?id=accepted}}")
    for name, text, responses, retries in scenarios:
        for arxiv_only in (True, False):
            entries = parse_bibtex(text).entries
            results = checker.verify_entries(OfflineNet(responses), entries, retries=retries,
                                             check=checker.verify_arxiv_entry if arxiv_only else checker.verify_entry)
            expected = [asdict(result) for result in results]
            report_value = json.loads(report.build_json(results, "fixture.bib"))
            del report_value["generated"]
            for result in expected:
                del result["entry"]
            yield {"name": name, "text": text, "arxiv_only": arxiv_only, "retries": retries,
                   "responses": responses, "expected": expected, "report": report_value}


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--check", action="store_true")
    args = args.parse_args()
    path = Path(__file__).with_name("verify_cases.json")
    data = json.dumps(list(fixtures()), ensure_ascii=False, indent=2, default=lambda value: sorted(value) if isinstance(value, set) else value) + "\n"
    if args.check:
        assert path.read_text(encoding="utf-8") == data, "Regenerate verification snapshots"
    else:
        path.write_text(data, encoding="utf-8")


if __name__ == "__main__":
    main()
