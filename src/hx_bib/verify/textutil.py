"""Text normalization helpers for citation comparison."""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List

_ACCENT_RE = re.compile(r"\\[`'\"^~=.cuvHtdbr]\s*\{?\\?(\w)\}?")
_CMD_RE = re.compile(r"\\[a-zA-Z]+")

CORPORATE_HINTS = (
    "team", "openai", "deepseek", "google", "deepmind", "qwen", "ai", "org",
    "lab", "labs", "research", "inc", "analysis", "lmsys", "anthropic", "meta",
)


def delatex(text: str) -> str:
    """Strip LaTeX markup down to comparable plain text."""
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\t", " ")
    text = re.sub(r"\\url\{([^}]*)\}", r"\1", text)
    text = _ACCENT_RE.sub(r"\1", text)
    text = (
        text.replace("\\&", "&")
        .replace("\\_", "_")
        .replace("\\%", "%")
        .replace("\\$", "$")
    )
    text = re.sub(r"\$[^$]*\$", lambda m: re.sub(r"[^0-9A-Za-z]", "", m.group(0)), text)
    text = _CMD_RE.sub("", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def asciifold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def norm_title(text: str) -> str:
    text = asciifold(delatex(text)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_ratio(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def surname_of_full(name: str) -> str:
    """'Nelson F. Liu' / 'Yang Zhang 0001' -> 'liu' / 'zhang'."""
    name = asciifold(delatex(name)).strip()
    tokens = [t for t in name.split() if not t.isdigit()]
    if not tokens:
        return ""
    last = tokens[-1]
    return re.sub(r"[^a-z0-9\- ]", "", last.lower())


def namekey(name: str) -> str:
    """Whole-name key: ascii-folded, lowercase, alphanumerics only.

    Lets 'YuYue' == 'Yu Yue' and 'Wei-Ying Ma' == 'WeiYing Ma'.
    """
    return re.sub(r"[^a-z0-9]", "", asciifold(delatex(name)).lower())


def parse_bib_authors(raw: str) -> List[Dict[str, str]]:
    """BibTeX author string -> list of {display, surname[, etal]}."""
    raw = delatex(raw)
    out: List[Dict[str, str]] = []
    for part in re.split(r"\s+and\s+", raw):
        part = part.strip().strip(",")
        if not part:
            continue
        if part.lower() in ("others", "et al", "et al."):
            out.append({"display": "et al.", "surname": "", "etal": "1"})
            continue
        if "," in part:
            last, _, first = part.partition(",")
            last = last.strip()
            display = (first.strip() + " " + last).strip()
        else:
            tokens = part.split()
            last = tokens[-1] if tokens else part
            display = part
        surname = re.sub(r"[^a-z0-9\- ]", "", asciifold(last).lower().strip())
        if surname:
            out.append({"display": display, "surname": surname})
    return out


def looks_corporate(raw_author: str) -> bool:
    """True if the author field is a group/organization, not a person list."""
    raw = delatex(raw_author).strip()
    if not raw:
        return False
    if re.search(r"\sand\s", raw):
        return False
    if "," in raw:
        return False
    tokens = raw.split()
    low = raw.lower()
    has_hint = any(h in re.split(r"[\s/\-]+", low) for h in CORPORATE_HINTS)
    camel = bool(re.search(r"[a-z][A-Z]", raw))
    return has_hint or camel or len(tokens) == 1
