/** Online Eval, Offline Eval and LLM Judges: the calibration buttons only take a
 *  label when the batch accepts one; a failing offline case drills down to its
 *  trajectory and steps (and says why a user case has none); the release record
 *  shows each full run's checks; the judge page reads its numbers and the model
 *  setting from the backend. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { routes, reset, show, calls, check, fakeApi } from './harness';

vi.mock('../../../services/api', () => fakeApi());
vi.mock('../../../utils/openExternal', () => ({ openExternal: vi.fn() }));
import { OnlineTab } from '../OnlineTab';
import { OfflineTab } from '../OfflineTab';
import { JudgesTab } from '../JudgesTab';

beforeEach(() => reset());

const online = (accepting: boolean, labels: Record<string, string> = {}) => ({
  last_run: '2026-10-26T09:00:00+00:00', window_days: 7, sessions_checked: 12, sampler_error: null, judge_error: null,
  checks: { O1_LANGUAGE: { checked: 40, failed: 2, rate: 0.05, examples: [] } }, trend: [],
  low_details: [{ session_id: 's1', title: 'Fix the build', goal_score: 0.3, tool_score: 0.5, reason: 'did not run tests' }],
  calibration: { accepting, reason: accepting ? null : 'scored by an older judge prompt', labels },
});

describe('Online Eval', () => {
  it('posts a label for a low-scored session and opens it', async () => {
    routes['GET /eval/session-quality'] = online(true);
    routes['POST /eval/calibration'] = { status: 'ok' };
    const { actions } = show(<OnlineTab />);
    const row = await screen.findByTestId('low-session');
    fireEvent.click(within(row).getByTestId('calibrate-disagree'));
    await waitFor(() => expect(calls.find((c) => c.url === '/eval/calibration')?.body).toEqual({ session_id: 's1', human: 'disagree' }));
    fireEvent.click(within(row).getByText('Open session'));
    expect(actions.openSession).toHaveBeenCalledWith('s1');
  });

  it('does not take a label from a batch the backend does not accept, and marks one already given', async () => {
    routes['GET /eval/session-quality'] = online(false, { s1: 'agree' });
    show(<OnlineTab />);
    const row = await screen.findByTestId('low-session');
    const agree = within(row).getByTestId('calibrate-agree') as HTMLButtonElement;
    expect(agree.disabled).toBe(true);
    expect(agree.className).toContain('btn-primary');
    fireEvent.click(agree);
    expect(calls.some((c) => c.url === '/eval/calibration')).toBe(false);
  });
});

const offCase = (id: string, origin: string) => ({
  id, category: 'decision', dimension: 'quality', tier: 'active', eval_method: 'behavior', release_tier: 'watch',
  _origin: origin, affected_by: [], evaluators: ['trajectory_capture'], purpose: { en: `${id} decides right.` },
  last_result: { status: 'failed', run_id: 'full1', triggered_at: '2026-10-20T03:00:00+00:00' },
});

describe('Offline Eval', () => {
  beforeEach(() => {
    routes['GET /eval/health'] = { release: null, dimension_summary: {}, last_run: null, total_cases: 2, lines: {
      quick: { last_run: null, passed: null, total: null, days: [] },
      full: { last_run: '2026-10-20T03:00:00+00:00', release: 'blocked', next_run: null },
      online: { last_run: null, sessions_checked: 0, next_run: null },
      golden_set: { product: 1, private: 1, total: 2, product_cap: 60, total_cap: 80 } } };
    routes['GET /eval/golden-set'] = { total_cases: 2, cases: [offCase('OFF_A', 'product'), offCase('MY_B', 'private')] };
    routes['GET /eval/history'] = [
      { run_id: 'probe1', line: 'probe', triggered_by: 'manual', triggered_at: '2026-10-26T12:00:00+00:00', release: 'release', release_reason: '1/1', total_cases: 1, cases_passed: 1, cases_failed: 0, cases_skipped: 0, duration_seconds: 9 },
      { run_id: 'canary1', line: 'quick', triggered_by: 'canary', triggered_at: '2026-10-26T09:00:00+00:00', release: 'release', total_cases: 36, cases_passed: 36, cases_failed: 0, cases_skipped: 0, duration_seconds: 9 },
      { run_id: 'full1', line: 'full', triggered_by: 'scheduled', triggered_at: '2026-10-20T03:00:00+00:00', release: 'blocked', release_reason: 'gate failed: Q_X',
        release_checks: [check('redline', true), check('dimensions', false, { cases: [{ id: 'Q_X' }] }), check('denominator', true, { selected: 36, with_result: 36 }), check('record', true)],
        report: '2026-10-20.html', total_cases: 46, cases_passed: 44, cases_failed: 2, cases_skipped: 0, duration_seconds: 900 },
      { run_id: 'old1', line: 'full', triggered_by: 'scheduled', triggered_at: '2026-09-01T03:00:00+00:00', release: null, total_cases: 158, cases_passed: 150, cases_failed: 8, cases_skipped: 0, duration_seconds: 900 },
    ];
    routes['GET /eval/runs/probe1'] = { run_id: 'probe1', cases: [{ id: 'OFF_A', status: 'passed' }] };
    routes['GET /eval/runs/full1'] = { run_id: 'full1', cases: [
      { id: 'OFF_A', status: 'failed', notes: 'trajectory: Read missing', observed_trajectory: ['Grep foo', 'Bash ls'],
        expected_check: [{ step: 'Grep', found: true }, { step: 'Read', found: false }],
        steps: [{ tool: 'Grep', input: 'foo', output: '0 hits' }, { tool: 'Bash', input: 'ls', output: 'boom', is_error: true }], final_text: 'I recommend X.' },
      { id: 'MY_B', status: 'failed', notes: 'judge: wrong option', observed_trajectory: ['Read'] },
    ] };
  });

  it('drills a failing case down to session, trajectory and step, from the latest full run (not a newer quick check or probe)', async () => {
    show(<OfflineTab />);
    const a = await screen.findByTestId('failing-OFF_A');
    const dd = within(a).getByTestId('drilldown');
    await waitFor(() => expect(dd.textContent).toContain('failed · trajectory: Read missing'));
    expect(dd.textContent).toContain('Expected but not seen: Read');
    expect(dd.textContent).toContain('2. Bash ls → boom');
    expect(dd.textContent).toContain('The agent’s final answer');
    expect(calls.some((c) => c.url === '/eval/runs/full1')).toBe(true);
    expect(calls.some((c) => c.url === '/eval/runs/probe1')).toBe(false);
    const b = within(screen.getByTestId('failing-MY_B')).getByTestId('drilldown');
    expect(b.textContent).toContain('Your own cases keep only tool names');
  });

  it('the release record lists full runs with a decision; older ones only on request', async () => {
    show(<OfflineTab />);
    const rec = await screen.findByTestId('release-record');
    const rows = rec.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('1 failed');
    expect(rows[0].textContent).toContain('36/36');
    expect(rows[0].textContent).toContain('gate failed: Q_X');
    expect(rec.textContent).not.toContain('canary');
    expect(rec.textContent).not.toContain('1/1');
    fireEvent.click(screen.getByText('Show'));
    expect(screen.getByTestId('release-record').querySelectorAll('tbody tr')).toHaveLength(2);
  });
});

const judges = (agentModel: string, temperature = 0) => ({
  judge_model: 'claude-sonnet-4-6', agent_model: agentModel, cases_total: 46, cases_judged: 10, gating_total: 36, gating_judged: 0,
  judges: [
    { id: 'offline_decision', line: 'offline', prompt: 'P1', system: 'S1', temperature, max_tokens: 400, case_ids: ['OFF_A'], calibration: null },
    { id: 'online_session', line: 'online', prompt: 'P2', system: 'S2', temperature: 0, max_tokens: 500, prompt_sha: 'abc',
      calibration: { prompt_sha: 'abc', labels: 4, agree: 3, agreement: 0.75 } },
  ],
});

describe('a started full run', () => {
  it('is watched until it is recorded, then everything refreshes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      routes['GET /eval/health'] = { release: null, dimension_summary: {}, last_run: null, total_cases: 0 };
      routes['GET /eval/golden-set'] = { total_cases: 0, cases: [] };
      routes['GET /eval/history'] = [];
      routes['POST /eval/run'] = { status: 'started', run_id: 'new1' };
      let n = 0;
      routes['GET /eval/runs/new1'] = () => (++n < 2 ? { run_id: 'new1', status: 'running' } : { run_id: 'new1', cases: [] });
      show(<OfflineTab />);
      fireEvent.click(await screen.findByTestId('run-full'));
      await waitFor(() => expect(screen.getByTestId('run-full').textContent).toBe('Running…'));
      expect((screen.getByTestId('run-full') as HTMLButtonElement).disabled).toBe(true);
      const before = calls.filter((c) => c.url.startsWith('/eval/history')).length;
      await vi.advanceTimersByTimeAsync(5500);
      await waitFor(() => expect(screen.getByTestId('run-full').textContent).toBe('Run the full set now'));
      await waitFor(() => expect(calls.filter((c) => c.url.startsWith('/eval/history')).length).toBeGreaterThan(before));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('LLM Judges', () => {
  it('reads the judge ratio and agreement, and saves a model change through settings', async () => {
    routes['GET /eval/judges'] = judges('gpt-x');
    routes['GET /settings'] = { eval_judge_model: 'claude-sonnet-4-6', available_models: ['claude-sonnet-4-6', 'claude-opus-4-8'] };
    routes['PUT /settings'] = {};
    show(<JudgesTab />);
    const ratio = await screen.findByTestId('judge-ratio');
    expect(ratio.textContent).toContain('0/36');
    expect(ratio.textContent).toContain('10/46');
    expect(screen.getByTestId('judge-agreement').textContent).toContain('75%');
    const sel = (await screen.findByTestId('judge-model')) as HTMLSelectElement;
    await waitFor(() => expect(sel.options.length).toBe(2));
    fireEvent.change(sel, { target: { value: 'claude-opus-4-8' } });
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ eval_judge_model: 'claude-opus-4-8' }));
  });

  it('flags a judge from the agent’s own model family, and a judge whose temperature is not 0', async () => {
    routes['GET /eval/judges'] = judges('claude-opus-5-5', 0.7);
    routes['GET /settings'] = {};
    show(<JudgesTab />);
    const req = await screen.findByTestId('judge-requirements');
    expect(req.textContent).toContain('same family');
    expect(req.textContent).toContain('temperature not 0');
    expect(req.textContent).toContain('temperature 0.7 / 0');
  });
});
