/**
 * Thoughts as narration + at most two disclosure levels (XG 2026-10-18).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - how the ask was read is said once: pipeline requirement > a restatement written
 *   before any work > the intent thought; only that one carries "Understanding"
 * - the "no restatement" note is suppressed by an intent thought, not by a thought
 *   that comes after the work
 * - a pipeline stage's body lists its steps directly (no second fold inside it)
 * - a sub-agent's own steps have no detail level (two levels at most)
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

describe('how the ask was read is said once', () => {
  it('no restatement: the intent thought carries the label', () => {
    render(viewOf([th('Read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.getByTestId('thought').textContent).toContain('Understanding');
    expect(screen.queryByTestId('turn-alignment')).toBeNull();
  });

  it('a restatement written before the work wins; the thought is just a thought', () => {
    render(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.getByTestId('turn-alignment').textContent).toContain('I read it as');
    expect(screen.getByTestId('thought').textContent).not.toContain('Understanding');
    expect(screen.getByTestId('thought').textContent).toContain('Thought');
  });

  it('a pipeline requirement wins over both', () => {
    render(viewOf([th('plan'), tx('Running the pipeline.'), tu('a'), tr('a'), RUN, tx('done')]));
    expect(screen.getAllByText('Understanding')).toHaveLength(1);
    expect(screen.getByTestId('turn-run')).toBeTruthy();
  });

  it('a thought AFTER the work does not stand in for the restatement', () => {
    render(viewOf([tu('a'), tr('a'), tu('b'), tr('b'), tu('c'), tr('c'), th('hm'), tx('done')]));
    expect(screen.getByTestId('turn-no-alignment')).toBeTruthy();
    render(viewOf([th('plan first'), tu('a'), tr('a'), tu('b'), tr('b'), tu('c'), tr('c'), tx('done')]));
    expect(screen.getAllByTestId('turn-no-alignment')).toHaveLength(1);
  });
});

describe('two levels at most', () => {
  it('a stage body lists its steps directly, thoughts in place, no second fold', () => {
    render(viewOf([tu('a'), tr('a'), th('between'), tu('b', 'Running: pwd'), tr('b'), stage('evaluate', null), tx('done')]));
    fireEvent.click(screen.getByTestId('pipeline-stage-row').querySelector('button')!);
    const body = screen.getByTestId('stage-body');
    expect(within(body).getAllByTestId('step-row')).toHaveLength(2);
    expect(within(body).queryByTestId('process-row')).toBeNull();
    expect(within(body).getByTestId('thought-text').textContent).toBe('between');
  });

  it('a sub-agent step opens to its steps, and those have no detail of their own', () => {
    const content = [tu('ag', 'Agent: Review spec', 'Agent', 'agent'), tu('s1', 'Running: grep x', 'Bash', 'bash', 'ag'),
      tr('s1'), tr('ag'), tu('z'), tr('z'), tx('done')];
    render(viewOf(content));
    fireEvent.click(screen.getByTestId('process-row'));
    const detail = screen.getByTestId('agent-detail');
    fireEvent.click(detail.querySelector('button')!);
    const subs = screen.getByTestId('agent-steps');
    expect(within(subs).getAllByTestId('step-row')).toHaveLength(1);
    expect(within(subs).queryAllByTitle('Show command')).toHaveLength(0);
    expect(within(subs).getByTestId('step-head')).toBeTruthy();
    // a main step still has its one detail level
    expect(screen.getAllByTitle('Show command').length).toBeGreaterThan(0);
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
