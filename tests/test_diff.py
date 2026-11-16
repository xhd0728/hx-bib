"""Diff tests: itemized field changes and side-by-side line rows."""

from hx_bib.core.diff import diff_lines, diff_texts


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
