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

  it('AC5: does NOT reset while the SAME tool_use block keeps running (stable id)', () => {
    // The current tool (last tool_use id t1) keeps streaming — re-renders with
    // the same last-tool id (e.g. its result/summary fills in) must NOT reset
    // the timer; elapsed reflects how long THIS invocation has run.
    const msgs1 = [assistantMsg([toolUse('t1', 'Bash')])];
    const { result, rerender } = renderHook(
      ({ streaming, messages }) => useStreamingActivity(streaming, messages),
      { initialProps: { streaming: true, messages: msgs1 } },
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);

    // Same tool_use id t1, just more context on the block — still the SAME
    // running invocation, so no re-anchor.
    const msgs2 = [assistantMsg([toolUse('t1', 'Bash', 'running tests')])];
    act(() => { rerender({ streaming: true, messages: msgs2 }); });
    act(() => { vi.advanceTimersByTime(300); });

    expect(result.current.displayedActivity?.toolName).toBe('Bash');
    // Same invocation (id t1) → NO reset; elapsed keeps the original anchor.
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

  it('AC6: re-anchors when the SAME tool name recurs after a Thinking gap (adversarial MED)', () => {
    // Read(5s) → think(null, 3s) → a NEW Read tool_use. A name-keyed anchor
    // would suppress the reset (name unchanged across the null gap) and show
    // elapsed≈8s — the exact stale symptom. Id-keyed anchor re-anchors → ~0.
    const read1 = [assistantMsg([toolUse('t1', 'Read')])];
    const { result, rerender } = renderHook(
      ({ s, m }) => useStreamingActivity(s, m),
      { initialProps: { s: true, m: read1 } },
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);

    // Thinking gap (assistant emits text, no tool) for 3s — anchor kept.
    const thinking = [assistantMsg([toolUse('t1', 'Read'), textBlock('pondering')])];
    act(() => { rerender({ s: true, m: thinking }); });
    act(() => { vi.advanceTimersByTime(3000); });

    // A NEW Read invocation (distinct id t2, same name).
    const read2 = [assistantMsg([toolUse('t1', 'Read'), textBlock('pondering'), toolUse('t2', 'Read')])];
    act(() => { rerender({ s: true, m: read2 }); });
    act(() => { vi.advanceTimersByTime(300); });

    expect(result.current.displayedActivity?.toolName).toBe('Read');
    // Must re-anchor for the NEW Read invocation, not show the cumulative ~8s.
    expect(result.current.elapsedSeconds).toBeLessThan(2);
  });

  it('AC6b: re-anchors on a second same-name tool in sequence (Read→Read)', () => {
    const read1 = [assistantMsg([toolUse('t1', 'Read')])];
    const { result, rerender } = renderHook(
      ({ s, m }) => useStreamingActivity(s, m),
      { initialProps: { s: true, m: read1 } },
    );
    act(() => { vi.advanceTimersByTime(5000); });
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);

    // Second distinct Read (t2) becomes the LAST tool_use → new invocation.
    const read2 = [assistantMsg([toolUse('t1', 'Read'), toolUse('t2', 'Read')])];
    act(() => { rerender({ s: true, m: read2 }); });
    act(() => { vi.advanceTimersByTime(300); });

    // toolCount is 2, name same — but it's a DISTINCT invocation (new id) so
    // the CURRENT-activity timer must re-anchor (distinct from the AC5 case,
    // which asserts no-reset when the displayed last-tool id is unchanged).
    expect(result.current.elapsedSeconds).toBeLessThan(2);
  });

  it('AC4: two concurrent keep-mounted tabs track independent elapsed/labels', () => {
    // Each TabView calls useStreamingActivity with ITS OWN (isStreaming, messages).
    // Keep-mounted tabs are hidden via CSS, not unmounted — switching is a
    // visibility toggle, so each hook instance retains its own anchor. This
    // models that isolation: tab A (Read, streaming) vs tab B (Bash, streaming).
    const tabA = renderHook(
      ({ s, m }) => useStreamingActivity(s, m),
      { initialProps: { s: true, m: [assistantMsg([toolUse('a1', 'Read')])] } },
    );
    // Tab A streams for 6s before tab B even starts.
    act(() => { vi.advanceTimersByTime(6000); });

    const tabB = renderHook(
      ({ s, m }) => useStreamingActivity(s, m),
      { initialProps: { s: true, m: [assistantMsg([toolUse('b1', 'Bash')])] } },
    );
    act(() => { vi.advanceTimersByTime(2000); });

    // A and B carry independent labels and independent elapsed anchors —
    // switching focus between them does not bleed one tab's timer into the other.
    expect(tabA.result.current.displayedActivity?.toolName).toBe('Read');
    expect(tabB.result.current.displayedActivity?.toolName).toBe('Bash');
    expect(tabA.result.current.elapsedSeconds).toBeGreaterThanOrEqual(7); // 6+2
    expect(tabB.result.current.elapsedSeconds).toBeGreaterThanOrEqual(1);
    expect(tabB.result.current.elapsedSeconds).toBeLessThan(tabA.result.current.elapsedSeconds);
  });
});
