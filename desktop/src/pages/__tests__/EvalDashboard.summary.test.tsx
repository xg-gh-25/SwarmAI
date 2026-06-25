/**
 * EvalDashboard Golden Set summary — tests for the client-side breakdown helper
 * and the clickable summary chips + tier filter.
 *
 * Invariants under test:
 *  - computeBreakdowns counts are derived from cases[] (anti-drift), never hardcoded
 *  - missing eval_method is bucketed as "(unset)", never dropped (5/153 real cases)
 *  - dimension enum is data-driven (live data has an undocumented 6th value "utility")
 *  - empty input → all-empty breakdowns, no crash
 */
import { describe, it, expect } from 'vitest';
import { computeBreakdowns } from '../EvalDashboard';
import type { GoldenSetCase } from '../EvalDashboard';

function mkCase(over: Partial<GoldenSetCase>): GoldenSetCase {
  return {
    id: 'X',
    category: 'compliance',
    dimension: 'compliance',
    level: 'session',
    title: 't',
    tier: 'active',
    evaluators: [],
    affected_by: [],
    last_result: null,
    ...over,
  };
}

describe('computeBreakdowns', () => {
  it('counts cases per category, tier, eval_method, dimension from the data', () => {
    const cases: GoldenSetCase[] = [
      mkCase({ id: 'a', category: 'compliance', tier: 'active', eval_method: 'llm', dimension: 'compliance' }),
      mkCase({ id: 'b', category: 'compliance', tier: 'stable', eval_method: 'programmatic', dimension: 'capability' }),
      mkCase({ id: 'c', category: 'decision', tier: 'active', eval_method: 'llm', dimension: 'judgment_quality' }),
    ];
    const b = computeBreakdowns(cases);
    expect(b.category).toEqual([
      { key: 'compliance', count: 2 },
      { key: 'decision', count: 1 },
    ]);
    expect(b.tier).toEqual([
      { key: 'active', count: 2 },
      { key: 'stable', count: 1 },
    ]);
    expect(b.eval_method).toEqual([
      { key: 'llm', count: 2 },
      { key: 'programmatic', count: 1 },
    ]);
    // dimension sorted by count desc then key asc → all count 1, alpha order
    expect(b.dimension).toEqual([
      { key: 'capability', count: 1 },
      { key: 'compliance', count: 1 },
      { key: 'judgment_quality', count: 1 },
    ]);
  });

  it('buckets missing eval_method as "(unset)" and never drops it', () => {
    const cases: GoldenSetCase[] = [
      mkCase({ id: 'a', eval_method: 'llm' }),
      mkCase({ id: 'b' }), // no eval_method
      mkCase({ id: 'c', eval_method: undefined }),
    ];
    const b = computeBreakdowns(cases);
    const total = b.eval_method.reduce((s, e) => s + e.count, 0);
    expect(total).toBe(3); // nothing dropped
    expect(b.eval_method).toContainEqual({ key: '(unset)', count: 2 });
    expect(b.eval_method).toContainEqual({ key: 'llm', count: 1 });
  });

  it('is data-driven for dimension (surfaces undocumented values like "utility")', () => {
    const cases: GoldenSetCase[] = [
      mkCase({ id: 'a', dimension: 'utility' }),
      mkCase({ id: 'b', dimension: 'utility' }),
      mkCase({ id: 'c', dimension: 'compliance' }),
    ];
    const b = computeBreakdowns(cases);
    expect(b.dimension[0]).toEqual({ key: 'utility', count: 2 });
  });

  it('sorts by count descending, then key ascending for ties', () => {
    const cases: GoldenSetCase[] = [
      mkCase({ id: '1', category: 'b' }),
      mkCase({ id: '2', category: 'a' }),
      mkCase({ id: '3', category: 'a' }),
      mkCase({ id: '4', category: 'c' }),
    ];
    const b = computeBreakdowns(cases);
    expect(b.category).toEqual([
      { key: 'a', count: 2 },
      { key: 'b', count: 1 },
      { key: 'c', count: 1 },
    ]);
  });

  it('returns empty breakdowns for empty input without crashing', () => {
    const b = computeBreakdowns([]);
    expect(b.category).toEqual([]);
    expect(b.tier).toEqual([]);
    expect(b.eval_method).toEqual([]);
    expect(b.dimension).toEqual([]);
  });
});
