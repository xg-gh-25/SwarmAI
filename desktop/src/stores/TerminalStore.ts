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

// Default shell: on macOS/Linux use the login shell (-l) so .zprofile/.zshrc
// PATH (brazil/swarm/toolbox) loads — the GUI-app-no-profile-env trap fix.
// (Windows would use powershell.exe; the desktop app ships mac/linux first.)
const DEFAULT_SHELL = '/bin/zsh';
const DEFAULT_ARGS = ['-l'];

let _seq = 0;
function nextId(): string {
  // Monotonic + a per-open counter — unique within the process. No Date.now()
  // dependency (kept deterministic-ish for tests; uniqueness is what matters).
  _seq += 1;
  return `term-${_seq}`;
}

function titleFromCwd(cwd?: string): string {
  if (cwd) {
    const parts = cwd.replace(/\/+$/, '').split('/');
    const base = parts[parts.length - 1];
    if (base) return base;
  }
  return 'zsh';
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
    const pty = spawn(opts.file ?? DEFAULT_SHELL, opts.args ?? DEFAULT_ARGS, {
      cols: opts.cols ?? 80,
      rows: opts.rows ?? 24,
      cwd: opts.cwd,
    });
    const tab: TerminalTab = {
      id,
      title: titleFromCwd(opts.cwd),
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
