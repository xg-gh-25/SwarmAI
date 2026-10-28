/** Live sessions and the judges: it leads with how many sessions were corrected; the
 *  calibration buttons appear only when the batch accepts a label; nothing to score is
 *  a reason, never a zero; the judge part reads its numbers and the model setting
 *  from the backend. (Offline cases and the run history are tested with Cases.) */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { routes, reset, show, calls, fakeApi } from './harness';

vi.mock('../../../services/api', () => fakeApi());
import { OnlineTab } from '../OnlineTab';
import { JudgesTab } from '../JudgesTab';

beforeEach(() => reset());

const online = (accepting: boolean, labels: Record<string, string> = {}) => ({
  last_run: '2026-10-26T09:00:00+00:00', window_days: 7, sessions_checked: 12, sampler_error: null, judge_error: null,
  checks: { O1_LANGUAGE: { checked: 40, failed: 2, rate: 0.05, examples: [] } }, trend: [],
  low_details: [{ session_id: 's1', title: 'Fix the build', goal_score: 0.3, tool_score: 0.5, reason: 'did not run tests' }],
  calibration: { accepting, reason: accepting ? null : 'scored by an older judge prompt', labels },
});

describe('Live sessions', () => {
  it('leads with how many sessions you corrected', async () => {
    routes['GET /eval/session-quality'] = { ...online(true), sessions_checked: 33,
      checks: { O3_CORRECTIONS: { checked: 33, failed: 14, rate: 0.424, examples: [] } } };
    show(<OnlineTab />);
    expect((await screen.findByTestId('online-hero')).textContent).toBe('You corrected the agent in 14 of 33 sessions.');
  });

  it('posts a label for a low-scored session, opened in place, and opens the session', async () => {
    routes['GET /eval/session-quality'] = online(true);
    routes['POST /eval/calibration'] = { status: 'ok' };
    const { actions } = show(<OnlineTab />);
    const row = await screen.findByTestId('low-session');
    expect(within(row).queryByTestId('calibrate-disagree')).toBeNull();
    fireEvent.click(within(row).getByText('Fix the build'));
    fireEvent.click(within(row).getByTestId('calibrate-disagree'));
    await waitFor(() => expect(calls.find((c) => c.url === '/eval/calibration')?.body).toEqual({ session_id: 's1', human: 'disagree' }));
    fireEvent.click(within(row).getByText('Open session'));
    expect(actions.openSession).toHaveBeenCalledWith('s1');
  });

  it('offers no label for a batch the backend does not accept, says why, and marks one already given', async () => {
    routes['GET /eval/session-quality'] = online(false, { s1: 'agree' });
    const { unmount } = show(<OnlineTab />);
    const row = await screen.findByTestId('low-session');
    fireEvent.click(within(row).getByText('Fix the build'));
    expect(within(row).queryByTestId('calibrate-agree')).toBeNull();
    expect(screen.getByTestId('calibration-closed').textContent).toContain('older judge prompt');
    expect(within(row).getByTestId('calibrate-label').textContent).toBe('You agreed with the judge.');
    unmount();
    routes['GET /eval/session-quality'] = online(true, { s1: 'agree' });
    show(<OnlineTab />);
    const again = await screen.findByTestId('low-session');
    fireEvent.click(within(again).getByText('Fix the build'));
    expect(within(again).getByTestId('calibrate-agree').className).toContain('btn-primary');
  });
});

describe('Live sessions: nothing to score is a reason, not a zero', () => {
  it('says the judge failed on part of the sample', async () => {
    routes['GET /eval/session-quality'] = { ...online(true), judge_sampled: 5, judge_failed: 2,
      checks: { O4_GOAL: { checked: 3, failed: 1, rate: 0.33, examples: [] } } };
    show(<OnlineTab />);
    expect((await screen.findByTestId('online-judge-partial')).textContent).toContain('2 of 5');
  });

  it('shows why a judged check has no count instead of 0/0', async () => {
    routes['GET /eval/session-quality'] = { ...online(true), judge_sampled: 3, judge_failed: 3,
      judge_error: '3/3 judged sessions failed: judge call failed',
      checks: { O4_GOAL: { checked: 0, failed: 0, rate: null, examples: [] } } };
    show(<OnlineTab />);
    expect(await screen.findByText(/The judge failed on every sampled session/)).toBeTruthy();
    expect(screen.queryByText(/0 of 0/)).toBeNull();
  });

  it('shows "nothing to score" for an empty code check', async () => {
    routes['GET /eval/session-quality'] = { ...online(true), sessions_checked: 0,
      checks: { O1_LANGUAGE: { checked: 0, failed: 0, rate: null, examples: [] } } };
    show(<OnlineTab />);
    expect(await screen.findByText('nothing to score')).toBeTruthy();
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

describe('Judges (in Method)', () => {
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
