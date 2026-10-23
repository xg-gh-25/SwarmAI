/**
 * Context usage is a pure VIEW of the backend snapshot.
 *
 * What is tested:
 * - ``hooks/contextUsage`` helpers: snapshot → ContextWarning (``source: none``
 *   clears), measuredAt ordering, critical-transition detection, the typed
 *   stale notifier.
 * - The lifecycle hook's single apply path: a ``context_warning`` SSE snapshot
 *   and a GET result both go through it; an older snapshot never replaces a
 *   newer one; ``context_compacted`` no longer empties the ring (the backend
 *   pushes the post-compact snapshot); the critical warning is an in-tab notice
 *   on the snapshot's own tab, raised once per critical episode.
 * - ``refreshContextUsage`` and the end-to-end staleness wiring: a successful
 *   ``chatService.compactSession`` → notifier → the hook's real subscription →
 *   ``GET /chat/sessions/{id}/context-usage`` → tab state (only HTTP mocked).
 * - Ring colour comes from the backend level.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import {
  toContextWarning,
  isNewerSnapshot,
  snapshotFromEvent,
  notifyContextUsageStale,
  onContextUsageStale,
  useContextUsageRefresh,
  type ContextUsageSnapshot,
} from '../hooks/contextUsage';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { tabNoticeStore } from '../stores/tabNoticeStore';
import { getContextRingColor } from '../pages/chat/components/ContextUsageRing';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import api from '../services/api';
import {
  testTabMap,
  createMockDeps,
  initTestTab,
  makeMessage,
  resetTestState,
} from './helpers/streamingTestUtils';

const mockAddToast = vi.hoisted(() => vi.fn());
const mockRemoveToast = vi.hoisted(() => vi.fn());
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: mockAddToast, removeToast: mockRemoveToast, toasts: [] }),
}));

const snap = (over: Partial<ContextUsageSnapshot> = {}): ContextUsageSnapshot => ({
  type: 'context_warning',
  level: 'ok',
  pct: 30,
  tokensEst: 300_000,
  window: 1_000_000,
  message: 'Context 30% full',
  source: 'live',
  basis: 'last_call',
  measuredAt: 100,
  ...over,
});

describe('contextUsage helpers', () => {
  it('maps a snapshot to a ContextWarning; source none / null pct clears', () => {
    expect(toContextWarning(snap())).toMatchObject({ level: 'ok', pct: 30, tokensEst: 300_000 });
    expect(toContextWarning(snap({ source: 'none', pct: null }))).toBeNull();
    expect(toContextWarning(snap({ pct: null }))).toBeNull();
  });

  it('orders by measuredAt; unknown previous accepts; missing stamp accepts', () => {
    expect(isNewerSnapshot(undefined, snap())).toBe(true);
    expect(isNewerSnapshot(100, snap({ measuredAt: 100 }))).toBe(true);
    expect(isNewerSnapshot(200, snap({ measuredAt: 100 }))).toBe(false);
    expect(isNewerSnapshot(200, snap({ measuredAt: undefined }))).toBe(true);
  });

  it('snapshotFromEvent narrows a stream event without losing provenance', () => {
    const s2 = snapshotFromEvent({ type: 'context_warning', level: 'warn', pct: null, tokensEst: 5,
      source: 'none', basis: 'none', measuredAt: 3 } as never);
    expect(s2).toMatchObject({ level: 'warn', pct: null, source: 'none', measuredAt: 3 });
  });

  it('stale notifier delivers to subscribers and unsubscribes', () => {
    const cb = vi.fn();
    const off = onContextUsageStale(cb);
    notifyContextUsageStale('s1');
    off();
    notifyContextUsageStale('s2');
    expect(cb).toHaveBeenCalledTimes(1);
    expect(cb).toHaveBeenCalledWith('s1');
  });

  it('useContextUsageRefresh reads the backend on active tab / session change only', () => {
    const refresh = vi.fn(async () => {});
    const { rerender } = renderHook(
      ({ tab, sid }) => useContextUsageRefresh(tab, sid, false, refresh),
      { initialProps: { tab: 'tab-1' as string | null, sid: 's1' as string | undefined } },
    );
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenLastCalledWith('tab-1');
    rerender({ tab: 'tab-1', sid: 's1' });
    expect(refresh).toHaveBeenCalledTimes(1); // nothing changed — no extra request
    rerender({ tab: 'tab-2', sid: 's2' });
    expect(refresh).toHaveBeenLastCalledWith('tab-2');
    rerender({ tab: 'tab-3', sid: undefined });
    expect(refresh).toHaveBeenCalledTimes(2); // a new tab with no session has nothing to read
  });

  it('useContextUsageRefresh re-reads when the turn ends and on window focus', () => {
    vi.useFakeTimers();
    const refresh = vi.fn(async () => {});
    const { rerender } = renderHook(
      ({ streaming }) => useContextUsageRefresh('tab-1', 's1', streaming, refresh),
      { initialProps: { streaming: true } },
    );
    expect(refresh).toHaveBeenCalledTimes(1); // activation
    rerender({ streaming: true });
    expect(refresh).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 5_000);    // a real turn takes seconds
    rerender({ streaming: false });           // turn ended
    expect(refresh).toHaveBeenCalledTimes(2);
    vi.setSystemTime(Date.now() + 10_000);
    act(() => { window.dispatchEvent(new Event('focus')); });
    expect(refresh).toHaveBeenCalledTimes(3);
    // focus + visibilitychange arriving together are ONE read
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(refresh).toHaveBeenCalledTimes(3);
    vi.setSystemTime(Date.now() + 1_000);
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(refresh).toHaveBeenCalledTimes(4);
    vi.useRealTimers();
  });

  it('ring colour derives from the backend level', () => {
    expect(getContextRingColor('critical')).toBe('#ef4444');
    expect(getContextRingColor('warn')).toBe('#eab308');
    expect(getContextRingColor('ok')).toBe('#22c55e');
    expect(getContextRingColor(null)).toBe('var(--color-border)');
  });
});

describe('lifecycle hook applies backend snapshots', () => {
  beforeEach(() => {
    resetTestState();
    for (const id of ['tab-d', 'tab-bg', 'tab-fg', 'tab-ep']) tabNoticeStore.clearTab(id);
    mockAddToast.mockClear();
    mockRemoveToast.mockClear();
    vi.restoreAllMocks();
  });

  function setup(tabId: string, sessionId = 'sess-ctx') {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    const msgId = `msg-${tabId}`;
    act(() => {
      initTestTab(tabId);
      testTabMap.get(tabId)!.sessionId = sessionId;
      result.current.setLocalSending(true);
      messageStoreRegistry.getOrCreate(tabId, { sessionId })
        .replace([makeMessage({ id: msgId, role: 'assistant', content: [] })]);
    });
    const handler = result.current.createStreamHandler(msgId, tabId);
    return { result, handler };
  }

  it('each SSE snapshot updates tab + active mirror; an older one is ignored', () => {
    const { result, handler } = setup('tab-a');
    act(() => { handler(snap({ pct: 31, tokensEst: 310_000, measuredAt: 10 }) as never); });
    act(() => { handler(snap({ pct: 35, tokensEst: 350_000, measuredAt: 20 }) as never); });
    act(() => { handler(snap({ pct: 20, tokensEst: 200_000, measuredAt: 15 }) as never); });
    expect(result.current.contextWarning?.pct).toBe(35);
    expect(testTabMap.get('tab-a')!.contextWarning?.pct).toBe(35);
  });

  it('a newer source=none snapshot clears the ring', () => {
    const { result, handler } = setup('tab-b');
    act(() => { handler(snap({ measuredAt: 10 }) as never); });
    act(() => { handler(snap({ source: 'none', pct: null, measuredAt: 11 }) as never); });
    expect(result.current.contextWarning).toBeNull();
  });

  it('context_compacted no longer empties the ring; the post-compact snapshot sets it', () => {
    const { result, handler } = setup('tab-c');
    act(() => { handler(snap({ level: 'critical', pct: 95, tokensEst: 950_000, measuredAt: 10 }) as never); });
    act(() => {
      handler({ type: 'context_compacted', sessionId: 'sess-ctx', trigger: 'auto', message: 'compacted' } as never);
    });
    expect(result.current.contextWarning?.pct).toBe(95);
    act(() => { handler(snap({ pct: 4, tokensEst: 41_000, basis: 'post_compact', measuredAt: 12 }) as never); });
    expect(result.current.contextWarning?.pct).toBe(4);
  });

  // A full context raises no notice: the CLI summarizes earlier messages at 75%
  // and the turn continues; the ring shows the level.
  it('a critical snapshot raises no notice and no toast', () => {
    const { result, handler } = setup('tab-d');
    act(() => { handler(snap({ level: 'critical', pct: 88, measuredAt: 1 }) as never); });
    expect(result.current.contextWarning?.level).toBe('critical');
    expect(tabNoticeStore.getSnapshot('tab-d')).toHaveLength(0);
    expect(mockAddToast).not.toHaveBeenCalled();
  });

  it('a GET answered after a newer SSE snapshot does not roll the ring back', async () => {
    const { result, handler } = setup('tab-race', 'sess-race');
    act(() => { handler(snap({ pct: 40, tokensEst: 400_000, measuredAt: 20 }) as never); });
    vi.spyOn(api, 'get').mockResolvedValue({ data: snap({ pct: 30, source: 'transcript', measuredAt: 10 }) } as never);
    await act(async () => { await result.current.refreshContextUsage('tab-race'); });
    expect(result.current.contextWarning?.pct).toBe(40);
  });

  it('a tab that switches to an older session accepts that session\'s older value', async () => {
    const { result, handler } = setup('tab-switch', 'sess-new');
    act(() => { handler(snap({ pct: 60, measuredAt: 500 }) as never); });
    act(() => { testTabMap.get('tab-switch')!.sessionId = 'sess-old'; });
    vi.spyOn(api, 'get').mockResolvedValue({ data: snap({ pct: 12, source: 'transcript', measuredAt: 100 }) } as never);
    await act(async () => { await result.current.refreshContextUsage('tab-switch'); });
    expect(result.current.contextWarning?.pct).toBe(12);
  });

  it('a failed GET keeps the current value', async () => {
    const { result, handler } = setup('tab-fail', 'sess-fail');
    act(() => { handler(snap({ pct: 33, measuredAt: 5 }) as never); });
    vi.spyOn(api, 'get').mockRejectedValue(new Error('offline'));
    await act(async () => { await result.current.refreshContextUsage('tab-fail'); });
    expect(result.current.contextWarning?.pct).toBe(33);
  });

  it('refresh: the stale notice refetches and a source=none answer empties the ring', async () => {
    const { result, handler } = setup('tab-ref', 'sess-ref');
    act(() => { result.current.setLocalSending(false); });
    act(() => { handler(snap({ pct: 70, level: 'warn', measuredAt: 5 }) as never); });
    vi.spyOn(api, 'post').mockResolvedValue({ data: { status: 'refreshed', message: 'ok' } } as never);
    vi.spyOn(api, 'get').mockResolvedValue({ data: snap({ source: 'none', pct: null, measuredAt: 9 }) } as never);
    await act(async () => { await chatService.refreshSession('sess-ref'); });
    await waitFor(() => expect(testTabMap.get('tab-ref')!.contextWarning).toBeNull());
    expect(result.current.contextWarning).toBeNull();
  });

  it('refreshContextUsage applies the GET result to the tab', async () => {
    const { result } = setup('tab-e', 'sess-e');
    const spy = vi.spyOn(api, 'get').mockResolvedValue({ data: snap({ pct: 27, source: 'transcript', measuredAt: 5 }) } as never);
    await act(async () => { await result.current.refreshContextUsage('tab-e'); });
    expect(spy).toHaveBeenCalledWith('/chat/sessions/sess-e/context-usage');
    expect(testTabMap.get('tab-e')!.contextWarning?.pct).toBe(27);
    expect(result.current.contextWarning?.pct).toBe(27);
  });

  it('a GET result for a session the tab no longer holds is dropped', async () => {
    const { result } = setup('tab-f', 'sess-f');
    vi.spyOn(api, 'get').mockImplementation(async () => {
      testTabMap.get('tab-f')!.sessionId = 'sess-other';
      return { data: snap({ pct: 77, measuredAt: 5 }) } as never;
    });
    await act(async () => { await result.current.refreshContextUsage('tab-f'); });
    expect(testTabMap.get('tab-f')!.contextWarning).toBeNull();
  });

  it('end to end: a successful compact refetches the tab from the backend', async () => {
    const { result } = setup('tab-g', 'sess-g');
    act(() => { result.current.setLocalSending(false); });
    vi.spyOn(api, 'post').mockResolvedValue({ data: { status: 'compacted', message: 'ok' } } as never);
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: snap({ pct: 3, tokensEst: 30_000, measuredAt: 50 }) } as never);
    await act(async () => { await chatService.compactSession('sess-g'); });
    await waitFor(() => expect(testTabMap.get('tab-g')!.contextWarning?.pct).toBe(3));
    expect(getSpy).toHaveBeenCalledWith('/chat/sessions/sess-g/context-usage');
    expect(result.current.contextWarning?.pct).toBe(3);
  });
});
