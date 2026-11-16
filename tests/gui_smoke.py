"""Headless GUI smoke test (NiceGUI user simulation, no browser needed).

Run manually:
    PYTHONPATH=src python3 tests/gui_smoke.py

It loads examples/custom.bib via the path input, walks through the
Format tab (preview → apply), and toggles the language.
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


async def main() -> None:
    async with user_simulation(main_file=MAIN) as user:
        await user.open("/")
        await user.should_see("hx-bib")
        await user.should_see("上传 BibTeX 文件")
        print("✓ upload screen renders")

        user.find(marker="path-input").type(str(SAMPLE))
        user.find(marker="load-btn").click()
        await user.should_see("custom.bib")
        await user.should_see("格式化")
        print("✓ file loaded from path")

        user.find("格式化").click()
        user.find(marker="format-preview").click()
        await user.should_see("逐项对照")
        await user.should_see("左右对照 Diff")
        print("✓ format preview + diff rendered")

        user.find(marker="diff-mode").click()
        await user.should_see("逐项对照")
        print("✓ diff mode toggle works")

        user.find(marker="apply-btn").click()
        await user.should_see("custom.bib")
        print("✓ apply to working copy works")

        user.find("清理").click()
        user.find(marker="clean-preview").click()
        await user.should_see("逐项对照")
        print("✓ clean tab works")

        user.find("归一化").click()
        user.find(marker="normalize-preview").click()
        await user.should_see("左右对照 Diff")
        print("✓ normalize tab works")

        user.find("格式校验").click()
        user.find("开始校验").click()
        await user.should_see("格式校验")
        print("✓ validate tab runs")

        user.find("会议期刊预设").click()
        await user.should_see("会议期刊预设")
        print("✓ venues tab renders")

        user.find("中 / EN").click()
        await user.should_see("Format")
        await user.should_see("Verify")
        print("✓ language toggle works (zh → en)")

    print("\nAll GUI smoke checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
