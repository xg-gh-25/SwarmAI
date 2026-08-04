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
import { isAtBottom, BOTTOM_THRESHOLD, nextFollowState } from '../TabView';

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

describe('nextFollowState — the single default-follow signal (root fix)', () => {
  it('reflow-grow (scrollTop unchanged, scrollHeight up) does NOT flip follow off', () => {
    // Start following: scrollTop 400, client 600, height 1000, lastScrollTop 400.
    // Canvas opens → content reflows taller: height 1000→1400, scrollTop STAYS 400.
    // Raw metric would read "not at bottom" (400+600=1000 < 1400-100), BUT because
    // scrollTop never moved this is a reflow, not a user scroll → follow stays true.
    const next = nextFollowState({ follow: true, lastScrollTop: 400 }, 400, 600, 1400);
    expect(next.follow).toBe(true); // still following → the pin keeps firing
    expect(next.lastScrollTop).toBe(400);
  });

  it('a GENUINE user scroll up (scrollTop moved up) flips follow OFF', () => {
    const next = nextFollowState({ follow: true, lastScrollTop: 400 }, 100, 600, 1000);
    expect(next.follow).toBe(false); // user is reading history — never yank them back (AC2)
    expect(next.lastScrollTop).toBe(100);
  });

  it('scrolling back to the bottom restores follow=true', () => {
    const next = nextFollowState({ follow: false, lastScrollTop: 100 }, 800, 600, 1400);
    expect(next.follow).toBe(true);
    expect(next.lastScrollTop).toBe(800);
  });

  it('default state is follow=true (a fresh tab pins to bottom for streaming AND idle)', () => {
    // No genuine scroll yet (scrollTop unchanged from the initial 0) → follow unchanged.
    const next = nextFollowState({ follow: true, lastScrollTop: 0 }, 0, 600, 1400);
    expect(next.follow).toBe(true);
  });

  it('BOTTOM_THRESHOLD is the shared constant', () => {
    expect(BOTTOM_THRESHOLD).toBe(100);
  });
});
