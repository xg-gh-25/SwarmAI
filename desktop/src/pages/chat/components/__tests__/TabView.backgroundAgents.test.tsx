/**
 * TabView background agents line (stores/backgroundAgentsStore).
 *
 * What is tested (real TabView, MessageBubble mocked): while the daemon reports a
 * background agent running for the tab's session, one line says how many — also when
 * no turn streams; it goes when they end; another session's agents do not show; a
 * click brings the first running agent's row into view.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import { backgroundAgentsStore } from '../../../../stores/backgroundAgentsStore';
import { afterActivation } from './scrollTestUtils';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown, o?: { count?: number }) =>
    (typeof d === 'string' ? d.replace('{{count}}', String(o?.count ?? '')) : k) }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => (
    <div data-testid={`bubble-${message.id}`}><div data-agent-id="ag1">agent row</div></div>),
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
const props = {
  tabId: 'A', sessionId: 's1', messages: [], isActive: true, isStreaming: false,
  pendingQuestion: null, activeTabPendingQuestion: null, pendingPermissionRequestId: null,
  contextWarning: null, isWaitingForBusy: false, onCancelBusyWait: noop,
  hasMoreMessages: false, isLoadingOlderMessages: false, onLoadOlder: noop,
  onAnswerQuestion: noop, onPermissionDecision: noop, onEscalationSelect: noop,
  onCancelQueued: noop, onContinue: noop, onFocusClick: noop, onItemClick: noop,
  onRetryQueueTimeout: noop,
};

describe('TabView — background agents line', () => {
  let scrolled: Element[] = [];
  beforeEach(() => {
    scrolled = [];
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = function (this: Element) { scrolled.push(this); };
    messageStoreRegistry.clear();
    backgroundAgentsStore.reset();
    messageStoreRegistry.getOrCreate('A').replace([{ id: 'a1', role: 'assistant', timestamp: new Date().toISOString(), content: [] }]);
  });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); backgroundAgentsStore.reset(); });

  it('shows while one runs (no turn streaming), goes when it ends; click scrolls to its row', async () => {
    const { queryByTestId } = render(<TabView {...props} />);
    await afterActivation();
    expect(queryByTestId('background-agents-line')).toBeNull();
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'ag1', description: 'Measure', status: 'running' }]); });
    const line = queryByTestId('background-agents-line')!;
    expect(line.textContent).toContain('1 background agent running');
    fireEvent.click(line.querySelector('button')!);
    expect(scrolled.map((e) => e.getAttribute('data-agent-id'))).toEqual(['ag1']);
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'ag1', status: 'completed' }]); });
    expect(queryByTestId('background-agents-line')).toBeNull();
  });

  it('another session’s agents do not show', async () => {
    const { queryByTestId } = render(<TabView {...props} />);
    await afterActivation();
    act(() => { backgroundAgentsStore.apply('s2', [{ toolUseId: 'x', status: 'running' }]); });
    expect(queryByTestId('background-agents-line')).toBeNull();
  });
});
