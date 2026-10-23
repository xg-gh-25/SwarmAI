/**
 * terminalLocation — where a new terminal opens, per chat tab
 * (docs/design/terminal-locations.md).
 *
 * A location is SwarmWS (no cwd: the Rust default), the active chat's working repo
 * (the newest pipeline run its session owns, read from the backend — one source, no
 * guessing) or a custom folder. Each chat tab keeps its own choice; with none, the
 * repo when the chat has one, else SwarmWS. Recent custom folders are global.
 *
 * Module-level store (survives StrictMode), read with useSyncExternalStore.
 *
 * Exports: terminalLocation, SWARMWS_KEY, keyOfCwd, baseName, type LocationChoice,
 * type ResolvedLocation, type WorkingRepo, type LocationSnapshot.
 */
import { canvasSessionService } from '../services/canvasSession';
import { SWARMWS_KEY, keyOfCwd, baseName } from './TerminalStore';

export { SWARMWS_KEY, keyOfCwd, baseName };

const CHOICES_KEY = 'terminalLocationByChatTab';
const RECENTS_KEY = 'terminalRecentDirs';
const MAX_CHOICES = 100;
const MAX_RECENTS = 6;
const NO_TAB = '_';

export interface WorkingRepo { path: string; name: string }
export type LocationChoice = { kind: 'swarmws' } | { kind: 'repo' } | { kind: 'custom'; path: string };
export interface ResolvedLocation {
  kind: 'swarmws' | 'repo' | 'custom';
  /** Shells are grouped by this: SWARMWS_KEY or the absolute path. */
  key: string;
  /** Passed to openTerminal; undefined = SwarmWS (Rust resolve_default_cwd). */
  cwd?: string;
  label: string;
}
export interface LocationSnapshot {
  current: ResolvedLocation;
  workingRepo: WorkingRepo | null;
  recents: string[];
}

export const SWARMWS_LOCATION: ResolvedLocation = { kind: 'swarmws', key: SWARMWS_KEY, label: 'SwarmWS' };

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / unavailable: the choice lives for this run only */
  }
}

function validChoice(c: unknown): c is LocationChoice {
  if (!c || typeof c !== 'object') return false;
  const k = (c as { kind?: unknown }).kind;
  if (k === 'swarmws' || k === 'repo') return true;
  return k === 'custom' && typeof (c as { path?: unknown }).path === 'string' && !!(c as { path: string }).path;
}

class TerminalLocationStore {
  private chatTabId: string | null = null;
  private sessionId: string | null = null;
  /** sessionId → its working repo (null = it has none). Absent = not read yet. */
  private repos = new Map<string, WorkingRepo | null>();
  private inflight = new Map<string, Promise<void>>();
  private dirty = new Set<string>();
  /** Bumped whenever the chat in front changes: an async continuation started for
   *  one chat checks it before acting (TerminalPanel's reveal auto-open). */
  private gen = 0;
  private choices: Record<string, LocationChoice> = {};
  private recents: string[] = [];
  private listeners = new Set<() => void>();
  private snap!: LocationSnapshot;

  constructor() {
    this.load();
  }

  /** Re-read persisted choices / recents (also used by tests after clearing). */
  load(): void {
    const raw = readJson<Record<string, unknown>>(CHOICES_KEY, {});
    this.choices = {};
    for (const [k, v] of Object.entries(raw ?? {})) if (validChoice(v)) this.choices[k] = v;
    const rec = readJson<unknown>(RECENTS_KEY, []);
    this.recents = Array.isArray(rec) ? rec.filter((p): p is string => typeof p === 'string' && !!p).slice(0, MAX_RECENTS) : [];
    this.snap = this.build();
  }

  subscribe(l: () => void): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  snapshot(): LocationSnapshot {
    return this.snap;
  }

  private notify(): void {
    const next = this.build();
    const prev = this.snap;
    if (prev.current.key === next.current.key && prev.current.kind === next.current.kind
        && prev.workingRepo?.path === next.workingRepo?.path
        && prev.recents.join('\0') === next.recents.join('\0')) return;
    this.snap = next;
    for (const l of this.listeners) l();
  }

  private workingRepo(): WorkingRepo | null {
    return this.sessionId ? this.repos.get(this.sessionId) ?? null : null;
  }

  private build(): LocationSnapshot {
    const repo = this.workingRepo();
    const choice = this.choices[this.chatTabId ?? NO_TAB];
    let current: ResolvedLocation = SWARMWS_LOCATION;
    if (choice?.kind === 'custom') {
      const key = keyOfCwd(choice.path);
      current = { kind: 'custom', key, cwd: key, label: baseName(key) };
    } else if (choice?.kind !== 'swarmws' && repo) {
      current = { kind: 'repo', key: keyOfCwd(repo.path), cwd: repo.path, label: repo.name };
    }
    return { current, workingRepo: repo, recents: [...this.recents] };
  }

  /** The chat tab now in front (ChatPage publishes it). Reads its working repo. */
  setActiveChat(chatTabId: string | null, sessionId: string | null): void {
    if (chatTabId !== this.chatTabId || sessionId !== this.sessionId) this.gen += 1;
    this.chatTabId = chatTabId;
    this.sessionId = sessionId;
    this.notify();
    void this.refresh();
  }

  /** Changes whenever the chat in front changes. */
  generation(): number {
    return this.gen;
  }

  /** Re-read the active chat's working repo (tab switch, turn end, panel reveal).
   *  A call that lands while a read for the same session is in flight schedules ONE
   *  more read after it (that read may predate a run created meanwhile); the promise
   *  settles once the newest read has. */
  refresh(): Promise<void> {
    const sid = this.sessionId;
    if (!sid) return Promise.resolve();
    const running = this.inflight.get(sid);
    if (running) {
      this.dirty.add(sid);
      return running;
    }
    const read = (): Promise<void> => canvasSessionService.workingRepo(sid)
      .then((repo) => {
        const prev = this.repos.get(sid);
        if (this.repos.has(sid) && prev?.path === repo?.path) return;
        this.repos.set(sid, repo);
        if (this.repos.size > 200) this.repos.delete(this.repos.keys().next().value as string);
        this.notify();
      })
      .catch(() => { /* backend unreachable: keep what we knew */ })
      .then(() => {
        if (!this.dirty.delete(sid)) return undefined;
        return read();
      });
    const p = read().finally(() => this.inflight.delete(sid));
    this.inflight.set(sid, p);
    return p;
  }

  /** Make *choice* this chat tab's location. A custom folder joins the recents. */
  choose(choice: LocationChoice): void {
    const tab = this.chatTabId ?? NO_TAB;
    delete this.choices[tab];
    this.choices[tab] = choice.kind === 'custom' ? { kind: 'custom', path: keyOfCwd(choice.path) } : choice;
    const keys = Object.keys(this.choices);
    for (const k of keys.slice(0, Math.max(0, keys.length - MAX_CHOICES))) delete this.choices[k];
    writeJson(CHOICES_KEY, this.choices);
    if (choice.kind === 'custom') {
      const p = keyOfCwd(choice.path);
      this.recents = [p, ...this.recents.filter((r) => r !== p)].slice(0, MAX_RECENTS);
      writeJson(RECENTS_KEY, this.recents);
    }
    this.notify();
  }

  /** ⌘⇧`: SwarmWS ↔ the working repo. False when the chat has no repo. */
  toggleRepo(): boolean {
    if (this.snap.current.kind === 'repo') {
      this.choose({ kind: 'swarmws' });
      return true;
    }
    if (!this.workingRepo()) return false;
    this.choose({ kind: 'repo' });
    return true;
  }

  /** Test-only: forget everything (in-memory and persisted). */
  reset(): void {
    this.chatTabId = null;
    this.sessionId = null;
    this.repos.clear();
    this.inflight.clear();
    this.dirty.clear();
    this.choices = {};
    this.recents = [];
    this.listeners.clear();
    try {
      localStorage.removeItem(CHOICES_KEY);
      localStorage.removeItem(RECENTS_KEY);
    } catch { /* ignore */ }
    this.snap = this.build();
  }
}

/** Module-level singleton — survives React StrictMode double-mount. */
export const terminalLocation = new TerminalLocationStore();
