/**
 * Tasks service for background agent task management.
 */
import api from './api';
import { getBackendPort } from './tauri';
import type { Task, TaskCreateRequest, TaskMessageRequest, TaskStatus } from '../types';

// Convert snake_case to camelCase
function toCamelCase(task: Record<string, unknown>): Task {
  return {
    id: task.id as string,
    agentId: task.agent_id as string,
    sessionId: task.session_id as string | null,
    status: task.status as TaskStatus,
    title: task.title as string,
    model: task.model as string | null,
    createdAt: task.created_at as string,
    startedAt: task.started_at as string | null,
    completedAt: task.completed_at as string | null,
    error: task.error as string | null,
    workDir: task.work_dir as string | null,
  };
}

// Convert camelCase to snake_case for requests
function toSnakeCase(request: TaskCreateRequest): Record<string, unknown> {
  return {
    agent_id: request.agentId,
    message: request.message,
    content: request.content,
    enable_skills: request.enableSkills,
    enable_mcp: request.enableMcp,
    add_dirs: request.addDirs,
  };
}

export const tasksService = {
  /**
   * List all tasks, optionally filtered by status or agent ID.
   */
  async list(status?: TaskStatus, agentId?: string): Promise<Task[]> {
    const params = new URLSearchParams();
    if (status) params.append('status', status);
    if (agentId) params.append('agent_id', agentId);

    const queryString = params.toString();
    const url = queryString ? `/tasks?${queryString}` : '/tasks';

    const response = await api.get(url);
    return response.data.map(toCamelCase);
  },

  /**
   * Get a specific task by ID.
   */
  async get(taskId: string): Promise<Task> {
    const response = await api.get(`/tasks/${taskId}`);
    return toCamelCase(response.data);
  },

  /**
   * Create and start a new background task.
   */
  async create(request: TaskCreateRequest): Promise<Task> {
    const response = await api.post('/tasks', toSnakeCase(request));
    return toCamelCase(response.data);
  },

  /**
   * Delete a task (cancels if running).
   */
  async delete(taskId: string): Promise<void> {
    await api.delete(`/tasks/${taskId}`);
  },

  /**
   * Cancel a running task.
   */
  async cancel(taskId: string): Promise<void> {
    await api.post(`/tasks/${taskId}/cancel`);
  },

  /**
   * Send a message to a running task.
   */
  async sendMessage(taskId: string, request: TaskMessageRequest): Promise<void> {
    await api.post(`/tasks/${taskId}/message`, {
      message: request.message,
      content: request.content,
    });
  },

  /**
   * Get count of running tasks (for sidebar badge).
   */
  async getRunningCount(): Promise<number> {
    const response = await api.get('/tasks/running/count');
    return response.data.count;
  },

  /**
   * Get SSE stream URL for a task.
   */
  getStreamUrl(taskId: string): string {
    const port = getBackendPort();
    return `http://localhost:${port}/api/tasks/${taskId}/stream`;
  },
};
