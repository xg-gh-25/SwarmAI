/**
 * Tests for usePendingToolElapsed (run_02e658d0).
 *
 * Verifies the three traps that make a streaming-render timer dangerous are
 * handled (mirrors useStreamingActivity's discipline):
 *   1. re-anchor on toolUseId (not mount-time) — no stale time on instance reuse
 *   2. interval gated on isPending — null + no ticking when not pending
 *   3. interval cleaned up on unmount AND on isPending flip
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePendingToolElapsed } from '../usePendingToolElapsed';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('usePendingToolElapsed', () => {
  it('AC1: ticks live every second while pending', () => {
    const { result } = renderHook(
      ({ id, p }) => usePendingToolElapsed(id, p),
      { initialProps: { id: 't1', p: true } },
    );
    expect(result.current).toBe(0);
    act(() => { vi.advanceTimersByTime(3000); });
    expect(result.current).toBe(3);
    act(() => { vi.advanceTimersByTime(60000); });
    expect(result.current).toBe(63);
  });

  it('AC2: returns null when not pending (completed card shows no timer)', () => {
    const { result } = renderHook(
      ({ id, p }) => usePendingToolElapsed(id, p),
      { initialProps: { id: 't1', p: false } },
    );
    expect(result.current).toBeNull();
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current).toBeNull();
  });

  it('AC2: stops + resets to null when pending flips false (tool completes)', () => {
    const { result, rerender } = renderHook(
      ({ id, p }) => usePendingToolElapsed(id, p),
      { initialProps: { id: 't1', p: true } },
    );
    act(() => { vi.advanceTimersByTime(10000); });
    expect(result.current).toBe(10);
    rerender({ id: 't1', p: false });
    expect(result.current).toBeNull();
    // No further ticking after completion
    act(() => { vi.advanceTimersByTime(10000); });
    expect(result.current).toBeNull();
  });

  it('AC2 (the key trap): re-anchors when the component instance is re-pointed at a different tool', () => {
    // Same hook instance, toolUseId changes (keyed-list reuse). The new tool must
    // start from 0, NOT inherit the prior tool's accumulated time.
    const { result, rerender } = renderHook(
      ({ id, p }) => usePendingToolElapsed(id, p),
      { initialProps: { id: 't1', p: true } },
    );
    act(() => { vi.advanceTimersByTime(120000); }); // t1 ran 2 minutes
    expect(result.current).toBe(120);
    rerender({ id: 't2', p: true }); // instance reused for a new tool
    expect(result.current).toBe(0); // MUST reset, not show 120
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current).toBe(5);
  });

  it('AC3: clears the interval on unmount (no leaked timer)', () => {
    const clearSpy = vi.spyOn(global, 'clearInterval');
    const { unmount } = renderHook(() => usePendingToolElapsed('t1', true));
    act(() => { vi.advanceTimersByTime(1000); });
    unmount();
    expect(clearSpy).toHaveBeenCalled();
    clearSpy.mockRestore();
  });

  it('AC3: a non-pending card runs NO interval (keep-mounted-tab idle-timer hazard)', () => {
    const setSpy = vi.spyOn(global, 'setInterval');
    renderHook(() => usePendingToolElapsed('t1', false));
    act(() => { vi.advanceTimersByTime(5000); });
    // The effect early-returns before setInterval when !isPending
    expect(setSpy).not.toHaveBeenCalled();
    setSpy.mockRestore();
  });

  it('re-pending the SAME instance later starts clean from 0', () => {
    const { result, rerender } = renderHook(
      ({ id, p }) => usePendingToolElapsed(id, p),
      { initialProps: { id: 't1', p: true } },
    );
    act(() => { vi.advanceTimersByTime(30000); });
    expect(result.current).toBe(30);
    rerender({ id: 't1', p: false });
    rerender({ id: 't1', p: true });
    expect(result.current).toBe(0); // fresh anchor, not 30
  });
});

import { formatToolElapsed } from '../../pages/chat/components/MergedToolBlock';

describe('formatToolElapsed', () => {
  it('formats sub-minute as Ns', () => {
    expect(formatToolElapsed(0)).toBe('0s');
    expect(formatToolElapsed(5)).toBe('5s');
    expect(formatToolElapsed(59)).toBe('59s');
  });
  it('formats >=60s as Nm SSs with zero-padded seconds', () => {
    expect(formatToolElapsed(60)).toBe('1m 00s');
    expect(formatToolElapsed(63)).toBe('1m 03s');
    expect(formatToolElapsed(125)).toBe('2m 05s');
  });
});
