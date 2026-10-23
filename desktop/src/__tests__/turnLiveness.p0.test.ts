/**
 * P0 acceptance tests for docs/design/chat-turn-liveness.md (v2) — frontend half.
 *
 * Each test states one invariant the spec requires and today's code breaks, so it
 * is `it.fails` (vitest: passes while the body fails; turns red the moment the fix
 * lands, and the marker must be removed in the phase that fixes it: P2).
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
    hook.current.setIsStreaming(true, 'T');
  });
  return tab;
}

beforeEach(() => { vi.useFakeTimers(); resetTestState(); fake.listeners.clear(); });
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe('chat-turn-liveness P0 (frontend)', () => {
  it.fails('T2: the bee stays on through a 68 s cold start', async () => {
    let now: ReturnType<typeof entry> = entry('cold', 'preparing', 1);
    vi.spyOn(chatService, 'getStreamingState').mockImplementation(async () => now as never);
    vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
    vi.spyOn(chatService, 'getSessionMessagesReconcileTail').mockImplementation(async () => [] as never);
    const hook = renderHook(() => useChatStreamingLifecycle(createMockDeps())).result;
    const tab = sentTab(hook);
    const off: number[] = [];
    for (let s = 1; s <= 90; s++) {
      if (s === 6) now = entry('cold', 'starting', 2);       // spawn begins
      if (s === 68) now = entry('streaming', 'waiting', 3);  // query sent
      await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
      if (!tab.isStreaming) off.push(s);
    }
    expect(off, `bee off at second(s) ${off.slice(0, 3).join(', ')}`).toEqual([]);
  });

  it.fails('T6: an older polled activity never overrides a newer pushed one', async () => {
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
