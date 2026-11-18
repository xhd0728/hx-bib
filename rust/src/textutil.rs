use crate::bibtex::rx;
use crate::diff::ratio;
use serde_json::{json, Value};
use unicode_normalization::UnicodeNormalization;

pub fn delatex(text: &str) -> String {
    let text = text.replace(['\n', '\t'], " ");
    let text = rx(r"\\url\{([^}]*)\}").replace_all(&text, "$1").to_string();
    let text = rx(r#"\\[`'"^~=.cuvHtdbr]\s*\{?\\?(\w)\}?"#)
        .replace_all(&text, "$1")
        .to_string();
    let text = text
        .replace("\\&", "&")
        .replace("\\_", "_")
        .replace("\\%", "%")
        .replace("\\$", "$");
    let text = rx(r"\$[^$]*\$")
        .replace_all(&text, |cap: &regex::Captures| {
            rx(r"[^0-9A-Za-z]").replace_all(&cap[0], "").into_owned()
        })
        .to_string();
    crate::bibtex::clean_value(
        &rx(r"\\[a-zA-Z]+")
            .replace_all(&text, "")
            .replace(['{', '}'], ""),
    )
}
pub fn asciifold(text: &str) -> String {
    text.nfkd().filter(|c| c.is_ascii()).collect()
}
pub fn norm_title(text: &str) -> String {
    rx(r"[^a-z0-9]+")
        .replace_all(&asciifold(&delatex(text)).to_lowercase(), " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}
pub fn title_ratio(a: &str, b: &str) -> f64 {
    let a = norm_title(a);
    let b = norm_title(b);
    if a.is_empty() || b.is_empty() {
        0.0
    } else {
        ratio(&a, &b)
    }
}
pub fn namekey(name: &str) -> String {
    rx(r"[^a-z0-9]")
        .replace_all(&asciifold(&delatex(name)).to_lowercase(), "")
        .into_owned()
}
pub fn surname(name: &str) -> String {
    let plain = asciifold(&delatex(name));
    let last = plain
        .split_whitespace()
        .rfind(|s| !s.chars().all(|c| c.is_ascii_digit()))
        .unwrap_or("");
    rx(r"[^a-z0-9\- ]")
        .replace_all(&last.to_lowercase(), "")
        .into_owned()
}
pub fn parse_authors(raw: &str) -> Vec<Value> {
    rx(r"\s+and\s+")
        .split(&delatex(raw))
        .filter_map(|part| {
            let part = part.trim().trim_matches(',');
            if part.is_empty() {
                return None;
            }
            if ["others", "et al", "et al."].contains(&part.to_lowercase().as_str()) {
                return Some(json!({"display":"et al.","surname":"","etal":"1"}));
            }
            let (last, display) = if let Some((last, first)) = part.split_once(',') {
                (
                    last.trim(),
                    format!("{} {}", first.trim(), last.trim())
                        .trim()
                        .to_string(),
                )
            } else {
                (
                    part.split_whitespace().last().unwrap_or(part),
                    part.to_string(),
                )
            };
            let surname = rx(r"[^a-z0-9\- ]")
                .replace_all(&asciifold(last).trim().to_lowercase(), "")
                .into_owned();
            if surname.is_empty() {
                None
            } else {
                Some(json!({"display":display,"surname":surname}))
            }
        })
        .collect()
}
pub fn corporate(raw: &str) -> bool {
    let raw = delatex(raw);
    if raw.is_empty() || rx(r"\sand\s").is_match(&raw) || raw.contains(',') {
        return false;
    }
    let lower = raw.to_lowercase();
    let hints = [
        "team",
        "openai",
        "deepseek",
        "google",
        "deepmind",
        "qwen",
        "ai",
        "org",
        "lab",
        "labs",
        "research",
        "inc",
        "analysis",
        "lmsys",
        "anthropic",
        "meta",
    ];
    rx(r"[\s/\-]+").split(&lower).any(|s| hints.contains(&s))
        || rx(r"[a-z][A-Z]").is_match(&raw)
        || raw.split_whitespace().count() == 1
}
