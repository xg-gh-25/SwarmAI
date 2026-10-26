/**
 * One row, one box for every turn card list (design 2026-10-26-turn-cards-unified.md
 * §2, acceptance tests 1-2).
 *
 * What is tested (real AssistantMessageView, leaf contexts mocked):
 * - product rows, changed-file rows and commit rows are all the one ItemRow
 *   (`data-item-row`) inside the one ItemBox (`data-item-box`);
 * - a long file name is whole and never wraps or shrinks (product and changed file);
 *   only the grey folder truncates, with the full path on hover; the box scrolls
 *   sideways instead of cutting the name;
 * - the code-changes box: title "N files changed" with the turn's +/- inside the box,
 *   rows with added / deleted / binary and +N −M, then a COMMITS section: short sha,
 *   subject, "→ main" or "not on <dest>", "+N more" past the listed subjects.
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

describe('one ItemRow / ItemBox for every list (tests 1-2)', () => {
  it('products, changed files and commits are the same row in the same box', () => {
    renderView([tx('Done.'), CHANGES, DELIV]);
    const product = screen.getByTestId('deliverable');
    const change = screen.getAllByTestId('change-row')[0];
    const commit = screen.getAllByTestId('commit-row')[0];
    for (const row of [product, change, commit]) {
      expect(row.hasAttribute('data-item-row')).toBe(true);
      expect(row.closest('[data-item-box]')).toBeTruthy();
    }
  });

  it('a long file name is whole on one line; only the folder truncates', () => {
    renderView([tx('Done.'), CHANGES, DELIV]);
    const names = [
      within(screen.getByTestId('deliverable')).getByTestId('deliverable-name'),
      screen.getAllByTestId('change-row').map((r) => r.querySelector('[data-item-name]')!)
        .find((n) => n.textContent === LONG)!,
    ];
    for (const name of names) {
      expect(name.textContent).toBe(LONG);
      expect(name.className).toContain('whitespace-nowrap');
      expect(name.className).toContain('shrink-0');
      expect(name.className).not.toMatch(/truncate|break-all|break-words/);
      const row = name.closest('[data-item-row]')!;
      const folder = name.nextElementSibling as HTMLElement;
      expect(folder.className).toContain('truncate');
      expect(folder.getAttribute('title')).toMatch(new RegExp(`${LONG}$`));
      expect(row.closest('[data-item-box]')!.className).toContain('overflow-x-auto');
    }
  });

  it('the changes box: title and totals inside it, file tails, a COMMITS section', () => {
    renderView([tx('Done.'), CHANGES]);
    const card = screen.getByTestId('turn-changes');
    const title = within(card).getAllByTestId('item-box-title')[0];
    expect(title.textContent).toContain('4 files changed');
    expect(title.textContent).toContain('+11');
    expect(title.textContent).toContain('−1');
    const row = (p: string) => screen.getAllByTestId('change-row').find((r) => r.getAttribute('data-path') === p)!;
    expect(within(row('new.py')).getByText('added')).toBeTruthy();
    expect(within(row('logo.png')).getByText('binary')).toBeTruthy();
    expect(row('src/fix.py').textContent).toContain('+4');
    expect(within(card).getByTestId('commits-title').textContent).toContain('Commits · 3');
    const commits = within(card).getAllByTestId('commit-row');
    expect(commits.map((c) => c.textContent)).toEqual([
      expect.stringContaining('d1d1d1d'), expect.stringContaining('e2e2e2e')]);
    expect(commits[0].textContent).toContain('fix the thing');
    expect(commits[0].textContent).toContain('→ main');
    expect(within(card).getByText('+1 more')).toBeTruthy();
  });

  it('a commit no longer on its branch says so', () => {
    const off = { ...(CHANGES as unknown as Record<string, unknown>) };
    off.landed = [{ ...(CHANGES as unknown as { landed: Record<string, unknown>[] }).landed[0], state: 'off', dest: 'trunk', count: 2 }];
    renderView([tx('x'), off as unknown as ContentBlock]);
    expect(screen.getAllByTestId('commit-row')[0].textContent).toContain('not on trunk');
    expect(screen.queryByText(/more$/)).toBeNull();
  });
});
