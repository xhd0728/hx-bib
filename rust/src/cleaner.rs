use crate::bibtex::{rx, Document, Entry};
use anyhow::{bail, Result};
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Default, Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct CleanOptions {
    pub remove: Vec<String>,
    pub keep: Vec<String>,
    pub add: IndexMap<String, String>,
    pub rename: IndexMap<String, String>,
    pub overwrite: bool,
    pub drop_empty: bool,
    pub drop_others: bool,
    pub months: bool,
    pub title_period: bool,
    pub doi: bool,
    pub pages: bool,
    pub clean_urls: bool,
    pub dedupe: bool,
    pub keep_last: bool,
    pub dedupe_title: bool,
}

fn title_key(entry: &Entry) -> String {
    rx(r"[^a-z0-9]+")
        .replace_all(&entry.get("title").to_lowercase(), "")
        .into_owned()
}

pub fn clean(doc: &mut Document, opts: &CleanOptions) -> Result<Vec<String>> {
    let mut notes = Vec::new();
    for entry in &mut doc.entries {
        for name in &opts.remove {
            let name = name.trim().to_lowercase();
            if entry.remove(&name) {
                notes.push(format!("{}: removed field '{name}'", entry.key));
            }
        }
        if !opts.keep.is_empty() {
            let names: Vec<_> = entry.fields.keys().cloned().collect();
            for name in names {
                if !opts
                    .keep
                    .iter()
                    .any(|v| v.trim().eq_ignore_ascii_case(&name))
                {
                    entry.remove(&name);
                    notes.push(format!("{}: removed field '{name}'", entry.key));
                }
            }
        }
        for (name, value) in &opts.add {
            if name.trim().is_empty() {
                bail!("field name must not be empty");
            }
            if opts.overwrite || !entry.fields.contains_key(&name.trim().to_lowercase()) {
                entry.set(name, value.clone());
                notes.push(format!(
                    "{}: {} field '{name}'",
                    entry.key,
                    if opts.overwrite { "set" } else { "added" }
                ));
            }
        }
        for (old, new) in &opts.rename {
            let old = old.trim().to_lowercase();
            let new = new.trim().to_lowercase();
            if let Some(value) = entry.fields.shift_remove(&old) {
                let bare = entry.bare_fields.remove(&old);
                entry.fields.insert(new.clone(), value);
                if bare {
                    entry.bare_fields.insert(new.clone());
                }
                notes.push(format!("{}: renamed '{old}' -> '{new}'", entry.key));
            }
        }
        let names: Vec<_> = entry.fields.keys().cloned().collect();
        if opts.drop_empty {
            for name in names {
                if entry.get(&name).trim().is_empty() {
                    entry.remove(&name);
                    notes.push(format!("{}: removed empty field '{name}'", entry.key));
                }
            }
        }
        if opts.drop_others {
            for name in ["author", "editor"] {
                let value = entry.get(name).to_string();
                if !value.is_empty() {
                    let new = rx(r"(?i)(?:\s+and)?\s+others\s*$")
                        .replace(value.trim(), "")
                        .trim()
                        .trim_end_matches(',')
                        .to_string();
                    if new != value.trim() {
                        entry.set(name, new);
                        notes.push(format!(
                            "{}: dropped trailing 'others' from '{name}'",
                            entry.key
                        ));
                    }
                }
            }
        }
        if opts.months && entry.fields.contains_key("month") {
            let value = entry.get("month").to_string();
            let key = value.trim().trim_matches(['{', '}']).trim().to_lowercase();
            let full = [
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
            ];
            let months = [
                "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
            ];
            let number = key
                .parse::<usize>()
                .ok()
                .filter(|n| {
                    (1..=12).contains(n)
                        && key.len() <= 2
                        && key.bytes().all(|b| b.is_ascii_digit())
                })
                .map(|n| n - 1);
            let found = number
                .or_else(|| full.iter().position(|m| *m == key))
                .or_else(|| months.iter().position(|m| *m == key))
                .or_else(|| {
                    months
                        .iter()
                        .position(|m| *m != "may" && format!("{m}.") == key)
                })
                .or_else(|| ["sept", "sept."].contains(&key.as_str()).then_some(8));
            if let Some(i) = found {
                let month = months[i];
                if value != month || !entry.bare_fields.contains("month") {
                    entry.set("month", month);
                    entry.bare_fields.insert("month".into());
                    if value.trim().trim_matches(['{', '}']) != month {
                        notes.push(format!(
                            "{}: month '{}' -> '{month}'",
                            entry.key,
                            value.trim()
                        ));
                    }
                }
            } else if !value.trim().is_empty() {
                notes.push(format!(
                    "{}: unrecognized month '{}' (left as is)",
                    entry.key,
                    value.trim()
                ));
            }
        }
        if opts.title_period {
            let value = entry.get("title").trim().to_string();
            if let Some(new) = value.strip_suffix('.') {
                if !new.is_empty() {
                    entry.set("title", new);
                    notes.push(format!("{}: removed trailing period in title", entry.key));
                }
            }
        }
        if opts.doi {
            let old = entry.get("doi").to_string();
            let new = rx(r"(?i)^\s*(?:https?://(?:dx\.)?doi\.org/|doi:\s*|DOI:?\s*)")
                .replace(&old, "")
                .trim()
                .to_string();
            if !new.is_empty() && new != old.trim() {
                entry.set("doi", new.clone());
                notes.push(format!("{}: doi '{}' -> '{new}'", entry.key, old.trim()));
            }
        }
        if opts.pages {
            let old = entry.get("pages").to_string();
            let mut new = old.clone();
            loop {
                let next = rx(r"(\d)\s*[-–—]\s*(\d)")
                    .replace_all(&new, "$1--$2")
                    .to_string();
                if next == new {
                    break;
                }
                new = next;
            }
            if new != old {
                entry.set("pages", new.clone());
                notes.push(format!("{}: pages '{old}' -> '{new}'", entry.key));
            }
        }
        if opts.clean_urls {
            let old = entry.get("url").trim();
            if old.contains('?') {
                if let Ok(mut url) = url::Url::parse(old) {
                    let params: Vec<_> = url
                        .query_pairs()
                        .map(|(k, v)| (k.into_owned(), v.into_owned()))
                        .collect();
                    let kept: Vec<_> = params
                        .iter()
                        .filter(|(k, _)| {
                            let k = k.to_lowercase();
                            !k.starts_with("utm_")
                                && ![
                                    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref_src",
                                    "ref_url",
                                ]
                                .contains(&k.as_str())
                        })
                        .collect();
                    if kept.len() != params.len() {
                        url.set_query(None);
                        if !kept.is_empty() {
                            url.query_pairs_mut()
                                .extend_pairs(kept.iter().map(|(k, v)| (k, v)));
                        }
                        entry.set("url", url.to_string());
                        notes.push(format!(
                            "{}: cleaned tracking parameters from url",
                            entry.key
                        ));
                    }
                }
            }
        }
    }
    if opts.dedupe {
        dedupe(doc, opts.keep_last, false, &mut notes);
    }
    if opts.dedupe_title {
        dedupe(doc, opts.keep_last, true, &mut notes);
    }
    Ok(notes)
}

fn dedupe(doc: &mut Document, last: bool, by_title: bool, notes: &mut Vec<String>) {
    let mut order: Vec<Entry> = Vec::new();
    let mut seen: HashMap<String, Entry> = HashMap::new();
    for entry in &doc.entries {
        let key = if by_title {
            title_key(entry)
        } else {
            entry.key.clone()
        };
        if let Some(old) = seen.get(&key).filter(|_| !by_title || !key.is_empty()) {
            if last {
                if let Some(pos) = order.iter().position(|e| e == old) {
                    order.remove(pos);
                }
                order.push(entry.clone());
            }
            notes.push(if by_title {
                if last {
                    format!(
                        "{}: removed duplicate title entry (kept later '{}')",
                        entry.key, entry.key
                    )
                } else {
                    format!(
                        "{}: removed duplicate title entry (same title as '{}')",
                        entry.key, old.key
                    )
                }
            } else {
                format!(
                    "{}: removed duplicate entry (kept {}, line {})",
                    entry.key,
                    if last { "last" } else { "first" },
                    entry.line
                )
            });
            if last {
                seen.insert(key, entry.clone());
            }
        } else {
            if !key.is_empty() || !by_title {
                seen.insert(key, entry.clone());
            }
            order.push(entry.clone());
        }
    }
    doc.entries = order;
}
