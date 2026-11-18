use crate::bibtex::{clean_value, parse, Entry};
use serde::Serialize;

#[derive(Clone, Debug, Serialize)]
pub struct Opcode {
    pub tag: String,
    pub i1: usize,
    pub i2: usize,
    pub j1: usize,
    pub j2: usize,
}

fn longest<T: Eq>(
    a: &[T],
    b: &[T],
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
    popular: &[bool],
) -> (usize, usize, usize) {
    let (mut best_i, mut best_j, mut size) = (alo, blo, 0);
    let mut previous = vec![0; bhi + 1];
    for (i, value) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut next = vec![0; bhi + 1];
        for j in blo..bhi {
            if !popular[j] && *value == b[j] {
                let n = previous[j] + 1;
                next[j + 1] = n;
                if n > size {
                    best_i = i + 1 - n;
                    best_j = j + 1 - n;
                    size = n;
                }
            }
        }
        previous = next;
    }
    while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
        best_i -= 1;
        best_j -= 1;
        size += 1;
    }
    while best_i + size < ahi && best_j + size < bhi && a[best_i + size] == b[best_j + size] {
        size += 1;
    }
    (best_i, best_j, size)
}
pub fn matching_blocks<T: Eq>(a: &[T], b: &[T]) -> Vec<(usize, usize, usize)> {
    blocks(a, b, &vec![false; b.len()])
}

fn blocks<T: Eq>(a: &[T], b: &[T], popular: &[bool]) -> Vec<(usize, usize, usize)> {
    let mut queue = vec![(0, a.len(), 0, b.len())];
    let mut matches = Vec::new();
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, n) = longest(a, b, alo, ahi, blo, bhi, popular);
        if n > 0 {
            matches.push((i, j, n));
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + n < ahi && j + n < bhi {
                queue.push((i + n, ahi, j + n, bhi));
            }
        }
    }
    matches.sort();
    let mut merged: Vec<(usize, usize, usize)> = Vec::new();
    for (i, j, n) in matches {
        if let Some(last) = merged.last_mut() {
            if last.0 + last.2 == i && last.1 + last.2 == j {
                last.2 += n;
                continue;
            }
        }
        merged.push((i, j, n));
    }
    merged.push((a.len(), b.len(), 0));
    merged
}
pub fn opcodes<T: Eq>(a: &[T], b: &[T]) -> Vec<Opcode> {
    let (mut i, mut j) = (0, 0);
    let mut out = Vec::new();
    for (ai, bj, n) in matching_blocks(a, b) {
        let tag = if i < ai && j < bj {
            "replace"
        } else if i < ai {
            "delete"
        } else if j < bj {
            "insert"
        } else {
            ""
        };
        if !tag.is_empty() {
            out.push(Opcode {
                tag: tag.into(),
                i1: i,
                i2: ai,
                j1: j,
                j2: bj,
            });
        }
        if n > 0 {
            out.push(Opcode {
                tag: "equal".into(),
                i1: ai,
                i2: ai + n,
                j1: bj,
                j2: bj + n,
            });
        }
        i = ai + n;
        j = bj + n;
    }
    out
}
pub fn ratio(a: &str, b: &str) -> f64 {
    let a: Vec<_> = a.chars().collect();
    let b: Vec<_> = b.chars().collect();
    if a.is_empty() && b.is_empty() {
        1.0
    } else {
        let mut frequency = std::collections::HashMap::new();
        for ch in &b {
            *frequency.entry(ch).or_insert(0_usize) += 1;
        }
        let popular: Vec<_> = b
            .iter()
            .map(|ch| b.len() >= 200 && frequency[ch] > b.len() / 100 + 1)
            .collect();
        2.0 * blocks(&a, &b, &popular).iter().map(|v| v.2).sum::<usize>() as f64
            / (a.len() + b.len()) as f64
    }
}

fn identity(e: &Entry) -> Vec<(String, String)> {
    let mut values: Vec<_> = e
        .fields
        .iter()
        .map(|(k, v)| (k.clone(), clean_value(v)))
        .collect();
    values.push(("@type".into(), e.kind.clone()));
    values.sort();
    values
}
pub fn match_entries(before: &[Entry], after: &[Entry]) -> Vec<(Option<usize>, Option<usize>)> {
    let mut used = vec![false; after.len()];
    let mut matches = vec![None; before.len()];
    for (i, old) in before.iter().enumerate() {
        if let Some(j) = after
            .iter()
            .enumerate()
            .position(|(j, new)| !used[j] && old.key == new.key && identity(old) == identity(new))
        {
            matches[i] = Some(j);
            used[j] = true;
        }
    }
    for (i, old) in before.iter().enumerate() {
        if matches[i].is_none() {
            if let Some(j) = after
                .iter()
                .enumerate()
                .position(|(j, new)| !used[j] && old.key == new.key)
            {
                matches[i] = Some(j);
                used[j] = true;
            }
        }
    }
    let mut pairs: Vec<_> = matches
        .into_iter()
        .enumerate()
        .map(|(i, j)| (Some(i), j))
        .collect();
    pairs.extend(
        used.iter()
            .enumerate()
            .filter(|(_, used)| !**used)
            .map(|(i, _)| (None, Some(i))),
    );
    pairs
}

#[derive(Clone, Debug, Serialize)]
pub struct Comparison {
    pub key: String,
    pub arxiv_id: Option<String>,
    pub kind: String,
    pub moved: bool,
    pub left: Option<String>,
    pub right: Option<String>,
    pub opcodes: Vec<Opcode>,
}
pub fn compare(before: &str, after: &str) -> Vec<Comparison> {
    let old = parse(before);
    let new = parse(after);
    let pairs = match_entries(&old.entries, &new.entries);
    let order: Vec<_> = pairs
        .iter()
        .filter_map(|(i, j)| if i.is_some() { *j } else { None })
        .collect();
    let mut sorted = order.clone();
    sorted.sort();
    let moved: Vec<_> = order
        .iter()
        .zip(&sorted)
        .filter(|(a, b)| a != b)
        .map(|(a, _)| *a)
        .collect();
    let mut result = Vec::new();
    for (i, j) in pairs {
        let left = i.map(|i| {
            let (a, b) = old.entries[i].source_span.unwrap();
            before[a..b].to_string()
        });
        let right = j.map(|j| {
            let (a, b) = new.entries[j].source_span.unwrap();
            after[a..b].to_string()
        });
        let is_moved = j.is_some_and(|v| moved.contains(&v));
        let kind = if i.is_none() {
            "added"
        } else if j.is_none() {
            "removed"
        } else if left != right || is_moved {
            "modified"
        } else {
            "unchanged"
        };
        let key = j
            .map(|j| new.entries[j].key.clone())
            .unwrap_or_else(|| old.entries[i.unwrap()].key.clone());
        let a: Vec<_> = left.as_deref().unwrap_or("").lines().collect();
        let b: Vec<_> = right.as_deref().unwrap_or("").lines().collect();
        let ops = opcodes(&a, &b);
        result.push(Comparison {
            key,
            arxiv_id: j
                .map(|j| &new.entries[j])
                .or_else(|| i.map(|i| &old.entries[i]))
                .and_then(crate::identifiers::arxiv_id),
            kind: kind.into(),
            moved: is_moved,
            left,
            right,
            opcodes: ops,
        });
    }
    let loose = |text: &str, entries: &[Entry]| {
        let mut chunks = Vec::new();
        let mut cursor = 0;
        for entry in entries {
            let (start, stop) = entry.source_span.unwrap();
            if !text[cursor..start].trim().is_empty() {
                chunks.push(text[cursor..start].trim().to_string());
            }
            cursor = stop;
        }
        if !text[cursor..].trim().is_empty() {
            chunks.push(text[cursor..].trim().to_string());
        }
        chunks.join("\n")
    };
    let left = loose(before, &old.entries);
    let right = loose(after, &new.entries);
    if left != right || (before != after && result.iter().all(|item| item.kind == "unchanged")) {
        let old_lines: Vec<_> = left.lines().collect();
        let new_lines: Vec<_> = right.lines().collect();
        let ops = opcodes(&old_lines, &new_lines);
        result.insert(
            0,
            Comparison {
                key: "File content".into(),
                arxiv_id: None,
                kind: "modified".into(),
                moved: false,
                left: Some(left),
                right: Some(right),
                opcodes: ops,
            },
        );
    }
    result
}
