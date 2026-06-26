// Feature: reconcile-gap structural fix (run_9db9f987) — single render source
/**
 * TabView single-render-source guard tests.
 *
 * Protects the structural fix for the #1 recurring bug (reconcile-gap): the
 * rendered message list must come from EXACTLY ONE source — this tab's own
 * MessageStore subscription. Before the fix, TabView.tsx:145 had a dual-source
 * selector `(storeMessages.length>0) ? storeMessages : messagesProp` that
 * rendered a stale `messagesProp` (a tabMapRef snapshot) whenever the store was
 * momentarily empty — the split-brain that truncated complete replies.
 *
 * These tests assert the store is the authority WITH an empty-store rescue:
 *  - When store has content, store wins regardless of messagesProp (even if
 *    messagesProp is longer/different — a stale prop must NOT override a
 *    populated store, the truncation bug the single-source change fixed).
 *  - When store is EMPTY and idle, the prop rescues the render (an empty store
 *    must never BLANK a completed answer the prop still holds — confirmed live:
 *    storeChars:0 / propChars:2788, switch did not recover). The rescue is gated
 *    on !isStreaming so live tokens always win and no stale prop flashes.
 *
 * Heavy children mocked (MessageBubble → MarkdownRenderer) for speed.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => (
    <div data-testid={`bubble-${message.id}`}>{message.id}</div>
  ),
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({
  EvolutionMessage: () => <div />,
  ChatErrorMessage: () => <div />,
}));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
function props(tabId: string, isActive: boolean, messagesProp: Message[] = []) {
  return {
    tabId,
    messages: messagesProp,
    isActive,
    isStreaming: false,
    pendingQuestion: null,
    activeTabPendingQuestion: null,
    pendingPermissionRequestId: null,
    contextWarning: null,
    isWaitingForBusy: false,
    hasMoreMessages: false,
    isLoadingOlderMessages: false,
    onLoadOlder: noop,
    onAnswerQuestion: noop,
    onPermissionDecision: noop,
    onEscalationSelect: noop,
    onCancelQueued: noop,
    onContinue: noop,
    onFocusClick: noop,
    onItemClick: noop,
    onRetryQueueTimeout: noop,
  };
}

function msg(id: string, text: string): Message {
  return { id, role: 'assistant', content: [{ type: 'text', text }], timestamp: new Date().toISOString() };
}

describe('TabView — single render source (reconcile-gap structural fix)', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('store wins over messagesProp when both are populated (no prop-fallback resurface)', () => {
    // Store has the REAL full reply; messagesProp is a STALE truncated snapshot.
    messageStoreRegistry.getOrCreate('A').replace([msg('full-1', 'complete reply')]);
    const staleProp = [msg('stale-1', 'truncated')];

    const { queryByTestId } = render(<TabView {...props('A', true, staleProp)} />);

    // Single-source: store content renders, the stale prop NEVER appears.
    expect(queryByTestId('bubble-full-1')).not.toBeNull();
    expect(queryByTestId('bubble-stale-1')).toBeNull();
  });

  it('empty store rescues from messagesProp when IDLE (cannot blank a completed answer)', () => {
    // Confirmed-live bug: the store transiently empties (reconcile timing /
    // placeholder reset / underflow) on the ACTIVE tab while the prop snapshot
    // still holds the completed answer (storeChars:0 / propChars:2788). Pure
    // store-only render then showed BLANK and switching tabs did not recover it.
    // Idle + empty store + prop-has-content → render the prop so the answer shows.
    messageStoreRegistry.getOrCreate('A').replace([]);
    const propSnapshot = [msg('prop-rescue', 'completed answer')];

    const { queryByTestId } = render(<TabView {...props('A', true, propSnapshot)} />);

    expect(queryByTestId('bubble-prop-rescue')).not.toBeNull(); // rescued, not blank
  });

  it('empty store does NOT rescue from prop while STREAMING (live tokens must win)', () => {
    // During streaming the store is the live source — its last assistant starts
    // empty and fills token-by-token. Falling back to the prop here would flash
    // the PREVIOUS turn's answer and hide live tokens. So the rescue is gated on
    // !isStreaming.
    messageStoreRegistry.getOrCreate('A').replace([]);
    const propSnapshot = [msg('prop-stale', 'previous turn')];

    const streamingProps = { ...props('A', true, propSnapshot), isStreaming: true };
    const { queryByTestId } = render(<TabView {...streamingProps} />);

    expect(queryByTestId('bubble-prop-stale')).toBeNull(); // no mid-stream prop flash
  });

  it('switch-back hydrates from the live store, not a reverse-flow replace', () => {
    // Tab A streamed content into its store. User switches to B, then back to A.
    // The store (module-level registry) survives the visibility toggle, so A's
    // content must come straight from the live subscription — no dependence on
    // ChatPage re-seeding the store from a tabState.messages snapshot.
    messageStoreRegistry.getOrCreate('A').replace([msg('a-real', 'streamed content')]);

    const { queryByTestId, rerender } = render(<TabView {...props('A', true, [])} />);
    expect(queryByTestId('bubble-a-real')).not.toBeNull();

    // Simulate switch away (inactive) then back (active) — prop stays empty the
    // whole time (ChatPage no longer reverse-seeds). Store content must persist.
    rerender(<TabView {...props('A', false, [])} />);
    rerender(<TabView {...props('A', true, [])} />);

    expect(queryByTestId('bubble-a-real')).not.toBeNull();
  });
});
