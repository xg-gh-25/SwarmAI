/**
 * PipelineStageRow labels and counts (XG 2026-10-19, items 1 + 6).
 *
 * What is tested (real AssistantMessageView, only leaf contexts mocked):
 * - no circled number before a stage or gate name; the label uses the same tag style
 *   as the WORKED / WORKING header (`stage-label`)
 * - a finished stage row says counts — steps · files changed · commands — and its
 *   duration; never a file name, never the model's summary text
 * - a gate row (GATE 0 / 1 / 2) says only its verdict: no counts, no duration, no
 *   chevron (it holds no work)
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
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

const bash = (id: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: 'Running: make test', category: 'bash' }) as ContentBlock;
const ed = (id: string, p: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Edit', summary: `Writing to ${p}`, category: 'write' }) as ContentBlock;
const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const row = (stage: string, at: string, over: Partial<PipelineStageContent> = {}): PipelineStageContent => ({
  type: 'pipeline_stage', runId: 'run_t1', stage, status: 'completed', ordinal: 5,
  position: 4, total: 8, profile: 'full', next: 'review', nextOrdinal: 6,
  gate: null, summary: null, at, ...over,
});

function renderView(content: ContentBlock[]) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:00:00+08:00', isError: false };
  const out = render(<ToastProvider><AssistantMessageView message={message} isStreaming={false} isLastAssistant /></ToastProvider>);
  // A finished pipeline turn's block starts folded (chat display v8): open it to see its lines.
  const head = screen.queryByTestId('pipeline-head');
  if (head && head.getAttribute('aria-expanded') === 'false') fireEvent.click(head);
  return out;
}

const CIRCLED = /[①-⑳]/;

describe('PipelineStageRow — labels', () => {
  it('stage and gate labels carry no circled number and use the WORKED tag style', () => {
    renderView([
      bash('a'), tr('a'),
      row('gate1', '2026-10-19T09:04:00+08:00', { ordinal: 4, position: null, gate: { name: 'GATE 1', verdict: 'WARN' } }),
      row('build', '2026-10-19T09:04:00+08:00'), tx('done'),
    ]);
    const rows = screen.getAllByTestId('pipeline-stage-row');
    expect(rows).toHaveLength(2);
    for (const r of rows) expect(r.textContent).not.toMatch(CIRCLED);
    const labels = screen.getAllByTestId('stage-label');
    expect(labels.map((l) => l.textContent)).toEqual(['GATE 1', 'BUILD']);
    for (const l of labels) expect(l.className).toMatch(/uppercase/);
  });
});

describe('PipelineStageRow — counts, not names', () => {
  it('a finished stage: steps · files changed · commands and its duration; no file names, no summary', () => {
    renderView([
      ed('e1', '/r/alpha_module.ts'), tr('e1'), ed('e2', '/r/beta_module.ts'), tr('e2'), bash('b1'), tr('b1'),
      row('build', '2026-10-19T09:03:30+08:00', { summary: 'TDD 6R→GREEN · changed things' }), tx('done'),
    ]);
    const r = screen.getByTestId('pipeline-stage-row');
    expect(screen.getByTestId('stage-counts').textContent).toBe('· 3 steps · 2 files changed · 1 command');
    expect(r.textContent).toContain('3m 30s');
    expect(r.textContent).not.toContain('alpha_module');
    expect(r.textContent).not.toContain('TDD 6R');
    expect(screen.queryByTestId('stage-changed-files')).toBeNull();
  });

  it('a gate row: its verdict only — no counts, no duration, no chevron', () => {
    renderView([
      bash('a'), tr('a'), row('evaluate', '2026-10-19T09:02:00+08:00', { ordinal: 1, position: 1 }),
      row('gate0', '2026-10-19T09:02:00+08:00', { ordinal: 1, position: null, gate: { name: 'GATE 0', verdict: 'SUPPORTED' } }),
      tx('done'),
    ]);
    const gate = screen.getAllByTestId('pipeline-stage-row')[1];
    expect(gate.textContent).toContain('GATE 0');
    expect(gate.textContent).toContain('SUPPORTED');
    expect(gate.querySelector('[data-testid="stage-counts"]')).toBeNull();
    expect(gate.textContent).not.toMatch(/\d+s\b/);
    expect(gate.textContent).not.toContain('chevron_right');
  });
});

describe('PipelineStageRow — a started stage is one row from start to end (AC2a/b)', () => {
  const start = (stage: string, at: string): PipelineStageContent =>
    row(stage, at, { status: 'in_progress', next: null, nextOrdinal: null });
  const view = (content: ContentBlock[], isStreaming: boolean) => {
    const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:00:00+08:00', isError: false };
    return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>;
  };

  it('running: "BUILD · in progress" (its place in the plan is the block header\'s k/N); ended: the same element, now with counts and its time', () => {
    const before = [start('build', '2026-10-19T09:01:00+08:00'), bash('a'), tr('a'), bash('b')];
    const { rerender } = render(view(before, true));
    const live = screen.getByTestId('pipeline-stage-live');
    expect(live.textContent).toContain('BUILD');
    expect(live.textContent).toContain('in progress');
    rerender(view([...before, tr('b'), row('build', '2026-10-19T09:03:00+08:00'), bash('c')], true));
    const done = screen.getByTestId('pipeline-stage-row');
    expect(done).toBe(live);
    expect(done.textContent).toContain('2m 00s');
    expect(screen.queryAllByTestId('pipeline-stage-row')).toHaveLength(1);
  });
});

describe('PipelineStageRow — gate verdict colour (finding 7)', () => {
  const gateRow = (stage: string, name: string, verdict: string) =>
    row(stage, '2026-10-19T09:02:00+08:00', { position: null, gate: { name, verdict } });
  it.each([
    ['gate2', 'GATE 2', '51/63 fixed', 'text-amber-400'],
    ['gate2', 'GATE 2', '11/11 fixed', null],
    ['gate0', 'GATE 0', 'WRONG-FRAME correction', 'text-amber-400'],
    ['gate0', 'GATE 0', 'SUPPORTED', null],
    ['gate1', 'GATE 1', 'BLOCK', 'text-red-400'],
  ])('%s %s → %s', (stage, name, verdict, cls) => {
    renderView([bash('a'), tr('a'), gateRow(stage, name, verdict), tx('done')]);
    const el = screen.getByText(verdict);
    if (cls) expect(el.className).toContain(cls);
    else expect(el.className).not.toMatch(/amber|red/);
  });
});

describe('PipelineStageRow — a started stage in a finished turn (round-2 M1)', () => {
  it('says it did not finish in this turn, folded, with no "in progress"', () => {
    renderView([row('build', '2026-10-19T09:01:00+08:00', { status: 'in_progress', next: null, nextOrdinal: null }), bash('a'), tr('a'), tx('Paused here.')]);
    const r = screen.getByTestId('pipeline-stage-row');
    expect(r.textContent).toContain('not finished in this turn');
    expect(r.textContent).not.toContain('in progress');
    expect(r.getAttribute('role')).toBeNull();
    expect(r.dataset.open).toBe('false');
    expect(screen.queryByTestId('pipeline-stage-live')).toBeNull();
  });
});

describe('Gate-2 findings — the live section is the last STAGE section', () => {
  it('after EVALUATE ends and GATE 0 lands, the arriving work is in an open, live EVALUATE row', () => {
    const content: ContentBlock[] = [
      row('evaluate', '2026-10-19T09:01:00+08:00', { status: 'in_progress', ordinal: 1, position: 1 }), bash('a'), tr('a'),
      row('evaluate', '2026-10-19T09:02:00+08:00', { ordinal: 1, position: 1 }),
      row('gate0', '2026-10-19T09:02:00+08:00', { ordinal: 1, position: null, gate: { name: 'GATE 0', verdict: 'SUPPORTED' } }),
      bash('b'),
    ];
    const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:00:00+08:00', isError: false };
    render(<ToastProvider><AssistantMessageView message={message} isStreaming isLastAssistant /></ToastProvider>);
    const [evaluate, gate] = screen.getAllByTestId('pipeline-stage-row');
    expect(evaluate.textContent).toContain('EVALUATE');
    expect(evaluate.dataset.open).toBe('true');
    expect(evaluate.querySelector('[data-live="true"]')).not.toBeNull();
    expect(gate.textContent).toContain('GATE 0');
    expect(gate.querySelector('[data-testid="stage-body"]')).toBeNull();
  });
});

describe('Gate-2 findings — verdict colour reads where a recorded history ended', () => {
  const gateRow = (verdict: string) =>
    row('gate1', '2026-10-19T09:02:00+08:00', { position: null, gate: { name: 'GATE 1', verdict } });
  it.each([
    ['SKIPPED', null], ['BLOCK→revised→PASS', null], ['PASS→BLOCK', 'text-red-400'], ['NO-GO', 'text-amber-400'],
  ])('%s', (verdict, cls) => {
    renderView([bash('a'), tr('a'), gateRow(verdict), tx('done')]);
    const el = screen.getByText(verdict);
    if (cls) expect(el.className).toContain(cls);
    else expect(el.className).not.toMatch(/amber|red/);
  });
});

describe('Gate-2 recheck LOW — a just-ended live stage folds 2s after it stops being live, not at once', () => {
  it('EVALUATE ended and held the live work; THINK starts: EVALUATE is still open, then folds after the delay', () => {
    vi.useFakeTimers();
    try {
      const at = (m: number) => `2026-10-19T09:0${m}:00+08:00`;
      const head: ContentBlock[] = [
        row('evaluate', at(1), { status: 'in_progress', ordinal: 1, position: 1 }), bash('a'), tr('a'),
        row('evaluate', at(2), { ordinal: 1, position: 1 }), bash('b'),
      ];
      const view = (content: ContentBlock[]) => {
        const message: Message = { id: 'm1', role: 'assistant', content, timestamp: at(0), isError: false };
        return <ToastProvider><AssistantMessageView message={message} isStreaming isLastAssistant /></ToastProvider>;
      };
      const { rerender } = render(view(head));
      const evaluate = screen.getByTestId('pipeline-stage-row');
      expect(evaluate.dataset.open).toBe('true');
      rerender(view([...head, tr('b'), row('think', at(3), { status: 'in_progress', ordinal: 2, position: 2 }), bash('c')]));
      expect(evaluate.dataset.open).toBe('true'); // not at once
      act(() => { vi.advanceTimersByTime(2100); });
      expect(evaluate.dataset.open).toBe('false');
    } finally {
      vi.useRealTimers();
    }
  });
});
