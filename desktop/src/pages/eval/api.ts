/**
 * OS Eval overlay — data contract with /api/eval/* (backend routers/eval.py,
 * core/eval_service.py) and the hooks every tab reads through.
 *
 * Shapes mirror the backend projections one to one; a field the backend does not
 * send is optional here, never assumed. Design: docs/design/2026-10-26-eval-as-product.md §8.
 *
 * @exports types for health / history / golden set / run / online / judges, and the
 *          query + mutation hooks of the six tabs
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../services/api';

// ─── shared ────────────────────────────────────────────────────────────────

export type Bilingual = { en?: string; zh?: string };
/** A run's release decision (core/eval_release.decide_release). */
export type ReleaseVerdict = 'release' | 'blocked' | 'incomplete';
/** How much say a case has in a release (core/eval_release.tier_of). */
export type ReleaseTier = 'red' | 'gate' | 'watch';
export type Dimension = 'cognition' | 'quality' | 'responsibility' | 'cost' | 'performance';
export const CASE_DIMENSIONS: Dimension[] = ['cognition', 'quality', 'responsibility'];

export interface ReleaseCheck {
  id: 'redline' | 'dimensions' | 'denominator' | 'record' | string;
  passed: boolean;
  selected?: number | null;
  with_result?: number | null;
  reason?: string | null;
  cases?: { id: string; status?: string; dimension?: string; kind?: string }[];
  known_issues?: { id: string; kind?: string; status?: string }[];
}
export interface ReleaseBlock { verdict: ReleaseVerdict; reason: string; checks: ReleaseCheck[] }

/** dimension → tier (gate / red / watch) → counts (core/eval_release.summarize_dimensions). */
export type DimensionSummary = Record<string, Record<string, { passed: number; failed: number; not_run: number }>>;

// ─── /health ───────────────────────────────────────────────────────────────

/** A follow-up (core/eval_followups.build_follow_ups). ``kind`` eval = a problem with
 *  the evaluation itself; evolution = a signal about the agent's behaviour. */
export interface FollowUp {
  kind?: 'eval' | 'evolution';
  severity: 'high' | 'medium' | 'low';
  rule: number;
  what: Bilingual;
  /** ``what`` without the case names (they are listed by ``case_ids``) */
  headline?: Bilingual;
  why: string;
  action: Bilingual;
  case_ids: string[];
}
export interface QuickDay {
  date: string; passed: number | null; failed: number | null; timeout: number | null;
  total: number | null; not_run: number | null; verdict: ReleaseVerdict | null;
}
export interface EvalLines {
  quick: { last_run: string | null; passed: number | null; total: number | null; days?: QuickDay[] };
  full: { last_run: string | null; release: ReleaseVerdict | null; next_run: string | null };
  online: { last_run: string | null; sessions_checked: number | null; next_run: string | null };
  golden_set: { product: number; private: number; total: number; product_cap: number; total_cap: number };
}
export interface EvalHealth {
  release: ReleaseBlock | null;
  dimension_summary: DimensionSummary;
  last_run: {
    run_id: string; triggered_by: string; triggered_at: string;
    /** "failed" = the run crashed before it had a result */
    status?: string | null; error?: string | null;
    cases_passed: number; cases_failed: number; cases_skipped: number;
    cases_error?: number; cases_timeout?: number;
  } | null;
  total_cases: number;
  follow_ups?: FollowUp[];
  evolution?: FollowUp[];
  lines?: EvalLines;
}

// ─── /history, /runs/{id} ──────────────────────────────────────────────────

export interface EvalRun {
  run_id: string;
  /** full = a comparable full run, quick = a quick check, probe = a hand-run subset
   *  (core/eval_followups.run_line). Only quick and full runs count for a release. */
  line?: 'full' | 'quick' | 'probe';
  /** "failed" = the run crashed before it had a result */
  status?: string | null;
  error?: string | null;
  triggered_by: string;
  triggered_at: string;
  release: ReleaseVerdict | null;
  release_reason?: string | null;
  release_checks?: ReleaseCheck[];
  /** The HTML report of this run's day (GET /eval/reports/{report}), or null. */
  report?: string | null;
  dimension_summary?: DimensionSummary;
  total_cases: number;
  cases_passed: number;
  cases_failed: number;
  cases_skipped: number;
  cases_error?: number;
  cases_timeout?: number;
  duration_seconds: number;
}
export interface RunStep { tool: string; input?: string | null; output?: string | null; is_error?: boolean }
export interface RunCaseResult {
  id: string;
  status: string;
  evaluator?: string;
  duration_ms?: number;
  notes?: string;
  /** Offline cases (eval_runner._RECORD_DETAIL): the drill-down. */
  final_text?: string;
  observed_trajectory?: string[];
  expected_check?: { step: string; found: boolean }[];
  steps?: RunStep[];
}
export interface RunDetail {
  run_id: string;
  triggered_by?: string;
  triggered_at?: string;
  release?: ReleaseBlock | null;
  total_cases?: number;
  duration_seconds?: number;
  cases?: RunCaseResult[];
  status?: string;
}

// ─── /golden-set ───────────────────────────────────────────────────────────

export interface GoldenCase {
  id: string;
  category: string;
  dimension: string;
  level?: string;
  title?: string;
  purpose?: Bilingual | null;
  tier: string;
  release_tier?: ReleaseTier;
  eval_method?: string;
  _origin?: 'product' | 'private' | string;
  affected_by: string[];
  evaluators: string[];
  last_result: { status: string; run_id: string; triggered_at: string } | null;
}
export interface GoldenSet { total_cases: number; cases: GoldenCase[] }
export interface CaseDetail extends GoldenCase {
  gating?: string;
  redline?: boolean;
  verification?: { command?: string; expected_contains?: string; negative_command?: string;
                   negative_expected_contains?: string; [k: string]: unknown };
  scenario?: { prompt?: string; system?: string; [k: string]: unknown };
  expected_trajectory?: string[];
  trajectory_match?: string;
  allowed_tools?: string[];
  decision_rubric?: string;
  history?: { run_id: string; triggered_at: string; status: string; notes?: string }[];
}
/** The form's payload (routers/eval.py CreateCaseRequest). */
export interface CaseInput {
  id: string;
  category: string;
  dimension: string;
  title?: string;
  purpose: Bilingual;
  affected_by: string[];
  evaluators: string[];
  eval_method: 'programmatic' | 'behavior';
  verification?: CaseDetail['verification'];
  scenario?: { prompt: string; system?: string; [k: string]: unknown };
  expected_trajectory?: string[];
  trajectory_match?: string;
  allowed_tools?: string[];
  decision_rubric?: string;
}
/** POST /golden-set/try: every admission check (a rejected save is a 400 whose detail names the failing checks). */
export interface AdmissionResult {
  ok: boolean;
  checks: { name: string; ok: boolean; errors: string[] }[];
  trial: { status: string; notes: string } | null;
}

// ─── /session-quality ──────────────────────────────────────────────────────

export interface OnlineExample { session_id?: string; title?: string; snippet?: string; reason?: string }
export interface OnlineCheck { checked: number | null; failed: number | null; rate: number | null; examples: OnlineExample[] }
export interface OnlineMetrics { turns: number | null; turn_p50_s: number | null; cost_p50_usd: number | null }
export interface OnlineLow { session_id?: string; title?: string; goal_score?: number; tool_score?: number; dimension?: string; reason?: string }
export interface OnlineTrendWeek { week: string; at?: string | null; rates: Record<string, number>; metrics: OnlineMetrics; backfilled?: boolean }
export interface OnlineScoring {
  last_run: string | null;
  window_days: number | null;
  sessions_checked: number;
  checks: Record<string, OnlineCheck>;
  low_details: OnlineLow[];
  sampler_error: string | null;
  judge_error: string | null;
  /** sampled sessions, and how many of them the judge failed on (not in O4/O5) */
  judge_sampled?: number | null;
  judge_failed?: number | null;
  metrics?: OnlineMetrics;
  trend: OnlineTrendWeek[];
  calibration?: { accepting: boolean; reason: string | null; labels: Record<string, 'agree' | 'disagree'> };
}

// ─── /judges ───────────────────────────────────────────────────────────────

export interface JudgeInfo {
  id: 'offline_decision' | 'online_session' | string;
  line: 'offline' | 'online' | string;
  prompt: string;
  system: string;
  temperature: number;
  max_tokens: number;
  case_ids?: string[];
  sample_cap?: number;
  low_threshold?: number;
  prompt_sha?: string;
  calibration: { prompt_sha: string; labels: number; agree: number; agreement: number | null } | null;
}
export interface JudgesPage {
  judge_model: string;
  agent_model: string | null;
  cases_total: number;
  cases_judged: number;
  gating_total: number;
  gating_judged: number;
  judges: JudgeInfo[];
}

// ─── hooks ─────────────────────────────────────────────────────────────────

const get = async <T,>(url: string) => (await api.get<T>(url)).data;
const fresh = { staleTime: 60_000 };

export const useEvalHealth = () => useQuery<EvalHealth>({ queryKey: ['eval-health'], queryFn: () => get('/eval/health'), ...fresh });
export const useEvalHistory = () => useQuery<EvalRun[]>({ queryKey: ['eval-history'], queryFn: () => get('/eval/history?limit=60'), ...fresh });
export const useGoldenSet = () => useQuery<GoldenSet>({ queryKey: ['eval-golden-set'], queryFn: () => get('/eval/golden-set'), ...fresh });
export const useSessionQuality = () => useQuery<OnlineScoring>({ queryKey: ['eval-session-quality'], queryFn: () => get('/eval/session-quality'), ...fresh });
export const useJudges = () => useQuery<JudgesPage>({ queryKey: ['eval-judges'], queryFn: () => get('/eval/judges'), ...fresh });
export const useCaseDetail = (id: string | null) => useQuery<CaseDetail>({
  queryKey: ['eval-case-detail', id], queryFn: () => get(`/eval/golden-set/${encodeURIComponent(id ?? '')}`), enabled: !!id, staleTime: 30_000,
});
export const useRunDetail = (runId: string | null) => useQuery<RunDetail>({
  queryKey: ['eval-run-detail', runId], queryFn: () => get(`/eval/runs/${encodeURIComponent(runId ?? '')}`), enabled: !!runId, staleTime: 30_000,
});

/** Refresh everything a case change or a run can move. */
function useInvalidateEval() {
  const qc = useQueryClient();
  return () => {
    for (const k of ['eval-health', 'eval-history', 'eval-golden-set', 'eval-case-detail', 'eval-judges', 'eval-run-detail']) {
      qc.invalidateQueries({ queryKey: [k] });
    }
  };
}

/** The detail of a rejected request, as the backend wrote it. */
export function errorDetail(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof d === 'string') return d;
  return e instanceof Error ? e.message : String(e);
}

export function useTryCase() {
  return useMutation({ mutationFn: async (c: CaseInput) => (await api.post<AdmissionResult>('/eval/golden-set/try', c)).data });
}
export function useSaveCase() {
  const refresh = useInvalidateEval();
  return useMutation({
    mutationFn: async ({ c, editing }: { c: CaseInput; editing: boolean }) => editing
      ? (await api.put(`/eval/golden-set/${encodeURIComponent(c.id)}`, { ...c, id: undefined })).data
      : (await api.post('/eval/golden-set', c)).data,
    onSuccess: refresh,
  });
}
export function useDeleteCase() {
  const refresh = useInvalidateEval();
  return useMutation({ mutationFn: async (id: string) => (await api.delete(`/eval/golden-set/${encodeURIComponent(id)}`)).data, onSuccess: refresh });
}
/** How long a started run is watched: a run killed mid-flight leaves its in-flight
 *  marker (status running) behind and would otherwise be polled forever. */
const WATCH_MS = 30 * 60_000;

/** Watch a started run (GET /runs/{id} answers status running from its in-flight
 *  marker) and refresh everything once it is recorded. True while it runs. */
export function useRunProgress(runId: string | null | undefined): { running: boolean; failed: string | null } {
  const refresh = useInvalidateEval();
  // the watch ends WATCH_MS after the run id arrives (disabling the query stops the polling)
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    setExpired(false);
    if (!runId) return;
    const timer = setTimeout(() => setExpired(true), WATCH_MS);
    return () => clearTimeout(timer);
  }, [runId]);
  const q = useQuery<RunDetail & { error?: string }>({
    queryKey: ['eval-run-progress', runId], queryFn: () => get(`/eval/runs/${encodeURIComponent(runId ?? '')}`),
    enabled: !!runId && !expired, retry: false,
    refetchInterval: (query) => (query.state.status === 'error' || (query.state.data && query.state.data.status !== 'running') ? false : 5000),
  });
  const done = !!q.data && q.data.status !== 'running';
  useEffect(() => { if (runId && done) refresh(); }, [runId, done]);  // eslint-disable-line react-hooks/exhaustive-deps
  const running = !!runId && !done && !q.isError && !expired;
  const failed = done && q.data?.status === 'failed' ? (q.data.error || 'failed') : null;
  return { running, failed };
}

/** Run a set of cases now (offline cases spawn a real agent, so they opt in). */
export function useRunCases() {
  return useMutation({
    mutationFn: async ({ ids, behavior }: { ids: string[]; behavior: boolean }) =>
      (await api.post<{ run_id: string }>('/eval/run-cases', { trigger: 'manual', case_ids: ids, include_behavior: behavior })).data,
  });
}
export function useRunQuick() {
  const refresh = useInvalidateEval();
  return useMutation({ mutationFn: async () => (await api.post('/eval/canary', {})).data, onSuccess: refresh });
}
export function useRunFull() {
  return useMutation({
    mutationFn: async () => (await api.post<{ run_id: string }>('/eval/run', { trigger: 'manual', include_behavior: true })).data,
  });
}
export function useRunOnline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => (await api.post('/jobs/run', { job_id: 'session-quality' })).data,
    onSuccess: () => {  // the job runs inside the request; health carries its follow-ups and line
      qc.invalidateQueries({ queryKey: ['eval-session-quality'] });
      qc.invalidateQueries({ queryKey: ['eval-health'] });
    },
  });
}
export function useCalibrate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (p: { session_id: string; human: 'agree' | 'disagree' }) => (await api.post('/eval/calibration', p)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-session-quality'] });
      qc.invalidateQueries({ queryKey: ['eval-judges'] });
    },
  });
}

/** The judge model setting (routers/settings.py validates it is resolvable). */
export function useJudgeModelSetting() {
  const qc = useQueryClient();
  const query = useQuery<{ eval_judge_model?: string; available_models?: string[] }>({
    queryKey: ['settings'], queryFn: () => get('/settings'), staleTime: 60_000,
  });
  const save = useMutation({
    mutationFn: async (model: string) => (await api.put('/settings', { eval_judge_model: model })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['eval-judges'] });
    },
  });
  return { query, save };
}
