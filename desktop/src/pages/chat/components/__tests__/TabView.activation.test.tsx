/**
 * TabView tab-ACTIVATION re-pin (run_a319239e — 4th recurrence of "switch to a
 * tab doesn't land at the bottom").
 *
 * THE BUG the 4 prior fixes missed: switching TO a tab landed mid-list, not at
 * the bottom, regardless of streaming/Canvas. All prior fixes hardened the
 * RESIZE/content-growth ResizeObserver re-pin; none added an ACTIVATION trigger.
 * A display:none→block visibility flip at an unchanged width is neither a resize
 * nor new content, so nothing re-pinned on switch — and for a tab the user once
 * scrolled up in, the stale userScrolledUpRef made the auto-scroll early-return.
 *
 * THE FIX: on isActive→true, if the user was at the bottom when they left
 * (wasAtBottomRef), reset the intent and requestAnimationFrame → scrollIntoView,
 * so the newly-laid-out (display:block) + async-throttled markdown is measured
 * on the next frame. Gated on wasAtBottomRef so a deliberate scroll-up survives.
 *
 * jsdom has no layout; we mock requestAnimationFrame to capture the callback and
 * spy scrollIntoView. Pure logic (shouldPinOnActivation) is tested DOM-free.
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

import { TabView, shouldPinOnActivation } from '../TabView';

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

describe('shouldPinOnActivation (pure)', () => {
  it('pins when the view was at bottom', () => { expect(shouldPinOnActivation(true)).toBe(true); });
  it('does NOT pin when the user had scrolled up', () => { expect(shouldPinOnActivation(false)).toBe(false); });
});

describe('TabView — activation re-pin (4th recurrence)', () => {
  it('scrolls to bottom on the frame AFTER a tab becomes active', () => {
    const store = messageStoreRegistry.getOrCreate('tab-a1');
    store.replace([msg('m1', 'hello')]);
    // Mount INACTIVE (display:none) — no activation scroll yet.
    const { rerender } = render(<TabView {...baseProps('tab-a1', false)} />);
    scrollIntoViewSpy.mockClear();
    rafCbs = [];
    // Activate the tab (the switch).
    act(() => { rerender(<TabView {...baseProps('tab-a1', true)} />); });
    // The fix scheduled a rAF (post-layout), not a synchronous scroll.
    expect(rafCbs.length).toBeGreaterThanOrEqual(1);
    // Flush the frame → it lands at the bottom.
    act(() => { rafCbs.forEach((cb) => cb(0)); });
    expect(scrollIntoViewSpy).toHaveBeenCalled();
  });
});
