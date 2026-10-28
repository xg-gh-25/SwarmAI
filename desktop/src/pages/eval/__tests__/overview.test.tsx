/** Overview answers one decision: the verdict follows the run's release block (an
 *  unknown or missing verdict never reads as releasable) with a one-sentence reason;
 *  the four checks keep their order and a failing one names its cases; "For you"
 *  lists the two follow-up kinds as two groups, each opened for its evidence and
 *  handed to Swarm from there; the five dimensions are one scoreboard. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, within, waitFor } from '@testing-library/react';
import { routes, reset, show, check, fakeApi } from './harness';

vi.mock('../../../services/api', () => fakeApi());
import { OverviewTab } from '../OverviewTab';

const checks = (over: Record<string, Record<string, unknown>> = {}) => [
  check('redline', true, over.redline), check('dimensions', true, over.dimensions),
  check('denominator', true, { selected: 36, with_result: 36, ...over.denominator }), check('record', true, over.record),
];
const health = (release: unknown, extra: Record<string, unknown> = {}) => ({
  release, dimension_summary: {}, total_cases: 46,
  last_run: { run_id: 'r1', triggered_by: 'scheduled', triggered_at: '2026-10-26T10:00:00+00:00', cases_passed: 36, cases_failed: 0, cases_skipped: 0 },
  follow_ups: [], evolution: [], ...extra,
});

beforeEach(() => {
  reset();
  routes['GET /eval/history'] = [];
  routes['GET /eval/session-quality'] = { trend: [], checks: {}, low_details: [], sessions_checked: 0 };
});

describe('Overview', () => {
  it.each([
    ['release', 'Releasable.'],
    ['blocked', 'Not releasable.'],
    ['incomplete', 'No verdict yet.'],
  ])('a %s verdict reads as %s', async (verdict, text) => {
    routes['GET /eval/health'] = health({ verdict, reason: 'x', checks: checks() });
    show(<OverviewTab />);
    expect((await screen.findByTestId('overview-verdict')).textContent).toContain(text);
  });

  it.each([[null], [{ verdict: 'toString', reason: '', checks: [] }]])('a missing or unknown verdict is an old record, never a release (%#)', async (release) => {
    routes['GET /eval/health'] = health(release);
    show(<OverviewTab />);
    const v = await screen.findByTestId('overview-verdict');
    expect(v.textContent).toContain('A record from before release decisions.');
    expect(v.textContent).not.toContain('Releasable');
  });

  it('with no quick check or full run yet it says so', async () => {
    routes['GET /eval/health'] = { ...health(null), last_run: null };
    show(<OverviewTab />);
    expect((await screen.findByTestId('overview-verdict')).textContent).toContain('No run yet.');
  });

  it('shows the four checks in order, each with its own state', async () => {
    routes['GET /eval/health'] = health({ verdict: 'incomplete', reason: 'x', checks: checks({
      denominator: { passed: false, with_result: 35, cases: [{ id: 'Q_SLOW', status: 'timeout' }] },
    }) });
    show(<OverviewTab />);
    const box = await screen.findByTestId('release-checks');
    const ids = Array.from(box.querySelectorAll('[data-testid^="release-check-"]')).map((e) => e.getAttribute('data-testid')).filter((x) => !x?.endsWith('-detail'));
    expect(ids).toEqual(['release-check-redline', 'release-check-dimensions', 'release-check-denominator', 'release-check-record']);
    expect(screen.getByTestId('release-check-denominator').getAttribute('data-passed')).toBe('false');
    const den = screen.getByTestId('release-check-denominator-detail');
    expect(den.textContent).toContain('35/36');
    expect(den.textContent).toContain('Q_SLOW (Timed out)');
    expect(screen.queryByTestId('release-check-redline-detail')).toBeNull();
    expect(screen.getByTestId('overview-lede').textContent).toContain('1 release-blocking case(s) have no result.');
    expect(screen.getByTestId('release-check-redline').getAttribute('data-passed')).toBe('true');
  });

  it('says why it is releasable in one sentence, naming the watched cases that fail', async () => {
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks({
      record: { known_issues: [{ id: 'W1' }, { id: 'W2' }] } }) });
    show(<OverviewTab />);
    expect((await screen.findByTestId('overview-lede')).textContent).toBe('All 36 release-blocking cases pass. 2 watched case(s) fail; they do not block.');
  });

  it('lists eval problems and behaviour signals as two groups; a row opens its evidence, handed to Swarm from there', async () => {
    routes['GET /eval/golden-set'] = { total_cases: 1, cases: [{ id: 'X', category: 'recall', dimension: 'cognition', tier: 'active',
      title: 'Recall finds memory', affected_by: [], evaluators: [], last_result: null }] };
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks() }, {
      follow_ups: [{ kind: 'eval', severity: 'high', rule: 1, what: { en: 'Case X has no teeth' }, why: '[decision-judge conf=0.9] raw', action: { en: 'Check the case' }, case_ids: ['X'] }],
      evolution: [{ kind: 'evolution', severity: 'medium', rule: 4, what: { en: 'Silent tool calls rising' }, why: 'w', action: { en: 'a' }, case_ids: [] }],
    });
    const { actions, go } = show(<OverviewTab />);
    const needs = await screen.findAllByTestId('needs-item');
    const evo = screen.getAllByTestId('evolution-item');
    expect(needs).toHaveLength(1);
    expect(needs[0].textContent).toContain('Case X has no teeth');
    expect(needs[0].textContent).toContain('Check the case');
    expect(needs[0].textContent).not.toContain('decision-judge');   // the evidence is behind "Look"
    expect(evo).toHaveLength(1);
    expect(screen.getByText('2 items, most important first')).toBeTruthy();
    fireEvent.click(within(needs[0]).getByText(/Look/));
    const panel = await screen.findByTestId('follow-up-detail');
    expect(panel.textContent).toContain('[decision-judge conf=0.9] raw');
    fireEvent.click(within(panel).getByText('Ask Swarm'));
    expect(actions.askSwarm).toHaveBeenCalledWith(expect.stringContaining('Case X has no teeth (X)'));
    fireEvent.click(await within(panel).findByText(/Recall finds memory/));
    expect(go).toHaveBeenCalledWith('cases', 'case:X');
  });

  it('a follow-up that lists cases shows its headline, with the case names underneath', async () => {
    routes['GET /eval/golden-set'] = { total_cases: 2, cases: [
      { id: 'A', category: 'decision', dimension: 'quality', tier: 'active', title: 'Small change goes native', affected_by: [], evaluators: [], last_result: null },
      { id: 'B', category: 'decision', dimension: 'quality', tier: 'active', purpose: { en: 'Reads MEMORY.md before answering. Else it guesses.' }, affected_by: [], evaluators: [], last_result: null }] };
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks() }, {
      follow_ups: [{ kind: 'eval', severity: 'medium', rule: 3, case_ids: ['A', 'B'], why: 'w', action: { en: 'Check the case first' },
        what: { en: '2 new offline case(s) fail on their first run: Small change goes native; Reads MEMORY.md before answering.' },
        headline: { en: '2 new offline cases fail on the first run' } }],
    });
    show(<OverviewTab />);
    const [row] = await screen.findAllByTestId('needs-item');
    await waitFor(() => expect(row.textContent).toContain('Small change goes native; Reads MEMORY.md before answering.'));
    expect(row.textContent).toContain('2 new offline cases fail on the first run');
    expect(row.textContent).not.toContain('case(s)');
    expect(row.textContent).not.toContain('Check the case first');   // the action is in the panel
  });

  it('says so when nothing needs you', async () => {
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks() });
    show(<OverviewTab />);
    expect(await screen.findByTestId('nothing-for-you')).toBeTruthy();
  });

  it('scores each dimension by its release-blocking cases and names the weakest watched signal', async () => {
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks() }, { dimension_summary: {
      cognition: { gate: { passed: 21, failed: 0, not_run: 0 }, watch: { passed: 3, failed: 2, not_run: 0 } },
      responsibility: { red: { passed: 6, failed: 0, not_run: 0 }, gate: { passed: 7, failed: 0, not_run: 0 } },
    } });
    routes['GET /eval/session-quality'] = { trend: [], low_details: [], sessions_checked: 33, checks: {
      O1_LANGUAGE: { checked: 512, failed: 43, rate: 0.084, examples: [] },
      O2_SILENT_TOOLS: { checked: 463, failed: 189, rate: 0.408, examples: [] } } };
    show(<OverviewTab />);
    expect((await screen.findByTestId('dimension-cognition-head')).textContent).toBe('21/21');
    expect(screen.getByTestId('dimension-cognition-sub').textContent).toBe('2 of 5 watched cases fail');
    expect(screen.getByTestId('dimension-responsibility-head').textContent).toBe('13/13');
    await waitFor(() => expect(screen.getByTestId('dimension-responsibility-sub').textContent).toBe('No text before a tool call 59%'));
    expect(screen.getByTestId('dimension-cost-head').textContent).toBe('no data yet');
  });
});
