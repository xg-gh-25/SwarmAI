/**
 * TabView — the stream-end reconcile does not remount a turn (chat display v8).
 *
 * What is tested (real TabView, MessageBubble mocked): the reconcile swaps the live
 * bubble's client id for the DB row's id and keeps the client id in
 * `metadata.client_id`; the bubble is keyed by that client id, so its element — and
 * with it every 2s fold linger and open choice inside — survives. Two rows sharing a
 * client id (a cut turn) still get unique keys.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import { afterActivation } from './scrollTestUtils';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k) }) }));
vi.mock('../MessageBubble', () => ({ MessageBubble: ({ message, viewKey }: { message: Message; viewKey?: string }) => <div data-testid="bubble" data-view-key={viewKey}>{message.id}</div> }));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
const props = {
  tabId: 'A', messages: [], isActive: true, isStreaming: false,
  pendingQuestion: null, activeTabPendingQuestion: null, pendingPermissionRequestId: null,
  contextWarning: null, isWaitingForBusy: false, onCancelBusyWait: noop,
  hasMoreMessages: false, isLoadingOlderMessages: false, onLoadOlder: noop,
  onAnswerQuestion: noop, onPermissionDecision: noop, onEscalationSelect: noop,
  onCancelQueued: noop, onContinue: noop, onRetryQueueTimeout: noop,
};
const asst = (id: string, metadata?: Message['metadata']): Message =>
  ({ id, role: 'assistant', timestamp: '2026-10-19T10:00:00+08:00', content: [{ type: 'text', text: 'hi' }], ...(metadata ? { metadata } : {}) }) as Message;

describe('TabView — reconcile keeps the turn mounted', () => {
  beforeEach(() => { (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn(); messageStoreRegistry.clear(); });
  afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

  it('renaming the live bubble to its DB id (client id kept in metadata) keeps the same element', async () => {
    const store = messageStoreRegistry.getOrCreate('A');
    store.replace([asst('local-c1-asst')]);
    const { getByTestId } = render(<TabView {...props} />);
    await afterActivation();
    const before = getByTestId('bubble');
    act(() => { store.replace([asst('db-uuid-1', { client_id: 'local-c1-asst' })]); });
    await afterActivation();
    expect(getByTestId('bubble').textContent).toBe('db-uuid-1'); // it re-rendered…
    expect(getByTestId('bubble')).toBe(before); // …in the same element
  });

  it('a continued turn: the TAIL row (the one ending) keeps its element across the rename; each row has its own view key', async () => {
    const store = messageStoreRegistry.getOrCreate('A');
    store.replace([asst('db-1', { client_id: 'local-c1-asst' }), asst('local-c1-asst')]);
    const { getAllByTestId } = render(<TabView {...props} />);
    await afterActivation();
    const tail = getAllByTestId('bubble')[1];
    act(() => { store.replace([asst('db-1', { client_id: 'local-c1-asst' }), asst('db-2', { client_id: 'local-c1-asst' })]); });
    await afterActivation();
    expect(getAllByTestId('bubble')[1].textContent).toBe('db-2');
    expect(getAllByTestId('bubble')[1]).toBe(tail);
    const keys = getAllByTestId('bubble').map((b) => b.dataset.viewKey);
    expect(new Set(keys).size).toBe(2);
  });

  it('two rows with one client id still render both (unique keys)', async () => {
    messageStoreRegistry.getOrCreate('A').replace([asst('db-1', { client_id: 'local-c1-asst' }), asst('db-2', { client_id: 'local-c1-asst' })]);
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { getAllByTestId } = render(<TabView {...props} />);
    await afterActivation();
    expect(getAllByTestId('bubble').map((b) => b.textContent)).toEqual(['db-1', 'db-2']);
    expect(err.mock.calls.some((c) => String(c[0]).includes('same key'))).toBe(false);
    err.mockRestore();
  });
});
