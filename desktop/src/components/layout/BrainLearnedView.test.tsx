/**
 * BrainLearnedView — what a Brain learned (design docs/design/2026-10-30-brain-change-feed.md §3.7).
 *
 * Questions, one per test:
 * - are the entries grouped meta → cognitive → operational, then open items, with one
 *   verdict line, and an empty layer one short line?
 * - is entry text shown as text (a `<task_id>` stays visible, never becomes markup)?
 * - does the range ask the backend for that range?
 * - does Withdraw confirm inline, send the anchor and the reason (a default when empty),
 *   take the entry away and refresh the Brain's counts?
 * - does a 409 say the entry changed and keep it, instead of failing silently?
 * - does the bottom line count the person's withdrawals, with the list behind it?
 * - does an entry say what it adds to, and when its time is only the collected time
 *   or unknown?
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import i18n from '../../i18n';
import { ApiError } from '../../services/api';

const invalidate = vi.fn();
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: invalidate }) }));
const knowledge = vi.fn();
vi.mock('../../services/ddd', () => ({
  useBrainKnowledge: (name: string, since: string) => knowledge(name, since),
}));
const redact = vi.fn();
vi.mock('../../services/ingestion', () => ({ ingestionService: { redact: (...a: unknown[]) => redact(...a) } }));

import { BrainLearnedView, whenText } from './BrainLearnedView';

const now = new Date().toISOString();
const item = (title: string, o: Record<string, unknown> = {}) => ({
  anchor: `Projects/P/2-understanding/TECH.md#Arch#cand:${title}`, unit_id: title, doc: 'TECH.md',
  path: 'Projects/P/2-understanding/TECH.md', section: 'Arch', type: 'decision', gap_kind: null,
  title, text: `- [decision] **${title}** Key the dir by <task_id>.`, written_at: now, collected: false,
  source: 'S7', source_ref: 'sess-1', adds_to: null, ...o,
});
const DATA = {
  brain: 'P', since: 'week',
  layers: [
    { layer: 'meta', items: [] },
    { layer: 'cognitive', items: [item('Ship a thin bundle', { adds_to: 'Ship a full clone' })] },
    { layer: 'operational', items: [item('Old lesson', { type: 'pitfall', collected: true }), item('No time', { type: 'guideline', written_at: null })] },
  ],
  gaps: [item('Open thing', { type: 'gap', gap_kind: 'bug' })],
  counts: { week: 4, all: 10 },
  withdrawn: { count: 1, items: [{ path: 'Projects/P/2-understanding/TECH.md', doc: 'TECH.md', at: now, reason: 'docs need no full run',
    title: 'Always run the full suite', type: 'guideline', written_at: null, source: 'S7' }] },
};

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage('en');
  knowledge.mockReturnValue({ data: DATA, isLoading: false, error: null, refetch: vi.fn() });
  redact.mockResolvedValue({ revision: 'r2' });
});

const titles = (el: HTMLElement) => within(el).queryAllByTestId('learned-entry-title').map((e) => e.textContent);

describe('BrainLearnedView', () => {
  it('groups by layer, then open items, under one verdict line', () => {
    render(<BrainLearnedView name="P" />);
    expect(screen.getByTestId('learned-verdict').textContent).toBe('P learned 4 entries this week');
    expect(screen.getByTestId('learned-layer-meta-empty').textContent).toBe('Nothing new in this range');
    expect(titles(screen.getByTestId('learned-layer-cognitive'))).toEqual(['Ship a thin bundle']);
    expect(titles(screen.getByTestId('learned-layer-operational'))).toEqual(['Old lesson', 'No time']);
    expect(titles(screen.getByTestId('learned-gaps'))).toEqual(['Open thing']);
    const order = [...document.querySelectorAll('section[data-testid^="learned-"]')].map((e) => e.getAttribute('data-testid'));
    expect(order).toEqual(['learned-layer-meta', 'learned-layer-cognitive', 'learned-layer-operational', 'learned-gaps']);
  });

  it('shows entry text as text, without the title repeated', () => {
    render(<BrainLearnedView name="P" />);
    const body = within(screen.getByTestId('learned-layer-cognitive')).getByTestId('learned-entry-body');
    expect(body.textContent).toBe('Key the dir by <task_id>.');
    expect(body.querySelector('task_id')).toBeNull();
  });

  it('the range asks for that range', () => {
    render(<BrainLearnedView name="P" />);
    expect(knowledge).toHaveBeenLastCalledWith('P', 'week');
    fireEvent.click(screen.getByTestId('learned-range-month'));
    expect(knowledge).toHaveBeenLastCalledWith('P', 'month');
    expect(screen.getByTestId('learned-verdict').textContent).toBe('P learned 4 entries this month');
  });

  it('Withdraw confirms inline, sends the anchor and reason, and refreshes the counts', async () => {
    render(<BrainLearnedView name="P" />);
    const entry = screen.getAllByTestId('learned-entry')[0];
    fireEvent.click(within(entry).getByTestId('learned-withdraw'));
    expect(redact).not.toHaveBeenCalled();                       // the first click only asks
    const box = within(entry).getByTestId('learned-confirm-reason') as HTMLTextAreaElement;
    expect(box.tagName).toBe('TEXTAREA');
    expect(box.rows).toBeGreaterThanOrEqual(4);
    fireEvent.change(box, { target: { value: '  wrong: we keep full clones  ' } });
    fireEvent.click(within(entry).getByTestId('learned-confirm-yes'));
    await waitFor(() => expect(redact).toHaveBeenCalledWith(DATA.layers[1].items[0].anchor, 'wrong: we keep full clones'));
    await waitFor(() => expect(titles(screen.getByTestId('learned-layer-cognitive'))).toEqual([]));
    expect(screen.getByTestId('learned-note').textContent).toContain('Ship a thin bundle');
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0].queryKey));
    expect(keys).toEqual(expect.arrayContaining([JSON.stringify(['brain-knowledge', 'P']), JSON.stringify(['brains-with-pinned'])]));
  });

  it('an empty reason sends the default one', async () => {
    render(<BrainLearnedView name="P" />);
    const entry = screen.getAllByTestId('learned-entry')[0];
    fireEvent.click(within(entry).getByTestId('learned-withdraw'));
    fireEvent.click(within(entry).getByTestId('learned-confirm-yes'));
    await waitFor(() => expect(redact).toHaveBeenCalledWith(expect.any(String), 'Withdrawn on the Brain page'));
  });

  it('a 409 says the entry changed and keeps it', async () => {
    redact.mockRejectedValueOnce(new ApiError({ code: 'CONFLICT', message: 'Conflict', detail: 'changed' }, 409));
    render(<BrainLearnedView name="P" />);
    const entry = screen.getAllByTestId('learned-entry')[0];
    fireEvent.click(within(entry).getByTestId('learned-withdraw'));
    fireEvent.click(within(entry).getByTestId('learned-confirm-yes'));
    await waitFor(() => expect(within(entry).getByTestId('learned-confirm-error').textContent)
      .toBe('This entry just changed. Refresh and try again.'));
    expect(titles(screen.getByTestId('learned-layer-cognitive'))).toEqual(['Ship a thin bundle']);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('a refusal shows the server\'s reason, and the entry stays', async () => {
    redact.mockRejectedValueOnce(new ApiError({ code: 'VALIDATION_FAILED', message: 'Invalid request',
      detail: 'This entry cannot be withdrawn' }, 400));
    render(<BrainLearnedView name="P" />);
    const entry = screen.getAllByTestId('learned-entry')[0];
    fireEvent.click(within(entry).getByTestId('learned-withdraw'));
    fireEvent.click(within(entry).getByTestId('learned-confirm-yes'));
    await waitFor(() => expect(within(entry).getByTestId('learned-confirm-error').textContent)
      .toBe('Could not withdraw: This entry cannot be withdrawn'));
  });

  it('the bottom line counts the withdrawals, the list is behind it', () => {
    render(<BrainLearnedView name="P" />);
    expect(screen.getByTestId('learned-withdrawn-line').textContent).toBe('Withdrew 1 entry (of 11 written)');
    expect(screen.queryByTestId('learned-withdrawn-list')).toBeNull();
    fireEvent.click(screen.getByTestId('learned-withdrawn-toggle'));
    const list = screen.getByTestId('learned-withdrawn-list').textContent ?? '';
    expect(list).toContain('Always run the full suite');
    expect(list).toContain('reason: docs need no full run');
    expect(list).toContain('Written time unknown');
  });

  it('an entry says what it adds to, and when its time is collected or unknown', () => {
    render(<BrainLearnedView name="P" />);
    expect(screen.getByTestId('learned-entry-adds').textContent).toBe('Adds to an earlier entry: Ship a full clone');
    const when = screen.getAllByTestId('learned-entry-when').map((e) => e.textContent);
    expect(when[1]).toMatch(/^collected today /);
    expect(when[2]).toBe('time unknown');
  });

  it('nothing in the range says so, in the person\'s language', async () => {
    knowledge.mockReturnValue({ data: { ...DATA, layers: DATA.layers.map((l) => ({ ...l, items: [] })), gaps: [] },
      isLoading: false, error: null, refetch: vi.fn() });
    await i18n.changeLanguage('zh');
    render(<BrainLearnedView name="P" />);
    expect(screen.getByTestId('learned-verdict').textContent).toBe('P 本周没有学到新的');
    expect(screen.getByTestId('learned-empty')).toBeTruthy();
    await i18n.changeLanguage('en');
  });
});

describe('whenText', () => {
  const t = (k: string, o?: Record<string, unknown>) => `${k.split('.').pop()} ${o?.time}`;
  it('today, yesterday, then a date; nothing for no time', () => {
    const now = new Date(2026, 9, 31, 12, 0);
    expect(whenText(new Date(2026, 9, 31, 9, 5).toISOString(), 'en', now, t)).toBe('today 09:05');
    expect(whenText(new Date(2026, 9, 30, 23, 0).toISOString(), 'en', now, t)).toBe('yesterday 23:00');
    expect(whenText(new Date(2026, 9, 28, 8, 0).toISOString(), 'en', now, t)).toBe('Oct 28');
    expect(whenText(new Date(2025, 9, 28, 8, 0).toISOString(), 'en', now, t)).toBe('Oct 28, 2025');   // another year says so
    expect(whenText(null, 'en', now, t)).toBeNull();
    expect(whenText('not a date', 'en', now, t)).toBeNull();
  });
});
