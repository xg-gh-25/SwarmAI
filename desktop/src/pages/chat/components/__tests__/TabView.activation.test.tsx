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
 * means streaming AND idle tabs pin to the bottom by default.
 *
 * 2026-10-18 (XG: "tab switch … 屏幕应该聚焦在 last message or working 区域"):
 * activation now ALWAYS lands at the bottom, even after a scroll-up (AC2 is
 * reversed — it had contradicted XG's 2026-08-03 choice A), and scroll events in
 * the activation frames are ignored (a re-shown display:none container may report
 * scrollTop 0). Unread-reply landing is in TabView.scrollFocus.test.tsx.
 *
 * jsdom has no layout; fakeScroller gives the container metrics and records
 * scrollTop writes, and real scroll events flip the mode. The pure decision
 * (nextFollowState) is also tested DOM-free.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';
import { fakeScroller } from './scrollTestUtils';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }) }));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => <div data-testid={`bubble-${message.id}`}>{message.id}</div>,
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));
vi.mock('../../../hooks/useStreamingActivity', () => ({
  useStreamingActivity: () => ({ displayedActivity: null, elapsedSeconds: 0 }),
}));

import { TabView, nextFollowState } from '../TabView';

// Mock RO (TabView subscribes one) so it doesn't throw; we don't fire it here.
class MockResizeObserver { observe() {} unobserve() {} disconnect() {} }

// Mock rAF: capture callbacks so the test can flush them deterministically.
let rafCbs: FrameRequestCallback[] = [];

beforeEach(() => {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
  rafCbs = [];
  (globalThis as unknown as { requestAnimationFrame: unknown }).requestAnimationFrame = (cb: FrameRequestCallback) => {
    rafCbs.push(cb);
    return rafCbs.length;
  };
  (globalThis as unknown as { cancelAnimationFrame: unknown }).cancelAnimationFrame = () => {};
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
  it('a reflow with no scroll keeps a scrolled-up tab reading (no move, no mode change)', () => {
    // No genuine scroll on activation (scrollTop unchanged) → follow stays false.
    expect(nextFollowState({ follow: false, lastScrollTop: 100 }, 100, 600, 1400).follow).toBe(false);
  });
});

describe('TabView — activation lands at the bottom (XG 2026-08-03 choice A, 2026-10-18)', () => {
  // Drain every pending frame, including frames scheduled by a frame.
  const flushRaf = () => act(() => {
    for (let i = 0; i < 10 && rafCbs.length; i += 1) { const cbs = rafCbs; rafCbs = []; cbs.forEach((cb) => cb(0)); }
  });
  const scrollerOf = (c: HTMLElement) => c.querySelector('.chat-scroll-gutter') as HTMLElement;

  it('pins to the bottom when a tab becomes active', () => {
    messageStoreRegistry.getOrCreate('tab-a1').replace([msg('m1', 'hello')]);
    const { container, rerender } = render(<TabView {...baseProps('tab-a1', false)} />);
    const s = fakeScroller(scrollerOf(container));
    act(() => { rerender(<TabView {...baseProps('tab-a1', true)} />); });
    expect(s.top).toBe(s.bottom);
  });

  it('AC1: a streaming tab that grew while inactive lands on the live bottom on return', () => {
    messageStoreRegistry.getOrCreate('tab-stream').replace([msg('m1', 'hi')]);
    const streamProps = { ...baseProps('tab-stream', true), isStreaming: true };
    const { container, rerender } = render(<TabView {...streamProps} />);
    const s = fakeScroller(scrollerOf(container));
    act(() => { rerender(<TabView {...streamProps} isActive={false} />); });
    s.setHeights(3000);
    act(() => { rerender(<TabView {...streamProps} isActive />); });
    expect(s.top).toBe(s.bottom);
  });

  it('AC2 (reversed): a scroll-up does not survive a switch — coming back lands at the bottom', () => {
    messageStoreRegistry.getOrCreate('tab-hist').replace([msg('m1', 'a'), msg('m2', 'b')]);
    const { container, rerender } = render(<TabView {...baseProps('tab-hist', true)} />);
    const s = fakeScroller(scrollerOf(container), 4000, 600);
    flushRaf();
    s.userScroll(3400);
    s.userScroll(0); // genuine scroll up → reading
    act(() => { rerender(<TabView {...baseProps('tab-hist', false)} />); });
    act(() => { rerender(<TabView {...baseProps('tab-hist', true)} />); });
    expect(s.top).toBe(s.bottom);
  });

  it('scroll events in the activation frames neither leave the bottom nor load older messages', () => {
    messageStoreRegistry.getOrCreate('tab-r').replace([msg('m1', 'a')]);
    const onLoadOlder = vi.fn();
    const { container, rerender } = render(<TabView {...baseProps('tab-r', false)} onLoadOlder={onLoadOlder} />);
    const s = fakeScroller(scrollerOf(container));
    act(() => { rerender(<TabView {...baseProps('tab-r', true)} onLoadOlder={onLoadOlder} />); });
    s.userScroll(0); // the engine resetting a re-shown display:none container
    expect(onLoadOlder).not.toHaveBeenCalled();
    s.setHeights(2400);
    act(() => { rerender(<TabView {...baseProps('tab-r', true)} onLoadOlder={onLoadOlder} messages={[msg('m1', 'a'), msg('m2', 'b')]} />); });
    expect(s.top).toBe(s.bottom); // still following
    flushRaf();
    s.userScroll(0); // after the frames: the user's scroll counts again
    expect(onLoadOlder).toHaveBeenCalled();
  });
});
