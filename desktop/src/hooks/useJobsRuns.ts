/**
 * useJobsRuns — data hook for the Radar sidebar's "Jobs & Runs" section.
 *
 * This section restores the job + pipeline-run visibility the Run-1 redesign
 * dropped (the old JobsBar was left orphaned; running pipelines only showed in a
 * bottom FYI bar; completed runs were invisible everywhere). It is the single
 * INVENTORY surface — "what jobs do I have, what runs happened" — as opposed to
 * the 🔔 attention queue, which owns only the ACTIONABLE copies (failing jobs /
 * paused runs). The same item legitimately appears in both: 🔔 = "act on this",
 * Jobs & Runs = "here's the full roster + status". (Option B, run_06b89c00.)
 *
 * Two pure-read backend sources, ZERO backend changes:
 *   1. GET /jobs/      → every scheduled job (name, schedule, status, last-run)
 *   2. GET /pipelines  → active + up to 5 recently-completed runs per project
 *
 * The merge/sort is a pure function (`aggregateJobsRuns`) so it is unit-testable
 * without React. The hook wraps it with a 30s poll, fail-soft (a source that
 * errors contributes nothing — the sidebar must never crash on a transient
 * fetch failure).
 *
 * Exports:
 * - JobRow / RunRow           — view models for the section rows
 * - JobHealth                 — the status-dot state for a job
 * - aggregateJobsRuns         — pure merge + sort (tested directly)
 * - useJobsRuns               — React hook returning { jobs, runs }
 */
import { useState, useEffect, useCallback } from 'react';
import { pipelinesService, type PipelineRun } from '../services/pipelines';
import { jobsService, type JobStatus } from '../services/jobs';

const POLL_MS = 30_000;

/** Status-dot state for a job row (drives the coloured dot). A scheduled job is
 *  never observed mid-run by this snapshot API, so there is no 'running' state —
 *  live execution is a PIPELINE-run concern (RunRow), not a job-status one. */
export type JobHealth = 'failed' | 'disabled' | 'healthy';

/** View model for one scheduled-job row in the Jobs & Runs section. */
export interface JobRow {
  id: string;
  name: string;
  schedule: string;
  health: JobHealth;
  /** ISO timestamp of the last run, or null if never run. */
  lastRun: string | null;
  /** Consecutive failure count (0 when healthy). */
  failures: number;
}

/** View model for one pipeline-run row in the Jobs & Runs section. */
export interface RunRow {
  id: string;
  title: string;
  project: string;
  status: PipelineRun['status'];
  /** Stage progress "N/M". */
  progress: string;
  /** ISO timestamp of last update (sort key). */
  updatedAt: string;
}

export interface JobsRunsResult {
  jobs: JobRow[];
  runs: RunRow[];
}

/** Derive the status-dot state from a job's enabled/failure/last-status fields. */
export function jobHealth(j: JobStatus): JobHealth {
  if (!j.enabled) return 'disabled';
  if (j.consecutiveFailures > 0 || j.lastStatus === 'failed') return 'failed';
  return 'healthy';
}

/** Health sort rank: failed first (most attention), then healthy, then disabled. */
function healthRank(h: JobHealth): number {
  return h === 'failed' ? 0 : h === 'healthy' ? 1 : 2;
}

/** The active run statuses IN DISPLAY ORDER — single source of truth for BOTH
 *  the "worth showing" filter AND the sort rank (so they can't drift: add a
 *  status here and it's both kept and correctly ranked). A run is ACTIVE iff
 *  it's running or paused; completed/failed/cancelled/abandoned are historical
 *  noise — not actionable, and the actionable paused copy also lives in 🔔. */
const ACTIVE_RUN_ORDER: readonly PipelineRun['status'][] = ['running', 'paused'];
const ACTIVE_RUN_STATUSES: ReadonlySet<PipelineRun['status']> = new Set(ACTIVE_RUN_ORDER);

/** Run-status sort rank, derived from ACTIVE_RUN_ORDER (only active runs reach
 *  here). Unknown → end, but the filter guarantees that never happens. */
function runStatusRank(s: PipelineRun['status']): number {
  const i = ACTIVE_RUN_ORDER.indexOf(s);
  return i === -1 ? ACTIVE_RUN_ORDER.length : i;
}

/**
 * Pure merge + sort of the two sources. No I/O, no React — unit-tested.
 * - Jobs: failed → healthy → disabled; stable (backend order) within a rank.
 * - Runs: ONLY active (running + paused), EXCLUDING crash-residue pauses
 *   (pauseKind==='crash_residue' — session-death residue, not an in-flight run;
 *   mirrors the NEEDS YOU guard, run_3d61db5b). completed/failed/cancelled/
 *   abandoned are dropped as historical noise. running → paused; within a
 *   bucket, newest first by updatedAt.
 */
export function aggregateJobsRuns(jobs: JobStatus[], pipelines: PipelineRun[]): JobsRunsResult {
  const jobRows: JobRow[] = jobs.map((j) => ({
    id: j.id,
    name: j.name,
    schedule: j.schedule,
    health: jobHealth(j),
    lastRun: j.lastRun,
    failures: j.consecutiveFailures,
  }));
  jobRows.sort((a, b) => healthRank(a.health) - healthRank(b.health));

  const runRows: RunRow[] = pipelines
    // Active = running/paused, but drop crash-residue pauses: a run the
    // orphan-transition paused when a session died is not actionable roster
    // content (it's residue, not an in-flight run). Mirrors the NEEDS YOU guard
    // (run_3d61db5b) — consume the backend's pause_kind verdict, fail-SHOW on
    // null (old backend / pre-field run still shows, never silently hidden).
    .filter((p) => ACTIVE_RUN_STATUSES.has(p.status) && p.pauseKind !== 'crash_residue')
    .map((p) => ({
      id: p.id,
      title: p.requirement,
      project: p.project,
      status: p.status,
      progress: p.progress,
      updatedAt: p.updatedAt,
    }));
  runRows.sort((a, b) => {
    const byStatus = runStatusRank(a.status) - runStatusRank(b.status);
    if (byStatus !== 0) return byStatus;
    // Same status bucket → newest first (updatedAt desc). Empty timestamps sort last.
    return (b.updatedAt || '').localeCompare(a.updatedAt || '');
  });

  return { jobs: jobRows, runs: runRows };
}

/**
 * React hook: polls /jobs/ + /pipelines (30s) and returns the merged, sorted
 * job + run rows. Fails soft — any source that errors contributes an empty list.
 */
export function useJobsRuns(): JobsRunsResult {
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [pipelines, setPipelines] = useState<PipelineRun[]>([]);

  const poll = useCallback(async () => {
    const [j, p] = await Promise.all([
      jobsService.fetchJobs().catch(() => [] as JobStatus[]),
      pipelinesService.fetchAllPipelines().catch(() => [] as PipelineRun[]),
    ]);
    setJobs(j);
    setPipelines(p);
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  return aggregateJobsRuns(jobs, pipelines);
}
