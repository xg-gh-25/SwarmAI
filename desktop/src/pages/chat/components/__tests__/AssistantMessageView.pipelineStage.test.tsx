/**
 * AssistantMessageView × pipeline_stage — a stage row folds its own work (v7.1).
 *
 * What is tested (real AssistantMessageView + ProcessBlock, only leaf contexts mocked):
 * - a finished stage is ONE row ("BUILD · GATE 1 WARN · 1 step · 1 command") that holds
 *   the work done before it; folded by default, it opens to that work on click
 * - the running stage says "REVIEW · in progress · stage 5 of 8" and holds the live
 *   work, open; a finished row carries no position
 * - colour only for exceptions: WARN is amber, PASS neutral; a gate row shows its
 *   verdict once, never "GATE1 · ★ GATE 1 WARN"
 * (labels without circled numbers and counts instead of file names:
 *  PipelineStageRow.labels.test.tsx)
 * - text before the first work (the alignment) stays outside every stage
 * - an unanswered question inside a finished stage keeps that stage open
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock, PipelineStageContent } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tu = (id: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: 'Running: ls -la src', category: 'bash' }) as ContentBlock;
const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const stage = (over: Partial<PipelineStageContent> = {}): PipelineStageContent => ({
  type: 'pipeline_stage', runId: 'run_t1', stage: 'build', status: 'completed', ordinal: 5,
  position: 4, total: 8, profile: 'full', next: 'review', nextOrdinal: 6,
  gate: { name: 'GATE 1', verdict: 'WARN' }, summary: null, at: '2026-10-17T22:00:00+08:00',
  ...over,
});

function renderView(content: ContentBlock[], isStreaming: boolean) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-17T21:55:00+08:00', isError: false };
  return render(
    <ToastProvider>
      <AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant />
    </ToastProvider>,
  );
}

describe('AssistantMessageView — pipeline stage rows', () => {
  it('history: a finished stage is one row that folds the work done before it', () => {
    renderView([tu('a'), stage(), tu('b')], false);
    const row = screen.getByTestId('pipeline-stage-row');
    expect(row.textContent).toContain('BUILD');
    expect(row.textContent).toContain('GATE 1 WARN');
    expect(row.textContent).toContain('5m 00s'); // 21:55 → 22:00
    expect(row.textContent).not.toContain('stage 4 of 8');
    // a (before the row) and b (after it: the turn ended in this stage) are both its work
    expect(screen.getByTestId('stage-counts').textContent).toBe('· 2 steps · 2 commands');
    // The turn has no result and ended in this stage: it starts open (where it stopped).
    expect(row.dataset.open).toBe('true');
    // The work after the last row (b) stays under it: nothing at the top level.
    expect(screen.getAllByTestId('work-group').every((g) => row.contains(g))).toBe(true);
    fireEvent.click(row.querySelector('button')!);
    expect(row.dataset.open).toBe('false');
    expect(screen.queryAllByTestId('work-group')).toHaveLength(0);
  });

  it('streaming: the running stage names its plan position and holds the live work, open', () => {
    renderView([tu('a'), tr('a'), stage(), tu('b')], true);
    const live = screen.getByTestId('pipeline-stage-live');
    expect(live.textContent).toContain('REVIEW');
    expect(live.textContent).toContain('stage 5 of 8');
    expect(live.firstElementChild?.querySelector('.animate-spin')).toBeNull(); // the live card holds the one spinner
    expect(live.querySelector('[data-live="true"]')).not.toBeNull();
    expect(screen.getAllByText(/progress_activity/)).toHaveLength(1);
  });

  it('streaming: while Gate 1 runs there is no plan position to claim', () => {
    renderView([tu('a'), tr('a'), stage({ stage: 'plan', ordinal: 3, position: 3, next: 'gate1', nextOrdinal: 4, gate: null }),
      tu('b')], true);
    const live = screen.getByTestId('pipeline-stage-live');
    expect(live.textContent).toContain('GATE 1');
    expect(live.textContent).not.toMatch(/stage \d of/);
  });

  it('streaming: right after a stage finishes, before any next step, the next stage is named', () => {
    renderView([tu('a'), tr('a'), stage()], true);
    expect(screen.getByTestId('pipeline-stage-live').textContent).toContain('REVIEW');
  });

  it('a finished turn shows no running stage', () => {
    renderView([tu('a'), stage(), tu('b')], false);
    expect(screen.queryByTestId('pipeline-stage-live')).toBeNull();
  });

  it('the summary (model-written) never reaches the row — neither as text nor as markup', () => {
    const { container } = renderView(
      [tu('a'), stage({ gate: null, summary: '<img src=x onerror=alert> **bold** [link] · more' })], false);
    const row = screen.getByTestId('pipeline-stage-row');
    expect(row.textContent).not.toContain('onerror');
    expect(row.textContent).not.toContain('bold');
    expect(row.querySelector('img')).toBeNull();
    expect(row.querySelector('a')).toBeNull();
    expect(container.querySelector('strong')).toBeNull();
  });

  it('a gate row says its verdict once; PASS is neutral, WARN amber', () => {
    renderView([tu('a'), stage({ stage: 'gate1', ordinal: 4, position: null, gate: { name: 'GATE 1', verdict: 'PASS' } }),
      tu('b'), stage({ gate: { name: 'GATE 1', verdict: 'WARN' } })], false);
    const [gate, build] = screen.getAllByTestId('pipeline-stage-row');
    expect(gate.textContent).toContain('GATE 1');
    expect(gate.textContent?.match(/GATE/g)).toHaveLength(1);
    expect(gate.querySelector('.text-amber-400, .text-red-400')).toBeNull();
    expect(build.querySelector('.text-amber-400')?.textContent).toContain('WARN');
  });

  it('a skipped stage says so and has no gate chip when none was recorded', () => {
    renderView([tu('a'), stage({ stage: 'plan', ordinal: 3, status: 'skipped', gate: null })], false);
    const row = screen.getByTestId('pipeline-stage-row');
    expect(row.textContent).toContain('PLAN');
    expect(row.textContent).toContain('skipped');
    expect(row.textContent).not.toContain('GATE');
  });

  it('the intent stays outside every stage', () => {
    renderView([tx('I read this as: add the registry key.'), tu('a'), stage({ gate: null })], false);
    const align = screen.getByTestId('turn-restatement');
    expect(align.textContent).toContain('add the registry key');
    expect(screen.getByTestId('pipeline-stage-row').contains(align)).toBe(false);
  });

  it('an unanswered question inside a finished stage keeps it open', () => {
    const ask = {
      type: 'ask_user_question', toolUseId: 'q1',
      questions: [{ question: 'STAGE_Q pick?', header: 'h', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
    } as ContentBlock;
    renderView([tu('a'), ask, stage({ gate: null })], false);
    expect(screen.getByTestId('pipeline-stage-row').dataset.open).toBe('true');
    expect(screen.getByText(/STAGE_Q/)).toBeTruthy();
  });
});
