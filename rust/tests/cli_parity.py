"""Check CLI output files, validation reports, exit codes and dry-run safety."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

args = argparse.ArgumentParser()
args.add_argument("--binary", required=True, type=Path)
args = args.parse_args()
binary = str(args.binary.resolve())
python = [sys.executable, "-c", "from hx_bib.cli import main; main()"]


def run(command, arguments):
    result = subprocess.run(command + [str(arg) for arg in arguments], capture_output=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if result.returncode and arguments[0] != "check":
        print(result.stdout, result.stderr)
    if result.returncode not in (0, 1):
        raise AssertionError(f"{command[0]} {arguments}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.returncode


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    source = root / "references.bib"
    original = "@article{example,title={An Example Paper.},author={Doe, Jane and others},journal={TBD},year={2025},pages={1-9},doi={https://doi.org/10.1234/example}}\n@misc{empty}\n"
    source.write_text(original, encoding="utf-8")
    scenarios = [
        ("format", ["--indent", "2", "--field-order", "alphabetical"]),
        ("clean", ["--doi", "--pages", "--drop-others", "--title-period"]),
        ("normalize", ["--venue-style", "full"]),
    ]
    for command, options in scenarios:
        expected, actual = root / "python.bib", root / "rust.bib"
        assert run(python, [command, source, *options, "-o", expected]) == run([binary], [command, source, *options, "-o", actual]) == 0
        assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8"), command
        assert run([binary], [command, source, *options, "--dry-run"]) == 0
        assert source.read_text(encoding="utf-8") == original, "dry-run modified input"
    for strict in ([], ["--strict"]):
        expected, actual = root / "python.json", root / "rust.json"
        assert run(python, ["check", source, "--json", expected, *strict]) == run([binary], ["check", source, "--json", actual, *strict])
        assert json.loads(expected.read_text(encoding="utf-8")) == json.loads(actual.read_text(encoding="utf-8"))
    source = root / "table.tex"
    source.write_text(r"\begin{tabular}{cc} a&bbb\\long&d\\\end{tabular}", encoding="utf-8")
    expected, actual = root / "python.tex", root / "rust.tex"
    assert run(python, ["table", source, "--amp", "align", "-o", expected]) == run([binary], ["table", source, "--amp", "align", "-o", actual]) == 0
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")

print("Python/Rust CLI parity passed for transforms, reports, exit codes and dry-run safety")
