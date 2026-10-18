/**
 * Tests for useStreamingRest — the per-tab "resting" cue behind the streaming bee.
 *
 * What is tested: while streaming, the hook flips to resting after STREAMING_REST_MS with
 * no change of its progress key; a key change wakes it immediately and re-arms
 * the timer; re-rendering with the SAME key (what a heartbeat does — touch()
 * never changes the store snapshot) does NOT re-arm it; not streaming → false
 * with no pending timer; unmount clears the timer.
 *
 * Methodology: renderHook + vi fake timers; keys are plain objects/strings
 * standing in for the store snapshot array identity, plus one chain test that
 * keys the hook on a REAL MessageStore's getSnapshot(): heartbeats (touch())
 * must leave the snapshot identity alone so the bee still rests, and a content
 * write must wake it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useStreamingRest, STREAMING_REST_MS } from '../useStreamingRest';
import { MessageStore } from '../../stores/MessageStore';
import type { Message } from '../../types';

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

type P = { streaming: boolean; k: unknown };

describe('useStreamingRest', () => {
  it('rests exactly after STREAMING_REST_MS of no progress (default 30s)', () => {
    expect(STREAMING_REST_MS).toBe(30_000);
    const { result } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: true, k: 'k1' },
    });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(29_999); });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current).toBe(true);
  });

  it('a progress-key change wakes it immediately and re-arms the timer', () => {
    const { result, rerender } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: true, k: 'k1' },
    });
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(result.current).toBe(true);
    rerender({ streaming: true, k: 'k2' });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(29_000); });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(1_000); });
    expect(result.current).toBe(true);
  });

  it('re-rendering with the SAME key (heartbeat-only) does not re-arm the timer', () => {
    const snapshot = [{ id: 'a1' }];
    const { result, rerender } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: true, k: snapshot },
    });
    act(() => { vi.advanceTimersByTime(20_000); });
    rerender({ streaming: true, k: snapshot });
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(result.current).toBe(true);
  });

  it('not streaming → false and no pending timer', () => {
    const { result } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: false, k: 'k1' },
    });
    expect(result.current).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(result.current).toBe(false);
  });

  it('stream end clears the resting state and the timer', () => {
    const { result, rerender } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: true, k: 'k1' },
    });
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(result.current).toBe(true);
    rerender({ streaming: false, k: 'k1' });
    expect(result.current).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('unmount clears the pending timer', () => {
    const { unmount } = renderHook(() => useStreamingRest(true, 'k1'));
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a stream that restarts on the same key begins awake', () => {
    const { result, rerender } = renderHook(({ streaming, k }: P) => useStreamingRest(streaming, k), {
      initialProps: { streaming: true, k: 'k1' },
    });
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(result.current).toBe(true);
    rerender({ streaming: false, k: 'k1' });
    rerender({ streaming: true, k: 'k1' });
    expect(result.current).toBe(false);
  });

  it('chain: real MessageStore — heartbeats (touch) do not wake it, content does', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 10 * 60_000 });
    const msg = { id: 'a1', role: 'assistant', content: [{ type: 'text', text: 'hi' }] } as Message;
    store.startStreaming('a1');
    store.append(msg);
    const { result, rerender } = renderHook(({ k }: { k: unknown }) => useStreamingRest(true, k), {
      initialProps: { k: store.getSnapshot() as unknown },
    });
    // Two heartbeats 15s apart, re-rendering with whatever the store now serves.
    act(() => { vi.advanceTimersByTime(15_000); });
    store.touch();
    rerender({ k: store.getSnapshot() });
    act(() => { vi.advanceTimersByTime(15_000); });
    store.touch();
    rerender({ k: store.getSnapshot() });
    expect(result.current).toBe(true);
    // A content write produces a new snapshot → awake on the same render.
    store.updateLast((m) => ({ ...m, content: [{ type: 'text', text: 'hi there' }] } as Message));
    rerender({ k: store.getSnapshot() });
    expect(result.current).toBe(false);
    store.destroy();
  });
});
