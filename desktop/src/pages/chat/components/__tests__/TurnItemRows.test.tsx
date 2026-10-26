/**
 * One row, one box for every turn card list (design 2026-10-26-turn-cards-unified.md
 * §2, acceptance tests 1-2).
 *
 * What is tested (real AssistantMessageView, leaf contexts mocked):
 * - a product row is the one ItemRow (`data-item-row`) inside the one ItemBox
 *   (`data-item-box`), which is the content column's width (`w-full`);
 * - a long file name is whole and never wraps or shrinks; only the grey folder
 *   truncates, with the full path on hover; the box scrolls sideways instead of
 *   cutting the name;
 * - the code-changes card is NOT an ItemBox: it keeps its KiroCrew look (its own
 *   tests: ChangesCard.test / ChangesCard.landings.test).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../services/chat', async (orig) => ({
  ...(await orig<typeof import('../../../../services/chat')>()),
  chatService: { getTurnDiff: vi.fn() },
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const LONG = '2026-10-26-session-products-and-remote-actions-design.md';
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const at = { gitRoot: '/r', start: 'a'.repeat(40), end: 'b'.repeat(40) };
const f = (path: string, extra: Record<string, unknown> = {}) => ({
  path, absolutePath: `/r/${path}`, repo: 'r', status: 'modified', added: 2, deleted: 1,
  binary: false, diffable: true, ...at, ...extra,
});
const CHANGES = {
  type: 'turn_changes',
  files: [f(`docs/design/deep/er/${LONG}`), f('new.py', { status: 'added', added: 5, deleted: 0 }),
    f('logo.png', { binary: true, added: null, deleted: null, diffable: false })],
  landed: [{
    repo: 'r', gitRoot: '/r', dest: 'main', branch: 'session/abc-x', turn: 't'.repeat(32),
    onto: 'c'.repeat(40), new: 'd'.repeat(40), count: 3, state: 'on',
    subjects: [{ sha: 'd1d1d1d', subject: 'fix the thing' }, { sha: 'e2e2e2e', subject: 'add a test' }],
    files: [f('src/fix.py', { added: 4, deleted: 0 })],
  }],
} as unknown as ContentBlock;
const DELIV = { type: 'turn_deliverables', files: [
  { path: `notes/${LONG}`, absolutePath: `/ws/a/b/c/d/${LONG}`, kind: 'content', operation: 'written' }] } as ContentBlock;

function renderView(content: ContentBlock[]) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={false} /></ToastProvider>);
}

describe('one ItemRow / ItemBox for product rows (tests 1-2)', () => {
  it('a product row is the shared ItemRow in the full-width ItemBox', () => {
    renderView([tx('Done.'), DELIV]);
    const product = screen.getByTestId('deliverable');
    expect(product.hasAttribute('data-item-row')).toBe(true);
    const box = product.closest('[data-item-box]') as HTMLElement;
    expect(box.className).toContain('w-full');
  });

  it('a long file name is whole on one line; only the folder truncates', () => {
    renderView([tx('Done.'), DELIV]);
    const name = within(screen.getByTestId('deliverable')).getByTestId('deliverable-name');
    expect(name.textContent).toBe(LONG);
    expect(name.className).toContain('whitespace-nowrap');
    expect(name.className).toContain('shrink-0');
    expect(name.className).not.toMatch(/truncate|break-all|break-words/);
    const folder = name.nextElementSibling as HTMLElement;
    expect(folder.className).toContain('truncate');
    expect(folder.getAttribute('title')).toMatch(new RegExp(`${LONG}$`));
    expect(name.closest('[data-item-box]')!.className).toContain('overflow-x-auto');
  });

  it('the changes card is not an ItemBox: it keeps its KiroCrew look', () => {
    renderView([tx('Done.'), CHANGES, DELIV]);
    const card = screen.getByTestId('turn-changes');
    expect(card.closest('[data-item-box]')).toBeNull();
    expect(card.querySelector('[data-item-row]')).toBeNull();
    expect(card.className).toContain('rounded-lg');
  });
});
