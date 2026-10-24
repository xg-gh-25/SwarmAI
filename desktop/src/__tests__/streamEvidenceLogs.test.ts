/**
 * Evidence logs for the chat stream structure (docs/design/2026-10-24-chat-stream-structure.md A0).
 *
 * What is tested, by driving the real code (the hook with its subscription, the
 * real MessageStore reconcile):
 * - `[stream-restart]`: a repeated start of a block that is already streaming (the
 *   block key absorbs it, D4) is logged once per block, with the block key, the
 *   transport that delivered the start (`via`: post | sub, tagged at the entry
 *   point), the handler that rendered it, and the eventSeq. A new block, or a block's first
 *   start, logs nothing. The log carries no content.
 * - `[live-diverged]`: a reconcile that keeps the local copy because it is longer
 *   than the record (the "more-complete wins" branch) is logged with the message id,
 *   both lengths, block-kind counts and the path (load / reconcile-id /
 *   reconcile-cid); no content. A reconcile the
 *   record wins logs nothing. One log per (message, lengths), not per reconcile.
 * These are the evidence that gates Track B; they change no behaviour.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  return {
    listeners,
    connected: true,
    emit(e: unknown) { for (const l of listeners) l(e); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => fake.connected,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: () => () => {},
    watch: () => {},
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle, applyTextDelta, applyThinkingDelta } from '../hooks/useChatStreamingLifecycle';
import { MessageStore, messageStoreRegistry } from '../stores/MessageStore';
import type { Message, ChatMessage } from '../types';
import { testTabMap, createMockDeps, initTestTab, makeMessage, resetTestState } from './helpers/streamingTestUtils';
import type { StreamEvent } from '../types';

const env = (eventSeq: number, extra: Record<string, unknown>) =>
  ({ sessionId: 'S', turnId: 'I', eventSeq, ...extra });
const SECRET = 'the-answer-body-never-logged';

function logged(spy: ReturnType<typeof vi.spyOn>, tag: string): unknown[][] {
  return spy.mock.calls.filter((c: unknown[]) => c[0] === tag);
}

describe('[stream-restart] evidence log', () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    resetTestState();
    fake.listeners.clear();
    fake.connected = true;
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => { warn.mockRestore(); });

  function startTurn() {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('t1'); });
    testTabMap.get('t1')!.sessionId = 'S';
    act(() => { fake.emit(env(1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-q-asst' })); });
  }

  it('a repeated start of the streaming block is logged once, with block, source and eventSeq', () => {
    startTurn();
    act(() => {
      fake.emit(env(2, { type: 'text_start', block: 'r.1:0' }));
      fake.emit(env(3, { type: 'text_delta', block: 'r.1:0', text: `${SECRET} head ` }));
      fake.emit(env(4, { type: 'text_start', block: 'r.1:0' }));
      fake.emit(env(5, { type: 'text_delta', block: 'r.1:0', text: `${SECRET} tail` }));
      fake.emit(env(6, { type: 'text_start', block: 'r.1:0' }));
      fake.emit(env(7, { type: 'text_delta', block: 'r.1:0', text: ' more' }));
    });
    const calls = logged(warn, '[stream-restart]');
    expect(calls).toHaveLength(1);
    expect(calls[0][1]).toEqual({ block: 'r.1:0', kind: 'text', via: 'sub', handler: 'sub', eventSeq: 5 });
    expect(JSON.stringify(calls)).not.toContain(SECRET);
  });

  it('thinking blocks too', () => {
    startTurn();
    act(() => {
      fake.emit(env(2, { type: 'thinking_start', block: 'r.1:0' }));
      fake.emit(env(3, { type: 'thinking_delta', block: 'r.1:0', thinking: 'a' }));
      fake.emit(env(4, { type: 'thinking_start', block: 'r.1:0' }));
      fake.emit(env(5, { type: 'thinking_delta', block: 'r.1:0', thinking: 'b' }));
    });
    expect(logged(warn, '[stream-restart]').map((c) => c[1])).toEqual(
      [{ block: 'r.1:0', kind: 'thinking', via: 'sub', handler: 'sub', eventSeq: 5 }]);
  });

  it('the POST path is labelled post (the subscription is down, so the POST renders)', () => {
    fake.connected = false;
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('t2');
      result.current.setLocalSending(true, 't2');
      result.current.setMessages([makeMessage({ id: 'local-c-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('t2')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-c-asst', 't2');
    const send = (seq: number, extra: Record<string, unknown>) =>
      act(() => { post({ sessionId: 'S', turnId: 'T', eventSeq: seq, ...extra } as unknown as StreamEvent); });
    send(1, { type: 'turn_start', turnClientId: 'local-c-asst' });
    send(2, { type: 'text_start', block: 'p.1:0' });
    send(3, { type: 'text_delta', block: 'p.1:0', text: 'head ' });
    send(4, { type: 'text_start', block: 'p.1:0' });
    send(5, { type: 'text_delta', block: 'p.1:0', text: 'tail' });
    expect(logged(warn, '[stream-restart]').map((c) => c[1])).toEqual(
      [{ block: 'p.1:0', kind: 'text', via: 'post', handler: 'sub', eventSeq: 5 }]);
  });

  it('the updaters call onRestart only for a fresh start that continues the same block', () => {
    const calls: string[] = [];
    const on = (k: string) => () => calls.push(k);
    const base = { id: 'a', role: 'assistant', content: [], timestamp: 't' } as unknown as Message;
    let m = applyTextDelta(base, 'x', true, 'b1', on('first'));        // new block: no
    m = applyTextDelta(m, 'y', false, 'b1', on('delta'));              // a delta: no
    m = applyTextDelta(m, 'z', true, 'b1', on('restart'));             // repeated start: yes
    m = applyTextDelta(m, 'w', true, 'b2', on('next'));                // another block: no
    let t = applyThinkingDelta(base, 'x', true, 'b1', on('t-first'));
    t = applyThinkingDelta(t, 'y', false, 'b1', on('t-delta'));
    t = applyThinkingDelta(t, 'z', true, 'b1', on('t-restart'));
    expect(calls).toEqual(['restart', 't-restart']);
    expect(m.content).toHaveLength(2);
    expect(t.content).toHaveLength(1);
  });

  it('first starts of distinct blocks, and later deltas of a block, log nothing', () => {
    startTurn();
    act(() => {
      fake.emit(env(2, { type: 'text_start', block: 'r.1:0' }));
      fake.emit(env(3, { type: 'text_delta', block: 'r.1:0', text: 'one' }));
      fake.emit(env(4, { type: 'text_delta', block: 'r.1:0', text: ' and more' }));
      fake.emit(env(5, { type: 'text_start', block: 'r.2:0' }));
      fake.emit(env(6, { type: 'text_delta', block: 'r.2:0', text: 'two' }));
    });
    expect(logged(warn, '[stream-restart]')).toHaveLength(0);
  });
});

const asst = (id: string, text: string): Message =>
  ({ id, role: 'assistant', content: [{ type: 'text', text }], timestamp: 't' } as Message);
const row = (id: string, text: string): ChatMessage =>
  ({ id, role: 'assistant', content: [{ type: 'text', text }], createdAt: 't' } as unknown as ChatMessage);

describe('[live-diverged] evidence log', () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    messageStoreRegistry.clear();
    warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  });
  afterEach(() => { warn.mockRestore(); messageStoreRegistry.clear(); });

  it('a reconcile that keeps the longer local copy is logged once, without content', () => {
    const store = new MessageStore();
    store.append(asst('m1', `${SECRET} ${'A'.repeat(80)}`));
    store.reconcile([row('m1', 'AA')]);
    store.reconcile([row('m1', 'AA')]); // same divergence again: not logged again
    const calls = logged(warn, '[live-diverged]');
    expect(calls).toHaveLength(1);
    expect(calls[0][1]).toEqual({
      id: 'm1', localLen: SECRET.length + 81, dbLen: 2,
      localKinds: { text: 1 }, dbKinds: { text: 1 }, where: 'reconcile-id',
    });
    expect(JSON.stringify(calls)).not.toContain(SECRET);
    store.destroy();
  });

  it('a reconcile the record wins logs nothing', () => {
    const store = new MessageStore();
    store.append(asst('m1', 'short'));
    store.reconcile([row('m1', 'B'.repeat(300))]);
    expect(logged(warn, '[live-diverged]')).toHaveLength(0);
    store.destroy();
  });

  it('the load path (seedFromLoad) logs it too', () => {
    const store = new MessageStore();
    store.append(asst('m1', 'C'.repeat(90)));
    store.seedFromLoad([asst('m1', 'CC')]);
    expect(logged(warn, '[live-diverged]').map((c) => (c[1] as { dbLen: number }).dbLen)).toEqual([2]);
    store.destroy();
  });
});

describe('no-store fallback (A1: the same reducer as the store path)', () => {
  it('a repeated start of the same block continues it in the tab cache too, and is logged', () => {
    resetTestState();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('nf'); });
    messageStoreRegistry.clear();   // no store: the handler writes the tab's cache
    const tab = testTabMap.get('nf')!;
    tab.messages = [makeMessage({ id: 'nf-asst', role: 'assistant' })];
    const h = hook.current.createStreamHandler('nf-asst', 'nf');
    act(() => {
      h({ type: 'text_start', block: 'r.1:0' } as StreamEvent);
      h({ type: 'text_delta', block: 'r.1:0', text: 'head ' } as StreamEvent);
      h({ type: 'text_start', block: 'r.1:0' } as StreamEvent);
      h({ type: 'text_delta', block: 'r.1:0', text: 'tail' } as StreamEvent);
    });
    const blocks = tab.messages.find((m) => m.id === 'nf-asst')!.content;
    expect(blocks).toHaveLength(1);
    expect((blocks[0] as { text: string }).text).toBe('head tail');
    expect(warn.mock.calls.filter((c) => String(c[0]).includes('[stream-restart]'))).toHaveLength(1);
    warn.mockRestore();
  });
});
