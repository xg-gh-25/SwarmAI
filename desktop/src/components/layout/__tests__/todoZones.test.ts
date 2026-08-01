/**
 * Tests for todoZones — 4-zone derivation from (status, review_state, dispatched_*).
 * Key cases: the tab_label-only ② (dispatched but session_id not yet backfilled),
 * status stays pending through ②/③ (locked invariant), recent sort + cap.
 */
import { describe, it, expect } from 'vitest';
import { zoneOf, deriveZones } from '../todoZones';
import type { ToDo } from '../../../types/todo';

function todo(over: Partial<ToDo> = {}): ToDo {
  return {
    id: Math.random().toString(36).slice(2),
    workspaceId: 'w', title: 't', description: null, source: null,
    sourceType: 'manual', status: 'pending', priority: 'none',
    dueDate: null, linkedContext: null, taskId: null,
    reviewState: null, reviewKind: null, dispatchedSessionId: null,
    dispatchedTabLabel: null, dispatchedAt: null, completedAt: null, reviewedAt: null,
    createdAt: '2026-08-01T00:00:00+00:00', updatedAt: '2026-08-01T00:00:00+00:00',
    ...over,
  };
}

describe('zoneOf', () => {
  it('① To Do: pending, no dispatch, no review', () => {
    expect(zoneOf(todo({ status: 'pending' }))).toBe('todo');
    expect(zoneOf(todo({ status: 'overdue' }))).toBe('todo');
  });

  it('② In Progress: dispatched via session_id (status still pending)', () => {
    expect(zoneOf(todo({ status: 'pending', dispatchedSessionId: 'S1' }))).toBe('in_progress');
  });

  it('② In Progress: dispatched via tab_label ONLY (session_id not yet backfilled)', () => {
    // The critical case: a dispatched-but-not-yet-sent todo has tab_label but no
    // session_id — it MUST still show in ②, not fall back to ①.
    expect(zoneOf(todo({ status: 'pending', dispatchedTabLabel: 'Tab 2', dispatchedSessionId: null }))).toBe('in_progress');
  });

  it('③ Completed: review_state=completed (status still pending — locked invariant)', () => {
    expect(zoneOf(todo({ status: 'pending', reviewState: 'completed', dispatchedSessionId: 'S1' }))).toBe('completed');
  });

  it('④ Recent: confirmed or rejected', () => {
    expect(zoneOf(todo({ status: 'handled', reviewState: 'confirmed' }))).toBe('recent');
    expect(zoneOf(todo({ status: 'cancelled', reviewState: 'rejected' }))).toBe('recent');
  });

  it('review_state takes precedence over dispatch (completed beats in_progress)', () => {
    expect(zoneOf(todo({ dispatchedSessionId: 'S1', reviewState: 'completed' }))).toBe('completed');
  });

  it('terminal status with no review_state is NOT on the board', () => {
    expect(zoneOf(todo({ status: 'handled', reviewState: null }))).toBeNull();
    expect(zoneOf(todo({ status: 'cancelled', reviewState: null }))).toBeNull();
  });
});

describe('deriveZones', () => {
  it('partitions into 4 zones', () => {
    const z = deriveZones([
      todo({ status: 'pending' }),
      todo({ status: 'pending', dispatchedTabLabel: 'Tab 1' }),
      todo({ reviewState: 'completed', dispatchedSessionId: 'S1' }),
      todo({ reviewState: 'confirmed', status: 'handled' }),
      todo({ status: 'handled', reviewState: null }), // off-board
    ]);
    expect(z.todo).toHaveLength(1);
    expect(z.in_progress).toHaveLength(1);
    expect(z.completed).toHaveLength(1);
    expect(z.recent).toHaveLength(1);
  });

  it('recent is most-recent-first by reviewedAt and capped', () => {
    const z = deriveZones([
      todo({ reviewState: 'confirmed', status: 'handled', reviewedAt: '2026-08-01T10:00:00+00:00' }),
      todo({ reviewState: 'rejected', status: 'cancelled', reviewedAt: '2026-08-03T10:00:00+00:00' }),
      todo({ reviewState: 'confirmed', status: 'handled', reviewedAt: '2026-08-02T10:00:00+00:00' }),
    ], 2);
    expect(z.recent).toHaveLength(2); // capped at 2
    expect(z.recent[0].reviewedAt).toBe('2026-08-03T10:00:00+00:00'); // newest first
    expect(z.recent[1].reviewedAt).toBe('2026-08-02T10:00:00+00:00');
  });
});
