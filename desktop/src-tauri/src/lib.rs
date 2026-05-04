use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use once_cell::sync::Lazy;
use regex::Regex;
use serde::Serialize;
use tauri::{AppHandle, Emitter, State};
use walkdir::WalkDir;

const CLI_NAME: &str = "accessibleoffice";
const LEGACY_CLI_NAME: &str = "a11yfix";
const CLAUDE_CLI: &str = "claude";

#[derive(Default)]
struct RunState {
    children: Mutex<HashMap<String, u32>>,
}

#[derive(Serialize, Clone)]
struct CliStatus {
    found: bool,
    path: Option<String>,
    version: Option<String>,
}

fn binary_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn fallback_paths_for(name: &str) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    let bin = binary_name(name);

    if let Some(home) = home_dir() {
        out.push(home.join(".local/bin").join(&bin));
        if cfg!(target_os = "macos") {
            for ver in ["3.13", "3.12", "3.11"] {
                out.push(home.join(format!("Library/Python/{ver}/bin")).join(&bin));
            }
        }
        if cfg!(windows) {
            out.push(home.join(".local").join("bin").join(&bin));
            for ver in ["Python313", "Python312", "Python311"] {
                out.push(
                    home.join("AppData")
                        .join("Roaming")
                        .join(ver)
                        .join("Scripts")
                        .join(&bin),
                );
            }
        }
    }

    if let Some(appdata) = std::env::var_os("APPDATA") {
        out.push(PathBuf::from(appdata).join("Python").join("Scripts").join(&bin));
    }

    if cfg!(target_os = "macos") {
        out.push(PathBuf::from("/opt/homebrew/bin").join(&bin));
    }
    out.push(PathBuf::from("/usr/local/bin").join(&bin));
    out.push(PathBuf::from("/usr/bin").join(&bin));
    out
}

fn resolve_binary(names: &[&str]) -> Option<PathBuf> {
    for name in names {
        if let Ok(path) = which::which(name) {
            return Some(path);
        }
        if cfg!(windows) {
            if let Ok(path) = which::which(format!("{name}.exe")) {
                return Some(path);
            }
        }
        if let Some(p) = fallback_paths_for(name).into_iter().find(|p| p.exists()) {
            return Some(p);
        }
    }
    None
}

fn version_of(path: &std::path::Path) -> Option<String> {
    let out = Command::new(path).arg("--version").output().ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() { None } else { Some(s) }
}

#[tauri::command]
async fn check_cli() -> CliStatus {
    let Some(path) = resolve_binary(&[CLI_NAME, LEGACY_CLI_NAME]) else {
        return CliStatus {
            found: false,
            path: None,
            version: None,
        };
    };
    let version = version_of(&path);
    CliStatus {
        found: true,
        path: Some(path.to_string_lossy().into_owned()),
        version,
    }
}

#[tauri::command]
async fn check_claude_code() -> CliStatus {
    let Some(path) = resolve_binary(&[CLAUDE_CLI]) else {
        return CliStatus {
            found: false,
            path: None,
            version: None,
        };
    };
    let version = version_of(&path);
    CliStatus {
        found: true,
        path: Some(path.to_string_lossy().into_owned()),
        version,
    }
}

#[tauri::command]
async fn open_url(url: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(&url).status();
    #[cfg(target_os = "windows")]
    let result = Command::new("cmd").args(["/C", "start", "", &url]).status();
    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open").arg(&url).status();
    result.map_err(|e| format!("open failed: {e}")).map(|_| ())
}

fn manifest_path_for(file_path: &str) -> PathBuf {
    let lower = file_path.to_ascii_lowercase();
    let suffix = if lower.ends_with(".pptx") {
        "pptx.manifest.json"
    } else {
        "docx.manifest.json"
    };
    PathBuf::from(file_path).with_extension(suffix)
}

#[tauri::command]
async fn run_a11yfix(
    app: AppHandle,
    state: State<'_, RunState>,
    run_id: String,
    file_path: String,
    mode: String,
) -> Result<serde_json::Value, String> {
    let Some(binary) = resolve_binary(&[CLI_NAME, LEGACY_CLI_NAME]) else {
        return Err(
            "AccessibleOffice CLI not found. Install with: pipx install git+https://github.com/ildunari/accessibleoffice.git"
                .to_string(),
        );
    };
    let manifest_path = manifest_path_for(&file_path);

    let _ = app.emit(
        "a11yfix-log",
        format!(
            "$ {} {} --mode {} --output {}",
            binary.display(),
            file_path,
            mode,
            manifest_path.display()
        ),
    );

    let mut cmd = Command::new(&binary);
    cmd.arg(&file_path)
        .arg("--mode")
        .arg(&mode)
        .arg("--output")
        .arg(&manifest_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Tauri windows can't host an interactive Claude Code agent, so the GUI runs `full` as a
    // dry-run preview. The CLI's stage-4 launcher prints the launch plan including the
    // orchestration path it picked (skill or embedded fallback) — the user copies the
    // command and executes it in a terminal.
    if mode == "full" {
        cmd.arg("--dry-run");
        let _ = app.emit(
            "a11yfix-mode-note",
            "Full mode prints the orchestrator launch command. Copy it into a terminal to execute. The embedded fallback runs even if the fixing-office-accessibility skill isn't installed.",
        );
    }

    let mut child = cmd.spawn().map_err(|e| format!("spawn failed: {e}"))?;
    let pid = child.id();
    state.children.lock().unwrap().insert(run_id.clone(), pid);

    if let Some(stdout) = child.stdout.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                emit_log_or_event(&app_clone, &line);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = app_clone.emit("a11yfix-log", format!("[err] {line}"));
            }
        });
    }

    // Tick elapsed time every 250ms while the run is alive.
    let app_progress = app.clone();
    let progress_run_id = run_id.clone();
    let stop_progress = Arc::new(Mutex::new(false));
    let stop_for_thread = stop_progress.clone();
    std::thread::spawn(move || {
        let start = Instant::now();
        loop {
            if *stop_for_thread.lock().unwrap() {
                break;
            }
            let elapsed_ms = start.elapsed().as_millis() as u64;
            let _ = app_progress.emit(
                "a11yfix-progress",
                serde_json::json!({ "run_id": progress_run_id, "elapsed_ms": elapsed_ms }),
            );
            std::thread::sleep(Duration::from_millis(250));
        }
    });

    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
    *stop_progress.lock().unwrap() = true;
    state.children.lock().unwrap().remove(&run_id);
    let _ = app.emit("a11yfix-log", format!("(exit {})", status.code().unwrap_or(-1)));

    if !manifest_path.exists() {
        return Err(format!(
            "manifest not produced at {}; check log",
            manifest_path.display()
        ));
    }

    let body = std::fs::read_to_string(&manifest_path).map_err(|e| format!("read manifest: {e}"))?;
    let v: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| format!("parse manifest: {e}"))?;
    Ok(v)
}

// Match the lines the Python CLI prints during a `--folder` batch run so the UI can render
// per-file progress without polling state files. Keeping these as `Lazy<Regex>` avoids
// recompiling them on every emitted log line.
static RE_BATCH_HEADER: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^\[batch\]\s+(?P<id>\S+)\s+mode=(?P<mode>\S+)\s+shards=(?P<shards>\d+)").unwrap()
});
static RE_BATCH_SHARD_START: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\[batch\]\s+(?P<sid>\S+):\s+(?P<n>\d+)\s+files").unwrap());
static RE_BATCH_FILE_OK: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^\s+\[ok\s*\]\s+(?P<name>.+?)\s+s2=(?P<s2>\d+)\s+s3=(?P<s3>\d+)\s+residual=(?P<res>\d+)\s+\((?P<sec>[0-9.]+)s\)$",
    )
    .unwrap()
});
static RE_BATCH_FILE_FAIL: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s+\[fail\]\s+(?P<name>.+?):\s+(?P<err>.+)$").unwrap());
static RE_BATCH_DONE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^\[batch\]\s+done\s+files=(?P<files>\d+)\s+done=(?P<done>\d+)\s+failed=(?P<failed>\d+).*cost=\$(?P<cost>[0-9.]+)",
    )
    .unwrap()
});
static RE_BATCH_STATE_DIR: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\[batch\]\s+state:\s+(?P<dir>.+)$").unwrap());

fn emit_batch_event(app: &AppHandle, line: &str) {
    if let Some(c) = RE_BATCH_HEADER.captures(line) {
        let _ = app.emit(
            "accofc-batch-start",
            serde_json::json!({
                "batch_id": &c["id"],
                "mode": &c["mode"],
                "shards": c["shards"].parse::<u32>().unwrap_or(0),
            }),
        );
    } else if let Some(c) = RE_BATCH_SHARD_START.captures(line) {
        let _ = app.emit(
            "accofc-batch-shard",
            serde_json::json!({
                "shard_id": &c["sid"],
                "files": c["n"].parse::<u32>().unwrap_or(0),
            }),
        );
    } else if let Some(c) = RE_BATCH_FILE_OK.captures(line) {
        let _ = app.emit(
            "accofc-batch-file",
            serde_json::json!({
                "name": &c["name"],
                "status": "done",
                "s2": c["s2"].parse::<u32>().unwrap_or(0),
                "s3": c["s3"].parse::<u32>().unwrap_or(0),
                "residual": c["res"].parse::<u32>().unwrap_or(0),
                "elapsed_sec": c["sec"].parse::<f64>().unwrap_or(0.0),
            }),
        );
    } else if let Some(c) = RE_BATCH_FILE_FAIL.captures(line) {
        let _ = app.emit(
            "accofc-batch-file",
            serde_json::json!({
                "name": &c["name"],
                "status": "failed",
                "error": &c["err"],
            }),
        );
    } else if let Some(c) = RE_BATCH_STATE_DIR.captures(line) {
        let _ = app.emit(
            "accofc-batch-state-dir",
            serde_json::json!({ "dir": &c["dir"] }),
        );
    } else if let Some(c) = RE_BATCH_DONE.captures(line) {
        let _ = app.emit(
            "accofc-batch-done",
            serde_json::json!({
                "files": c["files"].parse::<u32>().unwrap_or(0),
                "done": c["done"].parse::<u32>().unwrap_or(0),
                "failed": c["failed"].parse::<u32>().unwrap_or(0),
                "cost_usd": c["cost"].parse::<f64>().unwrap_or(0.0),
            }),
        );
    }
}

#[derive(Serialize)]
struct FolderScan {
    folder: String,
    docx_count: u32,
    pptx_count: u32,
    total: u32,
    sample: Vec<String>,
}

#[tauri::command]
async fn scan_folder(folder_path: String) -> Result<FolderScan, String> {
    let p = PathBuf::from(&folder_path);
    if !p.is_dir() {
        return Err(format!("not a directory: {folder_path}"));
    }
    let mut docx = 0u32;
    let mut pptx = 0u32;
    let mut sample: Vec<String> = Vec::new();
    for entry in WalkDir::new(&p).follow_links(false).into_iter().filter_map(Result::ok) {
        if !entry.file_type().is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with("~$") || name.starts_with(".~lock") {
            continue;
        }
        let lower = name.to_ascii_lowercase();
        if lower.ends_with(".docx") {
            docx += 1;
            if sample.len() < 10 {
                sample.push(name);
            }
        } else if lower.ends_with(".pptx") {
            pptx += 1;
            if sample.len() < 10 {
                sample.push(name);
            }
        }
    }
    Ok(FolderScan {
        folder: folder_path,
        docx_count: docx,
        pptx_count: pptx,
        total: docx + pptx,
        sample,
    })
}

#[tauri::command]
async fn run_batch(
    app: AppHandle,
    state: State<'_, RunState>,
    run_id: String,
    folder_path: String,
    mode: String,
    max_cost_usd: Option<f64>,
) -> Result<serde_json::Value, String> {
    let Some(binary) = resolve_binary(&[CLI_NAME, LEGACY_CLI_NAME]) else {
        return Err(
            "AccessibleOffice CLI not found. Install with: pipx install git+https://github.com/ildunari/accessibleoffice.git"
                .to_string(),
        );
    };

    let mut cmd = Command::new(&binary);
    cmd.arg("--folder")
        .arg(&folder_path)
        .arg("--mode")
        .arg(&mode);
    if let Some(cap) = max_cost_usd {
        cmd.arg("--max-cost-total-usd").arg(format!("{cap}"));
    }
    if mode == "full" {
        // Batch mode in the CLI never spawns interactive Claude Code; ensure the
        // user knows the agent step is dry-run only here.
        let _ = app.emit(
            "a11yfix-mode-note",
            "Batch mode runs `full` without the interactive agent step. Per-file fixes from stages 1-3 are still applied.",
        );
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let _ = app.emit(
        "a11yfix-log",
        format!("$ {} --folder {} --mode {}", binary.display(), folder_path, mode),
    );

    let mut child = cmd.spawn().map_err(|e| format!("spawn failed: {e}"))?;
    let pid = child.id();
    state.children.lock().unwrap().insert(run_id.clone(), pid);

    if let Some(stdout) = child.stdout.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                emit_batch_event(&app_clone, &line);
                let _ = app_clone.emit("a11yfix-log", line);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                emit_batch_event(&app_clone, &line);
                let _ = app_clone.emit("a11yfix-log", format!("[err] {line}"));
            }
        });
    }

    // Tick elapsed time so the UI shows a live timer.
    let app_progress = app.clone();
    let progress_run_id = run_id.clone();
    let stop_progress = Arc::new(Mutex::new(false));
    let stop_for_thread = stop_progress.clone();
    std::thread::spawn(move || {
        let start = Instant::now();
        loop {
            if *stop_for_thread.lock().unwrap() {
                break;
            }
            let elapsed_ms = start.elapsed().as_millis() as u64;
            let _ = app_progress.emit(
                "a11yfix-progress",
                serde_json::json!({ "run_id": progress_run_id, "elapsed_ms": elapsed_ms }),
            );
            std::thread::sleep(Duration::from_millis(250));
        }
    });

    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
    *stop_progress.lock().unwrap() = true;
    state.children.lock().unwrap().remove(&run_id);
    let _ = app.emit("a11yfix-log", format!("(exit {})", status.code().unwrap_or(-1)));

    Ok(serde_json::json!({
        "exit_code": status.code().unwrap_or(-1),
    }))
}

fn emit_log_or_event(app: &AppHandle, line: &str) {
    // The Python `cost_meter` emits structured single-line JSON so the GUI can parse it
    // without screen-scraping. We forward those as their own event and still echo the
    // raw line into the log so power users see exactly what the CLI printed.
    if line.starts_with("{\"event\":\"cost\"") {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            let _ = app.emit("a11yfix-cost", v);
        }
    } else if line.starts_with("{\"event\":\"stage\"") {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            let _ = app.emit("a11yfix-stage", v);
        }
    }
    let _ = app.emit("a11yfix-log", line.to_string());
}

#[tauri::command]
async fn cancel_run(state: State<'_, RunState>, run_id: String) -> Result<(), String> {
    let pid = state.children.lock().unwrap().remove(&run_id);
    let Some(pid) = pid else {
        return Ok(());
    };
    #[cfg(unix)]
    {
        let _ = Command::new("kill").arg(pid.to_string()).status();
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/F", "/T"])
            .status();
    }
    Ok(())
}

#[tauri::command]
async fn reveal_in_finder(path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.exists() {
        return Err(format!("path does not exist: {path}"));
    }
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg("-R").arg(&p).status();
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer")
        .arg(format!("/select,{}", p.display()))
        .status();
    #[cfg(target_os = "linux")]
    let result = {
        let parent = p.parent().unwrap_or(&p);
        Command::new("xdg-open").arg(parent).status()
    };
    result.map_err(|e| format!("reveal failed: {e}")).map(|_| ())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(RunState::default())
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            run_a11yfix,
            run_batch,
            scan_folder,
            check_cli,
            check_claude_code,
            cancel_run,
            reveal_in_finder,
            open_url
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
