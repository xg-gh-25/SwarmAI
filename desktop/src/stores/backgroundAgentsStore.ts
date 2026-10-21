/**
 * Background agents store — the status of each session's background Agents
 * (an Agent called with run_in_background), as the daemon tracks them
 * (backend core/background_agents.py).
 *
 * Two feeds, same data: a `background_agents` envelope on the window's session
 * event stream (live, on every change), and the `background_agents` field of every
 * `streaming-state` read (the snapshot: on load, and as the reconcile fallback).
 *
 * `loaded` turns true with the first snapshot: from then on, an agent the daemon does
 * not know is not running (the daemon restarted, or its CLI went away before it
 * finished) — the Agent row then says lost instead of "in background" forever.
 *
 * Key exports:
 * - `BackgroundAgent`, `BackgroundAgentStatus`
 * - `backgroundAgentsStore` — `apply(sessionId, agents)`, `applySnapshot(map)`, `get`, `subscribe`
 * - `useBackgroundAgents(sessionId)` — React subscription for one session
 * - `agentStatus(sessionId, toolUseId, kept)` — the status an Agent row shows
 */
import { useSyncExternalStore } from 'react';

export type BackgroundAgentStatus = 'running' | 'completed' | 'failed' | 'stopped' | 'lost';

export interface BackgroundAgent {
  toolUseId: string;
  description: string;
  status: BackgroundAgentStatus;
  startedAt?: number;
  endedAt?: number;
  durationMs?: number;
  toolUses?: number;
  summary?: string;
}

interface State { bySession: Map<string, BackgroundAgent[]>; loaded: boolean }

const EMPTY: BackgroundAgent[] = [];

function toAgent(raw: unknown): BackgroundAgent | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = typeof r.toolUseId === 'string' ? r.toolUseId : typeof r.tool_use_id === 'string' ? r.tool_use_id : '';
  if (!id) return null;
  const status = typeof r.status === 'string' ? r.status : 'running';
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);
  return {
    toolUseId: id,
    description: typeof r.description === 'string' ? r.description : '',
    status: (['running', 'completed', 'failed', 'stopped', 'lost'].includes(status) ? status : 'running') as BackgroundAgentStatus,
    startedAt: num(r.startedAt), endedAt: num(r.endedAt), durationMs: num(r.durationMs), toolUses: num(r.toolUses),
    summary: typeof r.summary === 'string' ? r.summary : undefined,
  };
}

const same = (a: BackgroundAgent[], b: BackgroundAgent[]) =>
  a.length === b.length && a.every((x, i) => JSON.stringify(x) === JSON.stringify(b[i]));

export function createBackgroundAgentsStore() {
  let state: State = { bySession: new Map(), loaded: false };
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());
  const list = (raw: unknown) => (Array.isArray(raw) ? raw.map(toAgent).filter((a): a is BackgroundAgent => !!a) : []);
  return {
    /** One session's agents, from a live `background_agents` envelope. */
    apply(sessionId: string, raw: unknown) {
      const next = list(raw);
      if (same(state.bySession.get(sessionId) ?? EMPTY, next)) return;
      const bySession = new Map(state.bySession);
      bySession.set(sessionId, next);
      state = { ...state, bySession };
      emit();
    },
    /** Every session's agents, from a streaming-state read (the daemon's whole view). */
    applySnapshot(map: Record<string, unknown>) {
      const bySession = new Map<string, BackgroundAgent[]>();
      for (const [sid, raw] of Object.entries(map)) bySession.set(sid, list(raw));
      // every streaming-state read (3s while streaming) lands here: only a real
      // change notifies, so the Agent rows and the tab line do not re-render for nothing
      const unchanged = state.loaded && bySession.size === state.bySession.size
        && [...bySession].every(([sid, a]) => same(state.bySession.get(sid) ?? EMPTY, a));
      if (unchanged) return;
      state = { bySession, loaded: true };
      emit();
    },
    get(sessionId: string | undefined): BackgroundAgent[] {
      return (sessionId && state.bySession.get(sessionId)) || EMPTY;
    },
    loaded: () => state.loaded,
    subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; },
    snapshot: () => state,
    /** tests */
    reset() { state = { bySession: new Map(), loaded: false }; emit(); },
  };
}

export const backgroundAgentsStore = createBackgroundAgentsStore();

export function useBackgroundAgents(sessionId: string | undefined): { agents: BackgroundAgent[]; loaded: boolean } {
  const st = useSyncExternalStore(backgroundAgentsStore.subscribe, backgroundAgentsStore.snapshot);
  return { agents: (sessionId && st.bySession.get(sessionId)) || EMPTY, loaded: st.loaded };
}

/**
 * What an Agent row shows for an Agent whose result said `background`: the daemon's
 * live status when it knows the agent; else `lost` once a snapshot was read (it is not
 * running any more and its end was never recorded); else still `running`. An end
 * already stored on the result (`background` with a final status) is used as is.
 */
export function agentStatus(
  agents: BackgroundAgent[], loaded: boolean, toolUseId: string, kept: string | undefined,
  stored?: { background?: boolean; durationMs?: number },
): BackgroundAgent | { status: string; durationMs?: number } | null {
  // its end is already on the stored result
  if (stored?.background && kept && kept !== 'background') return { status: kept, durationMs: stored.durationMs };
  if (kept !== 'background') return null;
  const live = agents.find((a) => a.toolUseId === toolUseId);
  if (live) return live;
  return { status: loaded ? 'lost' : 'running' };
}
