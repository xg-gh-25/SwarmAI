/**
 * OS Eval overlay — pure derivations from the API data (no React), so each is
 * testable on its own: ISO weeks, the five dimensions' series and headlines, the
 * four release checks in display form.
 *
 * Rule: never average measures that count different things. A dimension's
 * headline is the release-tier count when it has gating cases, otherwise its
 * weakest watched measure, named.
 */
import type { DimensionSummary, EvalRun, OnlineScoring, ReleaseCheck } from './api';

/** ISO week label ``YYYY-Www`` of a date, as the backend's session_quality writes it. */
export function isoWeek(d: Date): string {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

/** The last ``n`` ISO weeks ending with the one ``now`` is in, oldest first. */
export function lastWeeks(now: Date, n = 12): string[] {
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i--) out.push(isoWeek(new Date(now.getTime() - i * 7 * 86400000)));
  return out;
}

export type TierCounts = { passed: number; total: number };

/** Passed / total for the gating tiers (red + gate) or the watch tier of one dimension. */
export function tierCounts(summary: DimensionSummary | undefined, dim: string, tiers: string[]): TierCounts {
  const byTier = summary?.[dim] ?? {};
  let passed = 0;
  let total = 0;
  for (const t of tiers) {
    const c = byTier[t];
    if (!c) continue;
    passed += c.passed;
    total += c.passed + c.failed + c.not_run;
  }
  return { passed, total };
}
export const GATING = ['red', 'gate'];
export const WATCH = ['watch'];

/** One point per week: the pass share of the newest quick or full run of that week
 *  with a dimension summary (runs are newest first). A probe covers only the cases
 *  it ran, so it is no point on a trend. */
export function runSeries(runs: EvalRun[], dim: string, tiers: string[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of runs) {
    if (!r.dimension_summary || !r.triggered_at || r.line === 'probe') continue;
    const wk = isoWeek(new Date(r.triggered_at));
    if (wk in out) continue;
    const c = tierCounts(r.dimension_summary, dim, tiers);
    if (c.total) out[wk] = c.passed / c.total;
  }
  return out;
}

/** An online check as a share of turns / sessions that did it right, per week. */
export function onlineSeries(online: OnlineScoring | undefined, check: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const w of online?.trend ?? []) {
    const rate = w.rates?.[check];
    if (typeof rate === 'number') out[w.week] = 1 - rate;
  }
  return out;
}

export function metricSeries(online: OnlineScoring | undefined, key: 'turn_p50_s' | 'cost_p50_usd'): Record<string, number> {
  const out: Record<string, number> = {};
  for (const w of online?.trend ?? []) {
    const v = w.metrics?.[key];
    if (typeof v === 'number') out[w.week] = v;
  }
  return out;
}

/** The two latest values of a series, by week order. */
export function lastTwo(points: Record<string, number>, weeks: string[]): [number | undefined, number | undefined] {
  const ks = weeks.filter((w) => points[w] != null);
  return [points[ks[ks.length - 2]], points[ks[ks.length - 1]]];
}

/** Online checks per dimension (backend session_quality CHECKS). */
export const ONLINE_BY_DIM: Record<string, string[]> = {
  quality: ['O3_CORRECTIONS', 'O4_GOAL', 'O5_TOOLS'],
  responsibility: ['O1_LANGUAGE', 'O2_SILENT_TOOLS'],
};

export function checkById(checks: ReleaseCheck[] | undefined, id: string): ReleaseCheck | undefined {
  return (checks ?? []).find((c) => c.id === id);
}

export const formatSeconds = (s: number | null | undefined, zh: boolean) => {
  if (s == null) return '—';
  if (s < 60) return zh ? `${Math.round(s)} 秒` : `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return zh ? `${m} 分 ${r} 秒` : `${m}m ${r}s`;
};
export const formatUsd = (v: number | null | undefined) => (v == null ? '—' : `$${v.toFixed(2)}`);

/** A case whose latest result is anything but a pass. One predicate for the tab
 *  count and the Cases list, so the two never disagree. */
export const notPassing = (c: { tier?: string; last_result?: { status: string } | null }) =>
  c.tier !== 'archived' && !!c.last_result && c.last_result.status !== 'passed';
