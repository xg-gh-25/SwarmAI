/**
 * Tests for useCanvasHost — per-tab Canvas state (bug2) + host wiring.
 *
 * Business rules under test:
 *  - Canvas state (file / pinned / muted / expanded / manuallyOpen) is kept
 *    PER TAB in a ref-backed Map. Switching tabs restores that tab's slice;
 *    switching back restores the original (no "reset-not-restore", no bleed).
 *  - The active slice is exposed as React state so the panel re-renders.
 *  - Opening Canvas manually (swarm:open-canvas) sets manuallyOpen on the
 *    ACTIVE tab only.
 *
 * The file-resolve + auto-surface paths are exercised in integration; here we
 * pin the per-tab STATE machine, which is the OT01-sensitive core.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'resolved.md' } }) },
}));

describe('useCanvasHost — per-tab Canvas state (bug2)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('keeps Canvas state independent per tab and restores on switch-back', () => {
    let tabId = 'A';
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: tabId } },
    );

    // Tab A: open a file + pin it
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.setPinned(true));
    expect(result.current.file?.filePath).toBe('a.md');
    expect(result.current.pinned).toBe(true);

    // Switch to tab B → B has its own (empty) Canvas
    tabId = 'B';
    rerender({ t: tabId });
    expect(result.current.file).toBeNull();
    expect(result.current.pinned).toBe(false);

    // B opens its own file, muted
    act(() => result.current.setFile({ filePath: 'b.md', fileName: 'b.md' }));
    act(() => result.current.setMuted(true));
    expect(result.current.file?.filePath).toBe('b.md');

    // Switch back to A → A's file + pin restored, NOT B's
    tabId = 'A';
    rerender({ t: tabId });
    expect(result.current.file?.filePath).toBe('a.md');
    expect(result.current.pinned).toBe(true);
    expect(result.current.muted).toBe(false); // A was never muted; B's mute must not bleed
  });

  it('manual open (swarm:open-canvas) sets manuallyOpen on the active tab only', () => {
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'A' } },
    );
    act(() => {
      window.dispatchEvent(new CustomEvent('swarm:open-canvas'));
    });
    expect(result.current.isOpen).toBe(true);

    // Tab B is not manually opened
    rerender({ t: 'B' });
    expect(result.current.isOpen).toBe(false);
  });

  it('canvas-state emit re-fires when outputCount changes (Gate-2 MED: no stale count)', () => {
    const events: Array<{ outputCount?: number } | null> = [];
    const onState = (e: Event) => events.push((e as CustomEvent).detail);
    window.addEventListener('swarm:canvas-state', onState);
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' })); // isOpen → emit
    act(() => result.current.onCanvasMeta({ collapsed: false, outputCount: 3 })); // count → must re-emit
    window.removeEventListener('swarm:canvas-state', onState);
    const counts = events.filter(Boolean).map((d) => d!.outputCount);
    expect(counts).toContain(3); // the updated count reached a fresh emit (not frozen)
  });

  it('getCanvasSnapshot() reflects open SYNCHRONOUSLY after swarm:open-canvas (race fix)', () => {
    // The proprioception race (run_e45a04d3): the async swarm:canvas-state emit
    // could lag behind a fast send, so the SENSE snapshot read canvas.open=stale.
    // getCanvasSnapshot() must read the LIVE ref at call time — no waiting for a
    // React commit + effect. Assert it WITHOUT act()-flushing effects: dispatch,
    // then read synchronously in the same turn.
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    // Before any open: nothing to report.
    expect(result.current.getCanvasSnapshot()).toBeNull();

    act(() => {
      window.dispatchEvent(new CustomEvent('swarm:open-canvas'));
    });
    // Immediately (same call, no extra render wait) the snapshot must show open.
    const snap = result.current.getCanvasSnapshot();
    expect(snap).not.toBeNull();
    expect(snap!.open).toBe(true);
  });

  it('getCanvasSnapshot() carries the latest outputCount (no stale-closure count)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.onCanvasMeta({ collapsed: false, outputCount: 5 }));
    const snap = result.current.getCanvasSnapshot();
    expect(snap!.outputCount).toBe(5); // Gate-1 BLOCK1: not a frozen closure value
    expect(snap!.open).toBe(true);
  });

  it('getCanvasSnapshot() returns null after close (no stale open)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    expect(result.current.getCanvasSnapshot()).not.toBeNull();
    act(() => result.current.close());
    expect(result.current.getCanvasSnapshot()).toBeNull();
  });

  it('close clears the active tab Canvas (file + manuallyOpen) without touching other tabs', () => {
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: 'A' } },
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    rerender({ t: 'B' });
    act(() => result.current.setFile({ filePath: 'b.md', fileName: 'b.md' }));
    act(() => result.current.close());
    expect(result.current.file).toBeNull();
    expect(result.current.isOpen).toBe(false);
    // A untouched
    rerender({ t: 'A' });
    expect(result.current.file?.filePath).toBe('a.md');
  });
});
