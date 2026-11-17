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
        user.find(marker="run-check").click()
        await settle()
        await user.should_see("格式校验")
        print("✓ validate tab runs")

        user.find(marker="tab-verify").click()
        await settle()
        await user.should_see("并发线程数")
        await user.should_see("开始核查")
        print("✓ verify tab shows workers / pause controls")

        user.find(marker="tab-venues").click()
        await settle()
        await user.should_see("个预设")
        await user.should_see("用预设规范化引用")   # venues tab normalizes, not just browses
        user.find(marker="venue-normalize-preview").click()
        await settle()
        await user.should_see("修改预览")
        print("✓ venues tab renders preset cards and venue normalization preview")

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
        user.find(marker="table-example").click()
        await settle()
        user.find(marker="table-format").click()
        await settle()
        await user.should_see("个表格")
        await user.should_see("格式化结果")
        await user.should_see("修改前")
        print("✓ table formatter: example loads and formats (result-first view)")

        # Formatting only refreshes output: textarea widget and focus survive.
        input_widget = next(iter(user.find(marker="table-input").elements))
        user.find(marker="table-format").click()
        await settle()
        assert next(iter(user.find(marker="table-input").elements)) is input_widget
        user.find(marker="table-amp").trigger("update:model-value", 1)
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

        # In-page mode switch: the header control flips modes without reload.
        user.find(marker="nav-tools").click()
        await settle()
        await user.should_see("上传 BibTeX 文件")
        user.find(marker="nav-table").click()
        await settle()
        await user.should_see("LaTeX 表格格式化")
        print("✓ header switch flips tools ⇄ table in place")

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

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
