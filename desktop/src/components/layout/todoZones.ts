/**
 * todoZones — pure zone derivation for the ToDo flow board (run_5088b841, A2).
 *
 * The 4 zones are derived from (status, review_state, dispatched_*) TOGETHER —
 * the review dimension is orthogonal to status (A1 locked invariant: a dispatched
 * todo stays status='pending' until Confirm). Pure + exported so it's unit-testable
 * independent of React.
 *
 * Zone predicates (mirror the design doc's zone table):
 *   ① To Do        status in (pending,overdue) AND no dispatch AND review_state null
 *   ② In Progress  dispatched (session_id OR tab_label non-null) AND review_state null
 *   ③ Completed    review_state === 'completed'  (awaiting review)
 *   ④ Recent       review_state in (confirmed,rejected)
 */
import type { ToDo } from '../../types/todo';

export type ToDoZone = 'todo' | 'in_progress' | 'completed' | 'recent';

export function zoneOf(t: ToDo): ToDoZone | null {
  if (t.reviewState === 'completed') return 'completed';
  if (t.reviewState === 'confirmed' || t.reviewState === 'rejected') return 'recent';
  // review_state is null below here
  const dispatched = !!(t.dispatchedSessionId || t.dispatchedTabLabel);
  if (dispatched) return 'in_progress';
  if (t.status === 'pending' || t.status === 'overdue') return 'todo';
  // handled/cancelled/deleted with no review_state → not shown on the flow board
  return null;
}

export interface ZonedTodos {
  todo: ToDo[];
  in_progress: ToDo[];
  completed: ToDo[];
  recent: ToDo[];
}

/** Partition todos into the 4 zones. `recent` is capped + most-recent-first. */
export function deriveZones(todos: ToDo[], recentLimit = 20): ZonedTodos {
  const z: ZonedTodos = { todo: [], in_progress: [], completed: [], recent: [] };
  for (const t of todos) {
    const zone = zoneOf(t);
    if (zone) z[zone].push(t);
  }
  z.recent.sort((a, b) => (b.reviewedAt || '').localeCompare(a.reviewedAt || ''));
  z.recent = z.recent.slice(0, recentLimit);
  return z;
}
