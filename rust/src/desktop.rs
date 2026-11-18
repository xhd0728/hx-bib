#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use hx_bib::{
    bibtex, engine, identifiers,
    net::Net,
    report,
    verify::{self, CheckResult, RunControl},
};
use serde_json::{json, Value};
use std::sync::{atomic::Ordering, Arc, Mutex};
use tauri::{Emitter, State};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

#[derive(Default)]
struct Jobs(Mutex<Option<Arc<RunControl>>>);

#[tauri::command]
async fn process(request: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        engine::request(request).map_err(|e| e.to_string())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
#[allow(clippy::too_many_arguments)] // Tauri injects the app and job state alongside IPC fields.
async fn check_online(
    app: tauri::AppHandle,
    jobs: State<'_, Jobs>,
    text: String,
    arxiv_only: bool,
    ids: Option<Vec<String>>,
    delay: f64,
    workers: usize,
    use_cache: bool,
    run_id: u64,
) -> Result<Vec<CheckResult>, String> {
    if !delay.is_finite() || delay < 0.0 {
        return Err("Request delay must be >= 0".into());
    }
    let control = Arc::new(RunControl::default());
    {
        let mut active = jobs.0.lock().map_err(|e| e.to_string())?;
        if active.is_some() {
            return Err("A verification is already running".into());
        }
        *active = Some(control.clone());
    }
    let worker_control = control.clone();
    let worker_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let mut entries = bibtex::parse(&text).entries;
        if arxiv_only {
            let mut seen = std::collections::HashSet::new();
            entries.retain(|e| {
                identifiers::arxiv_id(e).is_some_and(|id| {
                    seen.insert(id.clone()) && ids.as_ref().is_none_or(|ids| ids.contains(&id))
                })
            });
        } else if let Some(ids) = &ids {
            entries.retain(|e| ids.contains(&e.key));
        }
        if entries.is_empty() {
            return Err("No matching entries to verify".into());
        }
        let net = Net::new(delay, use_cache).map_err(|e| e.to_string())?;
        let results = verify::verify_entries(
            &net,
            &entries,
            arxiv_only,
            if arxiv_only { 2 } else { 0 },
            workers,
            &worker_control,
            &|done, total, key| {
                let _ = worker_app.emit(
                    "verification-progress",
                    json!({"id":run_id,"done":done,"total":total,"key":key}),
                );
            },
        );
        Ok(if arxiv_only {
            results
        } else {
            verify::retry_failed(&net, results, &worker_control)
        })
    })
    .await
    .map_err(|e| e.to_string())
    .and_then(|r| r);
    let mut active = jobs.0.lock().map_err(|e| e.to_string())?;
    if active.as_ref().is_some_and(|v| Arc::ptr_eq(v, &control)) {
        *active = None;
    }
    result
}

#[tauri::command]
fn control_run(jobs: State<'_, Jobs>, action: String) {
    if let Some(control) = jobs.0.lock().unwrap().as_ref() {
        match action.as_str() {
            "pause" => control.paused.store(true, Ordering::Relaxed),
            "resume" => control.paused.store(false, Ordering::Relaxed),
            "stop" => {
                control.cancelled.store(true, Ordering::Relaxed);
                control.paused.store(false, Ordering::Relaxed);
            }
            _ => {}
        }
    }
}

#[tauri::command]
fn copy_text(app: tauri::AppHandle, text: String) -> Result<(), String> {
    app.clipboard().write_text(text).map_err(|e| e.to_string())
}

#[tauri::command]
async fn save_text(app: tauri::AppHandle, name: String, text: String) -> Result<bool, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = app.dialog().file().set_file_name(name).blocking_save_file();
        if let Some(path) = path {
            let path = path.into_path().map_err(|e| e.to_string())?;
            std::fs::write(path, text).map_err(|e| e.to_string())?;
            Ok(true)
        } else {
            Ok(false)
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn open_link(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|e| e.to_string())?;
    if !["http", "https"].contains(&parsed.scheme()) {
        return Err("Only HTTP(S) links are supported".into());
    }
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn build_report(results: Vec<CheckResult>) -> String {
    report::html(&results)
}

#[tauri::command]
fn platform() -> &'static str {
    std::env::consts::OS
}

fn main() {
    let debug = std::env::args().any(|v| v == "--debug");
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or(if debug {
        "debug"
    } else {
        "warn"
    }))
    .init();
    tauri::Builder::default()
        .setup(|app| {
            use tauri::Manager;
            let window = app.get_webview_window("main").expect("Main window missing");
            #[cfg(target_os = "windows")]
            window.set_decorations(false)?;
            window.show()?;
            Ok(())
        })
        .manage(Jobs::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            process,
            check_online,
            control_run,
            copy_text,
            save_text,
            open_link,
            build_report,
            platform
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                use tauri::Manager;
                let jobs = window.state::<Jobs>();
                let active = jobs.0.lock().unwrap();
                if let Some(control) = active.as_ref() {
                    control.cancelled.store(true, Ordering::Relaxed);
                    control.paused.store(false, Ordering::Relaxed);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Could not start hx-bib desktop");
}
