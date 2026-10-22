/**
 * useChatStreamingLifecycle × session subscription — the wiring of
 * hooks/sessionTurnRouter into the real handlers.
 *
 * - AC2: an idle tab renders a turn it did not send (a CLI-initiated turn) live
 *   from the subscription: spinner on at turn_start, content on each event,
 *   off at turn_end. The bubble carries the turn's persisted client_id.
 * - I6: with the subscription connected, POST copies of turn events do not
 *   render; the subscription copy renders once.
 * - AC3/F4: a POST lost mid-turn while the subscription is connected shows no
 *   reconnecting state; the subscription finishes the turn.
 * - A drained turn delivers the tab's queued message: the queue mirror is not
 *   re-sent, the synthetic bubble goes at turn_end, waiting-for-busy clears.
 * - With the subscription connected, SESSION_BUSY starts no fast DB poll.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { StreamEvent } from '../types';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  const conn = new Set<(c: boolean) => void>();
  return {
    connected: true,
    listeners,
    conn,
    joined: [] as string[],
    emit(e: unknown) { for (const l of listeners) l(e); },
    setConnected(v: boolean) { this.connected = v; for (const c of conn) c(v); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => fake.connected,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: (cb: (c: boolean) => void) => { fake.conn.add(cb); return () => fake.conn.delete(cb); },
    watch: (ids: string[]) => { fake.joined.push(...ids); },
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import {
  testTabMap, createMockDeps, initTestTab, makeMessage, resetTestState,
} from './helpers/streamingTestUtils';

const env = (turnId: string, eventSeq: number, extra: Record<string, unknown>) =>
  ({ sessionId: 'S', turnId, eventSeq, ...extra });
const text = (t: string) => ({ type: 'assistant', content: [{ type: 'text', text: t }] });
const lastAssistant = (tabId: string, rendered?: { role: string }[]) => {
  const ms = (rendered ?? messageStoreRegistry.get(tabId)?.messages ?? []) as { role: string; id: string; content: unknown; metadata?: Record<string, unknown> }[];
  return [...ms].reverse().find((m) => m.role === 'assistant');
};

describe('session turn wiring', () => {
  beforeEach(() => {
    resetTestState();
    fake.connected = true;
    fake.listeners.clear();
    fake.conn.clear();
  });

  it('an idle tab renders a CLI-initiated turn live from the subscription (AC2)', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('t1'); });
    testTabMap.get('t1')!.sessionId = 'S';
    act(() => { fake.emit(env('I', 1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-q-asst' })); });
    expect(testTabMap.get('t1')!.isStreaming).toBe(true);
    const bubble = lastAssistant('t1')!;
    expect(bubble.id).toBe('local-sub-I');
    expect(bubble.metadata?.client_id).toBe('injected-q-asst');
    expect(bubble.metadata?.turn_source).toBe('injected');
    act(() => { fake.emit(env('I', 2, text('done in background'))); });
    expect(JSON.stringify(lastAssistant('t1')!.content)).toContain('done in background');
    act(() => { fake.emit(env('I', 3, { type: 'result' })); });
    act(() => { fake.emit(env('I', 4, { type: 'turn_end' })); });
    expect(testTabMap.get('t1')!.isStreaming).toBe(false);
    expect(result.current).toBeTruthy();
  });

  it('with the subscription connected, POST copies of turn events do not render (I6)', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('t2');
      result.current.setIsStreaming(true, 't2');
      result.current.setMessages([makeMessage({ id: 'local-c-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('t2')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-c-asst', 't2');
    act(() => { post(env('T', 1, { type: 'turn_start' }) as StreamEvent); });
    act(() => { post(env('T', 2, text('from-post')) as StreamEvent); });
    expect(JSON.stringify(lastAssistant('t2', result.current.messages)!.content)).not.toContain('from-post');
    act(() => { fake.emit(env('T', 1, { type: 'turn_start' })); });
    act(() => { fake.emit(env('T', 2, text('from-post'))); });
    act(() => { fake.emit(env('T', 2, text('from-post'))); }); // duplicate seq
    const body = JSON.stringify(lastAssistant('t2', result.current.messages)!.content);
    expect(body.match(/from-post/g)?.length).toBe(1);
  });

  it('a POST lost mid-turn detaches; the subscription finishes the turn (AC3/F4)', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('t3');
      result.current.setIsStreaming(true, 't3');
      result.current.setMessages([makeMessage({ id: 'local-d-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('t3')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-d-asst', 't3');
    const complete = result.current.createCompleteHandler('t3');
    const lost = result.current.createDisconnectHandler('t3');
    act(() => { post(env('T', 1, { type: 'turn_start' }) as StreamEvent); });
    act(() => { fake.emit(env('T', 1, { type: 'turn_start' })); });
    act(() => { lost(); });
    expect(testTabMap.get('t3')!.isReconnecting).toBeFalsy();
    act(() => { fake.emit(env('T', 2, text('after the drop'))); });
    expect(JSON.stringify(lastAssistant('t3', result.current.messages)!.content)).toContain('after the drop');
    act(() => { fake.emit(env('T', 3, { type: 'result' })); });
    act(() => { fake.emit(env('T', 4, { type: 'turn_end' })); });
    expect(testTabMap.get('t3')!.isStreaming).toBe(false);
    act(() => { complete(); }); // a late [DONE] changes nothing
    expect(testTabMap.get('t3')!.isStreaming).toBe(false);
  });

  it('subscription down: the POST renders and its [DONE] completes (fallback)', () => {
    fake.connected = false;
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('t4');
      result.current.setIsStreaming(true, 't4');
      result.current.setMessages([makeMessage({ id: 'local-e-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('t4')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-e-asst', 't4');
    const complete = result.current.createCompleteHandler('t4');
    act(() => { post(env('T', 1, { type: 'turn_start' }) as StreamEvent); });
    act(() => { post(env('T', 2, text('via post')) as StreamEvent); });
    expect(JSON.stringify(lastAssistant('t4', result.current.messages)!.content)).toContain('via post');
    act(() => { complete(); });
    expect(testTabMap.get('t4')!.isStreaming).toBe(false);
  });

  it('a drained turn retires the queued mirror without re-sending it', () => {
    const onDrainQueue = vi.fn();
    renderHook(() => useChatStreamingLifecycle({ ...createMockDeps(), onDrainQueue }));
    act(() => { initTestTab('t5', [makeMessage({ id: 'queued-1', role: 'user', content: [{ type: 'text', text: 'q' }] })]); });
    messageStoreRegistry.getOrCreate('t5').appendMany([makeMessage({ id: 'queued-1', role: 'user', content: [{ type: 'text', text: 'q' }] })]);
    const tab = testTabMap.get('t5')!;
    tab.sessionId = 'S';
    tab.isWaitingForBusy = true;
    tab.queuedMessage = { messageId: 'queued-1', text: 'q', attachments: [] } as unknown as typeof tab.queuedMessage;
    act(() => { fake.emit(env('D', 1, { type: 'turn_start', source: 'drain', turnClientId: 'drain-z-asst' })); });
    expect(tab.queuedMessage).toBeUndefined();
    act(() => { fake.emit(env('D', 2, text('answer'))); });
    act(() => { fake.emit(env('D', 3, { type: 'turn_end' })); });
    expect(tab.isWaitingForBusy).toBe(false);
    expect((messageStoreRegistry.get('t5')?.messages ?? []).some((m) => m.id === 'queued-1')).toBe(false);
    return new Promise<void>((r) => setTimeout(() => { expect(onDrainQueue).not.toHaveBeenCalled(); r(); }, 10));
  });

  it('SESSION_BUSY leaves the message to the server: waiting state on, no local re-send', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('t6');
      result.current.setIsStreaming(true, 't6');
      result.current.setMessages([makeMessage({ id: 'local-f-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('t6')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-f-asst', 't6');
    const getMsgs = vi.spyOn(chatService, 'getSessionMessagesReconcileTail');
    act(() => { post({ type: 'error', code: 'SESSION_BUSY', message: 'busy' } as StreamEvent); });
    expect(testTabMap.get('t6')!.isWaitingForBusy).toBe(true);
    expect(getMsgs).not.toHaveBeenCalled(); // no fast DB poll
    getMsgs.mockRestore();
  });

  it('a CLI-initiated turn after an assistant message continues that bubble, as the reloaded history does', () => {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('h1'); });
    const store = messageStoreRegistry.getOrCreate('h1');
    store.appendMany([
      makeMessage({ id: 'u-1', role: 'user', content: [{ type: 'text', text: 'audit it' }] }),
      makeMessage({ id: 'a-1', role: 'assistant', content: [{ type: 'text', text: 'four agents started' }], metadata: { client_id: 'local-u-asst' } }),
    ]);
    testTabMap.get('h1')!.sessionId = 'S';
    const agentTurn = (turnId: string, seq: number, body: string) => {
      act(() => { fake.emit(env(turnId, seq, { type: 'turn_start', source: 'injected', turnClientId: `injected-${turnId}-asst` })); });
      act(() => { fake.emit(env(turnId, seq + 1, text(body))); });
      act(() => { fake.emit(env(turnId, seq + 2, { type: 'result' })); });
      act(() => { fake.emit(env(turnId, seq + 3, { type: 'turn_end' })); });
    };
    agentTurn('I1', 1, 'agent one is back');
    agentTurn('I2', 5, 'agent two is back');
    const assistants = (messageStoreRegistry.get('h1')?.messages ?? []).filter((m) => m.role === 'assistant');
    expect(assistants.map((m) => m.id)).toEqual(['a-1']);
    const body = JSON.stringify(assistants[0].content);
    for (const t of ['four agents started', 'agent one is back', 'agent two is back']) {
      expect(body.match(new RegExp(t, 'g'))?.length).toBe(1);
    }
    expect(body.indexOf('four agents')).toBeLessThan(body.indexOf('agent one'));
    expect(body.indexOf('agent one')).toBeLessThan(body.indexOf('agent two'));
    expect(testTabMap.get('h1')!.isStreaming).toBe(false);
  });

  it('a CLI-initiated turn after a user message, and a user turn, each get their own bubble', () => {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('h2'); });
    const store = messageStoreRegistry.getOrCreate('h2');
    store.appendMany([
      makeMessage({ id: 'a-0', role: 'assistant', content: [{ type: 'text', text: 'earlier' }] }),
      makeMessage({ id: 'u-2', role: 'user', content: [{ type: 'text', text: 'next' }] }),
    ]);
    testTabMap.get('h2')!.sessionId = 'S';
    act(() => { fake.emit(env('J', 1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-J-asst' })); });
    act(() => { fake.emit(env('J', 2, text('cli turn'))); });
    act(() => { fake.emit(env('J', 3, { type: 'turn_end' })); });
    act(() => { fake.emit(env('K', 4, { type: 'turn_start', source: 'chat', turnClientId: 'injected-K-asst' })); });
    act(() => { fake.emit(env('K', 5, text('user turn'))); });
    act(() => { fake.emit(env('K', 6, { type: 'turn_end' })); });
    const ids = (messageStoreRegistry.get('h2')?.messages ?? []).filter((m) => m.role === 'assistant').map((m) => m.id);
    expect(ids).toEqual(['a-0', 'local-sub-J', 'local-sub-K']);
  });

  it('a replay of a hosted CLI turn re-renders it once into the same bubble', () => {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('h3'); });
    messageStoreRegistry.getOrCreate('h3').appendMany([
      makeMessage({ id: 'u-3', role: 'user', content: [{ type: 'text', text: 'go' }] }),
      makeMessage({ id: 'a-3', role: 'assistant', content: [{ type: 'text', text: 'started' }] }),
    ]);
    testTabMap.get('h3')!.sessionId = 'S';
    act(() => { fake.emit(env('L', 1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-L-asst' })); });
    act(() => { fake.emit(env('L', 2, text('half of agent L'))); });
    // resync: the subscription replays the running turn from its turn_start
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 0, activeTurnId: 'L' }); });
    act(() => { fake.emit(env('L', 1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-L-asst' })); });
    act(() => { fake.emit(env('L', 2, text('half of agent L'))); });
    act(() => { fake.emit(env('L', 3, text('all of agent L'))); });
    act(() => { fake.emit(env('L', 4, { type: 'turn_end' })); });
    const assistants = (messageStoreRegistry.get('h3')?.messages ?? []).filter((m) => m.role === 'assistant');
    expect(assistants.map((m) => m.id)).toEqual(['a-3']);
    const body = JSON.stringify(assistants[0].content);
    expect(body.match(/started/g)?.length).toBe(1);
    expect(body.match(/half of agent L/g)?.length ?? 0).toBeLessThanOrEqual(1);
    expect(body).toContain('all of agent L');
  });

  it('a tab that joins a running turn after a reload replaces the partial bubble, no duplicate (F8)', () => {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('t7'); });
    const store = messageStoreRegistry.getOrCreate('t7');
    store.appendMany([
      makeMessage({ id: 'u-1', role: 'user', content: [{ type: 'text', text: 'hi' }] }),
      makeMessage({ id: 'db-row-9', role: 'assistant', content: [{ type: 'text', text: 'partial' }], metadata: { client_id: 'local-z-asst' } }),
    ]);
    testTabMap.get('t7')!.sessionId = 'S';
    // the JOIN replay starts at the running turn's turn_start
    act(() => { fake.emit(env('R', 10, { type: 'turn_start', turnClientId: 'local-z-asst' })); });
    act(() => { fake.emit(env('R', 11, text('partial and more'))); });
    const assistants = (messageStoreRegistry.get('t7')?.messages ?? []).filter((m) => m.role === 'assistant');
    expect(assistants.map((m) => m.id)).toEqual(['local-z-asst']);
    expect(JSON.stringify(assistants[0].content)).toContain('partial and more');
    expect(JSON.stringify(assistants[0].content).match(/partial/g)?.length).toBe(1);
  });

  it.each([['POST first'], ['subscription first'], ['subscription down']])('a new tab\'s first send binds its session and renders once (%s)', (order) => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('n1');
      result.current.setIsStreaming(true, 'n1');
      result.current.setMessages([makeMessage({ id: 'local-n-asst', role: 'assistant', content: [] })]);
    });
    expect(testTabMap.get('n1')!.sessionId).toBeUndefined();
    const post = result.current.createStreamHandler('local-n-asst', 'n1');
    const complete = result.current.createCompleteHandler('n1');
    const evs = [
      env('T', 1, { type: 'turn_start', turnClientId: 'local-n-asst' }),
      env('T', 2, { type: 'session_start', sessionId: 'S' }),
      env('T', 3, text('first answer')),
    ];
    if (order === 'subscription down') fake.connected = false;
    for (const e of evs) {
      if (order === 'subscription down') { act(() => { post(e as StreamEvent); }); continue; }
      if (order === 'POST first') { act(() => { post(e as StreamEvent); }); act(() => { fake.emit(e); }); }
      else { act(() => { fake.emit(e); }); act(() => { post(e as StreamEvent); }); }
    }
    expect(testTabMap.get('n1')!.sessionId).toBe('S');
    const shown = result.current.messages.length ? result.current.messages : (messageStoreRegistry.get('n1')?.messages ?? []);
    if (!lastAssistant('n1', shown)) throw new Error('no assistant: ' + JSON.stringify({ react: result.current.messages, store: messageStoreRegistry.get('n1')?.messages, tab: testTabMap.get('n1')?.messages }));
    const body = JSON.stringify(lastAssistant('n1', shown)!.content);
    expect(body.match(/first answer/g)?.length).toBe(1);
    act(() => { complete(); });
    act(() => { fake.emit(env('T', 5, { type: 'turn_end' })); });
    expect(testTabMap.get('n1')!.isStreaming).toBe(false);
  });

  it('a question asked inside a turn the tab did not send shows up live and stays after the turn ends (AC4)', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('q1'); });
    testTabMap.get('q1')!.sessionId = 'S';
    act(() => { fake.emit(env('D', 1, { type: 'turn_start', source: 'drain', turnClientId: 'drain-q-asst' })); });
    act(() => { fake.emit(env('D', 2, { type: 'ask_user_question', toolUseId: 'tu-1', questions: [{ question: 'Which?', header: 'h', options: [{ label: 'A', description: '' }], multiSelect: false }] })); });
    expect(testTabMap.get('q1')!.pendingQuestion?.toolUseId).toBe('tu-1');
    act(() => { fake.emit(env('D', 3, { type: 'turn_end' })); });
    expect(testTabMap.get('q1')!.pendingQuestion?.toolUseId).toBe('tu-1');
    expect(result.current).toBeTruthy();
  });

  it('a resync reloads the tab from the DB', () => {
    const tail = vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockResolvedValue([]);
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('r1'); });
    testTabMap.get('r1')!.sessionId = 'S';
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 99 }); });
    expect(tail).toHaveBeenCalledWith('S', expect.any(Number));
    tail.mockRestore();
  });

  it('a POST stream error while the subscription carries the turn is not shown as an error', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('e1');
      result.current.setIsStreaming(true, 'e1');
      result.current.setMessages([makeMessage({ id: 'local-g-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('e1')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-g-asst', 'e1');
    const onError = result.current.createErrorHandler('local-g-asst', 'e1');
    act(() => { post(env('T', 1, { type: 'turn_start' }) as StreamEvent); });
    act(() => { fake.emit(env('T', 1, { type: 'turn_start' })); });
    act(() => { onError(new Error('Stream stalled: backend not alive after 90s of silence')); });
    expect(testTabMap.get('e1')!.isStreaming).toBe(true);
    expect(JSON.stringify(lastAssistant('e1', result.current.messages)!.content)).not.toContain('stalled');
    act(() => { fake.emit(env('T', 2, text('still going'))); });
    expect(JSON.stringify(lastAssistant('e1', result.current.messages)!.content)).toContain('still going');
  });

  it('when the subscription drops mid-turn, the POST copies it never delivered render', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('s1');
      result.current.setIsStreaming(true, 's1');
      result.current.setMessages([makeMessage({ id: 'local-h-asst', role: 'assistant', content: [] })]);
    });
    testTabMap.get('s1')!.sessionId = 'S';
    const post = result.current.createStreamHandler('local-h-asst', 's1');
    act(() => { post(env('T', 1, { type: 'turn_start' }) as StreamEvent); });
    act(() => { fake.emit(env('T', 1, { type: 'turn_start' })); });
    act(() => { post(env('T', 2, text('only on the POST')) as StreamEvent); });
    expect(JSON.stringify(lastAssistant('s1', result.current.messages)!.content)).not.toContain('only on the POST');
    act(() => { fake.setConnected(false); });
    expect(JSON.stringify(lastAssistant('s1', result.current.messages)!.content)).toContain('only on the POST');
  });

  it('an answer turn rebuilt from its replay replaces its own bubble and keeps the question turn', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab('a1');
      result.current.setIsStreaming(true, 'a1');
    });
    const store = messageStoreRegistry.getOrCreate('a1');
    store.appendMany([
      makeMessage({ id: 'local-q-asst', role: 'assistant', content: [{ type: 'text', text: 'which one?' }], metadata: { client_id: 'local-q-asst' } }),
      makeMessage({ id: '1700000000000', role: 'assistant', content: [] }),
    ]);
    testTabMap.get('a1')!.sessionId = 'S';
    // the answer's POST: numeric placeholder, turnClientId = the question turn's key
    const post = result.current.createStreamHandler('1700000000000', 'a1');
    const start = env('A', 20, { type: 'turn_start', source: 'answer', turnClientId: 'local-q-asst' });
    act(() => { post(start as StreamEvent); });
    act(() => { fake.emit(start); });
    act(() => { fake.emit({ type: 'resync', sessionId: 'S', eventSeq: 19, activeTurnId: 'A' }); });
    act(() => { fake.emit(start); });
    act(() => { fake.emit(env('A', 21, text('answered'))); });
    const ids = (messageStoreRegistry.get('a1')?.messages ?? []).filter((m) => m.role === 'assistant').map((m) => m.id);
    expect(ids).toContain('local-q-asst');           // the question turn stays
    expect(ids).not.toContain('1700000000000');      // the POST's own bubble is replaced
    expect(ids.filter((x) => x === 'local-sub-A')).toHaveLength(1);
  });
});
