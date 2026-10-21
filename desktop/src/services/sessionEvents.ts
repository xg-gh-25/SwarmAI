/**
 * Session events client — the ONE subscription per desktop window to
 * `GET /api/chat/events` (backend core/session_events).
 *
 * Every turn of every session is published there: user turns, drained pending
 * messages, CLI-initiated turns after a background task, channel turns,
 * continuations. This client keeps a single long-lived stream for the whole
 * window (never one per tab: the webview talks HTTP/1.1 to the daemon and caps
 * connections per host, so per-tab streams would starve ordinary requests).
 *
 * - `connected` turns true on the server's `events_hello` and false when the
 *   stream ends, errors, or stays silent past `silenceMs` (the server sends a
 *   heartbeat every 15s).
 * - It keeps a cursor (the last `eventSeq` received) for each WATCHED session —
 *   the sessions shown in tabs, set by `watch(ids)` — and the hello's `epoch`; a
 *   reconnect sends both, so the server replays what was missed, or answers
 *   `resync` when it cannot (buffer too short, daemon restarted). Cursors of
 *   sessions no longer watched are dropped, so the set stays bounded by the tabs.
 *   A newly watched session without a cursor is joined (cursor -1): the server
 *   replays its active turn, so a tab restored after a reload sees a turn that is
 *   still running; the hello's `seqs` then give it a baseline for later reconnects.
 * - It reconnects forever with the given back-off until `stop()`.
 * - Listeners get every envelope and `resync`; `events_hello` and heartbeats stay
 *   internal. Routing to tabs is the caller's job (hooks/sessionTurnRouter.ts).
 */
import type { StreamEvent } from '../types';
import { consumeSSEStream } from './chat';
import { getApiBaseUrl } from './tauri';

export interface SessionEnvelope extends StreamEvent {
  sessionId?: string;
  turnId?: string;
  turnClientId?: string;
  eventSeq?: number;
}

export interface SessionEventsClient {
  start(): void;
  stop(): void;
  isConnected(): boolean;
  subscribe(listener: (ev: SessionEnvelope) => void): () => void;
  onConnectionChange(cb: (connected: boolean) => void): () => void;
  /** the sessions shown in tabs: cursors are kept (and sent) for these only */
  watch(sessionIds: string[]): void;
  cursorFor(sessionId: string): number | undefined;
}

export interface SessionEventsOptions {
  baseUrl?: () => string;
  fetchImpl?: (url: string, init?: RequestInit) => Promise<Response>;
  reconnectDelaysMs?: number[];
  silenceMs?: number;
  /** batch window for join() calls made while connected (one reconnect per batch) */
  joinDebounceMs?: number;
}

const DEFAULT_DELAYS = [500, 1000, 2000, 5000];
const DEFAULT_SILENCE_MS = 45_000;
const JOIN = -1;

export function createSessionEventsClient(opts: SessionEventsOptions = {}): SessionEventsClient {
  const baseUrl = opts.baseUrl ?? getApiBaseUrl;
  const doFetch = opts.fetchImpl ?? ((u: string, i?: RequestInit) => fetch(u, i));
  const delays = opts.reconnectDelaysMs?.length ? opts.reconnectDelaysMs : DEFAULT_DELAYS;
  const silenceMs = opts.silenceMs ?? DEFAULT_SILENCE_MS;
  const joinDebounceMs = opts.joinDebounceMs ?? 200;

  const listeners = new Set<(ev: SessionEnvelope) => void>();
  const connListeners = new Set<(c: boolean) => void>();
  const cursors = new Map<string, number>();
  const joins = new Set<string>();
  let watched = new Set<string>();
  // the joins the in-flight connection's URL carried: only those are answered by its hello
  let sentJoins = new Set<string>();
  let epoch: string | undefined;
  let running = false;
  let connected = false;
  let attempt = 0;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let silenceTimer: ReturnType<typeof setTimeout> | undefined;
  let rejoinTimer: ReturnType<typeof setTimeout> | undefined;

  const setConnected = (v: boolean) => {
    if (connected === v) return;
    connected = v;
    for (const cb of connListeners) {
      try { cb(v); } catch (e) { console.error('[sessionEvents] connection listener', e); }
    }
  };

  const buildUrl = () => {
    const cur: Record<string, number> = {};
    for (const [sid, seq] of cursors) cur[sid] = seq;
    for (const sid of joins) if (!(sid in cur)) cur[sid] = JOIN;
    sentJoins = new Set([...joins].filter((sid) => cur[sid] === JOIN));
    const q = new URLSearchParams();
    if (Object.keys(cur).length) q.set('cursors', JSON.stringify(cur));
    if (epoch) q.set('epoch', epoch);
    const qs = q.toString();
    return `${baseUrl()}/api/chat/events${qs ? `?${qs}` : ''}`;
  };

  const dispatch = (ev: SessionEnvelope) => {
    if (ev.type === 'events_hello') {
      epoch = (ev as { epoch?: string }).epoch ?? epoch;
      const seqs = (ev as { seqs?: Record<string, number> }).seqs ?? {};
      // a join this connection carried is answered (its active turn follows):
      // baseline it for later reconnects
      for (const sid of sentJoins) {
        if (watched.has(sid) && !cursors.has(sid)) cursors.set(sid, typeof seqs[sid] === 'number' ? seqs[sid] : 0);
        joins.delete(sid);
      }
      sentJoins = new Set();
      attempt = 0;
      setConnected(true);
      // a session watched while this connection was being set up is still to join
      if (joins.size) scheduleRejoin();
      return;
    }
    if (ev.type === 'heartbeat') return;
    if (ev.sessionId && typeof ev.eventSeq === 'number') {
      // a session still waiting for its join gets no cursor yet: the join's
      // replay must start at its active turn's turn_start
      if (watched.has(ev.sessionId) && !joins.has(ev.sessionId)) {
        const prev = cursors.get(ev.sessionId);
        if (ev.type === 'resync' || prev === undefined || ev.eventSeq > prev) cursors.set(ev.sessionId, ev.eventSeq);
      }
    }
    for (const l of listeners) {
      try { l(ev); } catch (e) { console.error('[sessionEvents] listener', e); }
    }
  };

  const scheduleRejoin = () => {
    if (!connected || rejoinTimer !== undefined) return;
    rejoinTimer = setTimeout(() => {
      rejoinTimer = undefined;
      controller?.abort();
    }, joinDebounceMs);
  };

  const scheduleReconnect = () => {
    setConnected(false);
    if (!running || retryTimer !== undefined) return;
    const d = delays[Math.min(attempt, delays.length - 1)];
    attempt += 1;
    retryTimer = setTimeout(() => { retryTimer = undefined; connect(); }, d);
  };

  const connect = () => {
    if (!running || controller) return;
    const ctl = new AbortController();
    controller = ctl;
    const finish = () => {
      if (silenceTimer !== undefined) { clearTimeout(silenceTimer); silenceTimer = undefined; }
      if (controller === ctl) controller = null;
      scheduleReconnect();
    };
    doFetch(buildUrl(), { method: 'GET', credentials: 'include', signal: ctl.signal })
      .then(async (resp) => {
        if (!resp.ok || !resp.body) throw new Error(`events stream HTTP ${resp.status}`);
        const reader = resp.body.getReader();
        const arm = (r: ReadableStreamDefaultReader<Uint8Array>) => {
          if (silenceTimer !== undefined) clearTimeout(silenceTimer);
          silenceTimer = setTimeout(() => { r.cancel().catch(() => {}); }, silenceMs);
        };
        const disarm = () => {
          if (silenceTimer !== undefined) { clearTimeout(silenceTimer); silenceTimer = undefined; }
        };
        await consumeSSEStream(reader, arm, disarm, dispatch, finish, finish);
      })
      .catch((err) => {
        if (err?.name !== 'AbortError') console.warn('[sessionEvents] stream failed:', err?.message ?? err);
        finish();
      });
  };

  return {
    start() {
      if (running) return;
      running = true;
      attempt = 0;
      connect();
    },
    stop() {
      running = false;
      if (retryTimer !== undefined) { clearTimeout(retryTimer); retryTimer = undefined; }
      if (silenceTimer !== undefined) { clearTimeout(silenceTimer); silenceTimer = undefined; }
      if (rejoinTimer !== undefined) { clearTimeout(rejoinTimer); rejoinTimer = undefined; }
      const ctl = controller;
      controller = null;
      ctl?.abort();
      setConnected(false);
    },
    isConnected: () => connected,
    subscribe(l) { listeners.add(l); return () => { listeners.delete(l); }; },
    onConnectionChange(cb) { connListeners.add(cb); return () => { connListeners.delete(cb); }; },
    watch(ids) {
      const next = new Set(ids.filter(Boolean));
      for (const sid of [...cursors.keys()]) if (!next.has(sid)) cursors.delete(sid);
      for (const sid of [...joins]) if (!next.has(sid)) joins.delete(sid);
      let needJoin = false;
      for (const sid of next) {
        if (watched.has(sid) || cursors.has(sid)) continue;
        // always a JOIN: having seen some of a session's events does not mean the
        // tab saw its running turn's turn_start
        joins.add(sid);
        needJoin = true;
      }
      watched = next;
      // newly shown sessions without a cursor → one reconnect per batch, so the
      // server replays their active turn (a connect in flight picks them up after
      // its hello)
      if (needJoin) scheduleRejoin();
    },
    cursorFor: (sid) => cursors.get(sid),
  };
}

/** The window's single client (I5). */
export const sessionEvents: SessionEventsClient = createSessionEventsClient();
