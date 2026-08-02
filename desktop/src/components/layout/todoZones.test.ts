/**
 * Tests for todoZones — the pure flow-board zone derivation.
 *
 * Regression focus (run_8e29d63f): a todo with status='in_discussion' and no
 * dispatched_* / review_state used to fall through every predicate → zoneOf
 * returned null → the todo vanished from ALL four flow zones (visible only in
 * History). in_discussion is set by drag-to-chat bind AND TodoLifecycleHook
 * implicit-file-match, neither of which sets dispatched_*, so the state is
 * common. Semantically in_discussion = being worked on = In Progress.
 */
import { describe, it, expect } from 'vitest';
import { zoneOf, deriveZones } from './todoZones';
import type { ToDo } from '../../types/todo';

function makeTodo(overrides: Partial<ToDo>): ToDo {
  return {
    id: 'id-1',
    workspaceId: 'swarmws',
    title: 'test',
    description: null,
    source: null,
    sourceType: 'manual',
    status: 'pending',
    priority: 'none',
    dueDate: null,
    linkedContext: null,
    taskId: null,
    reviewState: null,
    reviewKind: null,
    dispatchedSessionId: null,
    dispatchedTabLabel: null,
    dispatchedAt: null,
    completedAt: null,
    reviewedAt: null,
    createdAt: '2026-08-02T00:00:00Z',
    updatedAt: '2026-08-02T00:00:00Z',
    ...overrides,
  };
}

describe('zoneOf', () => {
  it('maps pending → To Do', () => {
    expect(zoneOf(makeTodo({ status: 'pending' }))).toBe('todo');
  });

  it('maps overdue → To Do', () => {
    expect(zoneOf(makeTodo({ status: 'overdue' }))).toBe('todo');
  });

  it('maps dispatched (session or tab) → In Progress', () => {
    expect(zoneOf(makeTodo({ status: 'pending', dispatchedTabLabel: 'Tab 2' }))).toBe('in_progress');
    expect(zoneOf(makeTodo({ status: 'pending', dispatchedSessionId: 's1' }))).toBe('in_progress');
  });

  // REGRESSION: this is the bug — in_discussion with no dispatch must NOT vanish.
  it('maps in_discussion (no dispatch) → In Progress, not null', () => {
    expect(zoneOf(makeTodo({ status: 'in_discussion' }))).toBe('in_progress');
  });

  it('review_state takes precedence over in_discussion status', () => {
    // a reviewed todo is Completed/Recent regardless of status
    expect(zoneOf(makeTodo({ status: 'in_discussion', reviewState: 'completed' }))).toBe('completed');
    expect(zoneOf(makeTodo({ status: 'in_discussion', reviewState: 'confirmed' }))).toBe('recent');
  });

  it('handled/cancelled with no review_state → not on the flow board (null)', () => {
    expect(zoneOf(makeTodo({ status: 'handled' }))).toBeNull();
    expect(zoneOf(makeTodo({ status: 'cancelled' }))).toBeNull();
  });
});

describe('deriveZones', () => {
  it('an in_discussion todo lands in in_progress, not dropped', () => {
    const z = deriveZones([makeTodo({ id: 'a', status: 'in_discussion' })]);
    expect(z.in_progress.map((t) => t.id)).toContain('a');
    expect(z.todo).toHaveLength(0);
    expect(z.completed).toHaveLength(0);
    expect(z.recent).toHaveLength(0);
  });
});
