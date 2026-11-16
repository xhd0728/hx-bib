# hx-bib

A tidy toolbox for BibTeX files: **format**, **clean**, **normalize venues**, **validate** and **verify citations**.

- **CLI** (Typer + Rich) — pretty output, scriptable, CI-friendly, with itemized + GitHub-style side-by-side diff previews (`--dry-run`).
- **GUI** (NiceGUI, local web) — a single working copy shared by all tabs: every action is previewed first, then staged with undo / redo / reset, and the final text is one click away (copy all, download, per-entry copy).
- **Multi-source verification** — checks each entry against arXiv, Crossref, DBLP and OpenReview to catch hallucinated references, with concurrent workers, pause / stop, on-disk caching and arXiv-accepted detection.

中文文档（更完整）：[docs/README.zh.md](docs/README.zh.md)

## Install

Requires Python 3.9+.

```bash
git clone <this repo> && cd citation-refactor
pip install -e .            # installs the `hx-bib` command
```

Both entry points work without installation: `python -m hx_bib …` from a checkout with `PYTHONPATH=src`.

## Quick start

```bash
# Re-render with a consistent style (indent, field order, sorting)
hx-bib format refs.bib --indent 2 --field-order standard --sort key

# Preview instead of writing: itemized changes + red/green side-by-side diff
hx-bib format refs.bib --dry-run

# Clean: drop fields, strip 'and others', normalize months/DOIs/pages/URLs, dedupe
hx-bib clean refs.bib -r abstract -r keywords --drop-others --months --dedupe \
    --doi --pages --clean-urls --dedupe-title

# Normalize venues and arXiv entries
hx-bib normalize refs.bib --venue-style family --arxiv-style journal

# Offline structural check, then online verification (4 concurrent workers)
hx-bib check refs.bib --strict
hx-bib verify refs.bib --workers 4 --html report.html --json report.json

# Browse the built-in venue presets
hx-bib venues list --family ACM
hx-bib venues search retrieval

# Launch the browser UI
hx-bib gui
```

## Commands

| Command | What it does |
| --- | --- |
| `format` | Consistent indentation (`2/4/tab/align`), field order (`standard/alphabetical/keep`), entry sorting, `=` alignment, trailing commas. |
| `clean` | Remove / keep / add / rename fields, drop empty fields, strip `and others`, month macros (`jan…dec`), trailing title periods, duplicate cite keys. Advanced: normalize DOIs, unify page ranges (`12--34`), strip URL tracking parameters (`utm_*`, `fbclid`…), dedupe by title. |
| `normalize` | Canonical venue names (`abbr / full / family / keep`) and arXiv entries (`journal / misc / keep`), entry-type fixes. |
| `check` | Offline validation: required fields, year/pages shapes, unbalanced braces, duplicate keys, placeholder venues, style traps. |
| `verify` | Network verification against arXiv, Crossref, DBLP, OpenReview (`--workers N` for concurrency); HTML/JSON reports; flags arXiv preprints that have since been accepted. |
| `venues` | `list` / `search` the built-in conference & journal presets. |
| `gui` | Local NiceGUI app (`--port`, `--host`, `--show`). |

Every writing command supports `--dry-run / -n` (report only), `--stdout`, and `--output / -o`.

## Diff previews

With `--dry-run` (and after every action in the GUI) you get two views of the same change set:

- an **itemized list** — per entry, which fields were added (`+`), removed (`−`) or modified (`~`);
- a **side-by-side red/green line diff** like GitHub, with before/after line numbers.

The GUI goes one step further: three preview views (**itemized**, **side-by-side diff**, **per entry** — each entry card has its own copy button), a **show all / changes only** toggle, and a staging history — apply a change, preview the next one on top of it, undo / redo / reset at any time, then copy or download the final text.

## GUI workflow

All tabs edit one **working copy**: a structured preview is computed first (nothing changes until you click *Apply*), each applied action becomes a staged step you can undo / redo / reorder-free, and the status bar always shows how far the working copy has drifted from the original file. Settings (options, language, verify workers) persist in `localStorage`; the venue browser works with no file loaded. The UI is audited with Lighthouse (accessibility / best practices / SEO at 100).

## Venue families

Presets cover the common CS/AI conferences and journals. Grouping rules:

- ACL, EMNLP, NAACL, EACL, AACL, COLING, Findings, TACL, CL → family **ARR**
- KDD, SIGIR, MM, WWW, CIKM, WSDM, RecSys, CHI, SIGMOD, CCS, TOIS, TKDD → family **ACM**
- ICML, AISTATS, UAI → **PMLR**; NeurIPS, ICLR, AAAI, IJCAI, IEEE venues, JMLR, TMLR … have their own families.

`normalize --venue-style abbr` renders e.g. `Proceedings of EMNLP` (with `Findings of …` preserved);
`--venue-style family` collapses the split above to `ARR` / `ACM`.

## Verification verdicts

Each entry is classified as **OK** (title/authors/year confirmed) · **INFO** (cannot be verified — no source covers it) · **MINOR / WARN** (small differences) · **FAIL** (suspect / not found). `hx-bib verify` exits non-zero when anything is `FAIL`; results are cached politely on disk and can be exported as a filterable HTML report.

## Development

```bash
pip install -e ".[dev]"
pytest                       # unit + integration tests
PYTHONPATH=src python tests/gui_smoke.py   # headless GUI smoke test
```

## License

MIT
