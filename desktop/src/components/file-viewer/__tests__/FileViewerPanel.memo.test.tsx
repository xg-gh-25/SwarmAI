/**
 * FileViewerPanel memoization guard (run_24f98f06, root cause A).
 *
 * THE BUG: inputValue is ChatPage useState; the chat textarea is controlled
 * (value={inputValue}), so EVERY keystroke re-renders ChatPage. FileViewerPanel
 * (the Canvas subtree: FileViewer + FileEditorCore ~1780L + CanvasOutputRail) was
 * an un-memoized `export default function`, so every keystroke re-rendered the
 * whole heavy subtree → input froze whenever Canvas was open (and was free when
 * Canvas was closed, because {canvas.isOpen && ...} unmounts it).
 *
 * THE FIX: wrap the default export in React.memo. All props passed at the ChatPage
 * call site are referentially stable across a keystroke re-render (useCanvasHost
 * callbacks are useCallback dep-[patch]/[patch,activeTabId]; file/pinned/muted from
 * slice state; sessionId/tabScopeKey primitive), so the default shallow compare
 * blocks the re-render.
 *
 * This test asserts the memo is present + effective: a parent re-render with
 * IDENTICAL props must NOT re-run FileViewerPanel's body. Mutation check: reverting
 * the memo wrapper makes the render count go 1→2 (RED).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { useState } from 'react';
import FileViewerPanel from '../FileViewerPanel';

// Leaf-stub the heavy children AND count the FileViewer body renders — a
// FileViewerPanel re-render necessarily re-renders its FileViewer child, so the
// child's render count is a faithful proxy for "did the panel re-render".
let fileViewerRenders = 0;
vi.mock('../FileViewer', () => ({
  default: () => {
    fileViewerRenders++;
    return <div data-testid="file-viewer-stub" />;
  },
}));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

// Stable prop references, defined ONCE at module scope so the parent passes the
// exact same references on every render (mirrors useCanvasHost's dep-stable
// useCallbacks + slice-state values — the real call site's stability contract).
const STABLE = {
  onClose: () => {},
  pinned: false,
  onTogglePin: () => {},
  muted: false,
  onToggleMute: () => {},
  referencedFiles: { written: [] },
  tabScopeKey: 'tab-1',
  // Bug 2: stable collapse view-object + setter (mirrors useCanvasHost's memoized
  // `collapse` + useCallback `setCollapse` — the real call site's stability contract).
  collapse: { railed: false, outputsCollapsed: false },
  setCollapse: () => {},
};

beforeEach(() => {
  fileViewerRenders = 0;
});

describe('FileViewerPanel — memoization (root cause A: keystroke re-render storm)', () => {
  it('does NOT re-render when the parent re-renders with identical props', () => {
    let bumpParent!: () => void;
    function Parent() {
      // `tick` stands in for ChatPage's inputValue: it changes on every
      // "keystroke" but is NOT passed to FileViewerPanel — so a memoized panel
      // must skip re-rendering.
      const [, setTick] = useState(0);
      bumpParent = () => setTick((n) => n + 1);
      return <FileViewerPanel {...STABLE} />;
    }
    render(<Parent />);
    expect(fileViewerRenders).toBe(1); // initial mount

    // Simulate 5 keystrokes (parent re-renders, panel props unchanged).
    act(() => {
      bumpParent();
      bumpParent();
      bumpParent();
      bumpParent();
      bumpParent();
    });

    // Memoized panel: still 1. Un-memoized (the bug): would be 6.
    expect(fileViewerRenders).toBe(1);
  });

  it('DOES re-render when a real prop changes (memo is not over-blocking)', () => {
    let setPinned!: (v: boolean) => void;
    function Parent() {
      const [pinned, _setPinned] = useState(false);
      setPinned = _setPinned;
      return <FileViewerPanel {...STABLE} pinned={pinned} />;
    }
    render(<Parent />);
    expect(fileViewerRenders).toBe(1);

    act(() => setPinned(true)); // a genuine state change the panel must reflect
    expect(fileViewerRenders).toBe(2);
  });
});
