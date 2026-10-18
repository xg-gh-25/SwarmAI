/**
 * TabView "back to latest" pill — says what runs now (chat display v7.1).
 *
 * What is tested (real TabView, MessageBubble mocked): once the user has scrolled
 * away from the bottom, the pill repeats the live card's title — "Reading x.ts ·
 * back to latest" — never a step count ("step 92") or a second timer that would
 * disagree with the card's (design-judgment Surface 6: one fact, once). With no
 * step running it says "Thinking · back to latest".
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
  fireEvent.scroll(el);
  el.scrollTop = 200;
  fireEvent.scroll(el);
}

describe('TabView pill — the live action, once', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

  it('names the running step, with no step count and no timer', async () => {
    messageStoreRegistry.getOrCreate('A').replace([turn([
      { type: 'tool_use', id: 't1', name: 'Bash', summary: 'Running: ls', category: 'bash' },
      { type: 'tool_result', toolUseId: 't1', isError: false },
      { type: 'tool_use', id: 't2', name: 'Read', summary: 'Reading /r/src/x.ts', category: 'read' },
    ])]);
    const { container, getByTestId } = render(<TabView {...props(true)} />);
    await afterActivation();
    scrollAway(container);
    const pill = getByTestId('streaming-pill');
    expect(pill.textContent).toContain('Read x.ts · back to latest');
    expect(pill.textContent).not.toMatch(/step \d/);
    expect(pill.textContent).not.toMatch(/\d+s\b/);
  });

  it('between steps it says Thinking', async () => {
    messageStoreRegistry.getOrCreate('A').replace([turn([
      { type: 'tool_use', id: 't1', name: 'Bash', summary: 'Running: ls', category: 'bash' },
      { type: 'tool_result', toolUseId: 't1', isError: false },
    ])]);
    const { container, getByTestId } = render(<TabView {...props(true)} />);
    await afterActivation();
    scrollAway(container);
    expect(getByTestId('streaming-pill').textContent).toContain('Thinking · back to latest');
  });
});
