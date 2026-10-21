/**
 * sessionEvents client — the one per-window subscription to GET /api/chat/events.
 *
 * Covers: connected only after the hello, cursors advance per session and are sent
 * on reconnect together with the hello's epoch for WATCHED sessions only, join
 * cursors (-1) for watched sessions without a cursor and a hello baseline after
 * it, reconnect after the stream ends, silence timeout, and listeners receiving
 * envelopes and resync but not hello/heartbeat.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createSessionEventsClient } from '../sessionEvents';

const enc = new TextEncoder();
const frame = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;

function controllableStream() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { ctrl = c; } });
  return {
    stream,
    push: (o: unknown) => ctrl.enqueue(enc.encode(frame(o))),
    end: () => ctrl.close(),
  };
}

const flush = async (n = 5) => { for (let i = 0; i < n; i++) await Promise.resolve(); await new Promise((r) => setTimeout(r, 0)); };

describe('sessionEvents client', () => {
  afterEach(() => { vi.useRealTimers(); });

  it('connects, reports connected after hello, dispatches envelopes, advances cursors', async () => {
    const s1 = controllableStream();
    const urls: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => { urls.push(url); return new Response(s1.stream); });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5] });
    c.watch(['A']);
    const got: unknown[] = [];
    c.subscribe((e) => got.push(e));
    const states: boolean[] = [];
    c.onConnectionChange((v) => states.push(v));
    c.start();
    await flush();
    expect(c.isConnected()).toBe(false);
    s1.push({ type: 'events_hello', epoch: 'E1', seqs: {} });
    await flush();
    expect(c.isConnected()).toBe(true);
    s1.push({ type: 'heartbeat' });
    s1.push({ type: 'text_delta', sessionId: 'A', turnId: 't', eventSeq: 3 });
    await flush();
    expect(got).toEqual([{ type: 'text_delta', sessionId: 'A', turnId: 't', eventSeq: 3 }]);
    expect(c.cursorFor('A')).toBe(3);
    expect(states).toEqual([true]);
    c.stop();
  });

  it('reconnects after the stream ends with cursors, joins and the epoch', async () => {
    const streams = [controllableStream(), controllableStream()];
    const urls: string[] = [];
    let i = 0;
    const fetchImpl = vi.fn(async (url: string) => { urls.push(url); return new Response(streams[i++].stream); });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5] });
    c.watch(['A', 'B']);
    c.start();
    await flush();
    streams[0].push({ type: 'events_hello', epoch: 'E1', seqs: {} });
    streams[0].push({ type: 'text_delta', sessionId: 'A', turnId: 't', eventSeq: 7 });
    await flush();
    streams[0].end();
    await flush();
    expect(c.isConnected()).toBe(false);
    await new Promise((r) => setTimeout(r, 20));
    await flush();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const u = new URL(urls[1]);
    // A has a cursor; B took the hello's baseline (0: no seq yet) — no join needed any more
    expect(JSON.parse(u.searchParams.get('cursors')!)).toEqual({ A: 7, B: 0 });
    expect(JSON.parse(new URL(urls[0]).searchParams.get('cursors')!)).toEqual({ A: -1, B: -1 });
    expect(u.searchParams.get('epoch')).toBe('E1');
    expect(new URL(urls[0]).pathname).toBe('/api/chat/events');
    c.stop();
  });

  it('a resync is dispatched and sets the cursor', async () => {
    const s1 = controllableStream();
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl: async () => new Response(s1.stream), reconnectDelaysMs: [5] });
    c.watch(['A']);
    const got: unknown[] = [];
    c.subscribe((e) => got.push(e));
    c.start();
    await flush();
    s1.push({ type: 'events_hello', epoch: 'E2', seqs: {} });
    s1.push({ type: 'resync', sessionId: 'A', eventSeq: 12 });
    await flush();
    expect(got).toEqual([{ type: 'resync', sessionId: 'A', eventSeq: 12 }]);
    expect(c.cursorFor('A')).toBe(12);
    c.stop();
  });

  it('a silent stream is cancelled and reconnected', async () => {
    const streams = [controllableStream(), controllableStream()];
    let i = 0;
    const fetchImpl = vi.fn(async () => new Response(streams[i++].stream));
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5], silenceMs: 30 });
    c.start();
    await flush();
    streams[0].push({ type: 'events_hello', epoch: 'E1', seqs: {} });
    await flush();
    expect(c.isConnected()).toBe(true);
    await new Promise((r) => setTimeout(r, 80));
    await flush();
    // the replacement stream is silent too, so it may already have cycled again
    expect(fetchImpl.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(c.isConnected()).toBe(false);
    c.stop();
  });

  it('a failing fetch keeps retrying and stop() ends it', async () => {
    let calls = 0;
    const fetchImpl = vi.fn(async () => { calls++; throw new Error('down'); });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5] });
    c.start();
    await new Promise((r) => setTimeout(r, 40));
    expect(calls).toBeGreaterThan(1);
    c.stop();
    const at = calls;
    await new Promise((r) => setTimeout(r, 30));
    expect(calls).toBe(at);
    expect(c.isConnected()).toBe(false);
  });

  it('start() twice keeps one stream (one per window)', async () => {
    const s1 = controllableStream();
    const fetchImpl = vi.fn(async () => new Response(s1.stream));
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5] });
    c.start();
    c.start();
    await flush();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    c.stop();
  });

  it('watching a new session while connected reconnects once with its join cursor; watching it again does not', async () => {
    const streams = [controllableStream(), controllableStream(), controllableStream()];
    const urls: string[] = [];
    let i = 0;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      urls.push(url);
      const s = streams[i++];
      init?.signal?.addEventListener('abort', () => { try { s.end(); } catch { /* closed */ } });
      return new Response(s.stream);
    });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5], joinDebounceMs: 5 });
    c.start();
    await flush();
    streams[0].push({ type: 'events_hello', epoch: 'E1', seqs: {} });
    await flush();
    c.watch(['R']);
    c.watch(['R']);
    await new Promise((r) => setTimeout(r, 40));
    await flush();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(JSON.parse(new URL(urls[1]).searchParams.get('cursors')!)).toEqual({ R: -1 });
    streams[1].push({ type: 'events_hello', epoch: 'E1', seqs: {} });
    await flush();
    c.watch(['R']);
    await new Promise((r) => setTimeout(r, 30));
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    c.stop();
  });

  it('reconnect sends cursors only for watched sessions, with a hello baseline', async () => {
    const streams = [controllableStream(), controllableStream(), controllableStream()];
    const urls: string[] = [];
    let i = 0;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      urls.push(url);
      const st = streams[i++];
      init?.signal?.addEventListener('abort', () => { try { st.end(); } catch { /* closed */ } });
      return new Response(st.stream);
    });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5], joinDebounceMs: 5 });
    c.watch(['T1']);
    c.start();
    await flush();
    streams[0].push({ type: 'events_hello', epoch: 'E1', seqs: { T1: 40, CH: 900 } });
    // a channel session no tab shows: delivered, but no cursor kept for it
    streams[0].push({ type: 'text_delta', sessionId: 'CH', turnId: 'x', eventSeq: 901 });
    await flush();
    expect(c.cursorFor('T1')).toBe(40);
    expect(c.cursorFor('CH')).toBeUndefined();
    // the tab of T1 closes, a tab shows CH mid-turn: CH must be JOINed (its turn
    // start was never shown), even though the window saw seq 901
    c.watch(['CH']);
    expect(c.cursorFor('T1')).toBeUndefined();
    await new Promise((r) => setTimeout(r, 30));
    await flush();
    expect(JSON.parse(new URL(urls[1]).searchParams.get('cursors')!)).toEqual({ CH: -1 });
    c.stop();
  });

  it('a session watched while a connect is in flight is joined after that connect\'s hello', async () => {
    const streams = [controllableStream(), controllableStream()];
    const urls: string[] = [];
    let i = 0;
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      urls.push(url);
      const st = streams[i++];
      init?.signal?.addEventListener('abort', () => { try { st.end(); } catch { /* closed */ } });
      return new Response(st.stream);
    });
    const c = createSessionEventsClient({ baseUrl: () => 'http://h', fetchImpl, reconnectDelaysMs: [5], joinDebounceMs: 5 });
    c.start();
    await flush();
    c.watch(['LATE']); // the first URL is already built without it
    streams[0].push({ type: 'events_hello', epoch: 'E1', seqs: { LATE: 77 } });
    await new Promise((r) => setTimeout(r, 30));
    await flush();
    expect(c.cursorFor('LATE')).toBeUndefined();
    expect(JSON.parse(new URL(urls[1]).searchParams.get('cursors')!)).toEqual({ LATE: -1 });
    c.stop();
  });
});
