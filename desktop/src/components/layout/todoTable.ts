/**
 * todoTable — pure derivation helpers for the ToDo flat-table workbench
 * (run_7ccfe39f). Replaces todoZones' 4-zone board derivation with a single
 * sortable table: status label, sort, range filter, KPI + 2 chart aggregations.
 *
 * ALL functions are pure + React-free so they unit-test independently. The React
 * surface (ToDoContent) is a thin renderer over these.
 *
 * Status derivation collapses (status, reviewState, dispatched_*) to 4 flat labels.
 * A terminal state wins over a dispatch signal (a dispatched-then-confirmed todo is
 * Completed, not In Progress). Precedence (Gate-2 correctness fix):
 *   Completed  status=handled OR reviewState in (confirmed, completed)
 *   Cancelled  status in (cancelled, deleted) OR reviewState=rejected
 *   In Progress dispatched (session/tab/at) OR status=in_discussion
 *   Pending    everything else (pending / overdue)
 *
 * ⚠️ `reviewState='rejected'` is CANCELLED, not Completed. The backend reject flow
 * sets status=cancelled + reviewState=rejected AND spawns a fresh pending todo — the
 * rejected original was NOT completed (work was rejected), so among the 4 labels it is
 * Cancelled. Lumping it into Completed (green) mislabels a rejected item as done — a
 * real case because history() surfaces rejected terminal todos into the table.
 */
import type { ToDo } from '../../types/todo';

export type TodoStatusLabel = 'Pending' | 'In Progress' | 'Completed' | 'Cancelled';

/** The 7 sortable table columns. */
export type SortKey = 'priority' | 'title' | 'source' | 'status' | 'created' | 'updated' | 'completed';
export type SortDir = 'asc' | 'desc';

/** Priority ordering for sort + tiebreaker (lower = higher priority). */
export const PRIORITY_RANK: Record<ToDo['priority'], number> = {
  high: 0,
  medium: 1,
  low: 2,
  none: 3,
};

const STATUS_RANK: Record<TodoStatusLabel, number> = {
  Pending: 0,
  'In Progress': 1,
  Completed: 2,
  Cancelled: 3,
};

/** Derive the flat table status label from the raw ToDo (precedence-ordered). */
export function deriveStatus(t: ToDo): TodoStatusLabel {
  // Cancelled is terminal-not-done: an explicit cancel/delete OR a REJECTED review
  // (backend reject sets status=cancelled + reviewState=rejected). Checked BEFORE the
  // Completed branch so a rejected item never renders as green "Completed" (Gate-2 HIGH).
  if (t.status === 'cancelled' || t.status === 'deleted' || t.reviewState === 'rejected') {
    return 'Cancelled';
  }
  if (t.status === 'handled' || t.reviewState === 'confirmed' || t.reviewState === 'completed') {
    return 'Completed';
  }
  if (t.dispatchedAt || t.dispatchedSessionId || t.dispatchedTabLabel || t.status === 'in_discussion') {
    return 'In Progress';
  }
  return 'Pending';
}

/** Primary comparator value for a sort key (higher = later in a `desc` sort). */
function primaryValue(t: ToDo, key: SortKey): number | string {
  switch (key) {
    case 'priority': return PRIORITY_RANK[t.priority];
    case 'title':    return t.title.toLocaleLowerCase();
    case 'source':   return t.sourceType;
    case 'status':   return STATUS_RANK[deriveStatus(t)];
    case 'created':  return t.createdAt;
    case 'updated':  return t.updatedAt;
    case 'completed': return t.completedAt ?? ''; // null → '' sorts last in desc
  }
}

/**
 * Stable sort (pure — returns a new array). Ties on the primary key break by
 * PRIORITY_RANK then createdAt-desc so equal-primary rows have a deterministic
 * order (the default view = newest-first with high-priority above on same date).
 */
export function sortTodos(rows: ToDo[], key: SortKey, dir: SortDir): ToDo[] {
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = primaryValue(a, key);
    const bv = primaryValue(b, key);
    let cmp: number;
    if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
    else cmp = String(av).localeCompare(String(bv));
    if (cmp !== 0) return cmp * mul;
    // tiebreakers (direction-independent so equal-primary order is stable):
    const pr = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
    if (pr !== 0) return pr;
    return b.createdAt.localeCompare(a.createdAt); // newest first on full tie
  });
}

/** Keep rows whose createdAt is within `days` of `now` (inclusive). null = All. */
export function filterByRange(rows: ToDo[], days: number | null, now: Date = new Date()): ToDo[] {
  if (days == null) return rows;
  const cutoff = now.getTime() - days * 24 * 60 * 60 * 1000;
  return rows.filter((t) => {
    const ts = new Date(t.createdAt).getTime();
    return !isNaN(ts) && ts >= cutoff;
  });
}

export interface TodoKpis {
  open: number;        // Pending
  inProgress: number;
  completed: number;
  completionRate: number; // completed / (all non-cancelled); 0 on empty
}

/** KPI counts over the given (already range/status-filtered) rows.
 *  ⚠️ Coupled to deriveStatus precedence: Cancelled (incl. reviewState=rejected) is
 *  excluded from the completionRate denominator, so a rejected todo inflates neither
 *  numerator nor denominator. If deriveStatus's rejected→Cancelled precedence changes,
 *  this rate silently shifts — the deriveStatus + computeKpis tests lock them together. */
export function computeKpis(rows: ToDo[]): TodoKpis {
  let open = 0, inProgress = 0, completed = 0, nonCancelled = 0;
  for (const t of rows) {
    const s = deriveStatus(t);
    if (s === 'Cancelled') continue;
    nonCancelled++;
    if (s === 'Pending') open++;
    else if (s === 'In Progress') inProgress++;
    else if (s === 'Completed') completed++;
  }
  return {
    open,
    inProgress,
    completed,
    completionRate: nonCancelled === 0 ? 0 : completed / nonCancelled,
  };
}

/** ISO-week key (YYYY-Www) for a date — Monday-based, matches common weekly bucketing. */
function isoWeekKey(iso: string): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  // Copy to UTC midnight, shift to nearest Thursday (ISO week rule)
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = date.getUTCDay() || 7; // Sun=0 → 7
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

export interface WeekBucket { week: string; created: number; completed: number; }

/** Per-ISO-week {created, completed} counts, chronological. `completed` counts a
 *  row in the week it was completed (completedAt), independent of created week. */
export function weeklyBuckets(rows: ToDo[]): WeekBucket[] {
  const map = new Map<string, WeekBucket>();
  const bump = (wk: string | null, field: 'created' | 'completed') => {
    if (!wk) return;
    const b = map.get(wk) ?? { week: wk, created: 0, completed: 0 };
    b[field]++;
    map.set(wk, b);
  };
  for (const t of rows) {
    bump(isoWeekKey(t.createdAt), 'created');
    if (deriveStatus(t) === 'Completed') {
      // prefer completedAt; fall back to reviewedAt/updatedAt so a completed row
      // without an explicit completedAt still lands in a week.
      bump(isoWeekKey(t.completedAt ?? t.reviewedAt ?? t.updatedAt), 'completed');
    }
  }
  return [...map.values()].sort((a, b) => a.week.localeCompare(b.week));
}

export interface SourceCount { source: string; count: number; }

/** Count by sourceType, sorted by count desc (ties → source name asc). */
export function sourceDist(rows: ToDo[]): SourceCount[] {
  const map = new Map<string, number>();
  for (const t of rows) map.set(t.sourceType, (map.get(t.sourceType) ?? 0) + 1);
  return [...map.entries()]
    .map(([source, count]) => ({ source, count }))
    .sort((a, b) => b.count - a.count || a.source.localeCompare(b.source));
}
