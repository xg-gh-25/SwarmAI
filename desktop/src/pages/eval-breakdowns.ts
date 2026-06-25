/**
 * Golden Set summary breakdown helper.
 *
 * Counts golden cases along four facets, computed live from cases[] so the
 * summary never drifts from what is actually loaded. Missing eval_method is
 * bucketed (not dropped); dimension is data-driven (live data carries values
 * not in the static enum, e.g. "utility"). Sorted by count desc, then key asc.
 *
 * Lives in its own module (not EvalDashboard.tsx) so the dashboard file only
 * exports components (react-refresh/only-export-components).
 */
import type { GoldenSetCase } from './EvalDashboard';

export type BreakdownEntry = { key: string; count: number };

export interface Breakdowns {
  category: BreakdownEntry[];
  tier: BreakdownEntry[];
  eval_method: BreakdownEntry[];
  dimension: BreakdownEntry[];
}

function countBy(cases: GoldenSetCase[], pick: (c: GoldenSetCase) => string): BreakdownEntry[] {
  const counts = new Map<string, number>();
  for (const c of cases) {
    const key = pick(c);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
}

export function computeBreakdowns(cases: GoldenSetCase[]): Breakdowns {
  return {
    category: countBy(cases, (c) => c.category),
    tier: countBy(cases, (c) => c.tier),
    eval_method: countBy(cases, (c) => c.eval_method || '(unset)'),
    dimension: countBy(cases, (c) => c.dimension),
  };
}
