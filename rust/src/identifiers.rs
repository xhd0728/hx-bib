use crate::bibtex::{rx, Entry};

pub fn arxiv_id(entry: &Entry) -> Option<String> {
    if let Some(cap) = rx(r"^(\d{4}\.\d{4,5})(?:v\d+)?$").captures(entry.get("eprint").trim()) {
        return Some(cap[1].into());
    }
    let blob = [
        "journal",
        "note",
        "howpublished",
        "url",
        "doi",
        "volume",
        "eprint",
    ]
    .map(|n| entry.get(n))
    .join(" ");
    if let Some(cap) = rx(r"(?i)10\.48550/arxiv\.(\d{4}\.\d{4,5})").captures(&blob) {
        return Some(cap[1].into());
    }
    for cap in rx(r"(\d{4}\.\d{4,5})(?:v\d+)?").captures_iter(&blob) {
        let all = cap.get(0).unwrap();
        let before = blob[..all.start()].chars().last();
        let after = blob[all.end()..].chars().next();
        if !before.is_some_and(|c| c.is_numeric()) && !after.is_some_and(|c| c.is_numeric()) {
            return Some(cap[1].into());
        }
    }
    for cap in rx(r"(?i)[a-z\-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?").find_iter(&blob) {
        if !blob[..cap.start()]
            .chars()
            .last()
            .is_some_and(|c| c.is_ascii_alphabetic())
        {
            return Some(cap.as_str().into());
        }
    }
    None
}

pub fn doi(entry: &Entry) -> Option<String> {
    let mut value = entry.get("doi").trim().to_string();
    if value.is_empty() {
        if let Some(cap) = rx(r"(?i)doi\.org/(10\.\S+)").captures(entry.get("url")) {
            value = cap[1].into();
        }
    }
    value = rx(r"(?i)^https?://(?:dx\.)?doi\.org/")
        .replace(&value, "")
        .trim()
        .to_string();
    if value.is_empty() || rx(r"(?i)^10\.48550/arxiv\.").is_match(&value) {
        None
    } else {
        Some(value)
    }
}

pub fn openreview_id(entry: &Entry) -> Option<String> {
    let blob = ["url", "howpublished", "note"]
        .map(|n| entry.get(n))
        .join(" ");
    rx(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)")
        .captures(&blob)
        .map(|c| c[1].into())
}

pub fn is_arxiv(entry: &Entry) -> bool {
    entry.fields.contains_key("eprint")
        || entry.fields.contains_key("archiveprefix")
        || ["journal", "booktitle", "note", "howpublished", "publisher"]
            .iter()
            .any(|name| {
                let value = entry.get(name).to_lowercase();
                value.contains("arxiv") || value.trim() == "corr"
            })
        || rx(r"(?i)^\s*abs/").is_match(entry.get("volume"))
}
