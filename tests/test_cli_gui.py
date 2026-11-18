"""GUI launch messages for users and redirected terminal output."""

import io
import logging

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hx_bib import cli
from hx_bib.gui import app as gui


@pytest.mark.parametrize("debug", [False, True])
def test_gui_reports_the_browser_address_once(monkeypatch, debug):
    calls = []
    monkeypatch.setattr(gui, "run_gui", lambda **kwargs: calls.append(kwargs))
    args = ["gui", "--host", "0.0.0.0", "--port", "8090", "--no-show"] + (["--debug"] if debug else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0
    assert result.output.count("http://127.0.0.1:8090") == 1
    assert ("DEBUG" in result.output) == debug
    assert calls == [{"host": "0.0.0.0", "port": 8090, "show": False, "debug": debug}]


@pytest.mark.parametrize("debug", [False, True])
def test_gui_debug_enables_terminal_and_server_logging(monkeypatch, debug):
    configurations = []
    server = []
    monkeypatch.setattr(gui.logging, "basicConfig", lambda **kwargs: configurations.append(kwargs))
    monkeypatch.setattr(gui, "register_pages", lambda: None)
    monkeypatch.setattr(gui.ui, "run", lambda **kwargs: server.append(kwargs))
    gui.run_gui(show=False, debug=debug)
    assert server[0]["uvicorn_logging_level"] == ("debug" if debug else "warning")
    assert server[0]["reload"] is False
    if debug:
        assert configurations[0]["level"] == logging.DEBUG
    else:
        assert not configurations


def test_interactive_banner_includes_browser_and_stop_instructions(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=True, color_system=None, width=72))
    cli._print_gui_banner("::", 8080, False)
    rendered = output.getvalue()
    assert "http://[::1]:8080" in rendered
    assert "Open the address above" in rendered
    assert "Ctrl+C" in rendered
