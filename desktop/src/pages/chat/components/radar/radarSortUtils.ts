/**
 * Pure sorting utility functions for all Swarm Radar zones.
 *
 * Each function returns a new sorted array without mutating the input.
 * All functions use `id` (string comparison) as the ultimate tiebreaker
 * to guarantee a deterministic total order (PE Finding #6).
 *
 * - ``sortTodos``           — Overdue first → priority → dueDate → createdAt → id
 * - ``sortWipTasks``        — Status order (blocked→wip→draft) → startedAt → id
 * - ``sortCompletedTasks``  — completedAt descending → id
 * - ``sortWaitingItems``    — createdAt ascending → id
 * - ``sortAutonomousJobs``  — Category (system first) → name alphabetical → id
 */

import type {
  RadarTodo,
  RadarWipTask,
  RadarCompletedTask,
  RadarWaitingItem,
  RadarAutonomousJob,
  RadarTodoPriority,
} from '../../../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const PRIORITY_ORDER: Record<RadarTodoPriority, number> = {
  high: 0,
  medium: 1,
  low: 2,
  none: 3,
};

const WIP_STATUS_ORDER: Record<string, number> = {
  blocked: 0,
  wip: 1,
  draft: 2,
};

/** Compare two strings, treating null/undefined as Infinity (sort last). */
function cmpStr(a: string | null, b: string | null, asc = true): number {
  if (a === b) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const r = a < b ? -1 : 1;
  return asc ? r : -r;
}

// ---------------------------------------------------------------------------
// sortTodos
// ---------------------------------------------------------------------------

/**
 * Sort ToDos: overdue first → priority (high→medium→low→none) →
 * dueDate (earliest first, null last) → createdAt (newest first) → id asc.
 */
export function sortTodos(todos: readonly RadarTodo[]): RadarTodo[] {
  return [...todos].sort((a, b) => {
    // 1. Overdue first
    const aOverdue = a.status === 'overdue' ? 0 : 1;
    const bOverdue = b.status === 'overdue' ? 0 : 1;
    if (aOverdue !== bOverdue) return aOverdue - bOverdue;

    // 2. Priority (high → medium → low → none)
    const aPri = PRIORITY_ORDER[a.priority] ?? 3;
    const bPri = PRIORITY_ORDER[b.priority] ?? 3;
    if (aPri !== bPri) return aPri - bPri;

    // 3. Due date (earliest first, null last)
    const dueCmp = cmpStr(a.dueDate, b.dueDate, true);
    if (dueCmp !== 0) return dueCmp;

    // 4. Created at (newest first)
    const createdCmp = cmpStr(a.createdAt, b.createdAt, false);
    if (createdCmp !== 0) return createdCmp;

    // 5. id ascending tiebreaker
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// sortWipTasks
// ---------------------------------------------------------------------------

/**
 * Sort WIP tasks: blocked first → wip → draft →
 * startedAt (most recent first) → id asc.
 */
export function sortWipTasks(tasks: readonly RadarWipTask[]): RadarWipTask[] {
  return [...tasks].sort((a, b) => {
    // 1. Status order: blocked → wip → draft
    const aOrd = WIP_STATUS_ORDER[a.status] ?? 9;
    const bOrd = WIP_STATUS_ORDER[b.status] ?? 9;
    if (aOrd !== bOrd) return aOrd - bOrd;

    // 2. startedAt descending (most recent first)
    const startCmp = cmpStr(a.startedAt, b.startedAt, false);
    if (startCmp !== 0) return startCmp;

    // 3. id ascending tiebreaker
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// sortCompletedTasks
// ---------------------------------------------------------------------------

/** Sort completed tasks: completedAt descending → id asc. */
export function sortCompletedTasks(
  tasks: readonly RadarCompletedTask[],
): RadarCompletedTask[] {
  return [...tasks].sort((a, b) => {
    const cmp = cmpStr(a.completedAt, b.completedAt, false);
    if (cmp !== 0) return cmp;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// sortWaitingItems
// ---------------------------------------------------------------------------

/** Sort waiting items: createdAt ascending → id asc. */
export function sortWaitingItems(
  items: readonly RadarWaitingItem[],
): RadarWaitingItem[] {
  return [...items].sort((a, b) => {
    const cmp = cmpStr(a.createdAt, b.createdAt, true);
    if (cmp !== 0) return cmp;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// sortAutonomousJobs
// ---------------------------------------------------------------------------

/**
 * Sort autonomous jobs: system before user_defined →
 * name alphabetical (case-insensitive) → id asc.
 */
export function sortAutonomousJobs(
  jobs: readonly RadarAutonomousJob[],
): RadarAutonomousJob[] {
  return [...jobs].sort((a, b) => {
    // 1. Category: system before user_defined
    const catOrder = (c: string) => (c === 'system' ? 0 : 1);
    const catCmp = catOrder(a.category) - catOrder(b.category);
    if (catCmp !== 0) return catCmp;

    // 2. Name alphabetical (case-insensitive)
    const nameCmp = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    if (nameCmp !== 0) return nameCmp;

    // 3. id ascending tiebreaker
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}
