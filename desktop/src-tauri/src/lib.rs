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
    let home = env::var("HOME").unwrap_or_default();

    // Common paths where Node.js, npm, and other tools might be installed
    let mut paths = vec![
        "/opt/homebrew/bin".to_string(),           // Homebrew on Apple Silicon
        "/opt/homebrew/sbin".to_string(),
        "/usr/local/bin".to_string(),              // Homebrew on Intel Mac / common location
        "/usr/local/sbin".to_string(),
        "/usr/bin".to_string(),
        "/bin".to_string(),
        "/usr/sbin".to_string(),
        "/sbin".to_string(),
        // Volta (Node.js version manager)
        format!("{}/.volta/bin", home),
        // fnm (Fast Node Manager)
        format!("{}/.fnm/aliases/default/bin", home),
        // pyenv (Python version manager)
        format!("{}/.pyenv/shims", home),
        format!("{}/.pyenv/bin", home),
        // Global npm / pnpm
        format!("{}/Library/pnpm", home),
        format!("{}/.npm-global/bin", home),
        // pipx
        format!("{}/.local/bin", home),
    ];

    // For nvm, we need to find actual node version directories (glob doesn't work in PATH)
    let nvm_dir = format!("{}/.nvm/versions/node", home);
    if let Ok(entries) = std::fs::read_dir(&nvm_dir) {
        for entry in entries.flatten() {
            let bin_path = entry.path().join("bin");
            if bin_path.exists() {
                paths.push(bin_path.to_string_lossy().to_string());
            }
        }
    }

    if !current_path.is_empty() {
        paths.push(current_path);
    }

    paths.join(":")
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
    let home = env::var("HOME").unwrap_or_default();

    // Try using user's shell to get proper environment
    let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());

    // Run through shell to get user's environment (including nvm, volta, etc.)
    let output = std::process::Command::new(&shell)
        .arg("-l")  // Login shell to source profile
        .arg("-c")  // Execute command
        .arg("node --version")
        .output();

    match output {
        Ok(output) => {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .to_string();
                Ok(version)
            } else {
                // Fallback: try with enhanced PATH
                let enhanced_path = get_enhanced_path();
                let output = std::process::Command::new("node")
                    .arg("--version")
                    .env("PATH", enhanced_path)
                    .output();

                match output {
                    Ok(output) if output.status.success() => {
                        let version = String::from_utf8_lossy(&output.stdout)
                            .trim()
                            .to_string();
                        Ok(version)
                    }
                    _ => Err("Node.js is not installed or not in PATH".to_string())
                }
            }
        }
        Err(_) => Err("Node.js is not installed or not in PATH".to_string())
    }
}

// Check Python version
#[tauri::command]
async fn check_python_version() -> Result<String, String> {
    let home = env::var("HOME").unwrap_or_default();
    let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());

    // Try using user's shell to get proper environment (including pyenv)
    let output = std::process::Command::new(&shell)
        .arg("-l")  // Login shell to source profile
        .arg("-c")  // Execute command
        .arg("python3 --version 2>&1 || python --version 2>&1")
        .output();

    match output {
        Ok(output) => {
            if output.status.success() {
                let version = String::from_utf8_lossy(&output.stdout)
                    .trim()
                    .to_string();
                if !version.is_empty() {
                    return Ok(version);
                }
            }
        }
        _ => {}
    }

    // Fallback: try pyenv directly if available
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

    // Fallback: try with enhanced PATH
    let enhanced_path = get_enhanced_path();

    // Try python3 first
    let output = std::process::Command::new("python3")
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

    // Finally try python
    let output = std::process::Command::new("python")
        .arg("--version")
        .env("PATH", &enhanced_path)
        .output();

    match output {
        Ok(output) if output.status.success() => {
            let version = String::from_utf8_lossy(&output.stdout)
                .trim()
                .to_string();
            Ok(version)
        }
        _ => Err("Python is not installed or not in PATH".to_string())
    }
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
        .setup(|_app| {
            // Backend will be started by frontend via initializeBackend()
            // This allows proper error handling in the UI
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
