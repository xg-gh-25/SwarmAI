/**
 * The one shared runs poll (./runsPoll).
 *
 * Questions: do two readers mounted together make one request, and one more per
 * interval; does a reader arriving later ask again only when the answer is old; does a
 * failed poll keep the last answer and say so; does reload's answer win over an older
 * one still in flight; is the answer dropped when the last reader leaves; does
 * sessionScope keep exactly the session's runs and their asks.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { swarmOutService, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { FRESH_MS, POLL_MS, _resetSharedPolls, reloadRuns, sessionScope, useSharedRuns } from './runsPoll';

vi.mock('../../services/swarmOut', () => ({ swarmOutService: { runs: vi.fn(), fleet: vi.fn() } }));
const runs = vi.mocked(swarmOutService.runs);
const answer = (title: string): SwarmOutRuns => ({ runs: [{ dispatch_id: title, title } as SwarmOutRun], asks: [], errors: [] });
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] });
  vi.setSystemTime(Date.parse('2026-11-24T06:00:00Z'));
  runs.mockReset();
  _resetSharedPolls();
});
afterEach(() => { vi.useRealTimers(); });

describe('the shared runs poll', () => {
  it('two readers mounted together make one request, then one per interval', async () => {
    runs.mockResolvedValue(answer('a'));
    const one = renderHook(() => useSharedRuns());
    const two = renderHook(() => useSharedRuns());
    await flush();
    expect(runs).toHaveBeenCalledTimes(1);
    expect(one.result.current.data?.runs[0].title).toBe('a');
    expect(two.result.current.data?.runs[0].title).toBe('a');
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await flush();
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('a reader arriving later asks again only when the answer is old', async () => {
    runs.mockResolvedValue(answer('a'));
    renderHook(() => useSharedRuns());
    await flush();
    renderHook(() => useSharedRuns());
    await flush();
    expect(runs).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + FRESH_MS + 1);
    renderHook(() => useSharedRuns());
    await flush();
    expect(runs).toHaveBeenCalledTimes(2);
  });

  it('a request that never answers does not stop the poll', async () => {
    runs.mockImplementationOnce(() => new Promise(() => {})).mockResolvedValue(answer('later'));
    const h = renderHook(() => useSharedRuns());
    await flush();
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await flush();
    expect(runs).toHaveBeenCalledTimes(2);
    expect(h.result.current.data?.runs[0].title).toBe('later');
  });

  it('keeps the last answer when a poll fails', async () => {
    runs.mockResolvedValueOnce(answer('a')).mockRejectedValueOnce(new Error('down'));
    const h = renderHook(() => useSharedRuns());
    await flush();
    await act(async () => { vi.advanceTimersByTime(POLL_MS); });
    await flush();
    expect(h.result.current.data?.runs[0].title).toBe('a');
    expect(h.result.current.failed).toBe(true);
  });

  it("reload's answer wins over an older one still in flight", async () => {
    let first: (d: SwarmOutRuns) => void = () => {};
    runs.mockImplementationOnce(() => new Promise((res) => { first = res; }))
      .mockResolvedValueOnce(answer('new'));
    const h = renderHook(() => useSharedRuns());
    reloadRuns();
    await flush();
    await act(async () => { first(answer('old')); });
    await flush();
    expect(h.result.current.data?.runs[0].title).toBe('new');
  });

  it('drops the answer and stops when the last reader leaves', async () => {
    runs.mockResolvedValue(answer('a'));
    const h = renderHook(() => useSharedRuns());
    await flush();
    h.unmount();
    await act(async () => { vi.advanceTimersByTime(POLL_MS * 3); });
    expect(runs).toHaveBeenCalledTimes(1);
    const again = renderHook(() => useSharedRuns(false));
    expect(again.result.current.data).toBeNull();
  });
});

describe('sessionScope', () => {
  const r = (id: string, from: string | null, sid: string | null = `hs-${id}`) =>
    ({ dispatch_id: id, hive_id: 'h1', session_id: sid, dispatched_from: from } as SwarmOutRun);
  const a = (sid: string) => ({ hive_id: 'h1', session_id: sid, request_id: sid } as SwarmOutRuns['asks'][number]);
  const d: SwarmOutRuns = {
    runs: [r('m1', 'S1'), r('m2', 'S1', null), r('o1', 'S2'), r('p1', null)],
    asks: [a('hs-m1'), a('hs-o1'), a('elsewhere')], answered: [a('hs-m1'), a('hs-p1')], errors: ['x'],
  };
  it("keeps the session's runs and the asks of their Hive sessions", () => {
    const s = sessionScope(d, 'S1')!;
    expect(s.runs.map((x) => x.dispatch_id)).toEqual(['m1', 'm2']);
    expect(s.asks.map((x) => x.session_id)).toEqual(['hs-m1']);
    expect(s.answered!.map((x) => x.session_id)).toEqual(['hs-m1']);
    expect(s.errors).toEqual([]);
  });
  it('an empty session id has none; the person\'s own dispatches are no tab\'s', () => {
    expect(sessionScope(d, null)!.runs).toEqual([]);
    expect(sessionScope(d, '')!.runs).toEqual([]);
    expect(sessionScope(null, 'S1')).toBeNull();
  });
});
