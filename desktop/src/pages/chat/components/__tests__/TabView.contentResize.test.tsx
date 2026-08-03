/**
 * TabView content-height re-pin (run_24f98f06, root cause B).
 *
 * THE BUG: switching to a streaming tab (and content growing DURING streaming)
 * failed to auto-land at the newest message. The resize re-pin's ResizeObserver
 * observed ONLY the scroll container (containerRef) — whose border-box height is
 * fixed (it's the viewport). When streaming content grows (markdown render is
 * 200ms-throttled in ContentBlockRenderer), the container's box doesn't change, so
 * the observer never fired and the view stranded above the newest message.
 *
 * THE FIX: wrap the message list in an inner content <div ref={contentRef}> and
 * ALSO observe THAT (its box grows with content). Re-pin stays gated on
 * wasAtBottomRef intent, so a user who genuinely scrolled up is not yanked back.
 *
 * jsdom has no layout + no real ResizeObserver, so we install a mock RO that
 * captures the callback and lets the test fire it, and spy scrollIntoView. The
 * assertion: the observer observes MORE THAN ONE element (container + content),
 * and firing it re-pins when at-bottom.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => <div data-testid={`bubble-${message.id}`}>{message.id}</div>,
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ EvolutionMessage: () => <div />, ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));
vi.mock('../../../hooks/useStreamingActivity', () => ({
  useStreamingActivity: () => ({ displayedActivity: null, elapsedSeconds: 0 }),
}));

import { TabView } from '../TabView';

// ── Mock ResizeObserver: capture the callback + record every observed element. ──
let roCallback: ResizeObserverCallback | null = null;
const observedEls: Element[] = [];
class MockResizeObserver {
  constructor(cb: ResizeObserverCallback) { roCallback = cb; }
  observe(el: Element) { observedEls.push(el); }
  unobserve() {}
  disconnect() {}
}

let scrollIntoViewSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  roCallback = null;
  observedEls.length = 0;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
  scrollIntoViewSpy = vi.fn();
  // jsdom lacks scrollIntoView; install a spy on the prototype.
  (Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollIntoViewSpy;
});
afterEach(() => {
  cleanup();
  messageStoreRegistry.clear?.();
});

function msg(id: string, text: string): Message {
  return { id, role: 'assistant', content: [{ type: 'text', text }] } as unknown as Message;
}

function baseProps(tabId: string) {
  const noop = () => {};
  return {
    tabId, isActive: true, messages: [msg('m1', 'hello')] as Message[],
    isStreaming: true, pendingQuestion: null, activeTabPendingQuestion: null,
    pendingPermissionRequestId: null, contextWarning: null, isWaitingForBusy: false,
    onCancelBusyWait: noop, hasMoreMessages: false, isLoadingOlderMessages: false,
    onLoadOlder: noop, onAnswerQuestion: noop, onPermissionDecision: noop,
    onEscalationSelect: noop, onCancelQueued: noop, onContinue: noop,
    onFocusClick: noop, onItemClick: noop, onRetryQueueTimeout: noop,
  };
}

describe('TabView — content-height re-pin (root cause B)', () => {
  it('observes BOTH the scroll container AND an inner content wrapper', () => {
    // Seed the store so the tab renders its message list (not the placeholder).
    const store = messageStoreRegistry.getOrCreate('tab-b1');
    store.replace([msg('m1', 'hello')]);
    render(<TabView {...baseProps('tab-b1')} />);
    // The bug: only the container was observed (length 1). The fix observes the
    // container + the content wrapper → at least 2 distinct elements.
    expect(observedEls.length).toBeGreaterThanOrEqual(2);
  });

  it('empty state still renders WelcomeScreen (space-y move did not break the empty layout)', () => {
    // messages=[] → the inner wrapper takes its flex-col/flex-1 branch so
    // WelcomeScreen (h-full) fills the viewport. Guards the Gate-1 B1 concern.
    const { getByTestId } = render(<TabView {...baseProps('tab-b3')} messages={[]} isStreaming={false} />);
    expect(getByTestId('welcome')).toBeTruthy();
  });

  it('re-pins (scrollIntoView) when the ResizeObserver fires while at bottom', () => {
    const store = messageStoreRegistry.getOrCreate('tab-b2');
    store.replace([msg('m1', 'hello')]);
    render(<TabView {...baseProps('tab-b2')} />);
    scrollIntoViewSpy.mockClear(); // ignore the mount-time auto-scroll
    // wasAtBottomRef defaults true (fresh view at bottom). Firing the observer
    // (content grew) must re-pin.
    expect(roCallback).toBeTruthy();
    act(() => { roCallback!([], {} as ResizeObserver); });
    expect(scrollIntoViewSpy).toHaveBeenCalled();
  });
});
