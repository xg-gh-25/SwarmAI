/**
 * FileViewerPanel responsive default width (run_b2d228f6).
 *
 * Canvas default width was a fixed 500px — a thin sliver on wide monitors
 * (500/3840 = 13%) and no adaptation to the screen. This suite verifies the
 * responsive default: first open (no localStorage) = clamp(320, 0.42*innerWidth,
 * 1200); a user-dragged width (localStorage present) stays authoritative and is
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
  isBookkeepingPath: () => false,
}));

const baseProps = {
  sessionId: 'sess-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  muted: false,
  onToggleMute: vi.fn(),
};

const { MIN_WIDTH, MAX_WIDTH, STORAGE_KEY } = PANEL_CONSTANTS;
const clampW = (w: number) => Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, w));

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
  it('AC1: first open (no localStorage) width = clamp(320, 0.42*innerWidth, 1200), not fixed 500', () => {
    setInnerWidth(2560); // 0.42*2560 = 1075.2 → 1075 (between MIN and MAX)
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(clampW(Math.round(0.42 * 2560))); // 1075, NOT the old fixed 500
  });

  it('AC1b: narrow screen clamps to MIN_WIDTH floor (0.42*640 = 269 < 320)', () => {
    setInnerWidth(640);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(MIN_WIDTH); // 268.8 clamped up to 320
  });

  it('AC1c: ultra-wide clamps to MAX_WIDTH ceiling (0.42*3840 = 1613 > 1200)', () => {
    setInnerWidth(3840);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(MAX_WIDTH); // 1613 clamped down to 1200
  });

  it('AC2: a stored (user-dragged) width is used verbatim, ignoring the responsive default', () => {
    localStorage.setItem(STORAGE_KEY, '720');
    setInnerWidth(2560); // responsive would be 1075, but stored 720 must win
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(720);
  });

  it('AC3: window resize recomputes width when NO stored value', () => {
    setInnerWidth(1600); // initial responsive = 672
    render(<FileViewerPanel {...baseProps} />);
    act(() => {
      setInnerWidth(2560); // resize wider
      window.dispatchEvent(new Event('resize'));
    });
    expect(panelWidth()).toBe(clampW(Math.round(0.42 * 2560))); // 1075
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
