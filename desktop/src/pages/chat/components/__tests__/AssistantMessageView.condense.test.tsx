/**
 * Finished turns condense (XG 2026-10-18 chat-experience round, item 2).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - once the turn is over, a note written right before a run of work becomes that
 *   work's title line, and is not rendered again in the stream
 * - opening the work shows the whole note above the steps when it has more to say
 *   than its headline; a short one-line note adds nothing on open
 * - never while streaming, never the alignment (restatement), never inside a
 *   pipeline stage's body, never across a thought
 * - noteHeadline / noteHasMore / headedProcesses as pure functions
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { HEADLINE_FULL_CHARS, deriveTurnView, headedProcesses, noteHasMore, noteHeadline, sectionUnits } from '../../turnView';
import type { ContentBlock, Message } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tu = (id: string, summary = 'Running: ls'): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary, category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const stage = (s: string): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_t', stage: s, status: 'completed', ordinal: 1, position: 1, total: 3,
  profile: 'full', next: null, nextOrdinal: null, gate: null, summary: 'done', at: '2026-10-18T00:00:00Z',
}) as ContentBlock;
const msg = (content: ContentBlock[]): Message =>
  ({ id: 'm1', role: 'assistant', content, timestamp: '2026-10-18T00:00:00Z', isError: false }) as Message;
const viewOf = (c: ContentBlock[], streaming = false) => (
  <ToastProvider><AssistantMessageView message={msg(c)} isStreaming={streaming} /></ToastProvider>
);
const blocks = (container: HTMLElement) => [...container.querySelectorAll('[data-testid="process-block"]')] as HTMLElement[];

const NOTE = 'Now check the store.\n\nThe second paragraph explains why.';
const TURN = [tx('I read it as: fix the cache.'), tu('a'), tr('a'), tx(NOTE), tu('b', 'Running: pwd'), tr('b'), tx('All done.')];

describe('a note heads the work it introduced (finished turns)', () => {
  it('the headline leads the folded line; the note is not repeated in the stream', () => {
    const { container } = render(viewOf(TURN));
    const b = blocks(container);
    expect(b).toHaveLength(2);
    expect(within(b[1]).getByTestId('process-title').textContent).toBe('Now check the store.');
    expect(screen.queryByText('The second paragraph explains why.')).toBeNull();
    expect(screen.getAllByText('Now check the store.')).toHaveLength(1);
  });

  it('opening the work shows the whole note above its steps', () => {
    const { container } = render(viewOf(TURN));
    const b = blocks(container)[1];
    expect(within(b).queryByTestId('process-title-full')).toBeNull();
    fireEvent.click(within(b).getByTestId('process-row'));
    const full = within(b).getByTestId('process-title-full');
    expect(full.textContent).toContain('The second paragraph explains why.');
    expect(within(b).getByTestId('step-row')).toBeTruthy();
    // above the steps
    const steps = within(b).getByTestId('process-steps');
    expect(full.compareDocumentPosition(steps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a short one-line note adds nothing when opened', () => {
    const { container } = render(viewOf([tx('ok:'), tu('a'), tr('a'), tx('Check pwd.'), tu('b', 'Running: pwd'), tr('b'), tx('done')]));
    const b = blocks(container)[1];
    fireEvent.click(within(b).getByTestId('process-row'));
    expect(within(b).queryByTestId('process-title-full')).toBeNull();
  });

  it('the alignment (restatement) never becomes a title', () => {
    const { container } = render(viewOf(TURN));
    expect(screen.getByTestId('turn-alignment').textContent).toContain('I read it as');
    expect(within(blocks(container)[0]).queryByTestId('process-title')).toBeNull();
  });

  it('never while streaming — not even over work that already finished', () => {
    render(viewOf([tx('I read it as: X.'), tu('a'), tr('a'), tx(NOTE), tu('b', 'Running: pwd')], true));
    expect(screen.queryByTestId('process-title')).toBeNull();
    expect(screen.getByText('The second paragraph explains why.')).toBeTruthy();
  });

  it('while streaming, a note before finished work still stays in the stream', () => {
    render(viewOf([tx('I read it as: X.'), tu('a'), tr('a'), tx(NOTE), tu('b', 'Running: pwd'), tr('b'),
      tx('next'), tu('c', 'Running: date')], true));
    expect(screen.queryByTestId('process-title')).toBeNull();
    expect(screen.getByText('The second paragraph explains why.')).toBeTruthy();
  });

  it('a thought between the note and the work keeps the note on its own', () => {
    render(viewOf([tx('I read it as: X.'), tu('a'), tr('a'), tx(NOTE), th('hm'), tu('b', 'Running: pwd'), tr('b'), tx('done')]));
    expect(screen.queryByTestId('process-title')).toBeNull();
    expect(screen.getByText('The second paragraph explains why.')).toBeTruthy();
  });

  it('inside a pipeline stage body the note stays a note', () => {
    render(viewOf([tu('a'), tr('a'), tx(NOTE), tu('b', 'Running: pwd'), tr('b'), stage('evaluate'), tx('done')]));
    fireEvent.click(screen.getByTestId('pipeline-stage-row').querySelector('button')!);
    const body = screen.getByTestId('stage-body');
    expect(within(body).queryByTestId('process-title')).toBeNull();
    expect(within(body).getByText('The second paragraph explains why.')).toBeTruthy();
  });
});

describe('segment title helpers', () => {
  it('noteHeadline: first non-empty line, markdown removed', () => {
    expect(noteHeadline('\n\n## **Fix** the `store` [here](http://x)\nmore')).toBe('Fix the store here');
    expect(noteHeadline('- 1. item')).toBe('item');
    expect(noteHeadline('Edit _turn_files and *this* one.')).toBe('Edit _turn_files and this one.');
    expect(noteHeadline('   ')).toBe('');
  });

  it('noteHasMore: more lines, or a headline too long for the folded line', () => {
    expect(noteHasMore('one line')).toBe(false);
    expect(noteHasMore('one\n\ntwo')).toBe(true);
    expect(noteHasMore('x'.repeat(HEADLINE_FULL_CHARS + 1))).toBe(true);
  });

  it('headedProcesses pairs only an adjacent finished top-level note → work', () => {
    const v = deriveTurnView([tx('align'), tu('a'), tr('a'), tx('  '), tu('b'), tr('b'), tx('n2'), tu('c'), tr('c'), tx('end')], false);
    const secs = sectionUnits(v.units, false, v.liveStage);
    const align = v.units.find((u) => u.kind === 'note');
    const map = headedProcesses(secs, align);
    const titled = [...map.entries()].map(([, n]) => (n.kind === 'note' ? n.text : ''));
    expect(titled).toContain('n2');
    expect(titled).not.toContain('align');
    expect(titled.every((t) => t.trim().length > 0)).toBe(true);
  });

  it('a note with no headline (only a code fence) is not a title', () => {
    const v = deriveTurnView([tx('align'), tu('a'), tr('a'), tx('```\nx\n```'), tu('b'), tr('b'), tx('end')], false);
    const secs = sectionUnits(v.units, false, v.liveStage);
    expect(headedProcesses(secs, v.units.find((u) => u.kind === 'note')).size).toBe(0);
  });
});
