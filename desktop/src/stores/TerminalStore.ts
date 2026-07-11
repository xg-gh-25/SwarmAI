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
 */
import { spawn, type IPty } from '../services/pty';

/** One terminal tab: identity + cwd + its live PTY handle + status. */
export interface TerminalTab {
  id: string;
  title: string;
  cwd?: string;
  pty: IPty;
  status: 'running' | 'exited';
  /** Set by the mounted TerminalTab component — returns the current xterm
   *  buffer text (last N lines) for the P2 "attach to chat" action. Null until
   *  the surface has mounted. */
  getBuffer?: () => string;
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
 * Yields the SLOT number of an UNTITLED (no-cwd) tab's title:
 * "zsh" → slot 1, "zsh 2" → slot 2, ... Returns 0 if the title doesn't parse.
 *
 * IMPORTANT: only ever call this on tabs that are actually untitled (no cwd) —
 * the caller filters by `!tab.cwd`, NOT by matching this regex against the
 * title. That structural filter is what prevents a cwd tab whose basename is
 * literally "zsh" (cwd ending in /zsh) from colliding with the untitled slot
 * numbering. (Adversarial: LOW title-collision finding.)
 */
const UNTITLED_RE = /^zsh(?: (\d+))?$/;
function untitledSlot(title: string): number {
  const m = UNTITLED_RE.exec(title);
  if (!m) return 0;
  return m[1] ? parseInt(m[1], 10) : 1; // bare "zsh" is slot 1
}

/**
 * Title for a new tab. A cwd tab uses the dir basename. An untitled (no-cwd)
 * tab gets a distinct numbered title like VSCode/Kiro: slot 1 renders "zsh",
 * slot N≥2 renders "zsh N". The new tab takes (highest existing slot) + 1.
 * Using max-slot+1 (not a live count) means closing a middle tab never
 * produces a duplicate label: with {zsh, zsh 3} open (slots 1,3) the next is
 * "zsh 4", not a colliding second "zsh 3". The Rust side still launches a
 * no-cwd shell in $HOME/.swarm-ai/SwarmWS (resolve_default_cwd) — the generic
 * numbered title is intentional and does NOT show that default's basename.
 *
 * `maxSlot` = highest untitled slot currently in use, or 0 if there are none.
 */
function titleFromCwd(cwd: string | undefined, maxSlot: number): string {
  if (cwd) {
    const parts = cwd.replace(/\/+$/, '').split('/');
    const base = parts[parts.length - 1];
    if (base) return base;
  }
  const slot = maxSlot + 1;
  return slot === 1 ? 'zsh' : `zsh ${slot}`;
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
    const pty = spawn(opts.file ?? sh.file, opts.args ?? sh.args, {
      cols: opts.cols ?? 80,
      rows: opts.rows ?? 24,
      cwd: opts.cwd,
    });
    // Highest untitled slot in use, so a new no-cwd tab gets the next distinct
    // number (VSCode/Kiro behavior). max-slot+1 avoids duplicate labels when a
    // middle tab was closed. Filter by `!t.cwd` (structural "is untitled"), NOT
    // by title regex — so a cwd tab basenamed "zsh" can't perturb the numbering.
    const maxSlot = Array.from(this.tabs.values()).reduce(
      (mx, t) => (t.cwd ? mx : Math.max(mx, untitledSlot(t.title))),
      0,
    );
    const tab: TerminalTab = {
      id,
      title: titleFromCwd(opts.cwd, maxSlot),
      cwd: opts.cwd,
      pty,
      status: 'running',
    };
    // Mark exited when the PTY child dies, so the tab bar can show it.
    pty.onExit(() => {
      const t = this.tabs.get(id);
      if (t) {
        t.status = 'exited';
        this.notify();
      }
    });
    this.tabs.set(id, tab);
    this.notify();
    return tab;
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
