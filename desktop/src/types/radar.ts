/**
 * Shared TypeScript types for the Swarm Radar redesign.
 *
 * Defines all Radar-specific types used across the five sub-specs:
 *
 * - ``RadarZoneId``         — Union type identifying the four Radar zones
 * - ``RadarTodo``           — ToDo item in the Needs Attention zone
 * - ``RadarWipTask``        — WIP task in the In Progress zone (Pick from Task)
 * - ``RadarCompletedTask``  — Completed task in the Completed zone
 * - ``RadarWaitingItem``    — Pending question/permission in Needs Attention
 * - ``RadarAutonomousJob``  — System or user-defined autonomous job
 * - ``RadarReviewItem``     — Placeholder for future review items (not populated)
 */

import type { Task } from './index';

// ---------------------------------------------------------------------------
// Zone identification
// ---------------------------------------------------------------------------

/** Identifies one of the four Radar zones. */
export type RadarZoneId =
  | 'needsAttention'
  | 'inProgress'
  | 'completed'
  | 'autonomousJobs';

// ---------------------------------------------------------------------------
// ToDo
// ---------------------------------------------------------------------------

/** Source type for a ToDo item. */
export type RadarTodoSourceType =
  | 'manual'
  | 'email'
  | 'slack'
  | 'meeting'
  | 'integration'
  | 'chat'
  | 'ai_detected';

/** Lifecycle status of a ToDo. */
export type RadarTodoStatus =
  | 'pending'
  | 'overdue'
  | 'in_discussion'
  | 'handled'
  | 'cancelled'
  | 'deleted';

/** Priority level of a ToDo. */
export type RadarTodoPriority = 'high' | 'medium' | 'low' | 'none';

/** A ToDo item displayed in the Needs Attention zone. */
export interface RadarTodo {
  id: string;
  workspaceId: string;
  title: string;
  description: string | null;
  source: string | null;
  sourceType: RadarTodoSourceType;
  status: RadarTodoStatus;
  priority: RadarTodoPriority;
  dueDate: string | null;
  linkedContext: string | null;
  taskId: string | null;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// WIP Task
// ---------------------------------------------------------------------------

/**
 * A WIP task displayed in the In Progress zone.
 *
 * Uses Pick from the existing Task type to avoid parallel type duplication,
 * plus a `hasWaitingInput` flag derived by the useWaitingInputZone hook
 * (Spec 3) at the composition layer.
 */
export type RadarWipTask = Pick<
  Task,
  | 'id'
  | 'workspaceId'
  | 'agentId'
  | 'sessionId'
  | 'status'
  | 'title'
  | 'description'
  | 'priority'
  | 'sourceTodoId'
  | 'model'
  | 'createdAt'
  | 'startedAt'
  | 'error'
> & {
  hasWaitingInput: boolean;
};

// ---------------------------------------------------------------------------
// Completed Task
// ---------------------------------------------------------------------------

/** A completed task displayed in the Completed zone. */
export interface RadarCompletedTask {
  id: string;
  workspaceId: string | null;
  agentId: string;
  sessionId: string | null;
  title: string;
  description: string | null;
  priority: string | null;
  completedAt: string;
  /** Always false in initial release — risk-assessment deferred. */
  reviewRequired: boolean;
  /** Always null in initial release — risk-assessment deferred. */
  reviewRiskLevel: string | null;
}

// ---------------------------------------------------------------------------
// Waiting Input
// ---------------------------------------------------------------------------

/**
 * A pending question or permission request in the Needs Attention zone.
 *
 * Ephemeral — derived from SSE props, not persisted in the database.
 * Disappears on page reload; the agent re-asks if still relevant.
 *
 * PE Finding #1: `createdAt` uses the SSE event arrival timestamp
 * (captured when pendingQuestion state is set in ChatPage) or the
 * matched WIP task's `startedAt` as a stable proxy — NOT `Date.now()`
 * at derivation time.
 */
export interface RadarWaitingItem {
  id: string;
  title: string;
  agentId: string;
  sessionId: string | null;
  /** Question or permission reason text, truncated to 200 chars. */
  question: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Autonomous Job
// ---------------------------------------------------------------------------

/** Category of an autonomous job. */
export type RadarJobCategory = 'system' | 'user_defined';

/** Status of an autonomous job. */
export type RadarJobStatus = 'running' | 'paused' | 'error' | 'completed';

/** A system or user-defined autonomous job. */
export interface RadarAutonomousJob {
  id: string;
  name: string;
  category: RadarJobCategory;
  status: RadarJobStatus;
  schedule: string | null;
  lastRunAt: string | null;
  nextRunAt: string | null;
  description: string | null;
}

// ---------------------------------------------------------------------------
// Review Item (placeholder — not populated in initial release)
// ---------------------------------------------------------------------------

/**
 * A completed task requiring user review based on risk-level policy.
 *
 * **Not populated in the initial release.** The `review_required` field on
 * tasks is always `false` and `review_risk_level` is always `null`.
 * Risk-assessment logic is deferred to a future spec.
 */
export interface RadarReviewItem {
  id: string;
  title: string;
  agentId: string;
  sessionId: string | null;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  completionSummary: string;
  completedAt: string;
}
