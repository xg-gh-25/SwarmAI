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
import FileViewerPanel, { PANEL_CONSTANTS } from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

const baseProps = {
  sessionId: 'sess-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
};

const { MIN_WIDTH, MAX_WIDTH, STORAGE_KEY, DEFAULT_FRACTION } = PANEL_CONSTANTS;
const clampW = (w: number) => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, w));
// Responsive default the component computes; asserted against the CONSTANT (not a
// hardcoded fraction) so the test tracks the source and can't silently drift.
const respW = (innerW: number) => clampW(Math.round(DEFAULT_FRACTION * innerW));

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
});
