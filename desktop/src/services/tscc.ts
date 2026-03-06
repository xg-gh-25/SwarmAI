/**
 * TSCC (Thread-Scoped Cognitive Context) API service.
 *
 * Provides methods to fetch live TSCC state and system prompt metadata.
 * Handles snake_case → camelCase conversion per the API naming convention.
 *
 * Key exports:
 * - ``toCamelCase``                — Convert snake_case TSCCState to camelCase
 * - ``getTSCCState``               — Fetch current TSCC state for a thread
 * - ``getSystemPromptMetadata``    — Fetch system prompt metadata for a session
 */

import type {
  TSCCActiveCapabilities,
  TSCCLiveState,
  TSCCSource,
  TSCCState,
  SystemPromptMetadata,
} from '../types';
import api from './api';

// ---------------------------------------------------------------------------
// snake_case → camelCase converters
// ---------------------------------------------------------------------------

/** Convert a snake_case active_capabilities object to camelCase. */
function capabilitiesToCamelCase(
  data: Record<string, unknown>,
): TSCCActiveCapabilities {
  return {
    skills: (data.skills as string[]) ?? [],
    mcps: (data.mcps as string[]) ?? [],
    tools: (data.tools as string[]) ?? [],
  };
}

/** Convert a snake_case source object to camelCase TSCCSource. */
function sourceToCamelCase(data: Record<string, unknown>): TSCCSource {
  return {
    path: data.path as string,
    origin: data.origin as string,
  };
}

/** Convert a snake_case live_state object to camelCase TSCCLiveState. */
function liveStateToCamelCase(
  data: Record<string, unknown>,
): TSCCLiveState {
  const ctx = data.context as Record<string, unknown>;
  return {
    context: {
      scopeLabel: ctx.scope_label as string,
      threadTitle: ctx.thread_title as string,
      mode: (ctx.mode as string) ?? undefined,
    },
    activeAgents: (data.active_agents as string[]) ?? [],
    activeCapabilities: capabilitiesToCamelCase(
      (data.active_capabilities as Record<string, unknown>) ?? {},
    ),
    whatAiDoing: (data.what_ai_doing as string[]) ?? [],
    activeSources: (
      (data.active_sources as Record<string, unknown>[]) ?? []
    ).map(sourceToCamelCase),
    keySummary: (data.key_summary as string[]) ?? [],
  };
}

/**
 * Convert a snake_case TSCCState API response to a camelCase TSCCState.
 */
export function toCamelCase(data: Record<string, unknown>): TSCCState {
  return {
    threadId: data.thread_id as string,
    projectId: (data.project_id as string) ?? null,
    scopeType: data.scope_type as TSCCState['scopeType'],
    lastUpdatedAt: data.last_updated_at as string,
    lifecycleState: data.lifecycle_state as TSCCState['lifecycleState'],
    liveState: liveStateToCamelCase(
      data.live_state as Record<string, unknown>,
    ),
  };
}

// ---------------------------------------------------------------------------
// API methods
// ---------------------------------------------------------------------------

/** Fetch the current TSCC state for a thread. */
export async function getTSCCState(threadId: string): Promise<TSCCState> {
  const response = await api.get(`/chat_threads/${threadId}/tscc`);
  return toCamelCase(response.data as Record<string, unknown>);
}

/** Fetch system prompt metadata for a session (snake_case → camelCase).
 *  Returns null if the session hasn't been initialized yet (404). */
export async function getSystemPromptMetadata(
  sessionId: string,
): Promise<SystemPromptMetadata | null> {
  try {
    const response = await api.get(`/chat/${sessionId}/system-prompt`);
    const data = response.data as Record<string, unknown>;
    const files = (data.files as Record<string, unknown>[]) ?? [];
    return {
      files: files.map((f) => ({
        filename: f.filename as string,
        tokens: f.tokens as number,
        truncated: f.truncated as boolean,
      })),
      totalTokens: (data.total_tokens as number) ?? 0,
      fullText: (data.full_text as string) ?? '',
    };
  } catch (err: unknown) {
    // 404 is expected when session hasn't been initialized yet
    if (err && typeof err === 'object' && 'response' in err) {
      const axiosErr = err as { response?: { status?: number } };
      if (axiosErr.response?.status === 404) {
        return null;
      }
    }
    throw err;
  }
}
