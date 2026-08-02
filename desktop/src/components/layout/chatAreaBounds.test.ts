/**
 * chatAreaBounds — viewport-clamp on the published chat-area rect.
 *
 * The observed chat-area element can report a bottom BELOW the fold (its
 * flex/overflow chain lets it extend past window.innerHeight). A fullscreen
 * Modal binds its scrim to this rect and anchors the panel bottom to
 * rect.height, so an un-clamped height pushes the panel below the visible
 * window (the "modal bottom overflows the window" bug, 2026-08-02).
 *
 * measure() must clamp BOTH width and height so the rect's RIGHT and BOTTOM never
 * exceed the viewport (the modal right+bottom overflow bug, 2026-08-02). These
 * tests drive a stub element with a known getBoundingClientRect and assert the
 * published rect is clamped in both dimensions.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { observeChatArea, readChatAreaRect } from './chatAreaBounds';

// jsdom lacks ResizeObserver; observeChatArea publishes the initial rect
// synchronously so the RO callback need not fire for these assertions.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }

function setViewportHeight(px: number) {
  Object.defineProperty(window, 'innerHeight', { value: px, configurable: true, writable: true });
}

function setViewportWidth(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, configurable: true, writable: true });
}

function stubEl(rect: Partial<DOMRect>): HTMLElement {
  const el = document.createElement('div');
  el.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => {}, ...rect } as DOMRect);
  return el;
}

describe('chatAreaBounds.measure — viewport clamp', () => {
  const origRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
  const origH = window.innerHeight;
  const origW = window.innerWidth;

  afterEach(() => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = origRO;
    setViewportHeight(origH);
    setViewportWidth(origW);
    vi.restoreAllMocks();
  });

  it('clamps a BELOW-FOLD rect: height = innerHeight - top (bottom pinned to viewport)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportHeight(768);
    setViewportWidth(2000); // keep the width clamp a no-op — this case exercises height
    // top 80 + height 700 = bottom 780 > 768 → clamp to 768 - 80 = 688.
    const stop = observeChatArea(stubEl({ left: 150, top: 80, width: 900, height: 700, bottom: 780 }));
    try {
      const r = readChatAreaRect();
      expect(r?.height).toBe(688);
      expect((r?.top ?? 0) + (r?.height ?? 0)).toBe(768); // bottom == viewport
      expect(r?.width).toBe(900); // width within a 2000px viewport → untouched
      expect(r?.left).toBe(150);
    } finally { stop(); }
  });

  it('leaves a WITHIN-VIEWPORT rect unchanged (clamp is a no-op)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportHeight(768);
    setViewportWidth(2000); // width within viewport → no-op for this height case
    // top 80 + height 600 = bottom 680 < 768 → unchanged.
    const stop = observeChatArea(stubEl({ left: 150, top: 80, width: 900, height: 600, bottom: 680 }));
    try {
      expect(readChatAreaRect()?.height).toBe(600);
    } finally { stop(); }
  });

  it('never returns a negative height when top is already past the fold', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportHeight(500);
    // top 600 is below a 500px viewport → clamp floors at 0, never negative.
    const stop = observeChatArea(stubEl({ left: 150, top: 600, width: 900, height: 300, bottom: 900 }));
    try {
      expect(readChatAreaRect()?.height).toBe(0);
    } finally { stop(); }
  });

  it('clamps an OVER-WIDE rect: width = innerWidth - left (right pinned to viewport)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportWidth(1200);
    setViewportHeight(2000); // keep height clamp a no-op for this case
    // left 150 + width 1200 = right 1350 > 1200 → clamp width to 1200 - 150 = 1050.
    const stop = observeChatArea(stubEl({ left: 150, top: 80, width: 1200, height: 400, bottom: 480 }));
    try {
      const r = readChatAreaRect();
      expect(r?.width).toBe(1050);
      expect((r?.left ?? 0) + (r?.width ?? 0)).toBe(1200); // right == viewport
    } finally { stop(); }
  });

  it('leaves a WITHIN-VIEWPORT width unchanged (width clamp is a no-op)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportWidth(1200);
    setViewportHeight(2000);
    // left 150 + width 900 = right 1050 < 1200 → unchanged.
    const stop = observeChatArea(stubEl({ left: 150, top: 80, width: 900, height: 400, bottom: 480 }));
    try {
      expect(readChatAreaRect()?.width).toBe(900);
    } finally { stop(); }
  });

  it('never returns a negative width when left is already past the right edge', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportWidth(400);
    setViewportHeight(2000);
    // left 500 is right of a 400px viewport → clamp floors at 0, never negative.
    const stop = observeChatArea(stubEl({ left: 500, top: 80, width: 300, height: 400, bottom: 480 }));
    try {
      expect(readChatAreaRect()?.width).toBe(0);
    } finally { stop(); }
  });
});
