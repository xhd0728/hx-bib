"""HTML / JSON reports for citation verification results."""

from __future__ import annotations

import html
import json
import os
import re
import time
from typing import Dict, List

from ..verify.checker import FAIL, INFO, MINOR, OK, SEVERITY_LABEL, WARN, CheckResult
from ..verify.textutil import delatex

ICONS = {OK: "✅", INFO: "ℹ️", MINOR: "🟡", WARN: "⚠️", FAIL: "❌"}
PALETTE = {
    FAIL: ("#e5484d", "#fdecec"),
    WARN: ("#f76b15", "#fdefe3"),
    MINOR: ("#e0a400", "#fdf6e3"),
    INFO: ("#4a7fd0", "#eef3fb"),
    OK: ("#30a46c", "#e9f7f0"),
}


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _linkify(text: str) -> str:
    out, last = [], 0
    for m in re.finditer(r"https?://[^\s)]+", text):
        out.append(_esc(text[last : m.start()]))
        url = m.group(0)
        out.append(f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a>')
        last = m.end()
    out.append(_esc(text[last:]))
    return "".join(out)


def _badge(severity: str) -> str:
    color, bg = PALETTE[severity]
    label = SEVERITY_LABEL[severity]
    return (
        f'<span class="badge" style="background:{bg};color:{color};'
        f'border:1px solid {color}">{ICONS[severity]} {label}</span>'
    )


def _ids_html(result: CheckResult) -> str:
    parts: List[str] = []
    if result.arxiv_id:
        aid = _esc(result.arxiv_id)
        parts.append(
            f'arXiv <a href="https://arxiv.org/abs/{aid}" target="_blank" '
            f'rel="noopener"><code>{aid}</code></a>'
        )
    if result.doi:
        doi = _esc(result.doi)
        parts.append(
            f'DOI <a href="https://doi.org/{doi}" target="_blank" '
            f'rel="noopener"><code>{doi}</code></a>'
        )
    if result.openreview_id:
        oid = _esc(result.openreview_id)
        parts.append(
            f'OpenReview <a href="https://openreview.net/forum?id={oid}" '
            f'target="_blank" rel="noopener"><code>{oid}</code></a>'
        )
    return " &nbsp;·&nbsp; ".join(parts)


def _evidence_html(result: CheckResult) -> str:
    rows: List[str] = []
    for rec in result.evidence:
        if rec.source in ("arXiv", "Crossref", "DBLP", "OpenReview") and rec.found:
            authors = rec.raw_authors or []
            auth = _esc(", ".join(authors[:8]) + (" …" if len(authors) > 8 else ""))
            title = _esc(delatex(rec.title))
            if rec.url:
                title_html = f'<a href="{_esc(rec.url)}" target="_blank" rel="noopener">{title}</a>'
            else:
                title_html = title
            venue = ""
            if rec.venue and rec.source in ("OpenReview", "DBLP", "Crossref"):
                venue = f' <span class="ven">{_esc(rec.venue)}</span>'
            rows.append(
                f'<div class="evi"><span class="src">{_esc(rec.source)}</span> '
                f'“{title_html}” <span class="yr">({_esc(str(rec.year))})</span>{venue}'
                f'<div class="auth">{auth}</div></div>'
            )
    if not rows:
        return ""
    return (
        '<div class="evidence"><div class="evi-label">Matched record(s)</div>'
        + "".join(rows)
        + "</div>"
    )


def _author_diff_html(result: CheckResult) -> str:
    diff = result.author_diff
    if not diff:
        return ""

    def names(items: List[dict]) -> str:
        cells = []
        for it in items:
            cls = "ok" if it.get("matched") else "bad"
            mark = "" if it.get("matched") else ' <span class="x">✗</span>'
            cells.append(f'<li class="{cls}">{_esc(it["display"])}{mark}</li>')
        return "<ol class='names'>" + "".join(cells) + "</ol>"

    return (
        '<div class="diff"><div class="diff-label">Author comparison'
        '<span class="lg"><span class="sw bad"></span>not found on the other side</span></div>'
        '<div class="cols">'
        f'<div class="col"><div class="ch">Your .bib</div>{names(diff["bib"])}</div>'
        f'<div class="col"><div class="ch">{_esc(diff["source"])} (authoritative)</div>'
        f"{names(diff['src'])}</div>"
        "</div></div>"
    )


def _finding_items(result: CheckResult) -> str:
    if result.issues:
        return "".join(
            f'<li><span class="dot" style="color:{PALETTE[sev][0]}">{ICONS[sev]}</span> '
            f"{_linkify(msg)}</li>"
            for sev, msg in result.issues
        )
    ref = result.reference
    source = ref.source if ref else "?"
    return (
        '<li><span class="dot" style="color:#30a46c">✓</span> '
        f"title, authors and year confirmed via {_esc(source)}</li>"
    )


def _card(result: CheckResult) -> str:
    entry = result.entry
    verdict = result.verdict
    border = PALETTE[verdict][0]
    title = _esc(delatex(entry.get("title") or ""))
    ids = _ids_html(result)
    return f"""
      <article class="card" data-verdict="{verdict}" style="border-left:6px solid {border}">
        <div class="card-head">
          {_badge(verdict)}
          <span class="key">{_esc(entry.key)}</span>
          <span class="meta">@{_esc(entry.type)} · line {entry.line}</span>
        </div>
        <div class="title">{title}</div>
        {f'<div class="ids">{ids}</div>' if ids else ''}
        <ul class="findings">{_finding_items(result)}</ul>
        {_author_diff_html(result)}
        {_evidence_html(result)}
      </article>"""


def build_html(results: List[CheckResult], bibfile: str) -> str:
    order = {FAIL: 0, WARN: 1, MINOR: 2, INFO: 3, OK: 4}
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    total = len(results)
    flagged = counts.get(FAIL, 0) + counts.get(WARN, 0)

    chips = ['<button class="chip active" data-filter="ALL">All <b>%d</b></button>' % total]
    for sev in (FAIL, WARN, MINOR, INFO, OK):
        if counts.get(sev):
            chips.append(
                f'<button class="chip" data-filter="{sev}" style="--c:{PALETTE[sev][0]}">'
                f'{ICONS[sev]} {SEVERITY_LABEL[sev]} <b>{counts[sev]}</b></button>'
            )

    cards = "\n".join(
        _card(r)
        for r in sorted(results, key=lambda r: (order[r.verdict], r.entry.line))
    )
    generated = time.strftime("%Y-%m-%d %H:%M:%S")
    safe_file = _esc(os.path.basename(bibfile))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hx-bib · citation verification — {safe_file}</title>
<style>
  :root {{ color-scheme: light; --bg:#f6f7f9; --card:#fff; --ink:#1c2024; --muted:#6b7280; --line:#e5e7eb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }}
  header {{ background:linear-gradient(180deg,#fff,#fbfcfd); border-bottom:1px solid var(--line); padding:26px 28px; }}
  h1 {{ margin:0 0 6px; font-size:21px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .sub code {{ background:#eef0f3; padding:1px 6px; border-radius:5px; }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:0 16px 60px; }}
  .toolbar {{ position:sticky; top:0; z-index:5; background:var(--bg); padding:16px 0 10px;
             display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
  .chip {{ cursor:pointer; border:1px solid var(--line); background:#fff; color:#374151;
          padding:6px 12px; border-radius:999px; font-size:13px; }}
  .chip b {{ color:var(--c,#374151); margin-left:3px; }}
  .chip.active {{ background:#111827; color:#fff; border-color:#111827; }}
  .chip.active b {{ color:#fff; }}
  .search {{ margin-left:auto; padding:7px 11px; border:1px solid var(--line); border-radius:8px;
            font-size:13px; min-width:200px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:14px 16px; margin:12px 0; box-shadow:0 1px 2px rgba(0,0,0,.03); }}
  .card-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .badge {{ font-size:11.5px; font-weight:700; letter-spacing:.02em; padding:3px 9px;
           border-radius:999px; white-space:nowrap; }}
  .key {{ font-weight:650; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:14px; }}
  .meta {{ color:var(--muted); font-size:12.5px; }}
  .title {{ margin:8px 0 2px; font-size:15.5px; font-weight:550; }}
  .ids {{ font-size:13px; color:#374151; margin-top:4px; }}
  .ids code {{ background:#eef0f3; padding:1px 5px; border-radius:5px; }}
  ul.findings {{ list-style:none; margin:10px 0 0; padding:0; }}
  ul.findings li {{ padding:3px 0; font-size:14px; }}
  .dot {{ display:inline-block; width:1.4em; }}
  .evidence {{ margin-top:11px; padding:10px 12px; background:#fafbfc; border:1px dashed var(--line);
              border-radius:9px; }}
  .evi-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
               margin-bottom:5px; }}
  .evi {{ font-size:13px; margin:5px 0; }}
  .evi .src {{ display:inline-block; font-weight:700; font-size:11px; color:#374151; background:#eef0f3;
              padding:1px 6px; border-radius:5px; margin-right:5px; }}
  .evi .yr {{ color:var(--muted); }}
  .evi .ven {{ color:#6b7280; font-size:12.5px; font-style:italic; }}
  .evi .auth {{ color:var(--muted); font-size:12.5px; margin-top:1px; }}
  .diff {{ margin-top:11px; padding:10px 12px; background:#fff; border:1px solid var(--line);
          border-radius:9px; }}
  .diff-label {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
                margin-bottom:8px; }}
  .diff-label .lg {{ text-transform:none; letter-spacing:0; margin-left:8px; font-size:11.5px; }}
  .diff-label .sw {{ display:inline-block; width:10px; height:10px; border-radius:2px;
                    vertical-align:middle; margin-right:3px; }}
  .sw.bad {{ background:#fde2e1; border:1px solid #e5484d; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
  @media (max-width:640px) {{ .cols {{ grid-template-columns:1fr; }} }}
  .col .ch {{ font-size:12px; font-weight:700; color:#374151; margin-bottom:4px; }}
  ol.names {{ margin:0; padding-left:22px; }}
  ol.names li {{ font-size:13px; padding:1px 4px; border-radius:4px; }}
  ol.names li.bad {{ background:#fdecec; color:#b4262a; font-weight:600; }}
  a {{ color:#2563eb; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .hidden {{ display:none; }}
  .legend {{ font-size:12.5px; color:var(--muted); margin-top:8px; }}
  footer {{ text-align:center; color:var(--muted); font-size:12px; padding:24px; }}
</style></head>
<body>
<header>
  <div class="wrap" style="padding-bottom:0">
    <h1>Citation verification report · 引用校验报告</h1>
    <div class="sub">File <code>{safe_file}</code> &nbsp;·&nbsp; {total} entries &nbsp;·&nbsp;
      <b style="color:{PALETTE[FAIL][0] if flagged else PALETTE[OK][0]}">{flagged} need attention</b>
      &nbsp;·&nbsp; generated {generated}</div>
    <div class="legend">❌ <b>SUSPECT</b> fabricated/invalid identifier or wrong title ·
      ⚠️ <b>WARNING</b> notable mismatch · 🟡 <b>MINOR</b> small diff ·
      ℹ️ <b>UNVERIFIABLE</b> no academic source · ✅ <b>OK</b> confirmed.
      Sources: arXiv, Crossref, DBLP, OpenReview.</div>
  </div>
</header>
<div class="wrap">
  <div class="toolbar">
    {''.join(chips)}
    <input class="search" id="q" type="search" placeholder="filter by key or title…">
  </div>
  <main id="cards">
    {cards}
  </main>
  <footer>Generated by hx-bib — verify flagged entries manually before trusting/deleting.</footer>
</div>
<script>
  const cards = [...document.querySelectorAll('.card')];
  let curFilter = 'ALL', curQ = '';
  function apply() {{
    for (const c of cards) {{
      const okV = curFilter === 'ALL' || c.dataset.verdict === curFilter;
      const okQ = !curQ || c.textContent.toLowerCase().includes(curQ);
      c.classList.toggle('hidden', !(okV && okQ));
    }}
  }}
  document.querySelectorAll('.chip').forEach(ch => ch.addEventListener('click', () => {{
    document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
    ch.classList.add('active');
    curFilter = ch.dataset.filter; apply();
  }}));
  document.getElementById('q').addEventListener('input', e => {{
    curQ = e.target.value.trim().toLowerCase(); apply();
  }});
</script>
</body></html>"""


def build_json(results: List[CheckResult], bibfile: str) -> str:
    data = {
        "tool": "hx-bib",
        "file": bibfile,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "entries": [],
    }
    for r in results:
        entry = r.entry
        data["entries"].append({
            "key": entry.key,
            "type": entry.type,
            "line": entry.line,
            "verdict": r.verdict,
            "verdict_label": SEVERITY_LABEL[r.verdict],
            "title": delatex(entry.get("title") or ""),
            "arxiv_id": r.arxiv_id,
            "arxiv_publication": (
                {"status": r.publication[0], "evidence": r.publication[1], "url": r.publication_url}
                if r.publication else None
            ),
            "doi": r.doi,
            "openreview_id": r.openreview_id,
            "issues": [{"severity": s, "message": m} for s, m in r.issues],
            "author_diff": r.author_diff,
            "evidence": [rec.to_dict() for rec in r.evidence],
        })
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_reports(results: List[CheckResult], bibfile: str,
                  html_path: str = "", json_path: str = "") -> List[str]:
    written = []
    if html_path:
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(build_html(results, bibfile))
        written.append(html_path)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            fh.write(build_json(results, bibfile))
        written.append(json_path)
    return written
