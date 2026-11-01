/**
 * TerminalStore — module-level registry of open terminal tabs.
 *
 * Mirrors `messageStoreRegistry` (MessageStore.ts): a module-level Map that
 * SURVIVES React StrictMode double-mount, so the source of truth for which
 * terminals exist + their live IPty handles lives outside React. Components
 * subscribe for re-render; the store owns the PTY lifecycle.
 *
 * Why a store (not React state): a PTY is an OS resource with a lifecycle that
 * must not be duplicated by StrictMode's dev double-invoke (Gate-1 C1) nor
 * orphaned when a tab re-mounts (C2). Centralizing spawn/kill here means the
 * identity of "the pty for tab X" is authoritative and closeTerminal always
 * kills exactly the right process.
 *
 * Every shell carries the location it was opened at (`locKey`, keyOfCwd) and a
 * number within it; the panel groups by location (docs/design/terminal-locations.md).
 *
 * Exports: terminalStore, SWARMWS_KEY, keyOfCwd, baseName, sendTerminalTextToChat,
 * ATTACH_TERMINAL_EVENT, types TerminalTab / OpenTerminalOptions.
 */
import { invoke } from '@tauri-apps/api/core';
import { spawn, type IPty, type IDisposable, type IPtyForkOptions } from '../services/pty';
import { isWindows } from '../utils/platform';

/** The grouping key of the SwarmWS location (a shell opened with no cwd). */
export const SWARMWS_KEY = 'swarmws';

/** The grouping key of a shell opened with *cwd*. */
export function keyOfCwd(cwd?: string): string {
  if (!cwd) return SWARMWS_KEY;
  return cwd.length > 1 ? cwd.replace(/\/+$/, '') : cwd;
}

export function baseName(path: string): string {
  const parts = path.replace(/\/+$/, '').split('/');
  return parts[parts.length - 1] || path;
}

/** One terminal tab: identity + cwd + its live PTY handle + status. */
export interface TerminalTab {
  id: string;
  title: string;
  cwd?: string;
  /** The location this shell was opened at (terminalLocation.keyOfCwd): shells are
   *  grouped by it in the tab strip. */
  locKey: string;
  /** Number within its location: 1 → "zsh", N → "zsh N" (named after the shell). */
  slot: number;
  pty: IPty;
  status: 'running' | 'exited';
  /** The child's exit status once exited (-1 when unknown / spawn failed). */
  exitCode?: number;
  /** Set by the mounted TerminalTab component — returns the current xterm
   *  buffer text (last N lines) for the P2 "attach to chat" action. Null until
   *  the surface has mounted. */
  getBuffer?: () => string;
  /** Set by the mounted TerminalTab component — focuses this tab's xterm so the
   *  user can type immediately. Used when the panel is REVEALED (collapse→reopen
   *  doesn't change `active`, so the [active] focus effect doesn't re-fire).
   *  Null until the surface has mounted. */
  focus?: () => void;
  /** Set by the mounted TerminalTab — the current xterm selection ('' when none). */
  getSelection?: () => string;
}

/** Window event the panel / ChatPage use to hand terminal text to the next chat
 *  message (one-shot, consumed on send). */
export const ATTACH_TERMINAL_EVENT = 'swarm:attach-terminal';

/** Hand *text* (with the shell's cwd) to the next chat message. False when empty. */
export function sendTerminalTextToChat(text: string, cwd?: string): boolean {
  if (!text) return false;
  window.dispatchEvent(new CustomEvent(ATTACH_TERMINAL_EVENT, { detail: { bufferTail: text, cwd: cwd ?? '' } }));
  return true;
}

/** Options for opening a terminal. */
export interface OpenTerminalOptions {
  cwd?: string;
  /** Shell program. Defaults to the user's shell (see defaultShell). */
  file?: string;
  /** Shell args. Defaults to ['-l'] (login, so the profile's PATH loads); none on Windows. */
  args?: string[];
  cols?: number;
  rows?: number;
  /** Tab title; default the shell's name ("zsh" / "zsh N") by its number within the location. */
  title?: string;
}

// The default shell:
//   - Windows → powershell.exe, no args (there is no `-l` login concept).
//   - macOS/Linux → the user's shell as a login shell (-l), so the profile's PATH
//     loads (a GUI app does not inherit it). Which shell is asked of the Rust side
//     (`default_shell`: $SHELL, else /bin/bash), never assumed.
// It is asked for once, when this store loads; a shell opened before the answer
// arrives spawns as soon as it does (DeferredPty).
const FALLBACK_SHELL = '/bin/bash'; // what `default_shell` itself falls back to
let _shell: string | null = null;
let _shellPromise: Promise<string> | null = null;

function loadShell(): Promise<string> {
  if (!_shellPromise) {
    _shellPromise = (isWindows()
      ? Promise.resolve('powershell.exe')
      : invoke<string>('default_shell')
          .then((file) => file || FALLBACK_SHELL)
          .catch(() => FALLBACK_SHELL)
    ).then((file) => { _shell = file; return file; });
  }
  return _shellPromise;
}

/** Default shell args: login (-l) on macOS/Linux, none on Windows. */
function shellArgs(): string[] {
  return isWindows() ? [] : ['-l'];
}

/**
 * An IPty for a shell that is spawned once the default shell is known. Input,
 * resizes and listeners given before then are passed on to the real pty; a kill
 * before then means the shell is never spawned.
 */
class DeferredPty implements IPty {
  private real: IPty | null = null;
  private queued: Array<(p: IPty) => void> = [];
  private killed = false;

  constructor(ready: Promise<string>, args: string[], opts: IPtyForkOptions,
    onSpawn: (file: string) => void) {
    void ready.then((file) => {
      if (this.killed) return;
      onSpawn(file);
      const p = spawn(file, args, opts);
      this.real = p;
      for (const f of this.queued) f(p);
      this.queued = [];
    });
  }

  get pid(): number {
    return this.real?.pid ?? -1;
  }

  private use(f: (p: IPty) => void): void {
    if (this.real) f(this.real);
    else this.queued.push(f);
  }

  private listen(subscribe: (p: IPty) => IDisposable): IDisposable {
    let sub: IDisposable | null = null;
    let disposed = false;
    this.use((p) => { if (!disposed) sub = subscribe(p); });
    return { dispose: () => { disposed = true; sub?.dispose(); } };
  }

  readonly onData = (l: (data: string) => void) => this.listen((p) => p.onData(l));
  readonly onExit = (l: (e: { exitCode: number }) => void) => this.listen((p) => p.onExit(l));
  write(data: string): void { this.use((p) => p.write(data)); }
  resize(cols: number, rows: number): void { this.use((p) => p.resize(cols, rows)); }
  kill(): void {
    if (this.real) this.real.kill();
    else { this.killed = true; this.queued = []; }
  }
}

void loadShell();

let _seq = 0;
function nextId(): string {
  // Monotonic + a per-open counter — unique within the process. No Date.now()
  // dependency (kept deterministic-ish for tests; uniqueness is what matters).
  _seq += 1;
  return `term-${_seq}`;
}

/**
 * Title of a shell: numbered within its LOCATION, like VSCode/Kiro — slot 1 renders
 * "zsh", slot N≥2 "zsh N". The location itself is shown by the panel's switcher, so
 * the title does not repeat the directory. A new shell takes (highest slot in use at
 * that location) + 1, so closing a middle one never produces a duplicate label.
 */
function shellName(file: string): string {
  const base = file.split(/[\\/]/).pop() ?? file;
  return base.replace(/\.exe$/i, '') || 'sh';
}

function titleOf(file: string, slot: number): string {
  const name = shellName(file);
  return slot === 1 ? name : `${name} ${slot}`;
}

class TerminalStore {
  private tabs = new Map<string, TerminalTab>();
  private listeners = new Set<() => void>();
  // Cached array snapshot for useSyncExternalStore — MUST be a stable reference
  // between notifications, else React's getSnapshot loops infinitely. Rebuilt
  // only inside notify() (i.e. only when the tab set actually changed).
  private snapshot: TerminalTab[] = [];

  /** Subscribe to registry changes (open/close/status). Returns unsubscribe. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    this.snapshot = Array.from(this.tabs.values());
    for (const l of this.listeners) l();
  }

  /** Stable snapshot for useSyncExternalStore (same ref until next notify). */
  listSnapshot(): TerminalTab[] {
    return this.snapshot;
  }

  /** Open a new terminal: spawn a PTY and register the tab. */
  openTerminal(opts: OpenTerminalOptions): TerminalTab {
    const id = nextId();
    const forkOpts = { cols: opts.cols ?? 80, rows: opts.rows ?? 24, cwd: opts.cwd };
    const args = opts.args ?? shellArgs();
    // The shell, and so the title: known now, or once the default shell is.
    const file = opts.file ?? _shell;
    const pty: IPty = file
      ? spawn(file, args, forkOpts)
      : new DeferredPty(loadShell(), args, forkOpts, (known) => {
          const t = this.tabs.get(id);
          if (t && opts.title === undefined) {
            t.title = titleOf(known, t.slot);
            this.notify();
          }
        });
    const locKey = keyOfCwd(opts.cwd);
    const slot = Array.from(this.tabs.values()).reduce(
      (mx, t) => (t.locKey === locKey ? Math.max(mx, t.slot) : mx),
      0,
    ) + 1;
    const tab: TerminalTab = {
      id,
      title: opts.title ?? titleOf(file ?? '', slot),
      cwd: opts.cwd,
      locKey,
      slot,
      pty,
      status: 'running',
    };
    // Mark exited when the PTY child dies, so the panel can show it.
    pty.onExit((e) => {
      const t = this.tabs.get(id);
      if (t) {
        t.status = 'exited';
        t.exitCode = typeof e?.exitCode === 'number' ? e.exitCode : -1;
        this.notify();
      }
    });
    this.tabs.set(id, tab);
    this.notify();
    return tab;
  }

  /**
   * Type *text* into shell *id* once it has started, WITHOUT Enter (design
   * docs/design/2026-11-01-settings-account-merge.md §4.3): the person reads it and runs
   * it. "Started" = its first output, then *quietMs* with no more output (a login shell
   * prints its profile's output and the prompt last); if it never goes quiet, *maxMs*
   * after the open. Written before that, the bytes are read by the startup scripts (an
   * update prompt, compinit's question) or echoed twice. Control characters (CR, LF,
   * ^C, ESC …) are stripped, so this can never run anything by itself.
   */
  typeWhenReady(id: string, text: string, quietMs = 400, maxMs = 5000): void {
    const tab = this.tabs.get(id);
    if (!tab) return;
    // eslint-disable-next-line no-control-regex
    const line = text.replace(/[\x00-\x1f\x7f]+/g, ' ').trim();
    if (!line) return;
    let quiet: ReturnType<typeof setTimeout> | null = null;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (quiet) clearTimeout(quiet);
      clearTimeout(cap);
      sub.dispose();
      const t = this.tabs.get(id);
      if (t && t.status === 'running') t.pty.write(line);
    };
    const sub = tab.pty.onData(() => {
      if (quiet) clearTimeout(quiet);
      quiet = setTimeout(finish, quietMs);
    });
    const cap = setTimeout(finish, maxMs);
  }

  /** Shells opened at location *locKey*, in insertion order. */
  listAt(locKey: string): TerminalTab[] {
    return Array.from(this.tabs.values()).filter((t) => t.locKey === locKey);
  }

  /** Live count at a location (the registry, not a React snapshot). */
  countAt(locKey: string): number {
    let n = 0;
    for (const t of this.tabs.values()) if (t.locKey === locKey) n += 1;
    return n;
  }

  /**
   * Close a terminal: kill its PTY and remove the tab.
   *
   * C2 identity-safety: only acts on a tab that is actually registered. A
   * close for an unknown/already-removed id is a silent no-op (never throws,
   * never kills a process that isn't ours).
   */
  closeTerminal(id: string): void {
    const tab = this.tabs.get(id);
    if (!tab) return;
    tab.pty.kill();
    this.tabs.delete(id);
    this.notify();
  }

  /** Kill every PTY and empty the registry (window close / app teardown). */
  closeAll(): void {
    for (const tab of this.tabs.values()) {
      tab.pty.kill();
    }
    this.tabs.clear();
    this.notify();
  }

  get(id: string): TerminalTab | undefined {
    return this.tabs.get(id);
  }

  /** Tabs in insertion order (stable tab bar). */
  list(): TerminalTab[] {
    return Array.from(this.tabs.values());
  }

  count(): number {
    return this.tabs.size;
  }

  /** Test-only: reset the registry without killing (killing is mocked). */
  clear(): void {
    this.tabs.clear();
    this.listeners.clear();
    this.snapshot = [];
    _seq = 0;
  }

  /** Ask for the default shell now (once); resolves when it is known. */
  loadDefaultShell(): Promise<void> {
    return loadShell().then(() => undefined);
  }

  /** Test-only: forget the known default shell so the next open asks again. */
  resetDefaultShell(): void {
    _shell = null;
    _shellPromise = null;
  }
}

/** Module-level singleton — survives React StrictMode double-mount. */
export const terminalStore = new TerminalStore();
