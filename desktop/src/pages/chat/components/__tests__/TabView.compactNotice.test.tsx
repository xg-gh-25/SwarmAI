// Feature: a context compaction is shown with the turn it happened in
/**
 * TabView compaction render tests.
 *
 * A compaction (a `compact-` system message, live or from history) is a turn event:
 * TabView attaches it to its turn's last assistant message (compaction.attachCompactions),
 * which shows it as one quiet line (CompactChip) — while the turn streams a running one
 * is the bee's line instead. Only a compaction in a turn with no answer yet stays in
 * the list as the full divider (CompactNotice). A compaction never dims prior messages.
 *
 * MessageBubble is mocked to expose the compactions it is given.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message, compactions, isStreaming }: { message: Message; compactions?: Message[]; isStreaming?: boolean }) => (
    <div data-testid={`bubble-${message.id}`} data-compactions={(compactions ?? []).map((m) => m.id).join(',')}
      data-streaming={isStreaming ? 'yes' : 'no'}>{message.id}</div>
  ),
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({
  ChatErrorMessage: () => <div />,
}));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));
vi.mock('../../../../components/common/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <div data-testid="md">{content}</div>,
}));

import { fireEvent } from '@testing-library/react';
import { TabView } from '../TabView';
import { CompactChip } from '../CompactNotice';

const noop = () => {};
function props(tabId: string, messagesProp: Message[]) {
  return {
    tabId,
    messages: messagesProp,
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

const asst = (id: string, text: string): Message =>
  ({ id, role: 'assistant', content: [{ type: 'text', text }], timestamp: new Date().toISOString() });
const compact = (id: string, text: string): Message =>
  ({ id, role: 'system', content: [{ type: 'text', text }], timestamp: new Date().toISOString() });

const NOTICE = 'Conversation above has been summarized\nThe conversation reached the agent context limit.';

describe('TabView — compact-notice in-stream divider (AC5)', () => {
  beforeEach(() => {
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('a compaction in a turn that has an answer goes with that answer, not into the list', () => {
    // the bug: a mid-turn compaction was appended after the answer, which kept streaming
    // above it, so the divider sat at the bottom of the window for the whole turn
    const user = (id: string): Message => ({ id, role: 'user', content: [{ type: 'text', text: 'q' }], timestamp: new Date().toISOString() });
    messageStoreRegistry.getOrCreate('A').replace([
      user('u1'), asst('a1', 'working'), compact('compact-live-1', NOTICE),
      user('u2'), asst('a2', 'next turn'),
    ]);
    const view = render(<TabView {...props('A', [])} isStreaming />);
    expect(view.queryAllByTestId('compact-notice')).toHaveLength(0);
    expect(view.getByTestId('bubble-a1').getAttribute('data-compactions')).toBe('compact-live-1');
    expect(view.getByTestId('bubble-a2').getAttribute('data-compactions')).toBe('');
  });

  it('a compaction before the turn has any answer stays in the list, full text', () => {
    const user: Message = { id: 'u1', role: 'user', content: [{ type: 'text', text: 'q' }], timestamp: new Date().toISOString() };
    messageStoreRegistry.getOrCreate('A').replace([user, compact('compact-123', NOTICE)]);
    const { getByText, getAllByTestId } = render(<TabView {...props('A', [])} />);
    expect(getAllByTestId('compact-notice')).toHaveLength(1);
    expect(getByText(/Conversation above has been summarized/)).not.toBeNull();
    expect(getByText(/reached the agent context limit/)).not.toBeNull();
  });

  it('does NOT dim prior messages (compact- is excluded from resume-boundary dimming)', () => {
    // A compact- system message followed by a real assistant message. If compact-
    // were treated as a resume boundary, 'a1' (before it) would get opacity-50.
    messageStoreRegistry.getOrCreate('A').replace([
      asst('a1', 'before compaction'),
      compact('compact-123', NOTICE),
      asst('a2', 'after compaction'),
    ]);
    const { getByTestId } = render(<TabView {...props('A', [])} />);
    const before = getByTestId('bubble-a1');
    // Walk up: the dimming wrapper is the bubble's parent div (opacity-50 when dimmed).
    const wrapper = before.parentElement;
    expect(wrapper?.className ?? '').not.toContain('opacity-50');
  });

  const record = { id: 'r1', trigger: 'auto', pre_tokens: 750361, post_tokens: 12833, duration_ms: 54523, summary: '**Overview** XG wants X.' };
  const withMeta = (id: string, metadata: Message['metadata']): Message => ({ ...compact(id, NOTICE), metadata });

  it('a compaction of an answer the list does not paint yet stays a divider', () => {
    const user = (id: string, md?: Message['metadata']): Message => ({ id, role: 'user', content: [{ type: 'text', text: 'q' }], timestamp: new Date().toISOString(), metadata: md });
    const empty = (id: string, md?: Message['metadata']): Message => ({ id, role: 'assistant', content: [], timestamp: new Date().toISOString(), metadata: md });
    // a CLI-started turn, still empty (not painted) — its turn-start compaction
    messageStoreRegistry.getOrCreate('A').replace([
      user('u1'), asst('a1', 'x'),
      withMeta('compact-live-9', { compactPhase: 'running', turnOf: 'i1' }), empty('i1', { turn_source: 'injected' }),
    ]);
    const view = render(<TabView {...props('A', [])} isStreaming />);
    expect(view.getAllByTestId('compact-notice')).toHaveLength(1);
    expect(view.getByTestId('bubble-a1').getAttribute('data-compactions')).toBe('');
    view.unmount();
    // the empty segment before a read steer is not painted either: the compaction
    // goes with the segment after the steer
    messageStoreRegistry.getOrCreate('A').replace([
      user('u1'), withMeta('compact-live-8', { compactPhase: 'running' }), empty('s1a'),
      user('st', { steered: true }), asst('s1b', 'go on'),
    ]);
    const steered = render(<TabView {...props('A', [])} isStreaming />);
    expect(steered.queryAllByTestId('compact-notice')).toHaveLength(0);
    expect(steered.getByTestId('bubble-s1b').getAttribute('data-compactions')).toBe('compact-live-8');
  });

  it('the turn line: sizes, time and trigger, the summary one click away', () => {
    const view = render(<CompactChip message={withMeta('compact-r1', { compaction: record })} isStreaming={false} />);
    expect(view.getByText('Context compacted')).not.toBeNull();
    expect(view.getByText(/750K → 13K · 55s · auto/)).not.toBeNull();
    const toggle = view.getByRole('button', { name: /Conversation Summary/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(view.queryByTestId('compact-summary')).toBeNull();
    fireEvent.click(toggle);
    expect(view.queryByTestId('md')?.textContent).toBe('**Overview** XG wants X.');
  });

  it('running: no line while its turn streams (the bee says it); afterwards it did not finish', () => {
    const running = withMeta('compact-live-1', { compactPhase: 'running' });
    const live = render(<CompactChip message={running} isStreaming />);
    expect(live.queryByTestId('compact-chip')).toBeNull();
    live.unmount();
    const after = render(<CompactChip message={running} isStreaming={false} />);
    expect(after.getByText('Context compaction did not finish')).not.toBeNull();
  });

  it('an earlier turn\'s running compaction goes with that turn, which is not streaming', () => {
    messageStoreRegistry.getOrCreate('A').replace([
      asst('a1', 'x'),
      withMeta('compact-live-old', { compactPhase: 'running' }),
      { id: 'u2', role: 'user', content: [{ type: 'text', text: 'next' }], timestamp: new Date().toISOString() },
      asst('a2', 'streaming now'),
    ]);
    const view = render(<TabView {...props('A', [])} isStreaming />);
    expect(view.getByTestId('bubble-a1').getAttribute('data-compactions')).toBe('compact-live-old');
    expect(view.getByTestId('bubble-a1').getAttribute('data-streaming')).toBe('no');
  });

  it('no summary → no toggle; failed is the only colored state', () => {
    const done = render(<CompactChip message={withMeta('compact-r2', { compaction: { ...record, id: 'r2', summary: '' } })} isStreaming={false} />);
    expect(done.queryByRole('button', { name: /Conversation Summary/ })).toBeNull();
    expect(done.getByTestId('compact-chip').firstElementChild?.className).not.toContain('--color-warning');
    done.unmount();
    const failed = render(<CompactChip message={withMeta('compact-live-2', { compactPhase: 'failed' })} isStreaming />);
    expect(failed.getByText('Context compaction failed')).not.toBeNull();
    expect(failed.getByTestId('compact-chip').firstElementChild?.className).toContain('--color-warning');
  });

  it('a TRUE resume boundary STILL dims prior messages (control — exclusion is compact-specific)', () => {
    // Sanity: the dimming mechanism still works for a real resume boundary, proving
    // the AC5 fix narrowed the exclusion to compact-/refresh- and did not disable dimming.
    messageStoreRegistry.getOrCreate('A').replace([
      asst('a1', 'prior session'),
      compact('resume-boundary-9', 'Session resumed'), // non-refresh, non-compact → dims
      asst('a2', 'current session'),
    ]);
    const { getByTestId } = render(<TabView {...props('A', [])} />);
    const prior = getByTestId('bubble-a1');
    expect(prior.parentElement?.className ?? '').toContain('opacity-50');
  });
});
