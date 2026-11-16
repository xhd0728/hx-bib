"""Extract arXiv / DOI / OpenReview identifiers from a BibTeX entry."""

from __future__ import annotations

import re
from typing import Optional

from .model import Entry

_ARXIV_NEW = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")
_ARXIV_OLD = re.compile(r"(?<![a-z])[a-z\-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?", re.I)
_ARXIV_DOI = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)


def extract_arxiv_id(entry: Entry) -> Optional[str]:
    """Return a normalized modern arXiv id (``2411.13154``) if one is present.

    Looks at ``eprint``, ``journal``, ``url``, ``note``, ``howpublished``,
    ``doi`` and the ``volume`` field (``abs/2411.13154`` style).
    """
    fields = entry.fields
    eprint = (fields.get("eprint") or "").strip()
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", eprint):
        return re.sub(r"v\d+$", "", eprint)

    blob = " ".join(
        fields.get(name, "")
        for name in ("journal", "note", "howpublished", "url", "doi", "volume", "eprint")
    )
    m = _ARXIV_DOI.search(blob)
    if m:
        return m.group(1)
    m = _ARXIV_NEW.search(blob)
    if m:
        return m.group(1)
    m = _ARXIV_OLD.search(blob)
    if m:
        return m.group(0)
    return None


def extract_doi(entry: Entry) -> Optional[str]:
    """Return a Crossref-style DOI (arXiv's ``10.48550`` DOIs excluded)."""
    doi = (entry.get("doi") or "").strip()
    if not doi:
        m = re.search(r"doi\.org/(10\.\S+)", entry.get("url") or "", re.I)
        doi = m.group(1) if m else ""
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I).strip()
    if not doi or re.match(r"10\.48550/arxiv\.", doi, re.I):
        return None
    return doi


def extract_openreview_id(entry: Entry) -> Optional[str]:
    blob = " ".join(
        entry.get(name) or "" for name in ("url", "howpublished", "note")
    )
    m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", blob)
    return m.group(1) if m else None


def is_arxiv_like(entry: Entry) -> bool:
    """True if the entry claims to be an arXiv preprint."""
    if "eprint" in entry.fields or "archiveprefix" in entry.fields:
        return True
    for name in ("journal", "booktitle", "note", "howpublished", "publisher"):
        value = (entry.get(name) or "").lower()
        if "arxiv" in value or "corr" == value.strip():
            return True
    volume = (entry.get("volume") or "")
    if re.match(r"\s*abs/", volume, re.I):
        return True
    return False
