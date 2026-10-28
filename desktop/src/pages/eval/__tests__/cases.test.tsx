/** Cases: tiers come from the backend's release_tier (missing = watch, never gate);
 *  the cases not passing come first with the reason from their run, the passing ones
 *  folded by category; a case opened from a cross-link opens on the right; an offline
 *  case drills down to what it was asked, what it did and its answer; a product case
 *  is read-only, deleting one of your own needs a second click and calls DELETE, and
 *  the form sends the shape the backend admits and shows each admission check's own
 *  result. Run history: the release record and a started or crashed full run. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { routes, reset, show, calls, check, failNext, fakeApi } from './harness';

vi.mock('../../../services/api', () => fakeApi());
vi.mock('../../../utils/openExternal', () => ({ openExternal: vi.fn() }));
import { CasesTab } from '../CasesTab';

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

/** A passing or never-run case's row is folded in its category: open the group first. */
async function row(id: string) {
  await screen.findByTestId('eval-cases');
  const hit = screen.queryByTestId(`case-row-${id}`);
  if (hit) return hit;
  for (const g of screen.queryAllByTestId(/^(not-run-)?group-/)) {
    const head = g.querySelector('button[aria-expanded="false"]');
    if (head) fireEvent.click(head);
    const r = screen.queryByTestId(`case-row-${id}`);
    if (r) return r;
  }
  throw new Error(`no row ${id}`);
}

describe('Cases', () => {
  it('counts tiers from release_tier; a case without one is watched, and archived cases are not shown', async () => {
    show(<CasesTab />);
    expect((await screen.findByTestId('tier-summary')).textContent).toContain('2 block a release, 3 are watched.');
    expect((await row('NO_TIER')).querySelector('[data-tier]')?.getAttribute('data-tier')).toBe('watch');
    expect(screen.queryByTestId('case-row-OLD_1')).toBeNull();
  });

  it('lists the cases not passing first, with the reason from their run, and folds the passing ones', async () => {
    routes['GET /eval/runs/r'] = { run_id: 'r', cases: [{ id: 'GATE_1', status: 'failed',
      notes: 'Read occurred but decision went the WRONG way — [decision-judge conf=0.90] The answer proposes the pipeline.. All 1 steps found' }] };
    show(<CasesTab />);
    const bad = await screen.findByTestId('not-passing');
    await waitFor(() => expect(within(bad).getByTestId('case-row-GATE_1').textContent).toContain('Failed · 10-26'));
    await waitFor(() => expect(within(bad).getByTestId('case-row-GATE_1').textContent).toContain('The answer proposes the pipeline.'));
    expect(within(bad).getByTestId('case-row-GATE_1').textContent).not.toContain('decision-judge');
    expect(screen.getByTestId('cases-head').textContent).toContain('1 not passing');
    expect(screen.queryByTestId('case-row-RED_1')).toBeNull();          // folded
    expect(screen.getByTestId('not-run-group-recall').textContent).toContain('3');
    fireEvent.click(screen.getByTestId('cases-line-offline'));
    expect(screen.queryByTestId('not-passing')).toBeNull();
    expect(screen.queryByTestId('not-run-group-recall')).toBeNull();
    expect(screen.getByTestId('not-run-group-decision')).toBeTruthy();
  });

  it('a case that never ran is not counted as passing, and a search that matches nothing says so', async () => {
    routes['GET /eval/golden-set'] = { total_cases: 2, cases: [
      gc('RUN_1', { last_result: { status: 'passed', run_id: 'r', triggered_at: '2026-10-26T10:00:00Z' } }),
      gc('NEW_1'),
    ] };
    show(<CasesTab />);
    expect((await screen.findByTestId('cases-head')).textContent).toContain('1 not run yet');
    expect(screen.getByTestId('passing').textContent).toContain('1');
    expect(screen.getByTestId('not-run').textContent).toContain('Not run yet');
    expect(screen.getByTestId('not-run').querySelector('[data-testid^="not-run-group-"]')).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('Search name, id or tier…'), { target: { value: 'zzz' } });
    expect(screen.getByTestId('cases-no-match').textContent).toContain('No case matches “zzz”.');
    fireEvent.click(within(screen.getByTestId('cases-no-match')).getByText('Clear the filter'));
    expect(screen.queryByTestId('cases-no-match')).toBeNull();
    expect(screen.getByTestId('passing')).toBeTruthy();
  });

  it('a case named in a cross-link opens on the right', async () => {
    routes['GET /eval/golden-set/RED_1'] = { ...gc('RED_1', { release_tier: 'red' }), history: [] };
    show(<CasesTab focus="case:RED_1" />);
    expect((await screen.findByTestId('case-detail')).textContent).toContain('RED_1 protects something.');
  });

  it('an offline case drills down to what it was asked, what it did and its answer', async () => {
    routes['GET /eval/golden-set/OFF_1'] = { ...gc('OFF_1', { eval_method: 'behavior', release_tier: 'watch', category: 'decision',
      scenario: { prompt: 'Fix the typo.' }, expected_trajectory: ['Read README'] }),
      history: [{ run_id: 'full1', triggered_at: '2026-10-28T02:35:00+00:00', status: 'failed', notes: 'x — [decision-judge conf=0.85] It proposes the pipeline.' },
                { run_id: 'full0', triggered_at: '2026-10-26T11:00:00+00:00', status: 'passed' }] };
    routes['GET /eval/runs/full1'] = { run_id: 'full1', cases: [{ id: 'OFF_1', status: 'failed',
      expected_check: [{ step: 'Read README', found: false }],
      steps: [{ tool: 'Read', input: '{"file_path": "/tmp/swarm-eval-ws-1/workspace/Projects/Demo/app/README.md"}' }, { tool: 'Bash', input: 'ls', is_error: true, output: 'ls: no such dir' }],
      final_text: 'I found the typo, but I could not fix it.' }] };
    show(<CasesTab />);
    fireEvent.click(await row('OFF_1'));
    const detail = await screen.findByTestId('case-detail');
    expect(detail.textContent).toContain('Fix the typo.');
    expect(screen.getByTestId('case-latest').textContent).toContain('It proposes the pipeline.');
    expect(screen.getByTestId('case-latest').textContent).toContain('passed on 10-26');
    const dd = screen.getByTestId('drilldown');
    await waitFor(() => expect(dd.textContent).toContain('Read Projects/Demo/app/README.md'));
    expect(dd.textContent).not.toContain('swarm-eval-ws');
    expect(dd.textContent).toContain('Expected but not seen: Read README');
    expect(within(dd).getByTestId('step-error').textContent).toContain('2. Bash ls · returned an error: ls: no such dir');
    expect(screen.getByTestId('case-latest').textContent).toContain('Failed');
    expect(dd.textContent).toContain('I found the typo');
  });

  it('a product case can be copied, not edited or deleted', async () => {
    routes['GET /eval/golden-set/RED_1'] = { ...gc('RED_1', { release_tier: 'red',
      verification: { command: 'probe x', expected_contains: 'X_OK', negative_command: 'probe x negative',
                      negative_expected_contains: 'X_TEETH', timeout: 30, grep: '', corpus: 'docs' } }), history: [] };
    show(<CasesTab />);
    fireEvent.click(await row('RED_1'));
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
    show(<CasesTab />);
    fireEvent.click(await row('MINE_1'));
    fireEvent.click(await screen.findByTestId('case-delete'));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    // the safe answer has focus, and "keep it" takes the question back
    expect(document.activeElement).toBe(screen.getByTestId('case-delete-confirm-cancel'));
    fireEvent.click(screen.getByTestId('case-delete-confirm-cancel'));
    expect(screen.queryByTestId('case-delete-confirm')).toBeNull();
    fireEvent.click(screen.getByTestId('case-delete'));
    fireEvent.click(screen.getByTestId('case-delete-confirm-ok'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/eval/golden-set/MINE_1')).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('case-panel')).toBeNull());
  });

  it('an offline case is sent as a behaviour case with its prompt, steps and rubric; "try" shows each check', async () => {
    routes['POST /eval/golden-set/try'] = { ok: false, trial: null, checks: [
      { name: 'schema', ok: true, errors: [] }, { name: 'offline', ok: false, errors: ['rubric has no FAIL side'] },
    ] };
    show(<CasesTab />);
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
    show(<CasesTab />);
    fireEvent.click(await row('MINE_2'));
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
    show(<CasesTab />);
    fireEvent.click(await row('MINE_1'));
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

const fullRuns = () => [
  { run_id: 'probe1', line: 'probe', triggered_by: 'manual', triggered_at: '2026-10-26T12:00:00+00:00', release: 'release', release_reason: '1/1', total_cases: 1, cases_passed: 1, cases_failed: 0, cases_skipped: 0, duration_seconds: 9 },
  { run_id: 'canary1', line: 'quick', triggered_by: 'canary', triggered_at: '2026-10-26T09:00:00+00:00', release: 'release', total_cases: 36, cases_passed: 36, cases_failed: 0, cases_skipped: 0, duration_seconds: 9 },
  { run_id: 'full1', line: 'full', triggered_by: 'scheduled', triggered_at: '2026-10-20T03:00:00+00:00', release: 'blocked', release_reason: 'gate failed: Q_X',
    release_checks: [check('redline', true), check('dimensions', false, { cases: [{ id: 'Q_X' }] }), check('denominator', true, { selected: 36, with_result: 36 }), check('record', true)],
    report: '2026-10-20.html', total_cases: 46, cases_passed: 44, cases_failed: 2, cases_skipped: 0, duration_seconds: 900 },
  { run_id: 'old1', line: 'full', triggered_by: 'scheduled', triggered_at: '2026-09-01T03:00:00+00:00', release: null, total_cases: 158, cases_passed: 150, cases_failed: 8, cases_skipped: 0, duration_seconds: 900 },
];
const emptyHealth = { release: null, dimension_summary: {}, last_run: null, total_cases: 0 };

describe('Cases: a layer keeps focus and what the user typed', () => {
  it('focus moves into the panel, Tab stays inside, and Escape gives it back to the row', async () => {
    routes['GET /eval/golden-set/RED_1'] = { ...gc('RED_1', { release_tier: 'red' }), history: [] };
    show(<CasesTab />);
    const opener = (await row('RED_1')) as HTMLElement;
    opener.focus();
    fireEvent.click(opener);
    const panel = await screen.findByTestId('case-panel');
    await screen.findByTestId('case-detail');
    expect(panel.getAttribute('aria-modal')).toBe('true');
    expect(panel.contains(document.activeElement)).toBe(true);
    const items = Array.from(panel.querySelectorAll<HTMLElement>('button:not([disabled]), summary'));
    items[items.length - 1].focus();
    fireEvent.keyDown(panel, { key: 'Tab' });
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(panel, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(items[items.length - 1]);
    // the overlay host listens on document (bubble): Escape pressed inside the panel
    // must stop at the panel
    const outer = vi.fn();
    document.addEventListener('keydown', outer);
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    document.removeEventListener('keydown', outer);
    expect(screen.queryByTestId('case-panel')).toBeNull();
    expect(outer).not.toHaveBeenCalled();          // only the panel closed, not the overlay
    expect(document.activeElement).toBe(opener);
  });

  it('a form with unsaved input asks before Escape, ✕ or a click outside drops it', async () => {
    show(<CasesTab />);
    fireEvent.click(await screen.findByTestId('add-case'));
    await screen.findByTestId('case-form');
    fireEvent.keyDown(window, { key: 'Escape' });                 // nothing typed: closes at once
    expect(screen.queryByTestId('case-panel')).toBeNull();
    fireEvent.click(screen.getByTestId('add-case'));
    fireEvent.change(await screen.findByTestId('form-id'), { target: { value: 'MY_CASE' } });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(await screen.findByTestId('discard')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByTestId('discard-cancel'));
    fireEvent.keyDown(window, { key: 'Escape' });                 // Escape on the question = keep editing
    expect(screen.queryByTestId('discard')).toBeNull();
    expect((screen.getByTestId('form-id') as HTMLInputElement).value).toBe('MY_CASE');
    fireEvent.click(screen.getByLabelText('Close'));
    fireEvent.click(screen.getByTestId('discard-cancel'));
    expect((screen.getByTestId('form-id') as HTMLInputElement).value).toBe('MY_CASE');
    fireEvent.click(screen.getByTestId('case-panel').parentElement!);   // the backdrop
    fireEvent.click(screen.getByTestId('discard-ok'));
    expect(screen.queryByTestId('case-panel')).toBeNull();
  });

  it('switching the kind of case keeps what was typed', async () => {
    show(<CasesTab />);
    fireEvent.click(await screen.findByTestId('add-case'));
    fireEvent.change(await screen.findByTestId('form-id'), { target: { value: 'MY_CASE' } });
    fireEvent.click(screen.getByTestId('form-type-quick'));
    expect(await screen.findByTestId('form-command')).toBeTruthy();
    expect((screen.getByTestId('form-id') as HTMLInputElement).value).toBe('MY_CASE');
    fireEvent.keyDown(screen.getByTestId('form-type-quick'), { key: 'ArrowLeft' });   // arrow keys move the choice
    expect(await screen.findByTestId('form-prompt')).toBeTruthy();
  });

  it('a drill-down that could not load says so, not "no tool steps"', async () => {
    routes['GET /eval/golden-set/OFF_1'] = { ...gc('OFF_1', { eval_method: 'behavior', release_tier: 'watch', category: 'decision' }),
      history: [{ run_id: 'full1', triggered_at: '2026-10-28T02:35:00+00:00', status: 'failed', notes: 'x' }] };
    failNext['GET /eval/runs/full1'] = { status: 500, detail: 'boom' };
    show(<CasesTab />);
    fireEvent.click(await row('OFF_1'));
    const dd = await screen.findByTestId('drilldown');
    await waitFor(() => expect(dd.textContent).toContain('Could not load this part.'));
    expect(dd.textContent).not.toContain('kept no tool steps');
  });

  it('the Run menu takes focus, closes on Escape without closing the overlay, and gives focus back', async () => {
    show(<CasesTab />);
    const trigger = await screen.findByTestId('run-menu');
    trigger.focus();
    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByTestId('run-quick').parentElement!.contains(document.activeElement)).toBe(true);
    const outer = vi.fn();
    document.addEventListener('keydown', outer);
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    document.removeEventListener('keydown', outer);
    expect(screen.queryByTestId('run-quick')).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('Run history', () => {
  it('the release record lists full runs with a decision; older ones only on request', async () => {
    routes['GET /eval/health'] = emptyHealth;
    routes['GET /eval/history'] = fullRuns();
    show(<CasesTab focus="history" />);
    const rec = await screen.findByTestId('release-record');
    const rows = rec.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('1 failed');
    expect(rows[0].textContent).toContain('36/36');
    // the reason in words from the run's checks; the backend's sentence stays the tooltip
    expect(rows[0].textContent).toContain('1 release-blocking case fails.');
    expect(rows[0].querySelector('[data-testid="release-reason"]')?.getAttribute('title')).toBe('gate failed: Q_X');
    expect(rec.textContent).not.toContain('canary');
    expect(rec.textContent).not.toContain('1/1');
    fireEvent.click(screen.getByText('Show'));
    expect(screen.getByTestId('release-record').querySelectorAll('tbody tr')).toHaveLength(2);
  });

  it('a crashed full run stays in view, and a started one that crashes says so', async () => {
    routes['GET /eval/health'] = emptyHealth;
    routes['GET /eval/history'] = [{ run_id: 'crash1', line: 'full', status: 'failed', error: 'model backend down',
      triggered_by: 'manual', triggered_at: '2026-10-26T03:00:00+00:00', release: null, total_cases: 0,
      cases_passed: 0, cases_failed: 0, cases_skipped: 0, duration_seconds: 0 }];
    routes['POST /eval/run'] = { status: 'started', run_id: 'new2' };
    routes['GET /eval/runs/new2'] = { run_id: 'new2', status: 'failed', error: 'model backend down', cases: [] };
    show(<CasesTab focus="history" />);
    const rec = await screen.findByTestId('release-record');
    expect(rec.textContent).toContain('did not finish');
    expect(rec.textContent).toContain('model backend down');
    fireEvent.click(screen.getByTestId('run-menu'));
    fireEvent.click(screen.getByTestId('run-full'));
    expect((await screen.findByTestId('run-full-failed')).textContent).toContain('model backend down');
  });
});

describe('a started full run', () => {
  const base = (runId: string) => {
    routes['GET /eval/health'] = emptyHealth;
    routes['GET /eval/golden-set'] = { total_cases: 0, cases: [] };
    routes['GET /eval/history'] = [];
    routes['POST /eval/run'] = { status: 'started', run_id: runId };
  };
  const start = async () => {
    fireEvent.click(await screen.findByTestId('run-menu'));
    fireEvent.click(screen.getByTestId('run-full'));
  };

  it('is watched until it is recorded, then everything refreshes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      base('new1');
      let n = 0;
      routes['GET /eval/runs/new1'] = () => (++n < 2 ? { run_id: 'new1', status: 'running' } : { run_id: 'new1', cases: [] });
      show(<CasesTab />);
      await start();
      await waitFor(() => expect(calls.some((c) => c.url === '/eval/run')).toBe(true));
      await waitFor(() => expect(screen.getByTestId('run-menu').textContent).toContain('Running…'));
      fireEvent.click(screen.getByTestId('run-menu'));
      expect((screen.getByTestId('run-full') as HTMLButtonElement).disabled).toBe(true);   // no second start
      const before = calls.filter((c) => c.url === '/eval/golden-set').length;
      await vi.advanceTimersByTimeAsync(5500);
      await waitFor(() => expect(screen.getByTestId('run-menu').textContent).not.toContain('Running'));
      await waitFor(() => expect(calls.filter((c) => c.url === '/eval/golden-set').length).toBeGreaterThan(before));   // the list refreshes
    } finally {
      vi.useRealTimers();
    }
  });

  it('stops at an unknown run instead of polling it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      base('stuck1');
      failNext['GET /eval/runs/stuck1'] = { status: 404, detail: 'Run not found' };
      show(<CasesTab />);
      await start();
      await waitFor(() => expect(calls.some((c) => c.url === '/eval/runs/stuck1')).toBe(true));
      await vi.advanceTimersByTimeAsync(20_000);
      expect(calls.filter((c) => c.url === '/eval/runs/stuck1')).toHaveLength(1);
      expect(screen.getByTestId('run-menu').textContent).not.toContain('Running');
    } finally {
      vi.useRealTimers();
    }
  });

  it('gives up after 30 minutes on a run killed mid-flight (its marker says running forever)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      base('stuck1');
      routes['GET /eval/runs/stuck1'] = { run_id: 'stuck1', status: 'running' };
      show(<CasesTab />);
      await start();
      await waitFor(() => expect(screen.getByTestId('run-menu').textContent).toContain('Running…'));
      await vi.advanceTimersByTimeAsync(30 * 60_000 + 1000);
      await waitFor(() => expect(screen.getByTestId('run-menu').textContent).not.toContain('Running'));
      const polls = calls.filter((c) => c.url === '/eval/runs/stuck1').length;
      await vi.advanceTimersByTimeAsync(20_000);
      expect(calls.filter((c) => c.url === '/eval/runs/stuck1').length).toBe(polls);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('Cases: one layer mechanism for focus and Escape', () => {
  const offline = () => ({ ...gc('OFF_1', { eval_method: 'behavior', release_tier: 'watch', category: 'decision',
    decision_rubric: 'PASS only if x; FAIL if y', expected_trajectory: ['Read A'] }), history: [] });

  it('Tab skips controls inside a closed section, and brings focus back when it fell to the page', async () => {
    routes['GET /eval/golden-set/OFF_1'] = offline();
    show(<CasesTab />);
    fireEvent.click(await row('OFF_1'));
    const panel = await screen.findByTestId('case-panel');
    await screen.findByTestId('case-detail');
    const how = within(screen.getByTestId('case-how')).getByText('How it is checked');
    how.focus();                                   // the last control a person can reach
    fireEvent.keyDown(how, { key: 'Tab' });
    // wraps to the first control; a hidden "See the judge" link inside the closed
    // section is not counted as the last one
    expect(document.activeElement).toBe(screen.getByLabelText('Close'));
    (document.activeElement as HTMLElement).blur();  // a focused control was removed: focus is on <body>
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: 'Tab' });
    expect(panel.contains(document.activeElement)).toBe(true);
  });

  it('Escape on the delete question cancels the question, not the panel, and focus goes back to Delete', async () => {
    routes['GET /eval/golden-set/MINE_1'] = { ...gc('MINE_1', { _origin: 'private', release_tier: 'watch' }), history: [] };
    show(<CasesTab />);
    fireEvent.click(await row('MINE_1'));
    const del = await screen.findByTestId('case-delete');
    del.focus();
    fireEvent.click(del);
    expect(screen.getByTestId('case-delete-confirm')).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByTestId('case-delete-confirm')).toBeNull();
    expect(screen.getByTestId('case-panel')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByTestId('case-delete'));
  });

  it('"Keep editing" gives focus back to the field, and an Escape that ends an input-method composition is ignored', async () => {
    show(<CasesTab />);
    fireEvent.click(await screen.findByTestId('add-case'));
    const id = await screen.findByTestId('form-id');
    fireEvent.change(id, { target: { value: 'MY_CASE' } });
    id.focus();
    fireEvent.keyDown(id, { key: 'Escape', isComposing: true });
    expect(screen.queryByTestId('discard')).toBeNull();
    fireEvent.keyDown(id, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('discard-cancel'));
    expect(document.activeElement).toBe(screen.getByTestId('form-id'));
  });

  it('a panel opened from another tab (no opener left) gives focus to the tab panel when it closes', async () => {
    const tabpanel = document.createElement('div');
    tabpanel.id = 'eval-scroll'; tabpanel.tabIndex = -1;
    document.body.appendChild(tabpanel);
    routes['GET /eval/golden-set/RED_1'] = { ...gc('RED_1', { release_tier: 'red' }), history: [] };
    show(<CasesTab focus="case:RED_1" />);
    await screen.findByTestId('case-detail');
    fireEvent.click(screen.getByLabelText('Close'));
    expect(document.activeElement).toBe(tabpanel);
    tabpanel.remove();
  });
});

describe('SidePanel reads unsaved input at the moment of the key', () => {
  it('asks before closing when isDirty turns true with no re-render in between', async () => {
    const { SidePanel } = await import('../ui');
    let dirty = false;
    const onClose = vi.fn();
    show(<SidePanel open onClose={onClose} label="Form" isDirty={() => dirty}><input data-testid="x" /></SidePanel>);
    dirty = true;                                   // a keystroke the panel has not re-rendered for
    fireEvent.keyDown(screen.getByTestId('x'), { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('discard')).toBeTruthy();
  });
});
