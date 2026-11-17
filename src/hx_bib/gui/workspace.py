"""Page-local workspace state, independent of NiceGUI widgets."""

from __future__ import annotations

import math
from copy import deepcopy

DEFAULT_SETTINGS = {
    "lang": "zh",
    "format": {
        "indent": "4", "field_order": "standard", "sort": "none",
        "align_equals": False, "trailing_comma": False, "drop_comments": False,
    },
    "clean": {
        "remove": [], "drop_empty": False, "drop_others": False, "months": False,
        "title_period": False, "dedupe": False, "keep_last": False,
        "doi": False, "pages": False, "clean_urls": False, "dedupe_title": False,
    },
    "normalize": {
        "venue_style": "abbr", "arxiv_style": "journal", "fix_types": True,
        "drop_arxiv_volume": True, "add_arxiv_url": True,
    },
    "verify": {"delay": 0.8, "workers": 1, "use_cache": True},
    "check": {
        "required_fields": True, "check_venues": True, "check_months": True,
        "check_title_period": True, "check_key_style": True,
    },
}

_CHOICES = {
    "lang": {"zh", "en"},
    "indent": {"0", "2", "4", "tab", "align"},
    "field_order": {"standard", "alphabetical", "keep"},
    "sort": {"none", "key", "year", "type", "title"},
    "venue_style": {"abbr", "full", "family", "keep"},
    "arxiv_style": {"journal", "misc", "keep"},
}


def merge_settings(base: dict, incoming: object) -> dict:
    """Ignore malformed/stale localStorage values and never alias defaults."""
    out = deepcopy(base)
    if not isinstance(incoming, dict):
        return out
    for key, default in base.items():
        value = incoming.get(key, default)
        if isinstance(default, dict):
            out[key] = merge_settings(default, value)
        elif key in _CHOICES:
            if isinstance(value, str) and value in _CHOICES[key]:
                out[key] = value
        elif isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(default, list):
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                out[key] = list(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value):
                if key == "workers":
                    out[key] = max(1, min(8, int(value)))
                elif key == "delay":
                    out[key] = max(0.0, float(value))
    return out


def new_state() -> dict:
    return {
        "lang": "zh", "mode": "tools", "text": "", "baseline": "",
        "name": "", "path": "", "history": [], "pointer": 0,
        "revision": 0, "previews": {}, "preview_jobs": {}, "drafts": {},
        "verify_results": None, "verify_filter": "ALL",
        "check_report": None, "check_filter": "ALL", "check_config": None, "check_job": None,
        "progress": idle_progress(), "run_control": None,
        "tab": "format", "venues_only": False,
        "source_text": "", "amp": "space", "blank_lines": True,
        "row_indent": 0, "result": None,
        "settings": merge_settings(DEFAULT_SETTINGS, {}), "settings_dirty": False,
    }


def idle_progress() -> dict:
    return {"done": 0, "total": 0, "key": "", "running": False, "paused": False}


def invalidate_results(state: dict) -> None:
    """Detach outstanding jobs before a document/history change."""
    if state["run_control"] is not None:
        state["run_control"].cancel()
    state.update(
        revision=state["revision"] + 1, previews={}, preview_jobs={}, preview_controls={},
        check_report=None, check_config=None, check_job=None, verify_results=None, check_filter="ALL",
        verify_filter="ALL", run_control=None, progress=idle_progress(),
    )


def push_stage(state: dict, label: str, text: str) -> bool:
    if state["text"] == text:
        return False
    history = state["history"][:state["pointer"]]
    history.append({"label": label, "text": text})
    invalidate_results(state)
    state.update(history=history, pointer=len(history), text=text)
    return True


def jump_stage(state: dict, pointer: int) -> None:
    pointer = max(0, min(pointer, len(state["history"])))
    if pointer == state["pointer"]:
        return
    invalidate_results(state)
    state["pointer"] = pointer
    state["text"] = state["history"][pointer - 1]["text"] if pointer else state["baseline"]


def preview_is_current(state: dict, revision: int, before: str) -> bool:
    return revision == state["revision"] and before == state["text"]
