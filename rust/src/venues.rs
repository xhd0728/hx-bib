use crate::bibtex::rx;
use serde::{Deserialize, Serialize};
use std::sync::LazyLock;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Venue {
    pub abbr: String,
    pub name: String,
    pub family: String,
    pub kind: String,
    pub aliases: Vec<String>,
}

pub static VENUES: LazyLock<Vec<Venue>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../data/venues.json")).expect("valid venue catalog")
});

pub fn prep(text: &str) -> String {
    let camel = rx(r"([a-z])([A-Z])").replace_all(text, "$1 $2").to_string();
    let text = camel.replace('&', " and ").to_lowercase();
    rx(r"\s+")
        .replace_all(rx(r"[^a-z0-9 ]+").replace_all(&text, " ").trim(), " ")
        .into_owned()
}

pub fn match_venue(text: &str, expected_kind: Option<&str>) -> Option<(&'static Venue, bool)> {
    let candidate = format!(" {} ", prep(text));
    let mut best = None;
    let mut best_length = 0;
    for venue in VENUES.iter() {
        if expected_kind.is_some_and(|kind| kind != venue.kind) {
            continue;
        }
        for alias in [&venue.abbr, &venue.name]
            .into_iter()
            .chain(venue.aliases.iter())
        {
            let term = prep(alias);
            if term.len() > best_length && candidate.contains(&format!(" {term} ")) {
                best = Some(venue);
                best_length = term.len();
            }
        }
    }
    best.map(|venue| (venue, candidate.contains(" findings ")))
}

pub fn render_venue(venue: &Venue, findings: bool, style: &str) -> String {
    if style == "family" {
        return venue.family.clone();
    }
    if venue.kind == "journal" {
        return if style == "abbr" {
            venue.abbr.clone()
        } else {
            venue.name.clone()
        };
    }
    let prefix = match (style == "full", findings) {
        (true, true) => "Findings of the ",
        (true, false) => "Proceedings of the ",
        (false, true) => "Findings of ",
        (false, false) => "Proceedings of ",
    };
    format!(
        "{prefix}{}",
        if style == "full" {
            &venue.name
        } else {
            &venue.abbr
        }
    )
}

pub fn search(query: &str) -> Vec<&'static Venue> {
    let q = prep(query);
    VENUES
        .iter()
        .filter(|v| {
            let mut terms = vec![v.abbr.clone(), v.name.clone()];
            terms.extend(v.aliases.clone());
            q.is_empty()
                || prep(&terms.join(" ")).contains(&q)
                || v.family.to_lowercase().contains(&q)
        })
        .collect()
}
