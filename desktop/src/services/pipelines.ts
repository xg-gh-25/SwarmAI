/**
 * Pipeline dashboard API service layer.
 *
 * Read access to autonomous-pipeline runs for the Radar sidebar's attention
 * queue (paused runs → 🔔) and the bottom FYI bar (running runs). This is the
 * first frontend consumer of the /api/pipelines dashboard endpoint, which has
 * existed backend-side with zero UI consumers.
 *
 * Exports:
 * - pipelinesService  — object with fetchActivePipelines()
 * - PipelineRun       — camelCase frontend representation of one run
 */

import api from './api';

/** camelCase frontend representation of a pipeline run (subset the Radar needs). */
export interface PipelineRun {
  id: string;
  project: string;
  requirement: string;
  status: 'running' | 'paused' | 'completed' | 'failed' | 'cancelled' | 'abandoned';
  currentStage: string;
  /** checkpoint.reason — WHY it paused (the decision text). Null if not paused. */
  checkpointReason: string | null;
  /**
   * Semantic pause classification from the backend (pause_kind):
   * - 'crash_residue' — paused by the orphan-transition when a session died
   *   (NOT a real decision — the Radar attention queue drops these).
   * - 'decision'      — a genuine pause the user must act on (Gate BLOCK / L2 /
   *   budget / retry-exhausted).
   * - null            — not a paused run (running/completed/etc.).
   */
  pauseKind: 'crash_residue' | 'decision' | null;
  /** Stage progress string "N/M" (backend key ``progress``), e.g. "5/8". */
  progress: string;
  /**
   * ISO timestamp of the last update (backend key ``updated_at``). The backend
   * sorts runs by this and trims completed to the 5 most-recent per project, so
   * it IS the "recently completed" recency signal — used to order the Jobs &
   * Runs run list. Empty string when absent.
   */
  updatedAt: string;
}

/** Convert a backend snake_case pipeline run to camelCase PipelineRun. */
export function pipelineToCamelCase(r: Record<string, unknown>): PipelineRun {
  const checkpoint = r.checkpoint as Record<string, unknown> | null | undefined;
  // Derive the "current" stage: for a paused run the checkpoint stage is where
  // it stopped; otherwise fall back to the progress string (e.g. "5/8" → "build"
  // is unknown here, so we surface the raw progress as the stage label).
  const checkpointStage = checkpoint?.stage as string | undefined;
  return {
    id: r.id as string,
    project: r.project as string,
    requirement: r.requirement as string,
    status: r.status as PipelineRun['status'],
    currentStage: checkpointStage ?? (r.progress as string) ?? '',
    checkpointReason: (checkpoint?.reason as string | undefined) ?? null,
    pauseKind: (r.pause_kind as PipelineRun['pauseKind']) ?? null,
    progress: (r.progress as string) ?? '',
    updatedAt: (r.updated_at as string) ?? '',
  };
}

export const pipelinesService = {
  /**
   * Fetch active (running + paused) pipeline runs across all projects.
   * Returns [] on any shape surprise — the sidebar must never crash on this.
   */
  async fetchActivePipelines(): Promise<PipelineRun[]> {
    const response = await api.get<{ pipelines?: Record<string, unknown>[] }>(
      '/pipelines?active=true',
    );
    const rows = response.data?.pipelines ?? [];
    return rows.map(pipelineToCamelCase);
  },

  /**
   * Fetch ALL pipeline runs (active + up to 5 recently-completed per project) —
   * the unfiltered dashboard endpoint. Powers the Jobs & Runs section's run
   * roster, which shows completed runs the attention queue drops. Returns [] on
   * any shape surprise — the sidebar must never crash on this.
   */
  async fetchAllPipelines(): Promise<PipelineRun[]> {
    const response = await api.get<{ pipelines?: Record<string, unknown>[] }>(
      '/pipelines',
    );
    const rows = response.data?.pipelines ?? [];
    return rows.map(pipelineToCamelCase);
  },
};
