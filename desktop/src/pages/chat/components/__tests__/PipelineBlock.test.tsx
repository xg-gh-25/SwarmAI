/**
 * PipelineBlock — a pipeline turn is ONE special WORKED block (chat display v8,
 * XG 2026-10-19).
 *
 * What is tested (real AssistantMessageView + PipelineBlock + PipelineStageRow):
 * - the header: "PIPELINE · BUILD · 5/11 · …" while live; once folded
 *   "PIPELINE · 11/11 · … · Gate 2 3/3 fixed"
 * - the checklist lists every planned stage and gate from the first frame, in plan
 *   order, pending lines dim with no body, the running line open with its work
 * - no line is ever inserted: the line keys only append across frames
 * - the block folds FOLD_DELAY_MS after the answer starts (live → done), never at
 *   once; a history mount starts folded and opens on click
 * - durations under 5s are not shown
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';
import { FULL, runBlock, row, tu, tr, tx, stageRun } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }), // tf falls back to its English text
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

afterEach(() => { vi.useRealTimers(); });

const msg = (content: ContentBlock[]): Message => ({ id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:59:00+08:00', isError: false });
const view = (content: ContentBlock[], isStreaming: boolean) => (
  <ToastProvider><AssistantMessageView message={msg(content)} isStreaming={isStreaming} isLastAssistant /></ToastProvider>
);
const lines = () => screen.getAllByTestId('checklist-line');
const statuses = () => lines().map((l) => `${l.dataset.stage}:${l.dataset.status}`);

const MID_BUILD: ContentBlock[] = [
  runBlock(),
  ...stageRun('evaluate', 1), row('gate0', 'completed'),
  ...stageRun('think', 1), ...stageRun('plan', 1),
  row('build', 'in_progress'), tu('b1'), tr('b1'), row('gate1', 'completed'), tu('b2'),
];

describe('PipelineBlock', () => {
  it('live: one block, header names the running stage and k/N, every planned line present', () => {
    render(view(MID_BUILD, true));
    expect(screen.getAllByTestId('pipeline-block')).toHaveLength(1);
    const head = screen.getByTestId('pipeline-head');
    expect(head.textContent).toContain('Pipeline');
    expect(head.textContent).toContain('BUILD');
    expect(head.textContent).toContain('5/11');
    expect(lines().map((l) => l.dataset.stage)).toEqual(FULL);
    expect(statuses()).toEqual([
      'evaluate:done', 'gate0:done', 'think:done', 'plan:done', 'gate1:done', 'build:running',
      'review:pending', 'test:pending', 'gate2:pending', 'deliver:pending', 'reflect:pending']);
    const build = lines().find((l) => l.dataset.stage === 'build')!;
    expect(build.querySelector('[data-testid="stage-body"]')).not.toBeNull();
    const pending = lines().find((l) => l.dataset.stage === 'review')!;
    expect(pending.querySelector('[data-testid="stage-body"]')).toBeNull();
    expect(pending.querySelector('button')).toBeNull(); // nothing to open
  });

  it('the first frame already lists every line; later frames only change status', () => {
    const first = [runBlock(), row('evaluate', 'in_progress'), tu('e1')];
    const { rerender } = render(view(first, true));
    const keys0 = lines().map((l) => l.dataset.stage);
    expect(keys0).toEqual(FULL);
    rerender(view(MID_BUILD, true));
    expect(lines().map((l) => l.dataset.stage)).toEqual(keys0);
  });

  it('folds FOLD_DELAY_MS after the answer starts, not at once', () => {
    vi.useFakeTimers();
    const done = [...MID_BUILD, tr('b2'), row('build', 'completed'),
      ...stageRun('review', 1), ...stageRun('test', 1), row('gate2', 'completed'),
      ...stageRun('deliver', 1), ...stageRun('reflect', 1), tx('the answer')];
    const { rerender } = render(view(MID_BUILD, true));
    rerender(view(done, false));
    expect(screen.queryAllByTestId('checklist-line').length).toBe(11); // still open
    act(() => { vi.advanceTimersByTime(2100); });
    expect(screen.queryAllByTestId('checklist-line')).toHaveLength(0);
    const head = screen.getByTestId('pipeline-head');
    expect(head.textContent).toContain('11/11');
    expect(head.textContent).toContain('Gate 2 3/3 fixed');
  });

  it('history: starts folded, opens to the checklist on click', () => {
    const done = [runBlock(), ...stageRun('evaluate', 1), row('gate0', 'completed'), ...stageRun('think', 1),
      ...stageRun('plan', 1), row('build', 'in_progress'), tu('b1'), tr('b1'), row('gate1', 'completed'), row('build', 'completed'),
      ...stageRun('review', 1), ...stageRun('test', 1), row('gate2', 'completed'), ...stageRun('deliver', 1), ...stageRun('reflect', 1),
      tx('the answer')];
    render(view(done, false));
    expect(screen.queryAllByTestId('checklist-line')).toHaveLength(0);
    fireEvent.click(screen.getByTestId('pipeline-head'));
    expect(lines()).toHaveLength(11);
  });

  it('a stage that took under 5s shows no duration', () => {
    render(view(MID_BUILD, true));
    const think = lines().find((l) => l.dataset.stage === 'think')!;
    expect(think.textContent).not.toMatch(/\b\d+s\b/); // all rows share one timestamp: 0s
  });
});
