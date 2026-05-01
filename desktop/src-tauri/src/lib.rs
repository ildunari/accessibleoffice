use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use tauri::{AppHandle, Emitter, Manager};

fn resolve_a11yfix_binary() -> String {
    // Try common install locations in order
    for candidate in [
        "/Users/kosta/.local/bin/a11yfix",
        "/Users/kosta/LocalDev/office-a11y-fixer/.venv/bin/a11yfix",
        "/opt/homebrew/bin/a11yfix",
        "a11yfix",
    ] {
        if std::path::Path::new(candidate).exists() {
            return candidate.to_string();
        }
    }
    "a11yfix".to_string()
}

#[tauri::command]
async fn run_a11yfix(
    app: AppHandle,
    file_path: String,
    mode: String,
) -> Result<serde_json::Value, String> {
    let binary = resolve_a11yfix_binary();
    let manifest_path = std::path::Path::new(&file_path)
        .with_extension(if file_path.ends_with(".pptx") {
            "pptx.manifest.json"
        } else {
            "docx.manifest.json"
        });

    let _ = app.emit("a11yfix-log", format!("$ {} {} --mode {} --output {}", binary, file_path, mode, manifest_path.display()));

    let mut cmd = Command::new(&binary);
    cmd.arg(&file_path)
        .arg("--mode")
        .arg(&mode)
        .arg("--output")
        .arg(&manifest_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // For 'full' mode, do a dry-run from the GUI — running interactive Claude Code
    // from a Tauri window is not the right UX. The user can launch from terminal.
    if mode == "full" {
        cmd.arg("--dry-run");
    }

    let mut child = cmd.spawn().map_err(|e| format!("spawn failed: {e}"))?;

    if let Some(stdout) = child.stdout.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().flatten() {
                let _ = app_clone.emit("a11yfix-log", line);
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        let app_clone = app.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().flatten() {
                let _ = app_clone.emit("a11yfix-log", format!("[err] {}", line));
            }
        });
    }

    let status = child.wait().map_err(|e| format!("wait failed: {e}"))?;
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
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
        .invoke_handler(tauri::generate_handler![run_a11yfix])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
