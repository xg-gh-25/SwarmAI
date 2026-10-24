/**
 * canvasSession — what each chat session produced, for its Canvas tabs (backend
 * routers/canvas_changes → core/canvas_session), and the ONE frontend store of it.
 *
 * The backend is the only source: Artifacts = the session's attribution record
 * (core/session_outputs, written as its turns run) joined with the product catalog;
 * Changes = what the session's own transcript says it changed (core/session_changes):
 * each turn's "files changed" card and the commits its messages name. The two are
 * separate requests and separate slots
 * of the cache (a part never waits for the other); the store keeps them per session
 * and fresh:
 *
 * - `swarm:file-changed` (the live signal) → a debounced Artifacts refresh of the
 *   owning tab's session. The backend queues that read behind the write the event
 *   came from, and a refresh asked for while one runs runs once more after it, so the
 *   answer always holds the new row.
 * - a turn ending / the session changing → both parts (`refreshCanvasSession`); the
 *   Changes tab opening → Changes (`refreshCanvasChanges`).
 * - what was loaded stays on screen while a part reloads.
 *
 * Tabs map to sessions as useCanvasHost reports them (`setActiveCanvasSession`), so a
 * background tab's event refreshes ITS session and its count stays right.
 *
 * @exports canvasSessionService, refreshCanvasSession, refreshCanvasArtifacts, refreshCanvasChanges, setCanvasStreaming,
 *   setActiveCanvasSession, sessionForTab, cachedArtifacts, useCanvasSession,
 *   useCanvasSessionArtifacts, resetCanvasSession, LIVE_REFRESH_MS, and the types
 */
import { useSyncExternalStore } from 'react';
import api from './api';
import { subscribeFileChanged } from '../hooks/fileChangedBroker';

export interface SessionArtifact {
  path: string;
  absolutePath: string;
  kind?: string;
  /** product catalog role: Deliverables | Knowledge | Pipeline | Activity | Other */
  role: string;
  /** In the workspace product catalog (the left-nav Artifacts lists it too). */
  product: boolean;
  starred: boolean;
  displayLabel: string;
  firstAt: string;
  lastAt: string;
  /** How many writes of the session touched it. */
  writes: number;
  /** Deleted by the session (a later write clears it). */
  deleted: boolean;
  exists: boolean;
  /** In its repo's HEAD (UPD) / not (NEW); null: cannot be told. */
  inHead?: boolean | null;
  baseRef?: string;
}

/** One file of a turn's card (core/turn_changes), as recorded. `start` → `end` are
 *  trees of `gitRoot`'s object store; the diff is GET /canvas/changes/tree-diff. */
export interface ChangedFile {
  path: string;
  absolutePath: string;
  repo: string;
  status: 'added' | 'modified' | 'deleted';
  added: number | null;
  deleted: number | null;
  binary?: boolean;
  diffable?: boolean;
  gitRoot: string;
  start: string;
  end: string;
}

/** One turn's "files changed" card (core/session_changes). */
export interface TurnChangeCard {
  /** `<message row id>:<block index>` */
  id: string;
  at: string;
  files: ChangedFile[];
  /** files the card did not list */
  omitted: number;
}

export interface CommitFile {
  path: string;
  added: number | null;
  deleted: number | null;
}

/** A commit the session's messages name, confirmed by git in one bound repo. */
export interface MentionedCommit {
  sha: string;
  short: string;
  subject: string;
  at: string;
  repo: string;
  /** the repo's main checkout */
  root: string;
  /** when the session last named it */
  mentionedAt: string;
  /** the upstream contains it; null: no upstream, or not checked (older commits) */
  pushed: boolean | null;
  files: CommitFile[];
  omittedFiles: number;
}

/** The session's Changes (core/session_changes.read). */
export interface SessionChanges {
  turns: TurnChangeCard[];
  /** turns beyond the cap */
  olderTurns: number;
  commits: MentionedCommit[];
  /** false: earlier messages of the session were not scanned */
  scannedAll: boolean;
}

export interface SessionOutputs {
  /** null: not loaded yet */
  artifacts: SessionArtifact[] | null;
  /** null: not loaded yet */
  changes: SessionChanges | null;
}

type Part = 'artifacts' | 'changes';

const NO_CHANGES: SessionChanges = { turns: [], olderTurns: 0, commits: [], scannedAll: true };

export const canvasSessionService = {
  async artifacts(sessionId: string): Promise<SessionArtifact[]> {
    const r = await api.get<{ artifacts: SessionArtifact[] | null }>(
      `/canvas/session/${encodeURIComponent(sessionId)}`, { params: { parts: 'artifacts' } });
    return r.data?.artifacts ?? [];
  },
  async changes(sessionId: string): Promise<SessionChanges> {
    const r = await api.get<{ changes: Partial<SessionChanges> | null }>(
      `/canvas/session/${encodeURIComponent(sessionId)}`, { params: { parts: 'changes' } });
    const c = r.data?.changes;
    return c ? { ...NO_CHANGES, ...c } : NO_CHANGES;
  },
  async commitDiff(root: string, sha: string, path: string): Promise<{ diff: string; truncated: boolean }> {
    const r = await api.get<{ diff: string; truncated: boolean }>('/canvas/changes/diff', { params: { root, sha, path } });
    return r.data ?? { diff: '', truncated: false };
  },
  /** One file of a turn card; null when the diff is not available (404). */
  async treeDiff(root: string, start: string, end: string, path: string): Promise<string | null> {
    try {
      const r = await api.get<{ diff: string }>('/canvas/changes/tree-diff', { params: { root, start, end, path } });
      return r.data?.diff ?? null;
    } catch (e) {
      if ((e as { response?: { status?: number } })?.response?.status === 404) return null;
      throw e;
    }
  },
  /** The bound repo the session's newest "files changed" card names (the terminal's default
   *  location, docs/design/terminal-locations.md); null when it has none. */
  async workingRepo(sessionId: string): Promise<{ path: string; name: string } | null> {
    const r = await api.get<{ workingRepo: { path: string; name: string } | null }>(
      `/canvas/session/${encodeURIComponent(sessionId)}/working-repo`);
    return r.data?.workingRepo ?? null;
  },
};

// ── store ─────────────────────────────────────────────────────────────────────
export interface CanvasSessionState {
  sessionId: string | null;
  data: SessionOutputs | null;
  error: string | null;
  /** a request for that part is running */
  loading: { artifacts: boolean; changes: boolean };
  /** the session's turn is streaming: Changes is not requested until it ends */
  streaming: boolean;
}
interface Entry { data: SessionOutputs | null; error: string | null; loading: { artifacts: boolean; changes: boolean } }

const IDLE = { artifacts: false, changes: false };
const EMPTY: Entry = { data: null, error: null, loading: IDLE };
let active: string | null = null;
let entries = new Map<string, Entry>();
const tabSession = new Map<string, string>();
const listeners = new Set<() => void>();
// one request per (session, part); a refresh asked for while one runs runs once more
// after it, so its answer is read after the event that asked for it
const inflight = new Map<string, { p: Promise<void>; again: boolean }>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
// sessions whose turn is streaming (useCanvasHost reports it): no Changes request
// for them until the turn ends, then exactly one
const streaming = new Set<string>();
let gen = 0;
let unsubscribe: (() => void) | null = null;
let activeSnap: CanvasSessionState = { sessionId: null, ...EMPTY, streaming: false };

function emit() {
  const e = (active && entries.get(active)) || EMPTY;
  activeSnap = { sessionId: active, data: e.data, error: e.error, loading: e.loading,
    streaming: !!active && streaming.has(active) };
  listeners.forEach((l) => l());
}
/** The new value of a part, or the cached one when the answer is the same: an
 *  unchanged refresh re-renders nothing. */
function same<T>(prev: T | null | undefined, next: T): T {
  return prev != null && JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
}
function put(sid: string, patch: { part: Part; value?: unknown; error?: string | null; loading: boolean }) {
  const cur = entries.get(sid) ?? EMPTY;
  const data = 'value' in patch
    ? { artifacts: cur.data?.artifacts ?? null, changes: cur.data?.changes ?? null,
        [patch.part]: same(cur.data?.[patch.part], patch.value) } as SessionOutputs
    : cur.data;
  entries.set(sid, {
    data,
    error: patch.error === undefined ? cur.error : patch.error,
    loading: { ...cur.loading, [patch.part]: patch.loading },
  });
  emit();
}

/** Debounce of the live refresh: a burst of writes is one request. */
export const LIVE_REFRESH_MS = 250;

function onFileChanged(e: Event) {
  const d = (e as CustomEvent<{ tabId?: string }>).detail ?? {};
  const sid = (d.tabId && tabSession.get(d.tabId)) || (!d.tabId ? active : null);
  if (!sid) return;
  const t = timers.get(sid);
  if (t) clearTimeout(t);
  timers.set(sid, setTimeout(() => { timers.delete(sid); void refreshCanvasArtifacts(sid); }, LIVE_REFRESH_MS));
}

function subscribe(l: () => void) {
  listeners.add(l);
  if (!unsubscribe) unsubscribe = subscribeFileChanged(onFileChanged);
  return () => { listeners.delete(l); };
}

/** The tab's session as useCanvasHost reported it. */
export function sessionForTab(tabId: string | null | undefined): string | null {
  return (tabId && tabSession.get(tabId)) || null;
}

/** The cached artifacts of a session (undefined: never loaded). */
export function cachedArtifacts(sessionId: string | null | undefined): SessionArtifact[] | undefined {
  return sessionId ? entries.get(sessionId)?.data?.artifacts ?? undefined : undefined;
}

/** Point the store at the active tab's session (null: none yet). */
export function setActiveCanvasSession(sessionId: string | null, tabId?: string | null) {
  if (tabId && sessionId) tabSession.set(tabId, sessionId);
  if (sessionId === active) return;
  active = sessionId;
  emit();
}

function load(sid: string, part: Part): Promise<void> {
  if (part === 'changes' && streaming.has(sid)) return Promise.resolve();   // after the turn
  const key = `${sid}\u0000${part}`;
  const cur = inflight.get(key);
  if (cur) { cur.again = true; return cur.p; }
  const mine = gen;
  const slot = { p: Promise.resolve(), again: false };
  put(sid, { part, loading: true });
  const run = async (): Promise<void> => {
    do {
      slot.again = false;
      try {
        const value = part === 'artifacts' ? await canvasSessionService.artifacts(sid) : await canvasSessionService.changes(sid);
        if (mine !== gen) return;
        put(sid, { part, value, error: null, loading: slot.again });
      } catch (e) {
        if (mine !== gen) return;
        put(sid, { part, error: e instanceof Error ? e.message : String(e), loading: slot.again });
      }
    } while (slot.again && mine === gen);
  };
  slot.p = run().finally(() => { if (inflight.get(key) === slot) inflight.delete(key); });
  inflight.set(key, slot);
  return slot.p;
}

/** Full refresh of a session (the active one by default): Artifacts and Changes as two
 *  independent requests, so the Artifacts never wait for the git work of Changes. */
export function refreshCanvasSession(sessionId?: string | null): Promise<void> {
  const sid = sessionId ?? active;
  return sid ? Promise.all([load(sid, 'artifacts'), load(sid, 'changes')]).then(() => {}) : Promise.resolve();
}

/** The cheap live refresh: artifacts only. */
export function refreshCanvasArtifacts(sessionId?: string | null): Promise<void> {
  const sid = sessionId ?? active;
  return sid ? load(sid, 'artifacts') : Promise.resolve();
}

/** Changes only (the Changes tab opening, its Refresh button). */
export function refreshCanvasChanges(sessionId?: string | null): Promise<void> {
  const sid = sessionId ?? active;
  return sid ? load(sid, 'changes') : Promise.resolve();
}

/** Whether *sessionId*'s turn is streaming (useCanvasHost). While it is, Changes is
 *  not requested; when it ends, Changes is requested once. */
export function setCanvasStreaming(sessionId: string | null, on: boolean): Promise<void> {
  if (!sessionId) return Promise.resolve();
  const was = streaming.has(sessionId);
  if (on === was) return Promise.resolve();
  if (on) streaming.add(sessionId);
  else streaming.delete(sessionId);
  emit();
  return on ? Promise.resolve() : load(sessionId, 'changes');
}

/** Test seam. */
export function resetCanvasSession() {
  gen++;
  active = null;
  entries = new Map();
  tabSession.clear();
  inflight.clear();
  timers.forEach((t) => clearTimeout(t));
  timers.clear();
  streaming.clear();
  activeSnap = { sessionId: null, ...EMPTY, streaming: false };
}

const snapshot = () => activeSnap;
export function useCanvasSession(): CanvasSessionState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Only the artifacts of *sessionId* (undefined until loaded): the subscriber
 *  re-renders when that array changes, never on loading / error flips. For the chat
 *  page host, whose re-render is the expensive one. */
export function useCanvasSessionArtifacts(sessionId: string | null): SessionArtifact[] | undefined {
  const pick = () => cachedArtifacts(sessionId);
  return useSyncExternalStore(subscribe, pick, pick);
}
