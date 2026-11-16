"""Entry script for the headless GUI simulation (NiceGUI user_test mode)."""

import os

from nicegui import core, ui

if os.environ.get("NICEGUI_USER_SIMULATION"):
    # Runpy treats this file as a "script"; disable script mode so that
    # @ui.page decorators (multi-page mode) are allowed.
    core.script_mode = False

from hx_bib.gui.app import register_pages  # noqa: E402

register_pages()
ui.run(title="hx-bib", reload=False)
