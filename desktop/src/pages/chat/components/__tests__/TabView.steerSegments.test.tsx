/**
 * TabView × steer segments — what TabView hands each MessageBubble
 * (chat-steer frontend §7): `continued` / `afterSteer` / `headerLive` from the
 * message list, live and reloaded alike. MessageBubble is mocked to expose them.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message, isStreaming, continued, afterSteer, headerLive }: {
    message: Message; isStreaming?: boolean; continued?: boolean; afterSteer?: boolean; headerLive?: boolean;
  }) => (
    <div data-testid={`bubble-${message.id}`} data-streaming={String(!!isStreaming)}
      data-flags={`${!!continued}/${!!afterSteer}/${!!headerLive}`} />
  ),
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
  onCancelQueued: noop, onContinue: noop, onRetryQueueTimeout: noop,
});
const ts = new Date().toISOString();
const msg = (id: string, role: 'user' | 'assistant', meta: Record<string, unknown> = {}): Message =>
  ({ id, role, timestamp: ts, content: [{ type: 'text', text: id }], metadata: { client_id: id, ...meta } });

describe('TabView — a steered turn', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

  it('T10g: the first segment is continued and keeps the pulse; the second streams without a header', () => {
    messageStoreRegistry.getOrCreate('A').replace([msg('q', 'user'), msg('A1', 'assistant'),
      msg('S', 'user', { steered: 'during' }), msg('A2', 'assistant')]);
    const { getByTestId } = render(<TabView {...props(true)} />);
    expect(getByTestId('bubble-A1').getAttribute('data-flags')).toBe('true/false/true');
    expect(getByTestId('bubble-A1').getAttribute('data-streaming')).toBe('false');
    expect(getByTestId('bubble-A2').getAttribute('data-flags')).toBe('false/true/false');
    expect(getByTestId('bubble-A2').getAttribute('data-streaming')).toBe('true');
  });
});
