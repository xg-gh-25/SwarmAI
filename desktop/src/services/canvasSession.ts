/**
 * canvasSession — what each chat session produced, for its Canvas tabs (backend
 * routers/canvas_changes → core/canvas_session), and the ONE frontend store of it.
 *
 * The backend is the only source: Artifacts = the session's attribution record
 * (core/session_outputs, written as its turns run) joined with the product catalog;
 * Changes = per repo, the code files it changed (net, with where that content is now)
 * and the commits that hold them. The store caches it per session and keeps it fresh:
 *
 * - `swarm:file-changed` (the live signal) → a debounced `parts=artifacts` refresh of
 *   the owning tab's session. The backend queues that read behind the write the event
 *   came from, so the refresh always holds the new row.
 * - a turn ending / the session changing / the Changes tab opening → a full refresh
 *   (`refreshCanvasSession`), which also recomputes Changes.
 *
 * Tabs map to sessions as useCanvasHost reports them (`setActiveCanvasSession`), so a
 * background tab's event refreshes ITS session and its count stays right.
 *
 * @exports canvasSessionService, refreshCanvasSession, refreshCanvasArtifacts,
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
  turns: number;
  /** Deleted by the session (a later write clears it). */
  deleted: boolean;
  exists: boolean;
  baseRef?: string;
}

/** Where the content the session left is now (core/canvas_session). */
export type FileState = 'uncommitted' | 'committed' | 'pushed' | 'changed';

export interface SessionFileChange {
  path: string;
  absolutePath?: string;
  status: 'added' | 'modified' | 'deleted';
  added: number | null;
  deleted: number | null;
  turns: number;
  firstAt: string;
  lastAt: string;
  /** Change trees: the session's net diff is start → end (GET /chat/turn-diff). */
  start: string;
  end: string;
  state: FileState;
  /** The commit holding this content (state 'committed'). */
  commit: string | null;
}

export interface SessionCommit {
  sha: string;
  short: string;
  subject: string;
  at: string;
  /** session: holds content this session left; run: a pipeline run it owned or surfaced. */
  source: 'session' | 'run';
  run?: string;
  files: string[];
}

export interface SessionRepo {
  root: string;
  repo: string;
  project: string | null;
  branch?: string;
  upstream: string | null;
  files: SessionFileChange[];
  commits: SessionCommit[];
  error?: string;
}

export interface CommitFile {
  path: string;
  insertions: number | null;
  deletions: number | null;
}

export interface SessionOutputs {
  artifacts: SessionArtifact[];
  /** null: not computed (an artifacts-only refresh) */
  changes: SessionRepo[] | null;
}

export const canvasSessionService = {
  async get(sessionId: string, parts: 'all' | 'artifacts' = 'all'): Promise<SessionOutputs> {
    const r = await api.get<SessionOutputs>(`/canvas/session/${encodeURIComponent(sessionId)}`,
      parts === 'all' ? undefined : { params: { parts } });
    return { artifacts: r.data?.artifacts ?? [], changes: r.data?.changes ?? (parts === 'all' ? [] : null) };
  },
  async commitFiles(root: string, sha: string): Promise<CommitFile[]> {
    const r = await api.get<{ files: CommitFile[] }>('/canvas/changes/commit', { params: { root, sha } });
    return r.data?.files ?? [];
  },
  async commitDiff(root: string, sha: string, path: string): Promise<{ diff: string; truncated: boolean }> {
    const r = await api.get<{ diff: string; truncated: boolean }>('/canvas/changes/diff', { params: { root, sha, path } });
    return r.data ?? { diff: '', truncated: false };
  },
};

// ── store ─────────────────────────────────────────────────────────────────────
export interface CanvasSessionState {
  sessionId: string | null;
  data: SessionOutputs | null;
  error: string | null;
  loading: boolean;
}
interface Entry { data: SessionOutputs | null; error: string | null; loading: boolean }

const EMPTY: Entry = { data: null, error: null, loading: false };
let active: string | null = null;
let entries = new Map<string, Entry>();
const tabSession = new Map<string, string>();
const listeners = new Set<() => void>();
const inflight = new Map<string, { parts: 'all' | 'artifacts'; p: Promise<void> }>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();
let gen = 0;
let unsubscribe: (() => void) | null = null;
let activeSnap: CanvasSessionState = { sessionId: null, ...EMPTY };

function emit() {
  const e = (active && entries.get(active)) || EMPTY;
  activeSnap = { sessionId: active, data: e.data, error: e.error, loading: e.loading };
  listeners.forEach((l) => l());
}
function put(sid: string, patch: Partial<Entry>) {
  entries.set(sid, { ...(entries.get(sid) ?? EMPTY), ...patch });
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
  return sessionId ? entries.get(sessionId)?.data?.artifacts : undefined;
}

/** Point the store at the active tab's session (null: none yet). */
export function setActiveCanvasSession(sessionId: string | null, tabId?: string | null) {
  if (tabId && sessionId) tabSession.set(tabId, sessionId);
  if (sessionId === active) return;
  active = sessionId;
  emit();
}

function load(sid: string, parts: 'all' | 'artifacts'): Promise<void> {
  const cur = inflight.get(sid);
  // one request at a time per session; a full one already covers artifacts
  if (cur && (cur.parts === 'all' || parts === 'artifacts')) return cur.p;
  const mine = gen;
  put(sid, { loading: true });
  const p = (cur ? cur.p.catch(() => {}) : Promise.resolve())
    .then(() => canvasSessionService.get(sid, parts))
    .then((data) => {
      if (mine !== gen) return;
      const prev = entries.get(sid)?.data;
      // an artifacts-only answer keeps the last Changes
      put(sid, { data: { artifacts: data.artifacts, changes: data.changes ?? prev?.changes ?? null }, error: null, loading: false });
    })
    .catch((e) => { if (mine === gen) put(sid, { error: e instanceof Error ? e.message : String(e), loading: false }); })
    .finally(() => { if (inflight.get(sid)?.p === p) inflight.delete(sid); });
  inflight.set(sid, { parts, p });
  return p;
}

/** Full refresh (artifacts + changes) of a session, the active one by default. */
export function refreshCanvasSession(sessionId?: string | null): Promise<void> {
  const sid = sessionId ?? active;
  return sid ? load(sid, 'all') : Promise.resolve();
}

/** The cheap live refresh: artifacts only. */
export function refreshCanvasArtifacts(sessionId?: string | null): Promise<void> {
  const sid = sessionId ?? active;
  return sid ? load(sid, 'artifacts') : Promise.resolve();
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
  activeSnap = { sessionId: null, ...EMPTY };
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
