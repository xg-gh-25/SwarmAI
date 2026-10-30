/**
 * Correction ledger client (backend routers/corrections.py, core/ingestion/correction_ledger;
 * design docs/design/2026-10-30-autonomy-tenets-gaps.md §2).
 *
 * The backend opens a row when a person's chat message states a mistake; the person says
 * which class it belongs to. This module keeps one small cache per session, shared by every
 * message of that session, so the picker under a message needs no request of its own.
 *
 * Key exports:
 * - `correctionsService` — `pending(sessionId)`, `confirm(id, choice)`
 * - `usePendingCorrection(sessionId, clientId, sentAt)` — the row for one message, or null
 * - `mapPending` — snake_case → camelCase for one row
 */
import { useEffect, useSyncExternalStore } from 'react';
import api from './api';

export interface CorrectionCandidate {
  key: string;
  kind: 'entry' | 'golden' | 'ledger';
  title: string;
}

export interface PendingCorrection {
  id: string;
  clientId: string | null;
  text: string;
  candidates: CorrectionCandidate[];
}

export function mapPending(raw: Record<string, unknown>): PendingCorrection {
  const cands = Array.isArray(raw.candidates) ? raw.candidates : [];
  return {
    id: String(raw.id),
    clientId: typeof raw.client_id === 'string' ? raw.client_id : null,
    text: typeof raw.text === 'string' ? raw.text : '',
    candidates: cands
      .filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
      .map((c) => ({ key: String(c.key), kind: c.kind as CorrectionCandidate['kind'], title: String(c.title ?? '') })),
  };
}

export const correctionsService = {
  async pending(sessionId: string): Promise<PendingCorrection[]> {
    const r = await api.get<{ corrections: Record<string, unknown>[] }>(
      '/ingestion/corrections/pending', { params: { session_id: sessionId } });
    return (r.data.corrections ?? []).map(mapPending);
  },
  /** The person's choice: a candidate key, 'new' or 'not_correction'. */
  async confirm(id: string, choice: string): Promise<void> {
    await api.post(`/ingestion/corrections/${encodeURIComponent(id)}/confirm`, { choice }, {
      headers: { 'Content-Type': 'application/json', 'X-Swarm-User-Action': 'correction' },
    });
  },
};

// ── one cache per session ────────────────────────────────────────────────────

/** A just-sent message's row is written by a backend task a moment after the send:
 *  ask again at these delays (ms) after the message was sent, and never after the last. */
const RETRY_AFTER_SEND_MS = [2500, 9000];
const MIN_REFETCH_MS = 2000;

interface Entry { rows: PendingCorrection[]; fetchedAt: number; inflight: boolean; }
const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();
const scheduled = new Set<string>();

function notify() { listeners.forEach((l) => l()); }

async function refresh(sessionId: string): Promise<void> {
  const e = cache.get(sessionId) ?? { rows: [], fetchedAt: 0, inflight: false };
  if (e.inflight || Date.now() - e.fetchedAt < MIN_REFETCH_MS) return;
  cache.set(sessionId, { ...e, inflight: true });
  try {
    const rows = await correctionsService.pending(sessionId);
    cache.set(sessionId, { rows, fetchedAt: Date.now(), inflight: false });
  } catch {
    cache.set(sessionId, { ...e, fetchedAt: Date.now(), inflight: false });
  }
  notify();
}

function subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }

/** Drop a row once the person chose (the picker disappears at once). */
export function forgetCorrection(sessionId: string, id: string): void {
  const e = cache.get(sessionId);
  if (!e) return;
  cache.set(sessionId, { ...e, rows: e.rows.filter((r) => r.id !== id) });
  notify();
}

/** The pending correction for one message of a session (matched by its client id). */
export function usePendingCorrection(sessionId: string | undefined, clientId: string | undefined,
  sentAt: string | undefined): PendingCorrection | null {
  const rows = useSyncExternalStore(subscribe, () => (sessionId ? cache.get(sessionId)?.rows : undefined));
  useEffect(() => {
    if (!sessionId || !clientId) return;
    void refresh(sessionId);
    const age = sentAt ? Date.now() - Date.parse(sentAt) : Infinity;
    // the timers only refresh the shared cache, so they outlive the message's view (a
    // list that unmounts a row while scrolling must not lose the retry)
    for (const delay of RETRY_AFTER_SEND_MS) {
      const wait = delay - age;
      const key = `${sessionId}:${clientId}:${delay}`;
      if (wait > 0 && !scheduled.has(key)) {
        scheduled.add(key);
        setTimeout(() => { void refresh(sessionId); }, wait);
      }
    }
  }, [sessionId, clientId, sentAt]);
  if (!clientId || !rows) return null;
  return rows.find((r) => r.clientId === clientId) ?? null;
}

/** Test seam: reset the shared cache. */
export function _resetCorrectionsCache(): void { cache.clear(); scheduled.clear(); notify(); }
