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
    description: data.description as string | undefined,
    source: data.source as string | undefined,
    sourceType: data.source_type as ToDo['sourceType'],
    status: data.status as ToDo['status'],
    priority: data.priority as ToDo['priority'],
    dueDate: data.due_date as string | undefined,
    taskId: data.task_id as string | undefined,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
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
};
