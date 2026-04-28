use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use tauri::{Emitter, Manager};
use tauri::webview::WebviewWindowBuilder;
use tauri::utils::config::WebviewUrl;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;
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

/// Fixed port for daemon mode. When a launchd-managed backend is already
/// listening on this port, Tauri connects to it instead of spawning a sidecar.
const DAEMON_PORT: u16 = 18321;
const DAEMON_PLIST_RELPATH: &str = "Library/LaunchAgents/com.swarmai.backend.plist";

// Backend state management
struct BackendState {
    child: Option<CommandChild>,
    port: u16,
    running: bool,
    pid: Option<u32>,  // Store PID for process tree cleanup on Windows
    /// Set to `true` when shutdown is intentional (stop_backend, window close, app exit).
    /// Prevents the terminated-event handler from auto-restarting.
    intentional_shutdown: bool,
    /// Set to `true` when connected to an external daemon (not our sidecar).
    /// When true, Tauri must NOT kill the backend on exit.
    is_daemon_mode: bool,
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            child: None,
            port: 8000,
            running: false,
            pid: None,
            intentional_shutdown: false,
            is_daemon_mode: false,
        }
    }
}

/// Maximum time (seconds) to wait for the backend to complete shutdown.
/// Covers: HookContext build (~1s) + DailyActivity batch (~5s) + drain (~8s).
/// The curl/PowerShell timeout is set to this value.
const SHUTDOWN_GRACE_SECONDS: u64 = 10;

/// Maximum number of automatic restart attempts before giving up.
/// Prevents infinite restart loops when the backend consistently crashes.
const MAX_AUTO_RESTARTS: u32 = 3;

/// Time window (seconds) for counting restart attempts.
/// Restart counter resets if the backend survives longer than this.
const RESTART_WINDOW_SECS: u64 = 60;

/// Post-shutdown sleep (seconds) for the manual `stop_backend` command.
/// Proportional to SHUTDOWN_GRACE_SECONDS for manual stop operations.
const STOP_BACKEND_SLEEP_SECONDS: u64 = 5;

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

// On Unix systems (macOS/Linux), kill the process tree using pkill
#[cfg(not(target_os = "windows"))]
fn kill_process_tree(pid: u32) {
    // First, kill all child processes recursively using pkill -P
    // This sends SIGTERM to all processes whose parent PID matches
    let _ = std::process::Command::new("pkill")
        .args(["-TERM", "-P", &pid.to_string()])
        .output();

    // Give child processes a moment to terminate gracefully
    std::thread::sleep(std::time::Duration::from_millis(100));

    // Force kill any remaining child processes
    let _ = std::process::Command::new("pkill")
        .args(["-KILL", "-P", &pid.to_string()])
        .output();

    // Finally, kill the parent process itself
    let _ = std::process::Command::new("kill")
        .args(["-9", &pid.to_string()])
        .output();

    println!("Killed process tree for PID: {}", pid);
}

type SharedBackendState = Arc<Mutex<BackendState>>;

#[derive(Serialize, Deserialize)]
pub struct BackendStatus {
    running: bool,
    port: u16,
    is_daemon_mode: bool,
}

/// Handle sidecar stdout/stderr/terminated events in a loop.
///
/// On unexpected termination (intentional_shutdown == false), waits 2 seconds
/// then spawns a fresh sidecar on a new port and loops back to handle the new
/// receiver. Uses iteration (not recursion) to avoid non-Send future issues.
///
/// Restart cap: at most `MAX_AUTO_RESTARTS` within a `RESTART_WINDOW_SECS`
/// sliding window. If the backend survives beyond the window, the counter
/// resets — so a one-off crash after hours of uptime gets a fresh budget.
async fn handle_sidecar_output(
    rx: tauri::async_runtime::Receiver<tauri_plugin_shell::process::CommandEvent>,
    app_handle: tauri::AppHandle,
    state: SharedBackendState,
) {
    use tauri_plugin_shell::process::CommandEvent;

    let mut current_rx = rx;
    let mut restart_count: u32 = 0;
    let mut window_start = std::time::Instant::now();

    loop {
        let mut should_restart = false;

        while let Some(event) = current_rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let _ = app_handle.emit("backend-log", String::from_utf8_lossy(&line).to_string());
                }
                CommandEvent::Stderr(line) => {
                    let _ = app_handle.emit("backend-error", String::from_utf8_lossy(&line).to_string());
                }
                CommandEvent::Terminated(payload) => {
                    let was_intentional = {
                        let mut backend = state.lock().await;
                        let intentional = backend.intentional_shutdown;
                        backend.running = false;
                        backend.child = None;
                        backend.pid = None;
                        intentional
                    };

                    if was_intentional {
                        println!("[Tauri] Backend terminated intentionally (exit code: {:?})", payload.code);
                        let _ = app_handle.emit("backend-terminated", payload.code);
                        return; // Done — no restart needed
                    }

                    // --- Restart budget check ---
                    // Reset counter if the backend survived past the window
                    if window_start.elapsed().as_secs() > RESTART_WINDOW_SECS {
                        restart_count = 0;
                        window_start = std::time::Instant::now();
                    }

                    restart_count += 1;
                    if restart_count > MAX_AUTO_RESTARTS {
                        eprintln!(
                            "[Tauri] Backend crashed {} times within {}s — giving up auto-restart",
                            restart_count, RESTART_WINDOW_SECS
                        );
                        let _ = app_handle.emit("backend-terminated", payload.code);
                        return; // Exhausted restart budget
                    }

                    // Unexpected death (e.g. killed by external pkill, OOM, crash)
                    println!(
                        "[Tauri] Backend terminated UNEXPECTEDLY (exit code: {:?}) — auto-restart {}/{}",
                        payload.code, restart_count, MAX_AUTO_RESTARTS
                    );
                    let _ = app_handle.emit("backend-terminated-restarting", payload.code);

                    // Exponential backoff: 2s, 4s, 8s for attempts 1, 2, 3
                    let backoff_secs = 2u64.pow(restart_count);
                    tokio::time::sleep(tokio::time::Duration::from_secs(backoff_secs)).await;

                    // Auto-restart: pick new port, re-spawn sidecar
                    let new_port = match portpicker::pick_unused_port() {
                        Some(p) => p,
                        None => {
                            eprintln!("[Tauri] No available port for backend restart — giving up");
                            let _ = app_handle.emit("backend-terminated", payload.code);
                            return;
                        }
                    };
                    let enhanced_path = get_enhanced_path();

                    match app_handle
                        .shell()
                        .sidecar("python-backend")
                        .map(|cmd| cmd.args(["--port", &new_port.to_string()]).env("PATH", &enhanced_path))
                    {
                        Ok(sidecar_cmd) => match sidecar_cmd.spawn() {
                            Ok((new_rx, new_child)) => {
                                let new_pid = new_child.pid();
                                {
                                    let mut backend = state.lock().await;
                                    backend.child = Some(new_child);
                                    backend.port = new_port;
                                    backend.running = true;
                                    backend.pid = Some(new_pid);
                                    backend.intentional_shutdown = false;
                                }
                                println!("[Tauri] Backend auto-restarted on port {} (PID: {})", new_port, new_pid);
                                let _ = app_handle.emit("backend-restarted", new_port);

                                // Loop back with the new receiver
                                current_rx = new_rx;
                                should_restart = true;
                            }
                            Err(e) => {
                                eprintln!("[Tauri] Failed to auto-restart backend: {}", e);
                                let _ = app_handle.emit("backend-terminated", payload.code);
                                return; // Give up
                            }
                        },
                        Err(e) => {
                            eprintln!("[Tauri] Failed to create sidecar command for restart: {}", e);
                            let _ = app_handle.emit("backend-terminated", payload.code);
                            return; // Give up
                        }
                    }
                    break; // Break inner loop to restart outer loop with new rx
                }
                _ => {}
            }
        }

        if !should_restart {
            return; // Receiver closed without termination event — nothing to do
        }
    }
}

/// Gracefully shut down the backend and then force-kill as safety net.
///
/// Mirrors the `stop_backend` command pattern:
/// 1. Capture state under lock, mark as not running
/// 2. Release lock before blocking I/O
/// 3. If was running: send_shutdown_request (curl timeout = 10s IS the grace period)
/// 4. Force kill process tree + child as safety net
///
/// Double-fire safe: if `backend.running` is already false (set by a
/// previous handler in the same close sequence), skips the shutdown
/// request and sleep, proceeding directly to force-kill.
fn graceful_shutdown_and_kill(state: SharedBackendState, context: &str) {
    tauri::async_runtime::block_on(async {
        let mut backend = state.lock().await;
        let was_running = backend.running;
        let port = backend.port;
        let pid = backend.pid;
        let child = backend.child.take();
        let is_daemon = backend.is_daemon_mode;

        // Mark as intentional + not running under lock — prevents double-fire and auto-restart
        backend.intentional_shutdown = true;
        backend.running = false;
        backend.pid = None;
        drop(backend); // Release lock before blocking I/O

        // Daemon mode: do NOT kill the backend — it's an external process that
        // should keep running after Tauri exits (channels, jobs stay alive).
        if is_daemon {
            println!("[{}] Backend is in daemon mode — leaving it running on port {}", context, port);
            return;
        }

        // Graceful shutdown only if backend was actually running
        if was_running {
            println!("[{}] Attempting graceful shutdown on port {}", context, port);
            // Fast-path: curl timeout (10s = SHUTDOWN_GRACE_SECONDS) serves as the
            // grace period. If curl succeeds, backend already completed disconnect_all().
            // If curl times out, 10s has already elapsed. No additional sleep needed.
            // Return value intentionally ignored — both paths proceed to force-kill.
            send_shutdown_request(port);
        }

        // Force kill as safety net (always, even if shutdown request succeeded)
        if let Some(pid) = pid {
            kill_process_tree(pid);
            println!("[{}] Killed backend process tree (PID: {})", context, pid);
        }

        if let Some(child) = child {
            let _ = child.kill();
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
/// Returns Ok(()) on success, Err on failure (caller should fall back to sidecar).
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
    let _ = app.emit("backend-upgrading", format!("{} → {}", daemon_version, app_version));

    // Step 1: Graceful shutdown
    send_shutdown_request(DAEMON_PORT);
    tokio::time::sleep(tokio::time::Duration::from_secs(SHUTDOWN_GRACE_SECONDS)).await;

    // Step 2: Atomic binary deploy from app bundle
    let home = std::env::var("HOME").unwrap_or_default();
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

    // Atomic deploy: copy to .tmp, then rename
    std::fs::copy(&bundled_binary, &tmp_binary)
        .map_err(|e| format!("Failed to copy binary: {}", e))?;
    std::fs::rename(&tmp_binary, &target_binary)
        .map_err(|e| format!("Failed to rename binary: {}", e))?;

    // Set executable permissions
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(
            &target_binary,
            std::fs::Permissions::from_mode(0o755),
        );
    }

    // Write version file (use date command for timestamp — avoids chrono dep)
    let timestamp = std::process::Command::new("date")
        .arg("+%Y-%m-%d %H:%M:%S")
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|_| "unknown".to_string());
    let version_content = format!("{} {}", app_version, timestamp);
    let _ = std::fs::write(format!("{}/.version", daemon_dir), version_content);

    println!("[Tauri] Daemon binary deployed: {}", target_binary);

    // Step 3: Restart daemon via launchd
    let uid_output = std::process::Command::new("id").arg("-u").output()
        .map_err(|e| format!("Failed to get UID: {}", e))?;
    let uid = String::from_utf8_lossy(&uid_output.stdout).trim().to_string();
    let gui_target = format!("gui/{}", uid);
    let plist_path = format!("{}/{}", home, DAEMON_PLIST_RELPATH);

    // bootout (stop) + bootstrap (start)
    let _ = std::process::Command::new("launchctl")
        .args(["bootout", &gui_target, &plist_path])
        .output();
    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
    let _ = std::process::Command::new("launchctl")
        .args(["bootstrap", &gui_target, &plist_path])
        .output();

    // Step 4: Verify new version
    if let Some(_port) = probe_daemon_health(10, 2).await {
        let new_version = get_daemon_version().await.unwrap_or_default();
        if new_version == app_version {
            println!("[Tauri] Daemon upgraded successfully: {}", app_version);
            let _ = app.emit("backend-upgraded", app_version);
            return Ok(());
        }
        println!(
            "[Tauri] Daemon running but version still {}, expected {}",
            new_version, app_version
        );
    }

    Err("Daemon upgrade failed — daemon not responding after restart".to_string())
}

/// Ensure the daemon plist is installed and bootstrapped into launchd.
/// Idempotent: safe to call when already bootstrapped (exit 37 is OK).
async fn ensure_daemon_bootstrapped() -> Result<(), String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let plist_path = format!("{}/{}", home, DAEMON_PLIST_RELPATH);

    // If plist doesn't exist, we can't bootstrap. The install_backend_daemon.py
    // script handles plist generation, but it requires the Python backend venv.
    // For now, if plist is missing, we fall back to sidecar.
    if !std::path::Path::new(&plist_path).exists() {
        return Err(format!("Daemon plist not found: {}", plist_path));
    }

    // Get UID via id -u (portable, no libc dependency)
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
            // Code 5 can mean "already loaded" (Ventura+) or genuine I/O error.
            // Code 37 = "already bootstrapped". Verify with launchctl list.
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
/// When Tauri connects to an external daemon (not a sidecar it owns),
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

            // Check if we're still in daemon mode (user might have stopped backend)
            {
                let backend = state.lock().await;
                if !backend.is_daemon_mode || !backend.running {
                    println!("[Tauri] Daemon watchdog: no longer in daemon mode — exiting");
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

// Start the Python backend sidecar
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

    // ── Daemon-first architecture ──────────────────────────────────────
    // Phase 1: Probe for existing daemon (handles 99% case: already running)
    // Phase 2: If not running, auto-bootstrap via launchd
    // Phase 3: Fallback to sidecar only if daemon cannot start
    {
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
                    backend.child = None;
                    backend.pid = None;
                }
                // Start background health watchdog (10s interval for faster detection)
                spawn_daemon_health_watchdog(app_clone.clone(), state_inner, 10);
                // Notify frontend of backend mode for UI display / onboarding
                let _ = app_clone.emit("backend-mode", "daemon");
                DAEMON_PORT
            }
        };

        // Phase 1: probe with retry (daemon might be mid-restart via ThrottleInterval)
        if let Some(_port) = probe_daemon_health(5, 2).await {
            println!("[Tauri] Found existing daemon on port {} — connecting", DAEMON_PORT);

            // Version sync: upgrade daemon binary if it doesn't match app version
            let app_version = app.config().version.clone().unwrap_or_default();
            if !app_version.is_empty() {
                match sync_daemon_version(&app, &app_version).await {
                    Ok(()) => {
                        // Either versions matched or upgrade succeeded
                    }
                    Err(e) => {
                        // Upgrade failed — connect to existing daemon anyway
                        // (stale daemon is better than no daemon)
                        println!("[Tauri] Daemon version sync failed: {} — using existing daemon", e);
                    }
                }
            }

            let port = connect_daemon(&state, &app).await;
            return Ok(port);
        }

        // Phase 2: daemon not running — attempt auto-bootstrap
        println!("[Tauri] No daemon found — attempting auto-bootstrap");
        if let Ok(()) = ensure_daemon_bootstrapped().await {
            // Wait for daemon to come up (cold start can take a few seconds)
            if let Some(_port) = probe_daemon_health(10, 2).await {
                println!("[Tauri] Daemon bootstrapped and healthy on port {}", DAEMON_PORT);
                let port = connect_daemon(&state, &app).await;
                return Ok(port);
            }
            println!("[Tauri] Daemon bootstrapped but not responding — falling back to sidecar");
        } else {
            println!("[Tauri] Daemon bootstrap failed — falling back to sidecar");
        }
    }

    // Phase 3: Sidecar fallback — only when daemon plist does NOT exist.
    // When the plist exists, the daemon is the intended backend. Spawning a
    // sidecar alongside it causes two backends competing for SQLite,
    // backend.json, and workspace git.  Return an error instead so the user
    // can diagnose the daemon issue (./dev.sh daemon logs).
    {
        let home = std::env::var("HOME").unwrap_or_default();
        let plist_path = format!("{}/{}", home, DAEMON_PLIST_RELPATH);
        if std::path::Path::new(&plist_path).exists() {
            return Err(format!(
                "Daemon is installed but not responding on port {}. \
                 Check daemon logs: ~/.swarm-ai/logs/backend-stderr.log \
                 or restart with: ./dev.sh daemon restart",
                DAEMON_PORT,
            ));
        }
    }

    // Find an available port
    let port = portpicker::pick_unused_port()
        .ok_or_else(|| "No available port for backend".to_string())?;

    // Get enhanced PATH for the sidecar
    let enhanced_path = get_enhanced_path();

    // Start the sidecar with enhanced environment
    let sidecar = app
        .shell()
        .sidecar("python-backend")
        .map_err(|e| format!("Failed to create sidecar command: {}", e))?
        .args(["--port", &port.to_string()])
        .env("PATH", enhanced_path);

    let (rx, child) = sidecar
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    // Get PID for process tree cleanup on Windows
    let pid = child.pid();

    // Store the child process (short lock)
    {
        let mut backend = state.lock().await;
        backend.child = Some(child);
        backend.port = port;
        backend.running = true;
        backend.pid = Some(pid);
        backend.intentional_shutdown = false;
    }

    // Delegate sidecar output + auto-restart handling to the standalone function
    let app_handle = app.clone();
    let state_clone = state.inner().clone();
    tauri::async_runtime::spawn(async move {
        handle_sidecar_output(rx, app_handle, state_clone).await;
    });

    // Poll the /health endpoint until the backend reports "healthy"
    let health_url = format!("http://127.0.0.1:{}/health", port);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| format!("Failed to create HTTP client: {}", e))?;

    let max_attempts = 30; // 30 attempts × 2s = 60s total timeout
    for attempt in 1..=max_attempts {
        tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;

        match client.get(&health_url).send().await {
            Ok(resp) => {
                if let Ok(body) = resp.text().await {
                    let (is_healthy, _, _) = parse_health_response(&body);
                    if is_healthy {
                        println!("[Tauri] Backend healthy after {} attempts", attempt);
                        // Notify frontend: running in sidecar mode (no 24/7 daemon)
                        let _ = app.emit("backend-mode", "sidecar");
                        return Ok(port);
                    }
                    println!(
                        "[Tauri] Backend not ready (attempt {}/{}): {}",
                        attempt, max_attempts, body
                    );
                }
            }
            Err(e) => {
                println!(
                    "[Tauri] Health check attempt {}/{} failed: {}",
                    attempt, max_attempts, e
                );
            }
        }

        // Check if backend process is still alive
        let backend = state.lock().await;
        if !backend.running {
            return Err("Backend process terminated during startup".to_string());
        }
    }

    Err(format!(
        "Backend failed to become healthy within {} seconds",
        max_attempts * 2
    ))
}

// Stop the Python backend
#[tauri::command]
async fn stop_backend(state: tauri::State<'_, SharedBackendState>) -> Result<(), String> {
    // Step 1: Capture all needed state under lock, then release
    // This avoids race conditions from dropping and re-acquiring the lock
    let (was_running, port, pid, child) = {
        let mut backend = state.lock().await;
        let was_running = backend.running;
        let port = backend.port;
        let pid = backend.pid;
        let child = backend.child.take();

        // Mark as intentional + not running to prevent auto-restart and concurrent operations
        backend.intentional_shutdown = true;
        backend.running = false;
        backend.pid = None;

        (was_running, port, pid, child)
    };
    // Lock is now released

    // Step 2: Try graceful shutdown via HTTP request (all platforms)
    // This allows the backend to properly terminate Claude CLI child processes
    if was_running {
        let success = send_shutdown_request(port);
        if !success {
            // Backend didn't respond — give it extra time before force-killing
            tokio::time::sleep(tokio::time::Duration::from_secs(STOP_BACKEND_SLEEP_SECONDS)).await;
        }
        // Fast path: if shutdown request succeeded, skip sleep — backend already cleaned up
    }

    // Step 3: Kill the entire process tree (works on all platforms)
    if let Some(pid) = pid {
        kill_process_tree(pid);
    }

    if let Some(child) = child {
        let _ = child.kill(); // Also try normal kill as fallback
    }

    // Step 4: On Windows, wait for processes to fully exit to release file handles
    // This is important for updates where the installer needs to overwrite the exe
    #[cfg(target_os = "windows")]
    if let Some(pid) = pid {
        wait_for_processes_exit(pid).await;
    }

    Ok(())
}

// Wait for processes to exit on Windows (checks both python-backend and claude.exe)
#[cfg(target_os = "windows")]
async fn wait_for_processes_exit(backend_pid: u32) {
    use std::time::Duration;

    // Process names to check - Claude CLI processes may outlive the Python backend
    // Note: On Windows, tasklist requires the full executable name with .exe extension
    let process_names = ["python-backend.exe", "claude.exe"];

    // Try up to 20 times with 500ms delay (10 seconds total)
    for i in 0..20 {
        let mut any_running = false;

        // Check each process name
        for process_name in &process_names {
            let output = std::process::Command::new("tasklist")
                .args(["/FI", &format!("IMAGENAME eq {}", process_name), "/NH"])
                .creation_flags(0x08000000) // CREATE_NO_WINDOW
                .output();

            if let Ok(out) = output {
                let stdout = String::from_utf8_lossy(&out.stdout);
                // tasklist returns "INFO: No tasks are running..." if no matches
                if stdout.contains(process_name) && !stdout.contains("INFO:") {
                    any_running = true;
                    println!("Process {} still running at check {}", process_name, i + 1);
                    break;
                }
            }
        }

        if !any_running {
            println!("All processes exited after {} checks", i + 1);
            return;
        }

        // Every 4 checks (2 seconds), try to kill any remaining claude.exe child processes
        // This handles orphaned processes that may have been missed by the initial kill
        if i > 0 && i % 4 == 0 {
            kill_claude_child_processes(backend_pid);
        }

        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    // Final attempt to kill any remaining claude.exe child processes
    kill_claude_child_processes(backend_pid);
    println!("Warning: Some processes may still be running after timeout (backend PID: {})", backend_pid);
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

                let _window = WebviewWindowBuilder::new(app, "main", url)
                    .title("SwarmAI")
                    .inner_size(1400.0, 900.0)
                    .min_inner_size(1024.0, 768.0)
                    .resizable(true)
                    .fullscreen(false)
                    .center()
                    .title_bar_style(tauri::TitleBarStyle::Overlay)
                    .hidden_title(true)
                    .zoom_hotkeys_enabled(false)
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
