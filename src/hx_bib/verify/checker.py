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

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..core.identifiers import extract_arxiv_id, extract_doi, extract_openreview_id
from ..core.model import Document, Entry
from .net import Net
from .sources import (
    SourceRecord,
    arxiv_batch,
    arxiv_lookup,
    crossref_lookup,
    crossref_search,
    dblp_search,
    doi_resolves,
    openreview_lookup,
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

    def worst_severity(self) -> str:
        if not self.issues:
            return OK
        return max((s for s, _ in self.issues), key=lambda s: SEVERITY_ORDER.get(s, 0))


ProgressFn = Callable[[int, int, str], None]


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
    )


# ---------------------------------------------------------------------------
# Batch verification
# ---------------------------------------------------------------------------
def prefetch_arxiv(net: Net, entries: List[Entry]) -> None:
    ids = sorted({i for i in (extract_arxiv_id(e) for e in entries) if i})
    if not ids:
        return
    net.arxiv_prefetch = {
        aid: rec for aid, rec in arxiv_batch(net, ids).items()
    }
    net.save()


def verify_entries(
    net: Net,
    entries: List[Entry],
    progress: Optional[ProgressFn] = None,
) -> List[CheckResult]:
    results: List[CheckResult] = []
    for idx, entry in enumerate(entries, 1):
        try:
            result = verify_entry(net, entry)
        except Exception as exc:  # noqa: BLE001 - never let one entry kill the run
            result = CheckResult(
                entry=entry, verdict=WARN,
                issues=[(WARN, f"checker error: {exc}")],
                any_error=True,
            )
        results.append(result)
        if progress:
            progress(idx, len(entries), entry.key)
        net.save()
    return results


def retry_failed(net: Net, results: List[CheckResult], pass_no: int = 1) -> List[CheckResult]:
    """Re-verify entries whose lookups failed transiently (cached lookups are free)."""
    updated = list(results)
    for idx, res in enumerate(results):
        if res.any_error and res.verdict != OK:
            updated[idx] = verify_entry(net, res.entry)
    net.save()
    return updated
