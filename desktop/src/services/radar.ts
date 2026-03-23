/**
 * Swarm Radar API service layer for ToDo, Task, and Autonomous Job operations.
 *
 * Centralizes all Radar API calls with snake_case ↔ camelCase conversion.
 * Follows the same HTTP client pattern as tasks.ts.
 *
 * Exports:
 * - radarService              — Object with ToDo + Task + Job + Artifact fetch/action methods
 * - toCamelCase               — Converts backend snake_case ToDo response to frontend camelCase
 * - toSnakeCase               — Converts frontend camelCase request to backend snake_case
 * - taskToCamelCase           — Converts backend snake_case task to RadarWipTask
 * - completedTaskToCamelCase  — Converts backend snake_case task to RadarCompletedTask
 * - jobToCamelCase            — Converts backend snake_case job to RadarAutonomousJob
 * - artifactToCamelCase       — Converts backend snake_case artifact to RadarArtifact
 */

import api from './api';
import type { RadarTodo, RadarWipTask, RadarCompletedTask, RadarAutonomousJob } from '../types';
import type { RadarArtifact } from '../pages/chat/components/RightSidebar/types';

/** Convert backend snake_case ToDo response to frontend camelCase RadarTodo. */
export function toCamelCase(todo: Record<string, unknown>): RadarTodo {
  return {
    id: todo.id as string,
    workspaceId: todo.workspace_id as string,
    title: todo.title as string,
    description: (todo.description as string) ?? null,
    source: (todo.source as string) ?? null,
    sourceType: todo.source_type as RadarTodo['sourceType'],
    status: todo.status as RadarTodo['status'],
    priority: todo.priority as RadarTodo['priority'],
    dueDate: (todo.due_date as string) ?? null,
    linkedContext: (todo.linked_context as string) ?? null,
    taskId: (todo.task_id as string) ?? null,
    createdAt: todo.created_at as string,
    updatedAt: todo.updated_at as string,
  };
}

/** Convert frontend camelCase fields to backend snake_case for request payloads. */
export function toSnakeCase(todo: Partial<RadarTodo>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (todo.id !== undefined) result.id = todo.id;
  if (todo.workspaceId !== undefined) result.workspace_id = todo.workspaceId;
  if (todo.title !== undefined) result.title = todo.title;
  if (todo.description !== undefined) result.description = todo.description;
  if (todo.source !== undefined) result.source = todo.source;
  if (todo.sourceType !== undefined) result.source_type = todo.sourceType;
  if (todo.status !== undefined) result.status = todo.status;
  if (todo.priority !== undefined) result.priority = todo.priority;
  if (todo.dueDate !== undefined) result.due_date = todo.dueDate;
  if (todo.linkedContext !== undefined) result.linked_context = todo.linkedContext;
  if (todo.taskId !== undefined) result.task_id = todo.taskId;
  if (todo.createdAt !== undefined) result.created_at = todo.createdAt;
  if (todo.updatedAt !== undefined) result.updated_at = todo.updatedAt;
  return result;
}

// ---------------------------------------------------------------------------
// Task conversion helpers (WIP & Completed — Spec 4)
// ---------------------------------------------------------------------------

/** Convert backend snake_case task response to frontend camelCase RadarWipTask. */
export function taskToCamelCase(task: Record<string, unknown>): RadarWipTask {
  return {
    id: task.id as string,
    workspaceId: (task.workspace_id as string) ?? null,
    agentId: task.agent_id as string,
    sessionId: (task.session_id as string) ?? null,
    status: task.status as RadarWipTask['status'],
    title: task.title as string,
    description: (task.description as string) ?? null,
    priority: (task.priority as string) ?? null,
    sourceTodoId: (task.source_todo_id as string) ?? null,
    model: (task.model as string) ?? null,
    createdAt: task.created_at as string,
    startedAt: (task.started_at as string) ?? null,
    error: (task.error as string) ?? null,
    hasWaitingInput: false, // Always false at service layer; computed by Spec 3's hook
  };
}

/** Convert backend snake_case task response to frontend camelCase RadarCompletedTask. */
export function completedTaskToCamelCase(task: Record<string, unknown>): RadarCompletedTask {
  return {
    id: task.id as string,
    workspaceId: (task.workspace_id as string) ?? null,
    agentId: task.agent_id as string,
    sessionId: (task.session_id as string) ?? null,
    title: task.title as string,
    description: (task.description as string) ?? null,
    priority: (task.priority as string) ?? null,
    completedAt: task.completed_at as string,
    reviewRequired: (task.review_required as boolean) ?? false,
    reviewRiskLevel: (task.review_risk_level as string) ?? null,
  };
}

// ---------------------------------------------------------------------------
// Autonomous Job conversion helper (Spec 5)
// ---------------------------------------------------------------------------

/** Convert backend snake_case autonomous job response to frontend camelCase RadarAutonomousJob. */
export function jobToCamelCase(job: Record<string, unknown>): RadarAutonomousJob {
  return {
    id: job.id as string,
    name: job.name as string,
    category: (job.category as string) === 'user_defined'
      ? 'user_defined'
      : 'system' as RadarAutonomousJob['category'],
    status: job.status as RadarAutonomousJob['status'],
    schedule: (job.schedule as string) ?? null,
    lastRunAt: (job.last_run_at as string) ?? null,
    nextRunAt: (job.next_run_at as string) ?? null,
    description: (job.description as string) ?? null,
    totalRuns: (job.total_runs as number) ?? 0,
    consecutiveFailures: (job.consecutive_failures as number) ?? 0,
    lastStatus: (job.last_status as string) ?? null,
  };
}

// ---------------------------------------------------------------------------
// Artifact conversion helper (Spec — Right Sidebar Redesign)
// ---------------------------------------------------------------------------

/** Convert backend snake_case artifact response to frontend camelCase RadarArtifact. */
export function artifactToCamelCase(a: Record<string, unknown>): RadarArtifact {
  return {
    path: a.path as string,
    title: a.title as string,
    type: a.type as RadarArtifact['type'],
    modifiedAt: a.modified_at as string,
  };
}

export const radarService = {
  /** Fetch active ToDos (pending + overdue) for a workspace. */
  async fetchActiveTodos(workspaceId: string): Promise<RadarTodo[]> {
    const params = new URLSearchParams();
    params.append('workspace_id', workspaceId);
    const response = await api.get(`/todos?${params.toString()}`);
    return response.data.map(toCamelCase);
  },

  /** Create a new ToDo via Quick-Add. */
  async createTodo(data: {
    workspaceId: string;
    title: string;
    sourceType?: string;
    priority?: string;
  }): Promise<RadarTodo> {
    const response = await api.post('/todos', {
      workspace_id: data.workspaceId,
      title: data.title,
      source_type: data.sourceType ?? 'manual',
      priority: data.priority ?? 'none',
    });
    return toCamelCase(response.data);
  },

  /** Update a ToDo's status (complete, cancel, delete). */
  async updateTodoStatus(todoId: string, status: string): Promise<void> {
    await api.patch(`/todos/${todoId}`, { status });
  },

  /** Convert a ToDo to a Task. Resolves default agent externally (PE Finding #4). */
  async convertTodoToTask(todoId: string, agentId: string): Promise<unknown> {
    const response = await api.post(`/todos/${todoId}/convert-to-task`, {
      agent_id: agentId,
    });
    return response.data;
  },

  /** Fetch WIP tasks (wip, draft, blocked) for a workspace. */
  async fetchWipTasks(workspaceId?: string): Promise<RadarWipTask[]> {
    const params = new URLSearchParams();
    params.append('status', 'wip,draft,blocked');
    if (workspaceId) params.append('workspace_id', workspaceId);

    const response = await api.get(`/tasks?${params.toString()}`);
    return response.data.map(taskToCamelCase);
  },

  /** Fetch completed tasks within the archive window. */
  async fetchCompletedTasks(
    workspaceId?: string,
    completedAfter?: string,
  ): Promise<RadarCompletedTask[]> {
    const params = new URLSearchParams();
    params.append('status', 'completed');
    if (workspaceId) params.append('workspace_id', workspaceId);
    if (completedAfter) params.append('completed_after', completedAfter);

    const response = await api.get(`/tasks?${params.toString()}`);
    return response.data.map(completedTaskToCamelCase);
  },

  /** Cancel a WIP task via the backend API. */
  async cancelTask(taskId: string): Promise<void> {
    await api.post(`/tasks/${taskId}/cancel`);
  },

  /** Fetch autonomous jobs (system + user-defined). */
  async fetchAutonomousJobs(): Promise<RadarAutonomousJob[]> {
    const response = await api.get('/autonomous-jobs');
    return response.data.map(jobToCamelCase);
  },

  /** Fetch recently modified artifacts from the workspace git tree. */
  async fetchRecentArtifacts(workspaceId: string, limit?: number): Promise<RadarArtifact[]> {
    const params = new URLSearchParams();
    params.append('workspace_id', workspaceId);
    params.append('limit', String(limit ?? 20));
    const response = await api.get(`/artifacts/recent?${params.toString()}`);
    return response.data.map(artifactToCamelCase);
  },
};
