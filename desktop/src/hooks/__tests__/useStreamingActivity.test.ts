/**
 * Tests for useStreamingActivity — elapsed-timer re-anchoring (run_81a580ba).
 *
 * Bug: the elapsed timer anchored once at stream start and only cleared on
 * stop, so it measured TOTAL stream duration. The label (toolName) changes
 * per tool_use block. Rendered together as "{tool} · {elapsed}", a long tool
 * or a stretch with no new tool_use block froze the label while the timer
 * climbed the whole-session time → false "hung for 28m".
 *
 * Fix (approach A): re-anchor the timer to the CURRENT activity — reset the
 * start time when the debounced displayedActivity.toolName changes, so elapsed
 * reflects how long the CURRENT tool has run.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useStreamingActivity } from '../useStreamingActivity';
import type { Message, ContentBlock } from '../../types';

function toolUse(id: string, name: string, summary = ''): ContentBlock {
  return { type: 'tool_use', id, name, summary } as ContentBlock;
}
function textBlock(text: string): ContentBlock {
  return { type: 'text', text } as ContentBlock;
}
function assistantMsg(content: ContentBlock[]): Message {
  return { id: 'a1', role: 'assistant', content } as Message;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useStreamingActivity — elapsed re-anchoring', () => {
  it('AC1: resets elapsed when the tool changes mid-stream', () => {
    const msgsA = [assistantMsg([toolUse('t1', 'Read')])];
    const { result, rerender } = renderHook(
      ({ streaming, messages }) => useStreamingActivity(streaming, messages),
      { initialProps: { streaming: true, messages: msgsA } },
    );

    // Tool A runs for 5s.
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);
    expect(result.current.displayedActivity?.toolName).toBe('Read');

    // A new tool block (Bash) arrives → label switches, timer must RE-ANCHOR.
    const msgsB = [assistantMsg([toolUse('t1', 'Read'), toolUse('t2', 'Bash')])];
    act(() => { rerender({ streaming: true, messages: msgsB }); });
    // Let any debounce + re-anchor settle (no wall-clock time passes yet).
    act(() => { vi.advanceTimersByTime(300); });

    expect(result.current.displayedActivity?.toolName).toBe('Bash');
    // Elapsed for the NEW tool must be small (re-anchored), not ~5s+.
    expect(result.current.elapsedSeconds).toBeLessThan(2);

    // And it climbs for the new tool.
    act(() => { vi.advanceTimersByTime(3000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(3);
  });

  it('AC5: does NOT reset when toolCount increments but toolName is unchanged', () => {
    const msgs1 = [assistantMsg([toolUse('t1', 'Bash')])];
    const { result, rerender } = renderHook(
      ({ streaming, messages }) => useStreamingActivity(streaming, messages),
      { initialProps: { streaming: true, messages: msgs1 } },
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);

    // Second Bash tool_use (same name) → toolCount 1→2, toolName still "Bash".
    const msgs2 = [assistantMsg([toolUse('t1', 'Bash'), toolUse('t2', 'Bash')])];
    act(() => { rerender({ streaming: true, messages: msgs2 }); });
    act(() => { vi.advanceTimersByTime(300); });

    expect(result.current.displayedActivity?.toolName).toBe('Bash');
    expect(result.current.displayedActivity?.toolCount).toBe(2);
    // Same tool name → NO reset; elapsed keeps the original anchor (~5s+).
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(5);
  });

  it('AC2: thinking (no tool) — elapsed climbs from stream start', () => {
    // Assistant has only text → deriveStreamingActivity returns hasContent w/ null toolName.
    const msgs = [assistantMsg([textBlock('thinking out loud')])];
    const { result } = renderHook(() => useStreamingActivity(true, msgs));
    act(() => { vi.advanceTimersByTime(7000); });
    expect(result.current.displayedActivity?.toolName ?? null).toBeNull();
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(6);
  });

  it('AC3: F4 gate — no timer runs when not streaming', () => {
    const msgs = [assistantMsg([toolUse('t1', 'Read')])];
    const { result } = renderHook(() => useStreamingActivity(false, msgs));
    act(() => { vi.advanceTimersByTime(10000); });
    expect(result.current.elapsedSeconds).toBe(0);
  });

  it('AC3b: stops/zeroes when streaming flips to false', () => {
    const msgs = [assistantMsg([toolUse('t1', 'Read')])];
    const { result, rerender } = renderHook(
      ({ streaming }) => useStreamingActivity(streaming, msgs),
      { initialProps: { streaming: true } },
    );
    act(() => { vi.advanceTimersByTime(4000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(3);
    act(() => { rerender({ streaming: false }); });
    expect(result.current.elapsedSeconds).toBe(0);
  });
});
