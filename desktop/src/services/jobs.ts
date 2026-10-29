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
  /**
   * Cron/interval schedule string (backend key ``schedule``), e.g. "0 2 * * 1-5"
   * or "after:signal-fetch". Surfaced by the Jobs & Runs section so the user can
   * see WHEN each job runs. Empty string when absent.
   */
  schedule: string;
  /**
   * Outcome of the most recent run (backend key ``last_status``): "success" /
   * "failed" / "skipped" / "never". Drives the Jobs & Runs status dot for a job
   * that is enabled but not currently in a failure streak. Defaults to "never".
   */
  lastStatus: string;
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
    schedule: (j.schedule as string) ?? '',
    lastStatus: (j.last_status as string) ?? 'never',
  };
}

/** Full roster row from GET /api/jobs/ — adds fields the Jobs & Runs overlay needs
 *  beyond the 🔔-queue JobStatus (type, category, source, totalRuns, lastError). */
export interface JobRosterRow extends JobStatus {
  type: string;
  category: string;
  /** "system" (read-only, code-defined) or "user" (editable, user-jobs.yaml). */
  source: string;
  totalRuns: number;
}

/** Overview stats from GET /api/jobs/status → scheduled_jobs. All real, no fabrication. */
export interface JobsOverview {
  total: number;
  enabled: number;
  healthy: number;
  failing: number;
  neverRun: number;
  monthlySpendUsd: number;
}

/** One historical run of a job (from GET /api/jobs/{id}/runs recent[]). */
export interface JobRun {
  date: string;
  status: string;
  tokens: number;
  duration: number;
  hasOutput: boolean;
}

/** Per-job run history from GET /api/jobs/{id}/runs. */
export interface JobRunsResult {
  jobId: string;
  lastOutput: string | null;
  lastOutputDate: string | null;
  recent: JobRun[];
}

/** Full roster row: extends the JobStatus mapping with the overlay-only fields. */
export function jobToRosterRow(j: Record<string, unknown>): JobRosterRow {
  return {
    ...jobToCamelCase(j),
    type: (j.type as string) ?? '',
    category: (j.category as string) ?? '',
    source: (j.source as string) ?? 'user',
    totalRuns: (j.total_runs as number) ?? 0,
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

  /** Full roster (all fields) for the Jobs & Runs overlay. [] on shape surprise. */
  async fetchRoster(): Promise<JobRosterRow[]> {
    const response = await api.get<Record<string, unknown>[]>('/jobs/');
    const rows = Array.isArray(response.data) ? response.data : [];
    return rows.map(jobToRosterRow);
  },

  /** Overview stats strip. Reads GET /api/jobs/status → scheduled_jobs. */
  async fetchOverview(): Promise<JobsOverview> {
    const response = await api.get<Record<string, unknown>>('/jobs/status');
    const sj = (response.data?.scheduled_jobs as Record<string, unknown>) ?? {};
    return {
      total: (sj.total as number) ?? 0,
      enabled: (sj.enabled as number) ?? 0,
      healthy: (sj.healthy as number) ?? 0,
      failing: (sj.failing as number) ?? 0,
      neverRun: (sj.never_run as number) ?? 0,
      monthlySpendUsd: (sj.monthly_spend_usd as number) ?? 0,
    };
  },

  /** Force-run a job now (POST /api/jobs/run). Returns the run status/summary. */
  async runJob(jobId: string): Promise<{ status: string; summary: string }> {
    const response = await api.post<Record<string, unknown>>('/jobs/run', { job_id: jobId });
    return {
      status: (response.data?.status as string) ?? 'unknown',
      summary: (response.data?.summary as string) ?? '',
    };
  },

  /** Per-job run history (GET /api/jobs/{id}/runs). Fail-soft empty on error. */
  async fetchJobRuns(jobId: string): Promise<JobRunsResult> {
    try {
      const response = await api.get<Record<string, unknown>>(`/jobs/${encodeURIComponent(jobId)}/runs`);
      const d = response.data ?? {};
      const recentRaw = Array.isArray(d.recent) ? (d.recent as Record<string, unknown>[]) : [];
      return {
        jobId: (d.job_id as string) ?? jobId,
        lastOutput: (d.last_output as string) ?? null,
        lastOutputDate: (d.last_output_date as string) ?? null,
        recent: recentRaw.map((r) => ({
          date: (r.date as string) ?? '',
          status: (r.status as string) ?? 'unknown',
          tokens: (r.tokens as number) ?? 0,
          duration: (r.duration as number) ?? 0,
          hasOutput: (r.has_output as boolean) ?? false,
        })),
      };
    } catch {
      return { jobId, lastOutput: null, lastOutputDate: null, recent: [] };
    }
  },
};
