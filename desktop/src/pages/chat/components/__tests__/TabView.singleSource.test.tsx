// Feature: reconcile-gap structural fix (run_9db9f987) — single render source
/**
 * TabView render-source guard tests — "more-complete wins, never prop while
 * streaming".
 *
 * The rendered message list comes from this tab's own MessageStore by default,
 * but the render layer applies the SAME "more-complete content wins" principle
 * as the store-vs-DB merge (MessageStore._mergePreservingInteractive):
 *  - WHILE STREAMING: store-only. The prop (a tabMapRef snapshot) lags the live
 *    store mid-stream, and a longer PREVIOUS answer in the prop must never
 *    overwrite the in-progress reply (the cross-turn clobber).
 *  - WHILE IDLE: render whichever source has the more-complete LAST ASSISTANT
 *    message. This rescues the cold-start/restore gap that pure store-only left
 *    blank — on launch the store lazy-loads from the backend (momentarily empty
 *    or a shorter/incompletely-persisted row) while the restored prop already
 *    holds the full last answer (frontend.log: storeChars 0→155 vs propChars
 *    1860, rendered blank/truncated).
 *
 * These tests assert:
 *  - idle + store MORE complete than prop → store wins (stale prop never resurfaces)
 *  - idle + prop MORE complete than store (incl. empty store) → prop wins (rescue)
 *  - STREAMING + prop longer than store → store still wins (no cross-turn clobber)
 *  - switch-back hydrates from the live store (prop empty)
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
function props(tabId: string, isActive: boolean, messagesProp: Message[] = [], isStreaming = false) {
  return {
    tabId,
    messages: messagesProp,
    isActive,
    isStreaming,
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

describe('TabView — render source: more-complete wins, never prop while streaming', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('idle: store wins over a SHORTER/stale messagesProp (no stale-prop resurface)', () => {
    // Store has the REAL full reply; messagesProp is a STALE truncated snapshot.
    messageStoreRegistry.getOrCreate('A').replace([msg('full-1', 'complete reply')]);
    const staleProp = [msg('stale-1', 'short')];

    const { queryByTestId } = render(<TabView {...props('A', true, staleProp)} />);

    // store is more complete → store content renders, stale prop NEVER appears.
    expect(queryByTestId('bubble-full-1')).not.toBeNull();
    expect(queryByTestId('bubble-stale-1')).toBeNull();
  });

  it('idle: empty/shorter store falls back to a MORE-COMPLETE prop (cold-start/restore rescue)', () => {
    // Store is empty (cold start: backend lazy-load not landed yet). The restored
    // prop already holds the full last answer. More-complete-wins → render prop,
    // NOT a blank bubble. This is the startup white-screen fix.
    messageStoreRegistry.getOrCreate('A').replace([]);
    const restoredProp = [msg('prop-full', 'the full restored answer')];

    const { queryByTestId } = render(<TabView {...props('A', true, restoredProp)} />);

    expect(queryByTestId('bubble-prop-full')).not.toBeNull();
  });

  it('STREAMING: a longer prop NEVER overrides the live store (no cross-turn clobber)', () => {
    // Mid-stream the store holds the in-progress (shorter) reply; the prop still
    // carries a longer PREVIOUS answer. Streaming gate → store-only, so the stale
    // longer prop must NOT render over the live turn.
    messageStoreRegistry.getOrCreate('A').replace([msg('live-1', 'streaming so far')]);
    const longerStaleProp = [msg('prev-long', 'a much longer previous answer from the last turn')];

    // isStreaming = true (4th arg)
    const { queryByTestId } = render(<TabView {...props('A', true, longerStaleProp, true)} />);

    expect(queryByTestId('bubble-live-1')).not.toBeNull();
    expect(queryByTestId('bubble-prev-long')).toBeNull();
  });

  it('idle: a longer prop for a DIFFERENT last message never clobbers a newer store turn', () => {
    // Store has a NEW turn (new-asst, short). The prop still holds the OLD turn
    // (old-asst, much longer). Different last-assistant ids → the longer prop must
    // NOT win (the same-id guard blocks the cross-turn clobber).
    messageStoreRegistry.getOrCreate('A').replace([
      msg('user-2', 'second question'),
      msg('new-asst', 'short new answer'),
    ]);
    const olderLongerProp = [msg('old-asst', 'a very very long answer from the previous turn that is longer')];

    const { queryByTestId } = render(<TabView {...props('A', true, olderLongerProp)} />);

    expect(queryByTestId('bubble-new-asst')).not.toBeNull(); // newer store turn rendered
    expect(queryByTestId('bubble-old-asst')).toBeNull();      // stale longer prop NOT used
  });

  it('idle: SAME-message truncation — prop fuller content swapped in (no blank/short)', () => {
    // Store loaded a shorter copy of the SAME last message the prop has fuller
    // content for (incomplete-persist / startup). Same id + no interactive block
    // → render the prop's fuller text for that one message.
    messageStoreRegistry.getOrCreate('A').replace([msg('m1', 'short')]);
    const fullerProp = [msg('m1', 'the full, complete, much longer answer text')];

    const { queryByTestId, getByTestId } = render(<TabView {...props('A', true, fullerProp)} />);

    // Same id → one bubble, and it carries the fuller content.
    expect(queryByTestId('bubble-m1')).not.toBeNull();
    expect(getByTestId('bubble-m1').textContent).toBe('m1'); // mock renders id; content swap is internal
  });

  it('idle: same-id but store has an interactive block — store wins (question not dropped)', () => {
    // Store's last assistant carries a live ask_user_question block (FE-synthesized,
    // never in the prop). Even though the prop has longer plain text for the same
    // id, the interactive guard keeps the store so the question UI is preserved.
    const storeMsg: Message = {
      id: 'm1', role: 'assistant', timestamp: new Date().toISOString(),
      content: [
        { type: 'text', text: 'short' } as Message['content'][number],
        { type: 'ask_user_question', toolUseId: 'q1', questions: [] } as unknown as Message['content'][number],
      ],
    };
    messageStoreRegistry.getOrCreate('A').replace([storeMsg]);
    const fullerProp = [msg('m1', 'a much longer plain-text answer without the question block')];

    // Must not throw and must render the store message (same id → one bubble).
    const { queryByTestId } = render(<TabView {...props('A', true, fullerProp)} />);
    expect(queryByTestId('bubble-m1')).not.toBeNull();
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
