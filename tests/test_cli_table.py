"""CLI tests for the `hx-bib table` subcommand."""

from typer.testing import CliRunner

from hx_bib.cli import app

runner = CliRunner()

TEX = (
    "Some text.\n"
    "\\begin{tabular}{cc}\n"
    "\\toprule a & b \\\\ \\midrule c & d \\\\ \\bottomrule\n"
    "\\end{tabular}\n"
)


def test_table_dry_run_shows_diff_and_writes_nothing(tmp_path):
    f = tmp_path / "doc.tex"
    f.write_text(TEX, encoding="utf-8")
    result = runner.invoke(app, ["table", str(f), "--dry-run"])
    assert result.exit_code == 0
    assert "table(s)" in result.output and "row(s)" in result.output
    assert f.read_text(encoding="utf-8") == TEX      # untouched


def test_table_writes_in_place(tmp_path):
    f = tmp_path / "doc.tex"
    f.write_text(TEX, encoding="utf-8")
    result = runner.invoke(app, ["table", str(f)])
    assert result.exit_code == 0
    formatted = f.read_text(encoding="utf-8")
    assert "a & b \\\\" in formatted
    assert formatted.index("\\toprule") < formatted.index("a & b")


def test_table_output_file_and_stdout(tmp_path):
    f = tmp_path / "doc.tex"
    f.write_text(TEX, encoding="utf-8")
    out = tmp_path / "out.tex"
    result = runner.invoke(app, ["table", str(f), "-o", str(out)])
    assert result.exit_code == 0
    assert f.read_text(encoding="utf-8") == TEX
    assert "a & b" in out.read_text(encoding="utf-8")

    result = runner.invoke(app, ["table", str(f), "--stdout"])
    assert result.exit_code == 0
    assert "a & b \\\\" in result.output


def test_table_stdin(tmp_path):
    result = runner.invoke(app, ["table", "-", "--stdout"], input=TEX)
    assert result.exit_code == 0
    assert "a & b \\\\" in result.output


def test_table_amp_options(tmp_path):
    f = tmp_path / "doc.tex"
    f.write_text(TEX, encoding="utf-8")
    result = runner.invoke(app, ["table", str(f), "--amp", "none", "--no-blank-lines"])
    assert result.exit_code == 0
    formatted = f.read_text(encoding="utf-8")
    assert "a&b \\\\" in formatted
    assert "\n\n" not in formatted.split("\\begin{tabular}")[1]


def test_table_invalid_amp_errors(tmp_path):
    f = tmp_path / "doc.tex"
    f.write_text(TEX, encoding="utf-8")
    result = runner.invoke(app, ["table", str(f), "--amp", "bogus"])
    assert result.exit_code != 0


def test_table_missing_file_errors():
    result = runner.invoke(app, ["table", "does-not-exist.tex"])
    assert result.exit_code != 0
