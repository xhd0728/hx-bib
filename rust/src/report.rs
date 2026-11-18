use crate::verify::CheckResult;
use serde_json::{json, Value};

pub fn json_value(results: &[CheckResult], file: &str) -> Value {
    let entries: Vec<_> = results.iter().map(|result| {
        let label = match result.verdict.as_str() {
            "OK" => "OK", "MINOR" => "MINOR", "WARN" => "WARNING", "FAIL" => "SUSPECT", _ => "UNVERIFIABLE",
        };
        let evidence: Vec<_> = result.evidence.iter().map(|record| {
            let mut value = serde_json::to_value(record).expect("Serializable record");
            value.as_object_mut().unwrap().remove("best_ratio");
            value
        }).collect();
        json!({
            "key": result.entry.key, "type": result.entry.kind, "line": result.entry.line,
            "verdict": result.verdict, "verdict_label": label,
            "title": crate::textutil::delatex(result.entry.get("title")),
            "arxiv_id": result.arxiv_id,
            "arxiv_publication": result.publication.as_ref().map(|(status, detail)| json!({"status":status,"evidence":detail,"url":result.publication_url})),
            "attempts": result.attempts,
            "attempt_errors": result.attempt_errors.iter().map(|(number, detail)| json!({"attempt":number,"detail":detail})).collect::<Vec<_>>(),
            "doi": result.doi, "openreview_id": result.openreview_id,
            "issues": result.issues.iter().map(|(severity, message)| json!({"severity":severity,"message":message})).collect::<Vec<_>>(),
            "author_diff": result.author_diff, "evidence": evidence,
        })
    }).collect();
    json!({"tool":"hx-bib","file":file,"generated":chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),"entries":entries})
}
pub fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}
pub fn html(results: &[CheckResult]) -> String {
    let mut content = String::new();
    for r in results {
        let evidence = r
            .evidence
            .iter()
            .map(|e| {
                let title = if let Some(url) = e
                    .url
                    .as_ref()
                    .filter(|url| url.starts_with("https://") || url.starts_with("http://"))
                {
                    format!(
                        "<a href=\"{}\" rel=\"noopener noreferrer\">{}</a>",
                        escape(url),
                        escape(&e.title)
                    )
                } else {
                    escape(&e.title)
                };
                format!(
                    "<li>{}: {} · {} {}</li>",
                    escape(&e.source),
                    title,
                    escape(&e.venue),
                    escape(&e.note)
                )
            })
            .collect::<String>();
        let errors = r
            .attempt_errors
            .iter()
            .map(|(n, detail)| format!("<li>Attempt {n}: {}</li>", escape(detail)))
            .collect::<String>();
        content.push_str(&format!("<article><h2>{} · {}</h2><p>{}</p><ul>{}</ul><ul>{evidence}</ul><details><summary>{} attempts</summary><ul>{errors}</ul></details></article>",escape(&r.entry.key),escape(&r.verdict),escape(r.entry.get("title")),r.issues.iter().map(|(s,m)|format!("<li>{}: {}</li>",escape(s),escape(m))).collect::<String>(),r.attempts));
    }
    format!("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>hx-bib verification report</title><style>body{{font:16px system-ui;max-width:1000px;margin:32px auto;padding:16px;background:#f6f7fb;color:#16213b}}article{{border:1px solid #dce0ec;border-radius:12px;background:white;padding:20px;margin:16px 0}}li{{overflow-wrap:anywhere}}h2{{font-size:18px}}</style><h1>hx-bib verification report</h1>{content}</html>")
}
