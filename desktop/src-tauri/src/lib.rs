use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use tauri::{Emitter, Manager};
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

// Backend state management
struct BackendState {
    child: Option<CommandChild>,
    port: u16,
    running: bool,
    pid: Option<u32>,  // Store PID for process tree cleanup on Windows
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            child: None,
            port: 8000,
            running: false,
            pid: None,
        }
    }
}

/// Maximum time (seconds) to wait for the backend to complete shutdown.
/// Covers: HookContext build (~1s) + DailyActivity batch (~5s) + drain (~8s).
/// The curl/PowerShell timeout is set to this value.
const SHUTDOWN_GRACE_SECONDS: u64 = 10;

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

        // Mark as not running under lock — prevents double-fire
        backend.running = false;
        backend.pid = None;
        drop(backend); // Release lock before blocking I/O

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

    // Find an available port
    let port = portpicker::pick_unused_port().unwrap_or(8000);

    // Get enhanced PATH for the sidecar
    let enhanced_path = get_enhanced_path();

    // Start the sidecar with enhanced environment
    let sidecar = app
        .shell()
        .sidecar("python-backend")
        .map_err(|e| format!("Failed to create sidecar command: {}", e))?
        .args(["--port", &port.to_string()])
        .env("PATH", enhanced_path);

    let (mut rx, child) = sidecar
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
    }

    // Spawn a task to handle sidecar output
    let app_handle = app.clone();
    let state_clone = state.inner().clone();
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let _ = app_handle.emit("backend-log", String::from_utf8_lossy(&line).to_string());
                }
                CommandEvent::Stderr(line) => {
                    let _ = app_handle.emit("backend-error", String::from_utf8_lossy(&line).to_string());
                }
                CommandEvent::Terminated(payload) => {
                    let _ = app_handle.emit("backend-terminated", payload.code);
                    // Update state when backend terminates
                    let mut backend = state_clone.lock().await;
                    backend.running = false;
                    backend.child = None;
                    backend.pid = None;
                    break;
                }
                _ => {}
            }
        }
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
                    if body.contains("\"healthy\"") {
                        println!("[Tauri] Backend healthy after {} attempts", attempt);
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

        // Mark as not running immediately to prevent concurrent operations
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
    })
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
            check_nodejs_version,
            check_python_version,
            check_git_bash_path,
        ])
        .setup(|app| {
            // Backend will be started by frontend via initializeBackend()
            // This allows proper error handling in the UI

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
