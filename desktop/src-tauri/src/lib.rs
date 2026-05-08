use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use tauri::{Emitter, Manager};
use tauri::webview::WebviewWindowBuilder;
use tauri::utils::config::WebviewUrl;
// tauri_plugin_shell: ShellExt import removed, but plugin still initialized
// for its "open" capability (open URLs in system browser via shell:allow-open).
use tokio::sync::Mutex;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

/// Resolve the user's full PATH by spawning a login shell.
///
/// macOS GUI apps launched from Finder/Dock inherit a minimal PATH from launchd
/// (~`/usr/bin:/bin:/usr/sbin:/sbin`).  The user's real PATH — including tools
/// installed via Homebrew, nvm, pyenv, Toolbox, AIM, mise, etc. — is only
/// available after `.zprofile` / `.bash_profile` / `.profile` have been sourced.
///
/// Strategy:
///   1. Spawn a **login** shell (`zsh -lc` / `bash -lc`) and print `$PATH`.
///      This sources profile files where PATH is configured.  Non-interactive
///      to avoid compinit/oh-my-zsh overhead.  Timeout: 3 seconds.
///   2. If that fails (no shell, timeout, parse error), fall back to a
///      hardcoded list of well-known tool directories so the app still works.
fn get_enhanced_path() -> String {
    // Start with fallback paths (well-known tool directories).
    // These are ALWAYS included because shell profile configs are unreliable
    // (e.g. toolbox/aim PATH may be in .zshrc which login shells don't source).
    let fallback = get_fallback_path();

    // Try login-shell resolution to pick up user-specific PATH entries
    // (e.g. from .zprofile, .zshenv, conda, nvm, etc.).
    #[cfg(not(target_os = "windows"))]
    {
        if let Some(shell_path) = resolve_path_from_login_shell() {
            // Merge: login-shell PATH first (user preference), then fallback dirs.
            // Duplicates are harmless — the OS deduplicates on lookup.
            return format!("{}:{}", shell_path, fallback);
        }
    }

    fallback
}

/// Spawn a login shell and read the resulting PATH.
///
/// Uses `-lc` (login, non-interactive) to source `.zprofile`/`.zshenv`/`.profile`
/// where PATH is typically configured, without triggering interactive overhead
/// (compinit, oh-my-zsh plugins, conda activate prompts, etc.).
///
/// A 3-second timeout prevents hung shell configs from blocking app startup.
///
/// Returns `Some(path_string)` on success, `None` on any failure (timeout,
/// parse error, missing shell).
#[cfg(not(target_os = "windows"))]
fn resolve_path_from_login_shell() -> Option<String> {
    use std::process::Command;
    use std::time::{Duration, Instant};
    use std::thread;

    let timeout = Duration::from_secs(3);

    // Detect user's default shell; fall back to zsh (macOS default since Catalina).
    let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());

    // `-l` = login (sources profile files where PATH is set).
    // `-c` = execute command.  No `-i` to avoid interactive overhead.
    let mut child = match Command::new(&shell)
        .args(["-lc", "echo __SWARM_PATH_START__${PATH}__SWARM_PATH_END__"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return None,
    };

    // Poll for completion with timeout to avoid blocking on hung .zshrc/.zprofile.
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_status)) => break,       // child exited
            Ok(None) => {                      // still running
                if start.elapsed() >= timeout {
                    let _ = child.kill();
                    let _ = child.wait();      // reap zombie
                    return None;
                }
                thread::sleep(Duration::from_millis(50));
            }
            Err(_) => return None,
        }
    }

    let output = child.wait_with_output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);

    // Extract PATH between markers to avoid shell motd / prompts
    if let Some(start) = stdout.find("__SWARM_PATH_START__") {
        if let Some(end) = stdout.find("__SWARM_PATH_END__") {
            let path = &stdout[start + "__SWARM_PATH_START__".len()..end];
            if !path.is_empty() && path.contains('/') {
                return Some(path.to_string());
            }
        }
    }
    None
}

/// Hardcoded fallback PATH for when login-shell resolution fails.
///
/// This covers the common tool installation directories across platforms.
/// It is intentionally broad — duplicate or non-existent entries are harmless.
fn get_fallback_path() -> String {
    let current_path = env::var("PATH").unwrap_or_default();

    #[cfg(target_os = "windows")]
    let (home, path_separator) = (
        env::var("USERPROFILE").unwrap_or_default(),
        ";"
    );

    #[cfg(not(target_os = "windows"))]
    let (home, path_separator) = (
        env::var("HOME").unwrap_or_default(),
        ":"
    );

    let mut paths = Vec::new();

    #[cfg(target_os = "macos")]
    {
        paths.extend_from_slice(&[
            "/opt/homebrew/bin".to_string(),
            "/opt/homebrew/sbin".to_string(),
            "/usr/local/bin".to_string(),
            "/usr/local/sbin".to_string(),
            "/usr/bin".to_string(),
            "/bin".to_string(),
            "/usr/sbin".to_string(),
            "/sbin".to_string(),
            format!("{}/Library/pnpm", home),
        ]);

        // Scan Homebrew's versioned package paths (node@XX, python@XX)
        for homebrew_opt in &["/opt/homebrew/opt", "/usr/local/opt"] {
            if let Ok(entries) = std::fs::read_dir(homebrew_opt) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str.starts_with("node") || name_str.starts_with("python") {
                        let bin_path = entry.path().join("bin");
                        if bin_path.exists() {
                            paths.push(bin_path.to_string_lossy().to_string());
                        }
                    }
                }
            }
        }
    }

    #[cfg(target_os = "linux")]
    {
        paths.extend_from_slice(&[
            "/usr/local/bin".to_string(),
            "/usr/local/sbin".to_string(),
            "/usr/bin".to_string(),
            "/bin".to_string(),
            "/usr/sbin".to_string(),
            "/sbin".to_string(),
        ]);
    }

    #[cfg(target_os = "windows")]
    {
        if let Ok(programfiles) = env::var("ProgramFiles") {
            paths.push(format!(r"{}\nodejs", programfiles));
            paths.push(format!(r"{}\Git\cmd", programfiles));
        }
        if let Ok(programfiles_x86) = env::var("ProgramFiles(x86)") {
            paths.push(format!(r"{}\nodejs", programfiles_x86));
        }
        if let Ok(appdata) = env::var("APPDATA") {
            paths.push(format!(r"{}\npm", appdata));
        }
        if let Ok(localappdata) = env::var("LOCALAPPDATA") {
            paths.push(format!(r"{}\Programs\Python\Python312", localappdata));
            paths.push(format!(r"{}\Programs\Python\Python311", localappdata));
            paths.push(format!(r"{}\Programs\Python\Python310", localappdata));
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        paths.extend_from_slice(&[
            format!("{}/.volta/bin", home),
            format!("{}/.fnm/aliases/default/bin", home),
            format!("{}/.pyenv/shims", home),
            format!("{}/.pyenv/bin", home),
            format!("{}/.npm-global/bin", home),
            format!("{}/.local/bin", home),
            format!("{}/.toolbox/bin", home),
            format!("{}/.aim/mcp-servers", home),
        ]);

        // mise (formerly rtx) managed runtimes
        let mise_dir = format!("{}/.local/share/mise/installs", home);
        if let Ok(tools) = std::fs::read_dir(&mise_dir) {
            for tool in tools.flatten() {
                if let Ok(versions) = std::fs::read_dir(tool.path()) {
                    for version in versions.flatten() {
                        let bin_path = version.path().join("bin");
                        if bin_path.exists() {
                            paths.push(bin_path.to_string_lossy().to_string());
                        }
                    }
                }
            }
        }

        // nvm managed node versions
        let nvm_dir = format!("{}/.nvm/versions/node", home);
        if let Ok(entries) = std::fs::read_dir(&nvm_dir) {
            for entry in entries.flatten() {
                let bin_path = entry.path().join("bin");
                if bin_path.exists() {
                    paths.push(bin_path.to_string_lossy().to_string());
                }
            }
        }
    }

    #[cfg(target_os = "windows")]
    {
        paths.push(format!(r"{}\AppData\Roaming\npm", home));
        paths.push(format!(r"{}\.volta\bin", home));
        if let Ok(nvm_home) = env::var("NVM_HOME") {
            paths.push(nvm_home);
        }
    }

    if !current_path.is_empty() {
        paths.push(current_path);
    }

    paths.join(path_separator)
}

/// Fixed port for daemon mode.
const DAEMON_PORT: u16 = 18321;
const DAEMON_PLIST_RELPATH: &str = "Library/LaunchAgents/com.swarmai.backend.plist";

// Backend state — tracks connection to daemon (macOS) or owned subprocess (Windows).
struct BackendState {
    port: u16,
    running: bool,
    pid: Option<u32>,
    /// Set to `true` when shutdown is intentional (stop_backend, window close, app exit).
    intentional_shutdown: bool,
    /// macOS: true (launchd daemon, survives app close).
    /// Windows: false (subprocess, dies with app).
    is_daemon_mode: bool,
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            port: DAEMON_PORT,
            running: false,
            pid: None,
            intentional_shutdown: false,
            // macOS uses launchd daemon; Windows uses subprocess
            #[cfg(target_os = "macos")]
            is_daemon_mode: true,
            #[cfg(not(target_os = "macos"))]
            is_daemon_mode: false,
        }
    }
}

/// Maximum time (seconds) to wait for the backend to complete shutdown.
/// Covers: HookContext build (~1s) + DailyActivity batch (~5s) + drain (~8s).
/// The curl/PowerShell timeout is set to this value.
const SHUTDOWN_GRACE_SECONDS: u64 = 10;

// NOTE: MAX_AUTO_RESTARTS, RESTART_WINDOW_SECS, STOP_BACKEND_SLEEP_SECONDS removed.
// Daemon auto-restart is handled by launchd KeepAlive — no Tauri-side restart logic.

// Send graceful shutdown request to backend via HTTP
// This allows the backend to properly terminate Claude CLI child processes before being killed
#[cfg(target_os = "windows")]
fn send_shutdown_request(port: u16) -> bool {
    // Use PowerShell to send HTTP POST request (Windows built-in, no dependencies needed)
    let result = std::process::Command::new("powershell")
        .args([
            "-NoProfile",
            "-Command",
            &format!(
                "try {{ Invoke-WebRequest -Uri 'http://127.0.0.1:{}/shutdown' -Method POST -TimeoutSec {} }} catch {{}}",
                port, SHUTDOWN_GRACE_SECONDS
            ),
        ])
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
        .output();

    match result {
        Ok(output) => {
            if output.status.success() {
                println!("Sent shutdown request to backend on port {}", port);
                true
            } else {
                eprintln!("Shutdown request returned non-zero exit code on port {}", port);
                false
            }
        }
        Err(e) => {
            eprintln!("Failed to send shutdown request to backend on port {}: {}", port, e);
            false
        }
    }
}

// Send graceful shutdown request to backend via HTTP (macOS/Linux)
// Uses curl which is available on most Unix systems
#[cfg(not(target_os = "windows"))]
fn send_shutdown_request(port: u16) -> bool {
    let timeout = SHUTDOWN_GRACE_SECONDS.to_string();
    let result = std::process::Command::new("curl")
        .args([
            "-s",                                          // Silent mode
            "-X", "POST",                                  // POST request
            "-m", &timeout,                                // Timeout = SHUTDOWN_GRACE_SECONDS
            &format!("http://127.0.0.1:{}/shutdown", port),
        ])
        .output();

    match result {
        Ok(output) => {
            if output.status.success() {
                println!("Sent shutdown request to backend on port {}", port);
                true
            } else {
                eprintln!("Shutdown request failed on port {}", port);
                false
            }
        }
        Err(e) => {
            eprintln!("Failed to send shutdown request to backend on port {}: {}", port, e);
            false
        }
    }
}

// Kill process tree on Windows using taskkill
#[cfg(target_os = "windows")]
fn kill_process_tree(pid: u32) {
    // Use taskkill with /T flag to kill the entire process tree
    // /F = force, /T = tree (kill child processes), /PID = process ID
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .creation_flags(0x08000000) // CREATE_NO_WINDOW - hide the console window
        .output();
    println!("Killed process tree for PID: {}", pid);
}

// Kill claude.exe processes that were children of a specific parent PID on Windows
// Uses PowerShell Get-CimInstance (WMIC is deprecated on Windows 11)
#[cfg(target_os = "windows")]
fn kill_claude_child_processes(parent_pid: u32) {
    // Use PowerShell Get-CimInstance to find claude.exe processes that were children of our backend
    // This avoids killing claude.exe processes from other SwarmAI instances or direct CLI usage
    let ps_script = format!(
        "Get-CimInstance Win32_Process | Where-Object {{ $_.Name -eq 'claude.exe' -and $_.ParentProcessId -eq {} }} | ForEach-Object {{ $_.ProcessId }}",
        parent_pid
    );

    let output = std::process::Command::new("powershell")
        .args(["-NoProfile", "-Command", &ps_script])
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
        .output();

    if let Ok(out) = output {
        let stdout = String::from_utf8_lossy(&out.stdout);
        // Each line is a PID
        for line in stdout.lines() {
            let trimmed = line.trim();
            if !trimmed.is_empty() {
                if let Ok(pid) = trimmed.parse::<u32>() {
                    let _ = std::process::Command::new("taskkill")
                        .args(["/F", "/PID", &pid.to_string()])
                        .creation_flags(0x08000000)
                        .output();
                    println!("Killed claude.exe child process PID: {}", pid);
                }
            }
        }
    }
    println!("Finished checking for claude.exe child processes of PID {}", parent_pid);
}

// On Linux, kill the process tree (used in subprocess mode).
// macOS doesn't need this — daemon lifecycle managed by launchd.
#[cfg(target_os = "linux")]
fn kill_process_tree(pid: u32) {
    let _ = std::process::Command::new("pkill")
        .args(["-TERM", "-P", &pid.to_string()])
        .output();
    std::thread::sleep(std::time::Duration::from_millis(200));
    let _ = std::process::Command::new("pkill")
        .args(["-KILL", "-P", &pid.to_string()])
        .output();
    let _ = std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .output();
    println!("Killed process tree for PID: {}", pid);
}

// macOS: no-op (daemon mode, never called — but needed for compilation)
#[cfg(target_os = "macos")]
fn kill_process_tree(_pid: u32) {
    // Daemon mode: process lifecycle is managed by launchd, not Tauri.
}

type SharedBackendState = Arc<Mutex<BackendState>>;

#[derive(Serialize, Deserialize)]
pub struct BackendStatus {
    running: bool,
    port: u16,
    is_daemon_mode: bool,
}

// Backend auto-restart: launchd KeepAlive (macOS daemon), or Tauri respawn (Windows/Linux subprocess).
// The health watchdog (spawn_daemon_health_watchdog) monitors backend liveness
// and emits frontend events on death/recovery.

/// Gracefully shut down the backend and then force-kill as safety net.
///
/// Platform behavior:
/// - macOS (daemon mode): do NOT kill — daemon survives app close.
/// - Windows (subprocess mode): send /shutdown → wait → force-kill process tree.
///
/// Double-fire safe: if `backend.running` is already false (set by a
/// previous handler in the same close sequence), skips the kill.
fn graceful_shutdown_and_kill(state: SharedBackendState, context: &str) {
    tauri::async_runtime::block_on(async {
        let mut backend = state.lock().await;
        let port = backend.port;
        let pid = backend.pid;
        let is_daemon = backend.is_daemon_mode;
        let was_running = backend.running;

        // Mark as intentional + not running under lock
        backend.intentional_shutdown = true;
        backend.running = false;
        backend.pid = None;
        drop(backend); // Release lock before blocking I/O

        if is_daemon {
            // Daemon mode (macOS): leave it running — channels, jobs stay alive.
            println!("[{}] Backend is daemon — leaving it running on port {}", context, port);
            return;
        }

        // Subprocess mode (Windows/Linux): we own the process, must kill it.
        if !was_running {
            println!("[{}] Backend already stopped — skipping kill", context);
            return;
        }

        println!("[{}] Subprocess mode — shutting down backend on port {}", context, port);

        // Step 1: Graceful HTTP shutdown (gives backend time to disconnect sessions)
        send_shutdown_request(port);

        // Step 2: Wait briefly for graceful exit
        std::thread::sleep(std::time::Duration::from_secs(3));

        // Step 3: Force kill process tree as safety net
        if let Some(pid) = pid {
            kill_process_tree(pid);
            #[cfg(target_os = "windows")]
            kill_claude_child_processes(pid);
        }
    });
}


/// Parse a /health JSON response body. Returns (is_healthy, version, boot_id).
/// Uses serde_json for correct parsing regardless of JSON formatting.
fn parse_health_response(body: &str) -> (bool, Option<String>, Option<String>) {
    match serde_json::from_str::<serde_json::Value>(body) {
        Ok(json) => {
            let is_healthy = json.get("status")
                .and_then(|v| v.as_str())
                .map(|s| s == "healthy")
                .unwrap_or(false);
            let version = json.get("version")
                .and_then(|v| v.as_str())
                .map(String::from);
            let boot_id = json.get("boot_id")
                .and_then(|v| v.as_str())
                .map(String::from);
            (is_healthy, version, boot_id)
        }
        Err(_) => (false, None, None),
    }
}

/// Probe daemon health endpoint with retries.
/// Returns Some(port) if daemon is healthy, None otherwise.
async fn probe_daemon_health(max_attempts: u32, interval_secs: u64) -> Option<u16> {
    let probe_url = format!("http://127.0.0.1:{}/health", DAEMON_PORT);
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return None,
    };

    for attempt in 1..=max_attempts {
        if let Ok(resp) = client.get(&probe_url).send().await {
            if let Ok(body) = resp.text().await {
                let (healthy, _, _) = parse_health_response(&body);
                if healthy {
                    return Some(DAEMON_PORT);
                }
            }
        }
        if attempt < max_attempts {
            tokio::time::sleep(tokio::time::Duration::from_secs(interval_secs)).await;
        }
    }
    None
}

/// Extract the daemon version from the /health JSON response.
/// Returns None if the version field is missing or unparseable.
async fn get_daemon_version() -> Option<String> {
    let probe_url = format!("http://127.0.0.1:{}/health", DAEMON_PORT);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .ok()?;

    let resp = client.get(&probe_url).send().await.ok()?;
    let body = resp.text().await.ok()?;
    let (_, version, _) = parse_health_response(&body);
    version
}

/// Sync daemon binary to match app version when a mismatch is detected.
///
/// Flow: graceful shutdown → atomic binary deploy → restart launchd → verify.
/// Returns Ok(()) on success, Err(msg) on failure (surfaced to user via event).
///
/// NOTE: the `app` parameter is retained for API compatibility with the
/// background wrapper (`sync_daemon_version_background`).  Event emission
/// has been moved to the wrapper, so this function no longer emits
/// `backend-upgrading` or `backend-upgraded` directly.
async fn sync_daemon_version(_app: &tauri::AppHandle, app_version: &str) -> Result<(), String> {
    let daemon_version = get_daemon_version().await
        .unwrap_or_else(|| "unknown".to_string());

    if daemon_version == app_version {
        return Ok(());  // Versions match — nothing to do
    }

    println!(
        "[Tauri] Daemon version mismatch: daemon={}, app={} — upgrading",
        daemon_version, app_version
    );
    // NOTE: the `backend-upgrading` event is emitted by
    // `sync_daemon_version_background` — the single source of truth for
    // upgrade lifecycle events.  Keeping the println! for debuggability.

    // Step 1: Bootout from launchd FIRST — prevents KeepAlive restart race.
    // bootout both removes the service from management AND sends SIGTERM,
    // so the old order (shutdown → bootout) had a race window where
    // KeepAlive restarted the daemon between shutdown and bootout, causing
    // multiple processes to fight over backend.json and SQLite.
    let home = std::env::var("HOME").unwrap_or_default();
    let uid_output = std::process::Command::new("id").arg("-u").output()
        .map_err(|e| format!("Failed to get UID: {}", e))?;
    let uid = String::from_utf8_lossy(&uid_output.stdout).trim().to_string();
    let gui_target = format!("gui/{}", uid);
    let plist_path = format!("{}/{}", home, DAEMON_PLIST_RELPATH);

    // Capture daemon PID BEFORE bootout deregisters the service.
    // After bootout, `launchctl print` returns non-zero immediately even
    // if the process is still draining, so we need the PID in advance.
    let service_label = "com.swarmai.backend";
    let service_target = format!("{}/{}", gui_target, service_label);
    let daemon_pid: Option<u32> = std::process::Command::new("launchctl")
        .args(["print", &service_target])
        .output()
        .ok()
        .and_then(|o| {
            let stdout = String::from_utf8_lossy(&o.stdout);
            stdout.lines()
                .find(|l| l.trim_start().starts_with("pid = "))
                .and_then(|l| l.trim().strip_prefix("pid = "))
                .and_then(|s| s.trim().parse::<u32>().ok())
        });
    if let Some(pid) = daemon_pid {
        println!("[Tauri] Captured daemon PID {} before bootout", pid);
    }

    let _ = std::process::Command::new("launchctl")
        .args(["bootout", &gui_target, &plist_path])
        .output();

    // Step 2: Also send graceful shutdown as belt-and-suspenders.
    // bootout's SIGTERM should trigger FastAPI lifespan shutdown, but an
    // explicit HTTP request ensures the backend runs its full cleanup path
    // (backend.json removal, session disconnect) even if SIGTERM races
    // with the event loop.
    send_shutdown_request(DAEMON_PORT);

    // Step 3: Wait for daemon process to FULLY EXIT before deploying.
    //
    // CRITICAL: PyInstaller --onefile keeps PYZ bytecode inside the binary
    // and re-opens it BY PATH on every lazy import (pyimod01_archive.py:119).
    // If we replace the binary while the daemon is still running, the next
    // lazy import opens the NEW binary with OLD PYZ offsets → zlib corruption
    // → ALL_RETRIES_EXHAUSTED for every session (COE: 2026-05-01 21:04).
    //
    // bootout sends SIGTERM but the process may take seconds to drain hooks
    // and disconnect sessions.  Poll until the process is actually gone.
    //
    // Poll `kill -0 <pid>` using the PID captured before bootout.
    // kill -0 checks process existence without sending a signal — no
    // false positives from cmdline matching (unlike pgrep -f).
    if let Some(pid) = daemon_pid {
        println!("[Tauri] Waiting for daemon process (PID {}) to exit", pid);
        let mut waited = 0u32;
        loop {
            // kill -0 checks if process exists without sending a signal
            let alive = std::process::Command::new("kill")
                .args(["-0", &pid.to_string()])
                .output()
                .map(|o| o.status.success())
                .unwrap_or(false);

            if !alive {
                println!("[Tauri] Daemon process exited after {}s", waited);
                break;
            }
            if waited >= 15 {
                println!("[Tauri] Daemon PID {} still alive after {}s — sending SIGKILL", pid, waited);
                let _ = std::process::Command::new("kill")
                    .args(["-9", &pid.to_string()])
                    .output();
                tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                break;
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
            waited += 1;
        }
    } else {
        // No PID found — daemon wasn't running or already exited.
        // Brief sleep as fallback (same as original pre-fix behavior).
        println!("[Tauri] No daemon PID found — waiting 3s as fallback");
        tokio::time::sleep(tokio::time::Duration::from_secs(3)).await;
    }

    // Step 4: Atomic binary deploy from app bundle
    let daemon_dir = format!("{}/.swarm-ai/daemon", home);

    // Find the bundled binary — check app bundle Contents/MacOS first
    let exe_path = std::env::current_exe().unwrap_or_default();
    let bundle_dir = exe_path.parent().unwrap_or(std::path::Path::new("/"));
    let bundled_binary = bundle_dir.join("python-backend");

    if !bundled_binary.exists() {
        return Err(format!(
            "Bundled daemon binary not found at: {}",
            bundled_binary.display()
        ));
    }

    let target_binary = format!("{}/python-backend", daemon_dir);
    let tmp_binary = format!("{}/python-backend.tmp", daemon_dir);

    // Ensure daemon dir exists
    std::fs::create_dir_all(&daemon_dir)
        .map_err(|e| format!("Failed to create daemon dir: {}", e))?;

    // Atomic deploy: copy to .tmp, then rename (cleanup .tmp on failure)
    std::fs::copy(&bundled_binary, &tmp_binary)
        .map_err(|e| format!("Failed to copy binary: {}", e))?;
    if let Err(e) = std::fs::rename(&tmp_binary, &target_binary) {
        let _ = std::fs::remove_file(&tmp_binary);  // cleanup partial deploy
        return Err(format!("Failed to rename binary: {}", e));
    }

    // Set executable permissions
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(
            &target_binary,
            std::fs::Permissions::from_mode(0o755),
        );
    }

    // Write version file — format: "{semver} {git_hash} {timestamp}"
    // Consistent with daemon-lib.sh and auto_install_daemon.
    // git_hash: not available at runtime in production (no .git in app bundle),
    // so we write "release" as placeholder. Dev deploys via daemon-lib.sh get real hash.
    let timestamp = std::process::Command::new("date")
        .arg("+%Y-%m-%d %H:%M:%S")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown".to_string());
    let version_content = format!("{} release {}", app_version, timestamp);
    let _ = std::fs::write(format!("{}/.version", daemon_dir), version_content);

    println!("[Tauri] Daemon binary deployed: {}", target_binary);

    // Step 5: Bootstrap (reload into launchd with new binary)
    let _ = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, &plist_path])
        .output();

    // Step 6: Verify new version
    if let Some(_port) = probe_daemon_health(10, 2).await {
        let new_version = get_daemon_version().await.unwrap_or_default();
        if new_version == app_version {
            println!("[Tauri] Daemon upgraded successfully: {}", app_version);
            // NOTE: the `backend-upgraded` event is emitted by
            // `sync_daemon_version_background` — the single source of
            // truth for upgrade lifecycle events.
            return Ok(());
        }
        println!(
            "[Tauri] Daemon running but version still {}, expected {}",
            new_version, app_version
        );
    }

    Err("Daemon upgrade failed — daemon not responding after restart".to_string())
}

/// Background wrapper around `sync_daemon_version`.
///
/// This function is designed to be spawned via `tauri::async_runtime::spawn`
/// and NEVER awaited on the `start_backend` critical path. It:
///
/// 1. Pre-checks the daemon's current version to avoid emitting spurious
///    `backend-upgrading` events when versions already match (required for
///    Property 2 — Preservation).
/// 2. Emits `backend-upgrading` with `{from, to}` payload at start.
/// 3. Invokes `sync_daemon_version`, catching any panic via
///    `FutureExt::catch_unwind`.
/// 4. Emits exactly one terminal event: `backend-upgraded` on `Ok(())`,
///    `backend-upgrade-failed` on `Err(_)` or panic.
///
/// Safety: this function never returns an error. All failure modes are
/// converted into events so that the Tauri runtime cannot surface an
/// uncaught background error.
async fn sync_daemon_version_background(
    app: tauri::AppHandle,
    app_version: String,
) {
    use futures::FutureExt;

    // Pre-check: if the daemon version already matches, do nothing and
    // do not emit any events. This preserves the happy-path invariant
    // that a matching version produces zero observable upgrade activity.
    let daemon_version = get_daemon_version().await
        .unwrap_or_else(|| "unknown".to_string());
    if daemon_version == app_version {
        return;
    }

    // Announce start.
    let _ = app.emit(
        "backend-upgrading",
        serde_json::json!({
            "from": daemon_version,
            "to": app_version,
        }),
    );

    // Run the existing sync routine, catching panics.
    let result = std::panic::AssertUnwindSafe(
        sync_daemon_version(&app, &app_version)
    )
    .catch_unwind()
    .await;

    match result {
        Ok(Ok(())) => {
            println!("[Tauri] Background daemon upgrade succeeded");
            let _ = app.emit(
                "backend-upgraded",
                serde_json::json!({ "version": app_version }),
            );
        }
        Ok(Err(e)) => {
            println!("[Tauri] Background daemon upgrade failed: {}", e);
            let _ = app.emit("backend-upgrade-failed", e);
        }
        Err(panic_info) => {
            let msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                format!("panic in sync_daemon_version: {}", s)
            } else if let Some(s) = panic_info.downcast_ref::<String>() {
                format!("panic in sync_daemon_version: {}", s)
            } else {
                "panic in sync_daemon_version (unknown payload)".to_string()
            };
            eprintln!("[Tauri] {}", msg);
            let _ = app.emit("backend-upgrade-failed", msg);
        }
    }
}

/// Auto-install the daemon: deploy binary + wrapper + plist, then bootstrap.
///
/// This runs on EVERY app launch (idempotent). On first launch it provisions
/// everything from scratch. On subsequent launches it updates the binary if
/// the app bundle has a newer version, and ensures launchd has it loaded.
///
/// Steps:
/// 1. Copy `python-backend` binary from app bundle to `~/.swarm-ai/daemon/`
/// 2. Copy `swarmai_backend.sh` wrapper from app resources to `~/.swarm-ai/`
/// 3. Generate plist from template (substitute HOME, LOG_DIR, WRAPPER_PATH)
/// 4. Write plist to `~/Library/LaunchAgents/com.swarmai.backend.plist`
/// 5. `launchctl bootstrap gui/<uid> <plist_path>`
///
/// Idempotent — safe to call on every launch.
fn auto_install_daemon(app: &tauri::AppHandle) -> Result<(), String> {
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() {
        return Err("HOME not set".to_string());
    }
    let home_path = std::path::Path::new(&home);

    let daemon_dir = home_path.join(".swarm-ai").join("daemon");
    let log_dir = home_path.join(".swarm-ai").join("logs");
    let launch_agents = home_path.join("Library").join("LaunchAgents");

    // Create directories
    std::fs::create_dir_all(&daemon_dir)
        .map_err(|e| format!("Failed to create daemon dir: {}", e))?;
    std::fs::create_dir_all(&log_dir)
        .map_err(|e| format!("Failed to create log dir: {}", e))?;
    std::fs::create_dir_all(&launch_agents)
        .map_err(|e| format!("Failed to create LaunchAgents dir: {}", e))?;

    // Step 1: Deploy python-backend binary from app bundle → ~/.swarm-ai/daemon/
    let daemon_binary = daemon_dir.join("python-backend");
    // In a Tauri .app bundle: SwarmAI.app/Contents/MacOS/python-backend
    let bundle_base = app.path().resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;
    // resource_dir = .app/Contents/Resources/ → parent = Contents/ → join MacOS
    let app_binary = bundle_base.parent()
        .ok_or("No parent of resource dir")?
        .join("MacOS")
        .join("python-backend");

    if app_binary.exists() {
        // Only copy if source is newer (or dest doesn't exist)
        let should_copy = if daemon_binary.exists() {
            let src_mtime = std::fs::metadata(&app_binary)
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
            let dst_mtime = std::fs::metadata(&daemon_binary)
                .and_then(|m| m.modified())
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
            src_mtime > dst_mtime
        } else {
            true
        };

        if should_copy {
            // Atomic copy: write .tmp then rename
            let tmp = daemon_binary.with_extension("tmp");
            std::fs::copy(&app_binary, &tmp)
                .map_err(|e| format!("Failed to copy binary: {}", e))?;
            std::fs::rename(&tmp, &daemon_binary)
                .map_err(|e| format!("Failed to rename binary: {}", e))?;

            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&daemon_binary, std::fs::Permissions::from_mode(0o755))
                    .map_err(|e| format!("Failed to chmod binary: {}", e))?;
            }
            println!("[Tauri] Deployed daemon binary from {:?}", app_binary);
        }
    } else {
        // Dev mode: binary not in app bundle. Check if previously deployed.
        if !daemon_binary.exists() {
            return Err(format!(
                "Backend binary not found in app bundle ({:?}) or daemon dir ({:?}). \
                 Run `./prod.sh build` to create it.",
                app_binary, daemon_binary
            ));
        }
        println!("[Tauri] Using previously deployed daemon binary");
    }

    // Write version file alongside binary — format: "{semver} {git_hash} {timestamp}"
    let version = app.config().version.clone().unwrap_or_default();
    if !version.is_empty() {
        let timestamp = std::process::Command::new("date")
            .arg("+%Y-%m-%d %H:%M:%S")
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_else(|_| "unknown".to_string());
        let _ = std::fs::write(
            daemon_dir.join(".version"),
            format!("{} release {}", version, timestamp),
        );
    }

    // Step 2: Deploy wrapper script from bundled resources → ~/.swarm-ai/swarmai_backend.sh
    let wrapper_dest = home_path.join(".swarm-ai").join("swarmai_backend.sh");
    let wrapper_src = bundle_base.join("daemon").join("swarmai_backend.sh");

    if wrapper_src.exists() {
        std::fs::copy(&wrapper_src, &wrapper_dest)
            .map_err(|e| format!("Failed to copy wrapper: {}", e))?;
    } else if !wrapper_dest.exists() {
        return Err(format!(
            "Wrapper script not found in bundle ({:?}) or at dest ({:?})",
            wrapper_src, wrapper_dest
        ));
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&wrapper_dest, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("Failed to chmod wrapper: {}", e))?;
    }

    // Clear quarantine (macOS Gatekeeper blocks quarantined scripts via launchd)
    let _ = std::process::Command::new("xattr")
        .args(["-d", "com.apple.quarantine"])
        .arg(&wrapper_dest)
        .output();
    let _ = std::process::Command::new("xattr")
        .args(["-d", "com.apple.quarantine"])
        .arg(&daemon_binary)
        .output();

    // Step 3+4: Generate plist from template and write
    let plist_template_path = bundle_base.join("daemon").join("com.swarmai.backend.plist.template");
    let plist_dest = launch_agents.join("com.swarmai.backend.plist");

    if plist_template_path.exists() {
        let plist_content = std::fs::read_to_string(&plist_template_path)
            .map_err(|e| format!("Failed to read plist template: {}", e))?;

        let plist_content = plist_content
            .replace("__WRAPPER_PATH__", wrapper_dest.to_str().unwrap_or(""))
            .replace("__LOG_DIR__", log_dir.to_str().unwrap_or(""))
            .replace("__HOME__", &home);

        std::fs::write(&plist_dest, &plist_content)
            .map_err(|e| format!("Failed to write plist: {}", e))?;
        println!("[Tauri] Installed daemon plist: {:?}", plist_dest);
    } else if !plist_dest.exists() {
        return Err(format!(
            "Plist template not found ({:?}) and no existing plist",
            plist_template_path
        ));
    }

    // Step 5: Bootstrap via launchctl
    bootstrap_daemon(&home)
}

/// Bootstrap the daemon via launchctl. Idempotent.
fn bootstrap_daemon(home: &str) -> Result<(), String> {
    let plist_path = format!("{}/{}", home, DAEMON_PLIST_RELPATH);

    if !std::path::Path::new(&plist_path).exists() {
        return Err(format!("Daemon plist not found: {}", plist_path));
    }

    // Get UID via id -u
    let uid_output = std::process::Command::new("id")
        .arg("-u")
        .output()
        .map_err(|e| format!("Failed to get UID: {}", e))?;
    let uid = String::from_utf8_lossy(&uid_output.stdout).trim().to_string();
    let gui_target = format!("gui/{}", uid);

    let output = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, &plist_path])
        .output()
        .map_err(|e| format!("Failed to run launchctl: {}", e))?;

    // launchctl exit codes:
    //   0  = bootstrapped successfully
    //   5  = I/O error (service already loaded, common on macOS Ventura+)
    //   37 = already bootstrapped (idempotent, not an error)
    match output.status.code() {
        Some(0) => {
            println!("[Tauri] Daemon bootstrapped successfully");
            Ok(())
        }
        Some(5) | Some(37) => {
            let code = output.status.code().unwrap_or(-1);
            let verify = std::process::Command::new("launchctl")
                .args(["list", "com.swarmai.backend"])
                .output();
            match verify {
                Ok(v) if v.status.success() => {
                    println!("[Tauri] Daemon already loaded (code {}, verified via launchctl list)", code);
                    Ok(())
                }
                _ => {
                    let stderr = String::from_utf8_lossy(&output.stderr);
                    Err(format!(
                        "launchctl bootstrap returned code {} but service not found in launchctl list: {}",
                        code, stderr
                    ))
                }
            }
        }
        Some(code) => {
            let stderr = String::from_utf8_lossy(&output.stderr);
            Err(format!("launchctl bootstrap failed (code {}): {}", code, stderr))
        }
        None => Err("launchctl killed by signal".to_string()),
    }
}

/// Background health watchdog for daemon mode.
///
/// When Tauri connects to an external daemon (not a subprocess it owns),
/// there's no process monitor. This task polls the daemon health endpoint
/// every `interval_secs` and emits frontend events on state changes:
///   - `backend-terminated-restarting` when daemon becomes unreachable (launchd will restart)
///   - `backend-restarted` when daemon recovers
///   - `backend-terminated` only after MAX_RECOVERY_ATTEMPTS failed (permanent death)
///
/// **boot_id detection:** The daemon returns a `boot_id` in `/health` that
/// changes on every process restart. If the poll interval misses a brief
/// outage (daemon restarts in <10s), boot_id change still triggers
/// `backend-restarted` so the frontend can refresh connections.
///
/// During recovery, polls every 3s instead of the normal interval to minimize downtime.
fn spawn_daemon_health_watchdog(
    app_handle: tauri::AppHandle,
    state: SharedBackendState,
    interval_secs: u64,
) {
    const MAX_RECOVERY_ATTEMPTS: u32 = 20; // 20 × 3s = 60s max wait for launchd restart
    const RECOVERY_POLL_SECS: u64 = 3;

    tauri::async_runtime::spawn(async move {
        let client = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(3))
            .build()
        {
            Ok(c) => c,
            Err(_) => return,
        };
        let health_url = format!("http://127.0.0.1:{}/health", DAEMON_PORT);
        let mut was_healthy = true;
        let mut recovery_attempts: u32 = 0;
        let mut known_boot_id: Option<String> = None;

        loop {
            // Adaptive interval: 30s when healthy (saves battery), 3s during recovery.
            // The caller's interval_secs is used as a minimum — we use max(interval, 30)
            // to ensure battery-friendly polling in steady state.
            let sleep_secs = if was_healthy { std::cmp::max(interval_secs, 30) } else { RECOVERY_POLL_SECS };
            tokio::time::sleep(tokio::time::Duration::from_secs(sleep_secs)).await;

            // Check if backend was intentionally stopped (user or app close)
            {
                let backend = state.lock().await;
                if !backend.running || backend.intentional_shutdown {
                    println!("[Tauri] Health watchdog: backend stopped intentionally — exiting");
                    return;
                }
            }

            let (healthy, current_boot_id) = match client.get(&health_url).send().await {
                Ok(resp) => {
                    if let Ok(body) = resp.text().await {
                        let (is_healthy, _, bid) = parse_health_response(&body);
                        (is_healthy, bid)
                    } else {
                        (false, None)
                    }
                }
                Err(_) => (false, None),
            };

            // boot_id change detection: daemon restarted silently (too fast for poll gap)
            if was_healthy && healthy {
                if let Some(ref bid) = current_boot_id {
                    match &known_boot_id {
                        Some(old_bid) if old_bid != bid => {
                            println!(
                                "[Tauri] Daemon watchdog: boot_id changed ({} → {}) — daemon restarted silently",
                                old_bid, bid
                            );
                            known_boot_id = Some(bid.clone());
                            let _ = app_handle.emit("backend-restarted", DAEMON_PORT);
                            continue; // Skip normal health transition logic
                        }
                        None => {
                            // First time seeing boot_id — record it
                            known_boot_id = Some(bid.clone());
                        }
                        _ => {} // Same boot_id — no restart
                    }
                }
            }

            if was_healthy && !healthy {
                // Daemon just went down — signal "restarting" (launchd will handle it)
                recovery_attempts = 1;
                println!("[Tauri] Daemon watchdog: daemon unreachable — restarting via launchd (attempt {}/{})",
                    recovery_attempts, MAX_RECOVERY_ATTEMPTS);
                let _ = app_handle.emit("backend-terminated-restarting", Option::<i32>::None);
            } else if !was_healthy && !healthy {
                // Still down — increment recovery counter
                recovery_attempts += 1;
                println!("[Tauri] Daemon watchdog: still waiting for daemon recovery (attempt {}/{})",
                    recovery_attempts, MAX_RECOVERY_ATTEMPTS);

                if recovery_attempts >= MAX_RECOVERY_ATTEMPTS {
                    // Give up — daemon is permanently dead
                    println!("[Tauri] Daemon watchdog: daemon failed to recover after {} attempts — permanent failure",
                        MAX_RECOVERY_ATTEMPTS);
                    let _ = app_handle.emit("backend-terminated", Option::<i32>::None);
                    // Keep watching in case it eventually comes back
                }
            } else if !was_healthy && healthy {
                // Daemon recovered (launchd KeepAlive restarted it)
                println!("[Tauri] Daemon watchdog: daemon recovered after {} attempts — emitting backend-restarted",
                    recovery_attempts);
                known_boot_id = current_boot_id; // Update to new boot_id
                let _ = app_handle.emit("backend-restarted", DAEMON_PORT);
                recovery_attempts = 0;
            }

            was_healthy = healthy;
        }
    });
}

// Start the backend — platform-specific architecture.
//
// macOS: launchd daemon (24/7, survives app close)
//   1. Probe existing daemon → connect
//   2. auto_install_daemon → bootstrap → connect
//
// Windows/Linux: subprocess (dies with app)
//   1. Probe existing backend on port → connect
//   2. Spawn python-backend as child process → wait for health → connect
//
// Dev mode: frontend skips this entirely (isDev = true), connects to localhost:8000.
#[tauri::command]
async fn start_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, SharedBackendState>,
) -> Result<u16, String> {
    // Check if already running (short lock)
    {
        let backend = state.lock().await;
        if backend.running {
            return Ok(backend.port);
        }
    }

    // Helper: connect to daemon and start health watchdog
    let connect_daemon = |state: &tauri::State<'_, SharedBackendState>, app: &tauri::AppHandle| {
        let state_inner = state.inner().clone();
        let app_clone = app.clone();
        async move {
            {
                let mut backend = state_inner.lock().await;
                backend.port = DAEMON_PORT;
                backend.running = true;
                backend.is_daemon_mode = true;
                backend.pid = None;
            }
            spawn_daemon_health_watchdog(app_clone.clone(), state_inner, 10);
            let _ = app_clone.emit("backend-mode", "daemon");
            DAEMON_PORT
        }
    };

    // ── Step 1: Probe for existing backend on DAEMON_PORT ─────────────
    if let Some(_port) = probe_daemon_health(3, 1).await {
        println!("[Tauri] Found existing backend on port {} — connecting", DAEMON_PORT);

        // macOS only: background version reconciliation
        #[cfg(target_os = "macos")]
        {
            let app_version = app.config().version.clone().unwrap_or_default();
            if !app_version.is_empty() {
                let app_handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    sync_daemon_version_background(app_handle, app_version).await;
                });
            }
        }

        let port = connect_daemon(&state, &app).await;
        return Ok(port);
    }

    // ── Step 2: Platform-specific provisioning ────────────────────────

    #[cfg(target_os = "macos")]
    {
        // macOS: install launchd daemon and bootstrap
        println!("[Tauri] No daemon found — auto-installing");

        if let Err(e) = auto_install_daemon(&app) {
            return Err(format!(
                "Failed to install daemon: {}. \
                 Check ~/.swarm-ai/logs/backend-stderr.log for details.",
                e
            ));
        }

        // Wait for daemon to come up (cold start takes a few seconds)
        if let Some(_port) = probe_daemon_health(15, 2).await {
            println!("[Tauri] Daemon installed and healthy on port {}", DAEMON_PORT);
            let port = connect_daemon(&state, &app).await;
            return Ok(port);
        }

        return Err(format!(
            "Daemon installed but not responding on port {} after 30s. \
             Check logs: ~/.swarm-ai/logs/backend-stderr.log",
            DAEMON_PORT,
        ));
    }

    #[cfg(not(target_os = "macos"))]
    {
        // Windows/Linux: spawn backend as subprocess (owned by Tauri)
        println!("[Tauri] Spawning backend subprocess on port {}", DAEMON_PORT);

        let binary_path = find_backend_binary(&app)?;
        let enhanced_path = get_enhanced_path();

        let mut cmd = std::process::Command::new(&binary_path);
        cmd.args(["--host", "127.0.0.1", "--port", &DAEMON_PORT.to_string()])
            .env("PATH", &enhanced_path)
            .env("SWARMAI_MODE", "subprocess")
            .env("SWARMAI_PORT", DAEMON_PORT.to_string())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null());

        #[cfg(target_os = "windows")]
        {
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }

        let child = cmd.spawn()
            .map_err(|e| format!(
                "Failed to start backend: {}. Binary: {:?}",
                e, binary_path
            ))?;

        let child_pid = child.id();
        println!("[Tauri] Backend subprocess spawned (PID {})", child_pid);

        // Update state with subprocess info
        {
            let mut backend = state.lock().await;
            backend.pid = Some(child_pid);
            backend.is_daemon_mode = false;
        }

        // Wait for health endpoint
        if let Some(_port) = probe_daemon_health(20, 1).await {
            println!("[Tauri] Backend subprocess healthy on port {}", DAEMON_PORT);
            {
                let mut backend = state.lock().await;
                backend.port = DAEMON_PORT;
                backend.running = true;
            }
            // Start health watchdog for subprocess crash detection
            spawn_daemon_health_watchdog(app.clone(), state.inner().clone(), 10);
            let _ = app.emit("backend-mode", "subprocess");
            return Ok(DAEMON_PORT);
        }

        // Backend didn't come up — kill the zombie and report error
        kill_process_tree(child_pid);
        Err(format!(
            "Backend started (PID {}) but not responding on port {} after 20s. \
             Check if port is in use or binary is corrupt.",
            child_pid, DAEMON_PORT,
        ))
    }
}

/// Find the backend binary — checks app bundle first, then data dir.
/// Works cross-platform: macOS bundle (Contents/MacOS/), Windows (same dir as exe).
#[allow(dead_code)] // Used on Windows/Linux only; macOS uses auto_install_daemon path.
fn find_backend_binary(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let exe_path = std::env::current_exe()
        .map_err(|e| format!("Cannot determine exe path: {}", e))?;
    let exe_dir = exe_path.parent().ok_or("Cannot determine exe directory")?;

    // Check adjacent to the executable (Tauri bundles externalBin here)
    #[cfg(target_os = "windows")]
    let binary_name = "python-backend.exe";
    #[cfg(not(target_os = "windows"))]
    let binary_name = "python-backend";

    let adjacent = exe_dir.join(binary_name);
    if adjacent.exists() {
        return Ok(adjacent);
    }

    // macOS: check Contents/MacOS/ inside .app bundle
    #[cfg(target_os = "macos")]
    {
        let bundle_dir = app.path().resource_dir()
            .map_err(|e| format!("Resource dir error: {}", e))?;
        let macos_binary = bundle_dir.parent()
            .ok_or("No parent of resource dir")?
            .join("MacOS")
            .join(binary_name);
        if macos_binary.exists() {
            return Ok(macos_binary);
        }
    }

    // Check data directory (~/.swarm-ai/daemon/)
    #[cfg(target_os = "windows")]
    let home_str = std::env::var("USERPROFILE").unwrap_or_default();
    #[cfg(not(target_os = "windows"))]
    let home_str = std::env::var("HOME").unwrap_or_default();
    if home_str.is_empty() {
        return Err(format!(
            "Backend binary '{}' not found adjacent to exe ({:?}) and HOME not set.",
            binary_name, adjacent
        ));
    }
    let home = std::path::PathBuf::from(&home_str);
    let data_binary = home.join(".swarm-ai").join("daemon").join(binary_name);
    if data_binary.exists() {
        return Ok(data_binary);
    }

    // Suppress unused variable warning on non-macOS
    let _ = app;

    Err(format!(
        "Backend binary '{}' not found. Checked:\n  - {:?}\n  - ~/.swarm-ai/daemon/{}",
        binary_name, adjacent, binary_name
    ))
}

// Stop the backend.
// - Daemon mode (macOS): just disconnect — daemon keeps running for 24/7 operation.
// - Subprocess mode (Windows): kill the owned process.
#[tauri::command]
async fn stop_backend(state: tauri::State<'_, SharedBackendState>) -> Result<(), String> {
    let mut backend = state.lock().await;
    let is_daemon = backend.is_daemon_mode;
    let port = backend.port;
    let pid = backend.pid;

    backend.intentional_shutdown = true;
    backend.running = false;
    backend.pid = None;
    drop(backend);

    if !is_daemon {
        // Subprocess mode: graceful shutdown then kill
        send_shutdown_request(port);
        std::thread::sleep(std::time::Duration::from_secs(2));
        if let Some(pid) = pid {
            kill_process_tree(pid);
        }
    }
    Ok(())
}

// Get backend status
#[tauri::command]
async fn get_backend_status(state: tauri::State<'_, SharedBackendState>) -> Result<BackendStatus, String> {
    let backend = state.lock().await;
    Ok(BackendStatus {
        running: backend.running,
        port: backend.port,
        is_daemon_mode: backend.is_daemon_mode,
    })
}

// Copy text to system clipboard using OS-native tools.
// Tauri webview doesn't grant navigator.clipboard permissions, so we bypass
// via pbcopy (macOS), xclip/xsel (Linux), or PowerShell (Windows).
#[tauri::command]
async fn copy_to_clipboard(text: String) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        use std::io::Write;
        let mut child = std::process::Command::new("pbcopy")
            .stdin(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to spawn pbcopy: {}", e))?;
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(text.as_bytes())
                .map_err(|e| format!("Failed to write to pbcopy: {}", e))?;
        }
        child.wait().map_err(|e| format!("pbcopy failed: {}", e))?;
        Ok(())
    }

    #[cfg(target_os = "linux")]
    {
        use std::io::Write;
        // Try xclip first, fall back to xsel
        let result = std::process::Command::new("xclip")
            .args(["-selection", "clipboard"])
            .stdin(std::process::Stdio::piped())
            .spawn();
        let mut child = match result {
            Ok(c) => c,
            Err(_) => std::process::Command::new("xsel")
                .args(["--clipboard", "--input"])
                .stdin(std::process::Stdio::piped())
                .spawn()
                .map_err(|e| format!("Neither xclip nor xsel available: {}", e))?,
        };
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(text.as_bytes())
                .map_err(|e| format!("Failed to write to clipboard tool: {}", e))?;
        }
        child.wait().map_err(|e| format!("Clipboard tool failed: {}", e))?;
        Ok(())
    }

    #[cfg(target_os = "windows")]
    {
        // Use PowerShell Set-Clipboard via stdin pipe to avoid injection.
        // Passing text as a command-line argument is unsafe because
        // PowerShell metacharacters ($, `, etc.) can execute arbitrary code.
        use std::io::Write;
        let mut child = std::process::Command::new("powershell")
            .args(["-NoProfile", "-Command", "$input | Set-Clipboard"])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn()
            .map_err(|e| format!("Failed to spawn powershell: {}", e))?;
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(text.as_bytes())
                .map_err(|e| format!("Failed to write to powershell stdin: {}", e))?;
        }
        child.wait().map_err(|e| format!("PowerShell Set-Clipboard failed: {}", e))?;
        Ok(())
    }
}

// Get backend port
#[tauri::command]
async fn get_backend_port(state: tauri::State<'_, SharedBackendState>) -> Result<u16, String> {
    let backend = state.lock().await;
    Ok(backend.port)
}

// Check Node.js version
#[tauri::command]
async fn check_nodejs_version() -> Result<String, String> {
    // Try direct execution with enhanced PATH first (works on all platforms)
    let enhanced_path = get_enhanced_path();

    #[cfg(target_os = "windows")]
    let node_cmd = "node.exe";

    #[cfg(not(target_os = "windows"))]
    let node_cmd = "node";

    let output = std::process::Command::new(node_cmd)
        .arg("--version")
        .env("PATH", &enhanced_path)
        .output();

    match output {
        Ok(output) if output.status.success() => {
            let version = String::from_utf8_lossy(&output.stdout)
                .trim()
                .to_string();
            return Ok(version);
        }
        _ => {}
    }

    // On Unix systems, try using user's shell as fallback (for nvm, volta, etc.)
    #[cfg(not(target_os = "windows"))]
    {
        let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());

        let output = std::process::Command::new(&shell)
            .arg("-l")  // Login shell to source profile
            .arg("-c")  // Execute command
            .arg("node --version")
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .to_string();
                return Ok(version);
            }
        }
    }

    // On Windows, try PowerShell as fallback
    #[cfg(target_os = "windows")]
    {
        let output = std::process::Command::new("powershell")
            .args(["-NoProfile", "-Command", "node --version"])
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .to_string();
                return Ok(version);
            }
        }
    }

    Err("Node.js is not installed or not in PATH".to_string())
}

// Check Git Bash path (Windows only)
// Returns the path if CLAUDE_CODE_GIT_BASH_PATH is set and the file exists,
// or tries to auto-detect Git Bash in common locations
#[tauri::command]
async fn check_git_bash_path() -> Result<String, String> {
    // Only relevant on Windows
    #[cfg(not(target_os = "windows"))]
    {
        return Err("Not applicable on this platform".to_string());
    }

    #[cfg(target_os = "windows")]
    {
        // First check if CLAUDE_CODE_GIT_BASH_PATH is set
        if let Ok(git_bash_path) = env::var("CLAUDE_CODE_GIT_BASH_PATH") {
            if std::path::Path::new(&git_bash_path).exists() {
                return Ok(git_bash_path);
            }
        }

        // Try to auto-detect Git Bash in common locations
        let common_paths = vec![
            // Default Git for Windows installation paths
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ];

        // Also check LOCALAPPDATA and ProgramFiles
        if let Ok(localappdata) = env::var("LOCALAPPDATA") {
            let path = format!(r"{}\Programs\Git\bin\bash.exe", localappdata);
            if std::path::Path::new(&path).exists() {
                return Ok(path);
            }
        }

        if let Ok(programfiles) = env::var("ProgramFiles") {
            let path = format!(r"{}\Git\bin\bash.exe", programfiles);
            if std::path::Path::new(&path).exists() {
                return Ok(path);
            }
        }

        for path in common_paths {
            if std::path::Path::new(path).exists() {
                return Ok(path.to_string());
            }
        }

        Err("Git Bash not found".to_string())
    }
}

// Check Python version
#[tauri::command]
async fn check_python_version() -> Result<String, String> {
    let enhanced_path = get_enhanced_path();

    // Windows uses python.exe, Unix uses python3 or python
    #[cfg(target_os = "windows")]
    let python_commands = vec!["python.exe", "python3.exe", "py.exe"];

    #[cfg(not(target_os = "windows"))]
    let python_commands = vec!["python3", "python"];

    // Try each Python command with enhanced PATH
    for cmd in &python_commands {
        let output = std::process::Command::new(cmd)
            .arg("--version")
            .env("PATH", &enhanced_path)
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                // Python 2.x writes version to stderr, Python 3.x to stdout
                let version_str = if !output.stdout.is_empty() {
                    String::from_utf8_lossy(&output.stdout)
                } else {
                    String::from_utf8_lossy(&output.stderr)
                };

                let version = version_str.trim().to_string();
                if !version.is_empty() {
                    return Ok(version);
                }
            }
        }
    }

    // On Unix systems, try using user's shell as fallback (for pyenv, etc.)
    #[cfg(not(target_os = "windows"))]
    {
        let home = env::var("HOME").unwrap_or_default();
        let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());

        let output = std::process::Command::new(&shell)
            .arg("-l")  // Login shell to source profile
            .arg("-c")  // Execute command
            .arg("python3 --version 2>&1 || python --version 2>&1")
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .to_string();
                if !version.is_empty() {
                    return Ok(version);
                }
            }
        }

        // Try pyenv directly if available
        let pyenv_path = format!("{}/.pyenv/shims/python3", home);
        if std::path::Path::new(&pyenv_path).exists() {
            if let Ok(output) = std::process::Command::new(&pyenv_path)
                .arg("--version")
                .output() {
                if output.status.success() {
                    let version = String::from_utf8_lossy(&output.stdout)
                        .trim()
                        .to_string();
                    return Ok(version);
                }
            }
        }
    }

    // On Windows, try PowerShell as fallback
    #[cfg(target_os = "windows")]
    {
        let output = std::process::Command::new("powershell")
            .args(["-NoProfile", "-Command", "python --version"])
            .output();

        if let Ok(output) = output {
            if output.status.success() {
                let version_str = if !output.stdout.is_empty() {
                    String::from_utf8_lossy(&output.stdout)
                } else {
                    String::from_utf8_lossy(&output.stderr)
                };
                let version = version_str.trim().to_string();
                if !version.is_empty() {
                    return Ok(version);
                }
            }
        }
    }

    Err("Python is not installed or not in PATH".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init());

    // Add desktop-only plugins
    #[cfg(desktop)]
    {
        builder = builder
            .plugin(tauri_plugin_process::init())
            .plugin(tauri_plugin_updater::Builder::new().build());
    }

    builder
        .manage(Arc::new(Mutex::new(BackendState::default())))
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            get_backend_port,
            copy_to_clipboard,
            check_nodejs_version,
            check_python_version,
            check_git_bash_path,
        ])
        .setup(|app| {
            // Backend will be started by frontend via initializeBackend()
            // This allows proper error handling in the UI

            // Create main window programmatically (removed from tauri.conf.json) so we
            // can attach on_navigation to block external URL navigation.
            // Without this, clicking http(s) links in chat navigates the webview away
            // from the React app, causing a fullscreen loading state with no way to
            // return (no back button, no close — app is bricked until restart).
            {
                use tauri_plugin_opener::OpenerExt;
                let handle = app.handle().clone();

                let url = {
                    #[cfg(debug_assertions)]
                    { WebviewUrl::External("http://localhost:1420".parse().unwrap()) }
                    #[cfg(not(debug_assertions))]
                    { WebviewUrl::default() }
                };

                let mut builder = WebviewWindowBuilder::new(app, "main", url)
                    .title("SwarmAI")
                    .inner_size(1400.0, 900.0)
                    .min_inner_size(1024.0, 768.0)
                    .resizable(true)
                    .fullscreen(false)
                    .center()
                    .zoom_hotkeys_enabled(false);

                // title_bar_style and hidden_title are macOS-only APIs
                #[cfg(target_os = "macos")]
                {
                    builder = builder
                        .title_bar_style(tauri::TitleBarStyle::Overlay)
                        .hidden_title(true);
                }

                let _window = builder
                    .on_navigation(move |url: &tauri::Url| {
                        match url.scheme() {
                            "tauri" | "asset" => true,
                            "http" | "https" => {
                                let host = url.host_str().unwrap_or("");
                                if host == "localhost" || host == "tauri.localhost" {
                                    true
                                } else {
                                    // External URL — open in system browser, block webview nav
                                    let _ = handle.opener().open_url(url.as_str(), None::<&str>);
                                    false
                                }
                            }
                            _ => false, // Block unknown schemes (javascript:, data:, blob:)
                        }
                    })
                    .build()?;
            }

            // Open DevTools automatically in debug builds or when SWARMAI_DEBUG is set
            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            // Also check for SWARMAI_DEBUG env var to enable in release builds
            #[cfg(not(debug_assertions))]
            {
                if std::env::var("SWARMAI_DEBUG").is_ok() {
                    if let Some(window) = app.get_webview_window("main") {
                        window.open_devtools();
                    }
                }
            }

            // Set up window close handler for cleanup (especially important on Windows)
            if let Some(window) = app.get_webview_window("main") {
                let app_handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::Destroyed = event {
                        // Best-effort: emit before block_on freezes event loop
                        let _ = app_handle.emit("shutdown-started", ());
                        // Graceful shutdown: send POST /shutdown, wait, then force-kill
                        let state = app_handle.state::<SharedBackendState>();
                        graceful_shutdown_and_kill(state.inner().clone(), "window_destroy");
                    }
                });
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            match event {
                tauri::RunEvent::Exit => {
                    // Best-effort: emit before block_on freezes event loop
                    let _ = app_handle.emit("shutdown-started", ());
                    // Graceful shutdown: send POST /shutdown, wait, then force-kill
                    let state = app_handle.state::<SharedBackendState>();
                    graceful_shutdown_and_kill(state.inner().clone(), "exit");
                }
                tauri::RunEvent::ExitRequested { api, .. } => {
                    // Don't prevent exit, but ensure cleanup
                    let _ = api; // Allow default exit behavior

                    // Best-effort: emit before block_on freezes event loop
                    let _ = app_handle.emit("shutdown-started", ());
                    // Graceful shutdown: send POST /shutdown, wait, then force-kill
                    let state = app_handle.state::<SharedBackendState>();
                    graceful_shutdown_and_kill(state.inner().clone(), "exit_requested");
                }
                _ => {}
            }
        });
}
