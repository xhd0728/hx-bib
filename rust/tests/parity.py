"""Compare the native engine with the Python implementation on identical inputs.

Run after cargo build with python rust/tests/parity.py --binary PATH.
Python is a development/test dependency only.
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import subprocess
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hx_bib.core import cleaner, identifiers, latex_table, normalize, parser, validator, venues, writer
from hx_bib.gui.bib_workbench import apply_options
from hx_bib.gui.bib_workbench import compare_entries
from hx_bib.gui.workspace import DEFAULT_SETTINGS, merge_settings
from hx_bib.verify import checker, sources, textutil


def corpus():
    texts = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.add(node.value)
    texts.update(path.read_text(encoding="utf-8") for path in (ROOT / "examples").glob("*.bib"))
    return sorted(texts)


def clean(text, options):
    doc = parser.parse_bibtex(text)
    notes = []
    for name in ("remove", "keep"):
        if options.get(name):
            notes += getattr(cleaner, name + "_fields")(doc, options[name])
    if options.get("add"):
        notes += cleaner.add_fields(doc, options["add"], overwrite=options.get("overwrite", False))
    if options.get("rename"):
        notes += cleaner.rename_fields(doc, options["rename"])
    for name, operation in (
        ("drop_empty", "drop_empty_fields"), ("drop_others", "drop_others"),
        ("months", "normalize_months"), ("title_period", "strip_title_periods"),
        ("doi", "normalize_dois"), ("pages", "unify_page_ranges"), ("clean_urls", "strip_url_tracking"),
    ):
        if options.get(name):
            notes += getattr(cleaner, operation)(doc)
    keep = "last" if options.get("keep_last") else "first"
    if options.get("dedupe"):
        notes += cleaner.dedupe_entries(doc, keep=keep)
    if options.get("dedupe_title"):
        notes += cleaner.dedupe_by_title(doc, keep=keep)
    return {"text": writer.render_document(doc, writer.FormatOptions()), "notes": notes}


def cases():
    texts = corpus()
    bibs = [s for s in texts if s.lstrip().startswith("@")]
    for month in ("may.", "sept.", "sept", "005", "+1", "09", "January", ""):
        text = "@misc{month,month={" + month + "}}"
        opts = {"months": True}
        yield {"op": "clean", "text": text, "options": opts}, clean(text, opts)
    for text in bibs:
        doc = parser.parse_bibtex(text)
        yield {"op": "parse", "text": text}, asdict(doc)
        yield {"op": "identifiers", "text": text}, [
            {"arxiv_id": identifiers.extract_arxiv_id(e), "doi": identifiers.extract_doi(e),
             "openreview_id": identifiers.extract_openreview_id(e), "is_arxiv_like": identifiers.is_arxiv_like(e)}
            for e in doc.entries
        ]
        for opts in ({}, {"indent": "2", "align_equals": True}, {"indent": "tab", "field_order": "keep"},
                     {"indent": "align", "field_order": "alphabetical", "sort_entries": "year"},
                     {"sort_entries": "key", "drop_comments": True, "trailing_comma": True}):
            yield {"op": "format", "text": text, "options": opts}, writer.render_document(doc, writer.FormatOptions(**opts))
        yield {"op": "check", "text": text}, {"issues": [asdict(i) for i in validator.validate_document(doc).issues]}
        for opts in ({"drop_others": True, "months": True, "doi": True, "pages": True, "clean_urls": True},
                     {"remove": ["abstract", "note"], "add": {"note": "New"}, "overwrite": True},
                     {"dedupe": True, "keep_last": True, "dedupe_title": True},
                     {"keep": ["title", "year"], "rename": {"year": "date"}, "title_period": True, "drop_empty": True}):
            yield {"op": "clean", "text": text, "options": opts}, clean(text, opts)
        for venue, arxiv in itertools.product(("abbr", "full", "family", "keep"), ("journal", "misc", "keep")):
            work = deepcopy(doc)
            opts = {"venue_style": venue, "arxiv_style": arxiv}
            notes = normalize.normalize_document(work, normalize.NormalizeOptions(**opts))
            yield {"op": "normalize", "text": text, "options": opts}, {"text": writer.render_document(work, writer.FormatOptions()), "notes": notes}
    for text in texts:
        if "\\begin{" in text:
            for amp in ("space", "align", "none"):
                for blank in (True, False):
                    opts = {"amp_style": amp, "blank_lines_around_rules": blank, "row_indent": 2}
                    yield {"op": "table", "text": text, "options": opts}, asdict(latex_table.format_tables(text, latex_table.TableFormatOptions(**opts)))
    for text in ("TBD", "Proceedings of EMNLP", "Findings of the ACL", "NeurIPS 2025", "ofKDD", "Research & Development"):
        for kind in (None, "journal", "conference"):
            for style in ("abbr", "full", "family"):
                match = venues.match_venue(text, expected_kind=kind)
                result = {"abbr": match[0].abbr, "findings": match[1], "rendered": venues.render_venue(*match, style)} if match else None
                yield {"op": "venue-match", "text": text, "options": {"expected_kind": kind, "style": style}}, result
    yield {"op": "venues", "text": ""}, [asdict(v) for v in venues.VENUES]
    for text in ("García, José and Jane Doe and others", r"{\textbf{A}} $x_1$ \& B", "Yang Zhang 0001", "MAP Team", "", "YuYue", "van der Waals, Jan", "Jane Doe and Alex Singh"):
        yield {"op": "text", "text": text}, {
            "delatex": textutil.delatex(text), "asciifold": textutil.asciifold(text),
            "norm_title": textutil.norm_title(text), "surname": textutil.surname_of_full(text),
            "namekey": textutil.namekey(text), "authors": textutil.parse_bib_authors(text),
            "corporate": textutil.looks_corporate(text),
        }
        record = sources.SourceRecord("arXiv", True, title="A Paper", authors=["doe", "singh"], raw_authors=["Jane Doe", "Alex Singh"], year="2024", venue="arXiv")
        yield {"op": "compare-authors", "text": text, "record": asdict(record)}, checker.cmp_authors(text, record)
    for text in ("A Paper", "A Papers", "Other title", "A Paper " * 40, "Research " * 40):
        record = sources.SourceRecord("arXiv", True, title="A Paper")
        yield {"op": "compare-title", "text": text, "record": asdict(record)}, checker.cmp_title(text, record)
    for text, title in (("A Paper " * 40, "Paper " * 40), ("Research " * 40, "Research " * 39 + "Other")):
        record = sources.SourceRecord("arXiv", True, title=title)
        yield {"op": "compare-title", "text": text, "record": asdict(record)}, checker.cmp_title(text, record)
    for year in ("", "2023", "2024", "2025", "2026", "2027"):
        for expect in (True, False):
            record = sources.SourceRecord("arXiv", True, year="2024")
            yield {"op": "compare-year", "text": year, "record": asdict(record), "options": {"expect_published": expect}}, checker.cmp_year(year, record, expect)
    for note in ("comment: Accepted at Example Conference", "comment: Not yet accepted at Example Conference", "comment: Submitted to Example Conference", "comment: Code published in the repository"):
        record = sources.SourceRecord("arXiv", True, venue="arXiv", note=note)
        yield {"op": "publication", "record": asdict(record), "records": []}, checker._arxiv_publication(record)
    for authors in (["Jane Doe", "Alex Singh"], [{"fullname": "Jane Doe", "username": "~Jane_Doe1"}, "Alex Singh"], ["Anonymous"]):
        note = {"id": "paper", "forum": "paper", "domain": "Example/2025/Conference", "pdate": 1758215815218,
                "content": {"title": {"value": "A Paper"}, "authors": {"value": authors},
                            "venue": {"value": "Example 2025 poster"}, "venueid": {"value": "Example/2025/Conference"}}}
        record = sources._openreview_record(note)
        yield {"op": "source-record", "options": {"source": "OpenReview"}, "data": note}, asdict(record) if record else None
    crossref = {"title": ["Example Paper"], "author": [{"family": "Doe", "given": "Jane"}],
                "issued": {"date-parts": [[2025, 3]]}, "container-title": ["Example Journal"],
                "DOI": "10.1000/example", "URL": "https://doi.org/10.1000/example", "type": "journal-article"}
    yield {"op": "source-record", "options": {"source": "Crossref"}, "data": crossref}, asdict(sources._crossref_item_to_record(crossref))
    atom = ('<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
            '<title>Example Paper</title><author><name>Jane Doe</name></author><published>2025-02-02</published>'
            '<id>https://arxiv.org/abs/2502.14739</id><arxiv:journal_ref>Example Journal</arxiv:journal_ref>'
            '<arxiv:comment>Accepted at Example Conference</arxiv:comment></entry>')
    import xml.etree.ElementTree as ET
    yield {"op": "source-record", "options": {"source": "arXiv"}, "text": atom}, [asdict(sources._arxiv_entry_to_record(ET.fromstring(atom)))]
    pairs = [
        ("@article{same,title={First}} @article{same,title={Last}}", "@article{same,title={Last}}"),
        ("@misc{a,title={A}}\n@misc{b,title={B}}", "@misc{b,title={B}}\n@misc{a,title={A}}"),
        ("@article{x,\n title={A}\n}", "@article{x,\n    title={A}\n}"),
        ("@string{venue=\"First\"}\n@misc{x}", "@string{venue=\"Last\"}\n@misc{x}"),
    ]
    for before, after in pairs:
        result = [{"key": (c.after or c.before).key if c.after or c.before else "File content",
                   "kind": c.kind, "moved": c.moved, "left": c.left, "right": c.right}
                  for c in compare_entries(before, after)]
        yield {"op": "diff", "text": before, "after": after}, result
    for enabled in ({"format": True}, {"format": False, "clean": True, "check": True},
                    {"clean": True, "normalize": True, "check": True, "venues": True}):
        settings = merge_settings(DEFAULT_SETTINGS, {"enabled": enabled, "clean": {"remove": ["abstract"], "pages": True}})
        text = "@article{known,title={Title},author={Doe},journal={TBD},year={2024},abstract={Text},pages={1-3}}\n@misc{link,url={https://example.com}}"
        after, report = apply_options(text, settings, {})
        yield {"op": "apply", "text": text, "settings": settings, "drafts": {}}, {"text": after, "issues": None if report is None else [asdict(i) for i in report.issues]}


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=lambda v: sorted(v) if isinstance(v, set) else v))


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--binary", required=True, type=Path)
    args = args.parse_args()
    samples = list(cases())
    process = subprocess.run([str(args.binary.resolve()), "engine"], input="".join(json.dumps(req, ensure_ascii=False) + "\n" for req, _ in samples), text=True, encoding="utf-8", capture_output=True)
    if process.returncode:
        print(f"Engine exited {process.returncode}, completed {len(process.stdout.splitlines())}/{len(samples)} requests")
        print(process.stderr[-4000:])
        raise SystemExit(1)
    responses = process.stdout.splitlines()
    assert len(responses) == len(samples), process.stderr
    failed = []
    for number, ((request, expected), raw) in enumerate(zip(samples, responses)):
        response = json.loads(raw)
        actual = response.get("result")
        if request["op"] == "parse" and actual:
            source = request["text"].encode("utf-8")
            for entry in actual["entries"]:
                start, end = entry["source_span"]
                entry["source_span"] = [len(source[:start].decode("utf-8")), len(source[:end].decode("utf-8"))]
        if request["op"] == "apply" and actual:
            actual.pop("comparisons", None)
        if request["op"] == "diff" and actual:
            for comparison in actual:
                comparison.pop("opcodes", None)
        if request["op"] == "clean" and actual:
            actual["notes"].sort()
            expected["notes"].sort()
        if not response.get("ok") or plain(expected) != actual:
            failed.append({"case": number, "request": request, "expected": plain(expected), "actual": response})
    if failed:
        path = ROOT / ".venv" / "rust-parity-failures.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(failed)}/{len(samples)} mismatches. Details in {path}")
        for failure in failed[:10]:
            print(failure["case"], failure["request"]["op"], repr(failure["request"].get("text", "")[:90]))
        raise SystemExit(1)
    print(f"Python/Rust parity passed for {len(samples)} cases")


if __name__ == "__main__":
    main()
