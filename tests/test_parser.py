"""Parser tests: tolerant parsing, field fidelity, structure preservation."""

from hx_bib.core.parser import parse_bibtex


def _one(text):
    doc = parse_bibtex(text)
    assert len(doc.entries) == 1
    return doc.entries[0]


def test_basic_entry():
    entry = _one("@article{smith2020, title = {A Title}, year = {2020}}")
    assert entry.type == "article"
    assert entry.key == "smith2020"
    assert entry.get("title") == "A Title"
    assert entry.get("year") == "2020"


def test_field_order_preserved():
    entry = _one("@article{a, year = {2020}, title = {T}, author = {X}}")
    assert list(entry.fields) == ["year", "title", "author"]


def test_nested_braces_kept():
    entry = _one("@article{a, title = {The {GPT}-{4} model}}")
    assert entry.get("title") == "The {GPT}-{4} model"


def test_quoted_values_and_concat():
    entry = _one('@article{a, title = "A" # " B", note = "quoted, with comma"}')
    assert entry.get("title") == "A B"
    assert entry.get("note") == "quoted, with comma"


def test_bare_fields_and_string_macros():
    entry = _one("@article{a, month = jan, year = 2020}")
    assert "month" in entry.bare_fields
    assert "year" in entry.bare_fields
    assert entry.get("month") == "jan"


def test_case_insensitive_field_access():
    entry = _one("@article{a, Title = {T}}")
    assert entry.get("title") == "T"
    assert entry.has("TITLE")
    entry.set("TITLE", "U")
    assert entry.get("title") == "U"


def test_at_string_preamble_comment_preserved():
    doc = parse_bibtex(
        "@comment{just a comment}\n"
        '@string{acm = "ACM"}\n'
        "@preamble{hello}\n"
        "@inproceedings{k, title = {T}}\n"
    )
    assert len(doc.comments) == 1
    assert len(doc.strings) == 1
    assert len(doc.preamble) == 1
    assert [e.key for e in doc.entries] == ["k"]


def test_multiple_entries_and_duplicate_keys():
    doc = parse_bibtex("@article{a, x = {1}}\n@article{b, x = {2}}\n@article{a, x = {3}}")
    assert doc.keys() == ["a", "b", "a"]
    assert doc.duplicate_keys() == {"a": 2}


def test_tolerant_on_unterminated_block():
    doc = parse_bibtex("@article{broken, title = {No closing brace")
    # Never raises; the damage is recorded as a warning.
    assert doc.warnings


def test_garbage_input_never_raises():
    for text in ("", "not bibtex at all", "@article", "@{}{}{}", "@article{x}"):
        parse_bibtex(text)  # must not raise
