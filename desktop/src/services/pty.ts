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

  readonly onData = this._onData.event;
  readonly onExit = this._onExit.event;

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
        void invoke('pty_kill', { pid }).catch(() => {});
        return pid;
      }
      void this.readLoop(pid);
      return pid;
    });
  }

  /** Poll pty_read until EOF or kill. Decodes bytes → string before emitting. */
  private async readLoop(pid: number): Promise<void> {
    while (this.alive) {
      try {
        // Rust returns a binary IPC Response → arrives as number[]/Uint8Array.
        const chunk = await invoke<Uint8Array | number[]>('pty_read', { pid });
        if (!this.alive) break;
        const bytes = chunk instanceof Uint8Array ? chunk : Uint8Array.from(chunk);
        if (bytes.length > 0) {
          // stream:true keeps a partial multi-byte char buffered across chunks.
          this._onData.fire(this.decoder.decode(bytes, { stream: true }));
        }
      } catch {
        // pty_read rejects with "EOF" (or any error) → child closed. Stop.
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
      this._onExit.fire({ exitCode: code });
    } catch {
      this._onExit.fire({ exitCode: -1 });
    }
    this._onExit.clear();
  }

  write(data: string): void {
    void this.handleReady.then((pid) => {
      if (this.alive) void invoke('pty_write', { pid, data }).catch(() => {});
    });
  }

  resize(cols: number, rows: number): void {
    void this.handleReady.then((pid) => {
      if (this.alive) void invoke('pty_resize', { pid, cols, rows }).catch(() => {});
    });
  }

  kill(): void {
    if (!this.alive && this.pid === -1) return;
    this.alive = false;
    this._onData.clear();
    void this.handleReady.then((pid) => {
      void invoke('pty_kill', { pid }).catch(() => {});
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
