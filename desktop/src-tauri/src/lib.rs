use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use once_cell::sync::Lazy;
use regex::Regex;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri::path::BaseDirectory;
use walkdir::WalkDir;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

const CLI_NAME: &str = "accessibleoffice";
const LEGACY_CLI_NAME: &str = "a11yfix";
const CLAUDE_CLI: &str = "claude";
// User-local install destinations populated by setup_dependencies.
// Officecli binary copied from bundled resources; wheel installed into a
// private venv. Both are inside HOME so no admin privileges are needed.
const APP_DIR_NAME: &str = ".accessibleoffice";
const RUNTIME_DIR_NAME: &str = ".accessibleoffice-runtime";

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

// ----- Setup wizard plumbing -----------------------------------------------
//
// The desktop app is shipped to users who may have never opened a terminal.
// First-run UX is therefore: detect prerequisites, copy bundled officecli into
// ~/.accessibleoffice/bin/officecli, create a Python venv at
// ~/.accessibleoffice-runtime/, install our wheel into it. If Python ≥ 3.11
// isn't present, we surface a per-platform install link instead of trying to
// install Python ourselves (cross-platform silent Python installs are fragile
// and require admin auth).

fn app_dir() -> Option<PathBuf> {
    home_dir().map(|h| h.join(APP_DIR_NAME))
}

fn runtime_dir() -> Option<PathBuf> {
    home_dir().map(|h| h.join(RUNTIME_DIR_NAME))
}

fn app_bin_dir() -> Option<PathBuf> {
    app_dir().map(|d| d.join("bin"))
}

fn managed_officecli_path() -> Option<PathBuf> {
    let name = if cfg!(windows) { "officecli.exe" } else { "officecli" };
    app_bin_dir().map(|d| d.join(name))
}

fn managed_cli_path() -> Option<PathBuf> {
    let name = if cfg!(windows) { "accessibleoffice.exe" } else { "accessibleoffice" };
    let bin_subdir = if cfg!(windows) { "Scripts" } else { "bin" };
    runtime_dir().map(|d| d.join(bin_subdir).join(name))
}

fn parse_python_version(s: &str) -> Option<(u32, u32, u32)> {
    // `python3 --version` prints "Python 3.12.1" (or rarely just "3.12.1").
    let re = Regex::new(r"(\d+)\.(\d+)\.(\d+)").ok()?;
    let c = re.captures(s)?;
    Some((
        c[1].parse().ok()?,
        c[2].parse().ok()?,
        c[3].parse().ok()?,
    ))
}

#[derive(Serialize, Clone)]
struct PythonInfo {
    ok: bool,
    path: Option<String>,
    version: Option<String>,
    install_url: String,
    install_hint: String,
}

fn python_install_url() -> &'static str {
    if cfg!(target_os = "macos") {
        "https://www.python.org/downloads/macos/"
    } else if cfg!(windows) {
        "https://www.python.org/downloads/windows/"
    } else {
        "https://www.python.org/downloads/"
    }
}

fn python_install_hint() -> &'static str {
    if cfg!(target_os = "macos") {
        "Click the link to download Python 3.12+ from python.org. After installing, quit and reopen this app."
    } else if cfg!(windows) {
        "Click the link to download Python 3.12+ for Windows. IMPORTANT: on the first installer screen, check the box that says \"Add python.exe to PATH\" before clicking Install. After installing, quit and reopen this app."
    } else {
        "Install Python 3.11+ via your package manager: `sudo apt install python3.12 python3.12-venv` (Debian/Ubuntu) or `sudo dnf install python3.12` (Fedora). Then reopen this app."
    }
}

fn detect_python() -> PythonInfo {
    let candidates: &[&str] = if cfg!(windows) {
        &["py -3.13", "py -3.12", "py -3.11", "py -3", "python3", "python"]
    } else {
        &["python3.13", "python3.12", "python3.11", "python3"]
    };
    for cand in candidates {
        // Split "py -3.12" into argv.
        let mut parts = cand.split_whitespace();
        let Some(prog) = parts.next() else { continue; };
        let extra: Vec<&str> = parts.collect();
        let mut cmd = Command::new(prog);
        cmd.args(&extra).arg("--version");
        let Ok(out) = cmd.output() else { continue; };
        if !out.status.success() {
            continue;
        }
        let s = format!(
            "{}{}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        );
        if let Some((maj, min, _)) = parse_python_version(&s) {
            if maj > 3 || (maj == 3 && min >= 11) {
                // Resolve the launcher path for display.
                let path = which::which(prog)
                    .map(|p| {
                        if extra.is_empty() {
                            p.to_string_lossy().into_owned()
                        } else {
                            format!("{} {}", p.display(), extra.join(" "))
                        }
                    })
                    .ok();
                return PythonInfo {
                    ok: true,
                    path,
                    version: Some(format!("{maj}.{min}")),
                    install_url: python_install_url().to_string(),
                    install_hint: python_install_hint().to_string(),
                };
            }
        }
    }
    PythonInfo {
        ok: false,
        path: None,
        version: None,
        install_url: python_install_url().to_string(),
        install_hint: python_install_hint().to_string(),
    }
}

#[derive(Serialize, Clone)]
struct ComponentStatus {
    ok: bool,
    path: Option<String>,
    version: Option<String>,
}

#[derive(Serialize, Clone)]
struct SetupStatus {
    platform: String,
    python: PythonInfo,
    officecli: ComponentStatus,
    wheel: ComponentStatus,
    claude: ComponentStatus,
    app_dir: String,
    runtime_dir: String,
    setup_complete: bool,
}

fn current_platform() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else {
        "linux"
    }
}

fn check_managed_officecli() -> ComponentStatus {
    let Some(p) = managed_officecli_path() else {
        return ComponentStatus { ok: false, path: None, version: None };
    };
    if !p.exists() {
        return ComponentStatus { ok: false, path: None, version: None };
    }
    let version = version_of(&p);
    ComponentStatus {
        ok: true,
        path: Some(p.to_string_lossy().into_owned()),
        version,
    }
}

fn check_managed_cli() -> ComponentStatus {
    let Some(p) = managed_cli_path() else {
        return ComponentStatus { ok: false, path: None, version: None };
    };
    if !p.exists() {
        return ComponentStatus { ok: false, path: None, version: None };
    }
    // Don't call --version; click rejects it with non-zero. Existence is enough
    // for the installed-state check.
    ComponentStatus {
        ok: true,
        path: Some(p.to_string_lossy().into_owned()),
        version: None,
    }
}

#[tauri::command]
async fn check_setup() -> SetupStatus {
    let python = detect_python();
    let officecli = check_managed_officecli();
    let wheel = check_managed_cli();
    let claude = match resolve_binary(&[CLAUDE_CLI]) {
        Some(p) => ComponentStatus {
            ok: true,
            path: Some(p.to_string_lossy().into_owned()),
            version: version_of(&p),
        },
        None => ComponentStatus { ok: false, path: None, version: None },
    };
    let app_dir_s = app_dir().map(|d| d.to_string_lossy().into_owned()).unwrap_or_default();
    let runtime_dir_s = runtime_dir().map(|d| d.to_string_lossy().into_owned()).unwrap_or_default();
    let setup_complete = python.ok && officecli.ok && wheel.ok;
    SetupStatus {
        platform: current_platform().to_string(),
        python,
        officecli,
        wheel,
        claude,
        app_dir: app_dir_s,
        runtime_dir: runtime_dir_s,
        setup_complete,
    }
}

fn emit_setup(app: &AppHandle, step: &str, status: &str, message: &str) {
    let _ = app.emit(
        "accofc-setup-progress",
        serde_json::json!({ "step": step, "status": status, "message": message }),
    );
}

fn copy_bundled_officecli(app: &AppHandle) -> Result<PathBuf, String> {
    let bin_dir = app_bin_dir().ok_or_else(|| "no HOME".to_string())?;
    std::fs::create_dir_all(&bin_dir).map_err(|e| format!("mkdir {}: {e}", bin_dir.display()))?;
    let dst_name = if cfg!(windows) { "officecli.exe" } else { "officecli" };
    let dst = bin_dir.join(dst_name);

    // Tauri preserves the directory structure of resource globs from
    // tauri.conf.json. Our config bundles `resources/officecli*`, so the
    // resolved path is BaseDirectory::Resource + "resources/<name>".
    let rel = format!("resources/{dst_name}");
    let src = app
        .path()
        .resolve(&rel, BaseDirectory::Resource)
        .map_err(|e| format!("resolve resource {rel}: {e}"))?;
    if !src.exists() {
        return Err(format!(
            "bundled officecli missing at {}; was the app built with prepare-resources?",
            src.display()
        ));
    }
    std::fs::copy(&src, &dst).map_err(|e| format!("copy {} -> {}: {e}", src.display(), dst.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perm = std::fs::metadata(&dst).map_err(|e| e.to_string())?.permissions();
        perm.set_mode(0o755);
        std::fs::set_permissions(&dst, perm).map_err(|e| e.to_string())?;
    }

    #[cfg(target_os = "macos")]
    {
        // Strip Gatekeeper quarantine so first launch doesn't prompt.
        let _ = Command::new("xattr")
            .args(["-d", "com.apple.quarantine"])
            .arg(&dst)
            .status();
    }

    Ok(dst)
}

fn create_venv(python: &PythonInfo) -> Result<PathBuf, String> {
    let runtime = runtime_dir().ok_or_else(|| "no HOME".to_string())?;
    let bin_subdir = if cfg!(windows) { "Scripts" } else { "bin" };
    let venv_python = runtime.join(bin_subdir).join(if cfg!(windows) { "python.exe" } else { "python" });
    if venv_python.exists() {
        return Ok(venv_python);
    }
    std::fs::create_dir_all(&runtime).map_err(|e| format!("mkdir {}: {e}", runtime.display()))?;
    let cmd_line = python
        .path
        .clone()
        .ok_or_else(|| "no python path resolved".to_string())?;
    let mut parts = cmd_line.split_whitespace();
    let prog = parts.next().ok_or_else(|| "empty python command".to_string())?;
    let extra: Vec<&str> = parts.collect();
    let out = Command::new(prog)
        .args(&extra)
        .args(["-m", "venv"])
        .arg(&runtime)
        .output()
        .map_err(|e| format!("spawn venv: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "python -m venv failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(venv_python)
}

fn install_wheel_into_venv(app: &AppHandle, venv_python: &Path) -> Result<(), String> {
    // Bundled wheels live in BaseDirectory::Resource + "resources/" because of
    // the glob in tauri.conf.json. Wheel filename embeds the version, so glob
    // by extension instead of hardcoding the name.
    let resource_root = app
        .path()
        .resolve("resources", BaseDirectory::Resource)
        .map_err(|e| format!("resolve resource root: {e}"))?;
    let wheel = std::fs::read_dir(&resource_root)
        .map_err(|e| format!("read resource dir {}: {e}", resource_root.display()))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .find(|p| p.extension().is_some_and(|ext| ext == "whl"))
        .ok_or_else(|| {
            format!(
                "no wheel found in {}; was the app built with prepare-resources?",
                resource_root.display()
            )
        })?;

    // Upgrade pip first so it understands modern wheel metadata, then force-reinstall the wheel.
    let pip = Command::new(venv_python)
        .args(["-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        .output()
        .map_err(|e| format!("spawn pip upgrade: {e}"))?;
    if !pip.status.success() {
        return Err(format!(
            "pip upgrade failed: {}",
            String::from_utf8_lossy(&pip.stderr).trim()
        ));
    }
    let install = Command::new(venv_python)
        .args(["-m", "pip", "install", "--quiet", "--force-reinstall"])
        .arg(&wheel)
        .output()
        .map_err(|e| format!("spawn pip install: {e}"))?;
    if !install.status.success() {
        return Err(format!(
            "pip install failed: {}",
            String::from_utf8_lossy(&install.stderr).trim()
        ));
    }
    Ok(())
}

fn try_install_officecli_skills(officecli: &Path) {
    // Best-effort: officecli exits non-zero if no AI tool is detected. Stages
    // 1-3 don't need the skills, so this is purely a stage-4 nicety.
    for skill in ["pptx", "word"] {
        let _ = Command::new(officecli)
            .args(["skills", "install", skill])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

#[tauri::command]
async fn setup_dependencies(app: AppHandle, force: bool) -> Result<SetupStatus, String> {
    // Step 1: confirm Python is present. If not, surface and bail — the UI
    // will show a per-platform install modal.
    emit_setup(&app, "python", "starting", "Checking for Python 3.11+");
    let python = detect_python();
    if !python.ok {
        emit_setup(&app, "python", "fail", "Python 3.11 or newer not found");
        return Err("python_missing".to_string());
    }
    emit_setup(
        &app,
        "python",
        "ok",
        &format!("Found Python {}", python.version.clone().unwrap_or_default()),
    );

    // Step 2: officecli — copy bundled resource into ~/.accessibleoffice/bin/.
    if force {
        if let Some(d) = app_dir() {
            let _ = std::fs::remove_dir_all(&d);
        }
    }
    emit_setup(&app, "officecli", "starting", "Installing OfficeCLI");
    let officecli_path = match copy_bundled_officecli(&app) {
        Ok(p) => {
            emit_setup(&app, "officecli", "ok", "OfficeCLI ready");
            p
        }
        Err(e) => {
            emit_setup(&app, "officecli", "fail", &format!("OfficeCLI install failed: {e}"));
            return Err(e);
        }
    };

    // Step 3: venv.
    if force {
        if let Some(d) = runtime_dir() {
            let _ = std::fs::remove_dir_all(&d);
        }
    }
    emit_setup(&app, "venv", "starting", "Creating Python runtime (this can take ~10s)");
    let venv_python = match create_venv(&python) {
        Ok(p) => {
            emit_setup(&app, "venv", "ok", "Runtime ready");
            p
        }
        Err(e) => {
            emit_setup(&app, "venv", "fail", &format!("venv failed: {e}"));
            return Err(e);
        }
    };

    // Step 4: install the wheel.
    emit_setup(&app, "wheel", "starting", "Installing AccessibleOffice (≈30s)");
    if let Err(e) = install_wheel_into_venv(&app, &venv_python) {
        emit_setup(&app, "wheel", "fail", &format!("wheel install failed: {e}"));
        return Err(e);
    }
    emit_setup(&app, "wheel", "ok", "AccessibleOffice installed");

    // Step 5: officecli skills (best-effort, don't fail the wizard if no AI tool present).
    emit_setup(&app, "skills", "starting", "Registering OfficeCLI skills");
    try_install_officecli_skills(&officecli_path);
    emit_setup(&app, "skills", "ok", "Setup complete");

    Ok(check_setup().await)
}

#[tauri::command]
async fn check_cli() -> CliStatus {
    // Prefer our managed venv binary so the install path is deterministic.
    if let Some(p) = managed_cli_path() {
        if p.exists() {
            return CliStatus {
                found: true,
                path: Some(p.to_string_lossy().into_owned()),
                version: None,
            };
        }
    }
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

// Resolve the CLI binary, preferring the managed venv copy installed by the
// setup wizard. Falls back to system PATH so power users can use a global
// install if they want.
fn resolve_cli_binary() -> Option<PathBuf> {
    if let Some(p) = managed_cli_path() {
        if p.exists() {
            return Some(p);
        }
    }
    resolve_binary(&[CLI_NAME, LEGACY_CLI_NAME])
}

// Build a PATH that has our managed officecli dir prepended so the CLI's
// subprocess calls find it without needing officecli installed system-wide.
fn augmented_path() -> std::ffi::OsString {
    let mut parts: Vec<PathBuf> = Vec::new();
    if let Some(d) = app_bin_dir() {
        if d.exists() {
            parts.push(d);
        }
    }
    if let Some(existing) = std::env::var_os("PATH") {
        for p in std::env::split_paths(&existing) {
            parts.push(p);
        }
    }
    std::env::join_paths(parts).unwrap_or_else(|_| std::env::var_os("PATH").unwrap_or_default())
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

#[cfg(unix)]
fn configure_command_for_cancel(cmd: &mut Command) {
    cmd.process_group(0);
}

#[cfg(not(unix))]
fn configure_command_for_cancel(_cmd: &mut Command) {}

#[tauri::command]
async fn run_a11yfix(
    app: AppHandle,
    state: State<'_, RunState>,
    run_id: String,
    file_path: String,
    mode: String,
) -> Result<serde_json::Value, String> {
    let Some(binary) = resolve_cli_binary() else {
        return Err(
            "AccessibleOffice is not set up. Click \"Set up\" in the welcome screen, or reinstall from the app menu."
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
        .env("PATH", augmented_path())
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
    configure_command_for_cancel(&mut cmd);

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
    let Some(binary) = resolve_cli_binary() else {
        return Err(
            "AccessibleOffice is not set up. Click \"Set up\" in the welcome screen, or reinstall from the app menu."
                .to_string(),
        );
    };

    let mut cmd = Command::new(&binary);
    cmd.arg("--folder")
        .arg(&folder_path)
        .arg("--mode")
        .arg(&mode)
        .env("PATH", augmented_path());
    if let Some(cap) = max_cost_usd {
        cmd.arg("--max-cost-total-usd").arg(format!("{cap}"));
    }
    if mode == "full" {
        cmd.arg("--dry-run");
        // Batch mode in the CLI never spawns interactive Claude Code; ensure the
        // user knows the agent step is dry-run only here.
        let _ = app.emit(
            "a11yfix-mode-note",
            "Batch mode runs `full` without the interactive agent step. Per-file fixes from stages 1-3 are still applied.",
        );
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    configure_command_for_cancel(&mut cmd);

    let _ = app.emit(
        "a11yfix-log",
        format!(
            "$ {} --folder {} --mode {}{}",
            binary.display(),
            folder_path,
            mode,
            if mode == "full" { " --dry-run" } else { "" }
        ),
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
        terminate_process_tree(pid);
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/F", "/T"])
            .status();
    }
    Ok(())
}

#[cfg(unix)]
fn collect_descendants(pid: u32, out: &mut Vec<u32>) {
    let Ok(output) = Command::new("pgrep").args(["-P", &pid.to_string()]).output() else {
        return;
    };
    if !output.status.success() {
        return;
    }
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let Ok(child) = line.trim().parse::<u32>() else {
            continue;
        };
        collect_descendants(child, out);
        out.push(child);
    }
}

#[cfg(unix)]
fn terminate_process_tree(pid: u32) {
    let mut pids = Vec::new();
    collect_descendants(pid, &mut pids);
    pids.push(pid);
    let process_group = format!("-{pid}");
    for signal in ["-TERM", "-KILL"] {
        let _ = Command::new("kill").arg(signal).arg(&process_group).status();
        for child in &pids {
            let _ = Command::new("kill").arg(signal).arg(child.to_string()).status();
        }
        std::thread::sleep(Duration::from_millis(250));
    }
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
            check_setup,
            setup_dependencies,
            cancel_run,
            reveal_in_finder,
            open_url
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
