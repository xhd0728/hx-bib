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
2. All BibTeX tools are visible in the left rail. Formatting, cleaning, normalization, and validation update as you change settings. Select **Verify online** or **arXiv acceptance** to start an online check.
3. Review both versions on the right. Expand **Compare and copy individual items** to copy one entry, or **Apply** to stage the preview. Use **Undo / Redo**, then copy or download the working copy.
4. **File actions** contains history, reset, and **Save to file** for bibliographies opened from a local path. An arXiv check lists evidence; no evidence does not imply rejection.

Use the header switch for **LaTeX tables**: paste or import `.tex` → **Format** → compare then copy or download. Expand **Advanced settings** for indentation and rule spacing.

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
