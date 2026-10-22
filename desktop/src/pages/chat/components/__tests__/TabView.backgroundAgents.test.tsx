/**
 * TabView background agents line (stores/backgroundAgentsStore).
 *
 * What is tested (real TabView, MessageBubble mocked): while the daemon reports a
 * background agent running for the tab's session, one line says how many — also when
 * no turn streams; it goes when they end; another session's agents do not show; a
 * click brings the first running agent's row into view; with no turn running it has a
 * Stop for those agents (onStopBackground), none while a turn streams.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, fireEvent, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import { backgroundAgentsStore } from '../../../../stores/backgroundAgentsStore';
import { afterActivation } from './scrollTestUtils';
import type { Message } from '../../../../types';

import { FOCUS_AGENT_EVENT } from '../../agentFocus';

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
  let focused: string[] = [];
  const onFocus = (event: Event) => focused.push((event as CustomEvent<{ toolUseId: string }>).detail.toolUseId);
  beforeEach(() => {
    focused = [];
    window.addEventListener(FOCUS_AGENT_EVENT, onFocus);
    messageStoreRegistry.clear();
    backgroundAgentsStore.reset();
    messageStoreRegistry.getOrCreate('A').replace([{ id: 'a1', role: 'assistant', timestamp: new Date().toISOString(), content: [] }]);
  });
  afterEach(() => {
    window.removeEventListener(FOCUS_AGENT_EVENT, onFocus);
    cleanup(); messageStoreRegistry.clear(); backgroundAgentsStore.reset();
  });

  it('shows while one runs (no turn streaming), goes when it ends; click scrolls to its row', async () => {
    const { queryByTestId } = render(<TabView {...props} />);
    await afterActivation();
    expect(queryByTestId('background-agents-line')).toBeNull();
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'ag1', description: 'Measure', status: 'running' }]); });
    const line = queryByTestId('background-agents-line')!;
    expect(line.textContent).toContain('1 background agent running');
    fireEvent.click(line.querySelector('button')!);
    expect(focused).toEqual(['ag1']);
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'ag1', status: 'completed' }]); });
    expect(queryByTestId('background-agents-line')).toBeNull();
  });

  it('does not paint an empty injected placeholder before its first background-finished block', async () => {
    const store = messageStoreRegistry.getOrCreate('A');
    store.replace([
      { id: 'old', role: 'assistant', timestamp: new Date().toISOString(), content: [{ type: 'text', text: 'old' }] },
      { id: 'injected', role: 'assistant', timestamp: new Date().toISOString(), content: [], metadata: { turn_source: 'injected' } },
    ] as Message[]);
    const { queryByTestId } = render(<TabView {...props} isStreaming />);
    await afterActivation();
    expect(queryByTestId('bubble-old')).not.toBeNull();
    expect(queryByTestId('bubble-injected')).toBeNull();

    act(() => { store.updateLast((message) => ({
      ...message,
      content: [{ type: 'background_finished', agents: [{ toolUseId: 'ag1', status: 'completed' }] }],
    })); });
    await afterActivation();
    expect(queryByTestId('bubble-injected')).not.toBeNull();
  });

  it('another session’s agents do not show', async () => {
    const { queryByTestId } = render(<TabView {...props} />);
    await afterActivation();
    act(() => { backgroundAgentsStore.apply('s2', [{ toolUseId: 'x', status: 'running' }]); });
    expect(queryByTestId('background-agents-line')).toBeNull();
  });

  it('with no turn running the line has a Stop for its agents; while a turn streams it does not', async () => {
    const onStop = vi.fn();
    const { queryByTestId, rerender } = render(<TabView {...props} onStopBackground={onStop} />);
    await afterActivation();
    act(() => { backgroundAgentsStore.apply('s1', [{ toolUseId: 'ag1', description: 'Measure', status: 'running' }]); });
    fireEvent.click(queryByTestId('background-agents-stop')!);
    expect(onStop).toHaveBeenCalledWith('A');
    expect(focused).toEqual([]); // Stop is its own button, it does not scroll
    rerender(<TabView {...props} onStopBackground={onStop} isStreaming />);
    expect(queryByTestId('background-agents-stop')).toBeNull();
  });
});
