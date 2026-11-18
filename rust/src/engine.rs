use crate::{
    bibtex::{self, Document, FormatOptions},
    cleaner::{self, CleanOptions},
    diff, identifiers,
    latex::{self, TableOptions},
    normalize::{self, NormalizeOptions},
    sources, textutil,
    validator::{self, ValidateOptions},
    venues, verify,
};
use anyhow::{bail, Result};
use serde_json::{json, Value};

fn options<T: serde::de::DeserializeOwned>(value: &Value) -> Result<T> {
    Ok(serde_json::from_value(if value.is_null() {
        json!({})
    } else {
        value.clone()
    })?)
}
pub fn request(request: Value) -> Result<Value> {
    let op = request["op"].as_str().unwrap_or("");
    let text = request["text"].as_str().unwrap_or("");
    let opts = &request["options"];
    match op {
        "parse" => Ok(serde_json::to_value(bibtex::parse(text))?),
        "format" => {
            let document = bibtex::parse(text);
            let config = options::<FormatOptions>(opts)?;
            Ok(json!(bibtex::render(&document, &config)?))
        }
        "clean" => {
            let mut document = bibtex::parse(text);
            let notes = cleaner::clean(&mut document, &options::<CleanOptions>(opts)?)?;
            Ok(json!({
                "text": bibtex::render(&document, &FormatOptions::default())?,
                "notes": notes,
            }))
        }
        "normalize" => {
            let mut document = bibtex::parse(text);
            let notes = normalize::normalize(&mut document, &options::<NormalizeOptions>(opts)?)?;
            Ok(json!({
                "text": bibtex::render(&document, &FormatOptions::default())?,
                "notes": notes,
            }))
        }
        "check" => {
            let document = bibtex::parse(text);
            let issues = validator::validate(&document, &options::<ValidateOptions>(opts)?);
            Ok(json!({"issues": issues}))
        }
        "table" => Ok(serde_json::to_value(latex::format_tables(
            text,
            &options::<TableOptions>(opts)?,
        )?)?),
        "identifiers" => {
            let entries = bibtex::parse(text).entries;
            let identifiers: Vec<_> = entries
                .iter()
                .map(|entry| {
                    json!({
                        "arxiv_id": identifiers::arxiv_id(entry),
                        "doi": identifiers::doi(entry),
                        "openreview_id": identifiers::openreview_id(entry),
                        "is_arxiv_like": identifiers::is_arxiv(entry),
                    })
                })
                .collect();
            Ok(json!(identifiers))
        }
        "venues" => Ok(json!(venues::search(text))),
        "venue-match" => {
            let matched = venues::match_venue(text, opts["expected_kind"].as_str());
            Ok(json!(matched.map(|(venue, findings)| json!({
                "abbr": venue.abbr,
                "findings": findings,
                "rendered": venues::render_venue(venue, findings, opts["style"].as_str().unwrap_or("abbr")),
            }))))
        }
        "text" => Ok(json!({
            "delatex": textutil::delatex(text),
            "asciifold": textutil::asciifold(text),
            "norm_title": textutil::norm_title(text),
            "surname": textutil::surname(text),
            "namekey": textutil::namekey(text),
            "authors": textutil::parse_authors(text),
            "corporate": textutil::corporate(text),
        })),
        "compare-title" => {
            let record = options::<sources::SourceRecord>(&request["record"])?;
            Ok(json!(verify::compare_title(text, &record)))
        }
        "compare-year" => {
            let record = options::<sources::SourceRecord>(&request["record"])?;
            Ok(json!(verify::compare_year(
                text,
                &record,
                opts["expect_published"].as_bool().unwrap_or(false)
            )))
        }
        "compare-authors" => {
            let record = options::<sources::SourceRecord>(&request["record"])?;
            Ok(json!(verify::compare_authors(text, &record)))
        }
        "publication" => {
            let record = options::<sources::SourceRecord>(&request["record"])?;
            let records = options::<Vec<sources::SourceRecord>>(&request["records"])?;
            Ok(json!(verify::publication(&record, &records)))
        }
        "source-record" => {
            let data = &request["data"];
            match opts["source"].as_str().unwrap_or("") {
                "OpenReview" => Ok(json!(sources::openreview_record(data))),
                "Crossref" => Ok(json!(sources::crossref_record(data))),
                "DBLP" => Ok(json!(sources::dblp_record(data, text))),
                "arXiv" => Ok(json!(sources::arxiv_atom(text))),
                "arXivHTML" => Ok(json!(sources::arxiv_html(
                    text,
                    opts["id"].as_str().unwrap_or("")
                ))),
                _ => bail!("unknown source"),
            }
        }
        "diff" => Ok(json!(diff::compare(
            text,
            request["after"].as_str().unwrap_or("")
        ))),
        "text-diff" => {
            let after = request["after"].as_str().unwrap_or("");
            let left: Vec<_> = text.lines().collect();
            let right: Vec<_> = after.lines().collect();
            Ok(json!({
                "key": "LaTeX",
                "kind": if text == after {"unchanged"} else {"modified"},
                "moved": false,
                "left": text,
                "right": after,
                "opcodes": diff::opcodes(&left, &right),
            }))
        }
        "apply" => apply(text, &request["settings"], &request["drafts"]),
        _ => bail!("unknown operation {op:?}"),
    }
}

fn enabled(settings: &Value, key: &str, default: bool) -> bool {
    settings["enabled"][key].as_bool().unwrap_or(default)
}
pub fn apply(text: &str, settings: &Value, drafts: &Value) -> Result<Value> {
    let mut doc = bibtex::parse(text);
    let original = doc.clone();
    let editing = ["clean", "format", "normalize", "venues"]
        .iter()
        .any(|key| enabled(settings, key, *key == "format"));
    if editing && !doc.warnings.is_empty() {
        bail!("bib_parse_first");
    }
    if enabled(settings, "clean", false) {
        let mut cfg: CleanOptions = options(&settings["clean"])?;
        let custom = drafts["clean_custom_remove"].as_str().unwrap_or("");
        cfg.remove.extend(
            bibtex::rx(r"[,\s]+")
                .split(custom)
                .filter(|s| !s.is_empty())
                .map(str::to_owned),
        );
        let key = drafts["clean_add_key"].as_str().unwrap_or("").trim();
        let value = drafts["clean_add_value"].as_str().unwrap_or("");
        if key.is_empty() != value.is_empty() {
            bail!("incomplete_field");
        }
        if !key.is_empty() {
            if !bibtex::rx(r"^[\w-]+$").is_match(key) {
                bail!("bib_invalid_field");
            }
            cfg.add.insert(key.into(), value.into());
        }
        cfg.overwrite = drafts["clean_overwrite"].as_bool().unwrap_or(false);
        cleaner::clean(&mut doc, &cfg)?;
    }
    if enabled(settings, "normalize", false) {
        let mut cfg: NormalizeOptions = options(&settings["normalize"])?;
        cfg.fix_entry_types = settings["normalize"]["fix_types"]
            .as_bool()
            .unwrap_or(cfg.fix_entry_types);
        if enabled(settings, "venues", false) {
            cfg.venue_style = "keep".into();
        }
        normalize::normalize(&mut doc, &cfg)?;
    }
    if enabled(settings, "venues", false) {
        let cfg = NormalizeOptions {
            venue_style: settings["venues"]["preset_style"]
                .as_str()
                .unwrap_or("abbr")
                .into(),
            arxiv_style: "keep".into(),
            ..NormalizeOptions::default()
        };
        for entry in &mut doc.entries {
            if !(identifiers::is_arxiv(entry) && identifiers::arxiv_id(entry).is_some())
                && ["booktitle", "journal"].iter().any(|f| {
                    !(**f == *"booktitle"
                        && entry
                            .get(f)
                            .trim()
                            .trim_matches(['{', '}'])
                            .trim()
                            .eq_ignore_ascii_case("tbd"))
                        && venues::match_venue(entry.get(f), None).is_some()
                })
            {
                normalize::normalize_entry(entry, &cfg);
            }
        }
    }
    let after = if enabled(settings, "format", true) {
        let mut value = settings["format"].clone();
        if let Some(map) = value.as_object_mut() {
            if let Some(sort) = map.remove("sort") {
                map.insert("sort_entries".into(), sort);
            }
        }
        bibtex::render(&doc, &options(&value)?)?
    } else if editing {
        reuse_text(text, &original, &doc)
    } else {
        text.into()
    };
    let updated = bibtex::parse(&after);
    let identity = |doc: &Document| {
        let mut entries: Vec<_> = doc
            .entries
            .iter()
            .map(|e| {
                let mut fields: Vec<_> = e
                    .fields
                    .iter()
                    .map(|(k, v)| (k.clone(), bibtex::clean_value(v)))
                    .collect();
                fields.sort();
                (e.kind.clone(), e.key.clone(), fields)
            })
            .collect();
        entries.sort();
        entries
    };
    if editing && (!updated.warnings.is_empty() || identity(&doc) != identity(&updated)) {
        bail!("bib_result_invalid");
    }
    let issues = if enabled(settings, "check", false) {
        Some(validator::validate(&updated, &options(&settings["check"])?))
    } else {
        None
    };
    Ok(json!({"text":after,"issues":issues,"comparisons":diff::compare(text,&after)}))
}
fn reuse_text(text: &str, before: &Document, after: &Document) -> String {
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut result = String::new();
    let mut cursor = 0;
    for old in &before.entries {
        let (start, stop) = old.source_span.unwrap();
        result.push_str(&text[cursor..start]);
        if let Some(new) = after
            .entries
            .iter()
            .find(|e| e.source_span == old.source_span)
        {
            if old == new {
                result.push_str(&text[start..stop]);
            } else {
                let indent = bibtex::rx(r"(?m)^([ \t]*)\w[\w-]*\s*=")
                    .captures(&text[start..stop])
                    .map(|c| c[1].to_string())
                    .unwrap_or_else(|| "    ".into());
                let cfg = FormatOptions {
                    indent: if indent.contains('\t') {
                        "tab".into()
                    } else {
                        indent.len().to_string()
                    },
                    field_order: "keep".into(),
                    ..FormatOptions::default()
                };
                result.push_str(&bibtex::render_entry(new, &cfg).replace('\n', newline));
            }
        }
        cursor = stop;
    }
    result.push_str(&text[cursor..]);
    result
}
