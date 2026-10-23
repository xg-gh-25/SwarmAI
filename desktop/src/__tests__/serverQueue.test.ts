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

describe('ChatPage busy send (source guard: ChatPage has no harness driving its sends)', () => {
  const src = readFileSync(join(__dirname, '..', 'pages', 'ChatPage.tsx'), 'utf8');
  const fnBody = (name: string) => {
    const i = src.indexOf(`const ${name} = useCallback(`);
    return src.slice(i, src.indexOf('\n  }, [', i));
  };

  it('a send while busy goes to the server, not to a frontend queue', () => {
    expect(src).not.toMatch(/queuedMessage|drainQueuedMessage|onDrainQueue/);
    const send = fnBody('handleSendMessage');
    expect(send).toMatch(/shouldQueueSend\(activeTabForGuard\)\) \{\s*void sendWhileBusy\(/);
  });

  it('the busy send never takes over the tab\'s stream handlers', () => {
    const busy = fnBody('sendWhileBusy');
    expect(busy).toMatch(/chatService\.streamChat\(/);
    expect(busy).not.toMatch(/incrementStreamGen|createStreamHandler|setLocalSending/);
  });

  it('a busy send that does not happen gives the draft back to its own tab', () => {
    const busy = fnBody('sendWhileBusy');
    const clear = busy.indexOf("setInputValue('')");
    const build = busy.indexOf('await buildContentArray(');
    expect(clear, 'the draft is taken while this tab is active').toBeGreaterThan(0);
    expect(clear).toBeLessThan(build);
    const after = busy.slice(build);
    expect(after).toMatch(/if \(failed \|\| !stillMine \|\| tabMapRef\.current\.get\(tabId\) !== tab \|\| tab\.sessionId !== sid \|\| content\.length === 0\) \{\s*restoreDraft\(tabId, text, attachments\);/);
    expect(after).toMatch(/const stillMine = tab\._preparingSend === prep;/);
    expect(after).not.toMatch(/setInputValue\(|clearAttachments\(/);   // never the active composer after the await
    expect(after.indexOf('rebindQueue(tabId)'), 'the queue is rebound before a local entry').toBeLessThan(after.indexOf('_queuedLocal.set('));
  });

  it('the normal send takes the draft before the await and gives it back when it does not send', () => {
    const send = fnBody('handleSendMessage');
    const build = send.indexOf('content = await buildContentArray(messageText');
    const before = send.slice(send.indexOf('const sendTabId = activeTabIdRef.current;'), build);
    expect(before).toMatch(/setInputValue\(''\);\s*clearAttachments\(\);/);
    const after = send.slice(build);
    expect(after).toMatch(/if \(failed \|\| !stillThisSend\(\) \|\| content\.length === 0\) \{[\s\S]{0,160}restoreDraft\(sendTabId, messageText, currentAttachments\);/);
    expect(after).not.toMatch(/setInputValue\(''\)|clearAttachments\(\)/);
  });

  it('a second Send while a send reads its attachments does nothing; only the owner clears the mark', () => {
    const send = fnBody('handleSendMessage');
    const guard = send.indexOf('if (activeTabForGuard?._preparingSend) return;');
    expect(guard).toBeGreaterThan(0);
    expect(guard, 'before the busy path').toBeLessThan(send.indexOf('shouldQueueSend(activeTabForGuard)'));
    expect(send).toMatch(/finally \{\s*if \(sendTab && sendTab\._preparingSend === prep\) sendTab\._preparingSend = undefined;/);
    expect(fnBody('sendWhileBusy')).toMatch(/const stillMine = tab\._preparingSend === prep;\s*if \(stillMine\) tab\._preparingSend = undefined;/);
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

  it('Stop ends a send still preparing, on both paths (busy and normal)', () => {
    const i = src.indexOf('const handleStop = () => {');
    const stop = src.slice(i, src.indexOf('\n  };', i));
    expect(stop.match(/\._preparingSend = undefined;/g)?.length).toBe(2);
    expect(stop.match(/\._sendToken = undefined;/g)?.length).toBe(2);
  });

  it('the normal send is also bound to the session it started in', () => {
    const send = fnBody('handleSendMessage');
    expect(send).toMatch(/const sendSid = sendTab\?\.sessionId;/);
    expect(send).toMatch(/t0\._sendToken === sendToken && t0\.sessionId === sendSid/);
  });
});
