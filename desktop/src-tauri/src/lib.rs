use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;

/// Integrated-terminal PTY commands (app-level, not a plugin). See terminal.rs.
mod terminal;
mod pty_pump;
use terminal::TerminalState;

/// One-tap screen capture command (app-level, not a plugin). See screen_capture.rs.
mod screen_capture;
mod runtime_deploy;
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

/// Freshness window for the backend liveness heartbeat file. If the file's
/// mtime is older than this, the writer thread has stopped → the process is
/// gone (or the binary predates the heartbeat feature). 10s = 10 missed 1s
/// ticks, comfortably past any GC/scheduling jitter on the writer thread.
const HEARTBEAT_STALE_SECS: u64 = 10;
/// loop_age at which the asyncio loop is considered genuinely WEDGED (permanent
/// deadlock), not merely busy. Above this, a fresh heartbeat no longer protects
/// the daemon from a terminated decision — a real wedge must still surface.
/// 15s ≫ any legitimate synchronous burst (context assembly, SSE serialization)
/// but well under a human's "is it dead?" patience.
const HEARTBEAT_WEDGE_SECS: f64 = 15.0;

/// The heartbeat as the watchdog uses it: freshness and wedge as above, plus the
/// loop's age and the backend's own stall diagnosis (`stall`, core/stall_diagnosis.py:
/// cause cpu / memory / low_battery / busy / unknown, `starved`, evidence), which the
/// UI shows as the reason. Same read rules: missing or stale → not fresh, nothing else.
#[derive(Debug, Clone, Default)]
struct HeartbeatView {
    fresh: bool,
    loop_wedged: bool,
    loop_age: Option<f64>,
    stall: Option<serde_json::Value>,
    /// The pid that wrote the file, read even when the file is stale.
    pid: Option<u32>,
}

impl HeartbeatView {
    /// The loop is stalled by an outside shortage (CPU / memory / low battery), not
    /// stuck in our code: the process is alive and will catch up.
    fn starved(&self) -> bool {
        self.stall
            .as_ref()
            .and_then(|s| s.get("starved"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }
}

/// Read the backend liveness heartbeat (`~/.swarm-ai/heartbeat`) written by the
/// independent-thread writer in `core/heartbeat.py`:
///
/// - `heartbeat_fresh` — the file exists and its mtime is within
///   `HEARTBEAT_STALE_SECS`. TRUE = the writer thread is ticking = the PROCESS
///   is alive (the thread writes even when the asyncio loop is CPU/GIL-bound,
///   because sleep + write release the GIL). A missing/stale file (old binary,
///   or a dead process whose writer stopped) → FALSE → the caller falls back to
///   the pre-existing pid+streak death logic (zero regression).
/// - `loop_wedged` — only meaningful when fresh; TRUE when the file's `loop_age`
///   (seconds since the event loop last ticked) ≥ `HEARTBEAT_WEDGE_SECS`. This
///   distinguishes a *busy* loop (small loop_age → not wedged → protect from
///   death) from a *genuinely deadlocked* loop (large loop_age → let it die).
///
/// Freshness uses FILE MTIME (filesystem truth, robust to in-file clock skew and
/// naturally goes stale when the writing process dies); wedge uses the parsed
/// `loop_age` field. Any read/parse failure → a default (not fresh) view = "can't prove
/// alive" → safe fallback to the legacy path. Pure file I/O, compiles on all
/// platforms (only consulted in daemon mode).
fn read_heartbeat_full() -> HeartbeatView {
    let home = std::env::var("HOME").unwrap_or_default();
    let path = std::path::Path::new(&home).join(".swarm-ai").join("heartbeat");

    let metadata = match std::fs::metadata(&path) {
        Ok(m) => m,
        Err(_) => return HeartbeatView::default(), // missing → not fresh → legacy fallback
    };
    let fresh = match metadata.modified() {
        Ok(mtime) => match mtime.elapsed() {
            // elapsed() Ok → mtime in the past; fresh if within the window.
            Ok(age) => age.as_secs() < HEARTBEAT_STALE_SECS,
            // Err → mtime is in the FUTURE (clock skew) → treat as fresh (the
            // file was just written; a future mtime can't mean "stale").
            Err(_) => true,
        },
        Err(_) => false,
    };
    if !fresh {
        // Stale: only the writer's pid is kept — it tells a SUSPENDED process (the
        // same pid launchd still runs, e.g. macOS at 3% battery) from a dead one.
        let pid = std::fs::read_to_string(&path)
            .ok()
            .and_then(|body| serde_json::from_str::<serde_json::Value>(&body).ok())
            .and_then(|j| j.get("pid").and_then(|v| v.as_u64()))
            .map(|p| p as u32);
        return HeartbeatView { pid, ..HeartbeatView::default() };
    }
    // Fresh → parse loop_age to decide wedged-vs-busy. A parse failure leaves
    // loop_wedged=false (busy, protected) — the conservative choice: a readable-
    // but-unparseable fresh file still proves the process is writing.
    let json = std::fs::read_to_string(&path)
        .ok()
        .and_then(|body| serde_json::from_str::<serde_json::Value>(&body).ok());
    let loop_age = json.as_ref().and_then(|j| j.get("loop_age")).and_then(|v| v.as_f64());
    HeartbeatView {
        fresh: true,
        loop_wedged: loop_age.map(|age| age >= HEARTBEAT_WEDGE_SECS).unwrap_or(false),
        loop_age,
        stall: json.as_ref().and_then(|j| j.get("stall")).cloned(),
        pid: json.as_ref().and_then(|j| j.get("pid")).and_then(|v| v.as_u64()).map(|p| p as u32),
    }
}

/// The daemon process is SUSPENDED, not dead: its heartbeat went stale but launchd
/// still runs the very pid that wrote it. The heartbeat thread is frozen with the
/// rest of the process (10-21: a 322s stall at 3% battery was declared dead and
/// the UI cut the connection). A different launchd pid is a restart, not this.
fn suspended(hb: &HeartbeatView, launchd_pid: Option<u32>) -> bool {
    !hb.fresh && hb.pid.is_some() && hb.pid == launchd_pid
}

/// How long a process may look suspended while the Mac is AWAKE before it is
/// treated as stuck. A real deadlock looks the same as a suspension (a C call
/// holding the GIL freezes the heartbeat thread, the pid stays), so "suspended"
/// cannot protect it forever. Measured with `Instant`, which on macOS does not
/// advance while the Mac sleeps; the 10-21 low-battery suspension lasted 322s.
const SUSPEND_MAX_SECS: u64 = 600;

/// The process looks suspended (`suspended`) and has for less than
/// SUSPEND_MAX_SECS of awake time. *suspended_for*: how long it has looked so
/// (None: just now).
fn suspended_within_cap(hb: &HeartbeatView, launchd_pid: Option<u32>,
                        suspended_for: Option<std::time::Duration>) -> bool {
    suspended(hb, launchd_pid)
        && suspended_for.map_or(true, |d| d.as_secs() < SUSPEND_MAX_SECS)
}

/// (heartbeat_fresh, loop_wedged) for the down-decision: a suspended process
/// reads as alive-but-busy (Degraded) until SUSPEND_MAX_SECS; then the old
/// path applies (stale heartbeat → escalates to Terminated).
fn decision_inputs(hb: &HeartbeatView, launchd_pid: Option<u32>,
                   suspended_for: Option<std::time::Duration>) -> (bool, bool) {
    if suspended_within_cap(hb, launchd_pid, suspended_for) {
        (true, false)
    } else {
        (hb.fresh, effective_wedged(hb))
    }
}

/// Track since when the process looks suspended (reset as soon as it does not)
/// and return for how long.
fn track_suspension(since: &mut Option<std::time::Instant>, hb: &HeartbeatView,
                    launchd_pid: Option<u32>) -> Option<std::time::Duration> {
    if suspended(hb, launchd_pid) {
        Some(since.get_or_insert_with(std::time::Instant::now).elapsed())
    } else {
        *since = None;
        None
    }
}

// ── Watchdog decision log (~/.swarm-ai/logs/watchdog.log) ──────────────────
// The watchdog's println! output goes nowhere in the bundled app, so after an
// offline/busy episode nobody could tell what it saw and decided. Every decision
// line also goes to this file, with the evidence (pid, heartbeat, loop age, the
// backend's stall diagnosis). Bounded: at WATCHDOG_LOG_MAX_BYTES it is moved to
// watchdog.log.1 (one generation kept). Best-effort: a failed write is ignored.
const WATCHDOG_LOG_MAX_BYTES: u64 = 2 * 1024 * 1024;

/// UTC `YYYY-MM-DDTHH:MM:SS.mmmZ` from a Unix time in milliseconds (no date crate).
fn utc_timestamp(ms: u128) -> String {
    let secs = (ms / 1000) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + if m <= 2 { 1 } else { 0 };
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z", y, m, d, rem / 3600, (rem % 3600) / 60, rem % 60, ms % 1000)
}

fn watchdog_log_path() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_default();
    std::path::Path::new(&home).join(".swarm-ai").join("logs").join("watchdog.log")
}

/// Print a watchdog line and append it, timestamped, to the watchdog log.
fn watchdog_log(line: &str) {
    println!("[Tauri] {}", line);
    let _ = append_watchdog_log(&watchdog_log_path(), line);
}

fn append_watchdog_log(path: &std::path::Path, line: &str) -> std::io::Result<()> {
    let path = path.to_path_buf();
    (|| -> std::io::Result<()> {
        use std::io::Write;
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        if std::fs::metadata(&path).map(|m| m.len() >= WATCHDOG_LOG_MAX_BYTES).unwrap_or(false) {
            let _ = std::fs::rename(&path, path.with_extension("log.1"));
        }
        let ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0);
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
        writeln!(f, "{} {}", utc_timestamp(ms), line)
    })()
}

/// The evidence behind a down-decision, as one log fragment.
fn watchdog_evidence(hb: &HeartbeatView, pid_present: Option<bool>) -> String {
    let mut out = format!(
        "pid_present={} hb_fresh={} loop_age={} wedged={}",
        pid_present.map(|b| b.to_string()).unwrap_or_else(|| "n/a".into()),
        hb.fresh,
        hb.loop_age.map(|a| format!("{:.1}s", a)).unwrap_or_else(|| "-".into()),
        hb.loop_wedged,
    );
    if let Some(stall) = &hb.stall {
        let g = |k: &str| stall.get(k).map(|v| v.to_string().trim_matches('"').to_string());
        out.push_str(&format!(" cause={} starved={}", g("cause").unwrap_or_else(|| "?".into()),
            g("starved").unwrap_or_else(|| "false".into())));
        for (k, name) in [("loadPerCore", "load"), ("memPercent", "mem%"), ("memPressure", "mem_pressure"),
                          ("battery", "battery%"), ("busyWith", "busy_with")] {
            if let Some(v) = g(k) {
                out.push_str(&format!(" {}={}", name, v));
            }
        }
        if let Some(top) = stall.get("topCpu").and_then(|v| v.as_array()) {
            let t: Vec<String> = top.iter().filter_map(|p| Some(format!("{}:{}%",
                p.get("name")?.as_str()?, p.get("cpu")?))).collect();
            if !t.is_empty() {
                out.push_str(&format!(" top_cpu={}", t.join(",")));
            }
        }
    }
    out
}

/// The stall cause of a heartbeat view ("-" without one): what makes a degraded
/// re-emit worth a log line.
fn stall_cause(hb: &HeartbeatView) -> String {
    hb.stall.as_ref().and_then(|s| s.get("cause")).and_then(|v| v.as_str()).unwrap_or("-").to_string()
}

/// The wedge flag the down-decision uses: a loop stalled ≥ HEARTBEAT_WEDGE_SECS by an
/// outside shortage (the CPU fully used, memory exhausted, low-battery throttling) is
/// a starved live process, not a deadlock — it stays Degraded ("slowed down"), never
/// "not responding". A wedge in our own code (or of unknown cause) still escalates.
fn effective_wedged(hb: &HeartbeatView) -> bool {
    hb.loop_wedged && !hb.starved()
}

/// The payload of `backend-degraded` / `backend-terminated-restarting`: what the
/// watchdog knows, so the UI can say why instead of a bare "offline".
fn health_event_payload(hb: &HeartbeatView, process_alive: Option<bool>,
                        launchd_pid: Option<u32>,
                        suspended_for: Option<std::time::Duration>) -> serde_json::Value {
    serde_json::json!({
        "port": DAEMON_PORT,
        "heartbeatFresh": hb.fresh,
        "loopAge": hb.loop_age,
        "processAlive": process_alive,
        // within SUSPEND_MAX_SECS: "paused"; past it the UI says "not running"
        "suspended": suspended_within_cap(hb, launchd_pid, suspended_for),
        "suspendedSecs": suspended_for.map(|d| d.as_secs()),
        "stall": hb.stall,
    })
}

/// Atomically install a file: copy `src` → `<dst>.<pid>.tmp-install`, chmod,
/// then rename to `dst`. The rename is atomic on a POSIX same-filesystem, so a
/// process already executing the old `dst` (e.g. a running guardian bash reading
/// its own script during an app upgrade) keeps its original inode and finishes
/// cleanly. Also avoids EACCES from re-copying over a read-only file left by a
/// prior install (files extracted from a code-signed .app bundle are 0444).
///
/// The tmp name includes the PID so two concurrent installs (e.g. the user
/// double-launches the .app) don't collide on a shared tmp path mid-copy.
///
/// NOT cfg-gated: uses only cross-platform stdlib (the PermissionsExt block is
/// `#[cfg(unix)]`). Mirrors the un-gated runtime copy (src/runtime_deploy.rs) — gating this but
/// not its un-gated caller `install_guardian` would break the Windows/Linux
/// build (caller references a non-existent fn). Dead code on non-macOS (the
/// only caller is reached solely from the macOS auto_install_daemon path).
#[allow(dead_code, unused_variables)]
fn atomic_install(src: &std::path::Path, dst: &std::path::Path, mode: u32) -> std::io::Result<()> {
    let tmp = dst.with_extension(format!("{}.tmp-install", std::process::id()));
    std::fs::copy(src, &tmp)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(mode))?;
    }
    let r = std::fs::rename(&tmp, dst); // atomic swap on same filesystem
    if r.is_err() {
        let _ = std::fs::remove_file(&tmp); // don't leak tmp on rename failure
    }
    r
}

/// Probe daemon health endpoint with retries.
/// Returns Some(port) if daemon is healthy, None otherwise.
/// Optionally emits progress events to the frontend via `app_handle`.
async fn probe_daemon_health(max_attempts: u32, interval_secs: u64) -> Option<u16> {
    probe_daemon_health_with_progress(max_attempts, interval_secs, None).await
}

// ── Adaptive first-launch / cold-start probe ────────────────────────────────
//
// Why this exists (run_e3dbc009): a fixed max_attempts×interval cap false-kills a
// slow-but-ALIVE backend on a new user's first launch. During the cold-start
// window (deploy ~500MB onedir + Python cold start + DB migration + workspace
// init) the daemon PROCESS is alive but has not yet bound the port, so /health
// returns connection-refused — indistinguishable, to the fixed-cap probe, from a
// dead daemon. The daemon's own {status:"initializing"} HTTP branch is
// unreachable here: uvicorn only serves AFTER lifespan startup completes
// (main.py:1049 sets _startup_complete=True before it yields), so during boot the
// only reachable signal is the OS process, not HTTP.
//
// The fix reads the real alive/dead discriminator — the launchd-managed daemon
// PROCESS — and keeps waiting while it is alive (bounded by an absolute ceiling as
// the O030 disaster-recovery backstop), failing FAST only when the process is
// genuinely gone. SLOW keeps waiting; a true HANG is bounded; a DEAD daemon fails
// fast.

// macOS-only: these tune the daemon cold-start adaptive probe, which exists only
// on the macOS launchd-daemon deployment (Win/Linux use the subprocess path).
/// Absolute ceiling for the adaptive cold-start wait (O030 disaster-recovery
/// backstop). Worst realistic first-launch cold start is ~35-70s; 300s gives
/// generous headroom while still bounding a genuine lifespan deadlock.
#[cfg(target_os = "macos")]
const COLD_START_CEILING_SECS: u64 = 300;
/// Consecutive no-process observations before declaring the daemon truly dead.
/// Must exceed the plist `ThrottleInterval` (10s) so a legitimate KeepAlive
/// respawn window (process momentarily absent) does not trip fail-fast. Set to 8
/// (× PROBE_INTERVAL_SECS(2) = 16s) rather than the bare minimum 6/12s, to leave
/// margin for launchctl's own latency in reporting the new pid after a respawn
/// (adversarial-review hardening: don't assume launchctl updates pid instantly).
#[cfg(target_os = "macos")]
const COLD_START_DEAD_STREAK: u32 = 8;
/// Poll cadence for the adaptive probe.
#[cfg(target_os = "macos")]
const PROBE_INTERVAL_SECS: u64 = 2;
/// Grace period for the OLD daemon to drain on a version-upgrade bootout before
/// we escalate to SIGKILL. bootout's SIGTERM triggers FastAPI lifespan shutdown
/// → `session_registry.disconnect_all()`, which fires end-of-conversation hooks
/// (DailyActivity flush, workspace auto-commit, distillation) and persists each
/// session's `--resume` identity. Those hooks on an ACTIVE session (a long agent
/// turn mid-tool-call, a large generation) can legitimately take longer than the
/// old hardcoded 15s — SIGKILL at 15s hard-cut a draining turn, skipping the
/// remaining hooks (A1 startup-hazard, run_2d3417d9). The poll loop breaks the
/// INSTANT the process self-exits, so this ceiling only ever bites a daemon that
/// is genuinely still draining; raising it costs nothing on the common fast-exit
/// path and gives an active session room to finish cleanly. NOT a full
/// pre-bootout active-session gate (that would defeat "the update eventually wins"
/// — Gate-0 verdict): update still wins, just with a humane drain window.
#[cfg(target_os = "macos")]
const DAEMON_UPGRADE_DRAIN_SECS: u32 = 45;

/// Outcome of a single health probe attempt, combining the HTTP result with
/// daemon process liveness. Pure classification — unit-tested.
/// macOS-only: consumed by `probe_daemon_health_adaptive` (the daemon cold-start
/// path exists only on macOS; Win/Linux use the subprocess spawn path).
#[cfg(target_os = "macos")]
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum ProbeOutcome {
    /// /health returned status=healthy — backend is up and serving.
    Ready,
    /// /health not healthy yet, but the daemon PROCESS is alive (still booting).
    Alive,
    /// /health not healthy AND no daemon process — genuinely down.
    Dead,
}

/// Pure classifier: given the HTTP health result and whether the daemon process
/// is alive, decide the probe outcome. `http_healthy` wins — a serving backend is
/// Ready regardless of anything else; otherwise process liveness distinguishes
/// "still booting" (Alive) from "dead".
#[cfg(target_os = "macos")]
fn classify_probe_outcome(http_healthy: bool, pid_present: bool) -> ProbeOutcome {
    if http_healthy {
        ProbeOutcome::Ready
    } else if pid_present {
        ProbeOutcome::Alive
    } else {
        ProbeOutcome::Dead
    }
}

/// Decision for the adaptive probe loop after one attempt. Pure — captures ALL
/// loop-termination logic so it is unit-testable without network/launchctl/sleep.
#[cfg(target_os = "macos")]
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum LoopDecision {
    /// Backend is serving — return Some(port).
    Succeed,
    /// Daemon process has been gone for >= dead-streak checks — fail fast.
    FailDead,
    /// Absolute ceiling reached while still not serving — bounded give-up.
    FailCeiling,
    /// Keep waiting (sleep one interval, probe again).
    Continue,
}

/// Pure loop-decision function. `consecutive_dead` is the number of consecutive
/// Dead outcomes INCLUDING the current attempt (caller increments before calling,
/// resets to 0 on any non-Dead outcome). `ever_alive` is true once the daemon
/// process has been observed alive at least once during this probe.
///
/// **Dead-streak fail-fast is GATED on `ever_alive`** (meta-review HIGH fix):
/// - "Was alive, now gone for N checks" (`ever_alive=true`) → a genuine crash /
///   respawn-loop → fail FAST (don't wait the full ceiling).
/// - "Never seen a pid yet" (`ever_alive=false`) → we are still inside the
///   launchd bootstrap→spawn window (on a disk-pressured first launch, launchctl
///   can report the registered service with NO `pid=` for >16s while the 500MB
///   onedir is still being paged in). Failing fast here would re-introduce the
///   EXACT false-fatal this change fixes. So a not-yet-started daemon keeps
///   waiting until the ceiling — the O030 backstop bounds a never-starts install.
///
/// Ordering note: an Alive process keeps waiting until the ceiling (SLOW is not a
/// failure; only a genuine hang is bounded by the ceiling).
#[cfg(target_os = "macos")]
fn probe_loop_decision(
    outcome: ProbeOutcome,
    elapsed_secs: u64,
    consecutive_dead: u32,
    ceiling_secs: u64,
    max_dead_streak: u32,
    ever_alive: bool,
) -> LoopDecision {
    match outcome {
        ProbeOutcome::Ready => LoopDecision::Succeed,
        ProbeOutcome::Dead => {
            if ever_alive && consecutive_dead >= max_dead_streak {
                // Was alive, now gone → real death. Fail fast.
                LoopDecision::FailDead
            } else if elapsed_secs >= ceiling_secs {
                // Never-started (still bootstrapping) OR streak not yet reached →
                // bounded only by the absolute ceiling.
                LoopDecision::FailCeiling
            } else {
                LoopDecision::Continue
            }
        }
        ProbeOutcome::Alive => {
            if elapsed_secs >= ceiling_secs {
                LoopDecision::FailCeiling
            } else {
                LoopDecision::Continue
            }
        }
    }
}

/// Decision for the RUNTIME health watchdog when a /health probe MISSES (the
/// daemon was healthy, now a probe failed/timed out). Pure — unit-testable
/// without launchctl/network/sleep. Distinguishes a transient stall (the
/// process is alive, just blocked >3s) from a genuine death.
// Pure decision logic (no OS calls) — compiled on ALL platforms. The watchdog
// runs on every platform (macOS daemon mode + Windows/Linux subprocess mode);
// only the launchctl-pid LIVENESS probe is macOS-only. Over-gating this to macOS
// broke the Windows/Linux build (symbols not found in scope).
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum WatchdogDownDecision {
    /// Process is alive, this is an early miss → DEGRADED, not dead. Keep the UI
    /// usable; do NOT emit terminated. (Emits `backend-degraded`.)
    Degraded,
    /// Process is gone, OR the miss streak reached the escalation threshold →
    /// treat as a real death. (Emits `backend-terminated-restarting`.)
    Terminated,
}

/// Pure down-decision for the runtime watchdog. A single missed probe on a LIVE
/// daemon must NOT be reported as death (the false-offline bug): a transient
/// event-loop stall >3s (GC, a heavy background hook, thread-pool contention)
/// trips one probe while the process is perfectly alive. So:
///   - subprocess-fallback mode (`is_daemon=false`, macOS dev path): there is no
///     launchctl-managed process to check, so `pid_present` is meaningless —
///     preserve the pre-existing straight-to-terminated behavior.
///   - daemon mode, process ALIVE, miss streak below threshold → Degraded.
///   - daemon mode, process GONE, or streak >= threshold → Terminated.
/// `consecutive_misses` counts misses INCLUDING the current one (caller passes 1
/// on the first miss). `escalate_threshold` is the miss count at which an alive-
/// but-persistently-unreachable daemon is finally declared terminated (symmetry
/// with the frontend 30s-poll failureThreshold=2).
///
/// ## Heartbeat override (run_5b0d6ec3 — the architectural root fix)
///
/// `pid_present` (launchctl-registered) proves the process EXISTS but NOT that
/// it isn't wedged, and the miss-streak escalation still declares a *busy* (not
/// dead) daemon Terminated at `escalate_threshold` misses (~6s) → the residual
/// false-offline. The independent-thread heartbeat closes this:
///   - `heartbeat_fresh` (file mtime < 10s) = the writer thread is ticking = the
///     process is ALIVE even under a full CPU/GIL burst on the loop thread. When
///     fresh, a busy loop (`loop_wedged=false`) is HARD-BLOCKED from Terminated —
///     it stays Degraded no matter how high the miss streak climbs. This is what
///     makes "busy misread as dead" structurally impossible.
///   - `loop_wedged` (heartbeat's `loop_age` ≥ 15s) = the loop is GENUINELY
///     deadlocked, not merely busy → the heartbeat no longer protects it; fall
///     through to the normal streak/pid logic so a real wedge still surfaces.
///   - heartbeat NOT fresh (missing / stale / old binary without the file) → the
///     override is inert and the legacy pid+streak path runs UNCHANGED (zero
///     regression: an old daemon binary produces no heartbeat file → same
///     behavior as before this change).
// Pure logic, no OS calls — compiled on all platforms (see enum note above).
fn watchdog_down_decision(
    is_daemon: bool,
    pid_present: bool,
    consecutive_misses: u32,
    escalate_threshold: u32,
    heartbeat_fresh: bool,
    loop_wedged: bool,
) -> WatchdogDownDecision {
    if !is_daemon {
        // Subprocess fallback: no launchd pid signal — keep old behavior.
        return WatchdogDownDecision::Terminated;
    }
    // Heartbeat override: a fresh heartbeat + a loop that is NOT wedged proves
    // the daemon is alive-but-busy. Never declare death — stay Degraded so the
    // UI keeps inputs usable, regardless of the miss streak. A wedged loop or a
    // stale/absent heartbeat skips this and uses the legacy logic below.
    if heartbeat_fresh && !loop_wedged {
        return WatchdogDownDecision::Degraded;
    }
    if pid_present && consecutive_misses < escalate_threshold {
        WatchdogDownDecision::Degraded
    } else {
        WatchdogDownDecision::Terminated
    }
}

/// Event the runtime watchdog emits on each RECOVERY poll (daemon mode, miss
/// streak at/over the escalation threshold, terminated not yet emitted this
/// episode). Pure — unit-tested. Degraded MUST re-emit `backend-degraded` on every
/// poll: useHealthMonitor.ts honours that signal as liveness proof only within
/// DEGRADED_LIVENESS_WINDOW_MS (15s), so it has to keep arriving for as long as
/// the stall lasts (RECOVERY_POLL_SECS = 3s cadence).
fn recovery_poll_event(decision: WatchdogDownDecision) -> &'static str {
    match decision {
        WatchdogDownDecision::Degraded => "backend-degraded",
        WatchdogDownDecision::Terminated => "backend-terminated-restarting",
    }
}

/// Check whether the launchd-managed daemon PROCESS is currently alive, given a
/// pre-resolved `uid` (resolve once per probe, not per attempt — the caller loops).
///
/// MECHANISM: `launchctl print gui/<uid>/com.swarmai.backend` prints a `pid = N`
/// line iff the service has a running process. The PID populates at exec() (before
/// the port binds), so this is a true "alive during boot" signal, not a
/// "serving" signal.
/// VERIFY: same call + `pid = ` parse already used in production at the
/// bootout-PID-capture path (see `sync_daemon_version`).
#[cfg(target_os = "macos")]
fn daemon_pid_present(uid: &str) -> bool {
    matches!(daemon_liveness(uid), DaemonLiveness::Alive)
}

/// Tri-state daemon liveness. The runtime watchdog needs to distinguish "launchctl
/// says the process is gone" (Gone → real death) from "launchctl itself failed to
/// run" (Unknown → a transient tool/system hiccup, NOT proof of death). Collapsing
/// Unknown into "gone" would reintroduce the false-offline bug through the liveness
/// check: a momentary launchctl failure during a system-load stall would wrongly
/// declare the (alive) daemon dead. `daemon_pid_present` keeps the old bool contract
/// for the cold-start callers (Unknown≡not-present there is fine — they only fail
/// FAST on a confirmed dead STREAK, gated by ever_alive).
#[cfg(target_os = "macos")]
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum DaemonLiveness {
    Alive,
    Gone,
    Unknown,
}

#[cfg(target_os = "macos")]
fn daemon_liveness(uid: &str) -> DaemonLiveness {
    daemon_liveness_pid(uid).0
}

/// `daemon_liveness` plus the pid launchd reports (`pid = N`).
#[cfg(target_os = "macos")]
fn daemon_liveness_pid(uid: &str) -> (DaemonLiveness, Option<u32>) {
    let service_target = format!("gui/{}/com.swarmai.backend", uid);
    match std::process::Command::new("launchctl")
        .args(["print", &service_target])
        .output()
    {
        Ok(o) => {
            let stdout = String::from_utf8_lossy(&o.stdout);
            match stdout.lines().find_map(|l| l.trim_start().strip_prefix("pid = ")) {
                Some(p) => (DaemonLiveness::Alive, p.trim().parse::<u32>().ok()),
                None => (DaemonLiveness::Gone, None),
            }
        }
        // launchctl failed to run (transient) — do NOT treat as death.
        Err(_) => (DaemonLiveness::Unknown, None),
    }
}

/// Resolve the current uid via `id -u` once (empty string on failure → the
/// launchctl target will simply never match, classified as no-pid).
#[cfg(target_os = "macos")]
fn current_uid() -> String {
    std::process::Command::new("id")
        .arg("-u")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Adaptive cold-start probe: keep waiting while the daemon PROCESS is alive
/// (bounded by `COLD_START_CEILING_SECS`), fail fast after `COLD_START_DEAD_STREAK`
/// consecutive no-process observations. Emits the same `backend-starting-progress`
/// events as the fixed probe. Returns Some(port) once /health is healthy, None on
/// dead-streak or ceiling.
///
/// Used for BOTH cold-start paths (first-install in `start_backend` and post-swap
/// verify in `sync_daemon_version`) — same false-fatal bug class (R25).
#[cfg(target_os = "macos")]
async fn probe_daemon_health_adaptive(app_handle: Option<&tauri::AppHandle>) -> Option<u16> {
    let probe_url = format!("http://127.0.0.1:{}/health", DAEMON_PORT);
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return None,
    };

    // Resolve uid ONCE — it is invariant for the process lifetime, so there is no
    // need to spawn `id -u` on every probe miss (adversarial-review perf fix).
    let uid = current_uid();

    let mut elapsed_secs: u64 = 0;
    let mut consecutive_dead: u32 = 0;
    // Whether we've observed the daemon process alive at least once this probe.
    // Gates dead-streak fail-fast: "never started yet" (bootstrap window) must NOT
    // fail fast, only "was alive, now gone" (crash) may. See probe_loop_decision.
    let mut ever_alive = false;

    loop {
        // Emit progress (elapsed / ceiling) so the frontend overlay can show a
        // real cold-start message instead of a stalled bar.
        if let Some(handle) = app_handle {
            let _ = handle.emit(
                "backend-starting-progress",
                serde_json::json!({
                    "elapsedSecs": elapsed_secs,
                    "totalSecs": COLD_START_CEILING_SECS,
                    "adaptive": true,
                }),
            );
        }

        let http_healthy = match client.get(&probe_url).send().await {
            Ok(resp) => match resp.text().await {
                Ok(body) => {
                    let (healthy, _, _) = parse_health_response(&body);
                    healthy
                }
                Err(_) => false,
            },
            Err(_) => false,
        };

        // Only shell out to launchctl when HTTP is not yet healthy (the boot
        // window) — a serving backend is Ready without a liveness check.
        let outcome = if http_healthy {
            ProbeOutcome::Ready
        } else {
            classify_probe_outcome(false, daemon_pid_present(&uid))
        };

        if outcome == ProbeOutcome::Dead {
            consecutive_dead += 1;
        } else {
            consecutive_dead = 0;
            // Ready OR Alive both prove the process exists (Ready = serving,
            // Alive = booting) — latch that we've seen it up.
            ever_alive = true;
        }

        match probe_loop_decision(
            outcome,
            elapsed_secs,
            consecutive_dead,
            COLD_START_CEILING_SECS,
            COLD_START_DEAD_STREAK,
            ever_alive,
        ) {
            LoopDecision::Succeed => return Some(DAEMON_PORT),
            LoopDecision::FailDead => {
                println!(
                    "[Tauri] Adaptive probe: daemon process gone for {} consecutive checks — failing fast at {}s",
                    consecutive_dead, elapsed_secs
                );
                return None;
            }
            LoopDecision::FailCeiling => {
                println!(
                    "[Tauri] Adaptive probe: reached {}s ceiling while daemon alive but not serving — giving up",
                    elapsed_secs
                );
                return None;
            }
            LoopDecision::Continue => {
                tokio::time::sleep(tokio::time::Duration::from_secs(PROBE_INTERVAL_SECS)).await;
                elapsed_secs += PROBE_INTERVAL_SECS;
            }
        }
    }
}

/// Probe daemon health with optional progress events emitted to the frontend.
/// Each attempt emits `backend-starting-progress` with `{ attempt, max_attempts, elapsed_secs }`.
async fn probe_daemon_health_with_progress(
    max_attempts: u32,
    interval_secs: u64,
    app_handle: Option<&tauri::AppHandle>,
) -> Option<u16> {
    let probe_url = format!("http://127.0.0.1:{}/health", DAEMON_PORT);
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
    {
        Ok(c) => c,
        Err(_) => return None,
    };

    for attempt in 1..=max_attempts {
        // Emit progress event to frontend
        if let Some(handle) = app_handle {
            let elapsed = (attempt - 1) as u64 * interval_secs;
            let _ = handle.emit("backend-starting-progress", serde_json::json!({
                "attempt": attempt,
                "maxAttempts": max_attempts,
                "elapsedSecs": elapsed,
                "totalSecs": max_attempts as u64 * interval_secs,
            }));
        }

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
/// macOS-only: part of the daemon cold-start/upgrade path (Win/Linux use the
/// subprocess spawn path). Gating avoids E0425 on the macos-only symbols it
/// shares a call graph with (`probe_daemon_health_adaptive`, `DAEMON_UPGRADE_DRAIN_SECS`).
#[cfg(target_os = "macos")]
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
/// macOS-only: the daemon cold-start/upgrade path exists only on macOS
/// (Win/Linux use the subprocess spawn path); its sole caller is macos-gated.
#[cfg(target_os = "macos")]
async fn sync_daemon_version(app: &tauri::AppHandle, app_version: &str) -> Result<(), String> {
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
            if waited >= DAEMON_UPGRADE_DRAIN_SECS {
                println!("[Tauri] Daemon PID {} still alive after {}s (drain ceiling) — sending SIGKILL", pid, waited);
                let _ = std::process::Command::new("kill")
                    .args(["-9", &pid.to_string()])
                    .output();
                // Poll until kernel reaps the process (SIGKILL delivery is async)
                for _ in 0..3 {
                    tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                    let reaped = std::process::Command::new("kill")
                        .args(["-0", &pid.to_string()])
                        .output()
                        .map(|o| !o.status.success())
                        .unwrap_or(true);
                    if reaped {
                        break;
                    }
                }
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

    // Step 4: Deploy the new version into the versioned runtime and switch `current`
    // (the old version stays as `previous` for rollback; nothing is overwritten in place).
    let bundle_base = app.path().resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;
    let runtime_root = runtime_deploy::runtime_root().map_err(|e| e.to_string())?;
    let previous_current = runtime_deploy::current_version(&runtime_root);
    let target_binary = deploy_bundled_runtime(app)?;
    println!("[Tauri] Daemon binary deployed: {:?}", target_binary);

    // Step 4b: Re-render the plist from the NEW bundle's template. We booted the
    // job out above, so this bootstrap is the one moment launchd re-reads it — a
    // job-definition change shipped in this release (e.g. ProcessType) would
    // otherwise never reach an upgraded install. Non-fatal: the old file still
    // bootstraps the new binary.
    match write_daemon_plist_from_bundle(&bundle_base, &home) {
        Ok(true) => println!("[Tauri] Daemon plist refreshed from bundle template"),
        Ok(false) => println!("[Tauri] No bundled plist template — keeping existing plist"),
        Err(e) => println!("[Tauri] Plist refresh failed (keeping existing plist): {}", e),
    }

    // Step 5: Bootstrap (reload into launchd with new binary)
    let _ = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, &plist_path])
        .output();

    // Step 6: Verify new version. Adaptive cold-start probe (same false-fatal
    // class as first-install, R25): a post-swap cold start can exceed a fixed 60s
    // cap while the daemon process is alive — wait while alive, bounded by ceiling.
    if let Some(_port) = probe_daemon_health_adaptive(Some(app)).await {
        let new_version = get_daemon_version().await.unwrap_or_default();
        if new_version == app_version {
            println!("[Tauri] Daemon upgraded successfully: {}", app_version);
            // the previous runtime version stays as the one rollback copy
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

    // Rollback: point `current` back at the previous runtime (or, when the old daemon
    // ran from the legacy ~/.swarm-ai/daemon deploy, drop `current` so the wrapper uses
    // it again), then bootstrap so a daemon is at least running.
    let rolled = match previous_current {
        Some(_) => runtime_deploy::rollback(&runtime_root).map(|v| v.to_string())
            .map_err(|e| e.to_string()),
        None => std::fs::remove_file(runtime_root.join("current"))
            .map(|_| "legacy".to_string()).map_err(|e| e.to_string()),
    };
    println!("[Tauri] Rolling back the backend runtime: {:?}", rolled);
    let _ = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, &plist_path])
        .output();

    Err("Daemon upgrade failed — daemon not responding after restart. Rolled back to previous version.".to_string())
}


/// Deploy the installer's bundled backend into the versioned runtime and make it
/// `current` (design 2026-10-24-swarmai-usable-by-anyone §3.1; src/runtime_deploy.rs).
/// Returns the executable the daemon / subprocess starts. Err when the app carries no
/// backend (a dev build: the caller then looks for one deployed by dev tooling).
fn deploy_bundled_runtime(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    let version = app.config().version.clone().unwrap_or_default();
    let resource_dir = app.path().resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;
    let root = runtime_deploy::runtime_root().map_err(|e| e.to_string())?;
    let dir = runtime_deploy::deploy(&resource_dir, &root, &version).map_err(|e| e.to_string())?;
    runtime_deploy::switch_current(&root, &version).map_err(|e| e.to_string())?;
    #[cfg(target_os = "macos")]
    {
        // a copy keeps the installer's quarantine attribute; launchd-started binaries
        // carrying it are refused by Gatekeeper
        let _ = std::process::Command::new("xattr")
            .args(["-dr", "com.apple.quarantine"]).arg(&dir).output();
    }
    println!("[Tauri] Backend runtime {} deployed at {:?}", version, dir);
    Ok(dir.join(runtime_deploy::exe_name()))
}

/// The executable the backend runs from: the runtime's `current`, else the legacy
/// `~/.swarm-ai/daemon` deploy (dev tooling before it moved to the runtime).
fn deployed_backend_exe() -> Option<std::path::PathBuf> {
    if let Ok(root) = runtime_deploy::runtime_root() {
        if let Some(exe) = runtime_deploy::current_exe(&root) {
            return Some(exe);
        }
    }
    #[cfg(target_os = "windows")]
    let home = std::env::var("USERPROFILE").unwrap_or_default();
    #[cfg(not(target_os = "windows"))]
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() {
        return None;
    }
    let legacy = std::path::PathBuf::from(home).join(".swarm-ai").join("daemon")
        .join(runtime_deploy::exe_name());
    legacy.is_file().then_some(legacy)
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
/// macOS-only: wraps `sync_daemon_version` (daemon upgrade path is macOS-only);
/// its sole caller in `start_backend` is already macos-gated.
#[cfg(target_os = "macos")]
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

/// Substitute the launchd plist template placeholders. Pure — unit-tested.
/// Must stay in step with the bash renderer (`scripts/daemon-lib.sh`
/// `_deploy_daemon_binary`) and the Python one (`/api/system/upgrade`).
#[allow(dead_code)]
fn render_daemon_plist(template: &str, wrapper_path: &str, log_dir: &str, home: &str) -> String {
    template
        .replace("__WRAPPER_PATH__", wrapper_path)
        .replace("__LOG_DIR__", log_dir)
        .replace("__HOME__", home)
}

/// Render the bundled `daemon/com.swarmai.backend.plist.template` into
/// `~/Library/LaunchAgents/com.swarmai.backend.plist`. Returns Ok(false) when the
/// bundle carries no template (dev layout) so the caller keeps the existing file.
///
/// Every path that bootstraps the daemon from the app bundle must call this
/// first: launchd reads the plist only at bootstrap, so a job definition change
/// shipped in the template (e.g. ProcessType Background → Standard) only takes
/// effect if the file on disk is rewritten before that bootstrap.
#[allow(dead_code)]
fn write_daemon_plist_from_bundle(bundle_base: &std::path::Path, home: &str) -> Result<bool, String> {
    let template_path = bundle_base.join("daemon").join("com.swarmai.backend.plist.template");
    if !template_path.exists() {
        return Ok(false);
    }
    let template = std::fs::read_to_string(&template_path)
        .map_err(|e| format!("Failed to read plist template: {}", e))?;
    let home_path = std::path::Path::new(home);
    let wrapper = home_path.join(".swarm-ai").join("swarmai_backend.sh");
    let log_dir = home_path.join(".swarm-ai").join("logs");
    let content = render_daemon_plist(
        &template,
        wrapper.to_str().unwrap_or(""),
        log_dir.to_str().unwrap_or(""),
        home,
    );
    let dest = home_path.join(DAEMON_PLIST_RELPATH);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create LaunchAgents dir: {}", e))?;
    }
    std::fs::write(&dest, &content)
        .map_err(|e| format!("Failed to write plist: {}", e))?;
    Ok(true)
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

    let log_dir = home_path.join(".swarm-ai").join("logs");
    let launch_agents = home_path.join("Library").join("LaunchAgents");

    // Create directories
    std::fs::create_dir_all(&log_dir)
        .map_err(|e| format!("Failed to create log dir: {}", e))?;
    std::fs::create_dir_all(&launch_agents)
        .map_err(|e| format!("Failed to create LaunchAgents dir: {}", e))?;

    // Step 1: Deploy the bundled backend into the versioned runtime
    // (~/.swarm-ai/runtime/<version>/, `current` → it). A dev app without a bundled
    // backend uses what dev tooling deployed.
    let bundle_base = app.path().resource_dir()
        .map_err(|e| format!("Failed to get resource dir: {}", e))?;
    if bundle_base.join("python-backend").join(runtime_deploy::exe_name()).exists() {
        deploy_bundled_runtime(app)?;
    } else if let Some(exe) = deployed_backend_exe() {
        println!("[Tauri] No bundled backend — using the deployed one at {:?}", exe);
    } else {
        return Err(format!(
            "The app carries no backend ({:?}) and none is deployed. Install a release \
             build, or build and deploy one with ./prod.sh build.",
            bundle_base.join("python-backend")
        ));
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
    // -r: recursive, clears quarantine on all files in the onedir bundle
    let _ = std::process::Command::new("xattr")
        .args(["-dr", "com.apple.quarantine"])
        .arg(&wrapper_dest)
        .output();
    if let Ok(root) = runtime_deploy::runtime_root() {
        let _ = std::process::Command::new("xattr")
            .args(["-dr", "com.apple.quarantine"])
            .arg(&root)
            .output();
    }

    // Step 3+4: Generate plist from template and write
    let plist_dest = launch_agents.join("com.swarmai.backend.plist");

    if write_daemon_plist_from_bundle(&bundle_base, &home)? {
        println!("[Tauri] Installed daemon plist: {:?}", plist_dest);
    } else if !plist_dest.exists() {
        return Err(format!(
            "Plist template not found ({:?}) and no existing plist",
            bundle_base.join("daemon").join("com.swarmai.backend.plist.template")
        ));
    }

    // Step 6: Install the C034 guardian watchdog (NON-FATAL). The guardian is a
    // recovery safety-net (re-bootstraps the backend if it dies deregistered);
    // it must NEVER block backend startup. `let _ =` swallows any error — if the
    // guardian fails to install, the backend still comes up via the bootstrap
    // below. The prevention half (ancestry re-exec) ships inside the backend
    // binary and is unaffected by guardian-install failure.
    if let Err(e) = install_guardian(&home, &bundle_base, &log_dir) {
        println!("[Tauri] Guardian install skipped (non-fatal): {}", e);
    }

    // Step 5: Bootstrap via launchctl
    bootstrap_daemon(&home)
}

/// Install the C034 guardian watchdog launchd agent (macOS, NON-FATAL).
///
/// Mirrors the backend-install idioms: copy script + standalone stdlib guard to
/// ~/.swarm-ai/, render the plist template, bootstrap via launchctl. Idempotent.
///
/// The caller invokes this with `let _ =` / `if let Err` — a guardian install
/// failure must not prevent the backend daemon from coming up (the guardian is
/// a safety net, not load-bearing).
///
/// Assets come from the bundled `daemon/` resources (staged in
/// desktop/resources/daemon/): `swarmai_guardian.sh`, `daemon_guard.py`,
/// `com.swarmai.guardian.plist.template`.
fn install_guardian(
    home: &str,
    bundle_base: &std::path::Path,
    log_dir: &std::path::Path,
) -> Result<(), String> {
    let home_path = std::path::Path::new(home);
    let swarm_dir = home_path.join(".swarm-ai");
    let guardian_dir = swarm_dir.join("guardian");
    std::fs::create_dir_all(&guardian_dir)
        .map_err(|e| format!("create guardian dir: {}", e))?;

    // 1. Copy the guardian loop script → ~/.swarm-ai/swarmai_guardian.sh
    //    ATOMIC (write .tmp + rename): on UPGRADE this overwrites a script the
    //    OLD guardian (StartInterval 30s) may be mid-execution running. A plain
    //    truncate-rewrite would corrupt a running bash read; rename swaps the
    //    inode so the running bash keeps its original file. Also: files copied
    //    out of a code-signed .app bundle are read-only (0444), so a 2nd-launch
    //    in-place copy would EACCES — rename + explicit chmod avoids that.
    let script_src = bundle_base.join("daemon").join("swarmai_guardian.sh");
    let script_dest = swarm_dir.join("swarmai_guardian.sh");
    if !script_src.exists() {
        return Err(format!("guardian script not in bundle: {:?}", script_src));
    }
    atomic_install(&script_src, &script_dest, 0o755)
        .map_err(|e| format!("install guardian script: {}", e))?;

    // 2. Copy the standalone (pure-stdlib) guard → ~/.swarm-ai/guardian/daemon_guard.py
    let guard_src = bundle_base.join("daemon").join("daemon_guard.py");
    let guard_dest = guardian_dir.join("daemon_guard.py");
    if !guard_src.exists() {
        return Err(format!("guardian guard.py not in bundle: {:?}", guard_src));
    }
    atomic_install(&guard_src, &guard_dest, 0o644)
        .map_err(|e| format!("install guardian guard.py: {}", e))?;

    // Clear quarantine on BOTH installed files (Gatekeeper quarantines anything
    // extracted from the .app bundle). The backend onedir clears its dir
    // recursively; ~/.swarm-ai/guardian/ is NOT under that path, so do it here.
    let _ = std::process::Command::new("xattr")
        .args(["-dr", "com.apple.quarantine"])
        .arg(&script_dest)
        .output();
    let _ = std::process::Command::new("xattr")
        .args(["-dr", "com.apple.quarantine"])
        .arg(&guard_dest)
        .output();

    // 3. Render the guardian plist from the template (__GUARDIAN_SCRIPT__/__LOG_DIR__).
    let tmpl_src = bundle_base
        .join("daemon")
        .join("com.swarmai.guardian.plist.template");
    if !tmpl_src.exists() {
        return Err(format!("guardian plist template not in bundle: {:?}", tmpl_src));
    }
    let tmpl = std::fs::read_to_string(&tmpl_src)
        .map_err(|e| format!("read guardian plist template: {}", e))?;
    let rendered = tmpl
        .replace("__GUARDIAN_SCRIPT__", script_dest.to_str().unwrap_or(""))
        .replace("__LOG_DIR__", log_dir.to_str().unwrap_or(""));
    let plist_dest = home_path
        .join("Library")
        .join("LaunchAgents")
        .join("com.swarmai.guardian.plist");
    std::fs::write(&plist_dest, &rendered)
        .map_err(|e| format!("write guardian plist: {}", e))?;

    // 4. Bootstrap the guardian (rc 0/5/37 = success — idempotent, like backend).
    let uid_output = std::process::Command::new("id")
        .arg("-u")
        .output()
        .map_err(|e| format!("get uid: {}", e))?;
    let uid = String::from_utf8_lossy(&uid_output.stdout).trim().to_string();
    let gui_target = format!("gui/{}", uid);
    let plist_str = plist_dest.to_str().unwrap_or("");

    let output = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, plist_str])
        .output()
        .map_err(|e| format!("launchctl bootstrap guardian: {}", e))?;
    match output.status.code() {
        Some(0) => {
            println!("[Tauri] Guardian watchdog bootstrapped");
            Ok(())
        }
        Some(5) | Some(37) => {
            // rc 5 (I/O error) / 37 (already in progress) usually mean "already
            // loaded" — but a malformed plist can also return 5. Verify the
            // agent is actually registered (parity with bootstrap_daemon), so a
            // genuine failure isn't masked as idempotent success.
            let verify = std::process::Command::new("launchctl")
                .args(["list", "com.swarmai.guardian"])
                .output();
            match verify {
                Ok(v) if v.status.success() => {
                    println!("[Tauri] Guardian already loaded (verified)");
                    Ok(())
                }
                _ => Err(format!(
                    "guardian bootstrap rc 5/37 but not in launchctl list (plist may be malformed)"
                )),
            }
        }
        Some(code) => Err(format!("guardian bootstrap code {}", code)),
        None => Err("guardian bootstrap killed by signal".to_string()),
    }
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

/// Spawn the backend as a subprocess (Windows/Linux).
///
/// Returns Ok(pid) on success, Err(msg) on failure.
/// Used by both `start_backend` (initial spawn) and the watchdog (crash recovery).
#[cfg(not(target_os = "macos"))]
async fn spawn_subprocess(app: &tauri::AppHandle, state: &SharedBackendState) -> Result<u32, String> {
    let binary_path = find_backend_binary(app)?;
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
        .map_err(|e| format!("Failed to start backend: {}. Binary: {:?}", e, binary_path))?;

    let child_pid = child.id();
    println!("[Tauri] Backend subprocess spawned (PID {})", child_pid);

    // Update state
    {
        let mut backend = state.lock().await;
        backend.pid = Some(child_pid);
        backend.is_daemon_mode = false;
        backend.intentional_shutdown = false;
    }

    // Wait for health endpoint
    if probe_daemon_health(20, 1).await.is_some() {
        let mut backend = state.lock().await;
        // Guard: if app closed during the probe, don't mark as running
        if backend.intentional_shutdown {
            drop(backend);
            kill_process_tree(child_pid);
            return Err("Backend came up but app is shutting down — killed".to_string());
        }
        backend.port = DAEMON_PORT;
        backend.running = true;
        return Ok(child_pid);
    }

    // Didn't come up — kill the zombie and clear state
    kill_process_tree(child_pid);
    {
        let mut backend = state.lock().await;
        backend.pid = None;
        backend.running = false;
    }
    Err(format!("Backend spawned (PID {}) but not healthy after 20s", child_pid))
}

/// No-op on macOS — subprocess spawn is never called (daemon mode only).
#[cfg(target_os = "macos")]
async fn spawn_subprocess(_app: &tauri::AppHandle, _state: &SharedBackendState) -> Result<u32, String> {
    Err("spawn_subprocess not supported on macOS (daemon mode only)".to_string())
}

/// Background health watchdog for daemon mode.
///
/// When Tauri connects to an external daemon (not a subprocess it owns),
/// there's no process monitor. This task polls the daemon health endpoint
/// every `interval_secs` and emits frontend events on state changes:
///   - `backend-terminated-restarting` when daemon becomes unreachable (launchd will restart)
///   - `backend-restarted` when the daemon recovers as a NEW process (boot_id changed)
///   - `backend-resumed` when the daemon recovers as the SAME process (boot_id
///     unchanged — it was just blocked >3s, never actually died)
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
    // Daemon mode: launchd ALWAYS restarts (KeepAlive:true). Onedir cold start
    // after a binary deploy can take 30-60s (ThrottleInterval 10s + extraction).
    // 40 × 3s = 120s is enough for even the worst-case deploy scenario.
    // Subprocess mode: 3 re-spawn attempts with backoff (handled separately below).
    const MAX_RECOVERY_ATTEMPTS: u32 = 40; // 40 × 3s = 120s — emit warning to frontend
    const MAX_DAEMON_WAIT: u32 = 200; // 200 × 3s = 600s — hard cap, stop polling
    const RECOVERY_POLL_SECS: u64 = 3;
    // Miss streak at which a DEGRADED (live-but-stalled) daemon is escalated to a
    // real terminated signal (run_13094a88). 2 misses × RECOVERY_POLL_SECS(3s) ≈ 6s
    // of persistent unreachability before the UI disables inputs — symmetric with
    // the frontend 30s-poll failureThreshold=2, well over any transient stall.
    const DOWN_ESCALATE_MISSES: u32 = 2;

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
        // Whether we've already emitted a terminated signal for the CURRENT down
        // episode (run_5b0d6ec3 HIGH-1 fix). The escalation re-checks the death
        // decision on EVERY degraded iteration (so a heartbeat going stale flips
        // busy→dead within one 3s poll instead of being masked for ~120s), but
        // the terminated event must fire only ONCE per episode. Reset to false on
        // any recovery (was_healthy back to true).
        let mut terminated_emitted = false;
        // The stall cause last written to the watchdog log in this episode.
        let mut last_logged_cause = String::from("-");
        // Since when the daemon looks suspended (track_suspension); awake time only.
        let mut suspended_since: Option<std::time::Instant> = None;
        let mut known_boot_id: Option<String> = None;
        // Resolve uid ONCE for the watchdog's lifetime (not per-miss) — the launchd
        // uid is stable, and the cold-start path resolves it once per session too.
        // Avoids spawning `id -u` on every missed probe during a recovery window.
        // macOS-only: the launchctl liveness probe needs it; non-macOS (subprocess
        // mode) has no launchctl signal, so the uid is unused there.
        #[cfg(target_os = "macos")]
        let watchdog_uid = current_uid();

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
                    watchdog_log("Health watchdog: backend stopped intentionally — exiting");
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
                            watchdog_log(&format!(
                                "Watchdog: boot_id changed ({} → {}) — daemon restarted silently",
                                old_bid, bid
                            ));
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
                // Backend probe MISSED after being healthy. A single miss does NOT
                // mean death (run_13094a88 false-offline root-fix): a transient
                // event-loop stall >3s (GC, a heavy background hook, thread-pool
                // contention) trips one probe while the daemon is perfectly alive.
                // Verify process liveness (launchctl pid) and DEGRADE — keep the UI
                // usable — instead of crying death on the first miss.
                recovery_attempts = 1;
                let is_daemon = { state.lock().await.is_daemon_mode };
                // pid check is only meaningful for the launchd-managed daemon; the
                // subprocess-fallback dev path has no launchctl signal (→ Terminated).
                // Unknown (launchctl itself failed — a transient hiccup) is treated as
                // ALIVE, not gone: a tool failure must NOT reintroduce a false death
                // (Gate-2 finding). Only a definitive Gone declares death.
                // launchctl-pid liveness is macOS-only. On non-macOS, is_daemon is
                // always false (subprocess mode has no launchd), so pid_present=false
                // and watchdog_down_decision returns Terminated — the pre-existing
                // Windows/Linux behavior, preserved.
                #[cfg(target_os = "macos")]
                let (pid_present, launchd_pid) = if is_daemon {
                    // launchctl is a blocking subprocess — offload so it can never
                    // stall the async watchdog task (Gate-2: a blocking call on the
                    // tokio task during a system-load stall could compound it).
                    let uid = watchdog_uid.clone();
                    let (liveness, lpid) = tokio::task::spawn_blocking(move || daemon_liveness_pid(&uid))
                        .await
                        .unwrap_or((DaemonLiveness::Unknown, None));
                    (liveness != DaemonLiveness::Gone, lpid)
                } else {
                    (false, None)
                };
                #[cfg(not(target_os = "macos"))]
                let (pid_present, launchd_pid): (bool, Option<u32>) = (false, None);
                // Heartbeat override (run_5b0d6ec3): a fresh heartbeat + non-wedged
                // loop proves alive-but-busy → stay Degraded even at the streak
                // ceiling. Read here (pure file I/O, fast) on every miss.
                let hb = read_heartbeat_full();
                let suspended_for = track_suspension(&mut suspended_since, &hb, launchd_pid);
                let (heartbeat_fresh, loop_wedged) = decision_inputs(&hb, launchd_pid, suspended_for);
                let payload = health_event_payload(&hb, if is_daemon { Some(pid_present) } else { None }, launchd_pid, suspended_for);
                match watchdog_down_decision(is_daemon, pid_present, recovery_attempts, DOWN_ESCALATE_MISSES, heartbeat_fresh, loop_wedged) {
                    WatchdogDownDecision::Degraded => {
                        // Alive but stalled: tell the UI to show a reconnecting
                        // banner WITHOUT disabling inputs. Escalation to terminated
                        // happens in the !was_healthy && !healthy block below once
                        // the miss streak reaches DOWN_ESCALATE_MISSES.
                        watchdog_log(&format!("Watchdog: /health missed — DEGRADED (miss {}/{}) {}",
                            recovery_attempts, DOWN_ESCALATE_MISSES, format!("{} suspended={} suspended_secs={}", watchdog_evidence(&hb, if is_daemon { Some(pid_present) } else { None }), suspended_within_cap(&hb, launchd_pid, suspended_for), suspended_for.map(|d| d.as_secs()).unwrap_or(0))));
                        last_logged_cause = stall_cause(&hb);
                        let _ = app_handle.emit("backend-degraded", payload);
                        was_healthy = false;
                        continue; // poll again at RECOVERY_POLL_SECS; may resume or escalate
                    }
                    WatchdogDownDecision::Terminated => {
                        // Process gone (or subprocess mode): a real death signal.
                        watchdog_log(&format!("Watchdog: /health missed — TERMINATED (miss {}/{}) {}",
                            recovery_attempts, DOWN_ESCALATE_MISSES, format!("{} suspended={} suspended_secs={}", watchdog_evidence(&hb, if is_daemon { Some(pid_present) } else { None }), suspended_within_cap(&hb, launchd_pid, suspended_for), suspended_for.map(|d| d.as_secs()).unwrap_or(0))));
                        let _ = app_handle.emit("backend-terminated-restarting", payload);
                    }
                }

                if is_daemon {
                    // Daemon mode: launchd KeepAlive will restart it — just wait
                    watchdog_log(&format!("Watchdog: daemon unreachable — waiting for launchd restart (attempt {}/{})",
                        recovery_attempts, MAX_RECOVERY_ATTEMPTS));
                } else {
                    // Subprocess mode: WE must restart it
                    const SUBPROCESS_BACKOFF: [u64; 3] = [3, 6, 12];
                    let backoff = SUBPROCESS_BACKOFF[0];
                    watchdog_log(&format!("Watchdog: subprocess crashed — re-spawning in {}s (attempt 1/3)", backoff));
                    tokio::time::sleep(tokio::time::Duration::from_secs(backoff)).await;
                    match spawn_subprocess(&app_handle, &state).await {
                        Ok(pid) => {
                            watchdog_log(&format!("Watchdog: subprocess re-spawned (PID {})", pid));
                            let _ = app_handle.emit("backend-restarted", DAEMON_PORT);
                            recovery_attempts = 0;
                            was_healthy = true;
                            continue;
                        }
                        Err(e) => watchdog_log(&format!("Watchdog: re-spawn attempt 1 failed: {}", e)),
                    }
                }
            } else if !was_healthy && !healthy {
                recovery_attempts += 1;
                let is_daemon = { state.lock().await.is_daemon_mode };

                if !is_daemon && recovery_attempts <= 3 {
                    // Subprocess mode: retry with exponential backoff
                    const SUBPROCESS_BACKOFF: [u64; 3] = [3, 6, 12];
                    let backoff = SUBPROCESS_BACKOFF[(recovery_attempts - 1).min(2) as usize];
                    watchdog_log(&format!("Watchdog: subprocess still down — re-spawning in {}s (attempt {}/3)",
                        backoff, recovery_attempts));
                    tokio::time::sleep(tokio::time::Duration::from_secs(backoff)).await;
                    match spawn_subprocess(&app_handle, &state).await {
                        Ok(pid) => {
                            watchdog_log(&format!("Watchdog: subprocess re-spawned (PID {})", pid));
                            let _ = app_handle.emit("backend-restarted", DAEMON_PORT);
                            recovery_attempts = 0;
                            was_healthy = true;
                            continue;
                        }
                        Err(e) => watchdog_log(&format!("Watchdog: re-spawn attempt {} failed: {}", recovery_attempts, e)),
                    }
                    if recovery_attempts >= 3 {
                        watchdog_log("Watchdog: subprocess failed to recover after 3 attempts — giving up");
                        let _ = app_handle.emit("backend-terminated", Option::<i32>::None);
                    }
                } else if is_daemon {
                    // Degraded→Terminated escalation (run_13094a88 + run_5b0d6ec3
                    // HIGH-1 fix): while DEGRADED (a live-but-stalled daemon), re-run
                    // the death decision on EVERY iteration once the streak reaches
                    // DOWN_ESCALATE_MISSES — NOT once at ==threshold. Why: a process
                    // SIGKILL'd mid-episode leaves a <10s-fresh heartbeat file; a
                    // once-only check at ==threshold could land inside that fresh
                    // window and never re-evaluate, masking a real death for up to
                    // MAX_RECOVERY_ATTEMPTS (~120s). Re-checking every poll means the
                    // heartbeat going stale (>10s) flips busy→dead within one 3s
                    // poll. `terminated_emitted` guards the EVENT so it still fires
                    // exactly once per down-episode (reset on recovery).
                    if recovery_attempts >= DOWN_ESCALATE_MISSES && !terminated_emitted {
                        // Unknown (launchctl hiccup) counts as alive — only a
                        // definitive Gone forces death before the streak elapses.
                        // Offloaded (spawn_blocking) like the first-miss check.
                        // macOS-only launchctl probe; non-macOS (subprocess mode)
                        // has no launchd signal → pid_present=false → Terminated.
                        #[cfg(target_os = "macos")]
                        let (pid_present, launchd_pid) = {
                            let uid = watchdog_uid.clone();
                            let (liveness, lpid) = tokio::task::spawn_blocking(move || daemon_liveness_pid(&uid))
                                .await
                                .unwrap_or((DaemonLiveness::Unknown, None));
                            (liveness != DaemonLiveness::Gone, lpid)
                        };
                        #[cfg(not(target_os = "macos"))]
                        let (pid_present, launchd_pid): (bool, Option<u32>) = (false, None);
                        // Heartbeat override (run_5b0d6ec3): re-read on EVERY escalation
                        // poll — a still-fresh, non-wedged heartbeat blocks the
                        // busy→terminated escalation (the residual false-offline);
                        // a stale heartbeat (dead process) or a genuinely wedged loop
                        // lets the streak escalate to a real death on the next poll.
                        let hb = read_heartbeat_full();
                        let suspended_for = track_suspension(&mut suspended_since, &hb, launchd_pid);
                        let (heartbeat_fresh, loop_wedged) = decision_inputs(&hb, launchd_pid, suspended_for);
                        let payload = health_event_payload(&hb, Some(pid_present), launchd_pid, suspended_for);
                        let decision = watchdog_down_decision(true, pid_present, recovery_attempts, DOWN_ESCALATE_MISSES, heartbeat_fresh, loop_wedged);
                        let event = recovery_poll_event(decision);
                        match decision {
                            WatchdogDownDecision::Terminated => {
                                watchdog_log(&format!("Watchdog: still unreachable after {} misses — TERMINATED {}",
                                    recovery_attempts, format!("{} suspended={} suspended_secs={}", watchdog_evidence(&hb, Some(pid_present)), suspended_within_cap(&hb, launchd_pid, suspended_for), suspended_for.map(|d| d.as_secs()).unwrap_or(0))));
                                let _ = app_handle.emit(event, payload);
                                terminated_emitted = true;
                            }
                            WatchdogDownDecision::Degraded => {
                                // Still alive-but-busy: RE-EMIT every recovery poll.
                                // The frontend treats a degraded signal as liveness
                                // proof for only DEGRADED_LIVENESS_WINDOW_MS (15s); a
                                // one-shot emit let a longer stall expire it, and the
                                // 30s JS poll then flipped to red "Backend is
                                // unavailable" while this watchdog still said alive.
                                // Logged when the stall's cause changes (not every 3s).
                                let cause = stall_cause(&hb);
                                if cause != last_logged_cause {
                                    watchdog_log(&format!("Watchdog: still DEGRADED (miss {}), cause changed {} → {} {}",
                                        recovery_attempts, last_logged_cause, cause, format!("{} suspended={} suspended_secs={}", watchdog_evidence(&hb, Some(pid_present)), suspended_within_cap(&hb, launchd_pid, suspended_for), suspended_for.map(|d| d.as_secs()).unwrap_or(0))));
                                    last_logged_cause = cause;
                                }
                                let _ = app_handle.emit(event, payload);
                            }
                        }
                    }
                    // Daemon mode: launchd will restart — wait up to MAX_DAEMON_WAIT.
                    // Log every 10 attempts (30s) to avoid spam.
                    if recovery_attempts % 10 == 0 {
                        watchdog_log(&format!("Watchdog: still waiting for daemon recovery (attempt {}, {}s elapsed)",
                            recovery_attempts, recovery_attempts * RECOVERY_POLL_SECS as u32));
                    }
                    // Emit warning at soft threshold — frontend shows "taking longer" banner.
                    // Only for a daemon already judged dead this episode: if every
                    // poll so far decided Degraded (fresh heartbeat, loop not wedged)
                    // the process is alive, and `backend-terminated` would disable
                    // inputs on a live daemon — the false offline this watchdog avoids.
                    if recovery_attempts == MAX_RECOVERY_ATTEMPTS && terminated_emitted {
                        watchdog_log(&format!("Watchdog: daemon recovery taking longer than expected ({} attempts)",
                            MAX_RECOVERY_ATTEMPTS));
                        let _ = app_handle.emit("backend-terminated", Option::<i32>::None);
                    }
                    // Hard cap: stop polling after 10 minutes — daemon is truly dead.
                    if recovery_attempts >= MAX_DAEMON_WAIT {
                        watchdog_log(&format!("Watchdog: daemon failed to recover after {} attempts ({}s) — giving up",
                            MAX_DAEMON_WAIT, MAX_DAEMON_WAIT * RECOVERY_POLL_SECS as u32));
                        return;
                    }
                }
            } else if !was_healthy && healthy {
                // Backend is reachable again after a down/stall window.
                // Distinguish a REAL restart (a new process — boot_id changed)
                // from a transient stall (same process that was just blocked >3s
                // by heavy synchronous work — boot_id unchanged). Only claim
                // "restarted" when boot_id PROVES a new process; otherwise the
                // daemon merely RESUMED and never actually died.
                let real_restart = match (&known_boot_id, &current_boot_id) {
                    (Some(old), Some(new)) => old != new,
                    _ => false, // can't prove a new process → don't overclaim a restart
                };
                if real_restart {
                    watchdog_log(&format!(
                        "Watchdog: backend RESTARTED (boot_id changed) after {} misses ({}s)",
                        recovery_attempts, recovery_attempts * RECOVERY_POLL_SECS as u32
                    ));
                    let _ = app_handle.emit("backend-restarted", DAEMON_PORT);
                } else {
                    watchdog_log(&format!(
                        "Watchdog: backend RESUMED (same process) after {} misses ({}s)",
                        recovery_attempts, recovery_attempts * RECOVERY_POLL_SECS as u32
                    ));
                    let _ = app_handle.emit("backend-resumed", DAEMON_PORT);
                }
                known_boot_id = match current_boot_id {
                    // Preserve the last known good baseline if this recovery
                    // probe couldn't read a boot_id — clobbering it with None
                    // would make the NEXT real restart undetectable.
                    Some(b) => Some(b),
                    None => known_boot_id,
                };
                recovery_attempts = 0;
                // Recovered → allow a fresh terminated emit for any FUTURE episode
                // (run_5b0d6ec3 HIGH-1: the once-guard is per down-episode).
                terminated_emitted = false;
                suspended_since = None;
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

        // Wait for daemon to come up. Adaptive: a new user's first launch does a
        // slow cold start (deploy ~500MB onedir + Python cold start + DB migration
        // + workspace init) that routinely exceeds a fixed 60s cap — but the daemon
        // PROCESS is alive throughout. Keep waiting while it's alive (bounded by
        // COLD_START_CEILING_SECS); fail fast only if the process is truly gone.
        if let Some(_port) = probe_daemon_health_adaptive(Some(&app)).await {
            println!("[Tauri] Daemon installed and healthy on port {}", DAEMON_PORT);
            let port = connect_daemon(&state, &app).await;
            return Ok(port);
        }

        return Err(format!(
            "Daemon installed but did not become healthy on port {} (process not \
             running, or still not serving after {}s). \
             Check logs: ~/.swarm-ai/logs/backend-stderr.log",
            DAEMON_PORT, COLD_START_CEILING_SECS,
        ));
    }

    #[cfg(not(target_os = "macos"))]
    {
        // Windows/Linux: spawn backend as subprocess (owned by Tauri)
        println!("[Tauri] Spawning backend subprocess on port {}", DAEMON_PORT);

        let state_inner = state.inner().clone();
        match spawn_subprocess(&app, &state_inner).await {
            Ok(pid) => {
                println!("[Tauri] Backend subprocess healthy (PID {})", pid);
                // Start health watchdog for crash detection + auto-restart
                spawn_daemon_health_watchdog(app.clone(), state_inner, 10);
                let _ = app.emit("backend-mode", "subprocess");
                Ok(DAEMON_PORT)
            }
            Err(e) => Err(e),
        }
    }
}

/// Find the backend binary — checks app bundle first, then data dir.
/// Works cross-platform: macOS bundle (Contents/MacOS/), Windows (same dir as exe).
#[allow(dead_code)] // Used on Windows/Linux only; macOS uses auto_install_daemon path.
fn find_backend_binary(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    // Same layout as macOS (design §3.1): deploy the installer's bundled backend into the
    // versioned runtime and run it from there, never from the app's own files.
    match deploy_bundled_runtime(app) {
        Ok(exe) => return Ok(exe),
        Err(e) => println!("[Tauri] Bundled backend not deployed ({}) — looking for a deployed one", e),
    }
    if let Some(exe) = deployed_backend_exe() {
        return Ok(exe);
    }
    // a dev layout: the binary next to the app executable
    let exe_path = std::env::current_exe()
        .map_err(|e| format!("Cannot determine exe path: {}", e))?;
    let adjacent = exe_path.parent().ok_or("Cannot determine exe directory")?
        .join(runtime_deploy::exe_name());
    if adjacent.exists() {
        return Ok(adjacent);
    }
    Err(format!(
        "Backend '{}' not found: the app carries none, nothing is deployed under the \
         runtime ({:?}), and none is next to the app ({:?}).",
        runtime_deploy::exe_name(), runtime_deploy::runtime_root().ok(), adjacent
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
        .manage(TerminalState::default())
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            get_backend_port,
            copy_to_clipboard,
            check_nodejs_version,
            check_python_version,
            check_git_bash_path,
            // Integrated terminal (app-level PTY commands — see terminal.rs)
            terminal::pty_spawn,
            terminal::pty_write,
            terminal::pty_read,
            terminal::pty_resize,
            terminal::pty_kill,
            terminal::pty_exitstatus,
            terminal::pty_get_all_pids,
            // One-tap screen capture (app-level — see screen_capture.rs)
            screen_capture::screen_capture_current_display,
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
                        // Reap any live integrated-terminal PTY children (AC6 backstop
                        // for a hard window close where the frontend beforeunload
                        // closeAll didn't run). Runs before the backend shutdown.
                        let term_state = app_handle.state::<TerminalState>();
                        tauri::async_runtime::block_on(terminal::reap_all_ptys(
                            term_state.inner(),
                        ));
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

// ── Tests: adaptive cold-start probe decision logic ─────────────────────────
// These exercise the REAL pure functions used by probe_daemon_health_adaptive
// (classify_probe_outcome + probe_loop_decision) — no local re-derivation of the
// prod logic (avoids test-theater: reverting the prod fn must turn these RED).
#[cfg(all(test, target_os = "macos"))]
mod adaptive_probe_tests {
    use super::*;

    // AC1: classify_probe_outcome maps the 4 (healthy,pid) combinations correctly.
    #[test]
    fn classify_healthy_is_ready_regardless_of_pid() {
        assert_eq!(classify_probe_outcome(true, true), ProbeOutcome::Ready);
        assert_eq!(classify_probe_outcome(true, false), ProbeOutcome::Ready);
    }

    #[test]
    fn classify_not_healthy_but_pid_present_is_alive() {
        assert_eq!(classify_probe_outcome(false, true), ProbeOutcome::Alive);
    }

    #[test]
    fn classify_not_healthy_no_pid_is_dead() {
        assert_eq!(classify_probe_outcome(false, false), ProbeOutcome::Dead);
    }

    // AC5: a Ready outcome always succeeds, even far past the old 60s boundary —
    // the false-fatal is gone (a slow backend that becomes healthy late still wins).
    #[test]
    fn ready_late_still_succeeds_past_old_60s_cap() {
        let d = probe_loop_decision(ProbeOutcome::Ready, 120, 0, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, true);
        assert_eq!(d, LoopDecision::Succeed);
    }

    // AC2: an Alive process keeps waiting well past 60s (adaptive, not fixed cap)...
    #[test]
    fn alive_keeps_waiting_past_60s_until_ceiling() {
        let d = probe_loop_decision(ProbeOutcome::Alive, 90, 0, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, true);
        assert_eq!(d, LoopDecision::Continue);
    }

    // ...but is bounded: an Alive process still not serving at the ceiling gives up
    // (O030 disaster-recovery backstop for a genuine lifespan deadlock).
    #[test]
    fn alive_at_ceiling_fails_ceiling() {
        let d = probe_loop_decision(ProbeOutcome::Alive, COLD_START_CEILING_SECS, 0, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, true);
        assert_eq!(d, LoopDecision::FailCeiling);
    }

    // ── Runtime watchdog down-decision (run_13094a88, false-offline root-fix) ──
    // These pass heartbeat_fresh=false (no heartbeat file — the legacy path), so
    // they ALSO prove zero-regression: an old binary without a heartbeat file
    // behaves EXACTLY as before the run_5b0d6ec3 heartbeat override was added.
    // AC1: a LIVE daemon on the FIRST miss is DEGRADED, never Terminated — the
    // single-3s-stall false-offline is structurally impossible now.
    #[test]
    fn down_first_miss_alive_daemon_is_degraded() {
        assert_eq!(
            watchdog_down_decision(true, true, 1, 2, false, false),
            WatchdogDownDecision::Degraded
        );
    }

    // AC3: a LIVE daemon still unreachable at the escalation threshold IS declared
    // terminated (a genuine persistent outage, not a blip) — WHEN there is no
    // fresh heartbeat to prove it's merely busy (legacy path).
    #[test]
    fn down_streak_reaches_threshold_terminates() {
        assert_eq!(
            watchdog_down_decision(true, true, 2, 2, false, false),
            WatchdogDownDecision::Terminated
        );
    }

    // AC3: a GONE process on the very first miss is terminated immediately —
    // liveness, not the counter, is the primary death signal.
    #[test]
    fn down_pid_gone_terminates_immediately() {
        assert_eq!(
            watchdog_down_decision(true, false, 1, 2, false, false),
            WatchdogDownDecision::Terminated
        );
    }

    // AC5: subprocess-fallback mode (is_daemon=false) has no launchctl signal, so
    // it preserves the pre-fix straight-to-terminated behavior regardless of pid
    // (and regardless of any heartbeat — subprocess mode has no heartbeat file).
    #[test]
    fn down_subprocess_mode_always_terminates() {
        assert_eq!(
            watchdog_down_decision(false, true, 1, 2, false, false),
            WatchdogDownDecision::Terminated
        );
        assert_eq!(
            watchdog_down_decision(false, false, 1, 2, false, false),
            WatchdogDownDecision::Terminated
        );
        // Even a fresh heartbeat cannot flip subprocess mode — the is_daemon guard
        // returns first (there is no launchd-managed process to protect).
        assert_eq!(
            watchdog_down_decision(false, true, 1, 2, true, false),
            WatchdogDownDecision::Terminated
        );
    }

    // ── Heartbeat override (run_5b0d6ec3 — the architectural root fix) ──
    // CORE: a fresh heartbeat + non-wedged loop keeps a daemon Degraded EVEN AT
    // the escalation threshold. This is the structural kill of the residual
    // false-offline: "busy loop, 2+ missed probes" can no longer become death.
    #[test]
    fn hb_fresh_nonwedged_blocks_terminate_at_threshold() {
        // Same inputs as down_streak_reaches_threshold_terminates (would be
        // Terminated on the legacy path) — the fresh heartbeat flips it.
        assert_eq!(
            watchdog_down_decision(true, true, 2, 2, true, false),
            WatchdogDownDecision::Degraded
        );
        // Even far past the threshold, a busy-but-alive daemon stays Degraded.
        assert_eq!(
            watchdog_down_decision(true, true, 99, 2, true, false),
            WatchdogDownDecision::Degraded
        );
    }

    // A fresh heartbeat protects even when launchctl says the pid is GONE: the
    // heartbeat writer thread ticking is a STRONGER liveness proof than a
    // transient launchctl miss (the whole reason it exists). Busy ≠ dead.
    #[test]
    fn hb_fresh_nonwedged_overrides_pid_gone() {
        assert_eq!(
            watchdog_down_decision(true, false, 1, 2, true, false),
            WatchdogDownDecision::Degraded
        );
    }

    // WEDGE: a fresh heartbeat but a WEDGED loop (loop_age ≥ 15s) is a genuine
    // deadlock — the override does NOT protect it; it falls through to the legacy
    // path. Below threshold → still Degraded; at threshold → real Terminated.
    // A loop wedged ≥15s by an outside shortage (CPU / memory / low battery) is a
    // starved live process: not wedged for the decision → Degraded at any streak.
    // A wedge in our own code (or of unknown cause) still escalates.
    #[test]
    fn starved_wedge_stays_degraded_own_wedge_escalates() {
        let starved = HeartbeatView {
            fresh: true, loop_wedged: true, loop_age: Some(40.0),
            stall: Some(serde_json::json!({"cause": "cpu", "starved": true, "loadPerCore": 12.9})),
            pid: None,
        };
        assert!(!effective_wedged(&starved));
        assert_eq!(
            watchdog_down_decision(true, true, 99, 2, starved.fresh, effective_wedged(&starved)),
            WatchdogDownDecision::Degraded
        );
        let own = HeartbeatView {
            stall: Some(serde_json::json!({"cause": "busy", "starved": false, "busyWith": "core/x.py:f"})),
            ..starved.clone()
        };
        assert!(effective_wedged(&own));
        assert_eq!(
            watchdog_down_decision(true, true, 2, 2, own.fresh, effective_wedged(&own)),
            WatchdogDownDecision::Terminated
        );
        let no_diag = HeartbeatView { stall: None, ..starved.clone() };
        assert!(effective_wedged(&no_diag)); // an older backend: unchanged behavior
    }

    #[test]
    fn watchdog_log_line_format_and_rotation() {
        assert_eq!(utc_timestamp(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(utc_timestamp(1_792_588_445_024), "2026-10-21T13:14:05.024Z");
        let dir = std::env::temp_dir().join(format!("swarm-wd-{}", std::process::id()));
        let path = dir.join("logs").join("watchdog.log");
        let _ = std::fs::remove_dir_all(&dir);
        append_watchdog_log(&path, "Watchdog: first").unwrap();
        append_watchdog_log(&path, "Watchdog: second").unwrap();
        let body = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = body.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].ends_with("Z Watchdog: first") && lines[0].starts_with("20"));
        // over the cap → moved to watchdog.log.1, a fresh file starts
        std::fs::write(&path, vec![b'x'; WATCHDOG_LOG_MAX_BYTES as usize]).unwrap();
        append_watchdog_log(&path, "Watchdog: after rotation").unwrap();
        assert!(std::fs::read_to_string(&path).unwrap().ends_with("Watchdog: after rotation\n"));
        assert_eq!(std::fs::metadata(path.with_extension("log.1")).unwrap().len(), WATCHDOG_LOG_MAX_BYTES);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn watchdog_evidence_names_the_cause() {
        let hb = HeartbeatView {
            fresh: true, loop_wedged: true, loop_age: Some(40.25),
            stall: Some(serde_json::json!({"cause": "cpu", "starved": true, "loadPerCore": 12.9,
                "topCpu": [{"name": "python3.12", "cpu": 340}]})),
            pid: None,
        };
        let e = watchdog_evidence(&hb, Some(true));
        assert_eq!(e, "pid_present=true hb_fresh=true loop_age=40.2s wedged=true cause=cpu starved=true load=12.9 top_cpu=python3.12:340%");
        assert_eq!(stall_cause(&hb), "cpu");
        let none = watchdog_evidence(&HeartbeatView::default(), None);
        assert_eq!(none, "pid_present=n/a hb_fresh=false loop_age=- wedged=false");
        assert_eq!(stall_cause(&HeartbeatView::default()), "-");
    }

    #[test]
    fn health_payload_carries_the_reason() {
        let hb = HeartbeatView {
            fresh: true, loop_wedged: false, loop_age: Some(7.5),
            stall: Some(serde_json::json!({"cause": "memory", "starved": true, "memPercent": 95.0})),
            pid: Some(42),
        };
        let p = health_event_payload(&hb, Some(true), Some(42), None);
        assert_eq!(p["port"], serde_json::json!(DAEMON_PORT));
        assert_eq!(p["heartbeatFresh"], serde_json::json!(true));
        assert_eq!(p["loopAge"], serde_json::json!(7.5));
        assert_eq!(p["processAlive"], serde_json::json!(true));
        assert_eq!(p["stall"]["cause"], serde_json::json!("memory"));
        assert_eq!(p["suspended"], serde_json::json!(false)); // fresh: not suspended
        let gone = health_event_payload(&HeartbeatView::default(), Some(false), None, None);
        assert_eq!(gone["heartbeatFresh"], serde_json::json!(false));
        assert!(gone["stall"].is_null() && gone["loopAge"].is_null());
    }

    // A stale heartbeat whose writer is the pid launchd still runs is a suspended
    // process (low battery / sleep): Degraded at any streak, never Terminated. A
    // different launchd pid (a restart) or no pid keeps the old escalation.
    #[test]
    fn suspended_process_stays_degraded_a_restart_does_not() {
        let stale = HeartbeatView { pid: Some(4242), ..HeartbeatView::default() };
        assert!(suspended(&stale, Some(4242)));
        let short = Some(std::time::Duration::from_secs(SUSPEND_MAX_SECS - 1));
        let (f, w) = decision_inputs(&stale, Some(4242), short);
        assert_eq!(watchdog_down_decision(true, true, 99, 2, f, w), WatchdogDownDecision::Degraded);
        assert_eq!(health_event_payload(&stale, Some(true), Some(4242), short)["suspended"], serde_json::json!(true));
        // Past the cap (awake time) a "suspension" is treated as stuck: the old
        // escalation applies and the UI no longer says paused.
        let long = Some(std::time::Duration::from_secs(SUSPEND_MAX_SECS));
        let (f, w) = decision_inputs(&stale, Some(4242), long);
        assert_eq!(watchdog_down_decision(true, true, 2, 2, f, w), WatchdogDownDecision::Terminated);
        let p = health_event_payload(&stale, Some(true), Some(4242), long);
        assert_eq!(p["suspended"], serde_json::json!(false));
        assert_eq!(p["suspendedSecs"], serde_json::json!(SUSPEND_MAX_SECS));
        for other in [Some(5000), None] {
            assert!(!suspended(&stale, other));
            let (f, w) = decision_inputs(&stale, other, None);
            assert_eq!(watchdog_down_decision(true, true, 2, 2, f, w), WatchdogDownDecision::Terminated);
        }
        let unknown_writer = HeartbeatView::default();
        assert!(!suspended(&unknown_writer, Some(4242)));
    }

    #[test]
    fn suspension_clock_starts_once_and_resets_when_it_ends() {
        let stale = HeartbeatView { pid: Some(7), ..HeartbeatView::default() };
        let mut since = None;
        let a = track_suspension(&mut since, &stale, Some(7)).unwrap();
        let first = since;
        std::thread::sleep(std::time::Duration::from_millis(20));
        let b = track_suspension(&mut since, &stale, Some(7)).unwrap();
        assert_eq!(since, first);            // the start is kept
        assert!(b > a);
        assert!(track_suspension(&mut since, &HeartbeatView::default(), Some(7)).is_none());
        assert!(since.is_none());            // a fresh heartbeat (or restart) resets it
    }

    #[test]
    fn hb_fresh_wedged_falls_through_to_legacy() {
        // Wedged + below threshold + pid present → legacy says Degraded.
        assert_eq!(
            watchdog_down_decision(true, true, 1, 2, true, true),
            WatchdogDownDecision::Degraded
        );
        // Wedged + at threshold → the genuine deadlock IS declared terminated.
        assert_eq!(
            watchdog_down_decision(true, true, 2, 2, true, true),
            WatchdogDownDecision::Terminated
        );
        // Wedged + pid gone → terminated immediately (both signals say dead).
        assert_eq!(
            watchdog_down_decision(true, false, 1, 2, true, true),
            WatchdogDownDecision::Terminated
        );
    }

    // Recovery-poll event contract with useHealthMonitor.ts: while the decision
    // stays Degraded the watchdog keeps emitting `backend-degraded` (refreshing
    // the frontend's 10s liveness window); only a real death escalates.
    #[test]
    fn recovery_poll_reemits_degraded_while_alive() {
        // busy daemon far past the threshold: decision stays Degraded → degraded event
        let d = watchdog_down_decision(true, true, 9, 2, true, false);
        assert_eq!(d, WatchdogDownDecision::Degraded);
        assert_eq!(recovery_poll_event(d), "backend-degraded");
        // stale heartbeat at threshold → real death → terminated event
        let d = watchdog_down_decision(true, true, 2, 2, false, false);
        assert_eq!(recovery_poll_event(d), "backend-terminated-restarting");
    }

    // The bundled plist template must run the daemon at Standard priority:
    // Background starved the loop + heartbeat thread under load (false offline).
    #[test]
    fn bundled_plist_template_is_standard_priority_and_renders() {
        let tpl = include_str!("../../resources/daemon/com.swarmai.backend.plist.template");
        let out = render_daemon_plist(tpl, "/h/.swarm-ai/swarmai_backend.sh", "/h/.swarm-ai/logs", "/h");
        assert!(!out.contains("__WRAPPER_PATH__") && !out.contains("__LOG_DIR__") && !out.contains("__HOME__"));
        let compact: String = out.split_whitespace().collect();
        assert!(compact.contains("<key>ProcessType</key><string>Standard</string>"),
            "daemon plist must use ProcessType=Standard");
        assert!(!compact.contains("<string>Background</string>"));
    }

    // STALE heartbeat (missing file / old binary) is inert — the decision is
    // identical to the legacy 4-arg behavior. This is the zero-regression proof.
    #[test]
    fn hb_stale_is_inert_legacy_behavior() {
        // fresh=false → same as down_streak_reaches_threshold_terminates.
        assert_eq!(
            watchdog_down_decision(true, true, 2, 2, false, false),
            WatchdogDownDecision::Terminated
        );
        assert_eq!(
            watchdog_down_decision(true, true, 1, 2, false, false),
            WatchdogDownDecision::Degraded
        );
    }

    // AC3: a truly-dead daemon (that WAS alive, then crashed) fails FAST once the
    // dead streak is reached — well under the ceiling.
    #[test]
    fn dead_streak_fails_fast_before_ceiling() {
        // ever_alive=true (was up, now gone) + streak reached → FailDead
        let d = probe_loop_decision(ProbeOutcome::Dead, 20, COLD_START_DEAD_STREAK, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, true);
        assert_eq!(d, LoopDecision::FailDead);
        // sanity: dead streak * interval is far below the ceiling
        assert!((COLD_START_DEAD_STREAK as u64) * PROBE_INTERVAL_SECS < COLD_START_CEILING_SECS);
    }

    // AC3 edge: a SINGLE transient no-process observation (below the streak) does
    // NOT fail — rides over a KeepAlive respawn (ThrottleInterval=10s < streak*interval).
    #[test]
    fn dead_below_streak_keeps_waiting() {
        let d = probe_loop_decision(ProbeOutcome::Dead, 4, 1, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, true);
        assert_eq!(d, LoopDecision::Continue);
        // the streak window must exceed the plist ThrottleInterval (10s) so a
        // legitimate respawn gap can't trip fail-fast
        assert!((COLD_START_DEAD_STREAK as u64) * PROBE_INTERVAL_SECS > 10);
    }

    // META-REVIEW HIGH regression guard: a daemon that has NEVER been seen alive
    // (ever_alive=false — still inside the launchd bootstrap→spawn window on a
    // disk-pressured first launch) must NOT fail fast even past the dead streak.
    // Failing fast here would re-introduce the exact false-fatal this change fixes.
    #[test]
    fn never_started_does_not_fail_fast_below_ceiling() {
        // streak far exceeded, but ever_alive=false and below ceiling → keep waiting
        let d = probe_loop_decision(ProbeOutcome::Dead, 40, COLD_START_DEAD_STREAK * 3, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, false);
        assert_eq!(d, LoopDecision::Continue);
    }

    // ...and a never-started daemon is still bounded by the absolute ceiling.
    #[test]
    fn never_started_fails_ceiling_not_deadstreak() {
        let d = probe_loop_decision(ProbeOutcome::Dead, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK * 5, COLD_START_CEILING_SECS, COLD_START_DEAD_STREAK, false);
        assert_eq!(d, LoopDecision::FailCeiling);
    }
}
