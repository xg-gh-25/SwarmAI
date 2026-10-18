/**
 * Thoughts as narration + at most two disclosure levels (XG 2026-10-18).
 *
 * What is tested (real AssistantMessageView + turnView, leaf contexts mocked):
 * - how the ask was read is said once: pipeline requirement > a restatement written
 *   before any work, else the opening thought; exactly one carries "Intent" 
 * - there is no "no restatement" note: the intent thought is the place to check
 *   how the ask was read (chat display v7.11)
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
  it('with nothing else, the opening thought is the Intent', () => {
    render(viewOf([th('Read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.getByTestId('thought').textContent).toContain('Intent');
    expect(screen.queryByTestId('turn-intent')).toBeNull();
  });

  it('a restatement written before the work is the Intent; the opening thought joins the work', () => {
    render(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a'), tr('a'), tx('done')]));
    expect(screen.getByTestId('turn-intent').textContent).toContain('I read it as');
    expect(screen.getAllByText('Intent')).toHaveLength(1);
    expect(screen.queryByTestId('thought')).toBeNull(); // folded inside WORKED
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('thought').textContent).toBe('plan'); // no second Intent label
    expect(screen.getAllByText('Intent')).toHaveLength(1);
  });

  it('streaming think → restate → first tool: the thought never moves or changes label', () => {
    const { rerender } = render(viewOf([th('plan')], true));
    rerender(viewOf([th('plan'), tx('I read it as: fix the cache.')], true));
    const pos = () => {
      const thought = screen.getByTestId('thought');
      const text = screen.getByText('I read it as: fix the cache.');
      // DOCUMENT_POSITION_FOLLOWING (4): the text comes after the thought
      return { follows: !!(thought.compareDocumentPosition(text) & 4), intent: thought.dataset.intent };
    };
    expect(pos()).toEqual({ follows: true, intent: 'false' });
    const before = screen.getByTestId('thought');
    rerender(viewOf([th('plan'), tx('I read it as: fix the cache.'), tu('a')], true));
    expect(pos()).toEqual({ follows: true, intent: 'false' });
    expect(screen.getByTestId('thought')).toBe(before); // same node: no remount
    expect(screen.getByTestId('turn-intent').textContent).toContain('I read it as');
  });

  it('a continuation pipeline turn (stage rows, no run block) has no thought Intent', () => {
    render(viewOf([stage('evaluate', 'think'), th(`The ask is to wire the cache. ${'x'.repeat(170)}`), tu('a'), tr('a'), tx('done')]));
    expect(screen.queryAllByText('Intent')).toHaveLength(0);
  });

  it('a thought after the work is narration in its group, with no label', () => {
    render(viewOf([tx('I read it as: X.'), tu('a'), tr('a'), th('hm'), tx('done')]));
    fireEvent.click(screen.getByTestId('group-toggle'));
    expect(screen.getByTestId('thought').textContent).toBe('hm');
    expect(screen.getByTestId('thought').dataset.intent).toBe('false');
  });

  it('a pipeline requirement wins over both', () => {
    render(viewOf([th('plan'), tx('Running the pipeline.'), tu('a'), tr('a'), RUN, tx('done')]));
    expect(screen.getAllByText('Intent')).toHaveLength(1);
    expect(screen.getByTestId('turn-run')).toBeTruthy();
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
    fireEvent.click(screen.getByTestId('pipeline-stage-row').querySelector('button')!);
    const body = screen.getByTestId('stage-body');
    expect(within(body).queryByTestId('group-toggle')).toBeNull();
    expect(within(body).getAllByTestId('step-row')).toHaveLength(4);
    expect(within(body).getByTestId('thought-text').textContent).toBe('between');
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
