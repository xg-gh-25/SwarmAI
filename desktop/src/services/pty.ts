/**
 * PTY service — xterm-friendly wrapper over our app-level Rust PTY commands.
 *
 * Vendored & adapted from `Tnze/tauri-plugin-pty` (MIT License, © Tnze) —
 * https://github.com/Tnze/tauri-plugin-pty. We copy the client `IPty` shape
 * rather than depend on the `tauri-pty` npm package, and point it at OUR
 * app-level commands (`pty_spawn`/`pty_read`/…) instead of the plugin's
 * `plugin:pty|*` invoke names. See src-tauri/src/terminal.rs for the rationale.
 *
 * Behavior:
 *   - `spawn(file,args,opts)` starts a PTY via `pty_spawn`, returns an `IPty`.
 *   - A read poll loop calls `pty_read` repeatedly; each chunk (raw bytes from
 *     the Rust IPC binary Response → Uint8Array) is UTF-8 decoded to a STRING
 *     (Gate-1 H2 — xterm.write wants a string, and naive byte-passing mangles
 *     multi-byte/CJK) and fanned out to all onData listeners.
 *   - `pty_read` rejecting with "EOF" (child closed) stops the loop and fires
 *     onExit.
 *   - onData/onExit return an `IDisposable` so React effects can unsubscribe
 *     (Gate-1 M1 — no dangling listeners on tab unmount).
 *
 * There is NO wall-clock timeout anywhere — the poll loop runs until EOF or
 * kill, which is exactly why a real `./prod.sh build` (minutes) streams fully,
 * unlike the agent Bash tool's ~120s cap (AC1).
 */
import { invoke } from '@tauri-apps/api/core';

/** A subscription that can be cancelled. */
export interface IDisposable {
  dispose(): void;
}

/** xterm-compatible event signature: `event(listener) => IDisposable`. */
export type IEvent<T> = (listener: (arg: T) => void) => IDisposable;

/** Options for spawning a PTY. */
export interface IPtyForkOptions {
  cols: number;
  rows: number;
  cwd?: string;
  env?: Record<string, string>;
}

/** The pseudo-terminal handle. Mirrors node-pty / tauri-pty's IPty subset. */
export interface IPty {
  /** The session handle (resolves once pty_spawn returns; -1 until then). */
  readonly pid: number;
  /** Fires with decoded output text as it streams from the PTY. */
  readonly onData: IEvent<string>;
  /** Fires once when the PTY child exits. */
  readonly onExit: IEvent<{ exitCode: number }>;
  /** Write user input (keystrokes) to the PTY. */
  write(data: string): void;
  /** Resize the PTY viewport. */
  resize(cols: number, rows: number): void;
  /** Kill the PTY child and stop the read loop. */
  kill(): void;
}

/**
 * Log a PTY invoke error — but stay SILENT for the two errors that are EXPECTED
 * during normal operation, so we don't spam false alarms:
 *   - "EOF": the child closed (normal read-loop termination).
 *   - "Unavailable pid": the session was already removed (normal teardown —
 *     e.g. a kill/write/resize that races a tab-close; Gate-1 flagged that a
 *     blanket console.error here fires on every tab close).
 * Any OTHER error is a real failure and IS surfaced (bare `.catch(()=>{})` used
 * to swallow these, which is how the ArrayBuffer decode bug stayed invisible).
 */
function logPtyError(op: string, e: unknown): void {
  const msg = typeof e === 'string' ? e : e instanceof Error ? e.message : String(e);
  // Exact-match: the Rust side emits these as EXACT literals (terminal.rs
  // `Err("EOF")` / `.ok_or("Unavailable pid")`). Substring-matching would wrongly
  // silence a genuine read error whose text merely contains "EOF" (Gate-2 LOW).
  if (msg === 'EOF' || msg === 'Unavailable pid') return;
  console.error(`pty ${op} error:`, e);
}

/** Minimal listener registry backing an IEvent. */
class Emitter<T> {
  private listeners = new Set<(arg: T) => void>();
  readonly event: IEvent<T> = (listener) => {
    this.listeners.add(listener);
    return { dispose: () => this.listeners.delete(listener) };
  };
  fire(arg: T): void {
    for (const l of this.listeners) l(arg);
  }
  clear(): void {
    this.listeners.clear();
  }
}

class TauriPty implements IPty {
  pid = -1;
  private readonly _onData = new Emitter<string>();
  private readonly _onExit = new Emitter<{ exitCode: number }>();
  private readonly decoder = new TextDecoder();
  private alive = true;
  private handleReady: Promise<number>;
  // Latched terminal exit. Set ONCE when the pty exits (EOF, error, or spawn
  // failure). The exit event can fire ~1 microtask after construction (spawn
  // failure) — before an async consumer subscribes — so we replay it to any
  // late subscriber instead of firing-into-the-void + clearing (Gate-2 MED race).
  private _exit: { exitCode: number } | null = null;

  readonly onData = this._onData.event;
  /**
   * Subscribe to the exit event. If the pty has ALREADY exited (latched in
   * _exit), replay it immediately to the new listener — so a subscriber that
   * registers after a fast spawn-failure still learns the tab is dead.
   */
  readonly onExit: IEvent<{ exitCode: number }> = (listener) => {
    if (this._exit) {
      listener(this._exit);
      return { dispose: () => {} };
    }
    return this._onExit.event(listener);
  };

  /** Fire the exit event exactly once and latch it for late subscribers. */
  private fireExit(exitCode: number): void {
    if (this._exit) return;
    this._exit = { exitCode };
    this._onExit.fire(this._exit);
    this._onExit.clear();
  }

  constructor(file: string, args: string[], opts: IPtyForkOptions) {
    this.handleReady = invoke<number>('pty_spawn', {
      file,
      args,
      cols: opts.cols,
      rows: opts.rows,
      cwd: opts.cwd,
      env: opts.env ?? {},
    }).then((pid) => {
      this.pid = pid;
      // If kill() was called before spawn resolved, kill now and don't poll.
      if (!this.alive) {
        void invoke('pty_kill', { pid }).catch((e) => logPtyError('kill', e));
        return pid;
      }
      void this.readLoop(pid);
      return pid;
    }).catch((e) => {
      // pty_spawn itself failed (bad shell, chdir ENOENT, etc.) — the terminal
      // will never produce output. Surface it (was an unhandled rejection) and
      // fire onExit so the tab shows as exited instead of a silent blank.
      logPtyError('spawn', e);
      this.alive = false;
      this.fireExit(-1);
      return -1;
    });
  }

  /** Poll pty_read until EOF or kill. Decodes bytes → string before emitting. */
  private async readLoop(pid: number): Promise<void> {
    while (this.alive) {
      try {
        // Rust's pty_read returns a `tauri::ipc::Response` of raw bytes, which
        // arrives in the webview as an **ArrayBuffer** (not number[]/Uint8Array).
        const chunk = await invoke<ArrayBuffer | Uint8Array | number[]>('pty_read', { pid });
        if (!this.alive) break;
        // `new Uint8Array(chunk)` is UNIVERSAL across every shape Tauri may
        // deliver: an ArrayBuffer becomes a view (len=byteLength), a number[]
        // is copied, an existing Uint8Array is copied. This is the upstream
        // form (Tnze/tauri-plugin-pty). ⚠️ Do NOT use `Uint8Array.from(chunk)`:
        // `Uint8Array.from(anArrayBuffer)` returns an EMPTY array (ArrayBuffer
        // is not iterable/array-like), so every byte is dropped and the terminal
        // renders NOTHING — the exact bug this replaced (verified: from(AB)→len 0).
        const bytes = new Uint8Array(chunk as ArrayBufferLike);
        if (bytes.length > 0) {
          // stream:true keeps a partial multi-byte char buffered across chunks.
          this._onData.fire(this.decoder.decode(bytes, { stream: true }));
        }
      } catch (e) {
        // pty_read rejects with "EOF" (child closed) → normal stop, silent.
        // Any OTHER error is a real read failure — surface it (it used to be
        // swallowed, which is how the decode bug stayed invisible).
        logPtyError('read', e);
        break;
      }
    }
    // Clear data listeners BEFORE firing exit, so an onExit handler that
    // (re)subscribes onData isn't immediately dropped by a late clear().
    this._onData.clear();
    if (this.alive) {
      // Loop ended via EOF (not an explicit kill) — report exit.
      this.alive = false;
      void this.emitExit(pid);
    }
  }

  private async emitExit(pid: number): Promise<void> {
    try {
      const code = await invoke<number>('pty_exitstatus', { pid });
      this.fireExit(code);
    } catch {
      this.fireExit(-1);
    }
  }

  write(data: string): void {
    void this.handleReady.then((pid) => {
      if (this.alive) void invoke('pty_write', { pid, data }).catch((e) => logPtyError('write', e));
    });
  }

  resize(cols: number, rows: number): void {
    void this.handleReady.then((pid) => {
      if (this.alive) void invoke('pty_resize', { pid, cols, rows }).catch((e) => logPtyError('resize', e));
    });
  }

  kill(): void {
    if (!this.alive && this.pid === -1) return;
    this.alive = false;
    this._onData.clear();
    void this.handleReady.then((pid) => {
      void invoke('pty_kill', { pid }).catch((e) => logPtyError('kill', e));
    });
  }
}

/** Spawn a PTY running `file args` and return its IPty handle. */
export function spawn(file: string, args: string[], opts: IPtyForkOptions): IPty {
  return new TauriPty(file, args, opts);
}

/** List all live PTY session handles (window-close reaper backstop). */
export async function getAllPids(): Promise<number[]> {
  return invoke<number[]>('pty_get_all_pids');
}
