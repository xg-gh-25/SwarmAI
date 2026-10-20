/**
 * Context usage — the frontend side of a BACKEND-owned value.
 *
 * The backend is the only authority on how full a session's context window is
 * (``backend/core/context_usage.py``). It pushes its snapshot as
 * ``context_warning`` SSE events (after every main-agent request, on
 * compaction, at turn end) and serves the same shape from
 * ``GET /api/chat/sessions/{id}/context-usage`` (tab activation, app restart,
 * after compact/refresh). This module holds the pure rules the frontend applies
 * to those snapshots — it never computes a value itself.
 *
 * @exports ContextUsageSnapshot      — wire shape (SSE event and GET body)
 * @exports toContextWarning          — snapshot → display ContextWarning (null = empty ring)
 * @exports isNewerSnapshot           — measuredAt ordering (newest wins)
 * @exports snapshotFromEvent         — typed StreamEvent → ContextUsageSnapshot
 *                                      (the critical-context notice is raised per
 *                                      tab in useChatStreamingLifecycle.applyContextUsage)
 * @exports useContextUsageRefresh    — read the backend value when the active tab or
 *                                      its session changes, when its turn ends, and
 *                                      when the window regains focus
 * @exports onContextUsageStale / notifyContextUsageStale
 *                                    — typed signal: "the backend value changed outside
 *                                      a stream" (manual compact / refresh)
 */
import { useEffect, useRef } from 'react';
import type { ContextWarning } from './useChatStreamingLifecycle';
import type { StreamEvent } from '../types';

/** A backend context-usage snapshot (SSE ``context_warning`` event / GET body). */
export interface ContextUsageSnapshot {
  type?: string;
  level: 'ok' | 'warn' | 'critical';
  /** Null when there is no context to report (``source: 'none'``). */
  pct: number | null;
  tokensEst: number;
  window?: number | null;
  message?: string;
  /** live = measured by the running session; transcript = what --resume would
   *  reload; none = the next turn rebuilds the context. */
  source?: 'live' | 'transcript' | 'none';
  basis?: string;
  /** Epoch seconds at which the backend measured it. */
  measuredAt?: number;
}

/** The display value for a snapshot; null clears the ring. */
export function toContextWarning(s: ContextUsageSnapshot): ContextWarning | null {
  if (s.source === 'none' || s.pct === null || !Number.isFinite(s.pct)) return null;
  return {
    level: s.level,
    pct: s.pct,
    tokensEst: s.tokensEst ?? 0,
    message: s.message ?? `Context ${s.pct}% full`,
  };
}

/** True when ``s`` may replace a value measured at ``prevMeasuredAt``.
 *  A snapshot without a stamp (older backend) is always accepted. */
export function isNewerSnapshot(
  prevMeasuredAt: number | undefined,
  s: ContextUsageSnapshot,
): boolean {
  if (prevMeasuredAt === undefined || s.measuredAt === undefined) return true;
  return s.measuredAt >= prevMeasuredAt;
}

/** A ``context_warning`` stream event IS a snapshot; this narrows the shared
 *  StreamEvent type to it without a cast. */
export function snapshotFromEvent(e: StreamEvent): ContextUsageSnapshot {
  return {
    type: e.type,
    level: e.level ?? 'ok',
    pct: e.pct ?? null,
    tokensEst: e.tokensEst ?? 0,
    window: e.window,
    message: e.message,
    source: e.source,
    basis: e.basis,
    measuredAt: e.measuredAt,
  };
}

type StaleListener = (sessionId: string) => void;
const staleListeners = new Set<StaleListener>();

/** Subscribe to "this session's backend value changed outside a stream".
 *  Returns the unsubscribe function. */
export function onContextUsageStale(cb: StaleListener): () => void {
  staleListeners.add(cb);
  return () => { staleListeners.delete(cb); };
}

/** Signal that ``sessionId``'s context usage changed without an SSE event. */
export function notifyContextUsageStale(sessionId: string): void {
  for (const cb of Array.from(staleListeners)) {
    try {
      cb(sessionId);
    } catch (err) {
      console.warn('[contextUsage] stale listener failed', err);
    }
  }
}

/** Refresh triggers for the same tab+session within this window are one read. */
export const REFRESH_COALESCE_MS = 500;

/** Read the backend snapshot for the active tab whenever the backend value may
 *  have changed without a stream event reaching it: the active tab or its
 *  session changes (tab switch, app-restart hydrate, a new session id), its turn
 *  ends (an error/give-up path may have dropped the session's context), or the
 *  window regains focus (a background compaction while away). Fire-and-forget:
 *  the refresh never throws and never blocks render. */
export function useContextUsageRefresh(
  activeTabId: string | null | undefined,
  sessionId: string | null | undefined,
  isStreaming: boolean,
  refresh: (tabId: string) => Promise<void>,
): void {
  const latest = useRef({ activeTabId, sessionId, refresh });
  latest.current = { activeTabId, sessionId, refresh };
  // Several triggers can fire together (focus + visibilitychange; a tab switch
  // away from a streaming tab also flips isStreaming): one read per tab+session
  // within REFRESH_COALESCE_MS is enough.
  const lastRead = useRef<{ key: string; at: number }>({ key: '', at: 0 });
  const readNow = useRef((tab: string, sid: string, r: (t: string) => Promise<void>) => {
    const key = `${tab}:${sid}`;
    const now = Date.now();
    if (lastRead.current.key === key && now - lastRead.current.at < REFRESH_COALESCE_MS) return;
    lastRead.current = { key, at: now };
    void r(tab);
  });

  useEffect(() => {
    if (activeTabId && sessionId) readNow.current(activeTabId, sessionId, refresh);
  }, [activeTabId, sessionId, refresh]);

  const wasStreaming = useRef(isStreaming);
  useEffect(() => {
    const ended = wasStreaming.current && !isStreaming;
    wasStreaming.current = isStreaming;
    const { activeTabId: tab, sessionId: sid, refresh: r } = latest.current;
    if (ended && tab && sid) readNow.current(tab, sid, r);
  }, [isStreaming]);

  useEffect(() => {
    const onFocus = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      const { activeTabId: tab, sessionId: sid, refresh: r } = latest.current;
      if (tab && sid) readNow.current(tab, sid, r);
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onFocus);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onFocus);
    };
  }, []);
}
