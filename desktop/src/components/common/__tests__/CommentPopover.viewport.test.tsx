/**
 * CommentPopover stays inside the window (the Canvas review comment box).
 *
 * Two ways it used to leave the window, each pinned here:
 * - App zoom: the anchor rect is client px while `style.top/left` are CSS px, so
 *   at zoom > 1 an unscaled rect put the popover zoom-times too far right and
 *   down. The anchor is scaled by clientToCssScale() and the clamp uses the
 *   layout viewport.
 * - Growth after opening: the comment box is drag-resizable, and the popover was
 *   placed once on open, so a box opened at the bottom edge pushed its buttons off
 *   screen as it grew. A ResizeObserver re-places it.
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

describe('CommentPopover — inside the window', () => {
  it('at zoom 1.5 the client-px anchor is scaled into CSS px and the clamp uses the layout viewport', () => {
    // 1500×900 window at zoom 1.5 -> layout viewport 1000×600 CSS px.
    setWindow(1500, 900, 1.5);
    setPopoverSize(364, 220);
    // Gutter right edge at client x=600 (CSS 400), top at client y=150 (CSS 100).
    render(<CommentPopover lineNumber={3} onSubmit={noop} onCancel={noop} topOffset={40} anchorRef={anchorAt({ top: 150, right: 600 })} />);
    const box = popoverBox();
    expect(box.left).toBeCloseTo(404, 3); // 600/1.5 + 4, not 604
    expect(box.top).toBeCloseTo(140, 3); // 150/1.5 + 40, not 190
    expect(box.maxHeight).toBeCloseTo(600 - 16, 3);
  });

  it('at zoom 1.5 an anchor near the bottom-right is clamped inside the layout viewport', () => {
    setWindow(1500, 900, 1.5);
    setPopoverSize(364, 220);
    // Client (1400, 840) is CSS (933, 560): both past where a 364×220 box still fits.
    render(<CommentPopover lineNumber={30} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 840, right: 1400 })} />);
    const box = popoverBox();
    expect(box.left + 364).toBeLessThanOrEqual(1000 - 8 + 1e-6);
    expect(box.top + 220).toBeLessThanOrEqual(600 - 8 + 1e-6);
  });

  it('when the comment box grows after opening, the popover moves up so its bottom stays on screen', () => {
    globalThis.ResizeObserver = ManualResizeObserver as unknown as typeof ResizeObserver;
    setWindow(1400, 900, 1);
    setPopoverSize(364, 220);
    render(<CommentPopover lineNumber={40} onSubmit={noop} onCancel={noop} topOffset={0} anchorRef={anchorAt({ top: 860, right: 900 })} />);
    expect(popoverBox().top).toBe(900 - 220 - 8);
    // The user drags the textarea taller: the popover is now 560 px high.
    setPopoverSize(364, 560);
    act(() => { resizeCallbacks.forEach((cb) => cb()); });
    expect(popoverBox().top).toBe(900 - 560 - 8);
  });
});
