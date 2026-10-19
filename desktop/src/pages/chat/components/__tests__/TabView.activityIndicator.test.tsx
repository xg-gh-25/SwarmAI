/**
 * TabView — ONE "what is the agent doing now" surface (AC7).
 *
 * The live step now lives in the assistant message's ProcessBlock. TabView must
 * therefore NOT render its old inline activity line ("Running: X — Running: …"),
 * and the sticky pill appears only once the user has scrolled away from the
 * bottom (follow=false) — the same single follow authority the pin path uses.
 * Reconnecting / resuming lines are connection state, not activity, and stay.
 *
 * Harness mirrors TabView.singleSource.test.tsx (MessageBubble mocked).
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
const toolTurn: Message = {
  id: 'a1', role: 'assistant', timestamp: new Date().toISOString(),
  content: [{ type: 'tool_use', id: 't1', name: 'Bash', summary: 'Running: cd /x; ls', category: 'bash' }],
};

function scrollContainer(container: HTMLElement): HTMLElement {
  const el = container.querySelector('.chat-scroll-gutter') as HTMLElement | null;
  if (!el) throw new Error('scroll container not found');
  return el;
}
function scrollTo(el: HTMLElement, top: number) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 2000 });
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: 500 });
  el.scrollTop = top;
  fireEvent.wheel(el);
  fireEvent.scroll(el);
}

describe('TabView — single current-activity surface', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

  it('streaming at the bottom: no inline activity line and no pill', () => {
    messageStoreRegistry.getOrCreate('A').replace([toolTurn]);
    const { container, queryByTestId } = render(<TabView {...props('A', true)} />);
    // The i18n mock returns the KEY for interpolated calls, so assert on keys:
    // neither the old inline line nor the old always-on pill may render.
    expect(container.textContent).not.toMatch(/chat\.(runningTool|thinking|processing)/);
    expect(container.textContent).not.toMatch(/Running:|Thinking\.\.\./);
    expect(queryByTestId('streaming-pill')).toBeNull();
  });

  it('after the user scrolls up the pill appears; scrolling back hides it', async () => {
    messageStoreRegistry.getOrCreate('A').replace([toolTurn]);
    const { container, queryByTestId } = render(<TabView {...props('A', true)} />);
    await afterActivation(); // scroll events in the activation frames are not the user's
    const el = scrollContainer(container);
    scrollTo(el, 1500); // at bottom (1500 + 500 = 2000)
    scrollTo(el, 200);  // genuine scroll up
    expect(queryByTestId('streaming-pill')).not.toBeNull();
    scrollTo(el, 1500); // back at the bottom
    expect(queryByTestId('streaming-pill')).toBeNull();
  });

  it('no pill when not streaming even if scrolled away', async () => {
    messageStoreRegistry.getOrCreate('A').replace([toolTurn]);
    const { container, queryByTestId } = render(<TabView {...props('A', false)} />);
    await afterActivation();
    const el = scrollContainer(container);
    scrollTo(el, 1500);
    scrollTo(el, 200);
    expect(queryByTestId('streaming-pill')).toBeNull();
  });

  it('reconnecting stays visible (connection state, not activity)', () => {
    messageStoreRegistry.getOrCreate('A').replace([toolTurn]);
    const { container } = render(<TabView {...props('A', true, { isReconnecting: true })} />);
    expect(container.textContent).toMatch(/Reconnecting/);
  });
});
