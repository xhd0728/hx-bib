use anyhow::{bail, Context, Result};
use clap::{Args, Parser, Subcommand};
use hx_bib::{
    bibtex::{self, FormatOptions},
    cleaner::{self, CleanOptions},
    diff, engine,
    latex::{self, TableOptions},
    net::Net,
    normalize::{self, NormalizeOptions},
    validator::{self, ValidateOptions},
    venues,
    verify::{self, RunControl},
};
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Read, Write},
    path::PathBuf,
};

#[derive(Parser)]
#[command(
    name = "hx-bib-rs",
    version,
    about = "Native Rust BibTeX and LaTeX workbench"
)]
struct Cli {
    #[arg(long, global = true)]
    debug: bool,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Format {
        #[command(flatten)]
        io: FileArgs,
        #[arg(long, short = 'i', default_value = "4")]
        indent: String,
        #[arg(long, overrides_with = "no_align_equals")]
        align_equals: bool,
        #[arg(long, overrides_with = "align_equals")]
        no_align_equals: bool,
        #[arg(long, short = 'f', default_value = "standard")]
        field_order: String,
        #[arg(long, short = 's', default_value = "none")]
        sort: String,
        #[arg(long)]
        trailing_comma: bool,
        #[arg(long)]
        drop_comments: bool,
    },
    Clean {
        #[command(flatten)]
        io: FileArgs,
        #[arg(long, short = 'r')]
        remove: Vec<String>,
        #[arg(long, short = 'k')]
        keep: Vec<String>,
        #[arg(long, short = 'a')]
        add: Vec<String>,
        #[arg(long)]
        rename: Vec<String>,
        #[arg(long)]
        overwrite: bool,
        #[arg(long)]
        drop_empty: bool,
        #[arg(long)]
        drop_others: bool,
        #[arg(long)]
        months: bool,
        #[arg(long)]
        title_period: bool,
        #[arg(long)]
        doi: bool,
        #[arg(long)]
        pages: bool,
        #[arg(long)]
        clean_urls: bool,
        #[arg(long)]
        dedupe: bool,
        #[arg(long)]
        keep_last: bool,
        #[arg(long)]
        dedupe_title: bool,
    },
    Normalize {
        #[command(flatten)]
        io: FileArgs,
        #[arg(long, default_value = "abbr")]
        venue_style: String,
        #[arg(long, default_value = "journal")]
        arxiv_style: String,
        #[arg(long, overrides_with = "no_fix_types")]
        fix_types: bool,
        #[arg(long, overrides_with = "fix_types")]
        no_fix_types: bool,
        #[arg(long, overrides_with = "keep_arxiv_volume")]
        drop_arxiv_volume: bool,
        #[arg(long, overrides_with = "drop_arxiv_volume")]
        keep_arxiv_volume: bool,
        #[arg(long, overrides_with = "no_arxiv_url")]
        add_arxiv_url: bool,
        #[arg(long, overrides_with = "add_arxiv_url")]
        no_arxiv_url: bool,
    },
    Check {
        file: PathBuf,
        #[arg(long = "json")]
        json_path: Option<PathBuf>,
        #[arg(long)]
        strict: bool,
        #[arg(long,default_value="all",value_parser=["all","issues"])]
        show: String,
    },
    Table {
        #[command(flatten)]
        io: FileArgs,
        #[arg(long, default_value = "space")]
        amp: String,
        #[arg(long, overrides_with = "no_blank_lines")]
        blank_lines: bool,
        #[arg(long, overrides_with = "blank_lines")]
        no_blank_lines: bool,
        #[arg(long, default_value_t = 0)]
        indent: i32,
    },
    Venues {
        #[command(subcommand)]
        command: VenueCommand,
    },
    Verify {
        file: PathBuf,
        #[arg(long)]
        html: Option<PathBuf>,
        #[arg(long = "json")]
        json_path: Option<PathBuf>,
        #[arg(long)]
        only: Vec<String>,
        #[arg(long, default_value_t = 0.8)]
        delay: f64,
        #[arg(long)]
        no_cache: bool,
        #[arg(long)]
        verbose: bool,
        #[arg(long,default_value_t=1,value_parser=clap::value_parser!(u8).range(0..=3))]
        retries: u8,
        #[arg(long,short='j',default_value_t=1,value_parser=clap::value_parser!(u8).range(1..=8))]
        workers: u8,
        #[arg(long)]
        arxiv: bool,
    },
    Gui,
    /// Process newline-delimited JSON requests for integrations and parity tests.
    Engine,
}
#[derive(Args)]
struct FileArgs {
    file: PathBuf,
    #[arg(long, short = 'o')]
    output: Option<PathBuf>,
    #[arg(long)]
    stdout: bool,
    #[arg(long, short = 'n')]
    dry_run: bool,
}
#[derive(Subcommand)]
enum VenueCommand {
    List {
        #[arg(long)]
        family: Option<String>,
        #[arg(long)]
        kind: Option<String>,
    },
    Search {
        query: String,
    },
    Show {
        abbr: String,
    },
}

fn read(path: &PathBuf) -> Result<String> {
    let mut bytes = Vec::new();
    if path.as_os_str() == "-" {
        io::stdin().read_to_end(&mut bytes)?;
    } else {
        bytes = std::fs::read(path).with_context(|| format!("cannot read {}", path.display()))?;
    }
    Ok(String::from_utf8(bytes.clone())
        .unwrap_or_else(|_| bytes.iter().map(|v| char::from(*v)).collect()))
}
fn finish(args: &FileArgs, before: &str, after: &str) -> Result<()> {
    if args.stdout {
        print!("{after}");
    } else if args.dry_run {
        for comparison in diff::compare(before, after)
            .iter()
            .filter(|c| c.kind != "unchanged")
        {
            println!("{} {}", comparison.kind, comparison.key);
            if let Some(value) = &comparison.left {
                for line in value.lines() {
                    println!("- {line}");
                }
            }
            if let Some(value) = &comparison.right {
                for line in value.lines() {
                    println!("+ {line}");
                }
            }
        }
    } else if args.output.is_some() || before != after {
        let target = args.output.as_ref().unwrap_or(&args.file);
        if target.as_os_str() == "-" {
            print!("{after}");
        } else {
            std::fs::write(target, after)?;
            println!("Wrote {}", target.display());
        }
    } else {
        println!("Already tidy.");
    }
    Ok(())
}
fn pairs(values: Vec<String>) -> Result<indexmap::IndexMap<String, String>> {
    values
        .into_iter()
        .map(|pair| {
            let Some((key, value)) = pair.split_once('=') else {
                bail!("expected field=value, got {pair:?}")
            };
            Ok((key.trim().to_lowercase(), value.trim().to_string()))
        })
        .collect()
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error:#}");
        std::process::exit(2);
    }
}
fn run() -> Result<()> {
    let cli = Cli::parse();
    let verbose = matches!(&cli.command, Command::Verify { verbose: true, .. });
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or(
        if cli.debug || verbose {
            "debug"
        } else {
            "warn"
        },
    ))
    .init();
    match cli.command {
        Command::Engine => {
            let stdout = io::stdout();
            let mut out = stdout.lock();
            for line in io::stdin().lock().lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                let result = serde_json::from_str::<Value>(&line)
                    .map_err(anyhow::Error::from)
                    .and_then(engine::request);
                let response = match result {
                    Ok(value) => json!({"ok":true,"result":value}),
                    Err(error) => json!({"ok":false,"error":error.to_string()}),
                };
                writeln!(out, "{}", serde_json::to_string(&response)?)?;
            }
        }
        Command::Format {
            io,
            indent,
            align_equals,
            no_align_equals: _,
            field_order,
            sort,
            trailing_comma,
            drop_comments,
        } => {
            let before = read(&io.file)?;
            let opts = FormatOptions {
                indent,
                align_equals,
                field_order,
                sort_entries: sort,
                trailing_comma,
                drop_comments,
                ..FormatOptions::default()
            };
            let after = bibtex::render(&bibtex::parse(&before), &opts)?;
            finish(&io, &before, &after)?;
        }
        Command::Clean {
            io,
            remove,
            keep,
            add,
            rename,
            overwrite,
            drop_empty,
            drop_others,
            months,
            title_period,
            doi,
            pages,
            clean_urls,
            dedupe,
            keep_last,
            dedupe_title,
        } => {
            let before = read(&io.file)?;
            let mut doc = bibtex::parse(&before);
            let opts = CleanOptions {
                remove,
                keep,
                add: pairs(add)?,
                rename: pairs(rename)?,
                overwrite,
                drop_empty,
                drop_others,
                months,
                title_period,
                doi,
                pages,
                clean_urls,
                dedupe,
                keep_last,
                dedupe_title,
            };
            for note in cleaner::clean(&mut doc, &opts)? {
                if !io.stdout {
                    eprintln!("{note}");
                }
            }
            let after = bibtex::render(&doc, &FormatOptions::default())?;
            finish(&io, &before, &after)?;
        }
        Command::Normalize {
            io,
            venue_style,
            arxiv_style,
            fix_types: _,
            no_fix_types,
            drop_arxiv_volume: _,
            keep_arxiv_volume,
            add_arxiv_url: _,
            no_arxiv_url,
        } => {
            let before = read(&io.file)?;
            let mut doc = bibtex::parse(&before);
            let opts = NormalizeOptions {
                venue_style,
                arxiv_style,
                fix_entry_types: !no_fix_types,
                drop_arxiv_volume: !keep_arxiv_volume,
                add_arxiv_url: !no_arxiv_url,
            };
            normalize::normalize(&mut doc, &opts)?;
            let after = bibtex::render(&doc, &FormatOptions::default())?;
            finish(&io, &before, &after)?;
        }
        Command::Check {
            file,
            json_path,
            strict,
            show,
        } => {
            let mut issues =
                validator::validate(&bibtex::parse(&read(&file)?), &ValidateOptions::default());
            issues.sort_by_key(|issue| {
                let rank = match issue.severity.as_str() {
                    "error" => 0,
                    "warning" => 1,
                    _ => 2,
                };
                (rank, issue.line, issue.key.clone())
            });
            for issue in &issues {
                if show != "issues" || issue.severity != "info" {
                    println!("{} {} {}", issue.severity, issue.key, issue.message);
                }
            }
            if let Some(path) = json_path {
                let items: Vec<_> = issues
                    .iter()
                    .map(|issue| {
                        json!({
                            "severity": issue.severity,
                            "key": issue.key,
                            "field": issue.field_name,
                            "line": issue.line,
                            "message": issue.message,
                        })
                    })
                    .collect();
                std::fs::write(
                    path,
                    serde_json::to_string_pretty(
                        &json!({"tool":"hx-bib","file":file,"issues":items}),
                    )?,
                )?;
            }
            if issues
                .iter()
                .any(|i| i.severity == "error" || strict && i.severity == "warning")
            {
                std::process::exit(1);
            }
        }
        Command::Table {
            io,
            amp,
            blank_lines: _,
            no_blank_lines,
            indent,
        } => {
            let before = read(&io.file)?;
            let result = latex::format_tables(
                &before,
                &TableOptions {
                    amp_style: amp,
                    blank_lines_around_rules: !no_blank_lines,
                    row_indent: indent,
                    ..TableOptions::default()
                },
            )?;
            for warning in result.warnings {
                eprintln!("{warning}");
            }
            finish(&io, &before, &result.text)?;
        }
        Command::Venues { command } => {
            let results = match command {
                VenueCommand::List { family, kind } => venues::VENUES
                    .iter()
                    .filter(|v| {
                        family
                            .as_ref()
                            .is_none_or(|f| v.family.eq_ignore_ascii_case(f))
                            && kind.as_ref().is_none_or(|k| v.kind.eq_ignore_ascii_case(k))
                    })
                    .collect(),
                VenueCommand::Search { query } => venues::search(&query),
                VenueCommand::Show { abbr } => venues::VENUES
                    .iter()
                    .filter(|v| v.abbr.eq_ignore_ascii_case(&abbr))
                    .collect(),
            };
            for v in results {
                println!("{:<8} {:<8} {:<11} {}", v.abbr, v.family, v.kind, v.name);
            }
        }
        Command::Verify {
            file,
            html,
            json_path,
            only,
            delay,
            no_cache,
            verbose: _,
            retries,
            workers,
            arxiv,
        } => {
            if !delay.is_finite() || delay < 0.0 {
                bail!("delay must be >= 0");
            }
            let entries: Vec<_> = bibtex::parse(&read(&file)?)
                .entries
                .into_iter()
                .filter(|e| {
                    (only.is_empty() || only.contains(&e.key))
                        && (!arxiv || hx_bib::identifiers::arxiv_id(e).is_some())
                })
                .collect();
            if entries.is_empty() {
                bail!("nothing to verify");
            }
            let net = Net::new(delay, !no_cache)?;
            let control = RunControl::default();
            let mut results = verify::verify_entries(
                &net,
                &entries,
                arxiv,
                if arxiv { retries as usize } else { 0 },
                workers as usize,
                &control,
                &|done, total, key| eprintln!("{done}/{total} {key}"),
            );
            if !arxiv {
                for _ in 0..retries {
                    if !results
                        .iter()
                        .any(|result| result.any_error && result.verdict != "OK")
                    {
                        break;
                    }
                    results = verify::retry_failed(&net, results, &control);
                }
            }
            for result in &results {
                println!("{} {}", result.verdict, result.entry.key);
            }
            let report = serde_json::to_string_pretty(&hx_bib::report::json_value(
                &results,
                &file.to_string_lossy(),
            ))?;
            if let Some(path) = json_path {
                std::fs::write(path, &report)?;
            }
            if let Some(path) = html {
                std::fs::write(path, hx_bib::report::html(&results))?;
            }
            if results.iter().any(|r| r.verdict == "FAIL") {
                std::process::exit(1);
            }
        }
        Command::Gui => {
            let current = std::env::current_exe()?;
            let desktop = current.with_file_name(if cfg!(windows) {
                "hx-bib-desktop.exe"
            } else {
                "hx-bib-desktop"
            });
            let mut command = std::process::Command::new(&desktop);
            if cli.debug {
                command.arg("--debug");
            }
            command.spawn().with_context(|| {
                format!(
                    "install the desktop package, or build with cargo tauri build: {}",
                    desktop.display()
                )
            })?;
        }
    }
    Ok(())
}
