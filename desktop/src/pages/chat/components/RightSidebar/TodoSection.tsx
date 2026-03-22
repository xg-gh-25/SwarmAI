/**
 * Read-only ToDo section for the Radar sidebar.
 *
 * Displays active ToDo items (status ``pending`` or ``overdue``) fetched via
 * ``radarService.fetchActiveTodos``.  Items are sorted by priority (high first)
 * then creation date (newest first).  A display limit of 5 items is enforced
 * by default with "See more" / "Show less" expansion controls.
 *
 * Each item row includes a title, colored priority dot, and a ``DragHandle``
 * with payload type ``radar-todo``.  No action buttons are rendered — the
 * list is strictly read-only.
 *
 * Key exports:
 * - ``TodoSection``          — The section component
 * - ``PRIORITY_WEIGHT``      — Priority-to-number mapping for sorting
 * - ``PRIORITY_COLORS``      — Priority-to-CSS-color mapping for dots
 * - ``filterActiveTodos``    — Filters to pending/overdue only
 * - ``sortByPriorityThenDate`` — Sorts by priority desc, then createdAt desc
 */

import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import type { RadarTodo } from '../../../../types';
import { radarService } from '../../../../services/radar';
import { DragHandle } from './shared/DragHandle';
import type { DropPayload } from './types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Default number of items shown before "See more" expansion. */
const DISPLAY_LIMIT = 5;

/** Polling interval for background refresh (ms). */
const POLL_INTERVAL_MS = 30_000;

/** Numeric weight per priority level (higher = more important). */
export const PRIORITY_WEIGHT: Record<string, number> = {
  high: 3,
  medium: 2,
  low: 1,
  none: 0,
};

/** CSS color value for each priority level's dot indicator. */
export const PRIORITY_COLORS: Record<string, string> = {
  high: 'var(--color-error, #ef4444)',
  medium: 'var(--color-warning, #f59e0b)',
  low: 'var(--color-info, #3b82f6)',
  none: 'var(--color-text-muted, #9ca3af)',
};

// ---------------------------------------------------------------------------
// Pure helpers (exported for testing)
// ---------------------------------------------------------------------------

/** Return only active (pending | overdue) ToDo items. */
export function filterActiveTodos(todos: RadarTodo[]): RadarTodo[] {
  return todos.filter(
    (t) => t.status === 'pending' || t.status === 'overdue',
  );
}

/**
 * Sort by priority descending (high=3 first), then createdAt descending
 * (newest first).  Uses id as a deterministic tiebreaker.
 */
export function sortByPriorityThenDate(todos: RadarTodo[]): RadarTodo[] {
  return [...todos].sort((a, b) => {
    const aPri = PRIORITY_WEIGHT[a.priority] ?? 0;
    const bPri = PRIORITY_WEIGHT[b.priority] ?? 0;
    if (aPri !== bPri) return bPri - aPri;

    // Newest first — reverse lexicographic on ISO date strings
    if (a.createdAt !== b.createdAt) {
      return a.createdAt > b.createdAt ? -1 : 1;
    }

    // Deterministic tiebreaker
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface TodoSectionProps {
  workspaceId: string | null;
  /** Report item count to parent for badge display. */
  onCountChange?: (count: number) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TodoSection({ workspaceId, onCountChange }: TodoSectionProps) {
  const [todos, setTodos] = useState<RadarTodo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  // Track whether initial load has completed (show spinner only for first load)
  const hasLoadedRef = useRef(false);

  // Stable fetch callback — silent=true skips the loading spinner (for polls)
  const fetchTodos = useCallback(
    async (silent: boolean) => {
      if (!workspaceId) {
        setTodos([]);
        return;
      }
      if (!silent) setLoading(true);
      setError(null);

      try {
        const data = await radarService.fetchActiveTodos(workspaceId);
        setTodos(data);
        hasLoadedRef.current = true;
      } catch (err) {
        // Only show error on initial load — silent polls swallow errors
        if (!silent) {
          setError(err instanceof Error ? err.message : 'Failed to load todos');
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [workspaceId],
  );

  // Initial fetch + reset on workspace change
  useEffect(() => {
    setExpanded(false);
    hasLoadedRef.current = false;
    fetchTodos(false);
  }, [fetchTodos]);

  // 30s polling — silent background refresh
  useEffect(() => {
    if (!workspaceId) return;
    const id = setInterval(() => fetchTodos(true), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [workspaceId, fetchTodos]);

  // Visibility change — refetch when user returns to the app/tab
  useEffect(() => {
    if (!workspaceId) return;
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        fetchTodos(true);
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    // Also refetch on window focus (covers alt-tab without visibility change)
    window.addEventListener('focus', handleVisibility);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
    };
  }, [workspaceId, fetchTodos]);

  // Derive active, sorted list
  const activeTodos = useMemo(
    () => sortByPriorityThenDate(filterActiveTodos(todos)),
    [todos],
  );

  // Report count to parent — use ref to avoid re-render loops
  const prevCountRef = useRef(-1);
  useEffect(() => {
    if (onCountChange && activeTodos.length !== prevCountRef.current) {
      prevCountRef.current = activeTodos.length;
      onCountChange(activeTodos.length);
    }
  }, [activeTodos.length, onCountChange]);

  const visibleTodos = expanded
    ? activeTodos
    : activeTodos.slice(0, DISPLAY_LIMIT);
  const remaining = activeTodos.length - DISPLAY_LIMIT;

  // --- Loading state ---
  if (loading) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        Loading todos…
      </p>
    );
  }

  // --- Error state ---
  if (error) {
    return (
      <p className="text-xs text-[var(--color-error)] py-2">
        {error}
      </p>
    );
  }

  // --- Empty state ---
  if (activeTodos.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        No active todos
      </p>
    );
  }

  // --- Item list ---
  return (
    <div>
      <ul className="space-y-1">
        {visibleTodos.map((todo) => {
          const payload: DropPayload = {
            type: 'radar-todo',
            id: todo.id,
            title: todo.title,
            context: todo.linkedContext ?? todo.description ?? undefined,
          };
          const dotColor =
            PRIORITY_COLORS[todo.priority] ?? PRIORITY_COLORS.none;

          return (
            <li
              key={todo.id}
              className="group flex items-center gap-2 px-1 py-1 rounded hover:bg-[var(--color-hover)] transition-colors"
            >
              {/* Priority dot */}
              <span
                className="shrink-0 w-2 h-2 rounded-full"
                style={{ backgroundColor: dotColor }}
                title={`Priority: ${todo.priority}`}
              />

              {/* Title */}
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {todo.title}
              </span>

              {/* Drag handle */}
              <DragHandle payload={payload} />
            </li>
          );
        })}
      </ul>

      {/* See more / Show less controls */}
      {remaining > 0 && !expanded && (
        <button
          className="text-xs text-[var(--color-link)] hover:underline mt-1 px-1"
          onClick={() => setExpanded(true)}
        >
          See more ({remaining} more)
        </button>
      )}
      {expanded && activeTodos.length > DISPLAY_LIMIT && (
        <button
          className="text-xs text-[var(--color-link)] hover:underline mt-1 px-1"
          onClick={() => setExpanded(false)}
        >
          Show less
        </button>
      )}
    </div>
  );
}
