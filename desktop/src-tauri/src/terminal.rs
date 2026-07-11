//! Integrated terminal PTY commands (app-level, NOT a Tauri plugin).
//!
//! Vendored & adapted from `Tnze/tauri-plugin-pty` (MIT License, © Tnze) —
//! https://github.com/Tnze/tauri-plugin-pty. We copy the ~7 portable-pty glue
//! commands directly as APP-LEVEL `#[tauri::command]`s (registered in
//! `lib.rs`'s existing `generate_handler!`) instead of depending on the crate,
//! for three reasons:
//!   1. The upstream crate is a small (21★, "developing") niche dependency; we
//!      prefer to own ~180 lines of trivial portable-pty glue than pin our app
//!      to its release cadence.
//!   2. As app-level commands we SKIP the entire Tauri v2 plugin permission
//!      scaffolding (`build.rs` + `links=` + `permissions/` codegen). App
//!      commands inherit `core:default` — no `pty:default` capability to
//!      misconfigure (killed Gate-1 finding H3).
//!   3. `portable-pty` (the real PTY engine, by the wezterm team) IS depended
//!      on — it is not vendorable and is the industry standard.
//!
//! Design invariants (this is the HUMAN's terminal, not the AI's):
//!   - PTY lives in the Tauri app process, never in the Python daemon → a
//!     `swarm daemon restart` from a terminal never self-kills the terminal.
//!   - No dangerous-command gating here — that gate is for the AI agent; the
//!     human owns this shell.
//!   - Every session is reaped on `pty_kill` / window close (no orphan shells).
//!
//! Command surface (all async, all keyed by a u32 session handle):
//!   pty_spawn → pty_read (poll) / pty_write / pty_resize / pty_kill /
//!   pty_exitstatus / pty_get_all_pids
//! The frontend (`services/pty.ts`) wraps `pty_read` in a poll loop and exposes
//! an xterm-friendly `onData`/`write`/`resize`/`kill` IPty interface.

use std::{
    collections::BTreeMap,
    ffi::OsString,
    sync::{
        atomic::{AtomicU32, Ordering},
        Arc,
    },
};

use portable_pty::{native_pty_system, Child, ChildKiller, CommandBuilder, PtyPair, PtySize};
use tauri::async_runtime::{Mutex, RwLock};

/// Opaque handle for one PTY session (monotonic, process-local).
pub type PtyHandler = u32;

/// A single live PTY session: the pair, the child process, its killer, and the
/// reader/writer handles onto the master side.
struct Session {
    pair: Mutex<PtyPair>,
    child: Mutex<Box<dyn Child + Send + Sync>>,
    child_killer: Mutex<Box<dyn ChildKiller + Send + Sync>>,
    writer: Mutex<Box<dyn std::io::Write + Send>>,
    reader: Mutex<Box<dyn std::io::Read + Send>>,
}

/// App-managed registry of all live PTY sessions. Registered via
/// `app.manage(TerminalState::default())` in `lib.rs` setup().
#[derive(Default)]
pub struct TerminalState {
    session_id: AtomicU32,
    sessions: RwLock<BTreeMap<PtyHandler, Arc<Session>>>,
}

/// Spawn a shell/command in a fresh PTY and return its session handle.
///
/// `file` is the program (e.g. the login shell `zsh`), `args` its arguments
/// (e.g. `["-l"]` so the login profile PATH — brazil/swarm/toolbox — loads),
/// `cwd` the working directory (a Swarm Project dir when opened via the
/// explorer right-click), `env` extra environment variables.
#[allow(clippy::too_many_arguments)]
#[tauri::command]
pub async fn pty_spawn(
    file: String,
    args: Vec<String>,
    cols: u16,
    rows: u16,
    cwd: Option<String>,
    env: BTreeMap<String, String>,
    state: tauri::State<'_, TerminalState>,
) -> Result<PtyHandler, String> {
    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| e.to_string())?;
    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    let reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;

    let mut cmd = CommandBuilder::new(file);
    cmd.args(args);
    if let Some(cwd) = cwd {
        cmd.cwd(OsString::from(cwd));
    }
    for (k, v) in env.iter() {
        cmd.env(OsString::from(k), OsString::from(v));
    }
    let child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
    let child_killer = child.clone_killer();
    let handler = state.session_id.fetch_add(1, Ordering::Relaxed);

    let session = Arc::new(Session {
        pair: Mutex::new(pair),
        child: Mutex::new(child),
        child_killer: Mutex::new(child_killer),
        writer: Mutex::new(writer),
        reader: Mutex::new(reader),
    });
    state.sessions.write().await.insert(handler, session);
    Ok(handler)
}

/// Write user input (keystrokes) to the PTY's stdin. Proves real interactivity.
#[tauri::command]
pub async fn pty_write(
    pid: PtyHandler,
    data: String,
    state: tauri::State<'_, TerminalState>,
) -> Result<(), String> {
    let session = state
        .sessions
        .read()
        .await
        .get(&pid)
        .ok_or("Unavailable pid")?
        .clone();
    session
        .writer
        .lock()
        .await
        .write_all(data.as_bytes())
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Read up to 4096 bytes of PTY output. Returns the raw bytes as a Tauri IPC
/// binary Response (the frontend decodes UTF-8). `Err("EOF")` signals the PTY
/// has closed so the frontend poll loop stops. The frontend calls this in a
/// loop; a real byte-stream (progress bars via `\r`, TUI escapes) flows through
/// unmodified — there is NO wall-clock timeout, unlike the agent Bash tool.
#[tauri::command]
pub async fn pty_read(
    pid: PtyHandler,
    state: tauri::State<'_, TerminalState>,
) -> Result<tauri::ipc::Response, String> {
    let session = state
        .sessions
        .read()
        .await
        .get(&pid)
        .ok_or("Unavailable pid")?
        .clone();
    let mut buf = vec![0u8; 4096];
    let n = session
        .reader
        .lock()
        .await
        .read(&mut buf)
        .map_err(|e| e.to_string())?;
    if n == 0 {
        Err(String::from("EOF"))
    } else {
        buf.truncate(n);
        Ok(tauri::ipc::Response::new(buf))
    }
}

/// Resize the PTY viewport (columns × rows) — driven by xterm's fit addon.
#[tauri::command]
pub async fn pty_resize(
    pid: PtyHandler,
    cols: u16,
    rows: u16,
    state: tauri::State<'_, TerminalState>,
) -> Result<(), String> {
    let session = state
        .sessions
        .read()
        .await
        .get(&pid)
        .ok_or("Unavailable pid")?
        .clone();
    session
        .pair
        .lock()
        .await
        .master
        .resize(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Kill the PTY's child process (SIGHUP by default). Called on tab close and on
/// window close so no orphan shells survive (AC6).
#[tauri::command]
pub async fn pty_kill(
    pid: PtyHandler,
    state: tauri::State<'_, TerminalState>,
) -> Result<(), String> {
    let session = state
        .sessions
        .read()
        .await
        .get(&pid)
        .ok_or("Unavailable pid")?
        .clone();
    session
        .child_killer
        .lock()
        .await
        .kill()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Wait for the child to exit and return its exit code, then remove the session
/// from the registry (only after the child has actually exited).
#[tauri::command]
pub async fn pty_exitstatus(
    pid: PtyHandler,
    state: tauri::State<'_, TerminalState>,
) -> Result<u32, String> {
    let session = state
        .sessions
        .read()
        .await
        .get(&pid)
        .ok_or("Unavailable pid")?
        .clone();
    let exitstatus = session
        .child
        .lock()
        .await
        .wait()
        .map_err(|e| e.to_string())?
        .exit_code();

    // Remove the session only after the child has exited.
    let _ = state.sessions.write().await.remove(&pid);
    Ok(exitstatus)
}

/// List all live PTY session handles. Used by the window-close reaper to kill
/// every remaining PTY child (AC6 backstop for when the frontend's per-tab
/// cleanup didn't run, e.g. on a hard window close).
#[tauri::command]
pub async fn pty_get_all_pids(
    state: tauri::State<'_, TerminalState>,
) -> Result<Vec<PtyHandler>, String> {
    let sessions = state.sessions.read().await;
    Ok(sessions.keys().copied().collect())
}

/// Kill every live PTY child and clear the registry. Called from the window
/// close handler in lib.rs as the Rust-side backstop to the frontend's
/// beforeunload closeAll (defense-in-depth for orphan reaping).
pub async fn reap_all_ptys(state: &TerminalState) {
    let handlers: Vec<PtyHandler> = {
        let sessions = state.sessions.read().await;
        sessions.keys().copied().collect()
    };
    for h in handlers {
        let session = { state.sessions.read().await.get(&h).cloned() };
        if let Some(session) = session {
            let _ = session.child_killer.lock().await.kill();
        }
    }
    state.sessions.write().await.clear();
}
