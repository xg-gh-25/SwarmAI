/**
 * Strips merged by kind, rendered (chat turn spec v6 §3.1, revised 2026-10-25).
 *
 * Renders real turns through AssistantMessageView and checks the DOM:
 * - a finished phase of reads with thoughts between them shows one `Explored` strip
 *   with its counts; opened, it lists the calls and the thoughts in stream order;
 * - commands merge as `Ran`; an Edit stays its own `Edited` row between them;
 * - live: a thought being written and a running call stay their own rows; when the
 *   calls before them merge (after the fold delay) the running call's element is kept
 *   (no remount, spec §0.8).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown, opts?: { count?: number }) =>
    (typeof fallback === 'string' && opts?.count !== undefined ? fallback.replace('{{count}}', String(opts.count)) : fallback) }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const use = (id: string, name: string, object: string): ContentBlock =>
  ({ type: 'tool_use', id, name, summary: name === 'Bash' ? `Running: ${object}` : object, category: 'x',
    display: { tool: name, object, key: `${name}:${object}` } }) as ContentBlock;
const tr = (id: string, isError = false): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, content: 'ok', isError, truncated: false, outcome: 'ok' }) as ContentBlock;

function view(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant sessionId="s1" /></ToastProvider>;
}
const openPhase = () => fireEvent.click(screen.getAllByTestId('group-toggle').find((b) => /Worked/i.test(b.textContent ?? ''))!);
const toolNames = () => screen.getAllByTestId('strip-tool').map((e) => e.textContent);

describe('finished phase: merged by kind', () => {
  const turn = [
    tx('Looking.'),
    use('a', 'Read', '/p/a.ts'), tr('a'), th('PLAN-THOUGHT check b next'),
    use('b', 'Grep', 'steer'), tr('b'), th('second idea'),
    use('c', 'Bash', 'sed -n 1,20p b.ts'), tr('c'),
    use('d', 'Edit', '/p/a.ts'), tr('d'),
    use('e', 'Bash', 'npm test'), tr('e', true),
    use('f', 'Bash', 'make lint'), tr('f'),
    tx('Done.'),
  ];

  it('reads with thoughts between are one Explored strip; Edit its own; commands one Ran incl. the failed one', () => {
    render(view(turn));
    openPhase();
    expect(toolNames()).toEqual(['Explored', 'Edit', 'Ran']);
    expect(screen.queryByText(/PLAN-THOUGHT/)).toBeNull();
    const explored = screen.getAllByTestId('tool-strip')[0];
    const row = within(explored).getAllByRole('button')[0];
    expect(row.textContent).toMatch(/3 calls · 2 thoughts/);
    expect(row.textContent).not.toMatch(/a\.ts|steer|sed/);
  });

  it('a same-tool merge (Read ×2) shows no file names on the row; they are in its title', () => {
    render(view([tx('Looking.'), use('a', 'Read', '/p/a.ts'), tr('a'), use('b', 'Read', '/p/b.ts'), tr('b'), tx('Done.')]));
    openPhase();
    const strip = screen.getAllByTestId('tool-strip').find((s) => /Read ×2/.test(s.textContent ?? ''))!;
    const row = within(strip).getAllByRole('button')[0];
    expect(row.textContent).not.toMatch(/a\.ts|b\.ts/);
    expect(row.getAttribute('title')).toMatch(/a\.ts[\s\S]*b\.ts/);
  });

  it('opened, the Explored strip lists its calls and thoughts in stream order', () => {
    render(view(turn));
    openPhase();
    const explored = screen.getAllByTestId('tool-strip')[0];
    fireEvent.click(within(explored).getAllByRole('button')[0]);
    const detail = within(explored).getByTestId('strip-detail');
    const order = [...detail.querySelectorAll('[data-testid="strip-tool"], [data-testid="thought"]')]
      .map((e) => (e.getAttribute('data-testid') === 'thought' ? 'T' : e.textContent));
    expect(order).toEqual(['Read', 'T', 'Grep', 'T', 'Bash']);
    expect(detail.textContent).toMatch(/PLAN-THOUGHT/);
  });
});

describe('live phase', () => {
  it('a thought being written and a running call stay rows; the running call keeps its element when earlier calls merge', () => {
    vi.useFakeTimers();
    const base = [tx('Looking.'), use('a', 'Read', '/p/a.ts'), tr('a'), th('mid'), use('b', 'Read', '/p/b.ts')];
    const { rerender } = render(view(base, true)); // b still running: nothing settled after mount
    const content = [...base, tr('b'), use('c', 'Bash', 'npm test')];
    rerender(view(content, true));
    const running = () => screen.getAllByTestId('tool-strip').find((s) => s.getAttribute('data-status') === 'pending')!;
    const before = running();
    expect(toolNames()).toEqual(['Read', 'Read', 'Bash']); // b just finished: it waits FOLD_DELAY_MS
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    rerender(view(content, true));
    expect(toolNames()).toEqual(['Explored', 'Bash']);
    expect(running()).toBe(before);
    // a thought being written after finished calls stays its own row
    rerender(view([...base, tr('b'), th('writing now')], true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getAllByTestId('thought').some((e) => e.getAttribute('data-live') === 'true')).toBe(true);
  });
});
