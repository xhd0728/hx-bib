use crate::bibtex::rx;
use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

const ENVS: &[&str] = &[
    "tabular",
    "tabular*",
    "tabularx",
    "tabulary",
    "longtable",
    "array",
];
const RULES: &[&str] = &[
    "toprule",
    "midrule",
    "bottomrule",
    "hline",
    "cline",
    "cmidrule",
    "specialrule",
    "addlinespace",
    "morecmidrules",
    "endfirsthead",
    "endhead",
    "endfoot",
    "endlastfoot",
];

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct TableOptions {
    pub amp_style: String,
    pub blank_lines_around_rules: bool,
    pub row_indent: i32,
    pub indent_unit: String,
}
impl Default for TableOptions {
    fn default() -> Self {
        Self {
            amp_style: "space".into(),
            blank_lines_around_rules: true,
            row_indent: 0,
            indent_unit: " ".into(),
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Cell {
    pub text: String,
    pub span: usize,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Element {
    Row { cells: Vec<Cell>, end: String },
    Rule { text: String },
    Comment { text: String },
}
#[derive(Clone, Debug, Serialize)]
pub struct TableSlice {
    pub name: String,
    pub begin: usize,
    pub body_start: usize,
    pub body_end: usize,
    pub end: usize,
    pub colspec: String,
    pub start_line: usize,
}
#[derive(Clone, Debug, Serialize)]
pub struct TableResult {
    pub text: String,
    pub tables: usize,
    pub rows: usize,
    pub warnings: Vec<String>,
}

fn skip_group(chars: &[char], mut i: usize, open: char, close: char) -> usize {
    let mut depth = 0_usize;
    while i < chars.len() {
        let c = chars[i];
        if c == '\\' {
            i = (i + 2).min(chars.len());
            continue;
        }
        if c == open {
            depth += 1;
        } else if c == close {
            depth = depth.saturating_sub(1);
            if depth == 0 {
                return i + 1;
            }
        }
        i += 1;
    }
    chars.len()
}
fn collect(chars: &[char]) -> String {
    chars.iter().collect()
}
fn flush_cell(buf: &mut String, cells: &mut Vec<Cell>) {
    let text = rx(r"[ \t]*\r?\n\s*")
        .replace_all(buf, " ")
        .trim()
        .to_string();
    let span = rx(r"^\\multicolumn\s*\{\s*(\d+)\s*\}")
        .captures(&text)
        .and_then(|c| c[1].parse::<usize>().ok())
        .unwrap_or(1)
        .max(1);
    cells.push(Cell { text, span });
    buf.clear();
}
fn flush_row(buf: &mut String, cells: &mut Vec<Cell>, elements: &mut Vec<Element>, end: String) {
    flush_cell(buf, cells);
    if cells.len() > 1 || cells.iter().any(|c| !c.text.is_empty()) {
        elements.push(Element::Row {
            cells: std::mem::take(cells),
            end,
        });
    }
    cells.clear();
}

pub fn parse_body(body: &str, warnings: &mut Vec<String>) -> Vec<Element> {
    let chars: Vec<_> = body.chars().collect();
    let mut elements = Vec::new();
    let mut cells = Vec::new();
    let mut buf = String::new();
    let mut envs = Vec::<String>::new();
    let mut depth = 0_usize;
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '\\' {
            let start = i;
            let mut next = i + 1;
            while next < chars.len() && chars[next].is_ascii_alphabetic() {
                next += 1;
            }
            if next == i + 1 && next < chars.len() {
                next += 1;
            }
            let word = collect(&chars[i..next]);
            let top = depth == 0 && envs.is_empty();
            if top && ["\\\\", "\\tabularnewline", "\\cr"].contains(&word.as_str()) {
                let mut end = word.clone();
                let mut j = next;
                if word == "\\\\" && chars.get(j) == Some(&'*') {
                    end.push('*');
                    j += 1;
                }
                if word != "\\cr" && chars.get(j) == Some(&'[') {
                    let k = skip_group(&chars, j, '[', ']');
                    end.push_str(&collect(&chars[j..k]));
                    j = k;
                }
                flush_row(&mut buf, &mut cells, &mut elements, end);
                i = j;
                continue;
            }
            if word == "\\begin" || word == "\\end" {
                let suffix = collect(&chars[i..]);
                let pat = if word == "\\begin" {
                    r"^\\begin\s*\{([A-Za-z*]+)\}"
                } else {
                    r"^\\end\s*\{([A-Za-z*]+)\}"
                };
                if let Some(cap) = rx(pat).captures(&suffix) {
                    let raw = cap.get(0).unwrap().as_str();
                    if word == "\\begin" {
                        envs.push(cap[1].into());
                    } else if let Some(last) = envs.pop() {
                        if last != cap[1] {
                            warnings.push(format!("mismatched \\end{{{}}}", &cap[1]));
                        }
                    }
                    buf.push_str(raw);
                    i += raw.chars().count();
                    continue;
                }
            }
            if top
                && RULES.contains(&word[1..].as_ref())
                && buf.trim().is_empty()
                && cells.is_empty()
            {
                let mut j = next;
                while j < chars.len() {
                    match chars[j] {
                        ' ' | '\t' => j += 1,
                        '(' => j = skip_group(&chars, j, '(', ')'),
                        '[' => j = skip_group(&chars, j, '[', ']'),
                        '{' => j = skip_group(&chars, j, '{', '}'),
                        _ => break,
                    }
                }
                elements.push(Element::Rule {
                    text: collect(&chars[start..j]).trim().into(),
                });
                i = j;
                continue;
            }
            buf.push_str(&word);
            i = next;
            continue;
        }
        match c {
            '{' => {
                depth += 1;
                buf.push(c);
            }
            '}' => {
                depth = depth.saturating_sub(1);
                buf.push(c);
            }
            '&' if depth == 0 && envs.is_empty() => flush_cell(&mut buf, &mut cells),
            '%' if envs.is_empty() => {
                let j = (i..chars.len())
                    .find(|j| chars[*j] == '\n')
                    .unwrap_or(chars.len());
                let comment = collect(&chars[i..j]).trim().to_string();
                if !cells.is_empty() || !buf.trim().is_empty() {
                    warnings.push(format!(
                        "dropped comment: {}",
                        comment.chars().take(60).collect::<String>()
                    ));
                } else if !comment.is_empty() {
                    elements.push(Element::Comment { text: comment });
                }
                i = j;
                continue;
            }
            _ => buf.push(c),
        }
        i += 1;
    }
    if !buf.trim().is_empty() || cells.iter().any(|c| !c.text.is_empty()) {
        flush_cell(&mut buf, &mut cells);
        if cells.iter().any(|c| !c.text.is_empty()) {
            elements.push(Element::Row {
                cells,
                end: String::new(),
            });
            warnings.push("unterminated row (missing \\\\)".into());
        }
    }
    if !envs.is_empty() {
        warnings.push(format!("unclosed environment(s): {}", envs.join(", ")));
    }
    elements
}

pub fn find_tables(text: &str) -> (Vec<TableSlice>, Vec<String>) {
    let begins = rx(r"\\begin\s*\{([A-Za-z*]+)\}");
    let ends = rx(r"\\end\s*\{([A-Za-z*]+)\}");
    let mut slices = Vec::new();
    let mut warnings = Vec::new();
    let mut cursor = 0;
    while let Some(cap) = begins.captures(&text[cursor..]) {
        let mat = cap.get(0).unwrap();
        let begin = cursor + mat.start();
        let opening_end = cursor + mat.end();
        let name = cap[1].to_string();
        cursor = opening_end;
        if !ENVS.contains(&name.as_str()) {
            continue;
        }
        let want = if ["tabular*", "tabularx", "tabulary"].contains(&name.as_str()) {
            2
        } else {
            1
        };
        let suffix: Vec<_> = text[opening_end..].chars().collect();
        let mut i = 0;
        let mut groups = Vec::new();
        while groups.len() < want && i < suffix.len() {
            match suffix[i] {
                ' ' | '\t' | '\r' | '\n' => i += 1,
                '[' if groups.is_empty() => i = skip_group(&suffix, i, '[', ']'),
                '{' => {
                    let end = skip_group(&suffix, i, '{', '}');
                    groups.push(collect(&suffix[i..end]));
                    i = end;
                }
                _ => {
                    warnings.push(format!("{name}: malformed opening arguments"));
                    break;
                }
            }
        }
        let body_start = opening_end + suffix[..i].iter().map(|c| c.len_utf8()).sum::<usize>();
        let mut j = body_start;
        let mut depth = 1;
        let mut found = None;
        while let Some(end_cap) = ends.captures(&text[j..]) {
            let end_match = end_cap.get(0).unwrap();
            let end_start = j + end_match.start();
            let end = j + end_match.end();
            if let Some(begin_cap) = begins.captures(&text[j..]) {
                let next = begin_cap.get(0).unwrap();
                if j + next.start() < end_start {
                    if begin_cap[1] == name {
                        depth += 1;
                    }
                    j += next.end();
                    continue;
                }
            }
            if end_cap[1] == name {
                depth -= 1;
                if depth == 0 {
                    found = Some((end_start, end));
                    break;
                }
            }
            j = end;
        }
        if let Some((body_end, end)) = found {
            slices.push(TableSlice {
                name,
                begin,
                body_start,
                body_end,
                end,
                colspec: groups.last().cloned().unwrap_or_default(),
                start_line: text[..begin].matches('\n').count(),
            });
            cursor = end;
        } else {
            warnings.push(format!("unclosed {name} (skipped)"));
        }
    }
    (slices, warnings)
}

fn span_content(text: &str) -> String {
    if !text.ends_with('}') {
        return text.into();
    }
    let mut depth = 0;
    for (i, c) in text.char_indices().rev() {
        if c == '}' {
            depth += 1;
        } else if c == '{' {
            depth -= 1;
            if depth == 0 {
                return text[i + 1..text.len() - 1].into();
            }
        }
    }
    text.into()
}
pub fn format_tables(text: &str, opts: &TableOptions) -> Result<TableResult> {
    if !["space", "align", "none"].contains(&opts.amp_style.as_str()) {
        bail!("unknown amp_style: {}", opts.amp_style);
    }
    if opts.row_indent < 0 {
        bail!("row_indent must be >= 0");
    }
    let (slices, mut warnings) = find_tables(text);
    let mut result = String::new();
    let mut cursor = 0;
    let mut rows = 0;
    for sl in &slices {
        let line_start = text[..sl.begin].rfind('\n').map(|n| n + 1).unwrap_or(0);
        let indent = &text[line_start..sl.begin];
        let indent = if indent.trim().is_empty() { indent } else { "" };
        let elements = parse_body(&text[sl.body_start..sl.body_end], &mut warnings);
        let mut widths = Vec::<usize>::new();
        for el in &elements {
            if let Element::Row { cells, .. } = el {
                rows += 1;
                let mut col = 0;
                for cell in cells {
                    if cell.span == 1 {
                        widths.resize(widths.len().max(col + 1), 0);
                        widths[col] = widths[col].max(cell.text.chars().count());
                    }
                    col += cell.span;
                }
            }
        }
        let mut lines = Vec::<String>::new();
        for (idx, el) in elements.iter().enumerate() {
            match el {
                Element::Rule { text } => {
                    if opts.blank_lines_around_rules
                        && !lines.is_empty()
                        && !lines.last().unwrap().is_empty()
                        && !matches!(
                            elements.get(idx.wrapping_sub(1)),
                            Some(Element::Rule { .. })
                        )
                    {
                        lines.push(String::new());
                    }
                    lines.push(text.clone());
                    if opts.blank_lines_around_rules
                        && elements
                            .get(idx + 1)
                            .is_some_and(|v| matches!(v, Element::Row { .. }))
                    {
                        lines.push(String::new());
                    }
                }
                Element::Comment { text } => lines.push(text.clone()),
                Element::Row { cells, end } => {
                    let mut col = 0;
                    let mut parts = Vec::new();
                    for cell in cells {
                        let mut part = cell.text.clone();
                        if opts.amp_style == "align" {
                            let pad = if cell.span == 1 {
                                widths
                                    .get(col)
                                    .copied()
                                    .unwrap_or(cell.text.chars().count())
                                    .saturating_sub(cell.text.chars().count())
                            } else {
                                widths
                                    .iter()
                                    .skip(col)
                                    .take(cell.span)
                                    .sum::<usize>()
                                    .saturating_add(3 * (cell.span - 1))
                                    .saturating_sub(span_content(&cell.text).chars().count())
                            };
                            part.push_str(&" ".repeat(pad));
                        }
                        parts.push(part);
                        col += cell.span;
                    }
                    let sep = if opts.amp_style == "none" { "&" } else { " & " };
                    let mut line = parts.join(sep);
                    if opts.amp_style == "align" {
                        line = line.trim_end().into();
                    }
                    if !end.is_empty() {
                        line.push(' ');
                        line.push_str(end);
                    }
                    lines.push(line);
                }
            }
        }
        while lines.last().is_some_and(|v| v.is_empty()) {
            lines.pop();
        }
        let prefix = format!(
            "{indent}{}",
            opts.indent_unit.repeat(opts.row_indent as usize)
        );
        let rendered = lines
            .iter()
            .map(|line| {
                if line.is_empty() {
                    String::new()
                } else {
                    format!("{prefix}{line}")
                }
            })
            .collect::<Vec<_>>()
            .join("\n");
        result.push_str(&text[cursor..sl.begin]);
        result.push_str(text[sl.begin..sl.body_start].trim_end());
        if !rendered.is_empty() {
            result.push('\n');
            result.push_str(&rendered);
        }
        result.push('\n');
        result.push_str(indent);
        result.push_str(text[sl.body_end..sl.end].trim());
        cursor = sl.end;
    }
    result.push_str(&text[cursor..]);
    Ok(TableResult {
        text: result,
        tables: slices.len(),
        rows,
        warnings,
    })
}
