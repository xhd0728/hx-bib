"""Built-in venue presets for common CS / AI conferences and journals.

Each :class:`Venue` knows its canonical abbreviation, full name, *family*
(the publisher / umbrella the user groups venues by — e.g. all ACL-series
venues can be collapsed to ``ARR``, the ACM ones to ``ACM``) and a list of
aliases used for tolerant matching ("Proceedings of EMNLP",
"Proc. of the 2023 Conference on Empirical Methods…", "emnlp", …).

Matching is deterministic: the longest alias wins, so
"Association for Computational Linguistics" resolves to ACL rather than to
the CL journal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Venue families -------------------------------------------------------------
FAMILY_ARR = "ARR"        # ACL / EMNLP / NAACL / … (ACL Rolling Review)
FAMILY_ACM = "ACM"        # KDD / SIGIR / MM / WWW / …
FAMILY_IEEE = "IEEE"      # CVPR / ICCV / TPAMI / …
FAMILY_NEURIPS = "NeurIPS"
FAMILY_PMLR = "PMLR"      # ICML / AISTATS / UAI (proceedings.mlr.press)
FAMILY_AAAI = "AAAI"
FAMILY_IJCAI = "IJCAI"
FAMILY_ICLR = "ICLR"
FAMILY_SPRINGER = "Springer"
FAMILY_JMLR = "JMLR"
FAMILY_TMLR = "TMLR"

CONFERENCE = "conference"
JOURNAL = "journal"


@dataclass(frozen=True)
class Venue:
    abbr: str
    name: str
    family: str
    kind: str = CONFERENCE
    aliases: Tuple[str, ...] = ()

    def match_terms(self) -> Tuple[str, ...]:
        return (self.abbr, self.name, *self.aliases)


VENUES: List[Venue] = [
    # ---- ACL series (family: ARR) -----------------------------------------
    Venue("ACL", "Annual Meeting of the Association for Computational Linguistics", FAMILY_ARR,
          aliases=("Association for Computational Linguistics", "ACL")),
    Venue("EMNLP", "Conference on Empirical Methods in Natural Language Processing", FAMILY_ARR,
          aliases=("Empirical Methods in Natural Language Processing",)),
    Venue("NAACL", "North American Chapter of the Association for Computational Linguistics", FAMILY_ARR,
          aliases=("North American Chapter of the ACL",)),
    Venue("EACL", "European Chapter of the Association for Computational Linguistics", FAMILY_ARR,
          aliases=("European Chapter of the ACL",)),
    Venue("AACL", "Asian Chapter of the Association for Computational Linguistics", FAMILY_ARR,
          aliases=("Asian Chapter of the ACL", "AACL-IJCNLP")),
    Venue("COLING", "International Conference on Computational Linguistics", FAMILY_ARR),
    Venue("TACL", "Transactions of the Association for Computational Linguistics", FAMILY_ARR,
          kind=JOURNAL),
    Venue("CL", "Computational Linguistics", FAMILY_ARR, kind=JOURNAL),
    # ---- Machine learning ---------------------------------------------------
    Venue("NeurIPS", "Advances in Neural Information Processing Systems", FAMILY_NEURIPS,
          aliases=("Conference on Neural Information Processing Systems",
                   "Neural Information Processing Systems", "NIPS")),
    Venue("ICLR", "International Conference on Learning Representations", FAMILY_ICLR),
    Venue("ICML", "International Conference on Machine Learning", FAMILY_PMLR),
    Venue("AISTATS", "International Conference on Artificial Intelligence and Statistics", FAMILY_PMLR),
    Venue("UAI", "Conference on Uncertainty in Artificial Intelligence", FAMILY_PMLR),
    Venue("JMLR", "Journal of Machine Learning Research", FAMILY_JMLR, kind=JOURNAL),
    Venue("TMLR", "Transactions on Machine Learning Research", FAMILY_TMLR, kind=JOURNAL),
    # ---- AAAI / IJCAI -------------------------------------------------------
    Venue("AAAI", "AAAI Conference on Artificial Intelligence", FAMILY_AAAI),
    Venue("IJCAI", "International Joint Conference on Artificial Intelligence", FAMILY_IJCAI),
    # ---- ACM series (family: ACM) ------------------------------------------
    Venue("KDD", "ACM SIGKDD Conference on Knowledge Discovery and Data Mining", FAMILY_ACM,
          aliases=("Knowledge Discovery and Data Mining", "SIGKDD")),
    Venue("SIGIR", "International ACM SIGIR Conference on Research and Development in Information Retrieval",
          FAMILY_ACM, aliases=("Research and Development in Information Retrieval",)),
    Venue("MM", "ACM International Conference on Multimedia", FAMILY_ACM,
          aliases=("International Conference on Multimedia",)),
    Venue("WWW", "ACM Web Conference", FAMILY_ACM,
          aliases=("The Web Conference", "World Wide Web Conference")),
    Venue("CIKM", "ACM International Conference on Information and Knowledge Management", FAMILY_ACM),
    Venue("WSDM", "ACM International Conference on Web Search and Data Mining", FAMILY_ACM),
    Venue("RecSys", "ACM Conference on Recommender Systems", FAMILY_ACM),
    Venue("CHI", "ACM CHI Conference on Human Factors in Computing Systems", FAMILY_ACM,
          aliases=("Human Factors in Computing Systems",)),
    Venue("SIGMOD", "ACM SIGMOD International Conference on Management of Data", FAMILY_ACM),
    Venue("CCS", "ACM Conference on Computer and Communications Security", FAMILY_ACM),
    Venue("TOIS", "ACM Transactions on Information Systems", FAMILY_ACM, kind=JOURNAL),
    Venue("TKDD", "ACM Transactions on Knowledge Discovery from Data", FAMILY_ACM, kind=JOURNAL),
    # ---- IEEE series (family: IEEE) ----------------------------------------
    Venue("CVPR", "IEEE/CVF Conference on Computer Vision and Pattern Recognition", FAMILY_IEEE,
          aliases=("Computer Vision and Pattern Recognition",)),
    Venue("ICCV", "IEEE/CVF International Conference on Computer Vision", FAMILY_IEEE),
    Venue("ICDM", "IEEE International Conference on Data Mining", FAMILY_IEEE),
    Venue("TPAMI", "IEEE Transactions on Pattern Analysis and Machine Intelligence", FAMILY_IEEE,
          kind=JOURNAL, aliases=("Pattern Analysis and Machine Intelligence",)),
    Venue("TKDE", "IEEE Transactions on Knowledge and Data Engineering", FAMILY_IEEE,
          kind=JOURNAL, aliases=("Knowledge and Data Engineering",)),
    Venue("TNNLS", "IEEE Transactions on Neural Networks and Learning Systems", FAMILY_IEEE,
          kind=JOURNAL),
    # ---- Others -------------------------------------------------------------
    Venue("ECCV", "European Conference on Computer Vision", FAMILY_SPRINGER),
]

VENUE_INDEX = {v.abbr.lower(): v for v in VENUES}


def get_venue(abbr: str) -> Optional[Venue]:
    return VENUE_INDEX.get(abbr.strip().lower())


def families() -> List[str]:
    seen: List[str] = []
    for v in VENUES:
        if v.family not in seen:
            seen.append(v.family)
    return seen


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")


def _prep(text: str) -> str:
    """Lowercase, split camelCase boundaries, drop punctuation, squash spaces."""
    text = _CAMEL_RE.sub(" ", text)  # 'ofKDD' -> 'of KDD'
    text = text.replace("&", " and ")
    text = _NON_ALNUM_RE.sub(" ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _alias_pattern(alias: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(_prep(alias)) + r"(?![a-z0-9])")


_COMPILED = [
    (venue, len(_prep(alias)), _alias_pattern(alias))
    for venue in VENUES
    for alias in venue.match_terms()
]


def match_venue(text: str, expected_kind: Optional[str] = None) -> Optional[Tuple[Venue, bool]]:
    """Match a free-form venue string.

    Returns ``(venue, findings)`` where ``findings`` is True when the string
    mentions *Findings of …*. Returns ``None`` when nothing matches.
    """
    if not text:
        return None
    cand = " " + _prep(text) + " "
    best: Optional[Venue] = None
    best_len = 0
    for venue, length, pattern in _COMPILED:
        if expected_kind and venue.kind != expected_kind:
            continue
        if length > best_len and pattern.search(cand):
            best, best_len = venue, length
    if best is None:
        return None
    findings = " findings " in cand
    return best, findings


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_venue(venue: Venue, findings: bool = False, style: str = "abbr") -> str:
    """Render the canonical booktitle / journal text.

    ``style`` — ``abbr``: "EMNLP"; ``full``: the long official name;
    ``family``: the umbrella (e.g. "ARR", "ACM").
    """
    if style == "family":
        return venue.family
    if venue.kind == JOURNAL:
        return venue.abbr if style == "abbr" else venue.name
    if style == "full":
        prefix = "Findings of the " if findings else "Proceedings of the "
        return prefix + venue.name
    prefix = "Findings of " if findings else "Proceedings of "
    return prefix + venue.abbr


def venue_line(venue: Venue) -> str:
    """One-line human summary for ``hx-bib venues``."""
    return f"{venue.abbr:<8} {venue.family:<8} {venue.kind:<11} {venue.name}"


def search_venues(query: str) -> List[Venue]:
    q = _prep(query)
    if not q:
        return list(VENUES)
    return [
        v for v in VENUES
        if q in _prep(" ".join(v.match_terms())) or q in v.family.lower()
    ]
