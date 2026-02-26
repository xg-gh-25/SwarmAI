/**
 * TSCC (Thread-Scoped Cognitive Context) API service.
 *
 * Provides methods to fetch live TSCC state and manage filesystem-based
 * snapshots. Handles snake_case → camelCase conversion per the API naming
 * convention.
 *
 * Key exports:
 * - ``toCamelCase``          — Convert snake_case TSCCState to camelCase
 * - ``snapshotToCamelCase``  — Convert snake_case TSCCSnapshot to camelCase
 * - ``getTSCCState``         — Fetch current TSCC state for a thread
 * - ``createSnapshot``       — Create a point-in-time snapshot
 * - ``listSnapshots``        — List all snapshots for a thread
 * - ``getSnapshot``          — Fetch a single snapshot by ID
 */

import type {
  TSCCActiveCapabilities,
  TSCCLiveState,
  TSCCSnapshot,
  TSCCSource,
  TSCCState,
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

/**
 * Convert a snake_case TSCCSnapshot API response to camelCase.
 */
export function snapshotToCamelCase(
  data: Record<string, unknown>,
): TSCCSnapshot {
  return {
    snapshotId: data.snapshot_id as string,
    threadId: data.thread_id as string,
    timestamp: data.timestamp as string,
    reason: data.reason as string,
    lifecycleState: data.lifecycle_state as TSCCSnapshot['lifecycleState'],
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

// ---------------------------------------------------------------------------
// API methods
// ---------------------------------------------------------------------------

/** Fetch the current TSCC state for a thread. */
export async function getTSCCState(threadId: string): Promise<TSCCState> {
  const response = await api.get(`/chat_threads/${threadId}/tscc`);
  return toCamelCase(response.data as Record<string, unknown>);
}

/** Create a point-in-time snapshot of the thread's TSCC state. */
export async function createSnapshot(
  threadId: string,
  reason: string,
): Promise<TSCCSnapshot> {
  const response = await api.post(`/chat_threads/${threadId}/snapshots`, {
    reason,
  });
  return snapshotToCamelCase(response.data as Record<string, unknown>);
}

/** List all snapshots for a thread in chronological order. */
export async function listSnapshots(
  threadId: string,
): Promise<TSCCSnapshot[]> {
  const response = await api.get(`/chat_threads/${threadId}/snapshots`);
  return (response.data as Record<string, unknown>[]).map(snapshotToCamelCase);
}

/** Fetch a single snapshot by ID. */
export async function getSnapshot(
  threadId: string,
  snapshotId: string,
): Promise<TSCCSnapshot> {
  const response = await api.get(
    `/chat_threads/${threadId}/snapshots/${snapshotId}`,
  );
  return snapshotToCamelCase(response.data as Record<string, unknown>);
}
