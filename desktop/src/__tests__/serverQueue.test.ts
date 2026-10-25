/**
 * The server's queue is the tab's queue (docs/design/chat-turn-liveness.md S3,
 * P2c): a user bubble is "Queued" while its client_id is on the session's
 * `pending` list, unclaimed; pushed lists apply in eventSeq order, a poll only
 * when it was sent after the last pushed list; a message just sent while busy is
 * queued until a list read after its `accepted` decides. Plus the mapper, the
 * cancel call and source guards on ChatPage's busy send.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { draftToRestore } from '../pages/chat/draftToRestore';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  return { listeners, emit(e: unknown) { for (const l of listeners) l(e); } };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {}, stop: () => {}, isConnected: () => true,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: () => () => {}, watch: () => {}, cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import api from '../services/api';
import { testTabMap, createMockDeps, initTestTab, resetTestState } from './helpers/streamingTestUtils';

const SID = 's-q';
const user = (id: string) => ({ id, role: 'user', content: [{ type: 'text', text: id }],
  timestamp: new Date().toISOString(), metadata: { client_id: id } });
const pend = (cid: string, claimed = false) => ({ id: `row-${cid}`, client_id: cid, pending_seq: 1,
  preview: cid, created_at: '', claimed });
/** the same entry as the hook holds it (after the mapper) */
const entry = (cid: string, claimed = false) => ({ id: `row-${cid}`, clientId: cid, pendingSeq: 1,
  preview: cid, createdAt: '', claimed });
const statePush = (eventSeq: number, pending: unknown[]) => ({
  type: 'session_state', sessionId: SID, turnId: '', eventSeq,
  state: { streaming: true, state: 'streaming', waiting_input: false, pending_count: pending.length,
    pending, pending_question: null, last_drained_seqs: [], activity: 'streaming', activity_seq: 1 },
});

function setup() {
  vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}) as never);
  const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
  act(() => { initTestTab('Q'); });
  const tab = testTabMap.get('Q')!;
  tab.sessionId = SID;
  const store = messageStoreRegistry.getOrCreate('Q', { sessionId: SID });
  return { hook, tab, store };
}
const queuedIds = (store: ReturnType<typeof messageStoreRegistry.getOrCreate>) =>
  store.messages.filter((m) => m.isQueued).map((m) => m.id);

beforeEach(() => { resetTestState(); fake.listeners.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('the server queue is the tab queue', () => {
  it('a pushed list marks its unclaimed messages; a newer push without one clears it; an older push is ignored', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-a'), user('local-b')] as never); });
    act(() => { fake.emit(statePush(5, [pend('local-a'), pend('local-b', true)])); });
    expect(queuedIds(store)).toEqual(['local-a']);          // b is claimed: being sent
    act(() => { fake.emit(statePush(4, [pend('local-b')])); });   // older news
    expect(queuedIds(store)).toEqual(['local-a']);
    act(() => { fake.emit(statePush(6, [])); });
    expect(queuedIds(store)).toEqual([]);
  });

  it('a poll sent before the last pushed list does not override it', async () => {
    vi.useFakeTimers();
    try {
      let release: ((v: unknown) => void) | null = null;
      vi.spyOn(chatService, 'getStreamingState').mockImplementation(() => new Promise((res) => { release = res; }));
      vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
      vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
      renderHook(() => useChatStreamingLifecycle(createMockDeps()));
      act(() => { initTestTab('Q'); });
      const tab = testTabMap.get('Q')!;
      tab.sessionId = SID;
      const store = messageStoreRegistry.getOrCreate('Q', { sessionId: SID });
      act(() => { store.appendMany([user('local-a')] as never); });
      await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });   // a poll is sent ...
      act(() => { fake.emit(statePush(7, [pend('local-a')])); });            // ... a push comes meanwhile
      expect(queuedIds(store)).toEqual(['local-a']);
      // the poll answers with what it read before the message was queued
      await act(async () => { release?.({ [SID]: { streaming: true, state: 'streaming', waitingInput: false,
        pendingCount: 0, pendingQuestion: null, lastDrainedSeqs: [], pending: [] } }); await vi.advanceTimersByTimeAsync(0); });
      expect(queuedIds(store), 'the older poll cleared the pushed queue').toEqual(['local-a']);
      expect(tab._serverQueue?.map((p) => p.clientId)).toEqual(['local-a']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a message just sent while busy stays queued until a list read after its `accepted` decides', () => {
    const { hook, tab, store } = setup();
    act(() => { store.appendMany([{ ...user('local-n'), isQueued: true }] as never); });
    tab._queuedLocal = new Map([['local-n', undefined]]);          // POST open, no receipt yet
    act(() => { hook.current.applyServerQueue('Q', [], Date.now()); });
    expect(queuedIds(store), 'a list read before the receipt cleared it').toEqual(['local-n']);
    const acceptedAt = Date.now();
    tab._queuedLocal.set('local-n', acceptedAt);                   // receipt: queued
    act(() => { hook.current.applyServerQueue('Q', [entry('local-n')], acceptedAt + 1); });
    expect(queuedIds(store)).toEqual(['local-n']);
    expect(tab._queuedLocal.has('local-n'), 'the server governs it now').toBe(false);
    act(() => { hook.current.applyServerQueue('Q', [], acceptedAt + 2); });   // drained
    expect(queuedIds(store)).toEqual([]);
  });

  it('a busy send awaiting its receipt keeps its request when another send is marked "Not sent"', () => {
    const { hook, tab, store } = setup();
    act(() => { store.appendMany([user('local-busy'), user('local-main')] as never); });
    tab._queuedLocal = new Map([['local-busy', undefined]]);
    tab._unsentRequests = new Map([['local-busy', { agentId: 'a', message: 'b', clientId: 'local-busy' }]]);
    act(() => {
      hook.current.setLocalSending(true, 'Q');
      hook.current.noteSend('Q', { agentId: 'a', message: 'm', clientId: 'local-main' });
      hook.current.setLocalSending(false, 'Q');           // local-main: not sent; prunes kept requests
    });
    expect(tab._unsentRequests.has('local-busy')).toBe(true);
  });

  it('a capped list keeps the marks of messages it does not carry', () => {
    const { hook, tab, store } = setup();
    act(() => { store.appendMany([user('local-51'), user('local-1'), user('local-n')] as never); });
    act(() => { hook.current.applyServerQueue('Q', [entry('local-51'), entry('local-1')], 5, true); });
    tab._queuedLocal = new Map([['local-n', 6]]);              // accepted at 6
    act(() => { hook.current.applyServerQueue('Q', [entry('local-1')], 10, false); });
    expect(queuedIds(store)).toEqual(['local-51', 'local-1', 'local-n']);
    expect(tab._queuedLocal.has('local-n'), 'a capped list does not decide it').toBe(true);
    act(() => { hook.current.applyServerQueue('Q', [], 11, true); });
    expect(queuedIds(store)).toEqual([]);
  });

  it('a poll from a backend that does not send `pending` leaves the marks alone', async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({ [SID]: { streaming: true,
        state: 'streaming', waitingInput: false, pendingCount: 0, pendingQuestion: null, lastDrainedSeqs: [] } }) as never);
      vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
      vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
      renderHook(() => useChatStreamingLifecycle(createMockDeps()));
      act(() => { initTestTab('Q'); });
      const tab = testTabMap.get('Q')!;
      tab.sessionId = SID;
      const store = messageStoreRegistry.getOrCreate('Q', { sessionId: SID });
      act(() => { store.appendMany([{ ...user('local-a'), isQueued: true }] as never); });
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
      expect(chatService.getStreamingState).toHaveBeenCalled();
      expect(queuedIds(store)).toEqual(['local-a']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a state without `pending` (older backend) leaves the marks alone', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-a')] as never); });
    act(() => { fake.emit(statePush(3, [pend('local-a')])); });
    const old = statePush(4, []);
    delete (old.state as { pending?: unknown }).pending;
    act(() => { fake.emit(old); });
    expect(queuedIds(store)).toEqual(['local-a']);
  });

  it('a new session starts the queue over (eventSeq is per session)', () => {
    const { tab, store } = setup();
    act(() => { store.appendMany([user('local-a'), user('local-b')] as never); });
    act(() => { fake.emit(statePush(90, [pend('local-a')])); });
    tab.sessionId = 'S2';
    act(() => { fake.emit({ ...statePush(2, [pend('local-b')]), sessionId: 'S2' }); });
    expect(queuedIds(store)).toEqual(['local-b']);
    expect(tab._serverQueueSeq).toBe(2);
  });

  it('a queued row the store does not show (after a reload) gets its bubble, once', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-a')] as never); });
    act(() => { fake.emit(statePush(5, [pend('local-a'), pend('local-r'), pend('local-c', true)])); });
    expect(store.messages.map((m) => m.id)).toEqual(['local-a', 'local-r']);   // claimed c is being sent
    expect(queuedIds(store)).toEqual(['local-a', 'local-r']);
    expect(store.messages[1].content).toEqual([{ type: 'text', text: 'local-r' }]);
    act(() => { fake.emit(statePush(6, [pend('local-r')])); });
    expect(store.messages.map((m) => m.id)).toEqual(['local-a', 'local-r']);   // not twice
    expect(queuedIds(store)).toEqual(['local-r']);
  });

  it('maps `pending` from the wire', () => {
    const e = chatService.toStreamingStateEntry({ streaming: false, state: 'idle',
      pending: [pend('local-z', true)] } as never);
    expect(e.pending).toEqual([{ id: 'row-local-z', clientId: 'local-z', pendingSeq: 1, preview: 'local-z',
      createdAt: '', claimed: true }]);
  });

  it('cancelPending: 200 cancelled, 409 claimed, 404 missing', async () => {
    const del = vi.spyOn(api, 'delete');
    del.mockResolvedValueOnce({ data: {} } as never);
    expect(await chatService.cancelPending('S 1', 'local-x')).toBe('cancelled');
    expect(del).toHaveBeenLastCalledWith('/chat/sessions/S%201/pending/local-x');
    del.mockRejectedValueOnce({ response: { status: 409 } });
    expect(await chatService.cancelPending('S', 'local-x')).toBe('claimed');
    del.mockRejectedValueOnce({ response: { status: 404 } });
    expect(await chatService.cancelPending('S', 'local-x')).toBe('missing');
  });
});

describe('ChatPage queue (send / stop behaviour: pages/chat/__tests__/turnControls.test.ts)', () => {
  const src = readFileSync(join(__dirname, '..', 'pages', 'ChatPage.tsx'), 'utf8');
  const fnBody = (name: string) => {
    const i = src.indexOf(`const ${name} = useCallback(`);
    return src.slice(i, src.indexOf('\n  }, [', i));
  };

  it('no frontend queue', () => {
    expect(src).not.toMatch(/queuedMessage|drainQueuedMessage|onDrainQueue/);
  });

  it('draftToRestore never overwrites what was typed or attached since', () => {
    const a = { id: 'a1', name: 'f.txt' } as never;
    const b = { id: 'b1', name: 'g.txt' } as never;
    expect(draftToRestore({ text: '', attachments: [] }, { text: 'hi', attachments: [a] })).toEqual({ text: 'hi', attachments: [a] });
    expect(draftToRestore({ text: 'new', attachments: [b] }, { text: 'hi', attachments: [a] })).toEqual({});
    expect(draftToRestore({ text: '  ', attachments: [b] }, { text: 'hi', attachments: [a] })).toEqual({ text: 'hi' });
    expect(draftToRestore({ text: '', attachments: [] }, { text: '', attachments: [] })).toEqual({});
  });

  it('pending is complete only with an explicit count that covers it', () => {
    const base = { streaming: false, state: 'idle' };
    expect(chatService.toStreamingStateEntry({ ...base, pending: [pend('x')] } as never).pendingComplete).toBe(false);
    expect(chatService.toStreamingStateEntry({ ...base, pending_count: 1, pending: [pend('x')] } as never).pendingComplete).toBe(true);
    expect(chatService.toStreamingStateEntry({ ...base, pending_count: 51, pending: [pend('x')] } as never).pendingComplete).toBe(false);
  });

  it('Cancel asks the server and gives the text back to its own tab, active or not', () => {
    const cancel = fnBody('handleCancelQueued');
    expect(cancel).toMatch(/chatService\.cancelPending\(/);
    expect(cancel).toMatch(/restoreDraft\(tabId, text, \[\]\)/);
    expect(cancel).not.toMatch(/activeTabIdRef\.current|setInputValue\(/);
  });
});

// ── steer (frontend design 2026-10-25 §4) ─────────────────────────────────────
describe('steer: Waiting comes from the receipt and the server, read from the row', () => {
  const steerPush = (eventSeq: number, pending: unknown[], steering: { client_id: string; preview: string }[],
                     extra: Record<string, unknown> = {}) => {
    const p = statePush(eventSeq, pending);
    Object.assign(p.state, steering.length ? { steering } : {}, extra);
    return p;
  };
  const waiting = (store: ReturnType<typeof messageStoreRegistry.getOrCreate>) =>
    store.messages.filter((m) => m.steerWaiting).map((m) => m.id);

  it('T10a: a steer receipt turns the bubble Waiting in place, not queued', () => {
    const { hook, tab, store } = setup();
    act(() => { store.appendMany([user('local-p'), { ...user('local-s'), isQueued: true }] as never); });
    tab._queuedLocal = new Map();
    tab._steerLocal = new Map([['local-s', Date.now()]]);
    act(() => { hook.current.applyServerQueue('Q', [], Date.now() - 1000); });   // read before the receipt
    expect(store.messages.map((m) => m.id)).toEqual(['local-p', 'local-s']);
    expect(waiting(store)).toEqual(['local-s']);
    expect(queuedIds(store)).toEqual([]);
  });

  it('T10d: Waiting, then the server moves it to the queue (never read): Queued', () => {
    const { tab, store } = setup();
    act(() => { store.appendMany([user('local-s')] as never); });
    tab._steerLocal = new Map([['local-s', Date.now() - 10]]);
    act(() => { fake.emit(steerPush(2, [], [{ client_id: 'local-s', preview: 'x' }])); });
    expect(waiting(store)).toEqual(['local-s']);
    expect(tab._steerLocal.has('local-s'), 'the server governs it now').toBe(false);
    act(() => { fake.emit(steerPush(3, [pend('local-s')], [])); });
    expect(waiting(store)).toEqual([]);
    expect(queuedIds(store)).toEqual(['local-s']);
  });

  it('T10m: Waiting stays when a snapshot drops it before the read arrives', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-s')] as never); });
    act(() => { fake.emit(steerPush(2, [], [{ client_id: 'local-s', preview: 'x' }])); });
    act(() => { fake.emit(steerPush(3, [], [])); });
    expect(waiting(store)).toEqual(['local-s']);
  });

  it('a read steer (metadata.steered) is never Waiting', () => {
    const { store } = setup();
    act(() => { store.appendMany([{ ...user('local-s'), metadata: { client_id: 'local-s', steered: 'during' } }] as never); });
    act(() => { fake.emit(steerPush(2, [], [{ client_id: 'local-s', preview: 'x' }])); });
    expect(waiting(store)).toEqual([]);
  });

  it('T10i: a steer the store does not show gets a provisional Waiting bubble, once', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-p')] as never); });
    act(() => { fake.emit(steerPush(2, [], [{ client_id: 'local-o', preview: 'from another tab' }])); });
    act(() => { fake.emit(steerPush(3, [], [{ client_id: 'local-o', preview: 'from another tab' }])); });
    expect(store.messages.map((m) => m.id)).toEqual(['local-p', 'local-o']);
    expect(store.messages[1]).toMatchObject({ role: 'user', steerWaiting: true,
      metadata: { client_id: 'local-o', provisional: true },
      content: [{ type: 'text', text: 'from another tab' }] });
  });

  it('the turn ending clears Waiting', async () => {
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const { tab, store } = setup();
    act(() => { store.appendMany([user('local-s')] as never); });
    act(() => { fake.emit(steerPush(2, [], [{ client_id: 'local-s', preview: 'x' }])); });
    expect(waiting(store)).toEqual(['local-s']);
    const idle = steerPush(3, [], []);
    Object.assign(idle.state, { streaming: false, state: 'idle', activity: 'idle', activity_seq: 9 });
    await act(async () => { fake.emit(idle); await Promise.resolve(); });
    expect(waiting(store)).toEqual([]);
    expect(tab._serverSteering ?? []).toEqual([]);
  });

  it('a list read before the session went idle makes nothing Waiting again; idle ends the segment target', async () => {
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const { hook, tab, store } = setup();
    act(() => { store.appendMany([user('local-s')] as never); });
    tab._turnHandlers = { ...(tab._turnHandlers ?? {}), segmentTarget: { turnId: 'T1', byHost: { h: 'local-s-asst' } } } as never;
    act(() => { fake.emit(steerPush(2, [], [])); });                    // the turn runs
    const before = Date.now() - 50;                                    // a poll sent before idle
    const idle = steerPush(3, [], []);
    Object.assign(idle.state, { streaming: false, state: 'idle', activity: 'idle', activity_seq: 9 });
    await act(async () => { fake.emit(idle); await Promise.resolve(); });
    expect(tab._turnHandlers?.segmentTarget).toBeUndefined();
    act(() => { hook.current.applyServerQueue('Q', [], before, true,
      { steering: [{ clientId: 'local-s', preview: 'x' }] } as never); });
    expect(waiting(store)).toEqual([]);
  });

  it('T10e: the queue reason follows the snapshot; none keeps the old label', () => {
    const { store } = setup();
    act(() => { store.appendMany([user('local-q')] as never); });
    act(() => { fake.emit(steerPush(2, [pend('local-q')], [], { queued_reason: 'question' })); });
    expect(store.messages[0]).toMatchObject({ isQueued: true, queuedReason: 'question' });
    act(() => { fake.emit(steerPush(3, [pend('local-q')], [], { queued_reason: 'busy' })); });
    expect(store.messages[0].queuedReason).toBe('busy');
    act(() => { fake.emit(steerPush(4, [pend('local-q')], [])); });              // switch off
    expect(store.messages[0].queuedReason).toBeUndefined();
  });

  it('maps steering, segments and queued_reason from the wire; absent stays absent', () => {
    const e = chatService.toStreamingStateEntry({ streaming: true, state: 'streaming', pending: [],
      steering: [{ client_id: 'c1', preview: 'p' }], segments: ['a-asst', 'c1-asst'], queued_reason: 'permission' } as never);
    expect(e.steering).toEqual([{ clientId: 'c1', preview: 'p' }]);
    expect(e.segments).toEqual(['a-asst', 'c1-asst']);
    expect(e.queuedReason).toBe('permission');
    const off = chatService.toStreamingStateEntry({ streaming: true, state: 'streaming', pending: [] } as never);
    expect('steering' in off || 'segments' in off || 'queuedReason' in off).toBe(false);
  });
});
