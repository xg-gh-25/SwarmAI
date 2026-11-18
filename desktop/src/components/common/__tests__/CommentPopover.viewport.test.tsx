/**
 * The Canvas review comment box sits NEXT TO the commented line, never on it, and
 * always inside the window.
 *
 * Pinned here:
 * - App zoom: the anchor rect is client px while `style.top/left` are CSS px, so at
 *   zoom > 1 an unscaled rect put the box zoom-times too far right and down. The
 *   target is scaled by clientToCssScale() and the placement uses the layout viewport.
 * - Below the line when it fits, above it when it does not; never covering the line.
 * - Never under the macOS title bar (the top 28 px of the window) or past an edge.
 * - Growth after opening: the input grows with the text, so the box is re-placed by
 *   a ResizeObserver and its bottom stays on screen.
 *
 * jsdom has no layout: the zoom, the window size, the anchor rect and the popover
 * size are stubbed, and ResizeObserver is replaced by a manual trigger.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import CommentPopover from '../CommentPopover';

const html = document.documentElement;
const origInnerWidth = window.innerWidth;
const origInnerHeight = window.innerHeight;
const OrigResizeObserver = globalThis.ResizeObserver;

let resizeCallbacks: Array<() => void> = [];
class ManualResizeObserver {
  cb: () => void;
  constructor(cb: () => void) { this.cb = cb; }
  observe() { resizeCallbacks.push(this.cb); }
  unobserve() {}
  disconnect() { resizeCallbacks = resizeCallbacks.filter((c) => c !== this.cb); }
}

function setWindow(width: number, height: number, zoom: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, writable: true, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: height, writable: true, configurable: true });
  Object.defineProperty(html, 'currentCSSZoom', { value: zoom, configurable: true });
}

/** The popover's own size in CSS px (what offsetWidth/offsetHeight report). */
function setPopoverSize(width: number, height: number) {
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { value: width, configurable: true });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { value: height, configurable: true });
}

function anchorAt(rect: { top: number; right: number }) {
  const ref = createRef<HTMLDivElement>();
  const el = document.createElement('div');
  el.getBoundingClientRect = () =>
    ({ x: 0, y: rect.top, top: rect.top, left: 0, right: rect.right, bottom: rect.top + 10, width: rect.right, height: 10, toJSON: () => ({}) }) as DOMRect;
  (ref as { current: HTMLDivElement | null }).current = el;
  return ref;
}

function popoverBox() {
  const el = screen.getByTestId('comment-popover') as HTMLElement;
  return { top: parseFloat(el.style.top), left: parseFloat(el.style.left), maxHeight: parseFloat(el.style.maxHeight) };
}

afterEach(() => {
  cleanup();
  Object.defineProperty(window, 'innerWidth', { value: origInnerWidth, writable: true, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: origInnerHeight, writable: true, configurable: true });
  delete (html as unknown as Record<string, unknown>).currentCSSZoom;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetHeight;
  globalThis.ResizeObserver = OrigResizeObserver;
  resizeCallbacks = [];
});

const noop = vi.fn();

const LINE = 24;
const TITLE_BAR_BOTTOM = 28;
/** The box and the line [top, top+LINE) do not overlap. */
function clearOfLine(box: { top: number }, height: number, lineTop: number) {
  return box.top >= lineTop + LINE || box.top + height <= lineTop;
}

describe('CommentPopover — next to the line, inside the window', () => {
  it('at zoom 1.5 the client-px anchor is scaled into CSS px; the box opens under the line', () => {
    // 1500×900 window at zoom 1.5 -> layout viewport 1000×600 CSS px.
    setWindow(1500, 900, 1.5);
    setPopoverSize(364, 220);
    // Gutter right edge at client x=600 (CSS 400), top at client y=150 (CSS 100); line at CSS 140.
    render(<CommentPopover lineNumber={3} onSubmit={noop} onCancel={noop} topOffset={40} anchorRef={anchorAt({ top: 150, right: 600 })} />);
    const box = popoverBox();
    expect(box.left).toBeCloseTo(404, 3); // 600/1.5 + 4, not 604
    expect(box.top).toBeCloseTo(140 + LINE + 6, 3); // under the line, CSS px
    expect(box.maxHeight).toBeCloseTo(600 - 8 - (140 + LINE + 6), 3); // the room below
  });

  it('a line near the bottom-right: the box goes above the line and stays inside the layout viewport', () => {
    setWindow(1500, 900, 1.5);
    setPopoverSize(364, 220);
    // Client (1400, 840) is CSS (933, 560): no room below a 220 px box.
    render(<CommentPopover lineNumber={30} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 840, right: 1400 })} />);
    const box = popoverBox();
    expect(box.left + 364).toBeLessThanOrEqual(1000 - 8 + 1e-6);
    expect(box.top + 220).toBeLessThanOrEqual(560 - 6 + 1e-6); // ends above the line
    expect(clearOfLine(box, 220, 560)).toBe(true);
  });

  it('a line just under the title bar: the box opens below it, never under the title bar', () => {
    setWindow(1400, 900, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={1} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 30, right: 300 })} />);
    const box = popoverBox();
    expect(box.top).toBeGreaterThanOrEqual(TITLE_BAR_BOTTOM);
    expect(clearOfLine(box, 220, 30)).toBe(true);
  });

  it('more room above than the box needs nowhere: it fills the room above but stops at the title bar', () => {
    setWindow(1400, 600, 1);
    setPopoverSize(364, 400);
    // Line at 400: 162 px below it, 358 px above it down to the title bar.
    render(<CommentPopover lineNumber={9} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 400, right: 300 })} />);
    const box = popoverBox();
    expect(box.top).toBeGreaterThanOrEqual(TITLE_BAR_BOTTOM + 8);
    expect(box.top + box.maxHeight).toBeLessThanOrEqual(400 - 6 + 1e-6); // scrolls inside, line clear
  });

  it('a window too small for either side keeps the whole box on screen, below the title bar', () => {
    setWindow(800, 300, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={5} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 140, right: 300 })} />);
    const box = popoverBox();
    expect(box.top).toBeGreaterThanOrEqual(TITLE_BAR_BOTTOM + 8);
    expect(box.top + Math.min(220, box.maxHeight)).toBeLessThanOrEqual(300 - 8 + 1e-6);
  });

  it('a window shorter than the title bar inset never gets a negative max height', () => {
    setWindow(800, 40, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={1} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 10, right: 300 })} />);
    expect(popoverBox().maxHeight).toBeGreaterThan(0);
  });

  it('when the comment box grows after opening, the popover moves up so its bottom stays on screen', () => {
    globalThis.ResizeObserver = ManualResizeObserver as unknown as typeof ResizeObserver;
    setWindow(1400, 900, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={40} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 860, right: 900 })} />);
    expect(popoverBox().top).toBe(860 - 6 - 220); // above the line: no room below it
    // The text grows: the popover is now 560 px high.
    setPopoverSize(364, 560);
    act(() => { resizeCallbacks.forEach((cb) => cb()); });
    expect(popoverBox().top).toBe(860 - 6 - 560);
    expect(popoverBox().top).toBeGreaterThanOrEqual(TITLE_BAR_BOTTOM);
  });

  it('the commented line is quoted in the box', () => {
    setWindow(1400, 900, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={2} lineText="alpha beta" onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 100, right: 300 })} />);
    expect(screen.getByTestId('comment-box-quote').textContent).toBe('alpha beta');
  });
});
