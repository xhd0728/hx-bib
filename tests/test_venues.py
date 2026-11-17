"""Venue preset tests: matching, families (ARR / ACM rules), rendering."""

from hx_bib.core import venues as V


def _match(text):
    return V.match_venue(text)


def test_acl_series_family_is_arr():
    for abbr in ("ACL", "EMNLP", "NAACL", "EACL", "COLING", "TACL"):
        venue = V.get_venue(abbr)
        assert venue is not None
        assert venue.family == V.FAMILY_ARR
        assert V.render_venue(venue, style="family") == "ARR"


def test_acm_series_family_is_acm():
    for abbr in ("KDD", "SIGIR", "MM", "WWW", "CIKM", "WSDM", "RecSys"):
        venue = V.get_venue(abbr)
        assert venue is not None
        assert venue.family == V.FAMILY_ACM
        assert V.render_venue(venue, style="family") == "ACM"


def test_match_by_full_name():
    hit = _match("Proceedings of the 2020 Conference on Empirical Methods in Natural Language Processing")
    assert hit is not None and hit[0].abbr == "EMNLP"


def test_match_handles_missing_space_camelcase():
    hit = _match("Proceedings ofKDD 2024")
    assert hit is not None and hit[0].abbr == "KDD"


def test_findings_detected():
    hit = _match("Findings of EMNLP 2023")
    assert hit is not None
    venue, findings = hit
    assert venue.abbr == "EMNLP"
    assert findings is True
    assert V.render_venue(venue, findings, "abbr") == "Findings of EMNLP"


def test_render_styles():
    emnlp = V.get_venue("EMNLP")
    assert V.render_venue(emnlp, style="abbr") == "Proceedings of EMNLP"
    assert V.render_venue(emnlp, style="full") == (
        "Proceedings of the Conference on Empirical Methods in Natural Language Processing"
    )
    assert V.render_venue(emnlp, style="family") == "ARR"
    tacl = V.get_venue("TACL")
    assert V.render_venue(tacl, style="abbr") == "TACL"
    assert V.render_venue(tacl, style="full").startswith("Transactions of")


def test_unknown_venue_returns_none():
    assert _match("Journal of Imaginary Studies") is None
    assert _match("") is None


def test_expected_kind_filter():
    text = "Transactions of the Association for Computational Linguistics"
    # Journal-only lookup finds TACL…
    hit = V.match_venue(text, expected_kind=V.JOURNAL)
    assert hit is not None and hit[0].abbr == "TACL"
    # …while a conference lookup falls back to the ACL alias.
    hit = V.match_venue(text, expected_kind=V.CONFERENCE)
    assert hit is not None and hit[0].kind == V.CONFERENCE


def test_search_and_families():
    assert V.search_venues("retrieval")  # SIGIR / TOIS etc.
    assert V.search_venues("arr")        # matches family names too
    fams = V.families()
    assert V.FAMILY_ARR in fams and V.FAMILY_ACM in fams and V.FAMILY_IEEE in fams


def test_ieee_transactions_on_big_data_preset():
    for name in ("TBD", "IEEE Transactions on Big Data", "IEEE Trans. Big Data",
                 "IEEE Transaction of Big Data"):
        hit = V.match_venue(name, expected_kind=V.JOURNAL)
        assert hit is not None and hit[0].abbr == "TBD"
    venue = V.get_venue("TBD")
    assert venue.kind == V.JOURNAL and venue.family == V.FAMILY_IEEE
    assert V.render_venue(venue, style="full") == "IEEE Transactions on Big Data"
    assert V.search_venues("big data") == [venue]
