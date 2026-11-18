"""Authoritative metadata sources: arXiv, Crossref, DBLP, OpenReview, URLs.

All clients return a :class:`SourceRecord` with a tri-state outcome:

* ``found=True``               — the source knows this record;
* ``found=False, error=False`` — definitive absence ("does not exist");
* ``found=False, error=True``  — transient failure (rate limit, network…).

Only a *definitive* absence may be reported as a hallucination.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..core.identifiers import extract_arxiv_id
from ..core.model import Entry
from .net import BROWSER_UA, Net
from .textutil import asciifold, namekey, norm_title, surname_of_full, title_ratio


@dataclass
class SourceRecord:
    source: str
    found: bool
    error: bool = False
    title: str = ""
    authors: List[str] = field(default_factory=list)      # surnames
    raw_authors: List[str] = field(default_factory=list)  # display names
    year: Optional[str] = None
    venue: str = ""
    doi: Optional[str] = None
    url: Optional[str] = None
    note: str = ""
    queried: Optional[str] = None
    match_ratio: Optional[float] = None
    best_ratio: Optional[float] = None
    publication_type: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "found": self.found,
            "error": self.error,
            "title": self.title,
            "authors": self.authors,
            "raw_authors": self.raw_authors,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "url": self.url,
            "note": self.note,
            "queried": self.queried,
            "match_ratio": self.match_ratio,
            "publication_type": self.publication_type,
        }


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------
_ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_CROSSREF_PUBLICATION_TYPES = {"journal-article", "proceedings-article"}


def publication_matches(reference: SourceRecord, candidate: SourceRecord) -> bool:
    """Require a close title and a shared author before linking two records."""
    generic = {"team", "group", "anonymous", "collaboration"}
    reference_authors = {namekey(author) for author in reference.authors} - generic - {""}
    candidate_authors = {namekey(author) for author in candidate.authors} - generic - {""}
    return bool(reference_authors & candidate_authors) and title_ratio(reference.title, candidate.title) >= 0.95


def _norm_arxiv_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id.strip())


def _arxiv_entry_to_record(elem: ET.Element) -> Optional[SourceRecord]:
    ns = _ARXIV_NS
    title = (elem.findtext("a:title", "", ns) or "").strip()
    if not title or title.lower().startswith("error"):
        return None
    raw_authors = [
        (a.findtext("a:name", "", ns) or "").strip()
        for a in elem.findall("a:author", ns)
    ]
    authors = [surname_of_full(a) for a in raw_authors]
    published = elem.findtext("a:published", "", ns) or ""
    year = published[:4] if published[:4].isdigit() else None
    id_url = (elem.findtext("a:id", "", ns) or "").strip()
    # journal_ref / comment tell us whether the preprint was accepted (or
    # even published) at a venue — used by the "accepted?" check.
    journal_ref = re.sub(r"\s+", " ", elem.findtext("arxiv:journal_ref", "", ns) or "").strip()
    comment = re.sub(r"\s+", " ", elem.findtext("arxiv:comment", "", ns) or "").strip()
    notes = []
    if journal_ref:
        notes.append("journal_ref: " + journal_ref)
    if comment:
        notes.append("comment: " + comment[:160])
    return SourceRecord(
        source="arXiv", found=True, title=title,
        authors=[a for a in authors if a], raw_authors=raw_authors,
        year=year, venue=journal_ref or "arXiv", url=id_url,
        note="; ".join(notes),
    )


def arxiv_batch(net: Net, ids: List[str]) -> Dict[str, Optional[SourceRecord]]:
    """Pre-fetch many arXiv ids (1 request per ~40 ids)."""
    out: Dict[str, Optional[SourceRecord]] = {}
    ids = [_norm_arxiv_id(i) for i in ids]
    for i in range(0, len(ids), 40):
        chunk = ids[i : i + 40]
        out.update(dict.fromkeys(chunk))
        url = (
            "https://export.arxiv.org/api/query?max_results=100&id_list="
            + ",".join(urllib.parse.quote(c) for c in chunk)
        )
        status, body = net.get(url, retries=1, timeout=7)
        if status != 200 or not body:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        by_id: Dict[str, Optional[SourceRecord]] = {}
        for elem in root.findall("a:entry", _ARXIV_NS):
            idu = elem.findtext("a:id", "", _ARXIV_NS) or ""
            m = re.search(r"/abs/([^?#]+)", idu)
            if m:
                by_id[_norm_arxiv_id(m.group(1))] = _arxiv_entry_to_record(elem)
        for c in chunk:
            out[c] = by_id.get(c)
    return out


def _parse_meta_tags(body: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for tag in re.findall(r"<meta\b[^>]*>", body, re.I):
        name = re.search(r'\bname\s*=\s*"([^"]*)"', tag, re.I)
        content = re.search(r'\bcontent\s*=\s*"([^"]*)"', tag, re.I)
        if name and content:
            out.setdefault(name.group(1).lower(), []).append(html.unescape(content.group(1)))
    return out


def arxiv_abs_lookup(net: Net, arxiv_id: str) -> SourceRecord:
    """Fallback via https://arxiv.org/abs/<id> (different infrastructure)."""
    url = "https://arxiv.org/abs/" + urllib.parse.quote(arxiv_id)
    status, body = net.get(url, ua=BROWSER_UA, retries=2, timeout=8)
    if status in (404, 410):
        return SourceRecord(source="arXiv", found=False, note="abs page 404")
    if status != 200 or not body:
        return SourceRecord(source="arXiv", found=False, error=True, note=f"abs HTTP {status}")
    meta = _parse_meta_tags(body)
    titles = meta.get("citation_title")
    if not titles:
        return SourceRecord(source="arXiv", found=False, error=True, note="abs page unparsed")
    # "Journal reference" / acceptance hints live in the abs page body.
    journal_ref = ""
    m = re.search(r'class="[^"]*\bjref\b[^"]*"[^>]*>(.*?)</td>', body, re.I | re.S)
    if m:
        journal_ref = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()
        journal_ref = re.sub(r"^Journal\s+ref(?:erence)?\s*:\s*", "", journal_ref, flags=re.I)
    comment = ""
    m = re.search(r'class="[^"]*\bcomments\b[^"]*"[^>]*>(.*?)</td>', body, re.I | re.S)
    if m:
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        comment = re.sub(r"\s+", " ", html.unescape(text)).strip()
        comment = re.sub(r"^Comments?\s*:\s*", "", comment, flags=re.I)
    notes = ["via abs page"]
    if journal_ref:
        notes.append("journal_ref: " + journal_ref)
    if comment:
        notes.append("comment: " + comment[:160])
    raw_authors: List[str] = []
    surnames: List[str] = []
    for a in meta.get("citation_author", []):
        a = a.strip()
        if "," in a:
            last, _, first = a.partition(",")
            raw_authors.append((first.strip() + " " + last.strip()).strip())
            sur = last.strip()
        else:
            raw_authors.append(a)
            sur = a.split()[-1] if a.split() else a
        sn = re.sub(r"[^a-z0-9\- ]", "", asciifold(sur).lower())
        if sn:
            surnames.append(sn)
    dates = meta.get("citation_date") or meta.get("citation_online_date") or []
    ym = re.search(r"(19|20)\d\d", dates[0]) if dates else None
    return SourceRecord(
        source="arXiv", found=True, title=titles[0].strip(),
        authors=surnames, raw_authors=raw_authors,
        year=ym.group(0) if ym else None, venue=journal_ref or "arXiv",
        url="https://arxiv.org/abs/" + arxiv_id, note="; ".join(notes),
    )


def arxiv_lookup(net: Net, arxiv_id: str) -> SourceRecord:
    arxiv_id = _norm_arxiv_id(arxiv_id)
    prefetch = net.arxiv_prefetch
    if prefetch is not None and arxiv_id in prefetch:
        rec = prefetch[arxiv_id]
        if rec is not None:
            return rec
        return arxiv_abs_lookup(net, arxiv_id)

    url = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    status, body = net.get(url, retries=1, timeout=7)
    if status == 200 and body:
        try:
            entries = ET.fromstring(body).findall("a:entry", _ARXIV_NS)
            rec = _arxiv_entry_to_record(entries[0]) if entries else None
            if rec is not None:
                return rec
            return arxiv_abs_lookup(net, arxiv_id)
        except ET.ParseError:
            pass
    return arxiv_abs_lookup(net, arxiv_id)


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------
def _crossref_item_to_record(msg: dict) -> SourceRecord:
    title = (msg.get("title") or [""])[0]
    authors: List[str] = []
    raw: List[str] = []
    for a in msg.get("author", []) or []:
        fam = a.get("family") or a.get("name") or ""
        raw.append((a.get("given", "") + " " + fam).strip() or fam)
        if fam:
            authors.append(re.sub(r"[^a-z0-9\- ]", "", asciifold(fam).lower()))
    year = None
    for key in ("published", "published-print", "published-online", "issued", "created"):
        date_parts = (msg.get(key) or {}).get("date-parts")
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = str(date_parts[0][0])
            break
    venue = (msg.get("container-title") or [""])[0]
    return SourceRecord(
        source="Crossref", found=True, title=title, authors=authors,
        raw_authors=raw, year=year, venue=venue,
        doi=msg.get("DOI"), url=msg.get("URL"), publication_type=msg.get("type") or "",
    )


def crossref_lookup(net: Net, doi: str) -> SourceRecord:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    status, body = net.get(url, accept="application/json")
    if status == 404:
        return SourceRecord(source="Crossref", found=False, note="DOI not registered")
    if status != 200 or not body:
        return SourceRecord(source="Crossref", found=False, error=True, note=f"HTTP {status}")
    try:
        msg = json.loads(body)["message"]
    except (ValueError, KeyError):
        return SourceRecord(source="Crossref", found=False, error=True, note="parse error")
    return _crossref_item_to_record(msg)


def crossref_search(net: Net, title: str, first_author: Optional[str] = None) -> SourceRecord:
    q = norm_title(title)
    if not q:
        return SourceRecord(source="Crossref", found=False, note="empty title")
    url = (
        "https://api.crossref.org/works?rows=10&select=title,author,issued,"
        "container-title,DOI,URL&query.bibliographic=" + urllib.parse.quote(q)
    )
    if first_author:
        url += "&query.author=" + urllib.parse.quote(first_author)
    status, body = net.get(url, accept="application/json")
    if status != 200 or not body:
        return SourceRecord(source="Crossref", found=False, error=True, note=f"HTTP {status}")
    try:
        items = json.loads(body)["message"]["items"]
    except (ValueError, KeyError):
        return SourceRecord(source="Crossref", found=False, error=True, note="parse error")
    best, best_r = None, 0.0
    for item in items:
        r = title_ratio(title, (item.get("title") or [""])[0])
        if r > best_r:
            best_r, best = r, item
    if best is None or best_r < 0.90:
        return SourceRecord(
            source="Crossref", found=False,
            note="no close Crossref title match", best_ratio=round(best_r, 2),
        )
    rec = _crossref_item_to_record(best)
    rec.match_ratio = round(best_r, 2)
    return rec


def _publication_queries(title: str) -> List[str]:
    full = norm_title(title)
    prefix, separator, _ = title.partition(":")
    short = norm_title(prefix) if separator else ""
    if len(short.replace(" ", "")) >= 5 and short != full:
        return [short, full]
    return [full] if full else []


def crossref_publication(net: Net, reference: SourceRecord) -> SourceRecord:
    for query in _publication_queries(reference.title):
        params = urllib.parse.urlencode({
            "rows": 5,
            "query.bibliographic": query,
        })
        status, body = net.get(
            "https://api.crossref.org/works?" + params, accept="application/json", retries=1, timeout=8,
        )
        if status != 200:
            return SourceRecord("Crossref", False, error=True, note=f"HTTP {status}")
        try:
            items = json.loads(body)["message"]["items"]
        except (ValueError, KeyError, TypeError):
            return SourceRecord("Crossref", False, error=True, note="parse error")
        for item in items:
            if item.get("type") in _CROSSREF_PUBLICATION_TYPES:
                record = _crossref_item_to_record(item)
                if record.venue and record.doi and publication_matches(reference, record):
                    return record
    return SourceRecord("Crossref", False, note="no matching journal or proceedings article")


def doi_resolves(net: Net, doi: str) -> Optional[bool]:
    """True (resolves), False (404), None (inconclusive)."""
    url = "https://doi.org/" + urllib.parse.quote(doi)
    status, _ = net.get(url, ua=BROWSER_UA, retries=3)
    if status in (404, 410):
        return False
    if status and 200 <= status < 400 or status in (401, 403):
        return True
    return None


# ---------------------------------------------------------------------------
# DBLP
# ---------------------------------------------------------------------------
def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def dblp_search(net: Net, title: str, first_author: Optional[str] = None,
                *, query: Optional[str] = None, retries: int = 3, timeout: Optional[float] = None) -> SourceRecord:
    q = query or norm_title(title)
    if not q:
        return SourceRecord(source="DBLP", found=False, note="empty title")
    if first_author:
        q = q + " " + first_author
    url = "https://dblp.org/search/publ/api?format=json&h=30&q=" + urllib.parse.quote(q)
    status, body = net.get(url, accept="application/json", retries=retries, timeout=timeout)
    if status != 200 or not body:
        return SourceRecord(source="DBLP", found=False, error=True, note=f"HTTP {status}")
    try:
        hits = json.loads(body)["result"]["hits"]
    except (ValueError, KeyError):
        return SourceRecord(source="DBLP", found=False, error=True, note="parse error")
    if hits.get("@total", "0") == "0" or "hit" not in hits:
        return SourceRecord(source="DBLP", found=False, note="no DBLP hit")

    scored = []
    for hit in _as_list(hits["hit"]):
        info = hit.get("info", {})
        scored.append((title_ratio(title, info.get("title", "")), info))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_r = scored[0][0] if scored else 0.0
    if not scored or best_r < 0.82:
        return SourceRecord(
            source="DBLP", found=False,
            note="no close DBLP title match", best_ratio=round(best_r, 2),
        )
    near = [info for r, info in scored if best_r - r <= 0.03]
    best = next((i for i in near if (i.get("venue") or "").upper() != "CORR"), near[0])
    authors_raw: List[str] = []
    auth = best.get("authors", {}).get("author") if best.get("authors") else None
    for a in _as_list(auth):
        authors_raw.append(a.get("text", "") if isinstance(a, dict) else str(a))
    authors = [surname_of_full(a) for a in authors_raw]
    return SourceRecord(
        source="DBLP", found=True, title=best.get("title", ""),
        authors=[a for a in authors if a], raw_authors=authors_raw,
        year=str(best.get("year", "")) or None, venue=best.get("venue", ""),
        publication_type=best.get("type") or "",
        doi=best.get("doi"), url=best.get("ee") or best.get("url"),
        match_ratio=round(best_r, 2),
    )


def dblp_publication(net: Net, reference: SourceRecord) -> SourceRecord:
    for query in _publication_queries(reference.title):
        record = dblp_search(net, reference.title, query=query, retries=1, timeout=8)
        if record.error or (record.found and publication_matches(reference, record)):
            return record
    return SourceRecord("DBLP", False, note="no matching publication record")


# ---------------------------------------------------------------------------
# OpenReview
# ---------------------------------------------------------------------------
_OPENREVIEW_NOTES = "https://api2.openreview.net/notes"


def _openreview_record(note: dict) -> Optional[SourceRecord]:
    content = note.get("content", {})

    def value_of(key: str):
        value = content.get(key)
        return value.get("value") if isinstance(value, dict) else value

    title = (value_of("title") or "").strip()
    forum = note.get("forum") or note.get("id")
    if not title or not forum:
        return None
    raw_authors = value_of("authors") or []
    if isinstance(raw_authors, str):
        raw_authors = [raw_authors]
    raw_authors = [author for author in raw_authors if author and author.lower() != "anonymous"]
    venue_id = value_of("venueid") or ""
    venue = (value_of("venue") or venue_id).strip()
    year_match = re.search(r"(19|20)\d\d", venue)
    year = year_match.group(0) if year_match else None
    if year is None:
        for key in ("pdate", "odate", "cdate"):
            milliseconds = note.get(key)
            if isinstance(milliseconds, (int, float)) and milliseconds > 0:
                year = time.strftime("%Y", time.gmtime(milliseconds / 1000))
                break

    publication_date = note.get("pdate")
    accepted = (
        note.get("id") == forum and venue_id and venue_id == note.get("domain")
        and isinstance(publication_date, (int, float)) and publication_date > 0
        and not note.get("ddate")
        and not re.search(r"/(?:submission|withdrawn|rejected|desk_rejected)(?:/|$)", venue_id, re.I)
    )
    authors = [surname_of_full(author) for author in raw_authors]
    return SourceRecord(
        source="OpenReview", found=True, title=title,
        authors=[author for author in authors if author], raw_authors=raw_authors,
        year=year, venue=venue, url="https://openreview.net/forum?" + urllib.parse.urlencode({"id": forum}),
        publication_type="accepted" if accepted else "",
    )


def openreview_lookup(net: Net, oid: str) -> SourceRecord:
    errored = False
    for parameter in ("id", "forum"):
        url = _OPENREVIEW_NOTES + "?" + urllib.parse.urlencode({parameter: oid})
        status, body = net.get(url, accept="application/json")
        if status != 200:
            errored = True
            continue
        try:
            notes = json.loads(body).get("notes", [])
        except (ValueError, AttributeError):
            errored = True
            continue
        for note in notes:
            if note.get("id") == oid:
                record = _openreview_record(note)
                if record is not None:
                    return record
    return SourceRecord(
        source="OpenReview", found=False, error=errored,
        note="could not reach OpenReview" if errored else "forum id not found",
    )


def openreview_publication(net: Net, reference: SourceRecord) -> SourceRecord:
    for query in _publication_queries(reference.title):
        params = urllib.parse.urlencode({"term": query, "content": "title", "source": "forum", "limit": 10})
        status, body = net.get(
            f"{_OPENREVIEW_NOTES}/search?{params}", accept="application/json", retries=1, timeout=8,
        )
        if status != 200:
            return SourceRecord("OpenReview", False, error=True, note=f"HTTP {status}")
        try:
            notes = json.loads(body)["notes"]
        except (KeyError, TypeError, ValueError):
            return SourceRecord("OpenReview", False, error=True, note="parse error")
        for note in notes:
            record = _openreview_record(note)
            if record and record.publication_type == "accepted" and publication_matches(reference, record):
                return record
    return SourceRecord("OpenReview", False, note="no matching public acceptance record")


# ---------------------------------------------------------------------------
# URL liveness
# ---------------------------------------------------------------------------
def url_check(net: Net, url: str) -> Dict[str, object]:
    url = url.strip()
    m = re.search(r"\\url\{([^}]*)\}", url)
    if m:
        url = m.group(1)
    m = re.search(r"https?://\S+", url)
    if not m:
        return {"url": url, "status": None, "note": "no URL"}
    url = m.group(0).rstrip(".,;)}")
    status, _ = net.get(url, ua=BROWSER_UA)
    return {"url": url, "status": status}


# ---------------------------------------------------------------------------
# Entry -> identifier convenience
# ---------------------------------------------------------------------------
def arxiv_id_of(entry: Entry) -> Optional[str]:
    return extract_arxiv_id(entry)
