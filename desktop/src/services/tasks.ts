/**
 * Tasks service for background agent task management.
 */
import api, { isApiError } from './api';
import { getBackendPort } from './tauri';
import type { Task, TaskCreateRequest, TaskMessageRequest, TaskStatus, PolicyViolationDetail } from '../types';
import { ErrorCodes } from '../types';

// Convert snake_case to camelCase
function toCamelCase(task: Record<string, unknown>): Task {
  return {
    id: task.id as string,
    workspaceId: (task.workspace_id as string) ?? null,
    agentId: task.agent_id as string,
    sessionId: task.session_id as string | null,
    status: task.status as TaskStatus,
    title: task.title as string,
    description: (task.description as string) ?? null,
    priority: (task.priority as string) ?? null,
    sourceTodoId: (task.source_todo_id as string) ?? null,
    blockedReason: (task.blocked_reason as string) ?? null,
    model: task.model as string | null,
    createdAt: task.created_at as string,
    startedAt: task.started_at as string | null,
    completedAt: task.completed_at as string | null,
    error: task.error as string | null,
    workDir: task.work_dir as string | null,
    reviewRequired: (task.review_required as boolean) ?? false,
    reviewRiskLevel: (task.review_risk_level as string) ?? null,
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

/**
 * Error thrown when task creation is blocked by workspace policy.
 * Contains the list of policy violations for UI display.
 */
export class PolicyViolationError extends Error {
  public readonly violations: PolicyViolationDetail[];
  public readonly suggestedAction: string;

  constructor(message: string, violations: PolicyViolationDetail[], suggestedAction: string) {
    super(message);
    this.name = 'PolicyViolationError';
    this.violations = violations;
    this.suggestedAction = suggestedAction;
  }
}

export const tasksService = {
  /**
   * List all tasks, optionally filtered by status, agent ID, or workspace ID.
   */
  async list(status?: TaskStatus, agentId?: string, workspaceId?: string): Promise<Task[]> {
    const params = new URLSearchParams();
    if (status) params.append('status', status);
    if (agentId) params.append('agent_id', agentId);
    if (workspaceId) params.append('workspace_id', workspaceId);

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
   * Throws PolicyViolationError if workspace policy blocks execution (409).
   */
  async create(request: TaskCreateRequest): Promise<Task> {
    try {
      const response = await api.post('/tasks', toSnakeCase(request));
      return toCamelCase(response.data);
    } catch (error) {
      if (isApiError(error) && error.statusCode === 409 && error.code === ErrorCodes.POLICY_VIOLATION) {
        const violations = error.policyViolations ?? [];
        throw new PolicyViolationError(
          error.response.message,
          violations.map(v => ({
            entityType: (v as Record<string, string>).entity_type ?? v.entityType,
            entityId: (v as Record<string, string>).entity_id ?? v.entityId,
            message: v.message,
            suggestedAction: (v as Record<string, string>).suggested_action ?? v.suggestedAction ?? (v as Record<string, string>).suggestedAction,
          })),
          error.suggestedAction ?? 'Enable required capabilities in workspace settings',
        );
      }
      throw error;
    }
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
