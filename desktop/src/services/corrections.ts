/**
 * Correction ledger client (backend routers/corrections.py, core/ingestion/correction_ledger;
 * design docs/design/2026-10-30-autonomy-tenets-gaps.md §2).
 *
 * The backend opens a row when a person's chat message states a mistake; the person says
 * which class it belongs to. This module keeps one small cache per session, shared by every
 * message of that session, so the picker under a message needs no request of its own, and
 * one set of answered rows shared by the chat picker and the CDF panel's list, so a choice
 * made in one place removes the row from both at once.
 *
 * Key exports:
 * - `correctionsService` — `pending(sessionId)`, `pendingAll()`, `rate()`, `confirm(id, choice)`
 * - `usePendingCorrection(sessionId, clientId, sentAt)` — the row for one recent message, or null
 * - `markAnswered(id)` / `useAnswered()` — the rows answered in this window
 * - `mapPending`, `mapRate` — snake_case → camelCase
 */
import { useEffect, useSyncExternalStore } from 'react';
import axios from 'axios';
import api from './api';

export type CorrectionKind = 'entry' | 'golden' | 'ledger';

export interface CorrectionCandidate {
  key: string;
  kind: CorrectionKind;
  title: string;
}

export interface PendingCorrection {
  id: string;
  at: string;
  surface: string;
  clientId: string | null;
  text: string;
  candidates: CorrectionCandidate[];
}

/** The repeat-correction rate over the window (backend correction_ledger.rate). */
export interface CorrectionRate {
  windowDays: number;
  confirmed: number;
  unconfirmed: number;
  repeat: number;
  rate: number | null;
  smallSample: boolean;
  bar: number;
}

const num = (v: unknown, d = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : d);

export function mapPending(raw: Record<string, unknown>): PendingCorrection {
  const cands = Array.isArray(raw.candidates) ? raw.candidates : [];
  return {
    id: String(raw.id),
    at: typeof raw.at === 'string' ? raw.at : '',
    surface: typeof raw.surface === 'string' ? raw.surface : 'chat',
    clientId: typeof raw.client_id === 'string' ? raw.client_id : null,
    text: typeof raw.text === 'string' ? raw.text : '',
    candidates: cands
      .filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
      .map((c) => ({ key: String(c.key), kind: c.kind as CorrectionKind, title: String(c.title ?? '') })),
  };
}

export function mapRate(raw: Record<string, unknown>): CorrectionRate {
  const thresholds = (raw.thresholds && typeof raw.thresholds === 'object' ? raw.thresholds : {}) as Record<string, unknown>;
  return {
    windowDays: num(raw.window_days, 28), confirmed: num(raw.confirmed), unconfirmed: num(raw.unconfirmed),
    repeat: num(raw.repeat), rate: typeof raw.rate === 'number' ? raw.rate : null,
    smallSample: raw.small_sample === true, bar: num(thresholds.L2, 0.2),
  };
}

export const correctionsService = {
  async pending(sessionId: string): Promise<PendingCorrection[]> {
    const r = await api.get<{ corrections: Record<string, unknown>[] }>(
      '/ingestion/corrections/pending', { params: { session_id: sessionId } });
    return (r.data.corrections ?? []).map(mapPending);
  },
  /** Every correction still waiting (the CDF panel), oldest first. */
  async pendingAll(): Promise<PendingCorrection[]> {
    const r = await api.get<{ corrections: Record<string, unknown>[] }>('/ingestion/corrections/pending');
    return (r.data.corrections ?? []).map(mapPending);
  },
  async rate(): Promise<CorrectionRate> {
    const r = await api.get<Record<string, unknown>>('/ingestion/corrections/rate');
    return mapRate(r.data);
  },
  /** The person's choice: a candidate key, 'new' or 'not_correction'. A row that was
   *  classified already (409: the other place, or an earlier click) counts as done. */
  async confirm(id: string, choice: string): Promise<void> {
    try {
      await api.post(`/ingestion/corrections/${encodeURIComponent(id)}/confirm`, { choice }, {
        headers: { 'Content-Type': 'application/json', 'X-Swarm-User-Action': 'correction' },
      });
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 409) return;
      throw e;
    }
  },
};

// ── shared state ─────────────────────────────────────────────────────────────

/** A just-sent message's row is written by a backend task a moment after the send:
 *  ask again at these delays (ms) after the message was sent. */
const RETRY_AFTER_SEND_MS = [2500, 9000];
const MIN_REFETCH_MS = 2000;
/** The line shows under messages of the conversation in progress only; an older
 *  correction still waiting is in the CDF panel's list, not under an old message. */
export const INLINE_MAX_AGE_MS = 10 * 60 * 1000;

interface Entry { rows: PendingCorrection[]; fetchedAt: number; inflight: boolean; again: boolean; }
const cache = new Map<string, Entry>();
const answered = new Set<string>();
let answeredVersion = 0;
const listeners = new Set<() => void>();
const scheduled = new Set<string>();

function notify() { listeners.forEach((l) => l()); }
function subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }

async function refresh(sessionId: string): Promise<void> {
  const e = cache.get(sessionId) ?? { rows: [], fetchedAt: 0, inflight: false, again: false };
  const wait = MIN_REFETCH_MS - (Date.now() - e.fetchedAt);
  if (e.inflight || wait > 0) {
    // never drop a refresh: run one more once the running fetch or the throttle is over
    if (!e.again) {
      cache.set(sessionId, { ...e, again: true });
      if (!e.inflight) setTimeout(() => { void runAgain(sessionId); }, wait);
    }
    return;
  }
  cache.set(sessionId, { ...e, inflight: true, again: false });
  let rows = e.rows;
  try {
    rows = await correctionsService.pending(sessionId);
  } catch { /* keep what was shown */ }
  const after = cache.get(sessionId);
  cache.set(sessionId, { rows, fetchedAt: Date.now(), inflight: false, again: after?.again ?? false });
  notify();
  if (after?.again) setTimeout(() => { void runAgain(sessionId); }, MIN_REFETCH_MS);
}

async function runAgain(sessionId: string): Promise<void> {
  const e = cache.get(sessionId);
  if (!e || !e.again || e.inflight) return;
  cache.set(sessionId, { ...e, again: false, fetchedAt: 0 });
  await refresh(sessionId);
}

/** A row the person answered (here or in the other place): gone from both at once,
 *  also when a fetch that started before the answer comes back with it. */
export function markAnswered(id: string): void {
  answered.add(id);
  answeredVersion += 1;
  notify();
}

/** Re-renders when a row is answered; the function it returns says which. */
export function useAnswered(): (id: string) => boolean {
  useSyncExternalStore(subscribe, () => answeredVersion);
  return (id: string) => answered.has(id);
}

/** The pending correction for one recent message of a session (matched by client id). */
export function usePendingCorrection(sessionId: string | undefined, clientId: string | undefined,
  sentAt: string | undefined): PendingCorrection | null {
  const rows = useSyncExternalStore(subscribe, () => (sessionId ? cache.get(sessionId)?.rows : undefined));
  useSyncExternalStore(subscribe, () => answeredVersion);
  const sentMs = sentAt ? Date.parse(sentAt) : NaN;
  const recent = Number.isFinite(sentMs) && Date.now() - sentMs < INLINE_MAX_AGE_MS;
  useEffect(() => {
    if (!sessionId || !clientId || !recent) return;
    void refresh(sessionId);
    // the timers only refresh the shared cache, so they outlive the message's view (a
    // list that unmounts a row while scrolling must not lose the retry)
    for (const delay of RETRY_AFTER_SEND_MS) {
      const wait = delay - (Date.now() - sentMs);
      const key = `${sessionId}:${clientId}:${delay}`;
      if (wait > 0 && !scheduled.has(key)) {
        scheduled.add(key);
        setTimeout(() => { void refresh(sessionId); }, wait);
      }
    }
  }, [sessionId, clientId, sentMs, recent]);
  if (!clientId || !rows || !recent) return null;
  return rows.find((r) => r.clientId === clientId && !answered.has(r.id)) ?? null;
}

/** Test seam: reset the shared state. */
export function _resetCorrectionsCache(): void {
  cache.clear(); scheduled.clear(); answered.clear(); answeredVersion += 1; notify();
}
