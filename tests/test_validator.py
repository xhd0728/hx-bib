"""Validator tests: required fields, value shapes, placeholders, style traps."""

from hx_bib.core.validator import (
    ERROR,
    INFO,
    WARNING,
    ValidateOptions,
    has_errors,
    summary,
    validate_document,
    validate_text,
)
from hx_bib.core.parser import parse_bibtex

CLEAN = """@inproceedings{vaswani2017attention,
  author = {Ashish Vaswani and Noam Shazeer},
  title = {Attention is All You Need},
  booktitle = {NeurIPS},
  year = {2017},
  pages = {5998--6008}
}
"""


def _issues(text, sev=None):
    report = validate_text(text)
    return [i for i in report.issues if sev is None or i.severity == sev]


def test_clean_entry_has_no_errors():
    report = validate_text(CLEAN)
    assert report.errors == []
    assert not has_errors(report)


def test_missing_required_field():
    issues = _issues("@inproceedings{x, title = {T}, year = {2020}}", ERROR)
    missing = {i.field_name for i in issues}
    assert "author" in missing and "booktitle" in missing
    assert has_errors(validate_text("@inproceedings{x, title = {T}, year = {2020}}"))


def test_empty_required_field():
    issues = _issues("@article{x, author = {A}, title = {}, journal = {J}, year = {2020}}", ERROR)
    assert any("empty" in i.message for i in issues)


def test_bad_year_and_pages():
    issues = _issues("@article{x, author = {A}, title = {T}, journal = {J}, year = {20XX}}")
    assert any(i.field_name == "year" and i.severity == WARNING for i in issues)
    issues = _issues(
        "@article{x, author = {A}, title = {T}, journal = {J}, year = {2020}, pages = {12-}",
    )
    assert any(i.field_name == "pages" for i in issues)


def test_placeholder_venue_warns():
    issues = _issues("@inproceedings{x, author = {A}, title = {T}, booktitle = {TBD}, year = {2020}}")
    assert any(i.severity == WARNING and "placeholder" in i.message for i in issues)


def test_tbd_journal_abbreviation_is_not_a_placeholder_or_unknown_venue():
    for name in ("TBD", "tbd", "IEEE Transactions on Big Data", "IEEE Trans. Big Data"):
        report = validate_text(
            "@article{x, author={A}, title={T}, journal={" + name + "}, year={2020}}"
        )
        assert not report.issues, name


def test_unambiguous_journal_placeholders_still_warn():
    for name in ("TODO", "unknown", "to be determined"):
        issues = _issues("@article{x, author={A}, title={T}, journal={" + name + "}, year={2020}}")
        assert any(i.severity == WARNING and "placeholder" in i.message for i in issues), name


def test_unbalanced_braces_error():
    # The parser tolerates this; the validator must flag it.
    doc = parse_bibtex("@article{x, author = {A}, title = {T}, journal = {J}, year = {2020}")
    report = validate_document(doc)
    assert len(report.errors) > 0


def test_duplicate_keys_error():
    report = validate_text("@misc{a, url = {https://x.org}}\n@misc{a, url = {https://x.org}}")
    assert any("duplicate" in i.message for i in report.issues if i.severity == ERROR)


def test_and_others_info():
    issues = _issues(
        "@article{x, author = {A and others}, title = {T}, journal = {J}, year = {2020}}", INFO
    )
    assert any("others" in i.message for i in issues)


def test_full_month_info():
    issues = _issues(
        "@article{x, author = {A}, title = {T}, journal = {J}, year = {2020}, month = {January}}",
        INFO,
    )
    assert any(i.field_name == "month" for i in issues)


def test_title_period_info():
    issues = _issues(
        "@article{x, author = {A}, title = {Trailing period.}, journal = {J}, year = {2020}}", INFO
    )
    assert any(i.field_name == "title" for i in issues)


def test_article_with_booktitle_warns():
    issues = _issues(
        "@article{x, author = {A}, title = {T}, booktitle = {EMNLP}, year = {2020}}", WARNING
    )
    assert any("inproceedings" in i.message for i in issues)


def test_arxiv_suggestions():
    issues = _issues(
        "@article{x, author = {A}, title = {T}, journal = {arXiv preprint arXiv:2411.13154}, "
        "year = {2024}}",
        INFO,
    )
    assert any(i.field_name == "eprint" for i in issues)


def test_options_toggle_checks():
    text = "@article{x, author = {A}, title = {T.}, journal = {Mystery Journal}, year = {2020}}"
    loose = validate_text(text, ValidateOptions(check_venues=False, check_title_period=False))
    assert not any(i.field_name == "title" for i in loose.issues)
    strict = validate_text(text)
    assert any(i.field_name == "title" for i in strict.issues)
    assert any(i.field_name == "journal" for i in strict.issues)


def test_report_helper_properties():
    report = validate_text(CLEAN)
    assert report.count(WARNING) == len(report.warnings)
    assert isinstance(summary(report), str) and summary(report)
