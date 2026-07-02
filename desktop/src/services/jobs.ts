/**
 * Scheduled-job status API service layer.
 *
 * Read access to the Swarm Job System's per-job status for the Radar sidebar's
 * attention queue (jobs with consecutive_failures > 0 → 🔔). First frontend
 * consumer of the consecutive_failures field, which exists backend-side but
 * was never surfaced to the UI.
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
}

/** Convert a backend snake_case job status to camelCase JobStatus. */
export function jobToCamelCase(j: Record<string, unknown>): JobStatus {
  return {
    id: (j.id as string) ?? '',
    name: (j.name as string) ?? (j.id as string) ?? 'job',
    consecutiveFailures: (j.consecutive_failures as number) ?? 0,
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
