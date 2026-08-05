/**
 * FileViewerPanel responsive default width (run_b2d228f6).
 *
 * Canvas default width was a fixed 500px — a thin sliver on wide monitors
 * (500/3840 = 13%) and no adaptation to the screen. This suite verifies the
 * responsive default: first open (no localStorage) = clamp(320, 0.34*innerWidth,
 * 900); a user-dragged width (localStorage present) stays authoritative and is
 * immune to window resize; the resize recompute is gated on (entered && !expanded
 * && no-stored-value) so it never clobbers expand-mode MAX_WIDTH or the reveal
 * animation (Gate-1 CRITICAL #1/#2).
 *
 * FileViewer + CanvasOutputRail are leaf-stubbed. jsdom window.innerWidth = 1024
 * by default; we set it explicitly per case.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import FileViewerPanel, { PANEL_CONSTANTS, availableCanvasMax } from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

const baseProps = {
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
  referencedFiles: { written: [] },
};

const { MIN_WIDTH, MAX_WIDTH, STORAGE_KEY, DEFAULT_FRACTION } = PANEL_CONSTANTS;
// Responsive default the component computes; asserted against the source formula
// (availableCanvasMax + the constant) so the test tracks the source and can't
// silently drift. The ceiling is availableCanvasMax(innerW) (viewport-aware, reserves
// chat), NOT raw MAX_WIDTH — that is the BUG2 fix under test.
const respW = (innerW: number) =>
  Math.max(MIN_WIDTH, Math.min(availableCanvasMax(innerW), Math.round(DEFAULT_FRACTION * innerW)));

function setInnerWidth(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, writable: true, configurable: true });
}

function panelWidth(): number {
  const el = screen.getByTestId('file-viewer-panel') as HTMLElement;
  return parseInt(el.style.width, 10);
}

const origInnerWidth = window.innerWidth;
let rafSpy: ReturnType<typeof vi.spyOn> | undefined;
beforeEach(() => {
  localStorage.clear();
  // Reveal animation flips `entered` via rAF; run it synchronously so the panel
  // renders its REAL width (not the MIN_WIDTH reveal floor) inside act().
  rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
    cb(0);
    return 0 as unknown as number;
  });
});
afterEach(() => {
  setInnerWidth(origInnerWidth);
  rafSpy?.mockRestore();
});

describe('FileViewerPanel — responsive default width (run_b2d228f6)', () => {
  it('AC1: first open (no localStorage) width = clamp(MIN, DEFAULT_FRACTION*innerWidth, MAX), not fixed 500', () => {
    setInnerWidth(2000); // mid-range: 0.34*2000 = 680 (between MIN 320 and MAX 900)
    render(<FileViewerPanel {...baseProps} />);
    const w = panelWidth();
    expect(w).toBe(respW(2000));       // tracks the constant
    expect(w).toBeGreaterThan(MIN_WIDTH);
    expect(w).toBeLessThan(MAX_WIDTH); // proves this case exercises the mid-range, not a clamp edge
    expect(w).not.toBe(500);           // NOT the old fixed default
  });

  it('AC1b: narrow screen clamps to MIN_WIDTH floor (DEFAULT_FRACTION*640 < 320)', () => {
    setInnerWidth(640);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(MIN_WIDTH); // 0.34*640 = 217.6 clamped up to 320
  });

  it('AC1c: ultra-wide clamps to MAX_WIDTH ceiling (DEFAULT_FRACTION*3840 > 900)', () => {
    setInnerWidth(3840);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(MAX_WIDTH); // 0.34*3840 = 1305.6 clamped down to 900
  });

  it('AC2: a stored (user-dragged) width is used verbatim, ignoring the responsive default', () => {
    localStorage.setItem(STORAGE_KEY, '720');
    setInnerWidth(2000); // responsive would be 680, but stored 720 must win
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(720);
  });

  it('AC3: window resize recomputes width when NO stored value', () => {
    setInnerWidth(1600); // initial responsive
    render(<FileViewerPanel {...baseProps} />);
    act(() => {
      setInnerWidth(2000); // resize wider (0.34*2000 = 680, mid-range)
      window.dispatchEvent(new Event('resize'));
    });
    expect(panelWidth()).toBe(respW(2000)); // recomputed, tracks the constant
  });

  it('AC3b: window resize does NOT override a user-dragged (stored) width', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setInnerWidth(1600);
    render(<FileViewerPanel {...baseProps} />);
    act(() => {
      setInnerWidth(3200);
      window.dispatchEvent(new Event('resize'));
    });
    expect(panelWidth()).toBe(600); // stored width immune to resize
  });

  // ── BUG2 fix: viewport-aware ceiling reserves chat width ───────────────────
  it('AC4: the width ceiling is availableCanvasMax (viewport-aware), NOT raw MAX_WIDTH', () => {
    // At 1000px the fraction (0.34*1000=340) is what wins for the DEFAULT — but the
    // POINT under test is the ceiling itself: availableCanvasMax(1000)=358 < MAX_WIDTH
    // 900. The panel can never reach MAX_WIDTH here (that would leave chat 1000-150-900-12
    // = -62). The stored/drag paths (AC4b, DRAG-clamped) are where the ceiling BINDS a
    // too-wide request; this case pins the default + the ceiling's value.
    setInnerWidth(1000);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(respW(1000)); // default (fraction wins at 1000)
    expect(panelWidth()).toBeLessThanOrEqual(availableCanvasMax(1000));
    expect(availableCanvasMax(1000)).toBe(358);
    expect(availableCanvasMax(1000)).toBeLessThan(MAX_WIDTH); // ceiling < raw MAX
  });

  it('AC4b: a stored width WIDER than currently fits is clamped DOWN to availableCanvasMax at read time (Finding 2)', () => {
    // User dragged to 850 on a big monitor, reopens on a 1100px laptop.
    localStorage.setItem(STORAGE_KEY, '850');
    setInnerWidth(1100); // avail = 1100-150-480-12 = 458
    render(<FileViewerPanel {...baseProps} />);
    // Stored 850 is NOT honored verbatim here — it would starve chat; clamp to 458.
    expect(panelWidth()).toBe(availableCanvasMax(1100));
    expect(panelWidth()).toBe(458);
  });

  it('AC4c: on a wide window the stored width IS honored verbatim (clamp is a ceiling, not a rewrite)', () => {
    localStorage.setItem(STORAGE_KEY, '720');
    setInnerWidth(2000); // avail = 2000-150-480-12 = 900 (MAX_WIDTH) → 720 fits, honored
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(720);
  });

  it('AC4d: MIN_WIDTH is the hard floor — a tiny window yields chat below CHAT_MIN_HEALTHY (physical reality)', () => {
    setInnerWidth(800); // avail = 800-150-480-12 = 158 → floored UP to MIN_WIDTH 320
    render(<FileViewerPanel {...baseProps} />);
    expect(availableCanvasMax(800)).toBe(MIN_WIDTH); // floor wins
    // chat gets 800-150-320-12 = 318 < CHAT_MIN_HEALTHY(480) — intentional, not a bug.
    expect(panelWidth()).toBeGreaterThanOrEqual(MIN_WIDTH);
  });

  // ── BUG1 fix: the resize DRAG interaction actually works (AC3 of the plan) ──
  it('DRAG: mousedown→mousemove→mouseup on the handle changes width AND persists it', () => {
    localStorage.setItem(STORAGE_KEY, '600'); // start from a known width
    setInnerWidth(2000); // avail = 900, so 600 and a wider drag both fit
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(600);

    const handle = screen.getByTestId('panel-resize-handle');
    // Drag the LEFT edge leftward by 100px → wider (delta = startX - clientX = 100).
    fireEvent.mouseDown(handle, { clientX: 1400 });
    fireEvent.mouseMove(document, { clientX: 1300 }); // moved left 100 → +100 width
    fireEvent.mouseUp(document);

    expect(panelWidth()).toBe(700); // 600 + 100
    expect(localStorage.getItem(STORAGE_KEY)).toBe('700'); // persisted
  });

  it('DRAG: a drag is clamped to availableCanvasMax so it cannot starve chat', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setInnerWidth(1100); // avail = 458
    render(<FileViewerPanel {...baseProps} />);
    // panel opens clamped to 458 (AC4b); drag to make it much wider
    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1000 });
    fireEvent.mouseMove(document, { clientX: 0 }); // huge leftward drag → +1000 requested
    fireEvent.mouseUp(document);
    expect(panelWidth()).toBe(availableCanvasMax(1100)); // clamped, chat preserved
    expect(panelWidth()).toBe(458);
  });

  it('DRAG: content column is inert (pointer-events:none) ONLY during the drag (iframe drag-shield)', () => {
    setInnerWidth(2000);
    render(<FileViewerPanel {...baseProps} />);
    const content = screen.getByTestId('canvas-content-column') as HTMLElement;
    expect(content.style.pointerEvents).toBe(''); // at rest: interactive

    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1400 });
    expect(content.style.pointerEvents).toBe('none'); // during drag: inert (shield)

    fireEvent.mouseUp(document);
    expect(content.style.pointerEvents).toBe(''); // after drag: interactive again
  });
});
