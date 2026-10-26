/** Overview answers one decision: the verdict sentence follows the run's release
 *  block (an unknown or missing verdict never reads as "can be released"), the four
 *  checks keep their order, and "needs you" and Evolution Suggestion are two lists
 *  from the two follow-up kinds. */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
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
    ['release', 'Can be released.'],
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
    expect(v.textContent).not.toContain('Can be released');
  });

  it('shows the four checks in order, each with its own state', async () => {
    routes['GET /eval/health'] = health({ verdict: 'incomplete', reason: 'x', checks: checks({
      denominator: { passed: false, with_result: 35, cases: [{ id: 'Q_SLOW', status: 'timeout' }] },
    }) });
    show(<OverviewTab />);
    const box = await screen.findByTestId('release-checks');
    const ids = Array.from(box.querySelectorAll('[data-testid^="release-check-"]')).map((e) => e.getAttribute('data-testid'));
    expect(ids).toEqual(['release-check-redline', 'release-check-dimensions', 'release-check-denominator', 'release-check-record']);
    const den = screen.getByTestId('release-check-denominator');
    expect(den.getAttribute('data-passed')).toBe('false');
    expect(den.textContent).toContain('35/36');
    expect(den.textContent).toContain('Q_SLOW timeout');
    expect(screen.getByTestId('release-check-redline').getAttribute('data-passed')).toBe('true');
  });

  it('lists eval problems under "needs you" and behaviour signals under Evolution Suggestion, and hands one to Swarm', async () => {
    routes['GET /eval/health'] = health({ verdict: 'release', reason: 'x', checks: checks() }, {
      follow_ups: [{ kind: 'eval', severity: 'high', rule: 1, what: { en: 'Case X has no teeth' }, why: 'w', action: { en: 'a' }, case_ids: ['X'] }],
      evolution: [{ kind: 'evolution', severity: 'medium', rule: 4, what: { en: 'Silent tool calls rising' }, why: 'w', action: { en: 'a' }, case_ids: [] }],
    });
    const { actions } = show(<OverviewTab />);
    const needs = await screen.findAllByTestId('needs-item');
    const evo = screen.getAllByTestId('evolution-item');
    expect(needs).toHaveLength(1);
    expect(needs[0].textContent).toContain('Case X has no teeth');
    expect(evo).toHaveLength(1);
    expect(evo[0].textContent).toContain('Silent tool calls rising');
    expect((await screen.findByTestId('overview-verdict')).textContent).toContain('1 thing needs you');
    fireEvent.click(within(needs[0]).getByText('Ask Swarm'));
    expect(actions.askSwarm).toHaveBeenCalledWith(expect.stringContaining('Case X has no teeth (X)'));
  });
});
