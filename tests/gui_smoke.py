"""Headless GUI smoke test (NiceGUI user simulation, no browser needed).

Run manually:
    PYTHONPATH=src python3 tests/gui_smoke.py

It loads examples/custom.bib via the path input, walks the preview views
(itemized / diff / per-entry), applies to the working copy, checks
undo/redo staging, the clean / normalize / validate tabs, the venues
browser (with and without a loaded file) and the language toggle.
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

        user.find(marker="import-path").click()
        user.find(marker="path-input").type(str(SAMPLE))
        user.find(marker="load-btn").click()
        await settle()
        await user.should_see("custom.bib")
        await user.should_see("工作副本")
        await user.should_see("格式化")
        print("✓ file loaded from path (status bar shows working copy)")

        user.find(marker="tab-format").click()
        user.find(marker="format-preview").click()
        await settle()
        await user.should_see("逐项对照")
        await user.should_see("应用到工作副本")
        print("✓ format preview (itemized view) rendered")

        user.find(marker="view-mode").trigger("update:model-value", 1)
        await settle()
        await user.should_see("按条目分组")
        user.find(marker="view-mode").trigger("update:model-value", 2)
        await settle()
        await user.should_see("每条结果独立成卡")
        user.find(marker="view-mode").trigger("update:model-value", 0)
        print("✓ all three preview views switch correctly")

        # Mode changes preserve the preview instead of rebuilding it empty.
        nav_widget = next(iter(user.find(marker="nav-table").elements))
        user.find(marker="nav-table").click()
        await settle()
        assert next(iter(user.find(marker="nav-table").elements)) is nav_widget
        user.find(marker="nav-tools").click()
        await settle()
        await user.should_see(marker="apply-btn")
        print("✓ preview survives tool mode changes")

        user.find("等号对齐").click()
        await settle()
        await user.should_see("选项已变化")
        assert not next(iter(user.find(marker="apply-btn").elements)).enabled
        user.find(marker="format-preview").click()
        await settle()
        assert next(iter(user.find(marker="apply-btn").elements)).enabled
        print("✓ changed options invalidate Apply until the preview is regenerated")

        user.find(marker="apply-btn").click()
        await settle()
        await user.should_see("已暂存 ")

        user.find(marker="undo-btn").click()
        await settle()
        await user.should_see("原始文件")
        print("✓ apply → staged step → undo works")

        user.find(marker="redo-btn").click()
        await settle()
        await user.should_see("已暂存 ")
        print("✓ redo works")

        user.find(marker="tab-clean").click()
        await settle()
        user.find("自定义删除（逗号分隔）").type("abstract")
        user.find(marker="nav-table").click()
        await settle()
        user.find(marker="nav-tools").click()
        await settle()
        assert next(iter(user.find("自定义删除（逗号分隔）").elements)).value == "abstract"
        user.find(marker="clean-preview").click()
        await settle()
        await user.should_see("逐项对照")
        print("✓ clean tab works")

        user.find(marker="tab-normalize").click()
        await settle()
        user.find(marker="normalize-preview").click()
        await settle()
        await user.should_see("逐项对照")
        print("✓ normalize tab works")

        user.find(marker="tab-check").click()
        await settle()
        await user.should_see("接下来怎么处理")
        await user.should_see("查看 BibTeX 中需要处理的问题")
        assert not next(iter(user.find(marker="check-show-results").elements)).enabled
        user.find(marker="run-check").click()
        await settle()
        await user.should_see("格式校验")
        await user.should_see(marker="check-filter")
        await user.should_see(marker="check-download")
        assert next(iter(user.find(marker="check-show-results").elements)).enabled
        user.find(marker="check-filter").trigger("update:model-value", 1)
        await settle()
        user.find(marker="check-option-check_venues").click()
        await settle()
        await user.should_see("检查项已变化")
        assert not next(iter(user.find(marker="check-download").elements)).enabled
        user.find(marker="run-check").click()
        await settle()
        assert next(iter(user.find(marker="check-download").elements)).enabled
        user.find(marker="check-goto-clean").click()
        await settle()
        await user.should_see(marker="clean-preview")
        user.find(marker="tab-check").click()
        await settle()
        await user.should_see(marker="check-filter")
        assert not next(iter(user.find(marker="check-option-check_venues").elements)).value
        print("✓ validation options, filters, stale report protection and repair shortcuts work")

        user.find(marker="tab-verify").click()
        await settle()
        await user.should_see("并发线程数")
        await user.should_see("开始核查")
        print("✓ verify tab shows workers / pause controls")

        user.find(marker="tab-venues").click()
        await settle()
        await user.should_see("个预设")
        query = next(iter(user.find(marker="venue-search").elements))
        family = next(iter(user.find(marker="venue-family").elements))
        kind = next(iter(user.find(marker="venue-kind").elements))
        query.set_value("Big Data")
        family.set_value("IEEE")
        kind.set_value("journal")
        await user.should_see(marker="venue-TBD-full")
        await user.should_not_see(marker="venue-ACL-full")
        user.find(marker="venue-used-only").click()
        await user.should_see("没有匹配的会议 / 期刊")
        user.find(marker="venue-clear-filters").click()
        await user.should_see(marker="venue-ACL-full")
        assert next(iter(user.find(marker="venue-search").elements)) is query
        query.set_value("EMNLP")
        user.find(marker="tab-check").click()
        await settle()
        user.find(marker="tab-venues").click()
        await user.should_see(marker="venue-EMNLP-full")
        await user.should_not_see(marker="venue-ACL-full")

        next(iter(user.find(marker="venue-view").elements)).set_value("preview")
        await user.should_see("规范化工作副本")
        style = next(iter(user.find(marker="venue-norm-style").elements))
        style.set_value("full")
        user.find(marker="venue-normalize-preview").click()
        await user.should_see(marker="apply-btn", retries=20)
        apply = next(iter(user.find(marker="apply-btn").elements))
        assert apply.enabled
        style.set_value("family")
        assert not apply.enabled
        style.set_value("full")
        assert apply.enabled
        user.find(marker="apply-btn").click()
        await user.should_see("会刊写法规范化")
        user.find(marker="undo-btn").click()
        await user.should_not_see(marker="apply-btn")
        print("✓ venue search, filters, persisted catalog and preview/apply/undo")

        user.find("中 / EN").click()
        await user.should_see("Format")
        await user.should_see("Verify")
        await user.should_see("Working copy")
        print("✓ language toggle works (zh → en)")

        # Fresh page: paste a .bib text directly (no file involved).
        await user.open("/")
        await user.should_see("上传 BibTeX 文件")
        user.find(marker="import-paste").click()
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
        await user.should_see("个表格", retries=20)
        await user.should_see("格式化结果")
        view = next(iter(user.find(marker="table-view").elements))
        view.set_value("compare")
        await user.should_see("修改前")
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
        await user.should_see(marker="tab-check")
        user.find(marker="tab-check").click()
        await user.should_see(marker="run-check")
        user.find(marker="run-check").click()
        await user.should_see(marker="check-filter", retries=20)
        user.find(marker="nav-table").click()
        await user.should_see("LaTeX 表格格式化")
        assert next(iter(user.find(marker="table-input").elements)) is input_widget
        assert input_widget.value == pasted_table and view.value == "compare"
        assert next(iter(user.find(marker="table-compare").elements)) is result_widget
        assert next(iter(user.find(marker="table-download").elements)).enabled
        user.find(marker="nav-tools").click()
        await user.should_see(marker="check-filter")
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

        # Identical output should never add a redundant history entry.
        await user.open("/")
        user.find(marker="bib-example").click()
        await settle()
        user.find(marker="format-preview").click()
        await settle()
        user.find(marker="apply-btn").click()
        await settle()
        user.find(marker="format-preview").click()
        await settle()
        assert not next(iter(user.find(marker="apply-btn").elements)).enabled
        print("✓ unchanged output disables Apply")

        # Exercise the real GUI callbacks with a gated, offline verifier.
        from hx_bib.gui import app as gui_app
        from hx_bib.verify.checker import CheckResult

        saved = gui_app._new_state, gui_app.prefetch_arxiv, gui_app.verify_entries
        state = gui_app._new_state()
        started, release = threading.Event(), threading.Event()
        requested = []

        def gated_verify(net, entries, **kwargs):
            requested.append([entry.key for entry in entries])
            started.set()
            assert release.wait(5)
            return [CheckResult(entry=entries[0], verdict="FAIL", issues=[("FAIL", "Title mismatch")])]

        gui_app._new_state = lambda: state
        gui_app.prefetch_arxiv = lambda *args: None
        gui_app.verify_entries = gated_verify
        try:
            await user.open("/")
            user.find(marker="bib-example").click()
            await settle()
            user.find(marker="tab-verify").click()
            await settle()
            await user.should_see("尚未开始核查")
            assert not next(iter(user.find(marker="verify-download").elements)).enabled
            keys = user.find(marker="verify-keys")
            keys.type("missing-citekey")
            user.find(marker="verify-start").click()
            await user.should_see("以下引用 key 不存在：missing-citekey")
            assert not started.is_set()
            entry = gui_app.parse_bibtex(state["text"]).entries[0]
            keys.clear()
            keys.type(entry.key)
            user.find(marker="verify-start").click()
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
            start_button = next(iter(user.find(marker="verify-start").elements))
            filter_widget = next(iter(user.find(marker="verify-filter").elements))
            release.set()
            await user.should_see("已停止，仅显示已完成的 1 条结果", retries=20)
            await user.should_see("Title mismatch")
            assert next(iter(user.find(marker="verify-download").elements)).enabled
            assert next(iter(user.find(marker="verify-show-results").elements)).enabled
            filter_widget.set_value("OK")
            await user.should_see("当前筛选下没有条目")
            assert next(iter(user.find(marker="verify-start").elements)) is start_button
            assert next(iter(user.find(marker="verify-filter").elements)) is filter_widget
            user.find(marker="tab-check").click()
            await settle()
            user.find(marker="tab-verify").click()
            await user.should_see("当前筛选下没有条目")
            print("✓ verification scope, pause/resume/stop, partial results and persistent filters")

            started.clear()
            release.clear()
            user.find(marker="verify-start").click()
            assert await asyncio.to_thread(started.wait, 5)
            assert not next(iter(user.find(marker="verify-download").elements)).enabled
            old_control = state["run_control"]
            user.find(marker="tab-format").click()
            await settle()
            user.find(marker="format-preview").click()
            await settle()
            user.find(marker="apply-btn").click()
            await settle()
            assert old_control.cancelled.is_set()
            release.set()
            await settle(0.5)
            assert state["verify_results"] is None and not state["progress"]["running"]
            print("✓ verification controls survive mode switches; document edits cancel stale runs")
        finally:
            release.set()
            gui_app._new_state, gui_app.prefetch_arxiv, gui_app.verify_entries = saved

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
