/**
 * TabView × streaming bee — the seam that delivers the resting cue.
 *
 * What is tested (the chain the unit tests stop short of): TabView keys
 * useStreamingRest on its real render-source `messages`, so
 * - only the streaming (last) assistant bubble receives isStreaming/isResting;
 * - 30s of heartbeats (MessageStore.touch()) with no content → that bubble rests;
 * - a content write (store.updateLast) wakes it;
 * - a hidden tab (isActive=false) holds no resting state and comes back awake.
 *
 * Harness mirrors TabView.activityIndicator.test.tsx (MessageBubble mocked to
 * expose the props it receives as data attributes).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message, isStreaming, isResting }: { message: Message; isStreaming?: boolean; isResting?: boolean }) => (
    <div
      data-testid={`bubble-${message.id}`}
      data-streaming={String(!!isStreaming)}
      data-resting={String(!!isResting)}
    />
  ),
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
function props(tabId: string, isStreaming: boolean, extra: Record<string, unknown> = {}) {
  return {
    tabId, messages: [], isActive: true, isStreaming,
    pendingQuestion: null, activeTabPendingQuestion: null, pendingPermissionRequestId: null,
    contextWarning: null, isWaitingForBusy: false, onCancelBusyWait: noop,
    hasMoreMessages: false, isLoadingOlderMessages: false, onLoadOlder: noop,
    onAnswerQuestion: noop, onPermissionDecision: noop, onEscalationSelect: noop,
    onCancelQueued: noop, onContinue: noop, onFocusClick: noop, onItemClick: noop,
    onRetryQueueTimeout: noop, ...extra,
  };
}
const ts = new Date().toISOString();
const older: Message = { id: 'a0', role: 'assistant', timestamp: ts, content: [{ type: 'text', text: 'earlier answer' }] };
const user: Message = { id: 'u1', role: 'user', timestamp: ts, content: [{ type: 'text', text: 'go' }] };
const live: Message = { id: 'a1', role: 'assistant', timestamp: ts, content: [{ type: 'text', text: 'working' }] };

function seed() {
  const store = messageStoreRegistry.getOrCreate('A');
  store.replace([older, user, live]);
  store.startStreaming('a1');
  return store;
}
const attr = (el: HTMLElement | null, name: string) => el?.getAttribute(name);

describe('TabView — streaming bee resting cue', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
    vi.useRealTimers();
  });

  it('only the streaming bubble rests after 30s of heartbeats; content wakes it', () => {
    const store = seed();
    const { getByTestId } = render(<TabView {...props('A', true)} />);
    expect(attr(getByTestId('bubble-a1'), 'data-streaming')).toBe('true');
    expect(attr(getByTestId('bubble-a0'), 'data-streaming')).toBe('false');
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('false');

    // Heartbeats every 15s: touch() resets the watchdog but writes no content.
    act(() => { vi.advanceTimersByTime(15_000); store.touch(); });
    act(() => { vi.advanceTimersByTime(15_000); store.touch(); });
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('true');
    expect(attr(getByTestId('bubble-a0'), 'data-resting')).toBe('false');

    // New content → the store notifies → a new snapshot → awake.
    act(() => {
      store.updateLast((m) => ({ ...m, content: [{ type: 'text', text: 'working harder' }] } as Message));
      vi.advanceTimersByTime(100);
    });
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('false');
  });

  it('a hidden tab holds no resting state and comes back awake', () => {
    seed();
    const { getByTestId, rerender } = render(<TabView {...props('A', true)} />);
    rerender(<TabView {...props('A', true, { isActive: false })} />);
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('false');
    rerender(<TabView {...props('A', true, { isActive: true })} />);
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('false');
  });

  it('stream end: no bubble is streaming or resting', () => {
    seed();
    const { getByTestId, rerender } = render(<TabView {...props('A', true)} />);
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('true');
    rerender(<TabView {...props('A', false)} />);
    expect(attr(getByTestId('bubble-a1'), 'data-streaming')).toBe('false');
    expect(attr(getByTestId('bubble-a1'), 'data-resting')).toBe('false');
  });
});
