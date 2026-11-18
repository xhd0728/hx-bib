"""Thread-safety / pause-cancel tests for batch verification (no network)."""

import threading
import time
from collections import Counter

from hx_bib.core.parser import parse_bibtex
from hx_bib.verify import checker
from hx_bib.verify.checker import OK, CheckResult, RunControl
from hx_bib.verify.report import build_html, build_json
from hx_bib.verify.sources import SourceRecord


class _FakeNet:
    def save(self):
        pass


def _entries(n=10):
    text = "\n".join(f"@article{{k{i}, title = {{T{i}}}}}" for i in range(n))
    return parse_bibtex(text).entries


def test_workers_produce_all_results(monkeypatch):
    seen = []
    lock = threading.Lock()

    def fake_verify(net, entry):
        with lock:
            seen.append(entry.key)
        time.sleep(0.01)
        return CheckResult(entry=entry, verdict=OK)

    monkeypatch.setattr(checker, "verify_entry", fake_verify)
    entries = _entries(12)
    results = checker.verify_entries(_FakeNet(), entries, workers=4)
    assert len(results) == 12
    assert sorted(r.entry.key for r in results) == sorted(e.key for e in entries)
    assert sorted(seen) == sorted(e.key for e in entries)


def test_progress_called_once_per_entry(monkeypatch):
    calls = []

    def fake_verify(net, entry):
        return CheckResult(entry=entry, verdict=OK)

    monkeypatch.setattr(checker, "verify_entry", fake_verify)
    entries = _entries(6)
    checker.verify_entries(
        _FakeNet(), entries, workers=3,
        progress=lambda done, total, key: calls.append((done, total, key)),
    )
    assert len(calls) == 6
    assert sorted(c[0] for c in calls) == [1, 2, 3, 4, 5, 6]
    assert all(c[1] == 6 for c in calls)


def test_worker_exception_does_not_kill_run(monkeypatch):
    def fake_verify(net, entry):
        if entry.key == "k3":
            raise RuntimeError("boom")
        return CheckResult(entry=entry, verdict=OK)

    monkeypatch.setattr(checker, "verify_entry", fake_verify)
    results = checker.verify_entries(_FakeNet(), _entries(6), workers=2)
    assert len(results) == 6
    bad = [r for r in results if r.entry.key == "k3"][0]
    assert bad.verdict != OK and bad.any_error


def test_cancel_returns_partial_results(monkeypatch):
    def fake_verify(net, entry):
        time.sleep(0.02)
        return CheckResult(entry=entry, verdict=OK)

    monkeypatch.setattr(checker, "verify_entry", fake_verify)
    control = RunControl()
    stop = threading.Timer(0.05, control.cancel)
    stop.start()
    results = checker.verify_entries(
        _FakeNet(), _entries(20), workers=1, control=control
    )
    stop.join()
    assert 0 < len(results) < 20


def test_pause_blocks_progress_until_resume(monkeypatch):
    done = []

    def fake_verify(net, entry):
        done.append(entry.key)
        return CheckResult(entry=entry, verdict=OK)

    monkeypatch.setattr(checker, "verify_entry", fake_verify)
    control = RunControl()
    control.pause()
    entries = _entries(4)
    t = threading.Thread(
        target=checker.verify_entries,
        args=(_FakeNet(), entries),
        kwargs={"workers": 2, "control": control},
    )
    t.start()
    time.sleep(0.15)
    assert done == []          # workers are parked while paused
    control.resume()
    t.join(timeout=5)
    assert not t.is_alive()
    assert len(done) == 4


def test_run_control_wait_semantics():
    control = RunControl()
    assert control.wait_if_paused() is True    # not paused → proceed
    control.pause()
    threading.Timer(0.1, control.cancel).start()
    assert control.wait_if_paused() is False   # cancelled while paused


def test_retries_are_isolated_and_report_recovered_and_exhausted_attempts():
    calls = Counter()

    def check(_net, entry):
        calls[entry.key] += 1
        if entry.key == "k1" and calls[entry.key] < 3:
            raise TimeoutError("server timed out")
        if entry.key == "k2":
            return CheckResult(entry, "INFO", any_error=True,
                               evidence=[SourceRecord("OpenReview", False, error=True, note="HTTP None")])
        return CheckResult(entry, OK)

    results = checker.verify_entries(_FakeNet(), _entries(3), workers=2, check=check, retries=2)
    assert calls == {"k0": 1, "k1": 3, "k2": 3}
    normal, recovered, failed = results
    assert normal.attempt_errors == [] and recovered.attempts == 3 and not recovered.any_error
    assert recovered.attempt_errors == [(1, "TimeoutError: server timed out"), (2, "TimeoutError: server timed out")]
    assert failed.any_error and failed.attempt_errors[-1] == (3, "OpenReview: connection failed or timed out")
    resumed = checker.merge_attempts(failed, CheckResult(failed.entry, OK))
    assert resumed.attempts == 4 and not resumed.any_error and len(resumed.attempt_errors) == 3
    assert '"attempts": 4' in build_json([resumed], "sample.bib")
    assert "Attempt 3: OpenReview: connection failed or timed out" in build_html([resumed], "sample.bib")
