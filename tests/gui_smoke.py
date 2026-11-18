"""Headless GUI smoke test (NiceGUI user simulation, no browser needed).

Run manually:
    PYTHONPATH=src python3 tests/gui_smoke.py

Exercises the BibTeX workbench, verification, and LaTeX tools in one session.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

os.environ["NICEGUI_USER_SIMULATION"] = "true"

from nicegui.testing.user_simulation import user_simulation  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
SAMPLE = REPO / "examples" / "custom.bib"
MAIN = REPO / "tests" / "_gui_main.py"


async def settle(seconds: float = 0.25) -> None:
    """Give the simulation a beat so pending re-renders finish (no stale clicks)."""
    await asyncio.sleep(seconds)


def open_section(user, marker):
    next(iter(user.find(marker=marker).elements)).open()


async def main() -> None:
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/")
        await user.should_see("hx-bib")
        await user.should_see("上传 BibTeX 文件")
        print("✓ upload screen renders")

        # Venues browser works without loading any file
        user.find(marker="browse-venues").click()
        await settle()
        await user.should_see(marker="venues-back")
        await user.should_see("个预设")
        await user.should_see(marker="venue-TBD-full")
        await user.should_not_see(marker="venue-normalize-preview")
        user.find(marker="venues-back").click()
        await settle()
        await user.should_see(marker="browse-venues")
        print("✓ venues browser opens without a file")

        open_section(user, "import-path")
        user.find(marker="path-input").type(str(SAMPLE))
        user.find(marker="load-btn").click()
        await settle()
        await user.should_see("custom.bib")
        await user.should_see("工作副本")
        for key in ("format", "check", "verify", "clean", "normalize", "venues"):
            await user.should_see(marker=f"options-{key}")
        await user.should_see(marker="action-arxiv")
        await user.should_see(marker="diff-empty")
        await user.should_not_see(marker="bib-diff-entry")
        mode = next(iter(user.find(marker="bib-diff-mode").elements))
        mode.set_value("all")
        await user.should_see(marker="bib-diff-entry")
        await user.should_see(marker="diff-0-copy-before")
        await user.should_see(marker="diff-0-copy-after")
        mode.set_value("changed")
        await user.should_not_see(marker="bib-diff-entry")

        for key in ("clean", "format", "check"):
            open_section(user, f"options-{key}")
        user.find(marker="enable-clean").click()
        user.find(marker="clean-custom_remove").type("address")
        user.find(marker="format-option-align_equals").click()
        user.find(marker="enable-check").click()
        await user.should_not_see(marker="bib-diff-entry")

        user.find(marker="apply-btn").click()
        await user.should_see("已暂存 1 步", retries=20)
        await user.should_see(marker="bib-diff-entry")
        await user.should_see(marker="check-download")
        assert next(iter(user.find(marker="options-format").elements)).value
        assert next(iter(user.find(marker="options-clean").elements)).value
        assert next(iter(user.find(marker="clean-custom_remove").elements)).value == "address"
        assert "0 修改" not in next(iter(user.find(marker="diff-count").elements)).text
        print("✓ peer settings apply together, with entry cards and a real text diff")

        user.find(marker="undo-btn").click()
        await user.should_see("原始文件")
        await user.should_not_see(marker="bib-diff-entry")
        user.find(marker="redo-btn").click()
        await user.should_see("已暂存 1 步")
        await user.should_see(marker="bib-diff-entry")
        print("✓ undo and redo refresh entry diffs")

        open_section(user, "options-venues")
        query = next(iter(user.find(marker="venue-search").elements))
        query.set_value("Big Data")
        next(iter(user.find(marker="venue-family").elements)).set_value("IEEE")
        next(iter(user.find(marker="venue-kind").elements)).set_value("journal")
        await user.should_see(marker="venue-TBD-full")
        await user.should_not_see(marker="venue-ACL-full")
        user.find(marker="venue-used-only").click()
        await user.should_see("没有匹配的会议 / 期刊")
        user.find(marker="venue-clear-filters").click()
        query.set_value("EMNLP")
        next(iter(user.find(marker="venues-preset_style").elements)).set_value("full")
        user.find(marker="enable-venues").click()
        user.find(marker="apply-btn").click()
        await user.should_see("已暂存 2 步", retries=20)
        assert next(iter(user.find(marker="venue-search").elements)).value == "EMNLP"
        print("✓ venue search, filters, and style are inline and included in Apply")

        user.find("中 / EN").click()
        await user.should_see("Clean up")
        await user.should_see("Entry diff")
        await user.should_see("Show all")
        await user.should_see("Working copy")
        print("✓ language toggle works (zh → en)")

        # Fresh page: paste a .bib text directly (no file involved).
        await user.open("/")
        await user.should_see("上传 BibTeX 文件")
        user.find(marker="paste-input").type("not a bibliography")
        user.find(marker="paste-load").click()
        await settle()
        await user.should_see("未识别到 BibTeX 条目")
        await user.should_see("上传 BibTeX 文件")
        user.find(marker="paste-input").clear()
        user.find(marker="paste-input").type(
            "@article{pasted1, title = {Pasted Entry}, year = {2024}}"
        )
        user.find(marker="paste-load").click()
        await settle()
        await user.should_see("pasted.bib")
        await user.should_see("工作副本")
        print("✓ pasted BibTeX content imported")

        # LaTeX table formatter page (/table).
        await user.open("/table")
        await user.should_see("LaTeX 表格格式化")
        assert not next(iter(user.find(marker="table-copy").elements)).enabled
        user.find(marker="table-example").click()
        await settle()
        user.find(marker="table-format").click()
        await user.should_see(marker="table-details", retries=20)
        await user.should_see(marker="table-copy-before")
        assert not next(iter(user.find(marker="table-advanced").elements)).value
        assert not next(iter(user.find(marker="table-details").elements)).value
        open_section(user, "table-details")
        await user.should_see(marker="table-item-copy-before")
        await user.should_see(marker="table-item-copy-after")
        print("✓ table formatter: example loads, formats and compares")

        # Formatting only refreshes output: textarea widget and focus survive.
        input_widget = next(iter(user.find(marker="table-input").elements))
        user.find(marker="table-format").click()
        await settle()
        assert next(iter(user.find(marker="table-input").elements)) is input_widget
        next(iter(user.find(marker="table-amp").elements)).set_value("align")
        await settle()
        await user.should_see("输入或选项已变化")
        assert not next(iter(user.find(marker="table-copy").elements)).enabled
        user.find(marker="table-format").click()
        await settle()
        assert next(iter(user.find(marker="table-copy").elements)).enabled
        print("✓ table input stays mounted and stale output cannot be exported")

        user.find(marker="table-input").clear()
        user.find(marker="table-input").type("\\begin{tabular}{cc} a & b \\\\ \\end{tabular}")
        user.find(marker="table-format").click()
        await settle()
        await user.should_see("个表格")
        print("✓ table formatter: pasted table formats")

        # Both workspaces stay mounted with their current view and input.
        result_widget = next(iter(user.find(marker="table-compare").elements))
        pasted_table = input_widget.value
        user.find(marker="nav-tools").click()
        await user.should_see("上传 BibTeX 文件")
        user.find(marker="bib-example").click()
        await user.should_see(marker="options-check")
        open_section(user, "options-check")
        user.find(marker="enable-check").click()
        user.find(marker="apply-btn").click()
        await user.should_see(marker="check-download", retries=20)
        user.find(marker="nav-table").click()
        await user.should_see("LaTeX 表格格式化")
        assert next(iter(user.find(marker="table-input").elements)) is input_widget
        assert input_widget.value == pasted_table
        assert next(iter(user.find(marker="table-compare").elements)) is result_widget
        assert next(iter(user.find(marker="table-download").elements)).enabled
        user.find(marker="nav-tools").click()
        await user.should_see(marker="check-download")
        user.find(marker="nav-table").click()
        await user.should_see(marker="table-input")
        print("✓ both workspaces keep their input, options, report and active view")

        from nicegui.elements.upload_files import SmallFileUpload

        uploader = next(iter(user.find(marker="table-import").elements))
        imported = "\\documentclass{article}\n" + pasted_table
        await uploader.handle_uploads([SmallFileUpload("example.tex", "text/plain", imported.encode("utf-8-sig"))])
        await user.should_see("已导入：example.tex")
        assert input_widget.value == imported
        assert not next(iter(user.find(marker="table-download").elements)).enabled
        await uploader.handle_uploads([SmallFileUpload("empty.tex", "text/plain", b" ")])
        await user.should_see("文件为空或不是可读取的文本，编辑区未改动。")
        assert input_widget.value == imported
        user.find(marker="table-format").click()
        await user.should_see("结果与当前输入和选项一致。", retries=20)
        assert next(iter(user.find(marker="table-download").elements)).enabled

        input_widget.set_value("LaTeX without a table")
        user.find(marker="table-format").click()
        await user.should_see("没有找到 tabular / tabularx / longtable 等表格环境", retries=20)
        assert not next(iter(user.find(marker="table-copy").elements)).enabled
        assert not next(iter(user.find(marker="table-download").elements)).enabled
        print("✓ .tex import, invalid input protection and export guards")

        await user.open("/")
        user.find(marker="bib-example").click()
        await user.should_see(marker="apply-btn")
        user.find(marker="apply-btn").click()
        await user.should_see("已暂存 1 步", retries=20)
        user.find(marker="apply-btn").click()
        await user.should_see("没有变化", retries=20)
        await user.should_see("已暂存 1 步")
        print("✓ unchanged output does not add an undo step")

        # Exercise the real GUI callbacks with a gated, offline verifier.
        from hx_bib.gui import app as gui_app
        from hx_bib.verify.checker import CheckResult

        saved = gui_app._new_state, gui_app.prefetch_arxiv, gui_app.verify_entries
        state = gui_app._new_state()
        state["drafts"]["verify_keys"] = "missing-citekey"
        started, release = threading.Event(), threading.Event()
        requested = []

        def gated_verify(net, entries, **kwargs):
            requested.append([entry.key for entry in entries])
            started.set()
            assert release.wait(5)
            return [CheckResult(
                entry=entries[0], verdict="FAIL", issues=[("FAIL", "Title mismatch")],
                arxiv_id=gui_app.extract_arxiv_id(entries[0]), publication=("not_found", ""),
            )]

        gui_app._new_state = lambda: state
        gui_app.prefetch_arxiv = lambda *args: None
        gui_app.verify_entries = gated_verify
        try:
            await user.open("/")
            user.find(marker="bib-example").click()
            await settle()
            open_section(user, "options-verify")
            user.find(marker="action-verify").click()
            await user.should_see("以下引用 key 不存在：missing-citekey")
            await user.should_see("并发线程数")
            assert not next(iter(user.find(marker="verify-download").elements)).enabled
            keys = user.find(marker="verify-keys")
            assert not started.is_set()
            entry = gui_app.parse_bibtex(state["text"]).entries[0]
            keys.clear()
            keys.type(entry.key)
            user.find(marker="action-verify").click()
            assert await asyncio.to_thread(started.wait, 5)
            assert requested == [[entry.key]]
            user.find(marker="verify-pause").click()
            await settle()
            assert state["progress"]["paused"]
            user.find(marker="nav-table").click()
            await settle()
            user.find(marker="nav-tools").click()
            await settle()
            assert next(iter(user.find(marker="verify-pause").elements)).enabled
            await user.should_see("继续")
            user.find(marker="verify-pause").click()
            await settle()
            assert not state["progress"]["paused"]
            user.find(marker="verify-stop").click()
            await user.should_see("正在停止")
            assert not next(iter(user.find(marker="verify-stop").elements)).enabled
            release.set()
            await user.should_see("已停止，仅显示已完成的 1 条结果", retries=20)
            await user.should_see("Title mismatch")
            assert next(iter(user.find(marker="verify-download").elements)).enabled
            print("✓ online scope, pause/resume/stop, and per-entry verification notes")

            started.clear()
            release.clear()
            user.find(marker="action-verify").click()
            assert await asyncio.to_thread(started.wait, 5)
            old_control = state["run_control"]
            user.find(marker="apply-btn").click()
            await settle()
            assert old_control.cancelled.is_set()
            release.set()
            await settle(0.5)
            assert state["verify_results"] is None and not state["progress"]["running"]
            print("✓ verification controls survive mode switches; document edits cancel stale runs")

            state = saved[0]()
            await user.open("/")
            user.find(marker="paste-input").type(
                "@article{preprint, title={Example}, eprint={2401.01234}}\n"
                "@article{journal, title={A journal article}}"
            )
            user.find(marker="paste-load").click()
            await user.should_see(marker="action-arxiv")
            started.clear()
            release.clear()
            user.find(marker="action-arxiv").click()
            assert await asyncio.to_thread(started.wait, 5)
            assert requested[-1] == ["preprint"]
            control = state["run_control"]
            user.find(marker="apply-btn").click()
            await user.should_see("已暂存 1 步", retries=20)
            assert state["run_control"] is control and not control.cancelled.is_set()
            release.set()
            await user.should_see("未找到证据", retries=20)
            await user.should_see(marker="arxiv-result")
            assert state["verify_results"] is None
            print("✓ independent arXiv run survives Apply and annotates the right entry")
        finally:
            release.set()
            gui_app._new_state, gui_app.prefetch_arxiv, gui_app.verify_entries = saved

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
