/**
 * Swarm Radar API service layer for ToDo and Artifact read operations.
 *
 * Centralizes all Radar API calls with snake_case ↔ camelCase conversion.
 * Follows the same HTTP client pattern as tasks.ts.
 *
 * Exports:
 * - radarService              — Object with ToDo + Artifact fetch methods
 * - toCamelCase               — Converts backend snake_case ToDo response to frontend camelCase
 * - artifactToCamelCase       — Converts backend snake_case artifact to RadarArtifact
 */

import api from './api';
import type { RadarTodo } from '../types';
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

  /** Fetch recently modified artifacts from the workspace git tree. */
  async fetchRecentArtifacts(workspaceId: string, limit?: number): Promise<RadarArtifact[]> {
    const params = new URLSearchParams();
    params.append('workspace_id', workspaceId);
    params.append('limit', String(limit ?? 20));
    const response = await api.get(`/artifacts/recent?${params.toString()}`);
    return response.data.map(artifactToCamelCase);
  },
};
