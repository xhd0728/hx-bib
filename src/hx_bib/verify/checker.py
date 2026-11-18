"""Citation verification against arXiv / Crossref / DBLP / OpenReview.

The verdict model mirrors the reference ``hallu-citation-check`` demo:

* ``OK``           — title/authors/year confirmed;
* ``INFO``         — nothing problematic but not academically confirmable
                     (blog, repo, model card…) or a transient failure;
* ``MINOR``        — small discrepancy (off-by-one year, single author…);
* ``WARN``         — notable mismatch worth a human look;
* ``FAIL``         — fabricated/invalid identifier or a different paper's title.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..core.identifiers import extract_arxiv_id, extract_doi, extract_openreview_id
from ..core.model import Entry
from .net import Net
from .sources import (
    SourceRecord,
    arxiv_batch,
    arxiv_lookup,
    crossref_lookup,
    crossref_publication,
    crossref_search,
    dblp_publication,
    dblp_search,
    doi_resolves,
    openreview_lookup,
    openreview_publication,
    publication_matches,
    url_check,
)
from .textutil import (
    delatex,
    looks_corporate,
    namekey,
    parse_bib_authors,
    surname_of_full,
    title_ratio,
)

OK, INFO, MINOR, WARN, FAIL = "OK", "INFO", "MINOR", "WARN", "FAIL"
SEVERITY_ORDER = {OK: 0, INFO: 1, MINOR: 2, WARN: 3, FAIL: 4}
SEVERITY_LABEL = {
    OK: "OK",
    INFO: "UNVERIFIABLE",
    MINOR: "MINOR",
    WARN: "WARNING",
    FAIL: "SUSPECT",
}

PAPER_TYPES = {
    "article", "inproceedings", "incollection", "inbook", "book",
    "phdthesis", "mastersthesis", "techreport", "conference",
}
logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    entry: Entry
    verdict: str
    issues: List[Tuple[str, str]] = field(default_factory=list)
    evidence: List[SourceRecord] = field(default_factory=list)
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    openreview_id: Optional[str] = None
    reference: Optional[SourceRecord] = None
    author_diff: Optional[dict] = None
    any_error: bool = False
    publication: Optional[Tuple[str, str]] = None
    publication_url: Optional[str] = None
    attempts: int = 1
    attempt_errors: List[Tuple[int, str]] = field(default_factory=list)

    def worst_severity(self) -> str:
        if not self.issues:
            return OK
        return max((s for s, _ in self.issues), key=lambda s: SEVERITY_ORDER.get(s, 0))


ProgressFn = Callable[[int, int, str], None]


class RunControl:
    """Pause / cancel handle shared between the UI and worker threads.

    ``paused`` and ``cancelled`` are :class:`threading.Event` objects so the
    same instance can be flipped from GUI callbacks while workers poll it.
    """

    def __init__(self) -> None:
        self.paused = threading.Event()
        self.cancelled = threading.Event()

    def pause(self) -> None:
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    def cancel(self) -> None:
        self.cancelled.set()
        self.paused.clear()

    def wait_if_paused(self) -> bool:
        """Block while paused. Return False once the run was cancelled."""
        while self.paused.is_set() and not self.cancelled.is_set():
            time.sleep(0.2)
        return not self.cancelled.is_set()


# ---------------------------------------------------------------------------
# Field comparisons
# ---------------------------------------------------------------------------
def cmp_title(bib_title: str, rec: SourceRecord) -> Tuple[str, float, Optional[str]]:
    ratio = title_ratio(bib_title, rec.title)
    if ratio >= 0.90:
        return OK, ratio, None
    if ratio >= 0.72:
        return MINOR, ratio, (
            f"title differs slightly (sim={ratio:.2f}) vs “{delatex(rec.title)}”"
        )
    return FAIL, ratio, (
        f"title does NOT match {rec.source} record (sim={ratio:.2f}) — "
        f"{rec.source} says “{delatex(rec.title)}”"
    )


def cmp_authors(bib_raw: str, rec: SourceRecord) -> Tuple[str, str, Optional[dict]]:
    if looks_corporate(bib_raw):
        return INFO, "group/organization author — not individually verifiable", None
    parsed = parse_bib_authors(bib_raw)
    truncated = any(b.get("etal") for b in parsed)
    bib = [b for b in parsed if not b.get("etal")]
    src_surnames = rec.authors
    src_raw = rec.raw_authors or []
    if not bib:
        return INFO, "no parseable authors in bib", None
    if not src_surnames:
        return INFO, f"{rec.source} record has no author list", None
    src_set = set(src_surnames)
    src_fullkeys = {namekey(s) for s in src_raw}

    def in_src(b: dict) -> bool:
        if b["surname"] in src_set:
            return True
        tokens = b["surname"].split()
        if len(tokens) > 1 and tokens[-1] in src_set:
            return True
        return namekey(b["display"]) in src_fullkeys

    bib_surnames, bib_fullkeys = set(), set()
    for b in bib:
        bib_surnames.add(b["surname"])
        tokens = b["surname"].split()
        if len(tokens) > 1:
            bib_surnames.add(tokens[-1])
        bib_fullkeys.add(namekey(b["display"]))

    def in_bib(raw: str) -> bool:
        return surname_of_full(raw) in bib_surnames or namekey(raw) in bib_fullkeys

    for b in bib:
        b["matched"] = "1" if in_src(b) else ""
    matched = [b for b in bib if b["matched"]]
    overlap = len(matched) / len(bib)
    first_ok = bool(bib[0]["matched"])
    missing = [b["display"] for b in bib if not b["matched"]]

    diff = {
        "source": rec.source,
        "bib": [{"display": b["display"], "matched": bool(b["matched"])} for b in bib],
        "src": [{"display": delatex(s), "matched": in_bib(s)} for s in src_raw],
    }
    detail = (
        f"bib {len(bib)}{'+' if truncated else ''} authors vs "
        f"{rec.source} {len(src_raw)}; overlap {len(matched)}/{len(bib)}"
    )
    miss_str = f"; not in {rec.source}: {', '.join(missing)}" if missing else ""
    if truncated:
        if overlap >= 0.99:
            return OK, detail, None
        if first_ok and overlap >= 0.85:
            return MINOR, detail + miss_str, diff
    if overlap >= 0.99 and len(bib) == len(src_raw):
        return OK, detail, None
    if first_ok and overlap >= 0.85:
        return MINOR, detail + miss_str, diff
    if not first_ok:
        return WARN, (
            f"first author “{bib[0]['display']}” not found in "
            f"{rec.source} author list"
        ), diff
    return WARN, (
        "listed authors disagree with " + rec.source + " — " + detail + miss_str
    ), diff


def cmp_year(
    bib_year: str, rec: SourceRecord, expect_published: bool = False
) -> Tuple[str, Optional[str]]:
    by = "".join(ch for ch in (bib_year or "") if ch.isdigit())
    ry = "".join(ch for ch in (rec.year or "") if ch.isdigit())
    if not by or not ry:
        return INFO, f"year not comparable (bib={bib_year!r}, {rec.source}={rec.year!r})"
    if by == ry:
        return OK, None
    d = int(by) - int(ry)
    if expect_published and 0 <= d <= 2:
        return OK, None
    if abs(d) == 1:
        return MINOR, (
            f"year off by 1 (bib={by}, {rec.source}={ry}) — "
            "common preprint/proceedings gap"
        )
    return WARN, f"year mismatch (bib={by}, {rec.source}={ry})"


def _venue_check(entry: Entry, records: Dict[str, SourceRecord]) -> Optional[Tuple[str, str]]:
    if entry.type != "inproceedings":
        return None
    booktitle = entry.get("booktitle") or ""
    if not booktitle:
        return None
    if records.get("OpenReview") or records.get("DBLP") or records.get("Crossref"):
        return None
    if records.get("arXiv"):
        return INFO, (
            f"claims venue “{delatex(booktitle)}” but only an arXiv preprint was "
            "found (not yet indexed as published — verify the venue manually)"
        )
    return None


# ---------------------------------------------------------------------------
# Per-entry verification
# ---------------------------------------------------------------------------
def _indexed_publication(arxiv: SourceRecord, records) -> Optional[SourceRecord]:
    publication_types = {
        "Crossref": {"journal-article", "proceedings-article"},
        "DBLP": {"Conference and Workshop Papers", "Journal Articles"},
        "OpenReview": {"accepted"},
    }
    return next((
        record for record in records if record and record.found and record.venue
        and record.publication_type in publication_types.get(record.source, ())
        and publication_matches(arxiv, record)
    ), None)


def _arxiv_publication(arxiv: SourceRecord, *records: Optional[SourceRecord]) -> Tuple[str, str]:
    indexed = _indexed_publication(arxiv, records)
    if indexed:
        year = f" {indexed.year}" if indexed.year and indexed.year not in indexed.venue else ""
        status = "accepted" if indexed.source == "OpenReview" else "record"
        return status, f"{indexed.source} · {indexed.venue}{year}"
    if arxiv.venue.strip().lower() not in {"", "arxiv", "corr"}:
        return "reference", arxiv.venue
    comment_match = re.search(r"\bcomment:\s*(.+)", arxiv.note or "", re.I)
    if comment_match:
        comment = comment_match.group(1)
        positive = re.search(
            r"\b(?:accepted (?:at|by|for|in|to)|published (?:at|by|in)|to appear (?:in|at))\b", comment, re.I,
        )
        negative = re.search(r"\b(?:not|never|if|once|when|whether)\b[^.;]{0,30}\b(?:accepted|published)\b",
                             comment, re.I)
        other_subject = re.search(
            r"\b(?:code|data|dataset|software|repository|supplement)\b[^.;]{0,20}\bpublished\b", comment, re.I,
        )
        if positive and not negative and not other_subject:
            return "comment", comment[:240]
    unavailable = list(dict.fromkeys(record.source for record in records if record and record.error))
    return ("unavailable", ", ".join(unavailable)) if unavailable else ("not_found", "")


def _find_publication(net: Net, arxiv: SourceRecord, indexes: list, evidence: list) -> tuple:
    publication = _arxiv_publication(arxiv, *indexes)
    if arxiv.title and arxiv.authors and publication[0] not in ("record", "accepted", "reference"):
        for search in (openreview_publication, crossref_publication, dblp_publication):
            record = search(net, arxiv)
            indexes.append(record)
            evidence.append(record)
            publication = _arxiv_publication(arxiv, *indexes)
            if publication[0] in ("record", "accepted"):
                break
    indexed = _indexed_publication(arxiv, indexes)
    url = indexed.url if indexed else arxiv.url if publication[0] in ("reference", "comment") else None
    return publication, url


def verify_arxiv_entry(net: Net, entry: Entry) -> CheckResult:
    """Check publication evidence without unrelated citation or URL checks."""
    arxiv_id = extract_arxiv_id(entry)
    if not arxiv_id:
        return CheckResult(entry, INFO)
    arxiv = arxiv_lookup(net, arxiv_id)
    arxiv.queried = arxiv_id
    evidence = [arxiv]
    indexes = []
    url = None
    if arxiv.found:
        if _arxiv_publication(arxiv)[0] != "reference":
            if doi := extract_doi(entry):
                indexes.append(crossref_lookup(net, doi))
            if openreview_id := extract_openreview_id(entry):
                indexes.append(openreview_lookup(net, openreview_id))
            evidence.extend(indexes)
        publication, url = _find_publication(net, arxiv, indexes, evidence)
    else:
        publication = ("unavailable", "arXiv") if arxiv.error else ("missing", "")
    status, detail = publication
    failed = any(record.error for record in evidence) and status in ("unavailable", "comment")
    message = f"arXiv publication: {status}" + (f" ({detail})" if detail else "")
    return CheckResult(entry, OK if status in ("record", "accepted", "reference") else INFO,
                       issues=[(INFO, message)], evidence=evidence, arxiv_id=arxiv_id,
                       any_error=failed, publication=publication, publication_url=url)


def verify_entry(net: Net, entry: Entry) -> CheckResult:
    fields = entry.fields
    bib_title = fields.get("title", "")
    bib_authors = fields.get("author", "")
    bib_year = fields.get("year", "")
    issues: List[Tuple[str, str]] = []
    evidence: List[SourceRecord] = []

    def add(severity: str, message: str) -> None:
        issues.append((severity, message))

    arxiv_id = extract_arxiv_id(entry)
    doi = extract_doi(entry)
    openreview_id = extract_openreview_id(entry)

    records: Dict[str, SourceRecord] = {}

    if arxiv_id:
        rec = arxiv_lookup(net, arxiv_id)
        rec.queried = arxiv_id
        evidence.append(rec)
        if rec.found:
            records["arXiv"] = rec
        elif rec.error:
            add(INFO, f"could not verify arXiv id {arxiv_id} ({rec.note}) — network/rate-limit, not checked")
        else:
            add(FAIL, f"arXiv id {arxiv_id} does NOT exist on arXiv — fabricated/incorrect identifier")

    if doi:
        rec = crossref_lookup(net, doi)
        rec.queried = doi
        evidence.append(rec)
        if rec.found:
            records["Crossref"] = rec
        elif rec.error:
            add(INFO, f"could not verify DOI {doi} ({rec.note}) — network/rate-limit, not checked")
        else:
            resolves = doi_resolves(net, doi)
            if resolves is True:
                add(INFO, f"DOI {doi} resolves (via doi.org) but is not in Crossref — metadata not cross-checked")
            elif resolves is None:
                add(INFO, f"could not verify DOI {doi} (doi.org unreachable) — not checked")
            else:
                add(FAIL, f"DOI {doi} does NOT resolve (Crossref + doi.org) — invalid/incorrect DOI")

    if openreview_id:
        rec = openreview_lookup(net, openreview_id)
        rec.queried = openreview_id
        evidence.append(rec)
        if rec.found:
            records["OpenReview"] = rec
        elif rec.error:
            add(INFO, f"could not reach OpenReview for id {openreview_id} — not checked")
        else:
            add(INFO, f"OpenReview id {openreview_id} not found — may be withdrawn/private")

    have_record = bool(
        records.get("arXiv") or records.get("Crossref") or records.get("OpenReview")
    )
    search_errored = False
    if bib_title and entry.type in PAPER_TYPES and not have_record:
        parsed = [b for b in parse_bib_authors(bib_authors) if not b.get("etal")]
        first_author = parsed[0]["surname"] if parsed else None
        cr = crossref_search(net, bib_title, first_author=first_author)
        evidence.append(cr)
        if cr.found:
            records["Crossref"] = cr
        else:
            search_errored = search_errored or cr.error
            dblp = dblp_search(net, bib_title, first_author=first_author)
            evidence.append(dblp)
            if dblp.found:
                records["DBLP"] = dblp
            else:
                search_errored = search_errored or dblp.error

    arxiv_rec = records.get("arXiv")
    publication = None
    publication_url = None
    if arxiv_id:
        publication = ("unavailable", "arXiv")
        if arxiv_rec is not None:
            indexes = [record for name, record in records.items() if name != "arXiv"]
            publication, publication_url = _find_publication(net, arxiv_rec, indexes, evidence)
            sources = {
                "record": "Matching publication record",
                "accepted": "Public acceptance record",
                "reference": "arXiv publication reference",
                "comment": "Author comment suggests acceptance or publication",
                "not_found": "No acceptance or publication evidence found",
                "unavailable": "Could not check acceptance or publication",
            }
            status, explanation = publication
            add(INFO, sources[status] + (f": {explanation}" if explanation else ""))

    title_ref = (
        records.get("arXiv") or records.get("Crossref")
        or records.get("OpenReview") or records.get("DBLP")
    )
    year_ref = (
        records.get("OpenReview") or records.get("Crossref")
        or records.get("DBLP") or records.get("arXiv")
    )

    title_status: Optional[str] = None
    author_diff: Optional[dict] = None
    if title_ref:
        title_status, _, message = cmp_title(bib_title, title_ref)
        if message:
            add(title_status, message)
        author_status, author_message, author_diff = cmp_authors(bib_authors, title_ref)
        if author_status != OK:
            add(author_status, "authors: " + author_message)
    if bib_authors and any(
        b.get("etal") for b in parse_bib_authors(bib_authors)
    ):
        add(INFO, "author list ends with 'and others' (truncated) — "
                  "the full author list is recommended")
    if year_ref:
        year_source = year_ref.source
        year_is_preprint = year_source == "arXiv" or (
            year_source == "DBLP" and (year_ref.venue or "").upper() == "CORR"
        )
        claims_venue = entry.type in ("inproceedings", "incollection", "conference") or bool(
            entry.fields.get("booktitle")
        )
        year_status, year_message = cmp_year(
            bib_year, year_ref, expect_published=year_is_preprint and claims_venue
        )
        if year_message:
            add(year_status, year_message)

    venue_note = _venue_check(entry, records)
    if venue_note:
        add(*venue_note)

    any_error = search_errored or any(
        r.error for r in evidence if r.source in ("arXiv", "Crossref", "OpenReview", "DBLP")
    )
    confirmed = bool(title_ref and title_status in (OK, MINOR))
    if not confirmed and not any(s == FAIL for s, _ in issues):
        url_field = (
            entry.get("url") or entry.get("howpublished") or entry.get("note") or ""
        )
        if url_field:
            result = url_check(net, url_field)
            evidence.append(
                SourceRecord(
                    source="URL", found=False, url=str(result.get("url")),
                    note=f"HTTP {result.get('status')}",
                )
            )
            status = result.get("status")
            if status == 200:
                add(INFO, f"no academic record found; URL is live (HTTP 200): {result['url']}")
            elif status in (301, 302, 303, 307, 308, 401, 403, 405, 406, 429):
                add(INFO, f"no academic record; URL reachable (HTTP {status}): {result['url']}")
            elif status in (404, 410):
                add(WARN, f"no academic record and URL returns HTTP {status} (resource does not exist): {result['url']}")
            elif status is None:
                add(INFO, f"no academic record; could not reach URL (network/anti-bot — verify manually): {result.get('url')}")
            else:
                add(WARN, f"no academic record; URL returned HTTP {status}: {result['url']}")
        elif any_error:
            add(INFO, "could not verify (DBLP/arXiv rate-limited or unreachable) — re-run to confirm; NOT treated as a problem")
        else:
            add(WARN, "not found in DBLP/arXiv/Crossref and no identifier or URL to verify")

    severities = [s for s, _ in issues]
    if FAIL in severities:
        verdict = FAIL
    elif WARN in severities:
        verdict = WARN
    elif MINOR in severities:
        verdict = MINOR
    elif confirmed:
        verdict = OK
    else:
        verdict = INFO

    return CheckResult(
        entry=entry,
        verdict=verdict,
        issues=issues,
        evidence=evidence,
        arxiv_id=arxiv_id,
        doi=doi,
        openreview_id=openreview_id,
        reference=title_ref,
        author_diff=author_diff,
        any_error=any_error,
        publication=publication,
        publication_url=publication_url,
    )


# ---------------------------------------------------------------------------
# Batch verification
# ---------------------------------------------------------------------------
def prefetch_arxiv(net: Net, entries: List[Entry]) -> None:
    ids = sorted({i for i in (extract_arxiv_id(e) for e in entries) if i})
    if len(ids) < 2:
        return
    net.arxiv_prefetch = {
        aid: rec for aid, rec in arxiv_batch(net, ids).items()
    }
    net.save()


def _safe_check(net: Net, entry: Entry, check) -> CheckResult:
    try:
        return check(net, entry)
    except Exception as exc:  # noqa: BLE001 - keep the rest of the batch running
        arxiv_id = extract_arxiv_id(entry)
        if not isinstance(exc, (TimeoutError, ConnectionError)):
            logger.exception("Verification failed for %s (arXiv %s)", entry.key, arxiv_id or "—")
        severity = INFO if check is verify_arxiv_entry else WARN
        return CheckResult(entry, severity, issues=[(severity, f"{type(exc).__name__}: {exc}")],
                           arxiv_id=arxiv_id, any_error=True)


def _check_with_retries(net: Net, entry: Entry, check, retries: int, control: Optional[RunControl]) -> CheckResult:
    failures = []
    for attempt in range(1, retries + 2):
        result = _safe_check(net, entry, check)
        if not result.any_error:
            break
        detail = "; ".join(f"{r.source}: {r.note}" for r in result.evidence if r.error)
        if not detail:
            detail = "; ".join(message for _, message in result.issues)
        detail = detail.replace("HTTP None", "connection failed or timed out") or "Verification unavailable"
        failures.append((attempt, detail))
        logger.warning("Verification attempt %d/%d failed for %s (arXiv %s): %s",
                       attempt, retries + 1, entry.key, result.arxiv_id or "—", detail)
        if attempt <= retries and control is not None and not control.wait_if_paused():
            break
    result.attempts = attempt
    result.attempt_errors = failures
    return result


def verify_entries(
    net: Net,
    entries: List[Entry],
    progress: Optional[ProgressFn] = None,
    workers: int = 1,
    control: Optional[RunControl] = None,
    *,
    check: Optional[Callable[[Net, Entry], CheckResult]] = None,
    retries: int = 0,
) -> List[CheckResult]:
    """Verify entries, optionally with ``workers`` concurrent threads.

    Concurrency is safe because :class:`Net` enforces per-host politeness
    spacing under a lock — parallel workers speed up cross-host lookups
    without hammering any single service. ``control`` allows pausing and
    cancelling the run between entries; a cancelled run returns the results
    completed so far.
    """
    entries = list(entries)
    check = check or verify_entry
    total = len(entries)
    results: List[Optional[CheckResult]] = [None] * total
    counter = {"done": 0}
    lock = threading.Lock()
    task_iter = iter(enumerate(entries))

    def run_one(idx: int, entry: Entry) -> None:
        results[idx] = _check_with_retries(net, entry, check, max(0, retries), control)
        with lock:
            counter["done"] += 1
            done = counter["done"]
        if progress:
            progress(done, total, entry.key)
        net.save()

    def worker() -> None:
        while True:
            if control is not None and not control.wait_if_paused():
                return
            with lock:
                try:
                    idx, entry = next(task_iter)
                except StopIteration:
                    return
            run_one(idx, entry)

    if workers <= 1:
        worker()
    else:
        n = max(1, min(workers, total))
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    return [r for r in results if r is not None]


def retry_failed(
    net: Net,
    results: List[CheckResult],
    pass_no: int = 1,
    control: Optional[RunControl] = None,
) -> List[CheckResult]:
    """Re-verify entries whose lookups failed transiently (cached lookups are free)."""
    updated = list(results)
    for idx, res in enumerate(results):
        if control is not None and not control.wait_if_paused():
            break
        if res.any_error and res.verdict != OK:
            new = _check_with_retries(net, res.entry, verify_entry, 0, control)
            updated[idx] = merge_attempts(res, new)
    net.save()
    return updated


def merge_attempts(previous: CheckResult, latest: CheckResult) -> CheckResult:
    """Keep the attempt history when replacing an individual result."""
    latest.attempt_errors = previous.attempt_errors + [
        (previous.attempts + number, detail) for number, detail in latest.attempt_errors
    ]
    latest.attempts += previous.attempts
    return latest
