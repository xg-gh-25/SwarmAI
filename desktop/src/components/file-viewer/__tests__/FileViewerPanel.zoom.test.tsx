/**
 * FileViewerPanel under app zoom (`<html style.zoom>`, useZoom).
 *
 * Under CSS zoom on <html>, window.innerWidth stays in unzoomed px while layout
 * runs in zoomed CSS px (measured in WKWebView and Chromium 143 at zoom 2 on an
 * 1100px window: innerWidth 1100, documentElement.clientWidth 550; pointer
 * clientX and getBoundingClientRect are unzoomed too). The Canvas used innerWidth
 * for every width and the raw clientX delta for drags, so at zoom > 1 it took
 * zoom-times its share (chat starved) and its edge outran the pointer.
 *
 * jsdom has no layout (clientWidth 0, empty rects), so each case here stubs the
 * zoomed geometry explicitly and restores it afterwards; the existing width
 * suite keeps running on the unstubbed fallback path.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import FileViewerPanel, { PANEL_CONSTANTS, availableCanvasMax } from '../FileViewerPanel';
import { layoutViewportWidth, clientToCssScale, APP_ZOOM_EVENT } from '../../../utils/layoutViewport';

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
  collapse: { railed: false, outputsCollapsed: false },
  setCollapse: vi.fn(),
  isOpen: true,
  onRevealCanvas: vi.fn(),
};

const { MIN_WIDTH, STORAGE_KEY, DEFAULT_FRACTION } = PANEL_CONSTANTS;
const html = document.documentElement;
const origInnerWidth = window.innerWidth;
let rafSpy: ReturnType<typeof vi.spyOn> | undefined;

/** Emulate `<html style.zoom = zoom>` on a window `windowPx` wide. */
function setZoomedViewport(windowPx: number, zoom: number) {
  Object.defineProperty(window, 'innerWidth', { value: windowPx, writable: true, configurable: true });
  Object.defineProperty(html, 'clientWidth', { value: Math.round(windowPx / zoom), configurable: true });
  html.getBoundingClientRect = () =>
    ({ x: 0, y: 0, left: 0, top: 0, right: windowPx, bottom: 700, width: windowPx, height: 700, toJSON: () => ({}) }) as DOMRect;
}

function panelWidth(): number {
  return parseInt((screen.getByTestId('file-viewer-panel') as HTMLElement).style.width, 10);
}

beforeEach(() => {
  localStorage.clear();
  rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
    cb(0);
    return 0 as unknown as number;
  });
});
afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { value: origInnerWidth, writable: true, configurable: true });
  delete (html as unknown as Record<string, unknown>).clientWidth;
  delete (html as unknown as Record<string, unknown>).getBoundingClientRect;
  rafSpy?.mockRestore();
});

describe('layoutViewport helpers', () => {
  it('read the zoomed layout width and the client->CSS scale', () => {
    setZoomedViewport(1100, 2);
    expect(layoutViewportWidth()).toBe(550);
    expect(clientToCssScale()).toBe(0.5);
  });

  it('fall back to innerWidth and scale 1 when there is no layout (jsdom, boot)', () => {
    Object.defineProperty(window, 'innerWidth', { value: 1300, writable: true, configurable: true });
    expect(html.clientWidth).toBe(0);
    expect(layoutViewportWidth()).toBe(1300);
    expect(clientToCssScale()).toBe(1);
  });
});

describe('FileViewerPanel — width follows the layout viewport under app zoom', () => {
  it('first open at zoom 2 sizes from 550 CSS px, not the 1100 unzoomed px', () => {
    setZoomedViewport(1100, 2);
    render(<FileViewerPanel {...baseProps} />);
    const expected = Math.max(MIN_WIDTH, Math.min(availableCanvasMax(550), Math.round(DEFAULT_FRACTION * 550)));
    expect(expected).toBe(MIN_WIDTH); // 0.34*550 = 187 -> floored to 320
    expect(panelWidth()).toBe(expected); // was 374 (0.34*1100) with innerWidth
  });

  it('a stored width is clamped to the zoomed ceiling at read time', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setZoomedViewport(1728, 2); // layout 864 -> ceiling max(320, 864-150-480-12) = 320
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(availableCanvasMax(864));
    expect(panelWidth()).toBe(320); // innerWidth would have allowed 600
  });

  it('drag at zoom 2: a 100 client-px pointer move changes the width by 50 CSS px', () => {
    localStorage.setItem(STORAGE_KEY, '400');
    setZoomedViewport(4000, 2); // layout 2000 -> ceiling 900, so 400 and 450 both fit
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(400);
    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 3000 });
    fireEvent.mouseMove(document, { clientX: 2900 });
    fireEvent.mouseUp(document);
    expect(panelWidth()).toBe(450); // edge tracks the pointer 1:1 on screen
    expect(localStorage.getItem(STORAGE_KEY)).toBe('450');
  });

  it('a zoom change recomputes the responsive default when no width is stored', () => {
    setZoomedViewport(1728, 1);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(Math.round(DEFAULT_FRACTION * 1728)); // 588
    act(() => {
      setZoomedViewport(1728, 2);
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    expect(panelWidth()).toBe(320);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull(); // a zoom is not a manual choice
  });

  it('a zoom change clamps a stored width down without persisting it', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setZoomedViewport(1728, 1);
    render(<FileViewerPanel {...baseProps} />);
    expect(panelWidth()).toBe(600);
    act(() => {
      setZoomedViewport(1728, 1.5); // layout 1152 -> ceiling 510
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    expect(panelWidth()).toBe(availableCanvasMax(1152));
    expect(panelWidth()).toBe(510);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('600'); // the user's choice is kept
  });

  it('a zoom round trip restores the dragged width (in to 1.5, back to 1)', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setZoomedViewport(1728, 1);
    render(<FileViewerPanel {...baseProps} />);
    act(() => {
      setZoomedViewport(1728, 1.5);
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    expect(panelWidth()).toBe(510);
    act(() => {
      setZoomedViewport(1728, 1);
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    expect(panelWidth()).toBe(600); // not stuck at 510
  });

  it('expanded: a zoom change re-snaps to the new ceiling, and restore is clamped', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setZoomedViewport(1728, 1);
    render(<FileViewerPanel {...baseProps} />);
    fireEvent.click(screen.getByLabelText('Expand Canvas for review'));
    expect(panelWidth()).toBe(availableCanvasMax(1728)); // 900
    act(() => {
      setZoomedViewport(1728, 2); // layout 864 -> ceiling 320
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    expect(panelWidth()).toBe(availableCanvasMax(864));
    fireEvent.click(screen.getByLabelText('Restore Canvas width'));
    expect(panelWidth()).toBe(availableCanvasMax(864)); // pre-expand 600 no longer fits
  });

  it('drag at zoom 1 with real geometry (measured scale 1) stays 1:1', () => {
    localStorage.setItem(STORAGE_KEY, '600');
    setZoomedViewport(2000, 1); // clientWidth == rect width == 2000, both > 0
    render(<FileViewerPanel {...baseProps} />);
    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 1400 });
    fireEvent.mouseMove(document, { clientX: 1300 });
    fireEvent.mouseUp(document);
    expect(panelWidth()).toBe(700);
  });

  it('a zoom change mid-drag re-takes the baseline so the edge keeps tracking the pointer', () => {
    localStorage.setItem(STORAGE_KEY, '400');
    setZoomedViewport(4000, 1); // ceiling 900 at zoom 1, 900 at zoom 2 (layout 2000)
    render(<FileViewerPanel {...baseProps} />);
    const handle = screen.getByTestId('panel-resize-handle');
    fireEvent.mouseDown(handle, { clientX: 3000 });
    fireEvent.mouseMove(document, { clientX: 2900 }); // +100 CSS px at zoom 1 -> 500
    expect(panelWidth()).toBe(500);
    act(() => {
      setZoomedViewport(4000, 2);
      window.dispatchEvent(new Event(APP_ZOOM_EVENT));
    });
    fireEvent.mouseMove(document, { clientX: 2800 }); // 100 more client px = 50 CSS px now
    fireEvent.mouseUp(document);
    expect(panelWidth()).toBe(550); // with the stale scale it would be 600
  });
});
