/**
 * PipelineBlock — a stage that runs again is the same checklist line (chat display
 * v8, XG 2026-10-19).
 *
 * What is tested (real AssistantMessageView): PLAN ran, Gate 1 sent the run back,
 * PLAN ran again → ONE PLAN line, "round 2" while it runs, its body split into two
 * rounds; a goal loop through goal_cycle the same. No second line ever appears.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';
import { GOAL, checklistOf, runBlock, row, tu, tr, stageRun } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }), // tf falls back to its English text
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

const view = (content: ContentBlock[], isStreaming: boolean) => {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:59:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>;
};
const lineOf = (stage: string) => screen.getAllByTestId('checklist-line').filter((l) => l.dataset.stage === stage);

describe('PipelineBlock — rounds', () => {
  it('PLAN again after a Gate 1 BLOCK: one line, round 2, two rounds in its body', () => {
    render(view([
      runBlock(), ...stageRun('evaluate', 1), ...stageRun('think', 1),
      ...stageRun('plan', 1), row('gate1', 'completed', undefined, { gate: { name: 'GATE 1', verdict: 'BLOCK' } }),
      row('plan', 'in_progress'), tu('p2'), tr('p2'), tu('p3'),
    ], true));
    const plan = lineOf('plan');
    expect(plan).toHaveLength(1);
    expect(plan[0].dataset.status).toBe('running');
    expect(plan[0].textContent).toContain('round 2');
    expect(plan[0].querySelectorAll('[data-testid="stage-round"]')).toHaveLength(2);
  });

  it('a goal loop re-entering goal_cycle stays on the goal_cycle line', () => {
    render(view([
      runBlock({ profile: 'goal', stages: GOAL.filter((s) => !s.startsWith('gate')), checklist: checklistOf(GOAL) }),
      ...stageRun('goal_cycle', 1), row('goal_cycle', 'in_progress'), tu('g2'),
    ], true));
    expect(lineOf('goal_cycle')).toHaveLength(1);
    expect(screen.getAllByTestId('checklist-line').map((l) => l.dataset.stage)).toEqual(GOAL);
  });
});
