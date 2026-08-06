/**
 * useFileViewerTabs — dirty-guard close contract (run_f49d3ff3 Gate-2 HIGH).
 *
 * closeTab NO-OPS on a dirty tab ("caller must confirm first"). The unified-close
 * discard path (FileEditorCore.handleDiscardChanges) relies on the caller first
 * clearing the flag via markDirty(id,false) — this test pins that contract so a
 * future change can't silently re-break the "discard actually removes the tab" fix:
 * a dirty tab does NOT close; after markDirty(false) it DOES.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFileViewerTabs } from '../useFileViewerTabs';

describe('useFileViewerTabs — dirty-guard close contract', () => {
  it('closeTab NO-OPS on a dirty tab, and REMOVES it once marked clean', () => {
    const { result } = renderHook(() => useFileViewerTabs());

    // Open two tabs (so a close is observable without the whole list emptying).
    act(() => { result.current.openTab('/ws/a.ts', 'a.ts'); });
    act(() => { result.current.openTab('/ws/b.ts', 'b.ts'); });
    expect(result.current.tabs).toHaveLength(2);

    const bId = result.current.tabs.find((t) => t.filePath === '/ws/b.ts')!.id;
    // Mark b dirty.
    act(() => { result.current.markDirty(bId, true); });
    expect(result.current.tabs.find((t) => t.id === bId)!.isDirty).toBe(true);

    // closeTab on a DIRTY tab → no-op (the guard the discard path must satisfy).
    act(() => { result.current.closeTab(bId); });
    expect(result.current.tabs).toHaveLength(2); // still there

    // Confirmed discard clears the flag (what handleDiscardChanges now does via
    // onContentChange→markDirty(false)) → closeTab removes it.
    act(() => { result.current.markDirty(bId, false); });
    act(() => { result.current.closeTab(bId); });
    expect(result.current.tabs).toHaveLength(1);
    expect(result.current.tabs[0].filePath).toBe('/ws/a.ts');
  });
});
