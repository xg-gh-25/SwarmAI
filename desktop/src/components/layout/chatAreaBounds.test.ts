/**
 * chatAreaBounds — viewport-clamp on the published chat-area rect.
 *
 * The observed chat-area element can report a bottom BELOW the fold (its
 * flex/overflow chain lets it extend past window.innerHeight). A fullscreen
 * Modal binds its scrim to this rect and anchors the panel bottom to
 * rect.height, so an un-clamped height pushes the panel below the visible
 * window (the "modal bottom overflows the window" bug, 2026-08-02).
 *
 * measure() must clamp height so the rect's BOTTOM never exceeds the viewport;
 * width is untouched (the radar bounds it horizontally). These tests drive a
 * stub element with a known getBoundingClientRect and assert the published rect.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { observeChatArea, readChatAreaRect } from './chatAreaBounds';

// jsdom lacks ResizeObserver; observeChatArea publishes the initial rect
// synchronously so the RO callback need not fire for these assertions.
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }

function setViewportHeight(px: number) {
  Object.defineProperty(window, 'innerHeight', { value: px, configurable: true, writable: true });
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

  afterEach(() => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = origRO;
    setViewportHeight(origH);
    vi.restoreAllMocks();
  });

  it('clamps a BELOW-FOLD rect: height = innerHeight - top (bottom pinned to viewport)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportHeight(768);
    // top 80 + height 700 = bottom 780 > 768 → clamp to 768 - 80 = 688.
    const stop = observeChatArea(stubEl({ left: 150, top: 80, width: 900, height: 700, bottom: 780 }));
    try {
      const r = readChatAreaRect();
      expect(r?.height).toBe(688);
      expect((r?.top ?? 0) + (r?.height ?? 0)).toBe(768); // bottom == viewport
      expect(r?.width).toBe(900); // width untouched
      expect(r?.left).toBe(150);
    } finally { stop(); }
  });

  it('leaves a WITHIN-VIEWPORT rect unchanged (clamp is a no-op)', () => {
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = ResizeObserverStub;
    setViewportHeight(768);
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
});
