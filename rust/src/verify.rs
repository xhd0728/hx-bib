use crate::{
    bibtex::{rx, Entry},
    identifiers::{arxiv_id, doi, openreview_id},
    net::Net,
    sources::{self, SourceRecord},
    textutil::{corporate, delatex, namekey, parse_authors, surname, title_ratio},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CheckResult {
    pub entry: Entry,
    pub verdict: String,
    pub issues: Vec<(String, String)>,
    pub evidence: Vec<SourceRecord>,
    pub arxiv_id: Option<String>,
    pub doi: Option<String>,
    pub openreview_id: Option<String>,
    pub reference: Option<SourceRecord>,
    pub author_diff: Option<Value>,
    pub any_error: bool,
    pub publication: Option<(String, String)>,
    pub publication_url: Option<String>,
    pub attempts: usize,
    pub attempt_errors: Vec<(usize, String)>,
}
impl CheckResult {
    fn new(entry: &Entry) -> Self {
        Self {
            entry: entry.clone(),
            verdict: "INFO".into(),
            issues: Vec::new(),
            evidence: Vec::new(),
            arxiv_id: arxiv_id(entry),
            doi: doi(entry),
            openreview_id: openreview_id(entry),
            reference: None,
            author_diff: None,
            any_error: false,
            publication: None,
            publication_url: None,
            attempts: 1,
            attempt_errors: Vec::new(),
        }
    }
}
#[derive(Default)]
pub struct RunControl {
    pub paused: AtomicBool,
    pub cancelled: AtomicBool,
}
impl RunControl {
    pub fn wait(&self) -> bool {
        while self.paused.load(Ordering::Relaxed) && !self.cancelled.load(Ordering::Relaxed) {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        !self.cancelled.load(Ordering::Relaxed)
    }
}

pub fn compare_title(title: &str, record: &SourceRecord) -> (String, f64, Option<String>) {
    let ratio = title_ratio(title, &record.title);
    if ratio >= 0.9 {
        ("OK".into(), ratio, None)
    } else if ratio >= 0.72 {
        (
            "MINOR".into(),
            ratio,
            Some(format!(
                "title differs slightly (sim={ratio:.2}) vs “{}”",
                delatex(&record.title)
            )),
        )
    } else {
        (
            "FAIL".into(),
            ratio,
            Some(format!(
                "title does NOT match {} record (sim={ratio:.2}) — {} says “{}”",
                record.source,
                record.source,
                delatex(&record.title)
            )),
        )
    }
}
pub fn compare_authors(raw: &str, record: &SourceRecord) -> (String, String, Option<Value>) {
    if corporate(raw) {
        return (
            "INFO".into(),
            "group/organization author — not individually verifiable".into(),
            None,
        );
    }
    let parsed = parse_authors(raw);
    let truncated = parsed.iter().any(|p| p.get("etal").is_some());
    let bib: Vec<_> = parsed.iter().filter(|p| p.get("etal").is_none()).collect();
    if bib.is_empty() {
        return ("INFO".into(), "no parseable authors in bib".into(), None);
    }
    if record.authors.is_empty() {
        return (
            "INFO".into(),
            format!("{} record has no author list", record.source),
            None,
        );
    }
    let matched: Vec<_> = bib
        .iter()
        .map(|b| {
            let last = b["surname"].as_str().unwrap_or("");
            record.authors.iter().any(|a| {
                a == last
                    || last.split_whitespace().count() > 1
                        && last.split_whitespace().last() == Some(a.as_str())
            }) || record
                .raw_authors
                .iter()
                .any(|a| namekey(a) == namekey(b["display"].as_str().unwrap_or("")))
        })
        .collect();
    let count = matched.iter().filter(|v| **v).count();
    let overlap = count as f64 / bib.len() as f64;
    let missing: Vec<_> = bib
        .iter()
        .zip(&matched)
        .filter(|(_, yes)| !**yes)
        .map(|(b, _)| b["display"].as_str().unwrap_or(""))
        .collect();
    let bib_surnames: BTreeSet<_> = bib
        .iter()
        .flat_map(|b| {
            let surname = b["surname"].as_str().unwrap_or("");
            vec![surname, surname.split_whitespace().last().unwrap_or("")]
        })
        .collect();
    let detail = format!(
        "bib {}{} authors vs {} {}; overlap {count}/{}",
        bib.len(),
        if truncated { "+" } else { "" },
        record.source,
        record.raw_authors.len(),
        bib.len()
    );
    let miss = if missing.is_empty() {
        String::new()
    } else {
        format!("; not in {}: {}", record.source, missing.join(", "))
    };
    if overlap >= 0.99 && (truncated || bib.len() == record.raw_authors.len()) {
        return ("OK".into(), detail, None);
    }
    let diff = json!({"source":record.source,"bib":bib.iter().zip(&matched).map(|(b,yes)|json!({"display":b["display"],"matched":yes})).collect::<Vec<_>>(),"src":record.raw_authors.iter().map(|a|json!({"display":delatex(a),"matched":bib_surnames.contains(surname(a).as_str())||bib.iter().any(|b|namekey(a)==namekey(b["display"].as_str().unwrap_or("")))})).collect::<Vec<_>>()});
    if matched[0] && overlap >= 0.85 {
        return ("MINOR".into(), detail + &miss, Some(diff));
    }
    let message = if !matched[0] {
        format!(
            "first author “{}” not found in {} author list",
            bib[0]["display"].as_str().unwrap_or(""),
            record.source
        )
    } else {
        format!(
            "listed authors disagree with {} — {detail}{miss}",
            record.source
        )
    };
    ("WARN".into(), message, Some(diff))
}
pub fn compare_year(
    year: &str,
    record: &SourceRecord,
    expect_published: bool,
) -> (String, Option<String>) {
    let by: String = year.chars().filter(|c| c.is_ascii_digit()).collect();
    let ry: String = record
        .year
        .as_deref()
        .unwrap_or("")
        .chars()
        .filter(|c| c.is_ascii_digit())
        .collect();
    if by.is_empty() || ry.is_empty() {
        let quoted_year = record
            .year
            .as_ref()
            .map(|v| format!("'{v}'"))
            .unwrap_or_else(|| "None".into());
        return (
            "INFO".into(),
            Some(format!(
                "year not comparable (bib='{year}', {}={quoted_year})",
                record.source
            )),
        );
    }
    if by == ry {
        return ("OK".into(), None);
    }
    let d = by.parse::<i64>().unwrap_or(0) - ry.parse::<i64>().unwrap_or(0);
    if expect_published && (0..=2).contains(&d) {
        return ("OK".into(), None);
    }
    if d.abs() == 1 {
        (
            "MINOR".into(),
            Some(format!(
                "year off by 1 (bib={by}, {}={ry}) — common preprint/proceedings gap",
                record.source
            )),
        )
    } else {
        (
            "WARN".into(),
            Some(format!("year mismatch (bib={by}, {}={ry})", record.source)),
        )
    }
}
fn indexed<'a>(arxiv: &SourceRecord, records: &'a [SourceRecord]) -> Option<&'a SourceRecord> {
    records.iter().find(|r| {
        r.found
            && !r.venue.is_empty()
            && match r.source.as_str() {
                "Crossref" => ["journal-article", "proceedings-article"]
                    .contains(&r.publication_type.as_str()),
                "DBLP" => ["Conference and Workshop Papers", "Journal Articles"]
                    .contains(&r.publication_type.as_str()),
                "OpenReview" => r.publication_type == "accepted",
                _ => false,
            }
            && sources::matches(arxiv, r)
    })
}
pub fn publication(arxiv: &SourceRecord, records: &[SourceRecord]) -> (String, String) {
    if let Some(record) = indexed(arxiv, records) {
        let year = record
            .year
            .as_ref()
            .filter(|year| !record.venue.contains(year.as_str()))
            .map(|y| format!(" {y}"))
            .unwrap_or_default();
        return (
            if record.source == "OpenReview" {
                "accepted"
            } else {
                "record"
            }
            .into(),
            format!("{} · {}{year}", record.source, record.venue),
        );
    }
    if !["", "arxiv", "corr"].contains(&arxiv.venue.trim().to_lowercase().as_str()) {
        return ("reference".into(), arxiv.venue.clone());
    }
    if let Some(cap) = rx(r"(?i)\bcomment:\s*(.+)").captures(&arxiv.note) {
        let comment = &cap[1];
        let positive = rx(
            r"(?i)\b(?:accepted (?:at|by|for|in|to)|published (?:at|by|in)|to appear (?:in|at))\b",
        )
        .is_match(comment);
        let negative =
            rx(r"(?i)\b(?:not|never|if|once|when|whether)\b[^.;]{0,30}\b(?:accepted|published)\b")
                .is_match(comment);
        let other = rx(
            r"(?i)\b(?:code|data|dataset|software|repository|supplement)\b[^.;]{0,20}\bpublished\b",
        )
        .is_match(comment);
        if positive && !negative && !other {
            return ("comment".into(), comment.chars().take(240).collect());
        }
    }
    let mut errors = Vec::new();
    for record in records {
        if record.error && !errors.contains(&record.source) {
            errors.push(record.source.clone());
        }
    }
    if errors.is_empty() {
        ("not_found".into(), String::new())
    } else {
        ("unavailable".into(), errors.join(", "))
    }
}
fn find_publication(
    net: &Net,
    arxiv: &SourceRecord,
    mut indexes: Vec<SourceRecord>,
    out: &mut CheckResult,
) {
    let mut status = publication(arxiv, &indexes);
    if !arxiv.title.is_empty()
        && !arxiv.authors.is_empty()
        && !["record", "accepted", "reference"].contains(&status.0.as_str())
    {
        for search in [
            sources::openreview_publication,
            sources::crossref_publication,
            sources::dblp_publication,
        ] {
            let record = search(net, arxiv);
            out.evidence.push(record.clone());
            indexes.push(record);
            status = publication(arxiv, &indexes);
            if ["record", "accepted"].contains(&status.0.as_str()) {
                break;
            }
        }
    }
    out.publication_url = indexed(arxiv, &indexes)
        .and_then(|r| r.url.clone())
        .or_else(|| {
            if ["reference", "comment"].contains(&status.0.as_str()) {
                arxiv.url.clone()
            } else {
                None
            }
        });
    out.publication = Some(status);
}
pub fn check_arxiv(net: &Net, entry: &Entry) -> CheckResult {
    let mut out = CheckResult::new(entry);
    let Some(id) = out.arxiv_id.clone() else {
        out.doi = None;
        out.openreview_id = None;
        return out;
    };
    let mut record = sources::arxiv(net, &id);
    record.queried = Some(id);
    out.evidence.push(record.clone());
    if record.found {
        let mut indexes = Vec::new();
        if publication(&record, &[]).0 != "reference" {
            if let Some(doi) = &out.doi {
                indexes.push(sources::crossref(net, doi));
            }
            if let Some(id) = &out.openreview_id {
                indexes.push(sources::openreview(net, id));
            }
            out.evidence.extend(indexes.clone());
        }
        find_publication(net, &record, indexes, &mut out);
    } else {
        out.publication = Some((
            if record.error {
                "unavailable"
            } else {
                "missing"
            }
            .into(),
            if record.error { "arXiv" } else { "" }.into(),
        ));
    }
    let (status, detail) = out.publication.as_ref().unwrap();
    out.any_error = out.evidence.iter().any(|r| r.error)
        && ["unavailable", "comment"].contains(&status.as_str());
    out.verdict = if ["record", "accepted", "reference"].contains(&status.as_str()) {
        "OK"
    } else {
        "INFO"
    }
    .into();
    out.issues.push((
        "INFO".into(),
        format!(
            "arXiv publication: {status}{}",
            if detail.is_empty() {
                String::new()
            } else {
                format!(" ({detail})")
            }
        ),
    ));
    out
}
pub fn check_entry(net: &Net, entry: &Entry) -> CheckResult {
    let mut out = CheckResult::new(entry);
    let mut records = HashMap::<String, SourceRecord>::new();
    if let Some(id) = &out.arxiv_id {
        let mut record = sources::arxiv(net, id);
        record.queried = Some(id.clone());
        if !record.found {
            out.issues.push((
                if record.error { "INFO" } else { "FAIL" }.into(),
                if record.error {
                    format!(
                        "could not verify arXiv id {id} ({}) — network/rate-limit, not checked",
                        record.note
                    )
                } else {
                    format!(
                        "arXiv id {id} does NOT exist on arXiv — fabricated/incorrect identifier"
                    )
                },
            ));
        } else {
            records.insert("arXiv".into(), record.clone());
        }
        out.evidence.push(record);
    }
    if let Some(id) = &out.doi {
        let mut record = sources::crossref(net, id);
        record.queried = Some(id.clone());
        if record.found {
            records.insert("Crossref".into(), record.clone());
        } else if record.error {
            out.issues.push((
                "INFO".into(),
                format!(
                    "could not verify DOI {id} ({}) — network/rate-limit, not checked",
                    record.note
                ),
            ));
        } else {
            let response = net.get(&format!("https://doi.org/{id}"), "", 3, 30);
            let (status, msg) = if [Some(404), Some(410)].contains(&response.status) {
                (
                    "FAIL",
                    format!(
                        "DOI {id} does NOT resolve (Crossref + doi.org) — invalid/incorrect DOI"
                    ),
                )
            } else if response
                .status
                .is_some_and(|s| (200..400).contains(&s) || s == 401 || s == 403)
            {
                ("INFO",format!("DOI {id} resolves (via doi.org) but is not in Crossref — metadata not cross-checked"))
            } else {
                (
                    "INFO",
                    format!("could not verify DOI {id} (doi.org unreachable) — not checked"),
                )
            };
            out.issues.push((status.into(), msg));
        }
        out.evidence.push(record);
    }
    if let Some(id) = &out.openreview_id {
        let mut record = sources::openreview(net, id);
        record.queried = Some(id.clone());
        if record.found {
            records.insert("OpenReview".into(), record.clone());
        } else {
            out.issues.push((
                "INFO".into(),
                if record.error {
                    format!("could not reach OpenReview for id {id} — not checked")
                } else {
                    format!("OpenReview id {id} not found — may be withdrawn/private")
                },
            ));
        }
        out.evidence.push(record);
    }
    let title = entry.get("title");
    if !title.is_empty()
        && [
            "article",
            "inproceedings",
            "incollection",
            "inbook",
            "book",
            "phdthesis",
            "mastersthesis",
            "techreport",
            "conference",
        ]
        .contains(&entry.kind.as_str())
        && records.is_empty()
    {
        let authors = parse_authors(entry.get("author"));
        let first = authors
            .iter()
            .find(|a| a.get("etal").is_none())
            .and_then(|a| a["surname"].as_str());
        let cr = sources::crossref_search(net, title, first);
        let found = cr.found;
        out.evidence.push(cr.clone());
        if found {
            records.insert("Crossref".into(), cr);
        } else {
            let dblp = sources::dblp_search(net, title, first, None, 3, 30);
            if dblp.found {
                records.insert("DBLP".into(), dblp.clone());
            }
            out.evidence.push(dblp);
        }
    }
    if out.arxiv_id.is_some() {
        if let Some(arxiv) = records.get("arXiv") {
            let indexes = out
                .evidence
                .iter()
                .filter(|r| r.found && r.source != "arXiv")
                .cloned()
                .collect();
            find_publication(net, arxiv, indexes, &mut out);
            let (status, detail) = out.publication.as_ref().unwrap();
            let label = match status.as_str() {
                "record" => "Matching publication record",
                "accepted" => "Public acceptance record",
                "reference" => "arXiv publication reference",
                "comment" => "Author comment suggests acceptance or publication",
                "not_found" => "No acceptance or publication evidence found",
                _ => "Could not check acceptance or publication",
            };
            out.issues.push((
                "INFO".into(),
                format!(
                    "{label}{}",
                    if detail.is_empty() {
                        String::new()
                    } else {
                        format!(": {detail}")
                    }
                ),
            ));
        } else {
            out.publication = Some(("unavailable".into(), "arXiv".into()));
        }
    }
    let title_ref = ["arXiv", "Crossref", "OpenReview", "DBLP"]
        .iter()
        .find_map(|n| records.get(*n));
    let year_ref = ["OpenReview", "Crossref", "DBLP", "arXiv"]
        .iter()
        .find_map(|n| records.get(*n));
    let mut confirmed = false;
    if let Some(record) = title_ref {
        let (status, _, message) = compare_title(title, record);
        confirmed = ["OK", "MINOR"].contains(&status.as_str());
        if let Some(message) = message {
            out.issues.push((status, message));
        }
        let (status, message, diff) = compare_authors(entry.get("author"), record);
        if status != "OK" {
            out.issues.push((status, format!("authors: {message}")));
        }
        out.author_diff = diff;
        out.reference = Some(record.clone());
    }
    if !entry.get("author").is_empty()
        && parse_authors(entry.get("author"))
            .iter()
            .any(|a| a.get("etal").is_some())
    {
        out.issues.push((
            "INFO".into(),
            "author list ends with 'and others' (truncated) — the full author list is recommended"
                .into(),
        ));
    }
    if let Some(record) = year_ref {
        let preprint = record.source == "arXiv"
            || record.source == "DBLP" && record.venue.to_uppercase() == "CORR";
        let claims = ["inproceedings", "incollection", "conference"].contains(&entry.kind.as_str())
            || !entry.get("booktitle").is_empty();
        let (status, message) = compare_year(entry.get("year"), record, preprint && claims);
        if let Some(message) = message {
            out.issues.push((status, message));
        }
    }
    if entry.kind == "inproceedings"
        && !entry.get("booktitle").is_empty()
        && records.contains_key("arXiv")
        && !["OpenReview", "DBLP", "Crossref"]
            .iter()
            .any(|n| records.contains_key(*n))
    {
        out.issues.push(("INFO".into(),format!("claims venue “{}” but only an arXiv preprint was found (not yet indexed as published — verify the venue manually)",delatex(entry.get("booktitle")))));
    }
    out.any_error = out.evidence.iter().any(|r| {
        r.error && ["arXiv", "Crossref", "OpenReview", "DBLP"].contains(&r.source.as_str())
    });
    if !confirmed && !out.issues.iter().any(|i| i.0 == "FAIL") {
        let raw = ["url", "howpublished", "note"]
            .iter()
            .map(|n| entry.get(n))
            .find(|s| !s.is_empty())
            .unwrap_or("");
        if !raw.is_empty() {
            let cleaned = rx(r"\\url\{([^}]*)\}")
                .captures(raw)
                .map(|c| c[1].to_string())
                .unwrap_or_else(|| raw.to_string());
            let url = rx(r"https?://\S+").find(&cleaned).map(|m| {
                m.as_str()
                    .trim_end_matches(['.', ',', ';', ')', '}'])
                    .to_string()
            });
            let response = url.as_ref().map(|u| net.get(u, "", 3, 30));
            let status = response.as_ref().and_then(|r| r.status);
            let target = url.as_deref().unwrap_or(raw);
            out.evidence.push(SourceRecord {
                url: Some(target.into()),
                note: format!(
                    "HTTP {}",
                    status
                        .map(|n| n.to_string())
                        .unwrap_or_else(|| "None".into())
                ),
                ..SourceRecord::missing("URL", "")
            });
            let(sev,message)=match status{Some(200)=>("INFO",format!("no academic record found; URL is live (HTTP 200): {target}")),Some(s)if [301,302,303,307,308,401,403,405,406,429].contains(&s)=>("INFO",format!("no academic record; URL reachable (HTTP {s}): {target}")),Some(s)if s==404||s==410=>("WARN",format!("no academic record and URL returns HTTP {s} (resource does not exist): {target}")),None=>("INFO",format!("no academic record; could not reach URL (network/anti-bot — verify manually): {target}")),Some(s)=>("WARN",format!("no academic record; URL returned HTTP {s}: {target}"))};
            out.issues.push((sev.into(), message));
        } else if out.any_error {
            out.issues.push(("INFO".into(),"could not verify (DBLP/arXiv rate-limited or unreachable) — re-run to confirm; NOT treated as a problem".into()));
        } else {
            out.issues.push((
                "WARN".into(),
                "not found in DBLP/arXiv/Crossref and no identifier or URL to verify".into(),
            ));
        }
    }
    out.verdict = ["FAIL", "WARN", "MINOR"]
        .into_iter()
        .find(|sev| out.issues.iter().any(|i| i.0 == *sev))
        .unwrap_or(if confirmed { "OK" } else { "INFO" })
        .into();
    out
}

pub fn merge_attempts(previous: &CheckResult, mut latest: CheckResult) -> CheckResult {
    for (number, _) in &mut latest.attempt_errors {
        *number += previous.attempts;
    }
    let mut errors = previous.attempt_errors.clone();
    errors.extend(latest.attempt_errors);
    latest.attempt_errors = errors;
    latest.attempts += previous.attempts;
    latest
}

pub fn retry_failed(
    net: &Net,
    results: Vec<CheckResult>,
    control: &RunControl,
) -> Vec<CheckResult> {
    results
        .into_iter()
        .map(|previous| {
            if control.wait() && previous.any_error && previous.verdict != "OK" {
                let latest = verify_entries(
                    net,
                    std::slice::from_ref(&previous.entry),
                    false,
                    0,
                    1,
                    control,
                    &|_, _, _| {},
                );
                if let Some(latest) = latest.into_iter().next() {
                    return merge_attempts(&previous, latest);
                }
            }
            previous
        })
        .collect()
}

pub fn verify_entries(
    net: &Net,
    entries: &[Entry],
    arxiv_only: bool,
    retries: usize,
    workers: usize,
    control: &RunControl,
    progress: &(dyn Fn(usize, usize, &str) + Sync),
) -> Vec<CheckResult> {
    let counter = Mutex::new(0_usize);
    let finished = Mutex::new(0_usize);
    let output = Mutex::new(vec![None; entries.len()]);
    std::thread::scope(|scope| {
        for _ in 0..workers.clamp(1, 8) {
            let counter = &counter;
            let output = &output;
            let finished = &finished;
            scope.spawn(move || loop {
                if !control.wait() {
                    break;
                }
                let idx = {
                    let mut next = counter.lock().unwrap();
                    if *next >= entries.len() {
                        break;
                    }
                    let idx = *next;
                    *next += 1;
                    idx
                };
                let entry = &entries[idx];
                let mut result = if arxiv_only {
                    check_arxiv(net, entry)
                } else {
                    check_entry(net, entry)
                };
                let mut errors = Vec::new();
                for attempt in 1..=retries + 1 {
                    result.attempts = attempt;
                    if result.any_error {
                        let mut detail = result
                            .evidence
                            .iter()
                            .filter(|r| r.error)
                            .map(|r| format!("{}: {}", r.source, r.note))
                            .collect::<Vec<_>>()
                            .join("; ");
                        if detail.is_empty() {
                            detail = result
                                .issues
                                .iter()
                                .map(|(_, message)| message.as_str())
                                .collect::<Vec<_>>()
                                .join("; ");
                        }
                        detail = detail.replace("HTTP None", "connection failed or timed out");
                        if detail.is_empty() {
                            detail = "Verification unavailable".into();
                        }
                        log::warn!(
                            "Verification attempt {attempt}/{} failed for {}: {detail}",
                            retries + 1,
                            entry.key
                        );
                        errors.push((attempt, detail));
                    }
                    if !result.any_error || attempt > retries || !control.wait() {
                        break;
                    }
                    result = if arxiv_only {
                        check_arxiv(net, entry)
                    } else {
                        check_entry(net, entry)
                    };
                }
                result.attempt_errors = errors;
                output.lock().unwrap()[idx] = Some(result);
                let mut done = finished.lock().unwrap();
                *done += 1;
                progress(*done, entries.len(), &entry.key);
            });
        }
    });
    if let Err(error) = net.save() {
        log::warn!("Could not save HTTP cache: {error}");
    }
    output.into_inner().unwrap().into_iter().flatten().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{bibtex, net::HttpResponse};

    #[test]
    fn python_http_snapshots_match_native_verification() {
        let cases: Vec<Value> =
            serde_json::from_str(include_str!("../tests/verify_cases.json")).unwrap();
        let mut failures = Vec::new();
        for case in cases {
            let fixtures: HashMap<String, Vec<HttpResponse>> =
                serde_json::from_value(case["responses"].clone()).unwrap();
            let net = Net::fixture(fixtures);
            let entries = bibtex::parse(case["text"].as_str().unwrap()).entries;
            let results = verify_entries(
                &net,
                &entries,
                case["arxiv_only"].as_bool().unwrap(),
                case["retries"].as_u64().unwrap() as usize,
                2,
                &RunControl::default(),
                &|_, _, _| {},
            );
            let mut report = crate::report::json_value(&results, "fixture.bib");
            report.as_object_mut().unwrap().remove("generated");
            assert_eq!(
                report, case["report"],
                "Report mismatch for {}",
                case["name"]
            );
            let mut actual = serde_json::to_value(results).unwrap();
            for result in actual.as_array_mut().unwrap() {
                result.as_object_mut().unwrap().remove("entry");
            }
            if actual != case["expected"] {
                failures.push(serde_json::json!({"name":case["name"],"arxiv_only":case["arxiv_only"],"expected":case["expected"],"actual":actual}));
            }
        }
        assert!(
            failures.is_empty(),
            "{}",
            serde_json::to_string_pretty(&failures).unwrap()
        );
    }

    #[test]
    fn cancelled_batch_does_not_start_requests() {
        let net = Net::fixture(HashMap::new());
        let control = RunControl::default();
        control.cancelled.store(true, Ordering::Relaxed);
        control.paused.store(true, Ordering::Relaxed);
        let entries = bibtex::parse("@misc{x,eprint={2401.12345}}").entries;
        assert!(
            verify_entries(&net, &entries, true, 2, 2, &control, &|_, _, _| panic!(
                "Unexpected progress"
            ))
            .is_empty()
        );
    }
}
