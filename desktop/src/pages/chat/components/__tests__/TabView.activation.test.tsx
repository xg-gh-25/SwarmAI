/**
 * TabView tab-ACTIVATION re-pin — ROOT FIX (default-follow controller).
 *
 * History: "switch to a tab doesn't land at the bottom" recurred 4× (run_a319239e
 * /75691aa8/24f98f06 …). Each prior fix ADDED a trigger to a design that inferred
 * "should I be at bottom?" from scroll events via THREE scattered gates
 * (auto-scroll on userScrolledUpRef, a single-rAF activation re-pin on
 * wasAtBottomRef, a ResizeObserver on wasAtBottomRef). A background STREAMING tab
 * (display:none + isActive-gated no-render) fires NO scroll events, so those gates
 * froze/desynced and the tab stopped following the live stream on return
 * (symptom C, user-confirmed). The root fix REPLACES all three with ONE
 * authoritative signal — `followRef` (default true), flipped false ONLY by a
 * genuine user scroll-up (nextFollowState), consulted by a single continuous pin
 * path ([messages,isActive] effect + RO, both gated on followRef). Default-follow
 * means streaming AND idle tabs pin to the bottom by default; being a ref, the
 * signal persists across the display:none deactivation (AC2 survives a switch).
 *
 * jsdom has no layout; we spy scrollIntoView and drive real scroll events to flip
 * follow. The pure decision (nextFollowState) is also tested DOM-free.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }) }));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => <div data-testid={`bubble-${message.id}`}>{message.id}</div>,
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ EvolutionMessage: () => <div />, ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));
vi.mock('../../../hooks/useStreamingActivity', () => ({
  useStreamingActivity: () => ({ displayedActivity: null, elapsedSeconds: 0 }),
}));

import { TabView, nextFollowState } from '../TabView';

// Mock RO (TabView subscribes one) so it doesn't throw; we don't fire it here.
class MockResizeObserver { observe() {} unobserve() {} disconnect() {} }

// Mock rAF: capture callbacks so the test can flush them deterministically.
let rafCbs: FrameRequestCallback[] = [];
let scrollIntoViewSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
  rafCbs = [];
  (globalThis as unknown as { requestAnimationFrame: unknown }).requestAnimationFrame = (cb: FrameRequestCallback) => {
    rafCbs.push(cb);
    return rafCbs.length;
  };
  (globalThis as unknown as { cancelAnimationFrame: unknown }).cancelAnimationFrame = () => {};
  scrollIntoViewSpy = vi.fn();
  (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollIntoViewSpy;
});
afterEach(() => { cleanup(); messageStoreRegistry.clear?.(); });

function msg(id: string, text: string): Message {
  return { id, role: 'assistant', content: [{ type: 'text', text }] } as unknown as Message;
}
function baseProps(tabId: string, isActive: boolean) {
  const noop = () => {};
  return {
    tabId, isActive, messages: [msg('m1', 'hello')] as Message[],
    isStreaming: false, pendingQuestion: null, activeTabPendingQuestion: null,
    pendingPermissionRequestId: null, contextWarning: null, isWaitingForBusy: false,
    onCancelBusyWait: noop, hasMoreMessages: false, isLoadingOlderMessages: false,
    onLoadOlder: noop, onAnswerQuestion: noop, onPermissionDecision: noop,
    onEscalationSelect: noop, onCancelQueued: noop, onContinue: noop,
    onFocusClick: noop, onItemClick: noop, onRetryQueueTimeout: noop,
  };
}

describe('nextFollowState — activation follows the default (pure)', () => {
  // The activation re-pin is no longer a shouldPinOnActivation(wasAtBottom) gate;
  // it is the DEFAULT follow=true consulted by the single pin path. These assert
  // the follow signal that gates activation: default-follow pins; a deliberate
  // scroll-up (follow=false) is preserved and does NOT re-pin on activation.
  it('default follow=true is preserved across a no-op (activation with no user scroll)', () => {
    expect(nextFollowState({ follow: true, lastScrollTop: 0 }, 0, 600, 1400).follow).toBe(true);
  });
  it('a scrolled-up tab (follow=false) stays false — never yanked back on activation', () => {
    // No genuine scroll on activation (scrollTop unchanged) → follow stays false.
    expect(nextFollowState({ follow: false, lastScrollTop: 100 }, 100, 600, 1400).follow).toBe(false);
  });
});

describe('TabView — activation re-pin (default-follow controller, root fix)', () => {
  it('scrolls to bottom when a tab becomes active and follow is on (the default)', () => {
    const store = messageStoreRegistry.getOrCreate('tab-a1');
    store.replace([msg('m1', 'hello')]);
    // Mount INACTIVE (display:none) — no activation scroll yet.
    const { rerender } = render(<TabView {...baseProps('tab-a1', false)} />);
    scrollIntoViewSpy.mockClear();
    // Activate the tab (the switch). Default follow=true → the [messages,isActive]
    // pin path fires and lands at the bottom (no rAF indirection needed anymore).
    act(() => { rerender(<TabView {...baseProps('tab-a1', true)} />); });
    expect(scrollIntoViewSpy).toHaveBeenCalled();
  });

  // AC1 (symptom C) — a STREAMING tab that grew in the background, on return,
  // FOLLOWS the live bottom: new content while inactive + re-activation both pin.
  it('AC1: a streaming tab that grew while inactive re-pins to the live bottom on return', () => {
    const store = messageStoreRegistry.getOrCreate('tab-stream');
    store.replace([msg('m1', 'hi')]);
    // Active + streaming, at bottom (default follow=true).
    const streamProps = { ...baseProps('tab-stream', true), isStreaming: true };
    const { rerender } = render(<TabView {...streamProps} />);
    // Switch AWAY (inactive) — background streaming keeps appending to the store.
    act(() => { rerender(<TabView {...streamProps} isActive={false} messages={[msg('m1', 'hi'), msg('m2', 'grew-1')]} />); });
    scrollIntoViewSpy.mockClear();
    // Switch BACK — the grown content + activation must land at the live bottom.
    act(() => { rerender(<TabView {...streamProps} isActive messages={[msg('m1', 'hi'), msg('m2', 'grew-1'), msg('m3', 'grew-2')]} />); });
    expect(scrollIntoViewSpy).toHaveBeenCalled();
  });

  // AC2 (the one exception) — a deliberate scroll-up (follow→false) is NEVER
  // yanked back to the bottom, and that intent SURVIVES a tab switch.
  it('AC2: a deliberate scroll-up is not re-pinned, and survives a tab switch', () => {
    const store = messageStoreRegistry.getOrCreate('tab-hist');
    store.replace([msg('m1', 'a'), msg('m2', 'b')]);
    const { container, rerender } = render(<TabView {...baseProps('tab-hist', true)} />);
    // The scroll container is the div carrying onScroll=handleScroll (the
    // overflow-y-auto body). Simulate a GENUINE user scroll-up on it: scrollTop
    // moved up (from the initial 0 baseline it must actually differ; start the
    // baseline high then move up) → handleScroll → nextFollowState flips follow=false.
    const scroller = container.querySelector('.overflow-y-auto') as HTMLElement;
    expect(scroller).toBeTruthy();
    // First a genuine scroll to establish lastScrollTop at the bottom, then up.
    const setMetrics = (top: number) => {
      Object.defineProperty(scroller, 'scrollTop', { value: top, configurable: true });
      Object.defineProperty(scroller, 'clientHeight', { value: 600, configurable: true });
      Object.defineProperty(scroller, 'scrollHeight', { value: 4000, configurable: true });
    };
    setMetrics(3400); // at bottom (3400+600=4000)
    act(() => { scroller.dispatchEvent(new Event('scroll')); });
    setMetrics(0);    // user scrolls UP to the top → genuine move → follow=false
    act(() => { scroller.dispatchEvent(new Event('scroll')); });
    scrollIntoViewSpy.mockClear();
    // New content arrives + a switch away and back — follow=false must persist,
    // so NONE of these re-pin the history-reading user.
    act(() => { rerender(<TabView {...baseProps('tab-hist', true)} messages={[msg('m1', 'a'), msg('m2', 'b'), msg('m3', 'c')]} />); });
    act(() => { rerender(<TabView {...baseProps('tab-hist', false)} messages={[msg('m1', 'a'), msg('m2', 'b'), msg('m3', 'c')]} />); });
    act(() => { rerender(<TabView {...baseProps('tab-hist', true)} messages={[msg('m1', 'a'), msg('m2', 'b'), msg('m3', 'c')]} />); });
    expect(scrollIntoViewSpy).not.toHaveBeenCalled();
  });
});
