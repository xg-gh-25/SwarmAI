/**
 * Test helpers for TabView scrolling (jsdom has no layout engine).
 *
 * - fakeScroller: gives a scroll container real-feeling metrics — a clamped
 *   scrollTop it records every write of, settable scrollHeight / clientHeight,
 *   `userScroll` (a wheel event — the user's input — then the move and its scroll
 *   event) and `engineScroll` (the move and scroll event with no user input, as
 *   when the engine clamps scrollTop after content shrinks).
 * - stubRect: a mutable getBoundingClientRect for one element.
 * - afterActivation: lets the post-activation frames pass, after which TabView
 *   handles scroll events again (a user scroll can change the mode).
 *
 * @exports fakeScroller, FakeScroller, stubRect, afterActivation
 */
import { act, fireEvent } from '@testing-library/react';

export interface FakeScroller {
  writes: number[];
  readonly top: number;
  readonly bottom: number;
  setHeights(scrollHeight: number, clientHeight?: number): void;
  userScroll(top: number): void;
  engineScroll(top: number): void;
}

export function fakeScroller(el: HTMLElement, scrollHeight = 2000, clientHeight = 500): FakeScroller {
  let top = 0;
  let sh = scrollHeight;
  let ch = clientHeight;
  const writes: number[] = [];
  const clamp = (v: number) => Math.max(0, Math.min(v, sh - ch));
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (v: number) => { top = clamp(v); writes.push(top); },
  });
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => sh });
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => ch });
  return {
    writes,
    get top() { return top; },
    get bottom() { return sh - ch; },
    setHeights(nextSh: number, nextCh = ch) { sh = nextSh; ch = nextCh; },
    userScroll(next: number) {
      act(() => {
        fireEvent.wheel(el, { deltaY: next < top ? -100 : 100 });
        top = clamp(next);
        fireEvent.scroll(el);
      });
    },
    engineScroll(next: number) {
      act(() => {
        top = clamp(next);
        fireEvent.scroll(el);
      });
    },
  };
}

/** A mutable rect for `el`: `r.top = …` moves it. */
export function stubRect(el: Element, init: { top: number; height: number }) {
  const r = { ...init };
  (el as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
    ({ top: r.top, bottom: r.top + r.height, height: r.height, left: 0, right: 0, width: 0, x: 0, y: r.top, toJSON: () => ({}) }) as DOMRect;
  return r;
}

export async function afterActivation() {
  await act(async () => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
}
