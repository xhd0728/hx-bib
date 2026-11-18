"""Regression coverage for document versions, history and persisted options."""

import asyncio
import threading

import pytest

from hx_bib.core.parser import parse_bibtex
from hx_bib.gui import app
from hx_bib.gui.bib_workbench import apply_options
from hx_bib.gui.workspace import (
    DEFAULT_SETTINGS,
    invalidate_results,
    jump_stage,
    merge_settings,
    new_state,
    preview_is_current,
    push_stage,
)
from hx_bib.verify import RunControl


def loaded_state():
    state = new_state()
    state.update(text="original", baseline="original")
    return state


def test_history_branch_discards_only_redo_tail():
    state = loaded_state()
    push_stage(state, "first", "one")
    push_stage(state, "second", "two")
    jump_stage(state, 1)
    push_stage(state, "branch", "three")
    assert [s["label"] for s in state["history"]] == ["first", "branch"]
    jump_stage(state, 0)
    assert state["text"] == "original"
    jump_stage(state, 99)
    assert state["text"] == "three"


def test_unchanged_apply_keeps_redo_and_does_not_add_stage():
    state = loaded_state()
    push_stage(state, "first", "one")
    jump_stage(state, 0)
    revision = state["revision"]
    assert not push_stage(state, "unchanged", "original")
    assert state["pointer"] == 0 and len(state["history"]) == 1
    assert state["revision"] == revision


def test_document_change_cancels_paused_verification_and_clears_reports():
    state = loaded_state()
    control = state["run_control"] = RunControl()
    control.pause()
    state.update(verify_results=["old"], check_report="old")
    push_stage(state, "edit", "updated")
    assert control.cancelled.is_set() and not control.paused.is_set()
    assert state["verify_results"] is None and state["check_report"] is None
    assert not state["progress"]["running"]


def test_undo_to_identical_text_still_invalidates_old_revision():
    state = loaded_state()
    revision = state["revision"]
    push_stage(state, "edit", "changed")
    jump_stage(state, 0)
    assert not preview_is_current(state, revision, "original")


def test_settings_reject_invalid_shapes_enums_and_nonfinite_numbers():
    settings = merge_settings(DEFAULT_SETTINGS, {
        "lang": "unknown", "format": {"indent": [], "align_equals": "false"},
        "clean": None, "normalize": {"venue_style": "oops"},
        "verify": {"workers": float("nan"), "delay": float("inf")},
        "unexpected": "ignored",
    })
    assert settings == DEFAULT_SETTINGS
    settings["clean"]["remove"].append("title")
    assert DEFAULT_SETTINGS["clean"]["remove"] == []


def test_settings_preserve_zero_delay_and_clamp_workers():
    settings = merge_settings(DEFAULT_SETTINGS, {"verify": {"delay": 0, "workers": 99}})
    assert settings["verify"]["delay"] == 0 and settings["verify"]["workers"] == 8
    assert merge_settings(DEFAULT_SETTINGS, {"verify": {"workers": -1}})["verify"]["workers"] == 1


def test_states_do_not_share_mutable_defaults():
    first, second = new_state(), new_state()
    first["settings"]["clean"]["remove"].append("title")
    first["drafts"]["import_text"] = "draft"
    assert not second["settings"]["clean"]["remove"] and not second["drafts"]


def test_upload_decoder_strips_bom_and_supports_legacy_bytes():
    assert app._read_file_bytes(b"\xef\xbb\xbf@article{a}") == "@article{a}"
    assert app._read_file_bytes(b"caf\xe9") == "caf\xe9"


def test_apply_runs_selected_tools_in_one_pass_and_validates_result():
    settings = merge_settings(DEFAULT_SETTINGS, {
        "enabled": {"clean": True, "normalize": True, "venues": True, "check": True},
        "clean": {"remove": ["abstract"], "pages": True},
        "format": {"indent": "2"},
        "normalize": {"venue_style": "abbr"},
        "venues": {"preset_style": "full"},
    })
    source = "@article{x, title={T}, journal={TBD}, year={2024}, author={Doe}, pages={1-3}, abstract={Text}}"
    after, report = apply_options(source, settings, {})
    entry = parse_bibtex(after).entries[0]
    assert entry.get("journal") == "IEEE Transactions on Big Data"
    assert entry.get("pages") == "1--3"
    assert not entry.has("abstract") and "\n  author " in after
    assert not report.issues


def test_validation_only_can_report_malformed_input_without_editing_it():
    source = "@article{first, title={Hello}}\n@article{unterminated, title={"
    settings = merge_settings(DEFAULT_SETTINGS, {"enabled": {"format": False, "check": True}})
    after, report = apply_options(source, settings, {})
    assert after == source and any(issue.code == "parse" for issue in report.issues)
    settings["enabled"]["format"] = True
    with pytest.raises(ValueError, match="bib_parse_first"):
        apply_options(source, settings, {})


def test_apply_rejects_field_values_that_would_change_the_parsed_structure():
    source = "@article{first, title={Hello}}"
    settings = merge_settings(DEFAULT_SETTINGS, {"enabled": {"clean": True}})
    with pytest.raises(ValueError, match="bib_result_invalid"):
        apply_options(source, settings, {"clean_add_key": "note", "clean_add_value": "x}, extra={y"})


def test_single_apply_has_one_history_step_and_preserves_arxiv_results(monkeypatch):
    state = loaded_state()
    state.update(arxiv_results=["evidence"], progress={"scope": "arxiv", "running": True}, run_control=RunControl())
    monkeypatch.setattr(app, "apply_options", lambda source, settings, drafts: ("combined", None))
    assert asyncio.run(app._apply_bib_options(state, "en"))
    assert state["text"] == "combined" and state["diff_before"] == "original"
    assert len(state["history"]) == 1 and state["arxiv_results"] == ["evidence"]
    assert not state["run_control"].cancelled.is_set()
    jump_stage(state, 0)
    assert state["text"] == "original" and state["diff_before"] is None
    jump_stage(state, 1)
    assert state["diff_before"] == "original"
    invalidate_results(state)
    assert state["arxiv_results"] is None and state["run_control"] is None


def test_pending_apply_does_not_write_into_replaced_document(monkeypatch):
    state = loaded_state()
    started, release = threading.Event(), threading.Event()

    def compute(source, settings, drafts):
        started.set()
        assert release.wait(5)
        return "obsolete", None

    monkeypatch.setattr(app, "apply_options", compute)

    async def scenario():
        task = asyncio.create_task(app._apply_bib_options(state, "en"))
        assert await asyncio.to_thread(started.wait, 5)
        invalidate_results(state)
        state["text"] = "new file"
        release.set()
        assert not await task

    asyncio.run(scenario())
    assert state["text"] == "new file" and not state["history"]


@pytest.mark.parametrize("copied", [True, False])
def test_copy_notification_reflects_browser_result(monkeypatch, copied):
    notifications = []

    async def javascript(code):
        return copied

    monkeypatch.setattr(app.ui, "run_javascript", javascript)
    monkeypatch.setattr(app.ui, "notify", lambda *args, **kwargs: notifications.append(kwargs))
    asyncio.run(app._copy_text('A "quoted" title', "en"))
    assert notifications[0]["type"] == ("positive" if copied else "warning")
