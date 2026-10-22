/**
 * canvasSession — what ONE chat session produced, for its Canvas tabs (backend
 * routers/canvas_changes → core/canvas_session), plus a small module store so the
 * tabs read it without re-rendering the Canvas panel.
 *
 * The data is rebuilt from the session's persisted turn records, so it is the same
 * after a reload or restart: Artifacts = the files its turns delivered, Changes = per
 * repo, the code files it changed (net, with where that content is now) and the
 * commits that hold them. A turn still streaming is not in it yet (the live rail
 * covers that); useCanvasHost refreshes when the session changes and a turn ends.
 *
 * @exports canvasSessionService, refreshCanvasSession, setActiveCanvasSession,
 *   useCanvasSession, useCanvasSessionArtifacts, resetCanvasSession, SessionArtifact, SessionRepo,
 *   SessionFileChange, SessionCommit, CommitFile, FileState
 */
import { useSyncExternalStore } from 'react';
import api from './api';

export interface SessionArtifact {
  path: string;
  absolutePath: string;
  kind?: string;
  /** product_registry role: Deliverables | Knowledge | Pipeline | Activity | Other */
  role: string;
  firstAt: string;
  lastAt: string;
  turns: number;
  exists: boolean;
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
  /** session: holds content this session left; run: a pipeline run it surfaced. */
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
  changes: SessionRepo[];
}

export const canvasSessionService = {
  async get(sessionId: string): Promise<SessionOutputs> {
    const r = await api.get<SessionOutputs>(`/canvas/session/${encodeURIComponent(sessionId)}`);
    return { artifacts: r.data?.artifacts ?? [], changes: r.data?.changes ?? [] };
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
let state: CanvasSessionState = { sessionId: null, data: null, error: null, loading: false };
const listeners = new Set<() => void>();
let seq = 0;
function set(next: Partial<CanvasSessionState>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}
function subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }
const snapshot = () => state;

/** Point the store at a session (null: none yet); clears the other session's data. */
export function setActiveCanvasSession(sessionId: string | null) {
  if (sessionId === state.sessionId) return;
  seq++;
  set({ sessionId, data: null, error: null, loading: false });
}

let inflight: { sid: string; p: Promise<void> } | null = null;

/** Refetch the active session's outputs. Calls while one is in flight for the same
 *  session share it (a turn end and the Changes tab opening at once = one request);
 *  a stale answer (the session switched meanwhile) is dropped. */
export function refreshCanvasSession(): Promise<void> {
  const sid = state.sessionId;
  if (!sid) return Promise.resolve();
  if (inflight && inflight.sid === sid) return inflight.p;
  const mine = ++seq;
  set({ loading: true });
  const p = canvasSessionService.get(sid)
    .then((data) => { if (mine === seq) set({ data, error: null, loading: false }); })
    .catch((e) => { if (mine === seq) set({ error: e instanceof Error ? e.message : String(e), loading: false }); })
    .finally(() => { if (inflight?.p === p) inflight = null; });
  inflight = { sid, p };
  return p;
}

/** Test seam. */
export function resetCanvasSession() { seq++; inflight = null; state = { sessionId: null, data: null, error: null, loading: false }; }

export function useCanvasSession(): CanvasSessionState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Only the artifacts of *sessionId* (undefined until loaded): the subscriber
 *  re-renders when that array changes, never on loading / error flips. For the chat
 *  page host, whose re-render is the expensive one. */
export function useCanvasSessionArtifacts(sessionId: string | null): SessionArtifact[] | undefined {
  const pick = () => (state.sessionId === sessionId ? state.data?.artifacts : undefined);
  return useSyncExternalStore(subscribe, pick, pick);
}
