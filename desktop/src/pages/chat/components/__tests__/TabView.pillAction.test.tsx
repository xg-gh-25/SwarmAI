/**
 * TabView "back to latest" pill (chat turn spec v6 §6).
 *
 * What is tested (real TabView, MessageBubble mocked): once the user has scrolled
 * away from the bottom while the turn streams, the pill says only "↓ Back to latest"
 * — never the running call, a step count, a timer or "Thinking" (what runs now is in
 * the turn itself; the one animated signal is the bee). The same with a call running
 * and between calls.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import { afterActivation } from './scrollTestUtils';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => <div data-testid={`bubble-${message.id}`}>{message.id}</div>,
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
const props = (isStreaming: boolean) => ({
  tabId: 'A', messages: [], isActive: true, isStreaming,
  pendingQuestion: null, activeTabPendingQuestion: null, pendingPermissionRequestId: null,
  contextWarning: null, isWaitingForBusy: false, onCancelBusyWait: noop,
  hasMoreMessages: false, isLoadingOlderMessages: false, onLoadOlder: noop,
  onAnswerQuestion: noop, onPermissionDecision: noop, onEscalationSelect: noop,
  onCancelQueued: noop, onContinue: noop, onFocusClick: noop, onItemClick: noop,
  onRetryQueueTimeout: noop,
});
const turn = (content: Message['content']): Message =>
  ({ id: 'a1', role: 'assistant', timestamp: new Date().toISOString(), content });

function scrollAway(container: HTMLElement) {
  const el = (container.querySelector('.chat-scroll-gutter') ?? container.querySelector('.overflow-y-auto')) as HTMLElement;
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 2000 });
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: 500 });
  el.scrollTop = 1500;
  fireEvent.wheel(el);
  fireEvent.scroll(el);
  el.scrollTop = 200;
  fireEvent.wheel(el);
  fireEvent.scroll(el);
}

describe('TabView pill — the live action, once', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

  const pillText = async (content: Message['content']) => {
    messageStoreRegistry.getOrCreate('A').replace([turn(content)]);
    const { container, getByTestId } = render(<TabView {...props(true)} />);
    await afterActivation();
    scrollAway(container);
    return getByTestId('streaming-pill').textContent ?? '';
  };

  it('a call running: only "↓ Back to latest"', async () => {
    const text = await pillText([
      { type: 'tool_use', id: 't1', name: 'Bash', summary: 'Running: ls', category: 'bash' },
      { type: 'tool_result', toolUseId: 't1', isError: false },
      { type: 'tool_use', id: 't2', name: 'Read', summary: 'Reading /r/src/x.ts', category: 'read' },
    ]);
    expect(text.trim()).toBe('↓ Back to latest');
  });

  it('between calls: still only "↓ Back to latest", never "Thinking"', async () => {
    const text = await pillText([
      { type: 'tool_use', id: 't1', name: 'Bash', summary: 'Running: ls', category: 'bash' },
      { type: 'tool_result', toolUseId: 't1', isError: false },
    ]);
    expect(text.trim()).toBe('↓ Back to latest');
    expect(text).not.toMatch(/Thinking|step \d|\d+s\b/);
  });
});
