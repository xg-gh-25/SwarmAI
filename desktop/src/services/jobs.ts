/**
 * Scheduled-job status API service layer.
 *
 * Read access to the Swarm Job System's per-job status for the Radar sidebar's
 * attention queue (enabled jobs with consecutive_failures > 0 → 🔔). Surfaces
 * consecutive_failures, enabled, last_run, and last_error — the last so the
 * 🔔 card shows WHY a job failed, not just that it did (run_f1a9b1ab).
 *
 * Exports:
 * - jobsService  — object with fetchJobs()
 * - JobStatus    — camelCase frontend representation of one job's status
 */

import api from './api';

/** camelCase frontend representation of a scheduled job's status. */
export interface JobStatus {
  id: string;
  name: string;
  consecutiveFailures: number;
  /**
   * Whether the job is currently enabled in the scheduler. A DISABLED job
   * (e.g. brain-push, deliberately halted 2026-06-27) must never appear in the
   * 🔔 attention queue — a stopped job's stale failure count is not actionable.
   * Backend key: ``enabled``. Defaults to ``true`` when absent so a shape
   * surprise fails OPEN (a job is shown, not silently hidden).
   */
  enabled: boolean;
  /**
   * ISO timestamp of the job's last run (backend key ``last_run``), or null if
   * it has never run. Surfaced so the attention queue can age out stale
   * one-off failures. Null when absent.
   */
  lastRun: string | null;
  /**
   * Error/summary of the most recent failure (backend key ``last_error``),
   * truncated to 500 chars server-side, or null when the job is healthy /
   * never failed. Surfaced so the 🔔 queue shows WHY a job failed, not just
   * that it did. Null when absent.
   */
  lastError: string | null;
}

/** Convert a backend snake_case job status to camelCase JobStatus. */
export function jobToCamelCase(j: Record<string, unknown>): JobStatus {
  return {
    id: (j.id as string) ?? '',
    name: (j.name as string) ?? (j.id as string) ?? 'job',
    consecutiveFailures: (j.consecutive_failures as number) ?? 0,
    // Fail OPEN: only an explicit `false` disables. Absent/unknown → shown.
    enabled: (j.enabled as boolean) !== false,
    lastRun: (j.last_run as string) ?? null,
    lastError: (j.last_error as string) ?? null,
  };
}

export const jobsService = {
  /**
   * Fetch all scheduled jobs with their status. Returns [] on shape surprise —
   * the sidebar must never crash on this.
   */
  async fetchJobs(): Promise<JobStatus[]> {
    const response = await api.get<Record<string, unknown>[]>('/jobs/');
    const rows = Array.isArray(response.data) ? response.data : [];
    return rows.map(jobToCamelCase);
  },
};
