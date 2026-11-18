use crate::bibtex::{rx, Document, Entry};
use crate::identifiers::{arxiv_id, is_arxiv};
use crate::venues::{match_venue, render_venue};
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize)]
#[serde(default)]
pub struct NormalizeOptions {
    pub venue_style: String,
    pub arxiv_style: String,
    pub fix_entry_types: bool,
    pub drop_arxiv_volume: bool,
    pub add_arxiv_url: bool,
}
impl Default for NormalizeOptions {
    fn default() -> Self {
        Self {
            venue_style: "abbr".into(),
            arxiv_style: "journal".into(),
            fix_entry_types: true,
            drop_arxiv_volume: true,
            add_arxiv_url: true,
        }
    }
}

fn conference(kind: &str) -> bool {
    ["inproceedings", "incollection", "conference"].contains(&kind)
}

pub fn normalize_entry(entry: &mut Entry, opts: &NormalizeOptions) -> Vec<String> {
    let mut notes = Vec::new();
    if opts.arxiv_style != "keep" && is_arxiv(entry) {
        if let Some(id) = arxiv_id(entry) {
            let volume = entry.get("volume").to_string();
            if opts.drop_arxiv_volume
                && (rx(r"(?i)^\s*abs/\d{4}\.\d{4,5}\s*$").is_match(&volume) || volume.trim() == id)
            {
                entry.remove("volume");
                notes.push(format!(
                    "{}: dropped pseudo-volume '{}'",
                    entry.key,
                    volume.trim()
                ));
            }
            if opts.arxiv_style == "misc" {
                if entry.kind != "misc" {
                    notes.push(format!(
                        "{}: entry type {} -> misc (arXiv preset)",
                        entry.key, entry.kind
                    ));
                    entry.kind = "misc".into();
                }
                for field in ["booktitle", "journal"] {
                    if entry.remove(field) {
                        notes.push(format!(
                            "{}: removed field '{field}' (arXiv preset)",
                            entry.key
                        ));
                    }
                }
                if entry.get("howpublished").is_empty() {
                    entry.set("howpublished", format!("arXiv:{id}"));
                }
            } else {
                if entry.kind != "article" {
                    notes.push(format!(
                        "{}: entry type {} -> article (arXiv preset)",
                        entry.key, entry.kind
                    ));
                    entry.kind = "article".into();
                }
                if entry.remove("booktitle") {
                    notes.push(format!(
                        "{}: removed field 'booktitle' (arXiv preset)",
                        entry.key
                    ));
                }
                let journal = format!("arXiv preprint arXiv:{id}");
                if entry.get("journal") != journal {
                    entry.set("journal", journal.clone());
                    notes.push(format!("{}: journal -> '{journal}'", entry.key));
                }
            }
            entry.set("eprint", id.clone());
            entry.set("archiveprefix", "arXiv");
            if opts.add_arxiv_url && entry.get("url").is_empty() {
                entry.set("url", format!("https://arxiv.org/abs/{id}"));
                notes.push(format!("{}: added arXiv url", entry.key));
            }
        }
    }
    if is_arxiv(entry) && arxiv_id(entry).is_some() {
        return notes;
    }
    for field in ["booktitle", "journal"] {
        if opts.venue_style == "keep" {
            continue;
        }
        let value = entry.get(field).to_string();
        if field == "booktitle"
            && value
                .trim()
                .trim_matches(['{', '}'])
                .trim()
                .eq_ignore_ascii_case("tbd")
        {
            continue;
        }
        let Some((venue, findings)) = match_venue(&value, None) else {
            continue;
        };
        let target = if field == "booktitle" && venue.kind == "journal" {
            "journal"
        } else if field == "journal" && venue.kind == "conference" {
            "booktitle"
        } else {
            field
        };
        let rendered = render_venue(venue, findings, &opts.venue_style);
        if field != target {
            if !entry.get(target).trim().is_empty() {
                continue;
            }
            if opts.fix_entry_types
                && ((target == "journal" && conference(&entry.kind))
                    || (target == "booktitle" && entry.kind == "article"))
            {
                let kind = if target == "journal" {
                    "article"
                } else {
                    "inproceedings"
                };
                notes.push(format!(
                    "{}: entry type {} -> {kind} ('{}')",
                    entry.key, entry.kind, venue.abbr
                ));
                entry.kind = kind.into();
            }
            entry.remove(field);
            entry.set(target, rendered.clone());
            notes.push(format!("{}: {target} -> '{rendered}'", entry.key));
        } else if rendered != value.trim() {
            entry.set(field, rendered.clone());
            notes.push(format!(
                "{}: {field} '{}' -> '{rendered}'",
                entry.key,
                value.trim()
            ));
        }
    }
    if opts.fix_entry_types {
        if entry.kind == "article"
            && entry.fields.contains_key("booktitle")
            && !entry.fields.contains_key("journal")
        {
            entry.kind = "inproceedings".into();
            notes.push(format!(
                "{}: entry type article -> inproceedings",
                entry.key
            ));
        } else if conference(&entry.kind)
            && entry.fields.contains_key("journal")
            && !entry.fields.contains_key("booktitle")
        {
            entry.kind = "article".into();
            notes.push(format!("{}: entry type article -> article", entry.key));
        }
    }
    notes
}

pub fn normalize(doc: &mut Document, opts: &NormalizeOptions) -> Result<Vec<String>> {
    if !["abbr", "full", "family", "keep"].contains(&opts.venue_style.as_str()) {
        bail!("invalid venue_style {:?}", opts.venue_style);
    }
    if !["journal", "misc", "keep"].contains(&opts.arxiv_style.as_str()) {
        bail!("invalid arxiv_style {:?}", opts.arxiv_style);
    }
    Ok(doc
        .entries
        .iter_mut()
        .flat_map(|entry| normalize_entry(entry, opts))
        .collect())
}
