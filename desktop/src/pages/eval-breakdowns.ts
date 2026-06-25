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

// All four facets bucket a missing/null/undefined value as "(unset)" rather
// than dropping it or letting a null key reach localeCompare (which would throw
// during the sort). The backend does not schema-validate category/tier/dimension,
// so a hand-edited YAML case can carry null — the fallback keeps the count honest
// and the render crash-free.
const UNSET = '(unset)';

export function computeBreakdowns(cases: GoldenSetCase[]): Breakdowns {
  return {
    category: countBy(cases, (c) => c.category || UNSET),
    tier: countBy(cases, (c) => c.tier || UNSET),
    eval_method: countBy(cases, (c) => c.eval_method || UNSET),
    dimension: countBy(cases, (c) => c.dimension || UNSET),
  };
}
