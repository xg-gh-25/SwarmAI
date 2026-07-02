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
};
