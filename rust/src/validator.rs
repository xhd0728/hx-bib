use crate::bibtex::{rx, Document, Entry};
use crate::identifiers::{arxiv_id, is_arxiv};
use crate::venues::match_venue;
use chrono::{Datelike, Local};
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Issue {
    pub severity: String,
    pub key: String,
    pub message: String,
    pub field_name: Option<String>,
    pub line: usize,
    pub code: String,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct ValidateOptions {
    pub required_fields: bool,
    pub check_venues: bool,
    pub check_months: bool,
    pub check_title_period: bool,
    pub check_key_style: bool,
    pub year_range: (i32, i32),
}
impl Default for ValidateOptions {
    fn default() -> Self {
        Self {
            required_fields: true,
            check_venues: true,
            check_months: true,
            check_title_period: true,
            check_key_style: true,
            year_range: (1900, Local::now().year() + 2),
        }
    }
}

fn add(out: &mut Vec<Issue>, e: &Entry, severity: &str, field: &str, code: &str, message: String) {
    out.push(Issue {
        severity: severity.into(),
        key: e.key.clone(),
        message,
        field_name: if field.is_empty() {
            None
        } else {
            Some(field.into())
        },
        line: e.line,
        code: code.into(),
    });
}

pub fn validate(doc: &Document, opts: &ValidateOptions) -> Vec<Issue> {
    let mut out = Vec::new();
    let mut counts = IndexMap::new();
    for e in &doc.entries {
        *counts.entry(e.key.clone()).or_insert(0) += 1;
    }
    for (key, count) in counts {
        if count > 1 {
            out.push(Issue {
                severity: "error".into(),
                key,
                message: format!("duplicate cite key appears {count} times"),
                field_name: None,
                line: 0,
                code: "duplicate_key".into(),
            });
        }
    }
    for warning in &doc.warnings {
        let line = rx(r"^line (\d+):")
            .captures(warning)
            .and_then(|c| c[1].parse().ok())
            .unwrap_or(0);
        out.push(Issue {
            severity: "error".into(),
            key: "-".into(),
            message: warning.clone(),
            field_name: None,
            line,
            code: "parse".into(),
        });
    }
    for e in &doc.entries {
        if opts.required_fields {
            let required: &[&str] = match e.kind.as_str() {
                "article" => &["author", "title", "journal", "year"],
                "inproceedings" | "incollection" => &["author", "title", "booktitle", "year"],
                "book" => &["title", "publisher", "year"],
                "phdthesis" | "mastersthesis" => &["author", "title", "school", "year"],
                "techreport" => &["author", "title", "institution", "year"],
                _ => &[],
            };
            if required.is_empty() && e.fields.is_empty() {
                add(
                    &mut out,
                    e,
                    "warning",
                    "",
                    "empty_entry",
                    format!("@{} entry has no fields", e.kind),
                );
            }
            for name in required {
                if !e.fields.contains_key(*name) {
                    add(
                        &mut out,
                        e,
                        "error",
                        name,
                        "missing_required",
                        format!("missing required field '{name}'"),
                    );
                } else if e.get(name).trim().is_empty() {
                    add(
                        &mut out,
                        e,
                        "error",
                        name,
                        "empty_required",
                        format!("required field '{name}' is empty"),
                    );
                }
            }
            if e.kind == "book"
                && !e.fields.contains_key("author")
                && !e.fields.contains_key("editor")
            {
                add(
                    &mut out,
                    e,
                    "error",
                    "",
                    "book_author",
                    "book has neither 'author' nor 'editor'".into(),
                );
            }
        }
        for (name, value) in &e.fields {
            if value.trim().is_empty() {
                add(
                    &mut out,
                    e,
                    "warning",
                    name,
                    "empty_field",
                    format!("field '{name}' is empty"),
                );
            }
            if value.matches('{').count() != value.matches('}').count() {
                add(
                    &mut out,
                    e,
                    "error",
                    name,
                    "braces",
                    format!("unbalanced braces in field '{name}'"),
                );
            }
        }
        let year = e.get("year");
        if !year.is_empty() {
            let year = year.trim();
            if !rx(r"^\d{4}$").is_match(year) {
                add(
                    &mut out,
                    e,
                    "warning",
                    "year",
                    "year",
                    format!("year '{year}' is not a 4-digit year"),
                );
            } else if year
                .parse::<i32>()
                .is_ok_and(|n| n < opts.year_range.0 || n > opts.year_range.1)
            {
                add(
                    &mut out,
                    e,
                    "warning",
                    "year",
                    "year_range",
                    format!(
                        "year {year} outside plausible range {}–{}",
                        opts.year_range.0, opts.year_range.1
                    ),
                );
            }
        }
        let pages = e.get("pages");
        if !pages.is_empty()
            && !rx(r"^\s*[A-Za-z]?\d+(?::\d+)?\s*(?:(?:--|-{1,2}|–)\s*[A-Za-z]?\d+(?::\d+)?\s*)?$")
                .is_match(pages)
        {
            add(
                &mut out,
                e,
                "warning",
                "pages",
                "pages",
                format!("pages '{pages}' is not in 'start--end' form"),
            );
        }
        let author = e.get("author");
        if rx(r"(?i)\s+and\s+others\s*$").is_match(author)
            || author.trim().eq_ignore_ascii_case("others")
        {
            add(
                &mut out,
                e,
                "info",
                "author",
                "others",
                "author list ends with 'and others' (incomplete by design?)".into(),
            );
        }
        let month = e.get("month");
        let m = month.trim().trim_matches(['{', '}']).trim().to_lowercase();
        if opts.check_months
            && !month.is_empty()
            && ([
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ]
            .contains(&m.as_str())
                || rx(r"^\d{1,2}$").is_match(&m))
        {
            add(
                &mut out,
                e,
                "info",
                "month",
                "month",
                format!("month '{}' — consider 'jan'…'dec' macro form", month.trim()),
            );
        }
        if opts.check_title_period && e.get("title").trim().ends_with('.') {
            add(
                &mut out,
                e,
                "info",
                "title",
                "title_period",
                "title ends with a period".into(),
            );
        }
        let url = e.get("url");
        if !url.is_empty() && !rx(r"^\s*(https?://|\\url\{)").is_match(url) {
            add(
                &mut out,
                e,
                "warning",
                "url",
                "url",
                format!("url '{url}' has no http(s) scheme"),
            );
        }
        if opts.check_venues && !is_arxiv(e) {
            for name in ["booktitle", "journal"] {
                let value = e.get(name).trim().trim_matches(['{', '}']).trim();
                if value.is_empty() {
                    continue;
                }
                let known = match_venue(
                    value,
                    if name == "journal" {
                        Some("journal")
                    } else {
                        None
                    },
                );
                if [
                    "tbd",
                    "todo",
                    "unknown",
                    "n/a",
                    "none",
                    "placeholder",
                    "to be determined",
                ]
                .contains(&value.to_lowercase().as_str())
                    && !(name == "journal" && known.is_some())
                {
                    add(
                        &mut out,
                        e,
                        "warning",
                        name,
                        "placeholder",
                        format!("{name} is a placeholder ('{value}') — fill in the real venue"),
                    );
                } else if known.is_none() && match_venue(value, None).is_none() {
                    add(
                        &mut out,
                        e,
                        "info",
                        name,
                        "unknown_venue",
                        format!("{name} '{value}' is not in the venue presets"),
                    );
                }
            }
        }
        if e.kind == "article"
            && e.fields.contains_key("booktitle")
            && !e.fields.contains_key("journal")
        {
            add(
                &mut out,
                e,
                "warning",
                "booktitle",
                "article_booktitle",
                "article with a booktitle — did you mean @inproceedings?".into(),
            );
        }
        if ["inproceedings", "conference"].contains(&e.kind.as_str())
            && e.fields.contains_key("journal")
            && !e.fields.contains_key("booktitle")
        {
            add(
                &mut out,
                e,
                "warning",
                "journal",
                "conference_journal",
                format!("@{} with a journal — did you mean @article?", e.kind),
            );
        }
        if is_arxiv(e) {
            let id = arxiv_id(e);
            if let Some(id) = &id {
                if e.get("eprint").is_empty() {
                    add(
                        &mut out,
                        e,
                        "info",
                        "eprint",
                        "arxiv_eprint",
                        format!("arXiv id {id} found but no 'eprint' field"),
                    );
                }
            }
            if !e.get("eprint").is_empty() && e.get("archiveprefix").is_empty() {
                add(
                    &mut out,
                    e,
                    "info",
                    "archiveprefix",
                    "arxiv_prefix",
                    "eprint present without 'archiveprefix = {arXiv}'".into(),
                );
            }
            let volume = e.get("volume");
            if rx(r"(?i)^\s*abs/").is_match(volume) || volume.trim() == id.as_deref().unwrap_or("")
            {
                add(
                    &mut out,
                    e,
                    "info",
                    "volume",
                    "arxiv_volume",
                    format!(
                        "pseudo-volume '{}' — preprints have no volume",
                        volume.trim()
                    ),
                );
            }
        }
        if e.kind == "misc"
            && !["howpublished", "url", "note", "eprint", "doi"]
                .iter()
                .any(|n| e.fields.contains_key(*n))
        {
            add(
                &mut out,
                e,
                "warning",
                "",
                "misc_location",
                "@misc entry has no url/howpublished/note/doi".into(),
            );
        }
        if opts.check_key_style {
            if e.key.chars().any(char::is_whitespace) {
                add(
                    &mut out,
                    e,
                    "warning",
                    "",
                    "key_space",
                    format!("cite key '{}' contains whitespace", e.key),
                );
            } else if e.key != e.key.to_lowercase() {
                add(
                    &mut out,
                    e,
                    "info",
                    "",
                    "key_case",
                    format!("cite key '{}' is not lowercase", e.key),
                );
            }
        }
    }
    out
}
