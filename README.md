# hx-bib

[中文使用说明](docs/README.zh.md)

## Install

Requires Python 3.11+. Run from the project directory:

```bash
python -m pip install -e .
```

## Browser interface

```bash
hx-bib gui
# Custom port, without opening a browser:
hx-bib gui --port 9000 --no-show
```

Open [localhost:8080](http://127.0.0.1:8080) for the default port.

1. Choose a `.bib` file, paste BibTeX, enter a local path, or try the example.
2. Choose **Format**, **Clean**, or **Normalize** → generate a preview → **Apply**.
3. Use **Undo / Redo** to revise steps, then copy or download the working copy. **Save to file** writes back only when loaded from a local path.
4. **Check** runs offline. **Verify** queries external citation services and supports pause, stop, and report download.
5. **Venue presets** lets you search, filter, and copy names. To standardize all matching citations, choose a style → **Generate preview** → **Apply**.

Use the header switch for **LaTeX tables**: paste LaTeX or import a `.tex` file → set spacing and indentation → format → review the result or per-table comparison → copy or download. Reformat after changing the input or options.

## Command line

Writing commands modify the input file by default. Preview with `--dry-run`, or use `-o` to write a separate file.

```bash
hx-bib format refs.bib --indent 2 --sort key --dry-run
hx-bib clean refs.bib -r abstract -r keywords --doi --pages -o cleaned.bib
hx-bib normalize refs.bib --venue-style abbr --arxiv-style journal --dry-run
hx-bib check refs.bib --strict
hx-bib verify refs.bib --workers 4 --html report.html --json report.json
hx-bib venues list --family ACM
hx-bib venues search retrieval
hx-bib table paper.tex --amp align --indent 2 --dry-run
```

For `format`, `clean`, `normalize`, and `table`:

| Option | Usage |
| --- | --- |
| `-n`, `--dry-run` | Preview without writing |
| `-o PATH`, `--output PATH` | Write to another file |
| `--stdout` | Print the result |

Use `hx-bib COMMAND --help` for all options. `python -m hx_bib` also works in place of `hx-bib`.
