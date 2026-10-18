/**
 * Send-time Canvas snapshot: a function captured EARLIER still reads the live state.
 *
 * Business rule: the Canvas snapshot attached to a chat send ("Current UI State")
 * is the live state of the tab the message is sent for. ChatPage's send callbacks
 * are memoized and are not rebuilt on every render, so they call whatever copy of
 * getCanvasSnapshot they captured. A copy that closes over an old active tab id or
 * output count reports ANOTHER tab's Canvas (observed: a tab whose rail held 23
 * rows was reported as "2 outputs", the other tab's count).
 *
 * What is tested:
 * - a getCanvasSnapshot captured before a tab switch reports the NEW active tab
 *   (AC1) and is the same function identity after the switch;
 * - a captured copy reports a count that grew after it was captured (AC2);
 * - getCanvasSnapshotFor(tabId) snapshots THAT tab — its own slice and its stored rail
 *   count — never the active tab's (AC3); an empty id yields null, never a
 *   silent fallback to the active tab;
 * - countStoredOutputs counts a background tab's stored rows with the same
 *   predicate and grouping the pill uses.
 *
 * Methodology: renderHook(useCanvasHost) with only the HTTP layer mocked; outputs
 * arrive through the real swarm:file-changed capture path (sessionStorage-backed).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCanvasHost } from '../useCanvasHost';
import { countStoredOutputs } from '../useReferencedFiles';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { resolved_path: 'resolved.md' } }) },
}));

function emitOutputs(tabId: string, n: number, prefix = 'out') {
  for (let i = 0; i < n; i++) {
    window.dispatchEvent(
      new CustomEvent('swarm:file-changed', {
        detail: { path: `${prefix}-${tabId}-${i}.py`, tabId, operation: 'written', relevance: 'deliverable', kind: 'source-final' },
      }),
    );
  }
}

function mount(initial: string) {
  return renderHook(
    ({ t }) => useCanvasHost({ activeTabId: t, sessionId: 's-' + t, isStreaming: false }),
    { initialProps: { t: initial } },
  );
}

describe('useCanvasHost — send-time snapshot is never a stale closure', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('AC1: a snapshot function captured on tab A reports tab B after the switch', () => {
    const { result, rerender } = mount('A');
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => emitOutputs('A', 2));
    const captured = result.current.getCanvasSnapshot; // what a memoized send callback holds
    const capturedFor = result.current.getCanvasSnapshotFor;

    rerender({ t: 'B' });
    act(() => emitOutputs('B', 23));

    const snap = captured();
    expect(snap).not.toBeNull();
    expect(snap!.outputCount).toBe(23); // tab B's rail, not tab A's 2
    expect(snap!.open).toBe(false);     // tab B's Canvas is closed; A's was open
    expect(capturedFor('B')).toEqual(snap);
    expect(result.current.getCanvasSnapshot).toBe(captured); // stable identity
    expect(result.current.getCanvasSnapshotFor).toBe(capturedFor);
  });

  it('AC1: switching to a tab whose rows were stored while it was in the background', () => {
    const { result, rerender } = mount('A');
    act(() => emitOutputs('B', 4)); // B is a background tab: rows go to its storage
    const captured = result.current.getCanvasSnapshot;
    rerender({ t: 'B' });
    expect(captured()!.outputCount).toBe(4);
  });

  it('AC2: a captured snapshot function sees a count that grew after capture', () => {
    const { result } = mount('A');
    act(() => emitOutputs('A', 1));
    const captured = result.current.getCanvasSnapshot;
    act(() => emitOutputs('A', 4, 'later'));
    expect(captured()!.outputCount).toBe(5);
  });

  it('AC3: an explicit tab id snapshots that tab, not the active one', () => {
    const { result, rerender } = mount('A');
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => result.current.setPinned(true));
    act(() => emitOutputs('A', 3));
    rerender({ t: 'B' });
    act(() => emitOutputs('B', 7));

    const forA = result.current.getCanvasSnapshotFor('A');
    expect(forA).toEqual({ open: true, outputCount: 3, pinned: true, muted: false, collapsed: false });
    expect(result.current.getCanvasSnapshotFor('B')!.outputCount).toBe(7);
    expect(result.current.getCanvasSnapshot()!.outputCount).toBe(7); // the active tab
  });

  it('AC3: a background tab that receives outputs while inactive reports them', () => {
    const { result } = mount('A');
    act(() => emitOutputs('BG', 4)); // stamped for a tab that is not active
    expect(result.current.getCanvasSnapshotFor('BG')).toEqual(
      { open: false, outputCount: 4, pinned: false, muted: false, collapsed: false },
    );
  });

  it('AC3: an empty tab id yields no snapshot instead of falling back to the active tab', () => {
    const { result } = mount('A');
    act(() => result.current.setFile({ filePath: 'a.md', fileName: 'a.md' }));
    act(() => emitOutputs('A', 2));
    expect(result.current.getCanvasSnapshotFor('')).toBeNull();
    expect(result.current.getCanvasSnapshotFor(null)).toBeNull();
    expect(result.current.getCanvasSnapshotFor('never-seen')).toBeNull();
  });

  it('countStoredOutputs counts a tab\'s stored rail rows like the pill does', () => {
    renderHook(() => useCanvasHost({ activeTabId: 'A', sessionId: 's-A', isStreaming: false }));
    act(() => emitOutputs('BG2', 3));
    act(() => {
      window.dispatchEvent(new CustomEvent('swarm:file-changed', {
        detail: { path: 'noise.py', tabId: 'BG2', operation: 'written', relevance: 'deliverable', kind: 'source' },
      }));
    });
    expect(countStoredOutputs('BG2')).toBe(3); // mid-run `source` rows are not outputs
    expect(countStoredOutputs('nothing-here')).toBe(0);
  });
});
