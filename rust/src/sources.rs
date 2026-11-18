use crate::{
    bibtex::rx,
    net::{HttpResponse, Net},
    textutil::{asciifold, namekey, norm_title, surname, title_ratio},
};
use scraper::{Html, Selector};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SourceRecord {
    pub source: String,
    pub found: bool,
    pub error: bool,
    pub title: String,
    pub authors: Vec<String>,
    pub raw_authors: Vec<String>,
    pub year: Option<String>,
    pub venue: String,
    pub doi: Option<String>,
    pub url: Option<String>,
    pub note: String,
    pub queried: Option<String>,
    pub match_ratio: Option<f64>,
    pub best_ratio: Option<f64>,
    pub publication_type: String,
}
impl SourceRecord {
    pub fn missing(source: &str, note: impl Into<String>) -> Self {
        Self {
            source: source.into(),
            note: note.into(),
            ..Self::default()
        }
    }
    pub fn failed(source: &str, note: impl Into<String>) -> Self {
        Self {
            error: true,
            ..Self::missing(source, note)
        }
    }
}
pub fn matches(reference: &SourceRecord, candidate: &SourceRecord) -> bool {
    let generic = ["team", "group", "anonymous", "collaboration", ""];
    reference.authors.iter().map(|a| namekey(a)).any(|a| {
        !generic.contains(&a.as_str()) && candidate.authors.iter().any(|b| namekey(b) == a)
    }) && title_ratio(&reference.title, &candidate.title) >= 0.95
}
fn string(v: &Value) -> String {
    v.as_str()
        .map(str::to_owned)
        .or_else(|| v.as_i64().map(|v| v.to_string()))
        .unwrap_or_default()
}
fn array(v: &Value) -> Vec<&Value> {
    if let Some(a) = v.as_array() {
        a.iter().collect()
    } else if v.is_null() {
        Vec::new()
    } else {
        vec![v]
    }
}
fn queries(title: &str) -> Vec<String> {
    let full = norm_title(title);
    if let Some((prefix, _)) = title.split_once(':') {
        let short = norm_title(prefix);
        if short.replace(' ', "").len() >= 5 && short != full {
            return vec![short, full];
        }
    }
    if full.is_empty() {
        Vec::new()
    } else {
        vec![full]
    }
}
fn query_url(base: &str, params: &[(&str, String)]) -> String {
    let q = url::form_urlencoded::Serializer::new(String::new())
        .extend_pairs(params.iter().map(|(k, v)| (*k, v.as_str())))
        .finish();
    format!("{base}?{q}")
}
fn json_response(response: &HttpResponse, source: &str) -> Result<Value, Box<SourceRecord>> {
    if response.status != Some(200) || response.body.is_empty() {
        Err(Box::new(SourceRecord::failed(source, response.failure())))
    } else {
        serde_json::from_str(&response.body)
            .map_err(|_| Box::new(SourceRecord::failed(source, "parse error")))
    }
}

pub fn arxiv_atom(body: &str) -> Vec<SourceRecord> {
    let Ok(doc) = roxmltree::Document::parse(body) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for entry in doc.descendants().filter(|n| n.has_tag_name("entry")) {
        let get = |name: &str| {
            entry
                .children()
                .find(|n| n.tag_name().name() == name)
                .and_then(|n| n.text())
                .unwrap_or("")
                .trim()
                .to_string()
        };
        let title = get("title");
        if title.is_empty() || title.to_lowercase().starts_with("error") {
            continue;
        }
        let raw_authors: Vec<String> = entry
            .children()
            .filter(|n| n.has_tag_name("author"))
            .filter_map(|n| {
                n.children()
                    .find(|c| c.has_tag_name("name"))
                    .and_then(|c| c.text())
                    .map(|s| s.trim().to_string())
            })
            .collect();
        let journal = crate::bibtex::clean_value(&get("journal_ref"));
        let comment = crate::bibtex::clean_value(&get("comment"));
        let mut notes = Vec::new();
        if !journal.is_empty() {
            notes.push(format!("journal_ref: {journal}"));
        }
        if !comment.is_empty() {
            notes.push(format!(
                "comment: {}",
                comment.chars().take(160).collect::<String>()
            ));
        }
        let published = get("published");
        let year = published
            .get(..4)
            .filter(|s| s.chars().all(|c| c.is_ascii_digit()))
            .map(str::to_owned);
        out.push(SourceRecord {
            source: "arXiv".into(),
            found: true,
            title,
            authors: raw_authors
                .iter()
                .map(|s| surname(s))
                .filter(|s| !s.is_empty())
                .collect(),
            raw_authors,
            year,
            venue: if journal.is_empty() {
                "arXiv".into()
            } else {
                journal
            },
            url: Some(get("id")),
            note: notes.join("; "),
            ..SourceRecord::default()
        });
    }
    out
}
pub fn arxiv(net: &Net, id: &str) -> SourceRecord {
    let url = query_url(
        "https://export.arxiv.org/api/query",
        &[("id_list", id.into())],
    );
    let response = net.get(&url, "", 1, 7);
    if response.status == Some(200) {
        if let Some(record) = arxiv_atom(&response.body).into_iter().next() {
            return record;
        }
    }
    let response = net.get(&format!("https://arxiv.org/abs/{id}"), "", 2, 8);
    if [Some(404), Some(410)].contains(&response.status) {
        return SourceRecord::missing("arXiv", "abs page 404");
    }
    if response.status != Some(200) {
        return SourceRecord::failed("arXiv", format!("abs page: {}", response.failure()));
    }
    arxiv_html(&response.body, id)
}
pub fn arxiv_html(body: &str, id: &str) -> SourceRecord {
    let html = Html::parse_document(body);
    let mut meta = std::collections::HashMap::<String, Vec<String>>::new();
    for node in html.select(&Selector::parse("meta").unwrap()) {
        if let (Some(name), Some(content)) =
            (node.value().attr("name"), node.value().attr("content"))
        {
            meta.entry(name.to_lowercase())
                .or_default()
                .push(content.to_string());
        }
    }
    let Some(title) = meta.get("citation_title").and_then(|v| v.first()) else {
        return SourceRecord::failed("arXiv", "abs page unparsed");
    };
    let journal = html
        .select(&Selector::parse(".jref").unwrap())
        .next()
        .map(|n| crate::bibtex::clean_value(&n.text().collect::<String>()))
        .unwrap_or_default();
    let journal = rx(r"(?i)^Journal\s+ref(?:erence)?\s*:\s*")
        .replace(&journal, "")
        .to_string();
    let comment = html
        .select(&Selector::parse(".comments").unwrap())
        .next()
        .map(|n| crate::bibtex::clean_value(&n.text().collect::<String>()))
        .unwrap_or_default();
    let comment = rx(r"(?i)^Comments?\s*:\s*")
        .replace(&comment, "")
        .to_string();
    let raw_authors: Vec<String> = meta
        .get("citation_author")
        .unwrap_or(&Vec::new())
        .iter()
        .map(|a| {
            if let Some((last, first)) = a.split_once(',') {
                format!("{} {}", first.trim(), last.trim()).trim().into()
            } else {
                a.trim().into()
            }
        })
        .collect();
    let date = meta
        .get("citation_date")
        .or_else(|| meta.get("citation_online_date"))
        .and_then(|v| v.first())
        .cloned()
        .unwrap_or_default();
    let year = rx(r"(?:19|20)\d\d")
        .find(&date)
        .map(|v| v.as_str().to_string());
    let mut notes = vec!["via abs page".to_string()];
    if !journal.is_empty() {
        notes.push(format!("journal_ref: {journal}"));
    }
    if !comment.is_empty() {
        notes.push(format!(
            "comment: {}",
            comment.chars().take(160).collect::<String>()
        ));
    }
    SourceRecord {
        source: "arXiv".into(),
        found: true,
        title: title.clone(),
        authors: raw_authors
            .iter()
            .map(|a| surname(a))
            .filter(|a| !a.is_empty())
            .collect(),
        raw_authors,
        year,
        venue: if journal.is_empty() {
            "arXiv".into()
        } else {
            journal
        },
        url: Some(format!("https://arxiv.org/abs/{id}")),
        note: notes.join("; "),
        ..SourceRecord::default()
    }
}
pub fn crossref_record(item: &Value) -> SourceRecord {
    let raw_authors: Vec<String> = array(&item["author"])
        .iter()
        .map(|a| {
            let family = a["family"]
                .as_str()
                .or_else(|| a["name"].as_str())
                .unwrap_or("");
            format!("{} {family}", a["given"].as_str().unwrap_or(""))
                .trim()
                .into()
        })
        .collect();
    let authors = array(&item["author"])
        .iter()
        .filter_map(|a| a["family"].as_str().or_else(|| a["name"].as_str()))
        .map(|a| {
            rx(r"[^a-z0-9\- ]")
                .replace_all(&asciifold(a).to_lowercase(), "")
                .into_owned()
        })
        .collect();
    let year = [
        "published",
        "published-print",
        "published-online",
        "issued",
        "created",
    ]
    .iter()
    .find_map(|key| {
        item[*key]["date-parts"][0][0]
            .as_i64()
            .map(|n| n.to_string())
    });
    SourceRecord {
        source: "Crossref".into(),
        found: true,
        title: item["title"][0].as_str().unwrap_or("").into(),
        authors,
        raw_authors,
        year,
        venue: item["container-title"][0].as_str().unwrap_or("").into(),
        doi: item["DOI"].as_str().map(str::to_owned),
        url: item["URL"].as_str().map(str::to_owned),
        publication_type: string(&item["type"]),
        ..SourceRecord::default()
    }
}
pub fn crossref(net: &Net, doi: &str) -> SourceRecord {
    let url = format!("https://api.crossref.org/works/{doi}");
    let response = net.get(&url, "application/json", 3, 30);
    if response.status == Some(404) {
        return SourceRecord::missing("Crossref", "DOI not registered");
    }
    match json_response(&response, "Crossref") {
        Ok(json) if json["message"].is_object() => crossref_record(&json["message"]),
        Ok(_) => SourceRecord::failed("Crossref", "parse error"),
        Err(e) => *e,
    }
}
pub fn crossref_search(net: &Net, title: &str, first: Option<&str>) -> SourceRecord {
    let q = norm_title(title);
    if q.is_empty() {
        return SourceRecord::missing("Crossref", "empty title");
    }
    let mut params = vec![
        ("rows", "10".into()),
        (
            "select",
            "title,author,issued,container-title,DOI,URL".into(),
        ),
        ("query.bibliographic", q),
    ];
    if let Some(first) = first {
        params.push(("query.author", first.into()));
    }
    let response = net.get(
        &query_url("https://api.crossref.org/works", &params),
        "application/json",
        3,
        30,
    );
    let json = match json_response(&response, "Crossref") {
        Ok(j) => j,
        Err(e) => return *e,
    };
    let Some(items) = json["message"]["items"].as_array() else {
        return SourceRecord::failed("Crossref", "parse error");
    };
    let mut best = None;
    let mut best_ratio = 0.0;
    for item in items {
        let ratio = title_ratio(title, item["title"][0].as_str().unwrap_or(""));
        if ratio > best_ratio {
            best = Some(item);
            best_ratio = ratio;
        }
    }
    if best_ratio < 0.9 || best.is_none() {
        return SourceRecord {
            best_ratio: Some((best_ratio * 100.0).round() / 100.0),
            ..SourceRecord::missing("Crossref", "no close Crossref title match")
        };
    }
    let mut record = crossref_record(best.unwrap());
    record.match_ratio = Some((best_ratio * 100.0).round() / 100.0);
    record
}
pub fn crossref_publication(net: &Net, reference: &SourceRecord) -> SourceRecord {
    for query in queries(&reference.title) {
        let response = net.get(
            &query_url(
                "https://api.crossref.org/works",
                &[("rows", "5".into()), ("query.bibliographic", query)],
            ),
            "application/json",
            1,
            8,
        );
        let json = match json_response(&response, "Crossref") {
            Ok(j) => j,
            Err(e) => return *e,
        };
        let Some(items) = json["message"]["items"].as_array() else {
            return SourceRecord::failed("Crossref", "parse error");
        };
        for item in items {
            if ["journal-article", "proceedings-article"]
                .contains(&item["type"].as_str().unwrap_or(""))
            {
                let record = crossref_record(item);
                if !record.venue.is_empty() && record.doi.is_some() && matches(reference, &record) {
                    return record;
                }
            }
        }
    }
    SourceRecord::missing("Crossref", "no matching journal or proceedings article")
}

pub fn openreview_record(note: &Value) -> Option<SourceRecord> {
    let content = note.get("content")?.as_object()?;
    let value = |key: &str| {
        let v = content.get(key).unwrap_or(&Value::Null);
        v.get("value").unwrap_or(v)
    };
    let title = value("title").as_str()?.trim().to_string();
    let forum = note["forum"]
        .as_str()
        .filter(|value| !value.is_empty())
        .or_else(|| note["id"].as_str())?;
    if title.is_empty() || forum.is_empty() {
        return None;
    }
    let raw_authors: Vec<String> = array(value("authors"))
        .into_iter()
        .filter_map(|a| a.as_str().or_else(|| a["fullname"].as_str()))
        .map(str::trim)
        .filter(|s| !s.is_empty() && !s.eq_ignore_ascii_case("anonymous"))
        .map(str::to_owned)
        .collect();
    let venue_id = value("venueid").as_str().unwrap_or("");
    let venue = value("venue")
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or(venue_id)
        .trim()
        .to_string();
    let year = rx(r"(?:19|20)\d\d")
        .find(&venue)
        .map(|m| m.as_str().into())
        .or_else(|| {
            ["pdate", "odate", "cdate"].iter().find_map(|k| {
                note[*k]
                    .as_f64()
                    .filter(|n| *n > 0.0)
                    .and_then(|n| chrono::DateTime::from_timestamp_millis(n as i64))
                    .map(|d| d.format("%Y").to_string())
            })
        });
    let accepted = note["id"].as_str() == Some(forum)
        && !venue_id.is_empty()
        && note["domain"].as_str() == Some(venue_id)
        && note["pdate"].as_f64().is_some_and(|n| n > 0.0)
        && note["ddate"].as_i64().unwrap_or(0) == 0
        && !rx(r"(?i)/(?:submission|withdrawn|rejected|desk_rejected)(?:/|$)").is_match(venue_id);
    Some(SourceRecord {
        source: "OpenReview".into(),
        found: true,
        title,
        authors: raw_authors
            .iter()
            .map(|a| surname(a))
            .filter(|a| !a.is_empty())
            .collect(),
        raw_authors,
        year,
        venue,
        url: Some(query_url(
            "https://openreview.net/forum",
            &[("id", forum.into())],
        )),
        publication_type: if accepted {
            "accepted".into()
        } else {
            String::new()
        },
        ..SourceRecord::default()
    })
}
pub fn openreview(net: &Net, id: &str) -> SourceRecord {
    let mut error = None;
    for parameter in ["id", "forum"] {
        let response = net.get(
            &query_url(
                "https://api2.openreview.net/notes",
                &[(parameter, id.into())],
            ),
            "application/json",
            3,
            30,
        );
        let json = match json_response(&response, "OpenReview") {
            Ok(j) => j,
            Err(e) => {
                error = Some(*e);
                continue;
            }
        };
        let Some(notes) = json["notes"].as_array() else {
            error = Some(SourceRecord::failed("OpenReview", "parse error"));
            continue;
        };
        for note in notes {
            if note["id"].as_str() == Some(id) {
                if let Some(record) = openreview_record(note) {
                    return record;
                }
            }
        }
    }
    error.unwrap_or_else(|| SourceRecord::missing("OpenReview", "forum id not found"))
}
pub fn openreview_publication(net: &Net, reference: &SourceRecord) -> SourceRecord {
    for query in queries(&reference.title) {
        let url = query_url(
            "https://api2.openreview.net/notes/search",
            &[
                ("term", query),
                ("content", "title".into()),
                ("source", "forum".into()),
                ("limit", "10".into()),
            ],
        );
        let response = net.get(&url, "application/json", 1, 8);
        let json = match json_response(&response, "OpenReview") {
            Ok(j) => j,
            Err(e) => return *e,
        };
        let Some(notes) = json["notes"].as_array() else {
            return SourceRecord::failed("OpenReview", "parse error");
        };
        for note in notes {
            if let Some(record) = openreview_record(note) {
                if record.publication_type == "accepted" && matches(reference, &record) {
                    return record;
                }
            }
        }
    }
    SourceRecord::missing("OpenReview", "no matching public acceptance record")
}
pub fn dblp_record(json: &Value, title: &str) -> SourceRecord {
    let hits = &json["result"]["hits"];
    if hits["@total"].as_str().unwrap_or("0") == "0" || hits["hit"].is_null() {
        return SourceRecord::missing("DBLP", "no DBLP hit");
    }
    let mut scores: Vec<_> = array(&hits["hit"])
        .iter()
        .map(|h| {
            (
                title_ratio(title, h["info"]["title"].as_str().unwrap_or("")),
                h["info"].clone(),
            )
        })
        .collect();
    scores.sort_by(|a, b| b.0.total_cmp(&a.0));
    let ratio = scores.first().map(|s| s.0).unwrap_or(0.0);
    if ratio < 0.82 {
        return SourceRecord {
            best_ratio: Some((ratio * 100.0).round() / 100.0),
            ..SourceRecord::missing("DBLP", "no close DBLP title match")
        };
    }
    let near: Vec<_> = scores.iter().filter(|s| ratio - s.0 <= 0.03).collect();
    let best = &near
        .iter()
        .find(|s| string(&s.1["venue"]).to_uppercase() != "CORR")
        .unwrap_or(&near[0])
        .1;
    let raw_authors: Vec<String> = array(&best["authors"]["author"])
        .into_iter()
        .map(|a| {
            a["text"]
                .as_str()
                .unwrap_or_else(|| a.as_str().unwrap_or(""))
                .to_string()
        })
        .collect();
    SourceRecord {
        source: "DBLP".into(),
        found: true,
        title: string(&best["title"]),
        authors: raw_authors
            .iter()
            .map(|a| surname(a))
            .filter(|a| !a.is_empty())
            .collect(),
        raw_authors,
        year: best["year"]
            .as_str()
            .filter(|year| !year.is_empty())
            .map(str::to_owned)
            .or_else(|| best["year"].as_i64().map(|year| year.to_string())),
        venue: string(&best["venue"]),
        publication_type: string(&best["type"]),
        doi: best["doi"].as_str().map(str::to_owned),
        url: best["ee"]
            .as_str()
            .or_else(|| best["url"].as_str())
            .map(str::to_owned),
        match_ratio: Some((ratio * 100.0).round() / 100.0),
        ..SourceRecord::default()
    }
}
pub fn dblp_search(
    net: &Net,
    title: &str,
    first: Option<&str>,
    query: Option<&str>,
    retries: usize,
    timeout: u64,
) -> SourceRecord {
    let mut q = query
        .map(str::to_owned)
        .unwrap_or_else(|| norm_title(title));
    if q.is_empty() {
        return SourceRecord::missing("DBLP", "empty title");
    }
    if let Some(first) = first {
        q.push(' ');
        q.push_str(first);
    }
    let response = net.get(
        &query_url(
            "https://dblp.org/search/publ/api",
            &[("format", "json".into()), ("h", "30".into()), ("q", q)],
        ),
        "application/json",
        retries,
        timeout,
    );
    match json_response(&response, "DBLP") {
        Ok(j) if j["result"]["hits"].is_object() => dblp_record(&j, title),
        Ok(_) => SourceRecord::failed("DBLP", "parse error"),
        Err(e) => *e,
    }
}
pub fn dblp_publication(net: &Net, reference: &SourceRecord) -> SourceRecord {
    for q in queries(&reference.title) {
        let record = dblp_search(net, &reference.title, None, Some(&q), 1, 8);
        if record.error || record.found && matches(reference, &record) {
            return record;
        }
    }
    SourceRecord::missing("DBLP", "no matching publication record")
}
