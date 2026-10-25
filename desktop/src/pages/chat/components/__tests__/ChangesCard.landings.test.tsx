/**
 * ChangesCard landings — the turn's landings on the "files changed" card
 * (design docs/design/2026-10-25-turn-result-card.md §5.1-§5.2, v9).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - a turn that only landed (no in-place file) still shows the card, titled
 *   "N commits landed", with `<branch> → <dest>`, its commit subjects, the
 *   on/not-on chip and its file rows (their diff opens through the turn diff);
 * - in-place rows and landings together: "N files changed · M commits landed";
 * - unknown / truncated / unverified are said, and a block with only those is kept;
 * - an old block (files only) renders as before;
 * - the history mapper turns `turn_id` / `landed_truncated` into camelCase.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
const getTurnDiff = vi.fn();
vi.mock('../../../../services/chat', async (orig) => ({
  ...(await orig<typeof import('../../../../services/chat')>()),
  chatService: { getTurnDiff: (q: unknown) => getTurnDiff(q) },
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const T = { gitRoot: '/r', start: 'a'.repeat(40), end: 'b'.repeat(40) };
const file = (path: string, added = 1, deleted = 0) => ({
  path, absolutePath: `/r/${path}`, repo: 'r', status: 'modified', added, deleted,
  binary: false, diffable: true, ...T,
});
const LANDING = {
  repo: 'r', gitRoot: '/r', dest: 'main', branch: 'session/abc-fix', turn: 't'.repeat(32),
  onto: 'c'.repeat(40), new: 'd'.repeat(40), count: 2, state: 'on',
  subjects: [{ sha: 'd1d1d1d', subject: 'fix the thing' }, { sha: 'e2e2e2e', subject: 'add a test' }],
  files: [file('src/fix.py', 4, 1)],
};
const block = (extra: Record<string, unknown>) =>
  ({ type: 'turn_changes', files: [], ...extra }) as unknown as ContentBlock;

function renderView(content: ContentBlock[]) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={false} /></ToastProvider>);
}

describe('ChangesCard landings', () => {
  it('a turn that only landed shows its landing', () => {
    getTurnDiff.mockResolvedValueOnce('@@ -1 +1 @@\n-a\n+b\n');
    renderView([tx('Landed.'), block({ landed: [LANDING] })]);
    const card = screen.getByTestId('turn-changes');
    expect(within(card).getByText('2 commits landed')).toBeTruthy();
    const land = within(card).getByTestId('turn-landing');
    expect(land.textContent).toContain('session/abc-fix');
    expect(land.textContent).toContain('main');
    expect(land.textContent).toContain('fix the thing');
    expect(within(land).getByTestId('landing-state').textContent).toContain('on main');
    const row = within(land).getAllByTestId('change-row')[0];
    expect(row.getAttribute('data-path')).toBe('src/fix.py');
    fireEvent.click(within(row).getByRole('button', { name: 'Show diff' }));
    expect(getTurnDiff).toHaveBeenCalledWith({ gitRoot: '/r', start: T.start, end: T.end, path: 'src/fix.py' });
  });

  it('a landing no longer on its branch says so', () => {
    renderView([tx('x'), block({ landed: [{ ...LANDING, state: 'off', count: 1, dest: 'trunk' }] })]);
    expect(screen.getByText('1 commit landed')).toBeTruthy();
    expect(screen.getByTestId('landing-state').textContent).toContain('not on trunk');
  });

  it('in-place rows and landings together', () => {
    renderView([tx('x'), block({ files: [file('a.py')], landed: [LANDING] })]);
    expect(screen.getByText('1 file changed · 2 commits landed')).toBeTruthy();
    expect(screen.getAllByTestId('change-row').map((r) => r.getAttribute('data-path')))
      .toEqual(['src/fix.py', 'a.py']);
  });

  it('what could not be read is said, and such a block is kept', () => {
    renderView([tx('x'), block({
      unknown: [{ repo: 'r', reason: 'no-reflog' }], landedTruncated: true, unverified: 2,
    })]);
    const card = screen.getByTestId('turn-changes');
    expect(card.textContent).toContain('Could not read landings in r (no-reflog)');
    expect(card.textContent).toContain('More landings may exist');
    expect(card.textContent).toContain('2 landing records could not be verified');
  });

  it('an old block renders as before and an empty one is not shown', () => {
    renderView([tx('x'), block({ files: [file('a.py')] })]);
    expect(screen.getByText('1 file changed')).toBeTruthy();
    expect(screen.queryByTestId('turn-landing')).toBeNull();
  });
});

describe('turn_changes mapping', () => {
  it('turn_id and landed_truncated become camelCase in both routes', async () => {
    const { toCamelCaseContentBlock } = await import('../../../../services/chat');
    const out = toCamelCaseContentBlock({ type: 'turn_changes', files: [], turn_id: 'x', landed_truncated: true });
    expect(out).toMatchObject({ turnId: 'x', landedTruncated: true });
    expect(out).not.toHaveProperty('turn_id');
  });
});
