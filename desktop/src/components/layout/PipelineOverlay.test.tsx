import type React from 'react';
/**
 * PipelineOverlay — every pipeline run, wherever it ran (design
 * docs/design/2026-11-01-brain-eval-and-runs.md §4).
 *
 * Questions, one per test:
 * - does the Runs tab list this machine's runs and the Hive pipeline runs (a native Hive
 *   run stays out), each tagged where it ran, waiting-for-you on top, the tab's label
 *   carrying the waiting count?
 * - does the filter (where, Brain) hold across the two tabs?
 * - does a local row open the big card, whose commands come from the backend, and do
 *   answering, resuming, cancelling and confirming a landing go through the run's routes
 *   (the run's session continues it)?
 * - does a Hive row open Swarm Out's card, and does a run the overlay was opened at open?
 * - does the Metrics tab show the distribution and switch between the five measures?
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';

const fetchAnalytics = vi.fn();
const fetchRunView = vi.fn();
const resumeRun = vi.fn();
const answerDecision = vi.fn();
const confirmLanded = vi.fn();
vi.mock('../../services/pipelines', () => ({
  pipelinesService: {
    fetchAnalytics: (w: string) => fetchAnalytics(w),
    fetchRunView: (id: string) => fetchRunView(id),
    fetchActivePipelines: () => Promise.resolve([]),
    resumeRun: (id: string) => resumeRun(id),
    answerDecision: (id: string, req: string, opt: string) => answerDecision(id, req, opt),
    confirmLanded: (id: string, c: Record<string, string>) => confirmLanded(id, c),
  },
}));
const apiPatch = vi.fn();
vi.mock('../../services/api', () => ({
  default: { patch: (url: string) => apiPatch(url) },
  classifyLoadError: () => 'load error',
}));
let HIVE: { runs: unknown[]; asks: unknown[] } | null = null;
vi.mock('../swarm-out/live', async (orig) => ({
  ...(await orig<typeof import('../swarm-out/live')>()),
  useRuns: () => ({ data: HIVE, failed: false, reload: () => {} }),
}));
vi.mock('../swarm-out/RunOverlay', () => ({
  RunOverlay: ({ id }: { id: string }) => <div data-testid="hive-card">{id}</div>,
}));
let TARGET: string | null = null;
vi.mock('../../utils/overlayTarget', () => ({ BRAIN_TARGET: 'brain:', takeOverlayTarget: () => { const t = TARGET; TARGET = null; return t; } }));

import { PipelineContent } from './PipelineOverlay';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';
const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

const run = (o: Record<string, unknown>) => ({
  id: 'run_x', requirement: 'Req', status: 'completed', profile: 'full', progress: '8/8', cycleTimeMin: 20,
  tokensActual: null, createdAt: '2026-10-31T10:00:00Z', updatedAt: '2026-10-31T10:20:00Z', pauseKind: null,
  needsYou: false, landedAs: null, checkpointReason: null, ownerSessionId: 'owner-1', notLanded: false,
  reportPath: null, auditCard: null, finishedAt: '2026-10-31T10:20:00Z', activeS: null, billedTokens: 2_000_000,
  costUsd: null, humanDecisions: 0, interventions: { answers: 0, corrections: 0, rescues: 0, total: 0 },
  landing: 'landed', notLandedReason: null, rework: 0, audit: [], ...o,
});
const ANALYTICS = {
  window: '30d', overall: {}, trend: [],
  byProject: [
    { project: 'SwarmAI', runs: [
      run({ id: 'run_done', requirement: 'Ship feature X' }),
      run({ id: 'run_ask', requirement: 'Pick storage', status: 'paused', pauseKind: 'decision', needsYou: true, landing: null }),
      run({ id: 'run_drop', requirement: 'Dropped one', status: 'abandoned', needsYou: true, landing: null }),
    ] },
    { project: 'AIDLC', runs: [run({ id: 'run_aidlc', requirement: 'Other brain' })] },
  ],
};
const hiveRun = (id: string, pipeline: boolean) => ({
  dispatch_id: id, hive_id: 'h1', hive: 'demo', session_id: null, title: `Hive ${id}`, origin: 'swarm-out',
  sent_at: '2026-10-31T09:00:00Z', state: 'stopped', stage: 'accepted',
  project_agent: { brain: 'SwarmAI', mode: 'change', profile: 'bugfix', revision: 'r', delivery: 'bundle', acceptance: null },
  settle: { settled: { at: '2026-10-31T10:00:00Z', outcome: 'passed', released: true } },
  result: { ended: 'completed', ended_at: '2026-10-31T09:30:00Z', tokens: null, cost_usd: null,
    pipeline: pipeline ? { run_id: `run_${id}`, profile: 'bugfix', status: 'completed', landing: 'landed', landed_sha: null, audit: [] } : null,
    work: { human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0 }, repos: [], changes: [], files: [], files_omitted: 0,
      steps: { total: 0, failed: 0, agents: 0, by_tool: {} } } },
});
const VIEW = (o: Record<string, unknown> = {}) => ({
  summary: run({ id: 'run_ask', status: 'paused', pauseKind: 'decision', needsYou: true }),
  detail: {
    id: 'run_ask', project: 'SwarmAI', requirement: 'Pick storage', status: 'paused', profile: 'full', cycleTimeMin: null,
    reportMd: '', reportPath: null, reflectLessons: ['a lesson'], commits: [], checkpointReason: null, pauseKind: 'decision',
    ownerSessionId: 'owner-1', needsYou: true, landedAs: null, createdAt: '2026-10-31T10:00:00Z', updatedAt: '',
    auditCard: null, continuePrompt: 'Continue pipeline run run_ask (project SwarmAI)',
    decision: { requestId: 'q1', kind: null, question: 'Which storage?', whyEvidenceInsufficient: null,
      options: [{ id: 'a', choice: 'files', consequence: 'simple', reversible: true, risk: 'low' }] },
  },
  stages: [{ name: 'evaluate', status: 'completed', attempts: 1 }, { name: 'build', status: 'open', attempts: 2 }],
  interventions: { answers: [], corrections: [{ at: '2026-10-31T10:05:00', text: 'use the other table' }], rescues: [] },
  acceptance: [{ id: 'R1.1', text: 'a stored row → it reads back' }],
  actions: [{ key: 'answer', primary: true }, { key: 'cancel', primary: false }, { key: 'open_session', primary: false }],
  ...o,
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  HIVE = { runs: [hiveRun('d1', true), hiveRun('native', false)], asks: [] };
  fetchAnalytics.mockResolvedValue(ANALYTICS);
  fetchRunView.mockResolvedValue(VIEW());
  resumeRun.mockResolvedValue({ ownerSessionId: 'owner-1' });
  answerDecision.mockResolvedValue({ ownerSessionId: 'owner-1' });
  confirmLanded.mockResolvedValue({ '': 'abc1234' });
  apiPatch.mockResolvedValue({});
});

const open = (onContinue = vi.fn().mockResolvedValue(true), close = vi.fn()) => {
  renderQ(<PipelineContent onDispatch={() => true} close={close} onContinue={onContinue} />);
  return { onContinue, close };
};

describe('PipelineOverlay', () => {
  it('lists every pipeline run, tagged where it ran, waiting for you on top', async () => {
    open();
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_ask')).toBeInTheDocument());
    const you = screen.getByTestId('pipeline-section-you').nextElementSibling as HTMLElement;
    expect(within(you).getAllByRole('button', { name: /Pick storage|Dropped one/ }).map((b) => b.getAttribute('data-testid')))
      .toEqual(['pipeline-run-run_ask', 'pipeline-run-run_drop']);      // to answer before to confirm
    expect(screen.getByTestId('pipeline-tab-runs').textContent).toContain('2');
    expect(within(screen.getByTestId('pipeline-run-run_ask')).getByTestId('pipeline-row-tag').textContent).toBe('Answer');
    expect(within(screen.getByTestId('pipeline-run-run_drop')).getByTestId('pipeline-row-tag').textContent).toBe('Confirm it landed');
    fireEvent.click(screen.getByTestId('pipeline-section-done'));
    expect(within(screen.getByTestId('pipeline-run-d1')).getByTestId('pipeline-row-where').textContent).toBe('demo');
    expect(within(screen.getByTestId('pipeline-run-run_done')).getByTestId('pipeline-row-where').textContent).toBe('This machine');
    expect(screen.queryByTestId('pipeline-run-native')).toBeNull();     // a native Hive run stays in Swarm Out
    expect(screen.queryByTestId('pipeline-overall')).toBeNull();        // no row of totals
  });

  it('the filter holds across the tabs', async () => {
    open();
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_ask')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-where-hive'));
    expect(screen.queryByTestId('pipeline-run-run_ask')).toBeNull();
    fireEvent.click(screen.getByTestId('pipeline-tab-metrics'));
    expect(screen.getByTestId('pipeline-dist-total').textContent).toBe('1');
    fireEvent.click(screen.getByTestId('pipeline-where-all'));
    fireEvent.change(screen.getByTestId('pipeline-brain-filter'), { target: { value: 'AIDLC' } });
    expect(screen.getByTestId('pipeline-dist-total').textContent).toBe('1');
    fireEvent.click(screen.getByTestId('pipeline-tab-runs'));
    fireEvent.click(screen.getByTestId('pipeline-section-done'));
    expect(screen.getByTestId('pipeline-run-run_aidlc')).toBeInTheDocument();
    expect(screen.queryByTestId('pipeline-run-run_done')).toBeNull();
  });

  it('a local row opens its big card; answering continues in its session', async () => {
    const { onContinue, close } = open();
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_ask')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-run-run_ask'));
    await waitFor(() => expect(screen.getByTestId('pipeline-card-commands')).toBeInTheDocument());
    expect(fetchRunView).toHaveBeenCalledWith('run_ask');
    expect(screen.getByTestId('pipeline-card-acceptance').textContent).toContain('a stored row → it reads back');
    expect(screen.getByTestId('pipeline-card-position').textContent).toContain('1 / 2');
    expect(screen.getByTestId('pipeline-card-cmd-cancel')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pipeline-card-tab-autonomy'));
    expect(screen.getByTestId('pipeline-card-autonomy').textContent).toContain('use the other table');
    fireEvent.click(screen.getByTestId('pipeline-card-option-a'));
    await waitFor(() => expect(answerDecision).toHaveBeenCalledWith('run_ask', 'q1', 'a'));
    await waitFor(() => expect(onContinue).toHaveBeenCalled());
    expect(onContinue.mock.calls[0][0]).toBe('owner-1');
    expect(onContinue.mock.calls[0][1]).toContain('I answered decision q1');
    expect(onContinue.mock.calls[0][1]).toContain('Continue pipeline run run_ask');
    await waitFor(() => expect(close).toHaveBeenCalled());
  });

  it('resume, cancel and confirm landed go through the run\'s routes', async () => {
    open(vi.fn().mockResolvedValue(false));
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_ask')).toBeInTheDocument());
    fetchRunView.mockResolvedValue(VIEW({ actions: [{ key: 'resume', primary: true }, { key: 'cancel', primary: false }] }));
    fireEvent.click(screen.getByTestId('pipeline-run-run_ask'));
    await waitFor(() => expect(screen.getByTestId('pipeline-card-cmd-cancel')).toBeInTheDocument());
    expect(screen.getByTestId('pipeline-card-position').textContent).toContain('1 / 2');
    fireEvent.click(screen.getByTestId('pipeline-card-cmd-cancel'));
    await waitFor(() => expect(apiPatch).toHaveBeenCalledWith('/pipelines/run_ask/cancel'));
    // settled: it leaves the J / K order, and its commands stay off until fresh ones come
    await waitFor(() => expect(screen.queryByTestId('pipeline-card-position')).toBeNull());
    expect((screen.getByTestId('pipeline-card-cmd-resume') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('pipeline-card-close'));

    fireEvent.click(screen.getByTestId('pipeline-run-run_ask'));
    await waitFor(() => expect(screen.getByTestId('pipeline-card-cmd-resume')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-card-cmd-resume'));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith('run_ask'));
    await waitFor(() => expect(screen.getByTestId('pipeline-card-note')).toBeInTheDocument());   // no tab took it: says where
    fireEvent.click(screen.getByTestId('pipeline-card-close'));

    fetchRunView.mockResolvedValue(VIEW({ actions: [{ key: 'confirm_landed', primary: true }],
      detail: { ...VIEW().detail, id: 'run_drop', decision: null } }));
    fireEvent.click(screen.getByTestId('pipeline-run-run_drop'));
    await waitFor(() => expect(screen.getByTestId('pipeline-card-cmd-confirm_landed')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-card-cmd-confirm_landed'));
    const box = screen.getByTestId('pipeline-card-confirm-input') as HTMLTextAreaElement;
    expect(box.rows).toBeGreaterThanOrEqual(4);                        // a big box for what the person writes
    const submit = screen.getByTestId('pipeline-card-confirm-submit') as HTMLButtonElement;
    fireEvent.change(box, { target: { value: 'not a commit' } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(box, { target: { value: 'repo=abc1234' } });
    fireEvent.click(submit);
    await waitFor(() => expect(confirmLanded).toHaveBeenCalledWith('run_drop', { repo: 'abc1234' }));
  });

  it('a Hive row opens Swarm Out\'s card; a run the overlay was opened at opens', async () => {
    TARGET = 'run_ask';
    open();
    await waitFor(() => expect(screen.getByTestId('pipeline-card')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-card-close'));
    fireEvent.click(screen.getByTestId('pipeline-section-done'));
    fireEvent.click(screen.getByTestId('pipeline-run-d1'));
    expect(screen.getByTestId('hive-card').textContent).toBe('d1');
  });

  it('opened at a Brain (the Brain map\'s Runs), the list is filtered to that Brain', async () => {
    TARGET = 'brain:AIDLC';
    open();
    await waitFor(() => expect((screen.getByTestId('pipeline-brain-filter') as HTMLSelectElement).value).toBe('AIDLC'));
    fireEvent.click(screen.getByTestId('pipeline-section-done'));
    expect(screen.getByTestId('pipeline-run-run_aidlc')).toBeInTheDocument();
    expect(screen.queryByTestId('pipeline-run-run_done')).toBeNull();
    expect(screen.queryByTestId('pipeline-card')).toBeNull();            // a Brain is not a run to open
  });

  it('Metrics: the distribution, and the five measures each with its chart', async () => {
    open();
    await waitFor(() => expect(screen.getByTestId('pipeline-run-run_ask')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('pipeline-tab-metrics'));
    expect(screen.getByTestId('pipeline-dist-total').textContent).toBe('5');
    expect(screen.getByTestId('pipeline-dist-hive').textContent).toContain('demo 1');
    expect(screen.getByTestId('pipeline-measure-delivery').textContent).toContain('3 / 5');
    expect(screen.getByTestId('pipeline-drill-delivery')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pipeline-measure-cost'));
    expect(screen.getByTestId('pipeline-drill-cost')).toBeInTheDocument();
    expect(screen.getByTestId('pipeline-measure-cost').textContent).toContain('M');
    fireEvent.click(screen.getByTestId('pipeline-measure-speed'));
    fireEvent.click(screen.getAllByTestId('pipeline-speed-dot')[0]);
    await waitFor(() => expect(screen.getByTestId('pipeline-card')).toBeInTheDocument());
  });
});
