/**
 * TabView scroll re-pin intent logic (bugfix run_75691aa8).
 *
 * The Gate-2 HIGH: when Canvas opens, the chat content reflows TALLER
 * (scrollHeight grows) while scrollTop stays put — which naively reads as
 * "user scrolled up" and would SUPPRESS the resize re-pin, defeating the fix.
 * The fix distinguishes a reflow-grow (scrollTop unchanged) from a genuine user
 * scroll (scrollTop moved) and only updates the re-pin INTENT on the latter.
 *
 * jsdom has no layout, so we test the pure decision (isAtBottom) + simulate the
 * intent-update rule the handler applies.
 */
import { describe, it, expect } from 'vitest';
import { isAtBottom, BOTTOM_THRESHOLD, nextScrollIntent } from '../TabView';

describe('isAtBottom', () => {
  it('true within threshold of bottom', () => {
    // scrollTop + clientHeight = 950, scrollHeight = 1000, threshold 100 → at bottom
    expect(isAtBottom(350, 600, 1000)).toBe(true);
  });
  it('false when scrolled up beyond threshold', () => {
    expect(isAtBottom(100, 600, 1000)).toBe(false); // 700 < 900
  });
  it('exactly at bottom is true', () => {
    expect(isAtBottom(400, 600, 1000)).toBe(true);
  });
});

describe('nextScrollIntent — reflow must not defeat the re-pin (Gate-2 HIGH)', () => {
  it('reflow-grow (scrollTop unchanged, scrollHeight up) does NOT flip wasAtBottom off', () => {
    // Start at bottom: scrollTop 400, client 600, height 1000, lastScrollTop 400.
    // Canvas opens → content reflows taller: height 1000→1400, scrollTop STAYS 400.
    const next = nextScrollIntent({ wasAtBottom: true, lastScrollTop: 400 }, 400, 600, 1400);
    // raw metric reads "scrolled up" (400+600=1000 < 1400-100)...
    expect(next.userScrolledUp).toBe(true);
    // ...BUT the re-pin INTENT stays true because scrollTop never moved → re-pin fires.
    expect(next.wasAtBottom).toBe(true);
  });

  it('a GENUINE user scroll up (scrollTop moved) DOES flip wasAtBottom off', () => {
    const next = nextScrollIntent({ wasAtBottom: true, lastScrollTop: 400 }, 100, 600, 1000);
    expect(next.wasAtBottom).toBe(false); // re-pin suppressed — user is reading history
    expect(next.lastScrollTop).toBe(100);
  });

  it('after re-pin (scrollTop moves back to bottom) intent returns to true', () => {
    const next = nextScrollIntent({ wasAtBottom: false, lastScrollTop: 100 }, 800, 600, 1400);
    expect(next.wasAtBottom).toBe(true);
    expect(next.userScrolledUp).toBe(false);
  });

  it('BOTTOM_THRESHOLD is the shared constant', () => {
    expect(BOTTOM_THRESHOLD).toBe(100);
  });
});
