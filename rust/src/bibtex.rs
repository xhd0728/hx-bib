use anyhow::{bail, Result};
use indexmap::IndexMap;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashMap},
    sync::{LazyLock, RwLock},
};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    #[serde(rename = "type")]
    pub kind: String,
    pub key: String,
    pub fields: IndexMap<String, String>,
    pub bare_fields: BTreeSet<String>,
    pub line: usize,
    pub source_span: Option<(usize, usize)>,
}

impl Entry {
    pub fn get(&self, name: &str) -> &str {
        self.fields.get(name).map(String::as_str).unwrap_or("")
    }

    pub fn set(&mut self, name: &str, value: impl Into<String>) {
        let name = name.trim().to_lowercase();
        self.bare_fields.remove(&name);
        self.fields.insert(name, value.into());
    }

    pub fn remove(&mut self, name: &str) -> bool {
        self.bare_fields.remove(name);
        self.fields.shift_remove(name).is_some()
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Document {
    pub entries: Vec<Entry>,
    pub strings: Vec<String>,
    pub preamble: Vec<String>,
    pub comments: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn rx(pattern: &str) -> Regex {
    static PATTERNS: LazyLock<RwLock<HashMap<String, Regex>>> =
        LazyLock::new(|| RwLock::new(HashMap::new()));
    if let Some(regex) = PATTERNS.read().unwrap().get(pattern) {
        return regex.clone();
    }
    let regex = Regex::new(pattern).expect("valid built-in pattern");
    PATTERNS
        .write()
        .unwrap()
        .insert(pattern.into(), regex.clone());
    regex
}

pub fn clean_value(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn closing(text: &str, start: usize, open: u8) -> (usize, usize, bool) {
    let mut depth = 1_usize;
    for (offset, ch) in text.as_bytes()[start..].iter().enumerate() {
        match ch {
            b'{' => depth += 1,
            b'}' => {
                depth = depth.saturating_sub(1);
                if open == b'{' && depth == 0 {
                    return (start + offset, start + offset + 1, true);
                }
            }
            b')' if open == b'(' && depth == 1 => {
                return (start + offset, start + offset + 1, true)
            }
            _ => {}
        }
    }
    (text.len(), text.len(), false)
}

fn top_comma(text: &str) -> Option<usize> {
    let mut depth = 0_usize;
    let mut quoted = false;
    for (i, ch) in text.bytes().enumerate() {
        match ch {
            b'"' if depth == 0 => quoted = !quoted,
            b'{' => depth += 1,
            b'}' => depth = depth.saturating_sub(1),
            b',' if depth == 0 && !quoted => return Some(i),
            _ => {}
        }
    }
    None
}

fn fields(text: &str, entry: &mut Entry) {
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        while i < bytes.len() && b" \t\r\n,".contains(&bytes[i]) {
            i += 1;
        }
        let Some(equal) = text[i..].find('=').map(|v| v + i) else {
            break;
        };
        let name = text[i..equal].trim().to_lowercase();
        if name.is_empty() || name.contains([',', '{', '}']) {
            break;
        }
        i = equal + 1;
        let value_start = i;
        let mut value_end = i;
        let mut parts = Vec::new();
        let mut bare = true;
        let mut has_macro = false;
        loop {
            while i < bytes.len() && b" \t\r\n".contains(&bytes[i]) {
                i += 1;
            }
            if i == bytes.len() {
                break;
            }
            if bytes[i] == b'#' {
                i += 1;
                continue;
            }
            if bytes[i] == b'{' {
                let (stop, end, _) = closing(text, i + 1, b'{');
                parts.push(text[i + 1..stop].to_string());
                i = end;
                bare = false;
            } else if bytes[i] == b'"' {
                let start = i + 1;
                i = start;
                let mut depth = 0_usize;
                while i < bytes.len() {
                    match bytes[i] {
                        b'{' => depth += 1,
                        b'}' => depth = depth.saturating_sub(1),
                        b'"' if depth == 0 => break,
                        _ => {}
                    }
                    i += 1;
                }
                parts.push(text[start..i].to_string());
                i = (i + 1).min(bytes.len());
                bare = false;
            } else {
                let start = i;
                while i < bytes.len() && !b",#} \t\r\n".contains(&bytes[i]) {
                    i += 1;
                }
                parts.push(text[start..i].trim().to_string());
                has_macro = true;
            }
            value_end = i;
            while i < bytes.len() && b" \t\r\n".contains(&bytes[i]) {
                i += 1;
            }
            if i < bytes.len() && bytes[i] == b'#' {
                i += 1;
                continue;
            }
            break;
        }
        let expression = parts.len() > 1 && has_macro;
        let value = if expression {
            text[value_start..value_end].trim().to_string()
        } else {
            parts.concat().trim().to_string()
        };
        entry.set(&name, value);
        if bare || expression {
            entry.bare_fields.insert(name);
        }
        while i < bytes.len() && b" \t\r\n,".contains(&bytes[i]) {
            i += 1;
        }
    }
}

pub fn parse(text: &str) -> Document {
    let head = rx(r"^@\s*([A-Za-z]+)\s*([{(])");
    let mut doc = Document::default();
    let mut cursor = 0;
    while cursor < text.len() {
        let Some(at) = text[cursor..].find('@').map(|p| p + cursor) else {
            if !text[cursor..].trim().is_empty() {
                doc.comments.push(text[cursor..].trim().to_string());
            }
            break;
        };
        if !text[cursor..at].trim().is_empty() {
            doc.comments.push(text[cursor..at].trim().to_string());
        }
        let Some(cap) = head.captures(&text[at..]) else {
            cursor = at + 1;
            continue;
        };
        let kind = cap[1].to_lowercase();
        let open = cap[2].as_bytes()[0];
        let body_start = at + cap.get(0).unwrap().end();
        let (stop, end, valid) = closing(text, body_start, open);
        let line = text[..at].bytes().filter(|v| *v == b'\n').count() + 1;
        cursor = end;
        if ["comment", "preamble", "string"].contains(&kind.as_str()) {
            if !valid {
                doc.warnings
                    .push(format!("line {line}: unterminated @{kind} block"));
            }
            let target = match kind.as_str() {
                "preamble" => &mut doc.preamble,
                "string" => &mut doc.strings,
                _ => &mut doc.comments,
            };
            target.push(text[at..end].trim().to_string());
            continue;
        }
        let body = &text[body_start..stop];
        let comma = top_comma(body);
        let key = body[..comma.unwrap_or(body.len())]
            .trim()
            .trim_end_matches(',');
        let error = if !valid {
            Some(format!(
                "line {line}: unterminated entry @{kind}{}…",
                open as char
            ))
        } else if key.is_empty() {
            Some(format!("line {line}: entry @{kind} has no cite key"))
        } else {
            None
        };
        if !key.is_empty() {
            let mut entry = Entry {
                kind,
                key: key.to_string(),
                line,
                source_span: Some((at, end)),
                ..Entry::default()
            };
            fields(comma.map(|p| &body[p + 1..]).unwrap_or(""), &mut entry);
            doc.entries.push(entry);
        }
        if let Some(error) = error {
            doc.warnings.push(error);
        }
    }
    doc
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(default)]
pub struct FormatOptions {
    pub indent: String,
    pub align_equals: bool,
    pub field_order: String,
    pub sort_entries: String,
    pub trailing_comma: bool,
    pub drop_comments: bool,
    pub blank_lines_between_entries: usize,
}

impl Default for FormatOptions {
    fn default() -> Self {
        Self {
            indent: "4".into(),
            align_equals: false,
            field_order: "standard".into(),
            sort_entries: "none".into(),
            trailing_comma: false,
            drop_comments: false,
            blank_lines_between_entries: 1,
        }
    }
}

impl FormatOptions {
    pub fn validate(&self) -> Result<()> {
        if !["align", "0", "tab"].contains(&self.indent.as_str())
            && (self.indent.is_empty() || !self.indent.chars().all(|c| c.is_ascii_digit()))
        {
            bail!(
                "invalid indent {:?}: use a number of spaces, 'tab' or 'align'",
                self.indent
            );
        }
        if !["standard", "alphabetical", "keep"].contains(&self.field_order.as_str()) {
            bail!("invalid field_order {:?}", self.field_order);
        }
        if !["none", "key", "year", "type", "title"].contains(&self.sort_entries.as_str()) {
            bail!("invalid sort_entries {:?}", self.sort_entries);
        }
        Ok(())
    }
}

pub const STANDARD_FIELDS: &[&str] = &[
    "author",
    "title",
    "booktitle",
    "journal",
    "year",
    "volume",
    "number",
    "pages",
    "month",
    "editor",
    "series",
    "publisher",
    "address",
    "organization",
    "school",
    "institution",
    "edition",
    "chapter",
    "howpublished",
    "type",
    "note",
    "doi",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "url",
    "issn",
    "isbn",
    "language",
    "abstract",
    "keywords",
];

pub fn render_entry(entry: &Entry, opts: &FormatOptions) -> String {
    let indent = match opts.indent.as_str() {
        "tab" => "\t".into(),
        "align" => " ".repeat(entry.kind.len() + 2),
        _ => " ".repeat(opts.indent.parse::<usize>().unwrap_or(4)),
    };
    let mut names: Vec<_> = entry.fields.keys().collect();
    match opts.field_order.as_str() {
        "alphabetical" => names.sort(),
        "standard" => names.sort_by_key(|name| {
            STANDARD_FIELDS
                .iter()
                .position(|n| n == name)
                .unwrap_or(STANDARD_FIELDS.len())
        }),
        _ => {}
    }
    let width = if opts.align_equals {
        names.iter().map(|n| n.chars().count()).max().unwrap_or(0)
    } else {
        0
    };
    let mut lines = vec![format!("@{}{{{},", entry.kind, entry.key)];
    for (i, name) in names.iter().enumerate() {
        let value = &entry.fields[*name];
        let rendered = if entry.bare_fields.contains(*name) {
            value.clone()
        } else {
            format!("{{{}}}", clean_value(value))
        };
        let padding = " ".repeat(width.saturating_sub(name.chars().count()));
        let comma = if opts.trailing_comma || i < names.len() - 1 {
            ","
        } else {
            ""
        };
        lines.push(format!("{indent}{name}{padding} = {rendered}{comma}"));
    }
    lines.push("}".into());
    lines.join("\n")
}

pub fn render(doc: &Document, opts: &FormatOptions) -> Result<String> {
    opts.validate()?;
    let mut blocks = doc.preamble.clone();
    blocks.extend(doc.strings.clone());
    if !opts.drop_comments {
        blocks.extend(doc.comments.iter().map(|s| s.trim().to_string()));
    }
    let mut entries: Vec<_> = doc.entries.iter().collect();
    match opts.sort_entries.as_str() {
        "key" => entries.sort_by_key(|e| e.key.to_lowercase()),
        "type" => entries.sort_by_key(|e| (&e.kind, e.key.to_lowercase())),
        "title" => entries.sort_by_key(|e| e.get("title").to_lowercase()),
        "year" => entries.sort_by_key(|e| {
            let year: String = e.get("year").chars().filter(|c| c.is_numeric()).collect();
            (
                if year.is_empty() { "9999".into() } else { year },
                e.key.to_lowercase(),
            )
        }),
        _ => {}
    }
    blocks.extend(entries.into_iter().map(|entry| render_entry(entry, opts)));
    Ok(format!(
        "{}\n",
        blocks
            .join(&"\n".repeat(opts.blank_lines_between_entries + 1))
            .trim_end()
    ))
}
