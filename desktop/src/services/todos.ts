/**
 * ToDos service for managing Signals/ToDo entities.
 */
import api from './api';
import type {
  ToDo,
  ToDoCreateRequest,
  ToDoUpdateRequest,
  ToDoStatus,
} from '../types/todo';
import type { Task } from '../types';

/** Convert snake_case API response to camelCase frontend type. */
export function toCamelCase(data: Record<string, unknown>): ToDo {
  return {
    id: data.id as string,
    workspaceId: data.workspace_id as string,
    title: data.title as string,
    description: (data.description as string) ?? null,
    source: (data.source as string) ?? null,
    sourceType: data.source_type as ToDo['sourceType'],
    status: data.status as ToDo['status'],
    priority: data.priority as ToDo['priority'],
    dueDate: (data.due_date as string) ?? null,
    linkedContext: (data.linked_context as string) ?? null,
    taskId: (data.task_id as string) ?? null,
    // Flow-closure fields (A2) — thread all 7 or the overlay's zone derivation
    // silently sees undefined (the A1 _dict_to_response lesson, frontend side).
    reviewState: (data.review_state as ToDo['reviewState']) ?? null,
    reviewKind: (data.review_kind as ToDo['reviewKind']) ?? null,
    dispatchedSessionId: (data.dispatched_session_id as string) ?? null,
    dispatchedTabLabel: (data.dispatched_tab_label as string) ?? null,
    dispatchedAt: (data.dispatched_at as string) ?? null,
    completedAt: (data.completed_at as string) ?? null,
    reviewedAt: (data.reviewed_at as string) ?? null,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
}

/** History stats shape from GET /api/todos/history/stats (5 aggregations). */
export interface ToDoHistoryStats {
  throughputWeekly: { week: string; created: number; completed: number }[];
  completionRate: number;
  sourceDistribution: Record<string, number>;
  confirmVsAuto: { manual: number; auto: number };
  rejectRate: number;
  totals: { created: number; completed: number; confirmed: number; rejected: number; reviewed: number };
}

/** Convert camelCase frontend request to snake_case for API. */
export function toSnakeCase(
  data: ToDoCreateRequest | ToDoUpdateRequest
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if ('workspaceId' in data && data.workspaceId !== undefined) result.workspace_id = data.workspaceId;
  if (data.title !== undefined) result.title = data.title;
  if (data.description !== undefined) result.description = data.description;
  if ('source' in data && data.source !== undefined) result.source = data.source;
  if ('sourceType' in data && data.sourceType !== undefined) result.source_type = data.sourceType;
  if ('status' in data && data.status !== undefined) result.status = data.status;
  if (data.priority !== undefined) result.priority = data.priority;
  if ('dueDate' in data && data.dueDate !== undefined) result.due_date = data.dueDate;
  return result;
}

export const todosService = {
  /** List ToDos with optional filters. */
  async list(
    workspaceId?: string,
    status?: ToDoStatus,
    limit?: number,
    offset?: number
  ): Promise<ToDo[]> {
    const params = new URLSearchParams();
    if (workspaceId) params.append('workspace_id', workspaceId);
    if (status) params.append('status', status);
    if (limit !== undefined) params.append('limit', String(limit));
    if (offset !== undefined) params.append('offset', String(offset));

    const queryString = params.toString();
    const url = queryString ? `/todos?${queryString}` : '/todos';
    const response = await api.get(url);
    return response.data.map(toCamelCase);
  },

  /** Get a specific ToDo by ID. */
  async get(id: string): Promise<ToDo> {
    const response = await api.get(`/todos/${id}`);
    return toCamelCase(response.data);
  },

  /** Create a new ToDo. */
  async create(data: ToDoCreateRequest): Promise<ToDo> {
    const response = await api.post('/todos', toSnakeCase(data));
    return toCamelCase(response.data);
  },

  /** Update an existing ToDo. */
  async update(id: string, data: ToDoUpdateRequest): Promise<ToDo> {
    const response = await api.put(`/todos/${id}`, toSnakeCase(data));
    return toCamelCase(response.data);
  },

  /** Soft-delete a ToDo (sets status to deleted). */
  async delete(id: string): Promise<void> {
    await api.delete(`/todos/${id}`);
  },

  /** Convert a ToDo to a Task. */
  async convertToTask(id: string): Promise<Task> {
    const response = await api.post(`/todos/${id}/convert-to-task`);
    return response.data;
  },

  /** Mark a ToDo as handled (completed). */
  async markHandled(id: string): Promise<void> {
    await api.post(`/todos/${id}/mark-handled`);
  },

  /** Mark a ToDo as cancelled (dismissed). */
  async markCancelled(id: string): Promise<void> {
    await api.post(`/todos/${id}/mark-cancelled`);
  },

  /** Bind a ToDo to a chat session (drag-to-chat). */
  async bindToSession(sessionId: string, todoId: string): Promise<void> {
    await api.post(`/todos/bind-session/${sessionId}`, { todo_id: todoId });
  },

  // ── ToDo flow-closure (A2) ──────────────────────────────────────────

  /** Dispatch ①→②: record the dead snapshot (tab_label + timestamp now;
   *  session_id backfilled later at the tab's first send). Keeps status=pending. */
  async dispatch(id: string, tabLabel: string, sessionId?: string): Promise<ToDo> {
    const body: Record<string, unknown> = { tab_label: tabLabel };
    if (sessionId) body.session_id = sessionId;
    const response = await api.post(`/todos/${id}/dispatch`, body);
    return toCamelCase(response.data);
  },

  /** ↩ Retreat ②→①: clear the dispatch snapshot (dispatched but never progressed). */
  async retreat(id: string): Promise<ToDo> {
    const response = await api.post(`/todos/${id}/retreat`);
    return toCamelCase(response.data);
  },

  /** Review a ③ Completed todo. confirm→handled/confirmed; reject→rejected + a
   *  NEW pending todo (returns new_todo_id). */
  async review(id: string, action: 'confirm' | 'reject'): Promise<{ action: string; todoId: string; status: string; newTodoId?: string }> {
    const response = await api.post(`/todos/${id}/review`, { action });
    const d = response.data;
    return { action: d.action, todoId: d.todo_id, status: d.status, newTodoId: d.new_todo_id };
  },

  /** History rows (DB recent + archive), most-recent-first, absolute timestamps. */
  async history(limit?: number, windowDays?: number): Promise<{ todos: ToDo[]; count: number }> {
    const params = new URLSearchParams();
    if (limit !== undefined) params.append('limit', String(limit));
    if (windowDays !== undefined) params.append('window_days', String(windowDays));
    const qs = params.toString();
    const response = await api.get(qs ? `/todos/history?${qs}` : '/todos/history');
    return { todos: (response.data.todos ?? []).map(toCamelCase), count: response.data.count ?? 0 };
  },

  /** The 5 History aggregations. */
  async historyStats(): Promise<ToDoHistoryStats> {
    const response = await api.get('/todos/history/stats');
    const d = response.data;
    return {
      throughputWeekly: d.throughput_weekly ?? [],
      completionRate: d.completion_rate ?? 0,
      sourceDistribution: d.source_distribution ?? {},
      confirmVsAuto: d.confirm_vs_auto ?? { manual: 0, auto: 0 },
      rejectRate: d.reject_rate ?? 0,
      totals: d.totals ?? { created: 0, completed: 0, confirmed: 0, rejected: 0, reviewed: 0 },
    };
  },
};
