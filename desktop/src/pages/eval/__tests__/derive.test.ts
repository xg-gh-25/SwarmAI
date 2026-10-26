/** The overlay's pure derivations: ISO weeks match the backend's labels, a week's
 *  point is its newest run, online rates turn into "done right" shares, and times
 *  with a zone are shown in local time. */
import { describe, it, expect } from 'vitest';
import { isoWeek, lastWeeks, onlineSeries, runSeries, tierCounts, metricSeries, lastTwo } from '../derive';
import { when, splitPurpose } from '../ui';
import type { EvalRun, OnlineScoring } from '../api';

const run = (at: string, gatePassed: number, gateTotal: number): EvalRun => ({
  run_id: at, triggered_by: 'scheduled', triggered_at: at, release: 'release', total_cases: 0,
  cases_passed: 0, cases_failed: 0, cases_skipped: 0, duration_seconds: 0,
  dimension_summary: { cognition: { gate: { passed: gatePassed, failed: gateTotal - gatePassed, not_run: 0 },
                                    watch: { passed: 1, failed: 0, not_run: 1 } } },
});

describe('derive', () => {
  it('labels ISO weeks like the backend (year boundary included)', () => {
    expect(isoWeek(new Date(2026, 9, 26))).toBe('2026-W44');
    expect(isoWeek(new Date(2026, 9, 25))).toBe('2026-W43');
    expect(isoWeek(new Date(2027, 0, 1))).toBe('2026-W53');
    const weeks = lastWeeks(new Date(2026, 9, 26));
    expect(weeks).toHaveLength(12);
    expect(weeks[11]).toBe('2026-W44');
    expect(weeks[0]).toBe('2026-W33');
  });

  it('counts gating and watch tiers apart, with not-run in the denominator', () => {
    const s = run('2026-10-26T10:00:00+00:00', 3, 4).dimension_summary;
    expect(tierCounts(s, 'cognition', ['red', 'gate'])).toEqual({ passed: 3, total: 4 });
    expect(tierCounts(s, 'cognition', ['watch'])).toEqual({ passed: 1, total: 2 });
    expect(tierCounts(s, 'quality', ['gate'])).toEqual({ passed: 0, total: 0 });
  });

  it('takes the newest run of a week (runs come newest first)', () => {
    const runs = [run('2026-10-27T10:00:00+00:00', 4, 4), run('2026-10-26T10:00:00+00:00', 1, 4), run('2026-10-20T10:00:00+00:00', 2, 4)];
    expect(runSeries(runs, 'cognition', ['gate'])).toEqual({ '2026-W44': 1, '2026-W43': 0.5 });
  });

  it('turns an online failure rate into the share done right, and reads metrics', () => {
    const on = { trend: [{ week: '2026-W43', rates: { O2_SILENT_TOOLS: 0.25 }, metrics: { turns: 9, turn_p50_s: 120, cost_p50_usd: 1.5 } },
                         { week: '2026-W44', rates: {}, metrics: { turns: 0, turn_p50_s: null, cost_p50_usd: null } }] } as unknown as OnlineScoring;
    expect(onlineSeries(on, 'O2_SILENT_TOOLS')).toEqual({ '2026-W43': 0.75 });
    expect(metricSeries(on, 'turn_p50_s')).toEqual({ '2026-W43': 120 });
    expect(lastTwo({ a: 1, c: 3 }, ['a', 'b', 'c'])).toEqual([1, 3]);
  });

  it('shows a zoned timestamp in local time and a naive one as written', () => {
    const d = new Date('2026-10-26T11:04:00+00:00');
    const p2 = (n: number) => String(n).padStart(2, '0');
    expect(when('2026-10-26T11:04:00+00:00')).toBe(`${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`);
    expect(when('2026-10-26T19:04:58')).toBe('10-26 19:04');
    expect(when(null)).toBe('—');
  });

  it('splits a purpose into what it protects and what breaks', () => {
    expect(splitPurpose('Agent looks it up first. If not, archives are lost.')).toEqual(['Agent looks it up first.', 'If not, archives are lost.']);
    expect(splitPurpose('先去找。坏了意味着归档丢了。')).toEqual(['先去找。', '坏了意味着归档丢了。']);
  });
});
