/**
 * Canonical Todo entity types.
 *
 * Single source of truth for the ToDo domain entity across the app (Radar
 * sidebar, Welcome screen, convert-to-task). The shape mirrors the backend
 * `ToDoResponse` (backend/schemas/todo.py) exactly — status values are the
 * raw backend enum (snake_case, e.g. `in_discussion`), and response fields
 * that the backend may omit are `| null`.
 */

/** Lifecycle status of a ToDo — values match the backend ToDoStatus enum verbatim. */
export type ToDoStatus =
  | 'pending'
  | 'overdue'
  | 'in_discussion'
  | 'handled'
  | 'cancelled'
  | 'deleted';

/** Source type for a ToDo — values match the backend ToDoSourceType enum. */
export type ToDoSourceType =
  | 'manual'
  | 'email'
  | 'slack'
  | 'meeting'
  | 'integration'
  | 'chat'
  | 'ai_detected';

/** Priority level of a ToDo. */
export type Priority = 'high' | 'medium' | 'low' | 'none';

/**
 * A ToDo item as returned by the backend (GET /todos).
 *
 * Optional/omittable fields are `| null` (the API returns null, not absent).
 */
export interface ToDo {
  id: string;
  workspaceId: string;
  title: string;
  description: string | null;
  source: string | null;
  sourceType: ToDoSourceType;
  status: ToDoStatus;
  priority: Priority;
  dueDate: string | null;
  linkedContext: string | null;
  taskId: string | null;
  // ToDo flow-closure fields (run_5088b841, A2) — mirror A1's 7 backend columns.
  // review dimension is ORTHOGONAL to status (zones derived from both).
  reviewState: 'completed' | 'confirmed' | 'rejected' | null;
  reviewKind: 'manual' | 'auto' | null;
  dispatchedSessionId: string | null;
  dispatchedTabLabel: string | null;
  dispatchedAt: string | null;
  completedAt: string | null;
  reviewedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ToDoCreateRequest {
  workspaceId?: string;
  title: string;
  description?: string;
  source?: string;
  sourceType?: ToDoSourceType;
  priority?: Priority;
  dueDate?: string;
}

export interface ToDoUpdateRequest {
  title?: string;
  description?: string;
  status?: ToDoStatus;
  priority?: Priority;
  dueDate?: string;
}
