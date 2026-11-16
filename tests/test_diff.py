"""Diff tests: itemized field changes and side-by-side line rows."""

from hx_bib.core.diff import diff_lines, diff_texts, split_entries


def test_detects_added_removed_modified_fields():
    before = "@article{a, author = {A}, title = {Old}, year = {2020}}"
    after = "@article{a, author = {A}, title = {New}, volume = {3}, year = {2020}}"
    diff = diff_texts(before, after)
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.kind == "modified"
    kinds = {c.field_name: c.kind for c in entry.field_changes}
    assert kinds == {"title": "modified", "volume": "added"}

    after2 = "@article{a, author = {A}, title = {Old}}"
    diff2 = diff_texts(before, after2)
    kinds = {c.field_name: c.kind for c in diff2.entries[0].field_changes}
    assert kinds == {"year": "removed"}


def test_entry_added_removed():
    diff = diff_texts("@article{a, title = {T}}", "@article{a, title = {T}}\n@article{b, title = {U}}")
    assert diff.count("added") == 1
    assert diff.entries[1].key == "b"

    diff = diff_texts("@article{a, title = {T}}\n@article{b, title = {U}}", "@article{a, title = {T}}")
    assert diff.count("removed") == 1
    assert diff.entries[1].key == "b"


def test_type_change_detected():
    diff = diff_texts(
        "@article{a, title = {T}, journal = {J}}",
        "@inproceedings{a, title = {T}, booktitle = {J}}",
    )
    assert diff.entries[0].type_changed


def test_whitespace_only_change_is_ignored():
    diff = diff_texts("@article{a, title = {A   B}}", "@article{a, title = {A B}}")
    assert diff.changed == []


def test_changed_helper():
    same = diff_texts("@article{a, title = {T}}", "@article{a, title = {T}}")
    assert same.changed == []
    changed = diff_texts("@article{a, title = {T}}", "@article{a, title = {U}}")
    assert len(changed.changed) == 1


def test_diff_lines_rows():
    rows = diff_lines("line1\nline2\n", "line1\nline2 changed\nline3\n")
    kinds = {r.kind for r in rows}
    assert "replace" in kinds          # line2 → line2 changed
    assert "insert" in kinds           # line3
    # Line numbers are tracked for both sides.
    replaced = [r for r in rows if r.kind == "replace"][0]
    assert replaced.left_no == 2 and replaced.right_no == 2
    for row in rows:
        if row.kind in ("equal", "replace", "delete"):
            assert row.left is not None
        if row.kind in ("equal", "replace", "insert"):
            assert row.right is not None


def test_diff_lines_identical():
    rows = diff_lines("a\nb\n", "a\nb\n")
    assert all(r.kind == "equal" for r in rows)


def test_diff_lines_offsets():
    rows = diff_lines("a\nb", "a\nc", left_offset=10, right_offset=20)
    assert rows[0].kind == "equal"
    assert rows[0].left_no == 11 and rows[0].right_no == 21
    assert rows[1].left_no == 12 and rows[1].right_no == 22


def test_diff_lines_moved_line_is_context_not_change():
    # A line that only moved is paired as context — position is not a change.
    before = "a = 1\nb = 2\nc = 3\n"
    after = "c = 3\na = 1\nb = 2\n"
    rows = diff_lines(before, after)
    assert all(r.kind == "equal" for r in rows)


def test_diff_lines_field_reorder_with_comma_shift():
    # Reordering moves the trailing comma with the position, not the value.
    before = (
        "@inproceedings{k,\n"
        "  pages = {1--2},\n"
        "  title = {T},\n"
        "  year = {2020}\n"
        "}\n"
    )
    after = (
        "@inproceedings{k,\n"
        "  title = {T},\n"
        "  year = {2020},\n"
        "  pages = {1--2}\n"
        "}\n"
    )
    rows = diff_lines(before, after)
    assert all(r.kind == "equal" for r in rows)


def test_diff_lines_value_change_survives_reorder():
    # Only the line whose value changed is marked; moved lines stay context.
    before = (
        "@article{a,\n"
        "  author = {X},\n"
        "  title = {Old},\n"
        "  year = {2020}\n"
        "}\n"
    )
    after = (
        "@article{a,\n"
        "  title = {New},\n"
        "  author = {X},\n"
        "  year = {2020},\n"
        "}\n"
    )
    rows = diff_lines(before, after)
    changed = [r for r in rows if r.kind != "equal"]
    assert len(changed) == 1
    assert changed[0].kind == "replace"
    assert "Old" in changed[0].left and "New" in changed[0].right


def test_split_entries_keys_and_spans():
    text = (
        "@string{x = {y}}\n"
        "@article{a,\n"
        "  title = {T}\n"
        "}\n"
        "\n"
        "@book{b, title={U}}\n"
    )
    spans = split_entries(text)
    assert [sp.key for sp in spans] == [None, "a", None, "b"]
    a = spans[1]
    assert a.start == 1
    assert a.lines[0].startswith("@article{a")
    assert a.lines[-1] == "}"
    assert spans[3].lines == ["@book{b, title={U}}"]
    assert spans[3].start == 5


def test_split_entries_nested_braces():
    text = "@article{a,\n  title = {A {nested} title}\n}\n"
    spans = split_entries(text)
    assert len(spans) == 1
    assert spans[0].key == "a"
    assert spans[0].lines[-1] == "}"
