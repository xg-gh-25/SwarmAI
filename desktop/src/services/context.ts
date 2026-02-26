/**
 * Context assembly preview service.
 *
 * Provides methods to fetch the assembled context preview for a project
 * and bind tasks/todos to chat threads mid-session. Handles snake_case →
 * camelCase conversion per the API naming convention, and supports
 * ETag-based caching to avoid redundant requests when context is unchanged.
 *
 * Key exports:
 * - ``getContextPreview``   — Fetch context preview with ETag support
 * - ``bindThread``          — Bind task/todo to a thread mid-session
 * - ``toCamelCase``         — Convert snake_case API response to camelCase ContextPreview
 * - ``layerToCamelCase``    — Convert a single snake_case layer to camelCase ContextLayer
 */

import type { ContextLayer, ContextPreview, ThreadBindResponse } from '../types';
import api from './api';
import { getBackendPort } from './tauri';

/** Per-project ETag cache for conditional requests. */
const etagCache = new Map<string, string>();

/** Per-project cached response for 304 reuse. */
const responseCache = new Map<string, ContextPreview>();

/**
 * Convert a single snake_case layer response to a camelCase ContextLayer.
 */
export function layerToCamelCase(data: Record<string, unknown>): ContextLayer {
  return {
    layerNumber: data.layer_number as number,
    name: data.name as string,
    sourcePath: data.source_path as string,
    tokenCount: data.token_count as number,
    contentPreview: data.content_preview as string,
    truncated: data.truncated as boolean,
    truncationStage: (data.truncation_stage as number) ?? 0,
  };
}

/**
 * Convert a snake_case ContextPreviewResponse to a camelCase ContextPreview.
 */
export function toCamelCase(data: Record<string, unknown>): ContextPreview {
  return {
    projectId: data.project_id as string,
    threadId: (data.thread_id as string) ?? null,
    layers: ((data.layers as Record<string, unknown>[]) ?? []).map(layerToCamelCase),
    totalTokenCount: data.total_token_count as number,
    budgetExceeded: data.budget_exceeded as boolean,
    tokenBudget: data.token_budget as number,
    truncationSummary: (data.truncation_summary as string) ?? '',
    etag: (data.etag as string) ?? '',
  };
}

/**
 * Fetch the assembled context preview for a project.
 *
 * Uses raw fetch (not axios) to handle 304 Not Modified responses
 * gracefully — the axios interceptor would reject 304 as an error.
 *
 * ETag flow:
 * 1. On first request, stores the returned ETag per project.
 * 2. On subsequent requests, sends If-None-Match header.
 * 3. On 304, returns the previously cached response.
 * 4. On 200, updates the cache and returns the new data.
 *
 * Returns null only when the server returns 304 and no prior cache exists
 * (should not happen in practice).
 */
export async function getContextPreview(
  projectId: string,
  threadId?: string,
  tokenBudget?: number,
): Promise<ContextPreview | null> {
  const params = new URLSearchParams();
  if (threadId) params.set('thread_id', threadId);
  if (tokenBudget !== undefined) params.set('token_budget', String(tokenBudget));

  const port = getBackendPort();
  const query = params.toString();
  const url = `http://localhost:${port}/api/projects/${projectId}/context${query ? `?${query}` : ''}`;

  const headers: Record<string, string> = {};
  const cachedEtag = etagCache.get(projectId);
  if (cachedEtag) {
    headers['If-None-Match'] = cachedEtag;
  }

  const response = await fetch(url, { headers });

  if (response.status === 304) {
    // Context unchanged — return cached data
    return responseCache.get(projectId) ?? null;
  }

  if (!response.ok) {
    throw new Error(`Context preview request failed: ${response.status}`);
  }

  const data = await response.json();
  const result = toCamelCase(data);

  // Update ETag and response caches
  if (result.etag) {
    etagCache.set(projectId, result.etag);
  }
  responseCache.set(projectId, result);

  return result;
}

/**
 * Bind a task and/or todo to a chat thread mid-session.
 *
 * Sends snake_case body to the backend and converts the response
 * to camelCase ThreadBindResponse.
 */
export async function bindThread(
  threadId: string,
  request: { taskId?: string; todoId?: string; mode: 'replace' | 'add' },
): Promise<ThreadBindResponse> {
  const response = await api.post(`/chat_threads/${threadId}/bind`, {
    task_id: request.taskId,
    todo_id: request.todoId,
    mode: request.mode,
  });
  const data = response.data as Record<string, unknown>;
  return {
    threadId: data.thread_id as string,
    taskId: (data.task_id as string) ?? null,
    todoId: (data.todo_id as string) ?? null,
    contextVersion: data.context_version as number,
  };
}
