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

// outputCount now derives from the RESIDENT useReferencedFiles store (run_9e42c066),
// not the removed onCanvasMeta round-trip. Drive it via the real swarm:file-changed
// event — this exercises the actual production capture path. Each event = one output
// row (kind defaults to source-final = a counted output) for the given tab.
function emitOutputs(tabId: string, n: number) {
  for (let i = 0; i < n; i++) {
    window.dispatchEvent(
      new CustomEvent('swarm:file-changed', {
        detail: { path: `out-${tabId}-${i}.py`, tabId, operation: 'written', relevance: 'deliverable', kind: 'source-final' },
      }),
    );
  }
}

describe('useCanvasHost — per-tab Canvas state (bug2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear(); // the resident referenced-files store persists per-tab in sessionStorage
  });

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
    act(() => emitOutputs('A', 3)); // 3 outputs land → count → must re-emit
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
    act(() => emitOutputs('A', 5));
    const snap = result.current.getCanvasSnapshot();
    expect(snap!.outputCount).toBe(5); // Gate-1 BLOCK1: not a frozen closure value
    expect(snap!.open).toBe(true);
  });

  it('outputCount is PER-TAB — a count on tab A does not bleed into tab B', () => {
    let tabId = 'A';
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: tabId } },
    );
    // Tab A: open + 5 outputs
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => emitOutputs('A', 5));
    expect(result.current.getCanvasSnapshot()!.outputCount).toBe(5);

    // Switch to tab B: fresh Canvas → its count must NOT be A's 5 (resident store
    // is keyed by tabId, so B loads its own — empty — sessionStorage bucket)
    tabId = 'B';
    rerender({ t: tabId });
    act(() => result.current.setFile({ filePath: 'b.md', fileName: 'b.md' }));
    const snapB = result.current.getCanvasSnapshot();
    expect(snapB!.outputCount).toBe(0); // B has its own count, not A's stale 5

    // Switch back to A: its 5 is restored (re-loaded from A's sessionStorage bucket)
    tabId = 'A';
    rerender({ t: tabId });
    expect(result.current.getCanvasSnapshot()!.outputCount).toBe(5);
  });

  it('close() does NOT discard outputCount — outputs persist so the pill still shows them (run_9e42c066)', () => {
    // Behavior change from the pre-resident design: outputCount now derives from the
    // RESIDENT store (useReferencedFiles, per-tab sessionStorage), which must SURVIVE a
    // Canvas close — otherwise closing the panel would erase the knowledge that N
    // outputs exist, and the ChatHeader "N outputs" pill (shown when !isOpen) would be
    // wrong. So a run's outputs are retained across close; they clear only on a new tab
    // / new session (a fresh sessionStorage bucket), not on close.
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => emitOutputs('A', 7));
    expect(result.current.outputCount).toBe(7);
    act(() => result.current.close());
    // Closed → outputs still counted (the pill relies on this), NOT wiped to 0.
    expect(result.current.outputCount).toBe(7);
    // Reopen manually — still 7 (never lost).
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); });
    expect(result.current.getCanvasSnapshot()!.outputCount).toBe(7);
  });

  it('getCanvasSnapshot() returns null after close (no stale open) — when there are NO outputs', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    expect(result.current.getCanvasSnapshot()).not.toBeNull();
    act(() => result.current.close());
    expect(result.current.getCanvasSnapshot()).toBeNull();
  });

  it('SENSE gap fix (run_9e42c066): closed Canvas WITH pending outputs still reports a snapshot (open=false, outputCount>0)', () => {
    // The ChatHeader pill shows the user "N outputs" on a closed Canvas; the agent
    // must SENSE the same. Before the fix getCanvasSnapshot returned null when closed,
    // hiding pending outputs from the agent even as the pill advertised them.
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    // Never open Canvas; a finish batch arrives while closed.
    act(() => emitOutputs('A', 4));
    expect(result.current.isOpen).toBe(false);
    const snap = result.current.getCanvasSnapshot();
    expect(snap).not.toBeNull();          // NOT hidden from SENSE anymore
    expect(snap!.open).toBe(false);        // honestly reports closed
    expect(snap!.outputCount).toBe(4);     // …but the count reaches the agent
  });

  it('SENSE: a pristine tab (closed, zero outputs) still reports null (no noise)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'PRISTINE', sessionId: 's-P', isStreaming: false }),
    );
    expect(result.current.isOpen).toBe(false);
    expect(result.current.outputCount).toBe(0);
    expect(result.current.getCanvasSnapshot()).toBeNull(); // truly-empty-and-closed → null
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

describe('useCanvasHost — pill lastSeenOutputCount (run_9dd59523, no nagging)', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); });

  it('opening Canvas marks the current outputs as seen (lastSeen catches up to outputCount)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => emitOutputs('A', 3));       // 3 outputs while closed
    expect(result.current.outputCount).toBe(3);
    expect(result.current.lastSeenOutputCount).toBe(0); // not seen yet → pill would show
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); }); // user opens
    expect(result.current.lastSeenOutputCount).toBe(3); // now seen → pill hides
  });

  it('outputs arriving WHILE open are marked seen immediately (no stale pill on close)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); }); // open first
    act(() => emitOutputs('A', 2));       // outputs arrive while open
    // lastSeen tracked them (fires on [isOpen,outputCount]) → on close the pill won't flash.
    expect(result.current.outputCount).toBe(2);
    expect(result.current.lastSeenOutputCount).toBe(2);
    act(() => result.current.close());
    // closed, but everything was seen → outputCount == lastSeen → pill stays hidden.
    expect(result.current.lastSeenOutputCount).toBe(2);
  });

  it('a NEW output after review pushes outputCount above lastSeen (pill reappears)', () => {
    const { result } = renderHook(() =>
      useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }),
    );
    act(() => emitOutputs('A', 2));
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); }); // seen=2
    act(() => result.current.close());
    // a DISTINCT new file (emitOutputs reuses out-A-0.. which would dedup) → outputCount 2→3
    act(() => {
      window.dispatchEvent(new CustomEvent('swarm:file-changed', {
        detail: { path: 'brand-new.py', tabId: 'A', operation: 'written', relevance: 'deliverable', kind: 'source-final' },
      }));
    });
    expect(result.current.outputCount).toBe(3);
    expect(result.current.lastSeenOutputCount).toBe(2); // 3 > 2 → pill shows again
  });

  it('lastSeen is PER-TAB — opening tab A does not mark tab B outputs seen', () => {
    let tabId = 'A';
    const { result, rerender } = renderHook(
      ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
      { initialProps: { t: tabId } },
    );
    act(() => emitOutputs('A', 2));
    act(() => { window.dispatchEvent(new CustomEvent('swarm:open-canvas')); }); // A seen=2
    expect(result.current.lastSeenOutputCount).toBe(2);
    // Switch to B (its own outputs, never opened)
    tabId = 'B';
    rerender({ t: tabId });
    act(() => emitOutputs('B', 1));
    expect(result.current.outputCount).toBe(1);
    expect(result.current.lastSeenOutputCount).toBe(0); // B not seen — A's open didn't bleed
  });
});
