/**
 * corrections service: a refresh is never dropped (a throttled one runs once the
 * throttle is over), and a row answered while a fetch was running stays answered.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const get = vi.fn();
vi.mock('../api', () => ({ default: { get: (...a: unknown[]) => get(...a), post: vi.fn() } }));

import { usePendingCorrection, markAnswered, _resetCorrectionsCache } from '../corrections';

const row = { id: 'cor-1', at: '', surface: 'chat', client_id: 'c-1', text: '不对', candidates: [] };

beforeEach(() => { vi.useFakeTimers(); _resetCorrectionsCache(); get.mockReset(); });
afterEach(() => { vi.useRealTimers(); });

describe('corrections cache', () => {
  it('a throttled refresh runs once the throttle is over', async () => {
    get.mockResolvedValue({ data: { corrections: [] } });
    const sent = new Date(Date.now() - 60_000).toISOString();
    renderHook(() => usePendingCorrection('S1', 'c-1', sent));
    await act(async () => { await Promise.resolve(); });
    expect(get).toHaveBeenCalledTimes(1);
    renderHook(() => usePendingCorrection('S1', 'c-2', sent));      // inside the 2 s throttle
    await act(async () => { await Promise.resolve(); });
    expect(get).toHaveBeenCalledTimes(1);
    get.mockResolvedValue({ data: { corrections: [row] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('a row answered while a fetch is out does not come back with it', async () => {
    let resolve: (v: unknown) => void = () => {};
    get.mockReturnValue(new Promise((r) => { resolve = r; }));
    const sent = new Date(Date.now() - 60_000).toISOString();
    const { result } = renderHook(() => usePendingCorrection('S1', 'c-1', sent));
    markAnswered('cor-1');
    await act(async () => { resolve({ data: { corrections: [row] } }); await Promise.resolve(); });
    expect(result.current).toBeNull();
  });
});
