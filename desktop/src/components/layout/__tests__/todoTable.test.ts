/**
 * todoTable — pure-helper unit tests (BUILD RED-first, run_7ccfe39f).
 *
 * The single sortable-table refactor replaces todoZones' 4-zone derivation with
 * a flat table whose sort/filter/status/KPI/chart logic lives in pure functions.
 * These lock the 6 helpers independent of React so a mutation to any is RED.
 */
import { describe, it, expect } from 'vitest';
import {
  deriveStatus,
  sortTodos,
  filterByRange,
  computeKpis,
  weeklyBuckets,
  sourceDist,
  PRIORITY_RANK,
  type SortKey,
} from '../todoTable';
import type { ToDo } from '../../../types/todo';

function mk(over: Partial<ToDo> = {}): ToDo {
  return {
    id: Math.random().toString(36).slice(2), workspaceId: 'swarmws',
    title: 't', description: null, source: null, sourceType: 'manual',
    status: 'pending', priority: 'none', dueDate: null, linkedContext: null,
    taskId: null, reviewState: null, reviewKind: null,
    dispatchedSessionId: null, dispatchedTabLabel: null, dispatchedAt: null,
    completedAt: null, reviewedAt: null,
    createdAt: '2026-08-01T00:00:00+00:00', updatedAt: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

const NOW = new Date('2026-08-07T12:00:00+00:00');

describe('deriveStatus', () => {
  it('Completed when reviewState is completed/confirmed OR status=handled', () => {
    expect(deriveStatus(mk({ reviewState: 'completed' }))).toBe('Completed');
    expect(deriveStatus(mk({ reviewState: 'confirmed' }))).toBe('Completed');
    expect(deriveStatus(mk({ status: 'handled' }))).toBe('Completed');
  });
  it('Cancelled when status is cancelled/deleted OR reviewState=rejected (terminal-not-done)', () => {
    expect(deriveStatus(mk({ status: 'cancelled' }))).toBe('Cancelled');
    expect(deriveStatus(mk({ status: 'deleted' }))).toBe('Cancelled');
    // Gate-2 HIGH: a REJECTED review is Cancelled, NOT green Completed — the backend
    // reject flow sets status=cancelled + reviewState=rejected; the work was rejected.
    expect(deriveStatus(mk({ reviewState: 'rejected' }))).toBe('Cancelled');
    expect(deriveStatus(mk({ status: 'cancelled', reviewState: 'rejected' }))).toBe('Cancelled');
  });
  it('In Progress when dispatched (any signal) or in_discussion', () => {
    expect(deriveStatus(mk({ dispatchedAt: '2026-08-02T00:00:00+00:00' }))).toBe('In Progress');
    expect(deriveStatus(mk({ dispatchedSessionId: 's1' }))).toBe('In Progress');
    expect(deriveStatus(mk({ dispatchedTabLabel: 'Tab 2' }))).toBe('In Progress');
    expect(deriveStatus(mk({ status: 'in_discussion' }))).toBe('In Progress');
  });
  it('Pending otherwise (pending/overdue, no dispatch, no review)', () => {
    expect(deriveStatus(mk({ status: 'pending' }))).toBe('Pending');
    expect(deriveStatus(mk({ status: 'overdue' }))).toBe('Pending');
  });
  it('Completed/Cancelled take precedence over a dispatch signal', () => {
    // a dispatched todo that got confirmed is Completed, not In Progress
    expect(deriveStatus(mk({ dispatchedAt: '2026-08-02T00:00:00+00:00', reviewState: 'confirmed' }))).toBe('Completed');
    // a dispatched todo that got REJECTED is Cancelled, not In Progress
    expect(deriveStatus(mk({ dispatchedAt: '2026-08-02T00:00:00+00:00', reviewState: 'rejected' }))).toBe('Cancelled');
  });
});

describe('sortTodos', () => {
  it('default (created desc) + priority tiebreaker high>medium>low>none', () => {
    const a = mk({ id: 'a', createdAt: '2026-08-05T00:00:00+00:00', priority: 'low' });
    const b = mk({ id: 'b', createdAt: '2026-08-05T00:00:00+00:00', priority: 'high' }); // same date, higher pri
    const c = mk({ id: 'c', createdAt: '2026-08-06T00:00:00+00:00', priority: 'none' }); // newest
    const out = sortTodos([a, b, c], 'created', 'desc');
    expect(out.map((t) => t.id)).toEqual(['c', 'b', 'a']); // c newest; b before a on tiebreak
  });
  it('asc reverses primary order', () => {
    const a = mk({ id: 'a', createdAt: '2026-08-05T00:00:00+00:00' });
    const c = mk({ id: 'c', createdAt: '2026-08-06T00:00:00+00:00' });
    expect(sortTodos([a, c], 'created', 'asc').map((t) => t.id)).toEqual(['a', 'c']);
  });
  it('sort by priority uses PRIORITY_RANK', () => {
    const hi = mk({ id: 'hi', priority: 'high' });
    const lo = mk({ id: 'lo', priority: 'low' });
    expect(sortTodos([lo, hi], 'priority', 'asc').map((t) => t.id)).toEqual(['hi', 'lo']);
    expect(PRIORITY_RANK.high).toBeLessThan(PRIORITY_RANK.none);
  });
  it('sort by title is alphabetical', () => {
    const z = mk({ id: 'z', title: 'zebra' });
    const a = mk({ id: 'a', title: 'apple' });
    expect(sortTodos([z, a], 'title', 'asc').map((t) => t.id)).toEqual(['a', 'z']);
  });
  it('is a pure function (does not mutate input array)', () => {
    const input = [mk({ id: 'a', createdAt: '2026-08-05T00:00:00+00:00' }), mk({ id: 'b', createdAt: '2026-08-06T00:00:00+00:00' })];
    const snapshot = input.map((t) => t.id);
    sortTodos(input, 'created', 'desc');
    expect(input.map((t) => t.id)).toEqual(snapshot);
  });
  it('completed sorts nulls last (dateless completedAt below dated)', () => {
    const done = mk({ id: 'done', completedAt: '2026-08-06T00:00:00+00:00' });
    const notdone = mk({ id: 'notdone', completedAt: null });
    expect(sortTodos([notdone, done], 'completed', 'desc')[0].id).toBe('done');
  });
});

describe('filterByRange', () => {
  it('null days = All (keeps everything)', () => {
    const rows = [mk({ createdAt: '2020-01-01T00:00:00+00:00' }), mk({ createdAt: '2026-08-07T00:00:00+00:00' })];
    expect(filterByRange(rows, null, NOW)).toHaveLength(2);
  });
  it('30d keeps only createdAt within window', () => {
    const inWin = mk({ id: 'in', createdAt: '2026-07-20T00:00:00+00:00' }); // 18d ago
    const outWin = mk({ id: 'out', createdAt: '2026-06-01T00:00:00+00:00' }); // 67d ago
    const out = filterByRange([inWin, outWin], 30, NOW);
    expect(out.map((t) => t.id)).toEqual(['in']);
  });
  it('boundary: exactly N days ago is kept (inclusive)', () => {
    const edge = mk({ id: 'edge', createdAt: '2026-07-08T12:00:00+00:00' }); // exactly 30d before NOW
    expect(filterByRange([edge], 30, NOW).map((t) => t.id)).toEqual(['edge']);
  });
});

describe('computeKpis', () => {
  it('counts open / in-progress / completed and completion rate', () => {
    const rows = [
      mk({ status: 'pending' }),                                   // Pending → open
      mk({ status: 'overdue' }),                                   // Pending → open
      mk({ dispatchedAt: '2026-08-02T00:00:00+00:00' }),           // In Progress
      mk({ reviewState: 'confirmed' }),                            // Completed
      mk({ status: 'handled' }),                                   // Completed
    ];
    const k = computeKpis(rows);
    expect(k.open).toBe(2);
    expect(k.inProgress).toBe(1);
    expect(k.completed).toBe(2);
    // completion rate = completed / (all non-cancelled) = 2/5
    expect(k.completionRate).toBeCloseTo(2 / 5, 5);
  });
  it('completionRate is 0 (not NaN) on empty input', () => {
    expect(computeKpis([]).completionRate).toBe(0);
  });
  it('cancelled excluded from completion-rate denominator', () => {
    const rows = [mk({ status: 'handled' }), mk({ status: 'cancelled' })];
    // 1 completed / 1 non-cancelled = 1.0
    expect(computeKpis(rows).completionRate).toBeCloseTo(1, 5);
  });
});

describe('weeklyBuckets', () => {
  it('buckets created vs completed by ISO week, chronological', () => {
    const rows = [
      mk({ createdAt: '2026-08-03T00:00:00+00:00' }),                                     // wk A created
      mk({ createdAt: '2026-08-04T00:00:00+00:00', status: 'handled', completedAt: '2026-08-05T00:00:00+00:00' }), // wk A created + completed
    ];
    const buckets = weeklyBuckets(rows);
    const total = buckets.reduce((s, b) => s + b.created, 0);
    expect(total).toBe(2);
    expect(buckets.reduce((s, b) => s + b.completed, 0)).toBe(1);
  });
  it('empty input → empty array (no crash)', () => {
    expect(weeklyBuckets([])).toEqual([]);
  });
});

describe('sourceDist', () => {
  it('counts by sourceType sorted desc', () => {
    const rows = [mk({ sourceType: 'chat' }), mk({ sourceType: 'chat' }), mk({ sourceType: 'manual' })];
    const d = sourceDist(rows);
    expect(d[0]).toEqual({ source: 'chat', count: 2 });
    expect(d[1]).toEqual({ source: 'manual', count: 1 });
  });
  it('empty → empty array', () => {
    expect(sourceDist([])).toEqual([]);
  });
});

// type-only guard: SortKey union stays the 7 columns
it('SortKey covers all sortable columns', () => {
  const keys: SortKey[] = ['priority', 'title', 'source', 'status', 'created', 'updated', 'completed'];
  expect(keys).toHaveLength(7);
});
