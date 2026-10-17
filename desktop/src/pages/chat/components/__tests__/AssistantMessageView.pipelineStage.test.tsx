/**
 * AssistantMessageView × pipeline_stage — backend-observed stage rows render.
 *
 * What is tested (real AssistantMessageView + ProcessBlock, only leaf contexts mocked):
 * - a pipeline_stage block renders a visible stage row ("⑤ BUILD · 4/8 · ★ GATE 1 WARN")
 *   between two folded process rows, during streaming AND from history
 * - while streaming, a "now running" line above the live process row names the next
 *   stage ("⑥ REVIEW · in progress"); it is gone once the turn ends
 * - the stage summary (model-written text from run.json) renders as plain text:
 *   markup in it is shown literally, never interpreted
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
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
const stage = (over: Partial<PipelineStageContent> = {}): PipelineStageContent => ({
  type: 'pipeline_stage', runId: 'run_t1', stage: 'build', status: 'completed', ordinal: 5,
  position: 4, total: 8, profile: 'full', next: 'review', nextOrdinal: 6,
  gate: { name: 'GATE 1', verdict: 'WARN' }, summary: null, at: '2026-10-17T22:00:00+08:00',
  ...over,
});

function renderView(content: ContentBlock[], isStreaming: boolean) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
  return render(
    <ToastProvider>
      <AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant />
    </ToastProvider>,
  );
}

describe('AssistantMessageView — pipeline stage rows', () => {
  it('history: a stage row sits between two folded process rows', () => {
    const { container } = renderView([tu('a'), stage(), tu('b')], false);
    const rows = screen.getAllByTestId('pipeline-stage-row');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('⑤ BUILD');
    expect(rows[0].textContent).toContain('stage 4 of 8');
    expect(rows[0].textContent).toContain('GATE 1');
    expect(rows[0].textContent).toContain('WARN');
    const order = [...container.querySelectorAll('[data-testid="process-block"], [data-testid="pipeline-stage-row"]')]
      .map((el) => el.getAttribute('data-testid'));
    expect(order).toEqual(['process-block', 'pipeline-stage-row', 'process-block']);
  });

  it('streaming: the live row after the stage row names the next stage', () => {
    const { container } = renderView([tu('a'), tr('a'), stage(), tu('b')], true);
    expect(screen.getByTestId('pipeline-stage-live').textContent).toContain('⑥ REVIEW');
    const order = [...container.querySelectorAll(
      '[data-testid="process-block"], [data-testid="pipeline-stage-row"], [data-testid="pipeline-stage-live"]')]
      .map((el) => el.getAttribute('data-testid'));
    expect(order).toEqual(['process-block', 'pipeline-stage-row', 'pipeline-stage-live', 'process-block']);
  });

  it('streaming: right after a stage finishes, before any next step, the next stage is named', () => {
    renderView([tu('a'), tr('a'), stage()], true);
    expect(screen.getByTestId('pipeline-stage-live').textContent).toContain('⑥ REVIEW');
  });

  it('a finished turn shows no running line', () => {
    renderView([tu('a'), stage(), tu('b')], false);
    expect(screen.queryByTestId('pipeline-stage-live')).toBeNull();
  });

  it('the summary is plain text — markup is shown, not interpreted', () => {
    const { container } = renderView(
      [tu('a'), stage({ summary: '<img src=x onerror=alert(1)> **bold** [link](http://x)' })], false);
    const row = screen.getByTestId('pipeline-stage-row');
    expect(row.textContent).toContain('<img src=x onerror=alert(1)> **bold** [link](http://x)');
    expect(row.querySelector('img')).toBeNull();
    expect(row.querySelector('a')).toBeNull();
    expect(container.querySelector('strong')).toBeNull();
  });

  it('a skipped stage says so and has no gate chip when none was recorded', () => {
    renderView([tu('a'), stage({ stage: 'plan', ordinal: 3, status: 'skipped', gate: null })], false);
    const row = screen.getByTestId('pipeline-stage-row');
    expect(row.textContent).toContain('③ PLAN');
    expect(row.textContent).toContain('skipped');
    expect(row.textContent).not.toContain('GATE');
  });
});
