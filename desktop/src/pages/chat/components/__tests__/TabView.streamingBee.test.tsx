/**
 * TabView × assistant bubbles — what TabView hands each MessageBubble.
 *
 * What is tested: an errored assistant message and the queue-timeout retry reach
 * MessageBubble. (The streaming bee's wait hint lives in StreamingBee itself, see
 * StreamingBee.test.tsx: TabView no longer computes a resting cue.)
 *
 * Harness mirrors TabView.activityIndicator.test.tsx (MessageBubble mocked to
 * expose the props it receives as data attributes).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message, isStreaming, onRetryError }: {
    message: Message; isStreaming?: boolean; onRetryError?: () => void;
  }) => (
    <div
      data-testid={`bubble-${message.id}`}
      data-streaming={String(!!isStreaming)}
      data-error={String(!!message.isError)}
      data-error-retry={String(!!onRetryError)}
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
    onCancelQueued: noop, onContinue: noop, onRetryQueueTimeout: noop, ...extra,
  };
}
const ts = new Date().toISOString();

const attr = (el: HTMLElement | null, name: string) => el?.getAttribute(name);

describe('TabView — terminal error stays on the assistant message path', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('passes an errored assistant message and queue retry through MessageBubble', () => {
    const error: Message = {
      id: 'error-1',
      role: 'assistant',
      timestamp: ts,
      isError: true,
      errorCode: 'QUEUE_TIMEOUT',
      content: [{ type: 'text', text: 'partial' }, { type: 'text', text: 'queue timed out' }],
    };
    messageStoreRegistry.getOrCreate('A').replace([error]);
    const retry = vi.fn();
    const { getByTestId } = render(<TabView {...props('A', false, { onRetryQueueTimeout: retry })} />);
    const bubble = getByTestId('bubble-error-1');
    expect(attr(bubble, 'data-error')).toBe('true');
    expect(attr(bubble, 'data-error-retry')).toBe('true');
  });
});
