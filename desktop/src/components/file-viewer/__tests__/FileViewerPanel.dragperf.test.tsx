/**
 * Canvas resize-drag PERFORMANCE guard (run_4b67c510).
 *
 * Two properties that make the drag "丝滑" (smooth), both of which jsdom CAN test
 * because they are about render-COUNT and call-COUNT, not geometry:
 *
 *  1. AC1 — memo(FileViewer): during a resize drag, FileViewerPanel calls setWidth
 *     (≤1/frame). Because FileViewer is now `export default memo(FileViewerImpl)`,
 *     those width-only re-renders must NOT reconcile the heavy FileViewer subtree.
 *     We render the REAL FileViewer (leaf children mocked) and count the leaf's
 *     renders — a faithful proxy for "did the FileViewer body re-run". Mutation:
 *     unwrap the source memo → the count grows with each drag frame (RED).
 *
 *  2. AC2/AC3 — rAF-throttle + persist-on-drag-end: many mousemoves within ONE
 *     animation frame coalesce to ≤1 setWidth (via a DEFERRED rAF mock, NOT the
 *     sync mock the other suites use), and localStorage is written exactly ONCE at
 *     mouseup (not per frame).
 *
 * WHY a dedicated file (not FileViewerPanel.memo.test.tsx): that suite MOCKS
 * ../FileViewer wholesale to proxy the PANEL's re-render — which structurally
 * cannot exercise the source `memo(FileViewer)` (the mock replaces it). This file
 * runs the REAL FileViewer so the source memo is under test (Gate-1 WARN 2).
 *
 * WHY a deferred rAF mock (not the sync one in width.test.tsx): a synchronous rAF
 * (`cb(0)` inline) fires the callback immediately, so N mousemoves = N inline
 * setWidths and the mouseup-flush branch is dead — the coalescing/flush assertions
 * would be vacuous (Gate-1 BLOCK 1). Here rAF callbacks are QUEUED and fired on
 * demand via flushRaf(), so coalescing is real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import FileViewerPanel, { PANEL_CONSTANTS } from '../FileViewerPanel';

// Count how many times the FileViewer body re-runs by counting a mocked LEAF child
// (FileEditorCore) that FileViewer renders for a text file. FileViewer itself is
// NOT mocked — so the source `memo(FileViewerImpl)` is genuinely exercised.
let leafRenders = 0;
vi.mock('../../common/FileEditorCore', () => ({
  default: () => {
    leafRenders++;
    return <div data-testid="editor-core-stub" />;
  },
}));
vi.mock('../FileViewerTabBar', () => ({ default: () => <div /> }));
vi.mock('../FileViewerStatusBar', () => ({ default: () => <div /> }));
vi.mock('../renderers/CsvRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/ImageRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/PdfRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/HtmlRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/VideoRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/AudioRenderer', () => ({ default: () => <div /> }));
vi.mock('../renderers/UnsupportedRenderer', () => ({ default: () => <div /> }));
vi.mock('../CanvasOutputRail', () => ({ CanvasOutputRail: () => <div /> }));
// api read → resolve a tiny text content so FileViewer renders FileEditorCore.
vi.mock('../../../services/api', () => ({
  default: {
    get: vi.fn(async () => ({ data: { content: 'hi', committedContent: 'hi' } })),
  },
}));

const { STORAGE_KEY, MIN_WIDTH } = PANEL_CONSTANTS;

// STABLE prop refs (module scope) — mirror useCanvasHost's dep-stable contract, so
// a width-only re-render passes identical props to FileViewer (memo can skip).
const STABLE_FILE = { filePath: '/ws/notes.txt', fileName: 'notes.txt' };
const STABLE = {
  tabScopeKey: 'tab-1',
  initialFile: STABLE_FILE,
  onClose: () => {},
  pinned: false,
  onTogglePin: () => {},
  muted: false,
  onToggleMute: () => {},
  referencedFiles: { written: [] },
};

function setInnerWidth(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, writable: true, configurable: true });
}
const origInnerWidth = window.innerWidth;

function panelWidth(): number {
  return parseInt((screen.getByTestId('file-viewer-panel') as HTMLElement).style.width, 10);
}

describe('FileViewerPanel drag perf — memo(FileViewer) skips the heavy subtree per frame (AC1)', () => {
  // SYNC rAF here (like width.test.tsx): the reveal effect + the drag rAF fire
  // inline, so each mousemove produces one setWidth. The POINT of this suite is the
  // memo, not coalescing — so with memo, the leaf render count must NOT grow with
  // the number of drag frames.
  let rafSpy: ReturnType<typeof vi.spyOn> | undefined;
  beforeEach(() => {
    localStorage.clear();
    leafRenders = 0;
    setInnerWidth(2000);
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 0 as unknown as number;
    });
  });
  afterEach(() => {
    setInnerWidth(origInnerWidth);
    rafSpy?.mockRestore();
  });

  it('a drag (mousedown + N mousemoves + mouseup) does NOT re-render FileViewer per frame', async () => {
    await act(async () => {
      render(<FileViewerPanel {...STABLE} />);
    });
    // Let the async content load settle so FileEditorCore has mounted once.
    await act(async () => { await Promise.resolve(); });
    const baseline = leafRenders;
    expect(baseline).toBeGreaterThanOrEqual(1); // FileViewer rendered the editor at least once

    const handle = screen.getByTestId('panel-resize-handle');
    // mousedown must COMMIT (isDragging effect binds the document listeners) BEFORE
    // the moves fire — so mousedown is its own act, then the moves, then mouseup.
    await act(async () => { fireEvent.mouseDown(handle, { clientX: 1400 }); });
    await act(async () => {
      // 6 drag frames — each triggers a parent setWidth (sync rAF fires inline).
      for (let x = 1390; x >= 1340; x -= 10) fireEvent.mouseMove(document, { clientX: x });
    });
    await act(async () => { fireEvent.mouseUp(document); });

    // The panel re-rendered many times (width changed each frame), but the MEMOized
    // FileViewer must have skipped every one → leaf render count unchanged.
    // Mutation check: unwrap `memo` in FileViewer.tsx → this grows past baseline (RED).
    expect(leafRenders).toBe(baseline);
    // And the width actually changed (drag worked): started 680 (0.34*2000), dragged wider.
    expect(panelWidth()).toBeGreaterThan(680);
  });
});

describe('FileViewerPanel drag perf — rAF coalescing + persist-on-drag-end (AC2/AC3)', () => {
  // DEFERRED rAF mock: callbacks are QUEUED, fired on demand. cancelAnimationFrame
  // removes by the exact monotonic id (Map-keyed) so a cancelled frame never fires.
  let rafQueue: Map<number, FrameRequestCallback>;
  let rafSeq: number;
  const flushRaf = () => {
    const cbs = Array.from(rafQueue.values());
    rafQueue.clear();
    cbs.forEach((cb) => cb(0));
  };
  let rafSpy: ReturnType<typeof vi.spyOn> | undefined;
  let cafSpy: ReturnType<typeof vi.spyOn> | undefined;

  beforeEach(() => {
    localStorage.clear();
    leafRenders = 0;
    setInnerWidth(2000);
    rafQueue = new Map();
    rafSeq = 0;
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      const id = ++rafSeq;
      rafQueue.set(id, cb);
      return id as unknown as number;
    });
    cafSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id: number) => {
      rafQueue.delete(id);
    });
  });
  afterEach(() => {
    setInnerWidth(origInnerWidth);
    rafSpy?.mockRestore();
    cafSpy?.mockRestore();
  });

  it('N mousemoves in one frame → exactly ONE setWidth (coalesced), and localStorage NOT written mid-drag', async () => {
    await act(async () => { render(<FileViewerPanel {...STABLE} />); });
    // The mount-reveal rAF is queued (deferred) — flush it so the panel shows its
    // real width before we start dragging.
    act(() => flushRaf());
    const startWidth = panelWidth(); // 680 (0.34*2000, under the ceiling)

    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1400 });
    // 5 mousemoves BEFORE any frame fires — they must coalesce to one pending width.
    fireEvent.mouseMove(document, { clientX: 1390 });
    fireEvent.mouseMove(document, { clientX: 1380 });
    fireEvent.mouseMove(document, { clientX: 1370 });
    fireEvent.mouseMove(document, { clientX: 1360 });
    fireEvent.mouseMove(document, { clientX: 1350 }); // net delta = +50 → width 730

    // No frame has fired yet → width unchanged, and NOTHING persisted.
    expect(panelWidth()).toBe(startWidth);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();

    // Exactly ONE rAF should be queued (the gate rafIdRef===null coalesces the rest).
    expect(rafQueue.size).toBe(1);

    act(() => flushRaf()); // fire the single frame
    expect(panelWidth()).toBe(730); // 680 + 50, the LAST requested width
    // Still not persisted — persist happens on drag-end, not per frame.
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('mouseup flushes the final frame and persists EXACTLY ONCE (== final width)', async () => {
    await act(async () => { render(<FileViewerPanel {...STABLE} />); });
    act(() => flushRaf());
    const startWidth = panelWidth();

    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1400 });
    fireEvent.mouseMove(document, { clientX: 1300 }); // delta +100 → 780, one frame queued
    expect(rafQueue.size).toBe(1);

    // mouseup BEFORE the frame fires: it must cancel the pending frame, apply the
    // final width synchronously, and persist once.
    act(() => { fireEvent.mouseUp(document); });

    expect(rafQueue.size).toBe(0); // the pending frame was cancelled (not left to fire stale)
    expect(panelWidth()).toBe(startWidth + 100); // final width applied
    expect(localStorage.getItem(STORAGE_KEY)).toBe(String(startWidth + 100)); // persisted once, == final
  });

  it('a click on the handle WITHOUT a mousemove does NOT snap/persist a stale or zero width (regression)', async () => {
    // REVIEW HIGH: handleMouseDown must seed pendingWidthRef=width, else a bare
    // click flushes pendingWidthRef.current (0 on first click → clamps to MIN_WIDTH
    // → snaps + persists a 320px panel). With the seed, a zero-move click commits
    // the UNCHANGED current width (idempotent) and persists that same value.
    await act(async () => { render(<FileViewerPanel {...STABLE} />); });
    act(() => flushRaf());
    const startWidth = panelWidth(); // 680, the real current width — NOT MIN_WIDTH

    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1400 });
    // NO mousemove — straight to mouseup (a stray click on the divider).
    act(() => { fireEvent.mouseUp(document); });

    expect(panelWidth()).toBe(startWidth); // did NOT snap to MIN_WIDTH
    expect(panelWidth()).not.toBe(MIN_WIDTH);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(String(startWidth)); // persisted the unchanged width
  });

  it('MIN_WIDTH floor still holds through the rAF path', async () => {
    await act(async () => { render(<FileViewerPanel {...STABLE} />); });
    act(() => flushRaf());
    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 400 });
    fireEvent.mouseMove(document, { clientX: 4000 }); // huge rightward → far below MIN
    act(() => flushRaf());
    fireEvent.mouseUp(document);
    expect(panelWidth()).toBe(MIN_WIDTH);
  });
});
