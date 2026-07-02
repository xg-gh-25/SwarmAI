/**
 * Shared TypeScript types for the Swarm Radar.
 *
 * Defines the Radar types still in use by the live RadarSidebar:
 *
 * - ``RadarTodo``  — ToDo item shown in the ToDo section
 */

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

/** A ToDo item displayed in the ToDo section. */
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
