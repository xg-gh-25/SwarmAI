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
import { spawn, type IPty } from '../services/pty';

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
  /** Number within its location: 1 → "zsh", N → "zsh N". */
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
  /** Shell program. Defaults to the login shell so profile PATH loads. */
  file?: string;
  /** Shell args. Defaults to ['-l'] (login) for brazil/swarm PATH. */
  args?: string[];
  cols?: number;
  rows?: number;
  /** Tab title; default "zsh" / "zsh N" by its number within the location. */
  title?: string;
}

// Default shell — platform-aware so the terminal works on all desktop targets
// (macOS/Linux subprocess/Windows subprocess), not just Unix:
//   - Windows → powershell.exe (no args; there is no `-l` login concept)
//   - macOS/Linux → the user's login shell (-l) so .zprofile/.zshrc PATH
//     (brazil/swarm/toolbox) loads — the GUI-app-no-profile-env trap fix.
// Detected via navigator (zero-dep, works in the Tauri webview) — avoids
// hardcoding /bin/zsh on Windows (which has no such path → spawn failure).
function isWindows(): boolean {
  if (typeof navigator === 'undefined') return false;
  // userAgentData.platform is the modern signal ("Windows"); userAgent is the
  // fallback ("... (Windows NT 10.0; ...)"). Match "windows" specifically —
  // NOT a bare /win/ which also matches "dar​win" (macOS!) and would spawn
  // powershell on every Mac. (Caught by Gate-2 re-test — the loose regex was a
  // fix-induced bug.)
  const uaData = (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData;
  const p = uaData?.platform ?? navigator.userAgent ?? '';
  return /windows|win32|win64/i.test(p);
}

function defaultShell(): { file: string; args: string[] } {
  return isWindows()
    ? { file: 'powershell.exe', args: [] }
    : { file: '/bin/zsh', args: ['-l'] };
}

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
    const sh = defaultShell();
    const file = opts.file ?? sh.file;
    const pty = spawn(file, opts.args ?? sh.args, {
      cols: opts.cols ?? 80,
      rows: opts.rows ?? 24,
      cwd: opts.cwd,
    });
    const locKey = keyOfCwd(opts.cwd);
    const slot = Array.from(this.tabs.values()).reduce(
      (mx, t) => (t.locKey === locKey ? Math.max(mx, t.slot) : mx),
      0,
    ) + 1;
    const tab: TerminalTab = {
      id,
      title: opts.title ?? titleOf(file, slot),
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
}

/** Module-level singleton — survives React StrictMode double-mount. */
export const terminalStore = new TerminalStore();
