/**
 * P0 acceptance tests for docs/design/chat-turn-liveness.md (v2) — frontend half.
 *
 * Each test states one invariant the spec requires. One still broken is `it.fails`
 * (vitest: passes while the body fails; turns red the moment the fix lands): T7
 * until P2b. T2 and T6 hold since P2a (the bee follows the backend's activity).
 *
 * Methodology: the real useChatStreamingLifecycle (the same harness as
 * liveReload.property.test.ts: subscription faked, chatService spied), fake timers
 * advanced 1 s at a time so the real reconcile loop runs at its real cadence.
 *
 * - T2  a send whose backend spends 68 s cold-starting (as 10-23 16:07) keeps the
 *       bee on the whole time: the backend's `activity` says the turn is preparing /
 *       starting, even though `state` is 'cold' and `streaming` false.
 * - T6  the newest activity wins: a slow poll carrying an OLDER activitySeq never
 *       overrides a newer pushed one.
 * - T7  a send the backend never accepted (backend unreachable) is not left looking
 *       sent: the tab is not streaming and the message is marked for retry.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  return {
    listeners,
    emit(e: unknown) { for (const l of listeners) l(e); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => true,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: () => () => {},
    watch: () => {},
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import { testTabMap, createMockDeps, initTestTab, resetTestState } from './helpers/streamingTestUtils';

const SID = 's-p0';

/** One poll answer: today's fields plus the spec's `activity` / `activitySeq`. */
function entry(state: string, activity: string, activitySeq: number) {
  return {
    [SID]: { streaming: state === 'streaming', state, waitingInput: false, pendingCount: 0,
      pendingQuestion: null, pendingPermission: null, lastDrainedSeqs: [], activity, activitySeq },
  };
}

/** A tab that has just sent: its POST handler is open, as ChatPage leaves it. */
function sentTab(hook: { current: ReturnType<typeof useChatStreamingLifecycle> }) {
  act(() => { initTestTab('T'); });
  const tab = testTabMap.get('T')!;
  tab.sessionId = SID;
  messageStoreRegistry.getOrCreate('T', { sessionId: SID });
  act(() => {
    hook.current.createStreamHandler('local-1-asst', 'T');
    hook.current.setLocalSending(true, 'T');
  });
  return tab;
}

beforeEach(() => { vi.useFakeTimers(); resetTestState(); fake.listeners.clear(); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe('chat-turn-liveness P0 (frontend)', () => {
  it('T2: the bee stays on through a 68 s cold start', async () => {
    let now: ReturnType<typeof entry> = entry('cold', 'preparing', 1);
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => now as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    const tab = sentTab(hook);
    // admission: the backend pushes `preparing` the moment the turn is claimed
    act(() => { fake.emit({ type: 'session_activity', sessionId: SID, turnId: '', activity: 'preparing', activity_seq: 1 }); });
    const off: number[] = [];
    for (let s = 1; s <= 90; s++) {
      if (s === 6) now = entry('cold', 'starting', 2);       // spawn begins
      if (s === 68) now = entry('streaming', 'waiting', 3);  // query sent
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      if (!tab.isStreaming) off.push(s);
    }
    expect(off, `bee off at second(s) ${off.slice(0, 3).join(', ')}`).toEqual([]);
  });

  it('T6: an older polled activity never overrides a newer pushed one', async () => {
    // the poll answers with seq 1 (read before the turn ended); the push already said seq 2 = idle
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => entry('streaming', 'streaming', 1) as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    const tab = sentTab(hook);
    act(() => {
      // as the backend emits it (snake_case; chatService.toStreamingStateEntry maps it)
      fake.emit({ type: 'session_state', sessionId: SID, eventSeq: 9, turnId: '',
        state: { streaming: false, state: 'idle', waiting_input: false, pending_count: 0,
          pending_question: null, last_drained_seqs: [], background_agents: [],
          activity: 'idle', activity_seq: 2 } });
    });
    for (let s = 1; s <= 40; s++) await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(tab.isStreaming, 'a stale poll (seq 1) re-armed the bee after idle (seq 2)').toBe(false);
    expect((tab as unknown as { activitySeq?: number }).activitySeq).toBe(2);
  });

  it('T2b: a new tab follows its session from `accepted`, before the cold start', async () => {
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}) as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('N'); });
    const tab = testTabMap.get('N')!;
    messageStoreRegistry.getOrCreate('N', {});
    const post = hook.current.createStreamHandler('local-n-asst', 'N');
    act(() => {
      hook.current.setLocalSending(true, 'N');
      post({ type: 'accepted', sessionId: 'S-new', clientId: 'local-n', duplicate: false,
        queued: false, userMessageId: 'u1', status: 'persisted' } as never);
    });
    expect(tab.sessionId).toBe('S-new');
    act(() => { fake.emit({ type: 'session_activity', sessionId: 'S-new', turnId: '', activity: 'starting', activity_seq: 3 }); });
    expect(tab.isStreaming).toBe(true);
  });

  it('T6b: a lost push is recovered by the poll (lit, then put out)', async () => {
    let now: ReturnType<typeof entry> = entry('streaming', 'streaming', 5);
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => now as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('T'); });
    const tab = testTabMap.get('T')!;
    tab.sessionId = SID;
    messageStoreRegistry.getOrCreate('T', { sessionId: SID });
    for (let s = 1; s <= 6; s++) await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(tab.isStreaming, 'no push arrived; the poll did not light the bee').toBe(true);
    now = entry('idle', 'idle', 6);
    for (let s = 1; s <= 5; s++) await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(tab.isStreaming).toBe(false);
    expect(hook.current).toBeTruthy();
  });

  it('T6c: a session the poll no longer lists is idle, unless news came after the poll was sent', async () => {
    let release: (() => void) | null = null;
    let lists = true;
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(() => new Promise((res) => {
      const answer = lists ? entry('streaming', 'streaming', 1) : {};
      release = () => res(answer as never);
    }));
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('T'); });
    const tab = testTabMap.get('T')!;
    tab.sessionId = SID;
    messageStoreRegistry.getOrCreate('T', { sessionId: SID });
    const answerPoll = async () => { await act(async () => { release?.(); await vi.advanceTimersByTimeAsync(0); }); };
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });   // first poll sent
    await answerPoll();
    expect(tab.isStreaming).toBe(true);
    // the unit is released: the next poll does not list the session ...
    lists = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });   // poll sent
    // ... but a new turn was pushed while that poll was in flight: it stays lit
    act(() => { fake.emit({ type: 'session_activity', sessionId: SID, turnId: '', activity: 'preparing', activity_seq: 2 }); });
    await answerPoll();
    expect(tab.isStreaming, 'a poll older than the push put the bee out').toBe(true);
    // a poll sent after that news, still not listing it: idle
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    await answerPoll();
    expect(tab.isStreaming).toBe(false);
  });

  it('R1: an answer / decision is allowed while the session waits for it', async () => {
    const { canContinueTurn } = await import('../hooks/streaming-guards');
    expect(canContinueTurn({ isStreaming: true, activity: 'waiting_input', localSending: false })).toBe(true);
    expect(canContinueTurn({ isStreaming: true, activity: 'streaming', localSending: false })).toBe(false);
    expect(canContinueTurn({ isStreaming: true, activity: 'waiting_input', localSending: true })).toBe(false);
    expect(canContinueTurn({ isStreaming: false, activity: 'idle', localSending: false })).toBe(true);
  });

  it('R2: the idle edge ends the store phase of a turn whose end was lost', async () => {
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}) as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('W'); });
    const tab = testTabMap.get('W')!;
    tab.sessionId = SID;
    const store = messageStoreRegistry.getOrCreate('W', { sessionId: SID });
    act(() => {
      hook.current.applyActivity('W', 'streaming', 1);
      store.startStreaming('local-w-asst');
    });
    expect(store.phase).toBe('streaming');
    act(() => { hook.current.applyActivity('W', 'idle', 2); });   // turn_end never came
    expect(tab.isStreaming).toBe(false);
    expect(store.phase).toBe('idle');
  });

  it('R3: a tab that shows another session starts its activity over', async () => {
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}) as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('R'); });
    const tab = testTabMap.get('R')!;
    tab.sessionId = 'S-A';
    act(() => { hook.current.applyActivity('R', 'streaming', 500); });
    expect(tab.isStreaming).toBe(true);
    // a new chat in the same tab: the old session's bee goes out on the switch
    tab.sessionId = undefined;
    act(() => { hook.current.bumpStreamingDerivation(); });
    expect(tab.isStreaming).toBe(false);
    // the tab resumes a running session whose seqs are lower than A's
    tab.sessionId = 'S-B';
    act(() => { hook.current.applyActivity('R', 'streaming', 300); });
    expect(tab.isStreaming, "B's activity was compared with A's seq").toBe(true);
  });

  it('R2b: fresh idle news on a tab already idle still ends a turn whose busy activity was lost', async () => {
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => ({}) as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    act(() => { initTestTab('W2'); });
    const tab = testTabMap.get('W2')!;
    tab.sessionId = SID;
    const store = messageStoreRegistry.getOrCreate('W2', { sessionId: SID });
    act(() => { store.startStreaming('local-w2-asst'); });   // the POST rendered; no activity arrived
    act(() => { hook.current.applyActivity('W2', 'idle', 7); });
    expect(store.phase).toBe('idle');
    // the same idle again changes nothing
    act(() => { hook.current.applyActivity('W2', 'idle', 7); });
    expect(tab.isStreaming).toBe(false);
  });

  it('R4: a send stopped while its attachments are read opens no POST (source guard)', async () => {
    const { readFileSync } = await import('node:fs');
    const { join } = await import('node:path');
    const src = readFileSync(join(__dirname, '..', 'pages', 'ChatPage.tsx'), 'utf8');
    const send = src.slice(src.indexOf('const handleSendMessage = useCallback'), src.indexOf('handleSendMessageRef.current = handleSendMessage'));
    const awaitAt = send.indexOf('await buildContentArray(');
    const checkAt = send.indexOf('if (!stillThisSend()) return;');
    const postAt = send.indexOf('chatService.streamChat(');
    expect(awaitAt, 'the send reads its attachments').toBeGreaterThan(0);
    expect(checkAt, 'the send re-checks its token after the await').toBeGreaterThan(awaitAt);
    expect(postAt, 'and before it writes or POSTs').toBeGreaterThan(checkAt);
    expect(send.slice(awaitAt, postAt)).not.toMatch(/activeTabIdRef\.current/);
    const stop = src.slice(src.indexOf('const handleStop = '), src.indexOf('const handleStop = ') + 1500);
    expect((stop.match(/_sendToken = undefined/g) ?? []).length, 'Stop cancels the pending send').toBe(2);
  });

  it.fails('T7: a send the backend never accepted is marked for retry, not left streaming', async () => {
    vi.spyOn(chatService, 'getStreamingState').mockRejectedValue(new Error('Unable to connect to backend service'));
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    const tab = sentTab(hook);
    const store = messageStoreRegistry.getOrCreate('T', { sessionId: SID });
    act(() => {
      store.appendMany([{ id: 'local-1', role: 'user', content: [{ type: 'text', text: 'hi' }],
        timestamp: new Date().toISOString(), metadata: { client_id: 'local-1' } } as never]);
      hook.current.createErrorHandler('local-1-asst', 'T')(new TypeError('network error'));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    const user = store.messages.find((m) => m.id === 'local-1') as unknown as { sendState?: string } | undefined;
    expect(tab.isStreaming).toBe(false);
    expect(user?.sendState, 'the unaccepted message is not marked "not sent · retry"').toBe('failed');
  });
});
