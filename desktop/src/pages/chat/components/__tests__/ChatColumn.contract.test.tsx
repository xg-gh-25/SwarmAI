/**
 * Chat column contract — the transcript, the composer and the Welcome screen
 * must all take their width and gutter from the shared chat-column utilities
 * defined in `src/index.css` (`chat-column`, `chat-gutter`,
 * `chat-scroll-gutter`), never from per-site literals.
 *
 * Why this exists: the three surfaces used to define their own widths
 * (transcript `max-w-[940px]`, composer uncapped, Welcome `max-w-3xl`) and
 * their own paddings, so their edges drifted apart. jsdom runs no CSS, so this
 * test pins the CLASS contract only (each surface opts into the shared rule and
 * carries none of the old literals that would compete with it). The geometry —
 * that the rule actually aligns the edges in WKWebView — is verified by the
 * layout harness run in the pipeline's TEST stage, not here.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => <div data-testid={`bubble-${message.id}`} />,
}));
vi.mock('../../../../components/chat', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ChatErrorMessage: () => <div />,
}));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));
vi.mock('../../../../services/attention', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  attentionService: { fetchAttention: vi.fn(() => new Promise(() => {})) },
}));

import { TabView } from '../TabView';
import { ChatInput } from '../ChatInput';
import { WelcomeScreen } from '../WelcomeScreen';

/** Old per-site literals that would compete with the shared rule. */
const RETIRED = ['max-w-[940px]', 'max-w-3xl', 'overflow-y-auto', 'pl-2', 'pr-4', 'px-4'];

function expectNoRetired(el: Element) {
  for (const cls of RETIRED) expect(el.classList.contains(cls), `${cls} on ${el.className}`).toBe(false);
}

const noop = () => {};
function tabProps(tabId: string) {
  return {
    tabId,
    messages: [] as Message[],
    isActive: true,
    isStreaming: false,
    pendingQuestion: null,
    activeTabPendingQuestion: null,
    pendingPermissionRequestId: null,
    contextWarning: null,
    isWaitingForBusy: false,
    onCancelBusyWait: noop,
    hasMoreMessages: false,
    isLoadingOlderMessages: false,
    onLoadOlder: noop,
    onAnswerQuestion: noop,
    onPermissionDecision: noop,
    onEscalationSelect: noop,
    onCancelQueued: noop,
    onContinue: noop,
    onRetryQueueTimeout: noop,
  };
}

function msg(id: string): Message {
  return { id, role: 'assistant', content: [{ type: 'text', text: id }], timestamp: new Date().toISOString() };
}

describe('chat column contract', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('transcript scroller uses the scroll gutter and its content uses the column', () => {
    messageStoreRegistry.getOrCreate('T').replace([msg('m1')]);
    const { container, getByTestId } = render(<TabView {...tabProps('T')} />);
    const scroller = container.firstChild as HTMLElement;
    expect(scroller.classList.contains('chat-scroll-gutter')).toBe(true);
    expectNoRetired(scroller);

    const wrapper = getByTestId('bubble-m1').closest('.chat-column');
    expect(wrapper).not.toBeNull();
    expect(scroller.contains(wrapper)).toBe(true);
    expectNoRetired(wrapper as Element);
  });

  it('composer uses the gutter and its card uses the column', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { container } = render(
      <QueryClientProvider client={client}>
        <ChatInput
          inputValue=""
          onInputChange={vi.fn()}
          onSend={vi.fn()}
          onStop={vi.fn()}
          isStreaming={false}
          selectedAgentId="agent-1"
          attachments={[]}
          onAddFiles={vi.fn()}
          onRemoveFile={vi.fn()}
          isProcessingFiles={false}
          fileError={null}
          canAddMore
          isExpanded={false}
          onExpandedChange={vi.fn()}
        />
      </QueryClientProvider>,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.classList.contains('chat-gutter')).toBe(true);
    expectNoRetired(root);
    const card = root.firstElementChild as HTMLElement;
    expect(card.classList.contains('chat-column')).toBe(true);
    // transition-all would animate the card's computed width on a Canvas toggle
    // while the transcript above snaps, so the two edges drift apart mid-transition.
    expect(card.classList.contains('transition-all')).toBe(false);
    // One background at a time: the idle card colour and the drag tint never coexist.
    expect(card.classList.contains('bg-[var(--color-card)]')).toBe(true);
    expect(card.classList.contains('bg-primary/5')).toBe(false);
    // ...and while a file is dragged over it, only the drag tint is present.
    fireEvent.dragOver(card, { dataTransfer: { types: ['Files'] } });
    expect(card.classList.contains('bg-primary/5')).toBe(true);
    expect(card.classList.contains('bg-[var(--color-card)]')).toBe(false);
  });

  it('Welcome screen uses the scroll gutter and the same column (not its own 768px)', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { container, getByTestId } = render(
      <QueryClientProvider client={client}><WelcomeScreen /></QueryClientProvider>,
    );
    const root = container.firstChild as HTMLElement;
    expect(root.classList.contains('chat-scroll-gutter')).toBe(true);
    expectNoRetired(root);

    const column = getByTestId('welcome-wordmark').closest('.chat-column');
    expect(column).not.toBeNull();
    expectNoRetired(column as Element);
    expect(container.querySelector('.max-w-3xl')).toBeNull();
  });
});
