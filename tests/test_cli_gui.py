"""GUI launch messages for users and redirected terminal output."""

import io

from rich.console import Console
from typer.testing import CliRunner

from hx_bib import cli
from hx_bib.gui import app as gui


def test_gui_reports_the_browser_address_once(monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "run_gui", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(cli.app, ["gui", "--host", "0.0.0.0", "--port", "8090", "--no-show"])
    assert result.exit_code == 0
    assert result.output.count("http://127.0.0.1:8090") == 1
    assert calls == [{"host": "0.0.0.0", "port": 8090, "show": False}]


def test_interactive_banner_includes_browser_and_stop_instructions(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=True, color_system=None, width=72))
    cli._print_gui_banner("::", 8080, False)
    rendered = output.getvalue()
    assert "http://[::1]:8080" in rendered
    assert "Open the address above" in rendered
    assert "Ctrl+C" in rendered
