/**
 * ChangesCard × AssistantMessageView — the turn's code changes (`turn_changes`).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - the header counts files (with the not-listed ones) and totals +/- once
 * - one row per file: folder dimmed + name, `new` / `deleted`, +a −d, the bar
 * - opening a diffable row fetches its diff on demand and shows the lines (file
 *   header dropped); a pruned diff says so; an oversized row says "File only" and
 *   the card says why; a binary row says "binary"
 * - a name opens the file in Canvas; a deleted file's name is not a button
 * - the code section comes only from turn_changes: no tool-derived "This turn
 *   changed" chips, with or without turn_changes (chat turn spec v6 §8)
 * - a file that is also a product shows in the deliverables section only
 * - it waits for the turn to end; changes and deliverables are two sections of ONE
 *   output card, changes first
 * - diffBar: share of adds/deletes, grey under five lines
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within, waitFor, act } from '@testing-library/react';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { AssistantMessageView } from '../AssistantMessageView';
import { diffBar } from '../ChangesCard';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { OPEN_FILE_EVENT } from '../../../../components/common/MarkdownRenderer';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
const getTurnDiff = vi.fn();
vi.mock('../../../../services/chat', () => ({ chatService: { getTurnDiff: (q: unknown) => getTurnDiff(q) } }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tu = (id: string, name: string, input: Record<string, unknown> = {}): ContentBlock =>
  ({ type: 'tool_use', id, name, input, summary: `${name}: x`, category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const edit = (id: string, p: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Edit', input: { file_path: p }, summary: `Writing to ${p}`, category: 'write' }) as ContentBlock;

const DIFF = 'diff --git a/src/a.ts b/src/a.ts\nindex 1..2 100644\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -1,2 +1,2 @@\n keep\n-old line\n+new line\n';
const at = { gitRoot: '/r', start: 'a'.repeat(40), end: 'b'.repeat(40) };
const CHANGES = {
  type: 'turn_changes', omitted: 1, files: [
    { path: 'src/a.ts', absolutePath: '/r/src/a.ts', repo: 'r', status: 'modified', added: 1, deleted: 1, binary: false, diffable: true, ...at },
    { path: 'src/big.ts', absolutePath: '/r/src/big.ts', repo: 'r', status: 'added', added: 9000, deleted: 0, binary: false, diffable: false, ...at },
    { path: 'old.py', absolutePath: '/r/old.py', repo: 'r', status: 'deleted', added: 0, deleted: 3, binary: false, diffable: true, ...at },
    { path: 'logo.png', absolutePath: '/r/logo.png', repo: 'r', status: 'modified', added: null, deleted: null, binary: true, diffable: false, ...at },
  ],
} as unknown as ContentBlock;
const DELIV = { type: 'turn_deliverables', files: [{ path: 'notes.md', absolutePath: '/ws/notes.md', kind: 'content', operation: 'written' }] } as ContentBlock;
const WORK: ContentBlock[] = [tx('I will fix it.'), edit('e1', '/r/src/a.ts'), tr('e1'), tu('b', 'Bash'), tr('b')];

function renderView(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} /></ToastProvider>);
}
const row = (path: string) => screen.getAllByTestId('change-row').find((r) => r.getAttribute('data-path') === path)!;

describe('ChangesCard', () => {
  it('header counts every file and totals the lines once', () => {
    renderView([...WORK, CHANGES, tx('Done.')]);
    const card = screen.getByTestId('turn-changes');
    expect(within(card).getByText('5 files changed')).toBeTruthy();
    const header = card.querySelector('header')!;
    expect(header.textContent).toContain('+9001');
    expect(header.textContent).toContain('−4');
    expect(within(card).getByText('1 more files are not listed.')).toBeTruthy();
  });

  it('rows: folder dimmed, status tags, counts, bar', () => {
    renderView([...WORK, CHANGES, tx('Done.')]);
    const a = row('src/a.ts');
    expect(a.textContent).toContain('src/');
    expect(a.textContent).toContain('a.ts');
    expect(a.textContent).toContain('+1');
    expect(a.textContent).toContain('−1');
    expect(within(row('src/big.ts')).getByText('new')).toBeTruthy();
    expect(within(row('old.py')).getByText('deleted')).toBeTruthy();
    expect(within(row('logo.png')).getByText('binary')).toBeTruthy();
  });

  it('opening a row fetches its diff; an oversized one says File only', async () => {
    getTurnDiff.mockResolvedValueOnce(DIFF);
    renderView([...WORK, CHANGES, tx('Done.')]);
    const a = row('src/a.ts');
    expect(within(a).queryByTestId('change-diff')).toBeNull();
    fireEvent.click(within(a).getByRole('button', { name: 'Show diff' }));
    expect(getTurnDiff).toHaveBeenCalledWith({ gitRoot: '/r', start: at.start, end: at.end, path: 'src/a.ts' });
    const diff = await within(a).findByTestId('change-diff');
    expect(diff.textContent).toContain('+new line');
    expect(diff.textContent).toContain('-old line');
    expect(diff.textContent).not.toContain('diff --git');
    const big = row('src/big.ts');
    expect(within(big).getByTestId('file-only')).toBeTruthy();
    expect(within(big).queryByRole('button', { name: 'Show diff' })).toBeNull();
    expect(screen.getByText(/1 more files are not listed/)).toBeTruthy();
  });

  it('a failed fetch can be retried by reopening the row', async () => {
    getTurnDiff.mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(DIFF);
    renderView([...WORK, CHANGES, tx('Done.')]);
    const toggle = within(row('src/a.ts')).getByRole('button', { name: 'Show diff' });
    fireEvent.click(toggle);
    await waitFor(() => expect(within(row('src/a.ts')).getByTestId('diff-error')).toBeTruthy());
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(await within(row('src/a.ts')).findByTestId('change-diff')).toBeTruthy();
  });

  it('a pruned diff says it is gone', async () => {
    getTurnDiff.mockResolvedValueOnce(null);
    renderView([...WORK, CHANGES, tx('Done.')]);
    fireEvent.click(within(row('old.py')).getByRole('button', { name: 'Show diff' }));
    await waitFor(() => expect(within(row('old.py')).getByTestId('diff-gone')).toBeTruthy());
  });

  it('a name opens the file; a deleted file is not a button', () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail.path);
    document.addEventListener(OPEN_FILE_EVENT, on);
    renderView([...WORK, CHANGES, tx('Done.')]);
    fireEvent.click(within(row('src/a.ts')).getByTitle('/r/src/a.ts'));
    document.removeEventListener(OPEN_FILE_EVENT, on);
    expect(seen).toEqual(['/r/src/a.ts']);
    expect(within(row('old.py')).getByTitle('/r/old.py').tagName).not.toBe('BUTTON');
  });

  it('no tool-derived "This turn changed" chips, with or without turn_changes', () => {
    renderView([...WORK, edit('e2', '/elsewhere/notes.txt'), tr('e2'), CHANGES, tx('Done.')]);
    expect(screen.queryByTestId('turn-files')).toBeNull();
    expect(screen.getAllByTestId('change-row').map((r) => r.getAttribute('data-path'))).not.toContain('/elsewhere/notes.txt');
  });

  it('a file that is also a product shows in the deliverables section only', () => {
    const product = { type: 'turn_deliverables', files: [{ path: 'src/a.ts', absolutePath: '/r/src/a.ts', kind: 'knowledge', operation: 'written' }] } as ContentBlock;
    renderView([...WORK, CHANGES, product, tx('Done.')]);
    expect(screen.getAllByTestId('change-row').map((r) => r.getAttribute('data-path'))).not.toContain('src/a.ts');
    expect(screen.getByTestId('output-deliverables').textContent).toContain('a.ts');
  });

  it('a turn without turn_changes has no code section and no chips', () => {
    renderView([...WORK, tx('Done.')]);
    expect(screen.queryByTestId('turn-changes')).toBeNull();
    expect(screen.queryByTestId('turn-files')).toBeNull();
  });

  it('waits for the turn to end, then is the first section of the one output card', () => {
    vi.useFakeTimers();
    const { container, rerender } = renderView([...WORK, CHANGES, DELIV], true);
    expect(screen.queryByTestId('turn-changes')).toBeNull();
    const message: Message = { id: 'm1', role: 'assistant', content: [...WORK, CHANGES, DELIV, tx('Done.')], timestamp: 'T', isError: false };
    rerender(<ToastProvider><AssistantMessageView message={message} isStreaming={false} /></ToastProvider>);
    // The ending lands with the WORKING fold, after the settle window — not on the spot.
    expect(screen.queryByTestId('output-card')).toBeNull();
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS); });
    const card = screen.getByTestId('output-card');
    expect(screen.getAllByTestId('output-card')).toHaveLength(1);
    const ids = Array.from(card.querySelectorAll('[data-testid]')).map((e) => e.getAttribute('data-testid'));
    expect(ids.indexOf('turn-changes')).toBeGreaterThanOrEqual(0);
    expect(ids.indexOf('turn-changes')).toBeLessThan(ids.indexOf('output-deliverables'));
    expect(container.contains(card)).toBe(true);
    vi.useRealTimers();
  });
});

describe('diffBar', () => {
  it('splits five squares by share and greys the rest under five lines', () => {
    expect(diffBar(10, 0)).toEqual(['add', 'add', 'add', 'add', 'add']);
    expect(diffBar(4, 6)).toEqual(['add', 'add', 'del', 'del', 'del']);
    expect(diffBar(1, 1)).toEqual(['add', 'del', 'none', 'none', 'none']);
    expect(diffBar(0, 0)).toEqual(['none', 'none', 'none', 'none', 'none']);
  });
});
