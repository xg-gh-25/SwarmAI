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

// ── Retro-Analytics dashboard types (run_f8494370) ──────────────────────────
// The WORK-zone Pipeline NavCard is a RETROSPECTIVE surface — these back
// GET /api/pipelines/analytics + GET /api/pipelines/{run_id}.

export interface PipelineRunSummary {
  id: string;
  requirement: string;
  status: string;
  profile: string;
  progress: string;
  cycleTimeMin: number | null;
  tokensActual: number;
  tokensEst: number;
  createdAt: string;
  updatedAt: string;
  pauseKind: 'crash_residue' | 'decision' | null;
  checkpointReason: string | null;
  /** Workspace-relative REPORT.md path if the run has a report, else null. The row's
   *  report button dispatches swarm:open-file{path} to render it in Canvas. Presence
   *  IS "has a report" — no separate bool (run_929024a8). */
  reportPath: string | null;
}

export interface PipelineProjectGroup {
  project: string;
  runCount: number;
  completionRate: number;
  avgCycleMin: number | null;
  abortedCount: number;
  runs: PipelineRunSummary[];
}

export interface PipelineTrendPoint {
  week: string;
  runs: number;
  completed: number;
  avgCycleMin: number | null;
  tokens: number;
}

export interface PipelineOverall {
  totalRuns: number;
  completed: number;
  completionRate: number;
  avgCycleMin: number | null;
  tokensActual: number;
  tokensEst: number;
  profileMix: Record<string, number>;
  abortedCount: number;
}

export interface PipelineAnalytics {
  window: string;
  overall: PipelineOverall;
  trend: PipelineTrendPoint[];
  byProject: PipelineProjectGroup[];
}

export interface PipelineStageTokens {
  stage: string;
  est: number;
  actual: number;
}

export interface PipelineCommit {
  repo: string;
  sha: string;
  files: string[];
}

export interface PipelineRunDetail {
  id: string;
  project: string;
  requirement: string;
  status: string;
  profile: string;
  cycleTimeMin: number | null;
  reportMd: string;
  /** Workspace-relative REPORT.md path if present, else null. The detail drawer's
   *  "View report in Canvas" button dispatches swarm:open-file{path} (run_929024a8). */
  reportPath: string | null;
  reflectLessons: string[];
  stageTokens: PipelineStageTokens[];
  commits: PipelineCommit[];
  checkpointReason: string | null;
  createdAt: string;
  updatedAt: string;
}

function runSummaryToCamel(r: Record<string, unknown>): PipelineRunSummary {
  return {
    id: r.id as string,
    requirement: (r.requirement as string) ?? '',
    status: (r.status as string) ?? '',
    profile: (r.profile as string) ?? '',
    progress: (r.progress as string) ?? '',
    cycleTimeMin: (r.cycle_time_min as number | null) ?? null,
    tokensActual: (r.tokens_actual as number) ?? 0,
    tokensEst: (r.tokens_est as number) ?? 0,
    createdAt: (r.created_at as string) ?? '',
    updatedAt: (r.updated_at as string) ?? '',
    pauseKind: (r.pause_kind as PipelineRunSummary['pauseKind']) ?? null,
    checkpointReason: (r.checkpoint_reason as string | null) ?? null,
    reportPath: (r.report_path as string | null) ?? null,
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

  /**
   * Fetch the retro-analytics payload: overall rollup + weekly trend + by-project
   * grouping. `window` = '30d' (default) | 'ytd'. Returns a zeroed payload on any
   * shape surprise — the dashboard must never crash on this.
   */
  async fetchAnalytics(window: '30d' | 'ytd' = '30d'): Promise<PipelineAnalytics> {
    const empty: PipelineAnalytics = {
      window,
      overall: {
        totalRuns: 0, completed: 0, completionRate: 0, avgCycleMin: null,
        tokensActual: 0, tokensEst: 0, profileMix: {}, abortedCount: 0,
      },
      trend: [],
      byProject: [],
    };
    try {
      const { data } = await api.get<Record<string, unknown>>(
        `/pipelines/analytics?window=${window}`,
      );
      const o = (data?.overall as Record<string, unknown>) ?? {};
      return {
        window: (data?.window as string) ?? window,
        overall: {
          totalRuns: (o.total_runs as number) ?? 0,
          completed: (o.completed as number) ?? 0,
          completionRate: (o.completion_rate as number) ?? 0,
          avgCycleMin: (o.avg_cycle_min as number | null) ?? null,
          tokensActual: (o.tokens_actual as number) ?? 0,
          tokensEst: (o.tokens_est as number) ?? 0,
          profileMix: (o.profile_mix as Record<string, number>) ?? {},
          abortedCount: (o.aborted_count as number) ?? 0,
        },
        trend: ((data?.trend as Record<string, unknown>[]) ?? []).map((t) => ({
          week: (t.week as string) ?? '',
          runs: (t.runs as number) ?? 0,
          completed: (t.completed as number) ?? 0,
          avgCycleMin: (t.avg_cycle_min as number | null) ?? null,
          tokens: (t.tokens as number) ?? 0,
        })),
        byProject: ((data?.by_project as Record<string, unknown>[]) ?? []).map((g) => ({
          project: (g.project as string) ?? 'unknown',
          runCount: (g.run_count as number) ?? 0,
          completionRate: (g.completion_rate as number) ?? 0,
          avgCycleMin: (g.avg_cycle_min as number | null) ?? null,
          abortedCount: (g.aborted_count as number) ?? 0,
          runs: ((g.runs as Record<string, unknown>[]) ?? []).map(runSummaryToCamel),
        })),
      };
    } catch {
      return empty;
    }
  },

  /**
   * Fetch one run's full retrospective (REPORT + reflect + stage tokens + commits).
   * Returns null on 404/any error — the drawer shows a graceful "not found".
   */
  async fetchRunDetail(runId: string): Promise<PipelineRunDetail | null> {
    try {
      const { data } = await api.get<Record<string, unknown>>(`/pipelines/${runId}`);
      if (!data || !data.id) return null;
      return {
        id: data.id as string,
        project: (data.project as string) ?? '',
        requirement: (data.requirement as string) ?? '',
        status: (data.status as string) ?? '',
        profile: (data.profile as string) ?? '',
        cycleTimeMin: (data.cycle_time_min as number | null) ?? null,
        reportMd: (data.report_md as string) ?? '',
        reportPath: (data.report_path as string | null) ?? null,
        reflectLessons: (data.reflect_lessons as string[]) ?? [],
        stageTokens: ((data.stage_tokens as Record<string, unknown>[]) ?? []).map((s) => ({
          stage: (s.stage as string) ?? '',
          est: (s.est as number) ?? 0,
          actual: (s.actual as number) ?? 0,
        })),
        commits: ((data.commits as Record<string, unknown>[]) ?? []).map((c) => ({
          repo: (c.repo as string) ?? '',
          sha: (c.sha as string) ?? '',
          files: (c.files as string[]) ?? [],
        })),
        checkpointReason: (data.checkpoint_reason as string | null) ?? null,
        createdAt: (data.created_at as string) ?? '',
        updatedAt: (data.updated_at as string) ?? '',
      };
    } catch {
      return null;
    }
  },
};
