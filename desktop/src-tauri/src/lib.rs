use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::env;
use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;
use tokio::sync::Mutex;

// Get enhanced PATH that includes common installation locations for the sidecar
fn get_enhanced_path() -> String {
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

    // Platform-specific common paths
    #[cfg(target_os = "macos")]
    {
        paths.extend_from_slice(&[
            "/opt/homebrew/bin".to_string(),           // Homebrew on Apple Silicon
            "/opt/homebrew/sbin".to_string(),
            "/usr/local/bin".to_string(),              // Homebrew on Intel Mac
            "/usr/local/sbin".to_string(),
            "/usr/bin".to_string(),
            "/bin".to_string(),
            "/usr/sbin".to_string(),
            "/sbin".to_string(),
            format!("{}/Library/pnpm", home),          // macOS-specific pnpm location
        ]);
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
        // Windows common installation locations
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

    // Cross-platform user-local paths (Volta, nvm, fnm, pyenv, etc.)
    #[cfg(not(target_os = "windows"))]
    {
        paths.extend_from_slice(&[
            format!("{}/.volta/bin", home),
            format!("{}/.fnm/aliases/default/bin", home),
            format!("{}/.pyenv/shims", home),
            format!("{}/.pyenv/bin", home),
            format!("{}/.npm-global/bin", home),
            format!("{}/.local/bin", home),
        ]);

        // For nvm, we need to find actual node version directories
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
        // Windows user-local paths
        paths.push(format!(r"{}\AppData\Roaming\npm", home));
        paths.push(format!(r"{}\.volta\bin", home));

        // nvm for Windows
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
}

impl Default for BackendState {
    fn default() -> Self {
        Self {
            child: None,
            port: 8000,
            running: false,
        }
    }
}

type SharedBackendState = Arc<Mutex<BackendState>>;

#[derive(Serialize, Deserialize)]
pub struct BackendStatus {
    running: bool,
    port: u16,
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

    // Store the child process (short lock)
    {
        let mut backend = state.lock().await;
        backend.child = Some(child);
        backend.port = port;
        backend.running = true;
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
                    break;
                }
                _ => {}
            }
        }
    });

    // Wait a bit for the backend to start
    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;

    Ok(port)
}

// Stop the Python backend
#[tauri::command]
async fn stop_backend(state: tauri::State<'_, SharedBackendState>) -> Result<(), String> {
    let mut backend = state.lock().await;

    if let Some(child) = backend.child.take() {
        child.kill().map_err(|e| format!("Failed to kill backend: {}", e))?;
    }

    backend.running = false;
    Ok(())
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
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Arc::new(Mutex::new(BackendState::default())))
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            get_backend_status,
            get_backend_port,
            check_nodejs_version,
            check_python_version,
        ])
        .setup(|app| {
            // Backend will be started by frontend via initializeBackend()
            // This allows proper error handling in the UI

            // Open DevTools automatically in debug builds or when OWORK_DEBUG is set
            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }

            // Also check for OWORK_DEBUG env var to enable in release builds
            #[cfg(not(debug_assertions))]
            {
                if std::env::var("OWORK_DEBUG").is_ok() {
                    if let Some(window) = app.get_webview_window("main") {
                        window.open_devtools();
                    }
                }
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                // Clean up backend process on exit
                let state = app_handle.state::<SharedBackendState>();
                let state_clone = state.inner().clone();

                // Use blocking task to ensure cleanup completes
                tauri::async_runtime::block_on(async {
                    let mut backend = state_clone.lock().await;
                    if let Some(child) = backend.child.take() {
                        let _ = child.kill();
                        println!("Backend process terminated on exit");
                    }
                    backend.running = false;
                });
            }
        });
}
