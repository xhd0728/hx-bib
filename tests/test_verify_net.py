"""Cache rules for mutable publication metadata and transient HTTP responses."""

import io
import time
import urllib.error

from hx_bib.verify.net import Net
from hx_bib.verify.sources import SourceRecord, openreview_publication


def test_challenge_and_expired_cache_entries_are_refetched(monkeypatch):
    net = Net(delay=0)
    challenged = "https://dblp.org/example"
    outdated = "https://arxiv.org/abs/2502.14739"
    fetched_at = time.time()
    net.cache = {
        "|" + challenged: {"status": 202, "body": "challenge", "fetched_at": fetched_at},
        "|" + outdated: {"status": 200, "body": "old metadata", "fetched_at": fetched_at - 90_000},
    }
    requests = []

    class Response(io.BytesIO):
        def getcode(self):
            return 200

    def download(request, **_kwargs):
        requests.append(request.full_url)
        return Response(b"updated metadata")

    monkeypatch.setattr("urllib.request.urlopen", download)
    assert net.get(challenged) == (200, "updated metadata")
    assert net.get(outdated) == (200, "updated metadata")
    assert net.get(challenged) == (200, "updated metadata")
    assert requests == [challenged, outdated]


def test_publication_lookup_keeps_timeout_detail(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise urllib.error.URLError(TimeoutError("read timed out"))

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    record = openreview_publication(Net(delay=0, use_cache=False), SourceRecord("arXiv", True, title="Example"))
    assert record.error and record.note == "TimeoutError: read timed out"
