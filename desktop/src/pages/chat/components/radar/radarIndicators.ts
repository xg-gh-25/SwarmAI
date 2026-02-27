/**
 * Indicator mapping and badge tint utilities for Swarm Radar zones.
 *
 * - ``getPriorityIndicator``  — Maps priority to emoji (🔴 🟡 🔵)
 * - ``getTimelineIndicator``  — Maps overdue/due-today to emoji (⚠️ ⏰)
 * - ``getSourceTypeLabel``    — Maps source type to emoji label
 * - ``getBadgeTint``          — Computes zone badge tint color
 */

import type {
  RadarTodo,
  RadarTodoPriority,
  RadarTodoSourceType,
  RadarAutonomousJob,
  RadarZoneId,
} from '../../../../types';

// ---------------------------------------------------------------------------
// Priority indicator
// ---------------------------------------------------------------------------

/** Map priority to its emoji indicator. Returns '' for 'none'. */
export function getPriorityIndicator(priority: RadarTodoPriority): string {
  switch (priority) {
    case 'high': return '🔴';
    case 'medium': return '🟡';
    case 'low': return '🔵';
    case 'none': return '';
  }
}

// ---------------------------------------------------------------------------
// Timeline indicator
// ---------------------------------------------------------------------------

/** Returns ⚠️ for overdue, ⏰ for due today, '' otherwise. */
export function getTimelineIndicator(
  status: string,
  dueDate: string | null,
): string {
  if (status === 'overdue') return '⚠️';
  if (dueDate) {
    const today = new Date().toISOString().slice(0, 10);
    const due = dueDate.slice(0, 10);
    if (due === today) return '⏰';
  }
  return '';
}

// ---------------------------------------------------------------------------
// Source type label
// ---------------------------------------------------------------------------

const SOURCE_TYPE_MAP: Record<RadarTodoSourceType, string> = {
  manual: '✏️',
  email: '📧',
  slack: '💬',
  meeting: '📅',
  integration: '🔗',
  chat: '💭',
  ai_detected: '🤖',
};

/** Map source type to its emoji label. */
export function getSourceTypeLabel(sourceType: RadarTodoSourceType): string {
  return SOURCE_TYPE_MAP[sourceType] ?? '';
}

// ---------------------------------------------------------------------------
// Badge tint
// ---------------------------------------------------------------------------

export type BadgeTint = 'red' | 'yellow' | 'green' | 'neutral';

interface BadgeTintContext {
  todos?: RadarTodo[];
  jobs?: RadarAutonomousJob[];
}

/**
 * Compute the badge tint for a Radar zone.
 *
 * - Needs Attention → red when any todo is overdue or high-priority
 * - In Progress     → always yellow
 * - Completed       → always green
 * - Autonomous Jobs → red when any job has error status, neutral otherwise
 */
export function getBadgeTint(
  zoneId: RadarZoneId,
  ctx: BadgeTintContext = {},
): BadgeTint {
  switch (zoneId) {
    case 'needsAttention': {
      const hasUrgent = (ctx.todos ?? []).some(
        (t) => t.status === 'overdue' || t.priority === 'high',
      );
      return hasUrgent ? 'red' : 'neutral';
    }
    case 'inProgress':
      return 'yellow';
    case 'completed':
      return 'green';
    case 'autonomousJobs': {
      const hasError = (ctx.jobs ?? []).some((j) => j.status === 'error');
      return hasError ? 'red' : 'neutral';
    }
  }
}
