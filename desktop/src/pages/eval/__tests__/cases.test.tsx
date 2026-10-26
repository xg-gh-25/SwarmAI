/** Golden Cases: tiers come from the backend's release_tier (missing = watch, never
 *  gate), a product case is read-only, deleting one of your own needs a second click
 *  and calls DELETE, and the form sends the shape the backend admits and shows each
 *  admission check's own result. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { routes, reset, show, calls, failNext, fakeApi } from './harness';

vi.mock('../../../services/api', () => fakeApi());
import { GoldenCasesTab } from '../GoldenCasesTab';

const gc = (id: string, extra: Record<string, unknown> = {}) => ({
  id, category: 'recall', dimension: 'cognition', tier: 'active', eval_method: 'programmatic',
  _origin: 'product', affected_by: [], evaluators: ['canary_pass'], last_result: null,
  purpose: { en: `${id} protects something. Breaks otherwise.` }, ...extra,
});

beforeEach(() => {
  reset();
  routes['GET /eval/golden-set'] = { total_cases: 5, cases: [
    gc('RED_1', { release_tier: 'red' }),
    gc('GATE_1', { release_tier: 'gate', last_result: { status: 'failed', run_id: 'r', triggered_at: '2026-10-26T10:00:00Z' } }),
    gc('NO_TIER'),
    gc('OFF_1', { eval_method: 'behavior', release_tier: 'watch', category: 'decision' }),
    gc('MINE_1', { _origin: 'private', release_tier: 'watch' }),
    gc('OLD_1', { tier: 'archived', release_tier: 'watch' }),
  ] };
});

describe('Golden Cases', () => {
  it('counts tiers from release_tier; a case without one is watched, and archived cases are not shown', async () => {
    show(<GoldenCasesTab />);
    const sum = await screen.findByTestId('tier-summary');
    expect(Array.from(sum.querySelectorAll('b')).map((b) => b.textContent)).toEqual(['1', '1', '3']);
    expect(screen.getByTestId('case-row-NO_TIER').querySelector('[data-tier]')?.getAttribute('data-tier')).toBe('watch');
    expect(screen.queryByTestId('case-row-OLD_1')).toBeNull();
  });

  it('filters by tier and by "failing only"', async () => {
    show(<GoldenCasesTab />);
    await screen.findByTestId('tier-summary');
    fireEvent.click(screen.getAllByText('Failing only')[0]);
    expect(screen.getByTestId('case-row-GATE_1')).toBeTruthy();
    expect(screen.queryByTestId('case-row-RED_1')).toBeNull();
    fireEvent.click(screen.getAllByText('Failing only')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Red line' }));
    expect(screen.getByTestId('case-row-RED_1')).toBeTruthy();
    expect(screen.queryByTestId('case-row-GATE_1')).toBeNull();
  });

  it('a product case can be copied, not edited or deleted', async () => {
    routes['GET /eval/golden-set/RED_1'] = { ...gc('RED_1', { release_tier: 'red',
      verification: { command: 'probe x', expected_contains: 'X_OK', negative_command: 'probe x negative',
                      negative_expected_contains: 'X_TEETH', timeout: 30, grep: '', corpus: 'docs' } }), history: [] };
    show(<GoldenCasesTab />);
    fireEvent.click(await screen.findByTestId('case-row-RED_1'));
    await screen.findByTestId('case-detail');
    expect(screen.getByTestId('case-copy')).toBeTruthy();
    expect(screen.queryByTestId('case-edit')).toBeNull();
    expect(screen.queryByTestId('case-delete')).toBeNull();
    fireEvent.click(screen.getByTestId('case-copy'));
    expect((screen.getByTestId('form-id') as HTMLInputElement).value).toBe('RED_1_MINE');
    // a copy takes only the verification keys a quick check reads
    routes['POST /eval/golden-set/try'] = { ok: true, trial: null, checks: [] };
    fireEvent.click(screen.getByTestId('form-try'));
    await waitFor(() => expect(calls.some((c) => c.url === '/eval/golden-set/try')).toBe(true));
    const v = (calls.find((c) => c.url === '/eval/golden-set/try')!.body as { verification: Record<string, unknown> }).verification;
    expect(Object.keys(v).sort()).toEqual(['command', 'expected_contains', 'negative_command', 'negative_expected_contains', 'timeout']);
  });

  it('deleting my case takes a second click and calls DELETE on it', async () => {
    routes['GET /eval/golden-set/MINE_1'] = { ...gc('MINE_1', { _origin: 'private', release_tier: 'watch' }), history: [] };
    routes['DELETE /eval/golden-set/MINE_1'] = { status: 'deleted' };
    show(<GoldenCasesTab />);
    fireEvent.click(await screen.findByTestId('case-row-MINE_1'));
    fireEvent.click(await screen.findByTestId('case-delete'));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    fireEvent.click(screen.getByTestId('case-delete-confirm'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/eval/golden-set/MINE_1')).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('case-panel')).toBeNull());
  });

  it('an offline case is sent as a behaviour case with its prompt, steps and rubric; "try" shows each check', async () => {
    routes['POST /eval/golden-set/try'] = { ok: false, trial: null, checks: [
      { name: 'schema', ok: true, errors: [] }, { name: 'offline', ok: false, errors: ['rubric has no FAIL side'] },
    ] };
    show(<GoldenCasesTab />);
    fireEvent.click(await screen.findByTestId('add-case'));
    const form = await screen.findByTestId('case-form');
    fireEvent.change(screen.getByTestId('form-id'), { target: { value: 'MY_LOOKUP' } });
    fireEvent.change(screen.getByTestId('form-head'), { target: { value: 'The agent looks it up first.' } });
    fireEvent.change(screen.getByTestId('form-prompt'), { target: { value: 'What did we decide?' } });
    fireEvent.change(screen.getByTestId('form-steps'), { target: { value: 'Grep|Read  Bash' } });
    fireEvent.change(screen.getByTestId('form-rubric'), { target: { value: 'PASS only if it cites the doc' } });
    fireEvent.change(screen.getByTestId('form-affected'), { target: { value: 'a.py  b.py' } });
    fireEvent.click(screen.getByTestId('form-try'));
    await waitFor(() => expect(screen.getByTestId('form-check-offline').getAttribute('data-state')).toBe('fail'));
    expect(screen.getByTestId('form-check-schema').getAttribute('data-state')).toBe('ok');
    expect(screen.getByTestId('form-check-refs').getAttribute('data-state')).toBe('pending');
    expect(screen.getByTestId('form-check-offline').textContent).toContain('rubric has no FAIL side');
    expect(within(form).queryByTestId('form-check-trial')).toBeNull();   // an offline case is not trial-run
    const body = calls.find((c) => c.url === '/eval/golden-set/try')!.body as Record<string, unknown>;
    expect(body).toMatchObject({
      id: 'MY_LOOKUP', eval_method: 'behavior', evaluators: ['trajectory_capture'], dimension: 'quality',
      scenario: { prompt: 'What did we decide?' }, expected_trajectory: ['Grep|Read', 'Bash'],
      decision_rubric: 'PASS only if it cites the doc', affected_by: ['a.py', 'b.py'],
      purpose: { en: 'The agent looks it up first.' },
    });
    expect(body).not.toHaveProperty('verification');
  });

  it('editing an offline case keeps what the form does not show; a changed field clears the old trial', async () => {
    routes['GET /eval/golden-set/MINE_2'] = { ...gc('MINE_2', { _origin: 'private', release_tier: 'watch', eval_method: 'behavior',
      purpose: { en: 'The agent asks once. Else it nags.', zh: '只问一次。否则烦人。' },
      scenario: { prompt: 'Change it', system: 'swarmai' }, expected_trajectory: ['Read'], trajectory_match: 'in_order',
      allowed_tools: ['Read', 'Grep'], decision_rubric: 'PASS only if one question' }), history: [] };
    routes['GET /eval/golden-set'] = { total_cases: 1, cases: [gc('MINE_2', { _origin: 'private', release_tier: 'watch', eval_method: 'behavior' })] };
    routes['POST /eval/golden-set/try'] = { ok: true, trial: null, checks: [{ name: 'schema', ok: true, errors: [] }] };
    routes['PUT /eval/golden-set/MINE_2'] = { status: 'updated' };
    show(<GoldenCasesTab />);
    fireEvent.click(await screen.findByTestId('case-row-MINE_2'));
    fireEvent.click(await screen.findByTestId('case-edit'));
    fireEvent.click(screen.getByTestId('form-try'));
    await waitFor(() => expect(screen.getByTestId('form-check-schema').getAttribute('data-state')).toBe('ok'));
    fireEvent.change(screen.getByTestId('form-prompt'), { target: { value: 'Change it now' } });
    expect(screen.getByTestId('form-check-schema').getAttribute('data-state')).toBe('pending');
    fireEvent.click(screen.getByTestId('form-save'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')!.body).toMatchObject({
      purpose: { en: 'The agent asks once. Else it nags.', zh: '只问一次。否则烦人。' },
      scenario: { prompt: 'Change it now', system: 'swarmai' }, trajectory_match: 'in_order', allowed_tools: ['Read', 'Grep'],
    });
  });

  it('editing my case PUTs it without an id in the body, and a rejected save shows the backend reason', async () => {
    routes['GET /eval/golden-set/MINE_1'] = { ...gc('MINE_1', { _origin: 'private', release_tier: 'watch',
      verification: { command: 'echo ok', expected_contains: 'ok', negative_command: 'echo no', negative_expected_contains: 'no' } }), history: [] };
    failNext['PUT /eval/golden-set/MINE_1'] = { status: 400, detail: 'duplicate: same command as Q_X' };
    show(<GoldenCasesTab />);
    fireEvent.click(await screen.findByTestId('case-row-MINE_1'));
    fireEvent.click(await screen.findByTestId('case-edit'));
    expect((screen.getByTestId('form-id') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByTestId('form-command') as HTMLInputElement).value).toBe('echo ok');
    fireEvent.click(screen.getByTestId('form-save'));
    expect((await screen.findByTestId('form-error')).textContent).toContain('duplicate: same command as Q_X');
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.url).toBe('/eval/golden-set/MINE_1');
    expect(put.body).toMatchObject({ eval_method: 'programmatic', verification: { command: 'echo ok', negative_command: 'echo no' } });
    expect((put.body as Record<string, unknown>).id).toBeUndefined();
    expect(screen.getByTestId('case-form')).toBeTruthy();   // a rejected save keeps the form open
  });
});
