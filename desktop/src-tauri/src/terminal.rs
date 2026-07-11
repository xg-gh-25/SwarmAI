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

/// Resolve the working directory for a new PTY.
///
/// When the caller supplies a non-empty `cwd` (e.g. the WorkspaceExplorer
/// right-click "Open terminal here" passes a project dir), that path always
/// wins. When it is absent or empty (the default bottom-panel / nav-icon
/// "new terminal"), default to the daemon workspace `$HOME/.swarm-ai/SwarmWS`
/// — the dir the Explorer is rooted at and the #1 use case (manage SwarmWS,
/// build Swarm projects). Falls back to `.` only if `$HOME` is unset.
///
/// Resolved HERE in Rust (not the frontend) because Rust knows `$HOME`
/// deterministically (same `env::var("HOME")` pattern the daemon deploy path
/// uses) and it avoids an async round-trip on the frontend.
fn resolve_default_cwd(cwd: Option<String>) -> String {
    if let Some(c) = cwd {
        if !c.trim().is_empty() {
            return c;
        }
    }
    match std::env::var("HOME") {
        Ok(home) if !home.is_empty() => {
            // Prefer the daemon workspace, but only if it EXISTS. portable-pty
            // passes cwd to the child's chdir, which fails (ENOENT) on a missing
            // dir — so a not-yet-created SwarmWS would make every default
            // terminal fail to spawn. Fall back to $HOME (always present) when
            // the workspace dir isn't there yet. (Adversarial: multi-specialist
            // confirmed fresh-install ENOENT risk.)
            let ws = format!("{home}/.swarm-ai/SwarmWS");
            if std::path::Path::new(&ws).is_dir() {
                ws
            } else {
                home
            }
        }
        _ => ".".to_string(),
    }
}

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
    // Default to $HOME/.swarm-ai/SwarmWS when no cwd is supplied (a passed
    // non-empty cwd always wins). See resolve_default_cwd.
    cmd.cwd(OsString::from(resolve_default_cwd(cwd)));
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
    // The PTY read is a BLOCKING syscall that parks until bytes arrive (an idle
    // shell prompt blocks indefinitely). Running it directly in this async
    // command would park a shared async-runtime worker for the whole idle time.
    // Move it onto a dedicated blocking thread via spawn_blocking so the async
    // workers stay free to schedule pty_write / pty_resize / other commands.
    //
    // `session` is Arc<Session> (Send + 'static) so it moves into the closure.
    // `reader` is a tokio async Mutex; from a NON-async blocking thread we take
    // it with `blocking_lock()` (the sanctioned tokio API for exactly this —
    // it would PANIC inside an async context, but spawn_blocking runs off the
    // async workers, so it is correct here). read() of one PTY serializes on
    // this lock, which is the desired behavior (a PTY has one byte stream).
    let read_result: Result<Vec<u8>, String> =
        tauri::async_runtime::spawn_blocking(move || {
            let mut buf = vec![0u8; 4096];
            let n = session
                .reader
                .blocking_lock()
                .read(&mut buf)
                .map_err(|e| e.to_string())?;
            buf.truncate(n);
            Ok(buf)
        })
        .await
        .map_err(|e| e.to_string())?; // JoinError (blocking thread panicked/cancelled)
    let buf = read_result?;
    if buf.is_empty() {
        Err(String::from("EOF"))
    } else {
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

#[cfg(test)]
mod tests {
    use super::resolve_default_cwd;

    // NOTE: these tests mutate the process-global HOME. `resolve_default_cwd`
    // reads HOME, and cargo runs tests in PARALLEL threads by default, so a
    // shared HOME would race. We serialize the HOME-dependent tests behind a
    // mutex and each restores nothing (they set their own value under the lock).
    use std::sync::Mutex;
    static HOME_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn passed_non_empty_cwd_wins() {
        // No HOME dependency — a passed non-empty cwd short-circuits before HOME.
        assert_eq!(
            resolve_default_cwd(Some("/Users/x/Projects/AIDLC".to_string())),
            "/Users/x/Projects/AIDLC"
        );
    }

    #[test]
    fn none_cwd_defaults_to_swarmws_when_it_exists() {
        // AC3: a no-cwd terminal starts in $HOME/.swarm-ai/SwarmWS when that dir
        // EXISTS. Create it under a unique temp HOME so the is_dir() check passes.
        let _g = HOME_LOCK.lock().unwrap();
        let home = std::env::temp_dir().join("swarmws_test_home_exists");
        let ws = home.join(".swarm-ai").join("SwarmWS");
        std::fs::create_dir_all(&ws).unwrap();
        std::env::set_var("HOME", &home);
        assert_eq!(
            resolve_default_cwd(None),
            ws.to_string_lossy().to_string()
        );
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn empty_cwd_treated_as_absent() {
        // Empty/whitespace cwd is treated as absent → resolves like None.
        let _g = HOME_LOCK.lock().unwrap();
        let home = std::env::temp_dir().join("swarmws_test_home_empty");
        let ws = home.join(".swarm-ai").join("SwarmWS");
        std::fs::create_dir_all(&ws).unwrap();
        std::env::set_var("HOME", &home);
        let expected = ws.to_string_lossy().to_string();
        assert_eq!(resolve_default_cwd(Some("".to_string())), expected);
        assert_eq!(resolve_default_cwd(Some("   ".to_string())), expected);
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn falls_back_to_home_when_swarmws_missing() {
        // Adversarial fix: if $HOME/.swarm-ai/SwarmWS does NOT exist (fresh
        // machine), fall back to $HOME (always present) rather than returning a
        // missing dir that portable-pty's chdir would reject with ENOENT.
        let _g = HOME_LOCK.lock().unwrap();
        let home = std::env::temp_dir().join("swarmws_test_home_missing");
        std::fs::create_dir_all(&home).unwrap();
        // Deliberately do NOT create .swarm-ai/SwarmWS under it.
        let _ = std::fs::remove_dir_all(home.join(".swarm-ai"));
        std::env::set_var("HOME", &home);
        assert_eq!(
            resolve_default_cwd(None),
            home.to_string_lossy().to_string()
        );
        let _ = std::fs::remove_dir_all(&home);
    }
}
