"""Regression coverage for document versions, history and persisted options."""

import asyncio
import threading

import pytest

from hx_bib.gui import app
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
    state.update(verify_results=["old"], check_report="old", previews={"format": "old"})
    push_stage(state, "edit", "updated")
    assert control.cancelled.is_set() and not control.paused.is_set()
    assert state["verify_results"] is None and state["check_report"] is None
    assert state["previews"] == {} and not state["progress"]["running"]


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


def test_preview_job_discards_result_after_document_change(monkeypatch):
    state = loaded_state()
    started, release = threading.Event(), threading.Event()
    rendered = []

    class Widget:
        is_deleted = False

        def props(self, *args, **kwargs):
            pass

    def compute(before):
        started.set()
        assert release.wait(5)
        return before + " updated"

    monkeypatch.setattr(app, "_render_preview", lambda *a, **k: rendered.append(a))

    async def scenario():
        task = asyncio.create_task(app._run_preview_job(
            Widget(), Widget(), state, "en", "format", compute, None, None, "format",
        ))
        assert await asyncio.to_thread(started.wait, 5)
        invalidate_results(state)
        release.set()
        await task

    asyncio.run(scenario())
    assert not rendered and not state["previews"]


def test_newer_preview_job_wins_even_if_old_job_finishes_last(monkeypatch):
    state = loaded_state()
    started, release = threading.Event(), threading.Event()
    rendered = []

    class Widget:
        is_deleted = False

        def props(self, *args, **kwargs):
            pass

    def old_job(before):
        started.set()
        assert release.wait(5)
        return "old result"

    monkeypatch.setattr(app, "_render_preview", lambda *a, **k: rendered.append(a[4]))

    async def scenario():
        old = asyncio.create_task(app._run_preview_job(
            Widget(), Widget(), state, "en", "format", old_job, None, None, "format",
        ))
        assert await asyncio.to_thread(started.wait, 5)
        await app._run_preview_job(Widget(), Widget(), state, "en", "format",
                                   lambda before: "new result", None, None, "format")
        release.set()
        await old

    asyncio.run(scenario())
    assert rendered == ["new result"]
    assert state["previews"]["format"]["result_text"] == "new result"


@pytest.mark.parametrize("copied", [True, False])
def test_copy_notification_reflects_browser_result(monkeypatch, copied):
    notifications = []

    async def javascript(code):
        return copied

    monkeypatch.setattr(app.ui, "run_javascript", javascript)
    monkeypatch.setattr(app.ui, "notify", lambda *args, **kwargs: notifications.append(kwargs))
    asyncio.run(app._copy_text('A "quoted" title', "en"))
    assert notifications[0]["type"] == ("positive" if copied else "warning")
