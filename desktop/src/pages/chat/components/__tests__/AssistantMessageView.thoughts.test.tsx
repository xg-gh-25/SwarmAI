/**
 * Thoughts as narration + at most two disclosure levels (XG 2026-10-18).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - how the ask was read: the restatement written before any work, plain text at
 *   the top (no "Intent" label, never a thought or the run's requirement — chat
 *   display v8); with none, nothing is promoted out of the work
 * - there is no "no restatement" note
 * - a pipeline stage's body lays its groups out open: title + content, so a stage
 *   is one click from its strips (two levels at most)
 * - a sub-agent is one strip; its own steps are not a level of their own
 * - before the first stage row the plan's first stage holds the work, so when the
 *   row lands the stage folds 2s later in place instead of vanishing at once
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
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

/** Open every folded run of finished calls (an Explored row, a "×N" row), so each call is a row. */
const expandFolds = (root: HTMLElement = document.body) => {
  for (const f of [...within(root).queryAllByTestId('step-fold'), ...within(root).queryAllByTestId('step-explored')]) {
    fireEvent.click(within(f).getAllByRole('button')[0]);
  }
};
/** A finished thought is one "Thought" line: open it and return its text. */
const openThought = (root: HTMLElement = document.body) => {
  fireEvent.click(within(root).getByTestId('thought-toggle'));
  return within(root).getByTestId('thought-text').textContent;
};
const tu = (id: string, summary = 'Running: ls', name = 'Bash', category = 'bash', parent?: string): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category, ...(parent ? { parentToolUseId: parent } : {}) }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const RUN = { type: 'pipeline_run', runId: 'run_t', profile: 'full', stages: ['evaluate', 'think', 'plan'],
  requirement: 'Do X', at: 'T' } as ContentBlock;
const stage = (s: string, next: string | null): ContentBlock => ({
  type: 'pipeline_stage', runId: 'run_t', stage: s, status: 'completed', ordinal: 1, position: 1, total: 3,
  profile: 'full', next, nextOrdinal: 2, gate: null, summary: 'done', at: '2026-10-18T00:00:00Z',
}) as ContentBlock;
const msg = (content: ContentBlock[]): Message =>
  ({ id: 'm1', role: 'assistant', content, timestamp: '2026-10-18T00:00:00Z', isError: false }) as Message;
const viewOf = (c: ContentBlock[], streaming = false) => (
  <ToastProvider><AssistantMessageView message={msg(c)} isStreaming={streaming} /></ToastProvider>
);

describe('how the ask was read: the restatement, plain text (chat display v8)', () => {
  it('with no restatement, the opening thought stays in the work: no lead item, no label', () => {
    render(viewOf([th('Read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
    expect(screen.queryByTestId('turn-restatement')).toBeNull();
    expect(screen.queryByTestId('thought')).toBeNull(); // folded inside WORKED
  });

  it('a restatement written before the work is plain text at the top; the opening thought joins the work', () => {
    render(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.getByTestId('turn-restatement').textContent).toContain('I read it as');
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
    expect(screen.queryByTestId('thought')).toBeNull(); // folded inside WORKED
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(openThought()).toBe('plan');
  });

  it('streaming think → restate → first tool: the restatement leads, the thought is in the work below it', () => {
    const { rerender } = render(viewOf([th('plan')], true));
    // nothing but the bee: no "Working · Thinking" card at the top of the turn
    expect(screen.queryByTestId('work-group')).toBeNull();
    rerender(viewOf([th('plan'), tx('I read it as: fix the cache.')], true));
    expect(screen.queryByTestId('work-group')).toBeNull();
    const text = screen.getByText('I read it as: fix the cache.');
    rerender(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a')], true));
    expect(screen.getByText('I read it as: fix the cache.')).toBe(text); // the text never moved
    expect(screen.getByTestId('turn-restatement').textContent).toContain('I read it as');
    const group = screen.getByTestId('work-group');
    const thought = within(group).getByTestId('thought');
    // DOCUMENT_POSITION_FOLLOWING (4): the thought comes after the restatement
    expect(!!(screen.getByTestId('turn-restatement').compareDocumentPosition(thought) & 4)).toBe(true);
    rerender(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a'), tr('a'), tu('b', 'Running: make x')], true));
    expect(screen.getByTestId('work-group')).toBe(group); // same block: no remount
  });

  it('a direct run is not a pipeline: no pipeline block, the turn reads as a chat turn (XG 2026-10-19)', () => {
    const direct = { ...RUN, profile: 'direct' } as ContentBlock;
    const row = { ...(stage('plan', null) as object), profile: 'direct', status: 'in_progress' } as ContentBlock;
    render(viewOf([tx('I read it as: fix the cache.'), tu('a'), tr('a'), direct, row, tu('b'), tr('b'), tx('done')]));
    expect(screen.queryByTestId('pipeline-block')).toBeNull();
    expect(screen.queryAllByTestId('checklist-line')).toHaveLength(0);
    expect(screen.getByTestId('turn-restatement').textContent).toContain('I read it as');
    expect(screen.getByTestId('turn-result').textContent).toContain('done');
    expect(screen.getAllByTestId('work-group')).toHaveLength(1); // one WORKED block
  });

  it('a direct run moved up to a pipeline profile shows as a pipeline from then on', () => {
    const direct = { ...RUN, profile: 'direct' } as ContentBlock;
    const full = { ...(stage('evaluate', 'think') as object), profile: 'full' } as ContentBlock;
    render(viewOf([tx('I read it as: fix the cache.'), tu('a'), tr('a'), direct, full, tu('b'), tr('b'), tx('done')]));
    expect(screen.getByTestId('pipeline-block')).toBeTruthy();
  });

  it('a continuation pipeline turn (stage rows, no run block) has no label', () => {
    render(viewOf([stage('evaluate', 'think'), th(`The ask is to wire the cache. ${'x'.repeat(170)}`), tu('a'), tr('a'), tx('done')]));
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
  });

  it('a thought after the work is narration in its group', () => {
    render(viewOf([tx('I read it as: X.'), tu('a'), tr('a'), th('hm'), tx('done')]));
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(openThought()).toBe('hm');
  });

  it('a pipeline turn shows its restatement, never the run requirement', () => {
    render(viewOf([th('plan'), tx('Running the pipeline.'), tu('a'), tr('a'), RUN, tx('done')]));
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
    expect(screen.queryByText('Do X')).toBeNull();
    expect(screen.getByTestId('turn-restatement').textContent).toContain('Running the pipeline.');
  });

  it('there is no "started without restating" note, with or without an intent', () => {
    const { unmount } = render(viewOf([tu('a'), tr('a'), tu('b'), tr('b'), tu('c'), tr('c'), th('hm'), tx('done')]));
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
    unmount();
    render(viewOf([th('plan first'), tu('a'), tr('a'), tu('b'), tr('b'), tu('c'), tr('c'), tx('done')]));
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
  });

  it('a restatement before the work removes the note', () => {
    render(viewOf([th('plan first'), tx('I read it as: X.'), tu('a'), tr('a'), tu('b'), tr('b'), tu('c'), tr('c'), tx('done')]));
    expect(screen.queryByTestId('turn-no-alignment')).toBeNull();
  });
});

describe('two levels at most', () => {
  it('an opened stage lists its items directly: no WORKED header inside a stage', () => {
    render(viewOf([tu('a'), tr('a'), tu('a2', 'Running: id'), tr('a2'), th('between'), tu('b', 'Running: pwd'), tu('b2', 'Running: make'),
      tr('b'), stage('evaluate', null), tx('done')]));
    fireEvent.click(screen.getByTestId('pipeline-head')); // a finished pipeline block starts folded
    fireEvent.click(screen.getByTestId('pipeline-stage-row').querySelector('button')!);
    const body = screen.getByTestId('stage-body');
    expect(within(body).queryByTestId('group-toggle')).toBeNull();
    expandFolds(body);
    const rows = within(body).getAllByTestId('step-row');
    expect(rows).toHaveLength(4);
    // the thought between two calls is inside the row of the call after it
    fireEvent.click(within(rows[2]).getByTestId('row-toggle'));
    expect(openThought(rows[2])).toBe('between');
  });

  it('a sub-agent is one strip; its own steps are not rows of their own', () => {
    const content = [tu('ag', 'Agent: Review spec', 'Agent', 'agent'), tu('s1', 'Running: grep x', 'Bash', 'bash', 'ag'),
      tr('s1'), tr('ag'), tu('z'), tr('z'), tx('done')];
    render(viewOf(content));
    fireEvent.click(screen.getByTestId('group-toggle'));
    const rows = screen.getAllByTestId('step-row');
    expect(rows.map((r) => r.dataset.tool)).toEqual(['Agent', 'Bash']);
    expect(rows[0].textContent).toContain('1 step');
    expect(screen.queryByTitle('Show command')).toBeNull();
  });
});

describe('the first pipeline stage does not vanish when its row lands', () => {
  it('before the row the plan names the stage; after it the stage folds 2s later in place', () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(viewOf([tx('Go.'), tu('c'), tr('c'), RUN, tu('a')], true));
      const live = screen.getByTestId('pipeline-stage-live');
      expect(live.textContent).toContain('EVALUATE');
      rerender(viewOf([tx('Go.'), tu('c'), tr('c'), RUN, tu('a'), tr('a'), stage('evaluate', 'think'), tu('b')], true));
      const row = screen.getByTestId('pipeline-stage-row');
      expect(row.dataset.open).toBe('true');            // lingers, not gone at once
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
      expect(screen.getByTestId('pipeline-stage-row').dataset.open).toBe('false');
      expect(screen.getByTestId('pipeline-stage-live').textContent).toContain('THINK');
    } finally {
      vi.useRealTimers();
    }
  });
});
