/**
 * Pipeline dashboard API service layer.
 *
 * Read access to autonomous-pipeline runs for the Radar sidebar's attention
 * queue (paused runs → 🔔) and the bottom FYI bar (running runs). This is the
 * first frontend consumer of the /api/pipelines dashboard endpoint, which has
 * existed backend-side with zero UI consumers.
 *
 * Exports:
 * - pipelinesService  — fetch runs / analytics / detail; resumeRun, answerDecision
 * - PipelineRun       — camelCase frontend representation of one run
 * - PipelinePauseKind, PipelineDecision — what a run needs from the user
 *   (docs/design/pipeline-v1-human-loop.md)
 */

import api from './api';

/**
 * What a run needs from the user (backend ``pause_kind``):
 * - 'decision'      — waiting for a person to answer a question (answer it)
 * - 'stopped'       — v1: the user pressed Stop / closed the owner's tab (Resume)
 * - 'worktree'      — v1: its worktree could not be created (Resume retries)
 * - 'idle'          — v1: active, but no session is driving it (continue in its session)
 * - null            — nothing needed
 */
export type PipelinePauseKind = 'decision' | 'stopped' | 'worktree' | 'idle' | null;

/** One option of a pending v1 decision. */
export interface PipelineDecisionOption {
  id: string;
  choice: string;
  consequence: string;
  reversible: boolean | null;
  risk: string | null;
}

/** The question a v1 run is waiting on a person to answer. */
export interface PipelineDecision {
  requestId: string;
  kind: string | null;
  question: string;
  whyEvidenceInsufficient: string | null;
  options: PipelineDecisionOption[];
}

function decisionToCamel(d: unknown): PipelineDecision | null {
  if (!d || typeof d !== 'object') return null;
  const r = d as Record<string, unknown>;
  if (typeof r.request_id !== 'string') return null;
  return {
    requestId: r.request_id,
    kind: (r.kind as string | null) ?? null,
    question: (r.question as string) ?? '',
    whyEvidenceInsufficient: (r.why_evidence_insufficient as string | null) ?? null,
    options: ((r.options as Record<string, unknown>[]) ?? []).map((o) => ({
      id: (o.id as string) ?? '',
      choice: (o.choice as string) ?? '',
      consequence: (o.consequence as string) ?? '',
      reversible: typeof o.reversible === 'boolean' ? o.reversible : null,
      risk: (o.risk as string | null) ?? null,
    })),
  };
}

/** camelCase frontend representation of a pipeline run (subset the Radar needs). */
export interface PipelineRun {
  id: string;
  project: string;
  requirement: string;
  status: 'running' | 'paused' | 'completed' | 'failed' | 'abandoned';
  /** What the run needs from the user (see PipelinePauseKind). */
  pauseKind: PipelinePauseKind;
  /** Stage progress string "N/M" (backend key ``progress``), e.g. "5/8". */
  progress: string;
  /**
   * ISO timestamp of the last update (backend key ``updated_at``). The backend
   * sorts runs by this and trims completed to the 5 most-recent per project, so
   * it IS the "recently completed" recency signal — used to order the Jobs &
   * Runs run list. Empty string when absent.
   */
  updatedAt: string;
  /** Renderer-owned five-dimension audit card, present after completion. */
  auditCard: string | null;
}

/** Convert a backend snake_case pipeline run to camelCase PipelineRun. */
export function pipelineToCamelCase(r: Record<string, unknown>): PipelineRun {
  return {
    id: r.id as string,
    project: r.project as string,
    requirement: r.requirement as string,
    status: r.status as PipelineRun['status'],
    pauseKind: (r.pause_kind as PipelineRun['pauseKind']) ?? null,
    progress: (r.progress as string) ?? '',
    updatedAt: (r.updated_at as string) ?? '',
    auditCard: (r.audit_card as string | null) ?? null,
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
  /** Measured tokens; null when the run (or any run in the total) was never measured. */
  tokensActual: number | null;
  createdAt: string;
  updatedAt: string;
  pauseKind: PipelinePauseKind;
  /** The user has to act on this run: the backend's one definition (routers.pipelines._needs_you). */
  needsYou: boolean;
  checkpointReason: string | null;
  /** v1: the chat session that drives the run now. */
  ownerSessionId: string | null;
  /** A completed run whose change did not land (it is not on main). */
  notLanded: boolean;
  /** Workspace-relative REPORT.md path if the run has a report, else null. The row's
   *  report button dispatches swarm:open-file{path} to render it in Canvas. Presence
   *  IS "has a report" — no separate bool (run_929024a8). */
  reportPath: string | null;
  auditCard: string | null;
  /** The facts a Run's card shows beside a Hive run's (design 2026-10-30-brain-runs-
   *  one-card); null when the run never recorded them (an older daemon too). */
  finishedAt: string | null;
  activeS: number | null;
  billedTokens: number | null;
  costUsd: number | null;
  humanDecisions: number | null;
  landing: string | null;
  notLandedReason: string | null;
  rework: number | null;
  audit: { dim: string; verdict: string; detail: string }[];
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
  tokens: number | null;
}

export interface PipelineOverall {
  totalRuns: number;
  completed: number;
  completionRate: number;
  avgCycleMin: number | null;
  /** Measured tokens; null when the run (or any run in the total) was never measured. */
  tokensActual: number | null;
  profileMix: Record<string, number>;
  abortedCount: number;
}

export interface PipelineAnalytics {
  window: string;
  overall: PipelineOverall;
  trend: PipelineTrendPoint[];
  byProject: PipelineProjectGroup[];
}

export interface PipelineCommit {
  repo: string;
  sha: string;
  files: string[];
}

export interface PipelineRunDetail {
  id: string;
  project: string;
  /** What the owner session is asked to run to continue (the backend writes the command). */
  continuePrompt: string;
  requirement: string;
  status: string;
  profile: string;
  cycleTimeMin: number | null;
  reportMd: string;
  /** Workspace-relative REPORT.md path if present, else null. The detail drawer's
   *  "View report in Canvas" button dispatches swarm:open-file{path} (run_929024a8). */
  reportPath: string | null;
  reflectLessons: string[];
  commits: PipelineCommit[];
  checkpointReason: string | null;
  pauseKind: PipelinePauseKind;
  /** The pending question when pauseKind is 'decision'. */
  decision: PipelineDecision | null;
  /** v1: the chat session that drives the run now. */
  ownerSessionId: string | null;
  createdAt: string;
  updatedAt: string;
  auditCard: string | null;
}

function runSummaryToCamel(r: Record<string, unknown>): PipelineRunSummary {
  return {
    id: r.id as string,
    requirement: (r.requirement as string) ?? '',
    status: (r.status as string) ?? '',
    profile: (r.profile as string) ?? '',
    progress: (r.progress as string) ?? '',
    cycleTimeMin: (r.cycle_time_min as number | null) ?? null,
    tokensActual: typeof r.tokens_actual === 'number' ? r.tokens_actual : null,
    createdAt: (r.created_at as string) ?? '',
    updatedAt: (r.updated_at as string) ?? '',
    pauseKind: (r.pause_kind as PipelineRunSummary['pauseKind']) ?? null,
    needsYou: r.needs_you === true,
    checkpointReason: (r.checkpoint_reason as string | null) ?? null,
    ownerSessionId: (r.owner_session_id as string | null) ?? null,
    notLanded: r.not_landed === true,
    reportPath: (r.report_path as string | null) ?? null,
    auditCard: (r.audit_card as string | null) ?? null,
    finishedAt: typeof r.finished_at === 'string' ? r.finished_at : null,
    activeS: num(r.active_s),
    billedTokens: num(r.billed_tokens),
    costUsd: num(r.cost_usd),
    humanDecisions: num(r.human_decisions),
    landing: typeof r.landing === 'string' ? r.landing : null,
    notLandedReason: typeof r.not_landed_reason === 'string' ? r.not_landed_reason : null,
    rework: num(r.rework),
    audit: Array.isArray(r.audit)
      ? (r.audit as { dim?: unknown; verdict?: unknown; detail?: unknown }[])
        .filter((a) => typeof a?.dim === 'string')
        .map((a) => ({ dim: a.dim as string, verdict: typeof a.verdict === 'string' ? a.verdict : '',
          detail: typeof a.detail === 'string' ? a.detail : '' }))
      : [],
  };
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export const pipelinesService = {
  /** Resume a stopped v1 run (appends a signed ``resume`` event). Nothing drives the
   *  run until its session continues it: returns that session's id, or null. */
  async resumeRun(runId: string): Promise<{ ownerSessionId: string | null }> {
    const { data } = await api.patch<Record<string, unknown>>(`/pipelines/${runId}/resume`);
    return { ownerSessionId: (data?.owner_session_id as string | null) ?? null };
  },

  /** A person answers a run's pending decision. Throws on a stale request (409). */
  async answerDecision(
    runId: string, requestId: string, optionId: string,
  ): Promise<{ ownerSessionId: string | null }> {
    const { data } = await api.post<Record<string, unknown>>(`/pipelines/${runId}/decision`, {
      request_id: requestId, option_id: optionId,
    });
    return { ownerSessionId: (data?.owner_session_id as string | null) ?? null };
  },

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
   * grouping. `window` is always '30d' (the run window). Returns a zeroed payload on any
   * shape surprise — the dashboard must never crash on this.
   */
  async fetchAnalytics(window: '30d' = '30d'): Promise<PipelineAnalytics> {
    const empty: PipelineAnalytics = {
      window,
      overall: {
        totalRuns: 0, completed: 0, completionRate: 0, avgCycleMin: null,
        tokensActual: null, profileMix: {}, abortedCount: 0,
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
          tokensActual: typeof o.tokens_actual === 'number' ? o.tokens_actual : null,
          profileMix: (o.profile_mix as Record<string, number>) ?? {},
          abortedCount: (o.aborted_count as number) ?? 0,
        },
        trend: ((data?.trend as Record<string, unknown>[]) ?? []).map((t) => ({
          week: (t.week as string) ?? '',
          runs: (t.runs as number) ?? 0,
          completed: (t.completed as number) ?? 0,
          avgCycleMin: (t.avg_cycle_min as number | null) ?? null,
          tokens: typeof t.tokens === 'number' ? t.tokens : null,
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
   * Fetch one run's full retrospective (REPORT + reflect + commits + decision).
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
        commits: ((data.commits as Record<string, unknown>[]) ?? []).map((c) => ({
          repo: (c.repo as string) ?? '',
          sha: (c.sha as string) ?? '',
          files: (c.files as string[]) ?? [],
        })),
        checkpointReason: (data.checkpoint_reason as string | null) ?? null,
        pauseKind: (data.pause_kind as PipelinePauseKind) ?? null,
        decision: decisionToCamel(data.decision),
        ownerSessionId: (data.owner_session_id as string | null) ?? null,
        continuePrompt: (data.continue_prompt as string) ?? '',
        createdAt: (data.created_at as string) ?? '',
        updatedAt: (data.updated_at as string) ?? '',
        auditCard: (data.audit_card as string | null) ?? null,
      };
    } catch {
      return null;
    }
  },
};
