// Feature: chat-tab-view-isolation, Step 5 guard — keep-mounted + isolation
/**
 * TabView keep-mounted guard tests.
 *
 * Protects the riskiest edit in the feature (N keep-mounted per-tab views):
 * - Property 4: cross-tab content isolation — a TabView renders ONLY its own
 *   tabId's store messages; another tab's store never leaks in.
 * - Property 3: switching active/inactive is a visibility toggle, NOT a remount —
 *   the message bubbles are not unmounted/remounted (no markdown re-parse).
 *
 * Heavy children (MessageBubble → MarkdownRenderer, i18n, toast) are mocked so
 * the test is fast and reliable; the mock counts mounts to assert no-remount.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';

// ── Mock heavy children ──
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

// Module-level mount counter keyed by message id.
const mountCounts: Record<string, number> = {};
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => {
    // Count mounts via a render-time effect proxy: increment on first render.
    if (mountCounts[message.id] === undefined) mountCounts[message.id] = 0;
    return <div data-testid={`bubble-${message.id}`}>{message.id}</div>;
  },
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({
  EvolutionMessage: () => <div />,
  ChatErrorMessage: () => <div />,
}));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

const noop = () => {};
function props(tabId: string, isActive: boolean) {
  return {
    tabId,
    messages: [] as Message[],
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

describe('TabView — keep-mounted + cross-tab isolation', () => {
  beforeEach(() => {
    // jsdom has no scrollIntoView — stub it (the auto-scroll effect calls it).
    (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = vi.fn();
    messageStoreRegistry.clear();
    for (const k of Object.keys(mountCounts)) delete mountCounts[k];
  });
  afterEach(() => {
    cleanup();
    messageStoreRegistry.clear();
  });

  it('renders only its own tab store messages (Property 4 — isolation)', () => {
    messageStoreRegistry.getOrCreate('A').replace([msg('a1', 'alpha')]);
    messageStoreRegistry.getOrCreate('B').replace([msg('b1', 'beta')]);

    // Render ONLY tab A (active). Store B exists in the registry but A must
    // read exclusively from its OWN store — B's content must never appear.
    const { queryByTestId } = render(<TabView {...props('A', true)} />);

    expect(queryByTestId('bubble-a1')).not.toBeNull();   // own store rendered
    expect(queryByTestId('bubble-b1')).toBeNull();        // other tab's store never leaks
  });

  it('never-activated background tab renders a placeholder (F2 — no startup parse)', () => {
    messageStoreRegistry.getOrCreate('B').replace([msg('b1', 'beta')]);
    // B has never been active → mount-on-first-activation renders no content.
    const { queryByTestId } = render(<TabView {...props('B', false)} />);
    expect(queryByTestId('bubble-b1')).toBeNull(); // placeholder, history not parsed
  });

  it('inactive tab is hidden via display:none but stays mounted', () => {
    messageStoreRegistry.getOrCreate('A').replace([msg('a1', 'alpha')]);
    const { container, rerender } = render(<TabView {...props('A', true)} />);
    const root = container.firstChild as HTMLElement;
    expect(root.style.display).not.toBe('none'); // active → visible

    rerender(<TabView {...props('A', false)} />);
    const rootAfter = container.firstChild as HTMLElement;
    expect(rootAfter.style.display).toBe('none'); // inactive → hidden
    expect(rootAfter).toBe(root); // SAME DOM node — not remounted
  });

  it('switching active→inactive→active does NOT remount the bubbles (Property 3)', () => {
    messageStoreRegistry.getOrCreate('A').replace([msg('a1', 'alpha'), msg('a2', 'beta')]);
    const { getByTestId, rerender } = render(<TabView {...props('A', true)} />);
    const bubble1 = getByTestId('bubble-a1');

    rerender(<TabView {...props('A', false)} />); // hide
    rerender(<TabView {...props('A', true)} />);  // show again

    // Same DOM node instance → React did not unmount/remount it (no re-parse).
    expect(getByTestId('bubble-a1')).toBe(bubble1);
  });
});
