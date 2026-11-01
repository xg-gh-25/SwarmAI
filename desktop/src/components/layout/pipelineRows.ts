/**
 * pipelineRows — every pipeline run, wherever it ran, as one row shape, and what the
 * Pipelines overlay shows about them (design docs/design/2026-11-01-brain-eval-and-runs.md
 * §4). Pure functions, no React, no fetch.
 *
 * Two sources: this machine's runs (GET /pipelines/analytics: ``PipelineRunSummary``) and
 * the Hive runs Swarm Out dispatched that ran a pipeline (GET /swarm-out/runs, those whose
 * result names a ``pipeline.run_id``; a Hive's native runs stay in Swarm Out). The backend
 * decides each run's state; this file only arranges the rows:
 *  - ``fromLocal`` / ``fromHive``: the adapters;
 *  - ``sections``: Waiting for you (to answer > to confirm > stopped, the longest waiting
 *    first), Running, Done (newest first);
 *  - ``filterRows`` / ``brainChips``: the shared filter (everywhere / this machine / Hive,
 *    and a Brain made from the rows, with counts);
 *  - ``distribution`` and the five measures (``delivery``, ``autonomy``, ``quality``,
 *    ``speed``, ``cost``) with the series their charts draw.
 * A value a run did not record is null and is left out of a measure (with the count it
 * covers), never counted as 0. Tokens are shown in M only (``tokenM``).
 */
import type { PipelineRunSummary } from '../../services/pipelines';
import type { SwarmOutAsk, SwarmOutRun } from '../../services/swarmOut';
import { brainLabel, interventionsOf, tokenTotal } from '../swarm-out/live';
import { stageOf, waitingSince as hiveWaitingSince } from '../swarm-out/runReview';

export type Where = 'local' | 'hive';
export type RowStage = 'needs_answer' | 'awaiting' | 'stopped' | 'running' | 'done';
/** what the row asks of the person: one verb */
export type RowTask = 'answer' | 'confirm' | 'resume' | 'continue' | 'accept' | 'stopped';
export type Outcome = 'landed' | 'manual' | 'not_landed' | 'discarded' | 'failed' | 'running' | 'other';

export interface RowInterventions { answers: number; corrections: number | null; rescues: number; total: number | null }

export interface PipelineRow {
  /** local: the run id; Hive: the dispatch id */
  key: string;
  runId: string | null;
  where: Where;
  hive: string | null;
  title: string;
  brain: string;
  profile: string;
  stage: RowStage;
  task: RowTask | null;
  outcome: Outcome;
  createdAt: string;
  /** when it ended (null while it runs) */
  endedAt: string | null;
  /** since when it waits on the person, else its last change */
  since: string;
  durationMin: number | null;
  tokens: number | null;
  costUsd: number | null;
  interventions: RowInterventions | null;
  audit: { dim: string; verdict: string }[];
  ownerSessionId: string | null;
  local?: PipelineRunSummary;
  hive_run?: SwarmOutRun;
}

const ms = (iso: string | null | undefined): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? NaN : t;
};
const DAY_MS = 24 * 3600 * 1000;

/** A run of this machine (``project`` is its Brain). */
export function fromLocal(r: PipelineRunSummary, project: string): PipelineRow {
  const stage: RowStage = r.needsYou
    ? (r.pauseKind === 'decision' ? 'needs_answer' : r.status === 'abandoned' ? 'awaiting' : 'stopped')
    : r.status === 'running' && !r.pauseKind ? 'running'
      : r.status === 'running' || r.status === 'paused' ? 'stopped' : 'done';
  const task: RowTask | null = !r.needsYou ? null
    : r.pauseKind === 'decision' ? 'answer'
      : r.status === 'abandoned' ? 'confirm'
        : r.pauseKind === 'idle' ? 'continue' : 'resume';
  const outcome: Outcome = r.status === 'running' || r.status === 'paused' ? 'running'
    : r.landing === 'human_override' || (r.status === 'abandoned' && r.landedAs) ? 'manual'
      : r.status === 'completed' ? (r.notLanded ? 'not_landed' : 'landed')
        : r.status === 'abandoned' ? 'discarded'
          : r.status === 'failed' ? 'failed' : 'other';
  const iv = r.interventions;
  return {
    key: r.id, runId: r.id, where: 'local', hive: null, title: r.requirement || r.id, brain: project,
    profile: r.profile, stage, task, outcome, createdAt: r.createdAt, endedAt: r.finishedAt,
    since: r.updatedAt || r.createdAt, durationMin: r.cycleTimeMin, tokens: r.billedTokens ?? r.tokensActual,
    costUsd: r.costUsd,
    interventions: iv ? { answers: iv.answers, corrections: iv.corrections, rescues: iv.rescues, total: iv.total } : null,
    audit: r.audit.map((a) => ({ dim: a.dim, verdict: a.verdict })), ownerSessionId: r.ownerSessionId, local: r,
  };
}

/** A Hive run that ran a pipeline (null for a native one: it stays in Swarm Out). */
export function fromHive(r: SwarmOutRun, asks: SwarmOutAsk[] = []): PipelineRow | null {
  const res = r.result;
  const runId = res?.pipeline?.run_id ?? null;
  if (!runId) return null;
  const s = stageOf(r);
  const stage: RowStage = s === 'needs_answer' || s === 'awaiting' || s === 'stopped' || s === 'running' ? s : 'done';
  const task: RowTask | null = s === 'needs_answer' ? 'answer' : s === 'awaiting' ? 'accept' : s === 'stopped' ? 'stopped' : null;
  const settled = r.settle?.settled?.outcome;
  const outcome: Outcome = s === 'running' ? 'running'
    : settled === 'passed' ? 'landed' : settled === 'abandoned' ? 'discarded' : settled ? 'other'
      : s === 'stopped' ? 'failed' : 'other';
  const ended = res?.ended_at ?? null;
  const start = ms(r.sent_at), end = ms(ended);
  const human = res?.work.human;
  return {
    key: r.dispatch_id, runId, where: 'hive', hive: r.hive, title: r.title, brain: brainLabel(r),
    profile: res?.pipeline?.profile ?? r.project_agent?.profile ?? '', stage, task, outcome,
    createdAt: r.sent_at, endedAt: s === 'running' ? null : ended, since: hiveWaitingSince(r, asks) ?? r.sent_at,
    durationMin: Number.isNaN(start) || Number.isNaN(end) || s === 'running' ? null : Math.round((end - start) / 60000),
    tokens: res ? tokenTotal(res) : null, costUsd: res?.cost_usd ?? null,
    interventions: res && human ? {
      answers: human.answered_questions, corrections: human.said ?? null, rescues: 0,
      total: human.said == null ? null : interventionsOf(res),
    } : null,
    audit: (res?.pipeline?.audit ?? []).map((a) => ({ dim: a.dim, verdict: a.verdict ?? '' })),
    ownerSessionId: r.session_id, hive_run: r,
  };
}

/** Every pipeline row: the local runs of every Brain, then the Hive pipeline runs. */
export function allRows(byProject: { project: string; runs: PipelineRunSummary[] }[] | undefined,
  hive: SwarmOutRun[] | undefined, asks: SwarmOutAsk[] = []): PipelineRow[] {
  const local = (byProject ?? []).flatMap((g) => g.runs.map((r) => fromLocal(r, g.project)));
  const remote = (hive ?? []).map((r) => fromHive(r, asks)).filter((r): r is PipelineRow => !!r);
  return [...local, ...remote];
}

export interface RowFilter { where: 'all' | Where; brain: string | null }

export function filterRows(rows: PipelineRow[], f: RowFilter): PipelineRow[] {
  return rows.filter((r) => (f.where === 'all' || r.where === f.where) && (!f.brain || r.brain === f.brain));
}

/** The Brain filter: every Brain the rows went under, with its count, biggest first. */
export function brainChips(rows: PipelineRow[]): { brain: string; count: number }[] {
  const by = new Map<string, number>();
  for (const r of rows) by.set(r.brain, (by.get(r.brain) ?? 0) + 1);
  return [...by.entries()].map(([brain, count]) => ({ brain, count }))
    .sort((a, b) => (b.count - a.count) || a.brain.localeCompare(b.brain));
}

const YOU_ORDER: RowStage[] = ['needs_answer', 'awaiting', 'stopped'];

export interface RowSections { you: PipelineRow[]; running: PipelineRow[]; done: PipelineRow[] }

/** Waiting for you (to answer > to confirm or accept > stopped, the longest waiting
 *  first; only a row that asks something), Running (newest first), Done (newest first).
 *  A stopped row that asks nothing (a Hive run that only opens its session) is done. */
export function sections(rows: PipelineRow[]): RowSections {
  const you: PipelineRow[] = [], running: PipelineRow[] = [], done: PipelineRow[] = [];
  for (const r of rows) {
    if (r.stage === 'running') running.push(r);
    else if (YOU_ORDER.includes(r.stage) && r.task) you.push(r);
    else done.push(r);
  }
  const rank = (r: PipelineRow) => YOU_ORDER.indexOf(r.stage);
  you.sort((a, b) => (rank(a) - rank(b)) || (ms(a.since) - ms(b.since)));
  running.sort((a, b) => ms(b.createdAt) - ms(a.createdAt));
  done.sort((a, b) => (ms(b.endedAt ?? b.since) || 0) - (ms(a.endedAt ?? a.since) || 0));
  return { you, running, done };
}

/** One word on a row: nobody stepped in, or how often; null when not known yet. */
export function autonomyOf(r: PipelineRow): { full: boolean; n: number } | null {
  const total = r.interventions?.total;
  if (total == null || r.stage === 'running') return null;
  return { full: total === 0, n: total };
}

// ── measures ──────────────────────────────────────────────────────────────────

/** Tokens in M: "684M", "12M", "0.4M"; "—" when unknown. */
export function tokenM(n: number | null | undefined): string {
  if (n == null) return '—';
  const m = n / 1_000_000;
  return m >= 10 ? `${Math.round(m)}M` : `${m.toFixed(1)}M`;
}

const dayKey = (t: number) => new Date(t).toISOString().slice(0, 10);

/** The day (or, once the data spans 14 days, the week starting Monday) a time falls in. */
function bucketOf(t: number, weekly: boolean): string {
  if (!weekly) return dayKey(t);
  const d = new Date(t);
  const back = (d.getUTCDay() + 6) % 7;
  return dayKey(t - back * DAY_MS);
}

function buckets(rows: PipelineRow[], now: number): { weekly: boolean; keys: string[] } {
  const starts = rows.map((r) => ms(r.createdAt)).filter((t) => !Number.isNaN(t));
  if (!starts.length) return { weekly: false, keys: [] };
  const first = Math.min(...starts);
  const weekly = now - first >= 14 * DAY_MS;
  const keys: string[] = [];
  for (let t = first; t <= now + DAY_MS; t += weekly ? 7 * DAY_MS : DAY_MS) {
    const k = bucketOf(t, weekly);
    if (!keys.includes(k)) keys.push(k);
  }
  const last = bucketOf(now, weekly);
  if (!keys.includes(last)) keys.push(last);
  return { weekly, keys: keys.filter((k) => k <= last) };
}

export interface Distribution {
  total: number; local: number; hive: number;
  byHive: { hive: string; count: number }[];
  weekly: boolean;
  points: { label: string; local: number; hive: number }[];
  since: string | null;
}

/** How many runs, where they ran, and when (a bar per day; per week once the data spans
 *  14 days). */
export function distribution(rows: PipelineRow[], now: number): Distribution {
  const local = rows.filter((r) => r.where === 'local').length;
  const by = new Map<string, number>();
  for (const r of rows) if (r.where === 'hive' && r.hive) by.set(r.hive, (by.get(r.hive) ?? 0) + 1);
  const { weekly, keys } = buckets(rows, now);
  const points = keys.map((label) => ({ label, local: 0, hive: 0 }));
  for (const r of rows) {
    const t = ms(r.createdAt);
    if (Number.isNaN(t)) continue;
    const p = points.find((x) => x.label === bucketOf(t, weekly));
    if (p) p[r.where] += 1;
  }
  const starts = rows.map((r) => ms(r.createdAt)).filter((t) => !Number.isNaN(t));
  return {
    total: rows.length, local, hive: rows.length - local,
    byHive: [...by.entries()].map(([hive, count]) => ({ hive, count })).sort((a, b) => b.count - a.count),
    weekly, points, since: starts.length ? new Date(Math.min(...starts)).toISOString() : null,
  };
}

const DELIVERED: Outcome[] = ['landed', 'manual'];

export interface Delivery {
  delivered: number; total: number; rate: number | null;
  counts: Record<'landed' | 'manual' | 'not_landed' | 'discarded' | 'failed' | 'running' | 'other', number>;
  points: { label: string; landed: number; manual: number; not_landed: number; discarded: number }[];
}

/** Was it delivered: landed here, landed by hand, or passed on the Host. */
export function delivery(rows: PipelineRow[], now: number): Delivery {
  const counts = { landed: 0, manual: 0, not_landed: 0, discarded: 0, failed: 0, running: 0, other: 0 };
  for (const r of rows) counts[r.outcome] += 1;
  const delivered = rows.filter((r) => DELIVERED.includes(r.outcome)).length;
  const { weekly, keys } = buckets(rows, now);
  const points = keys.map((label) => ({ label, landed: 0, manual: 0, not_landed: 0, discarded: 0 }));
  for (const r of rows) {
    const t = ms(r.createdAt);
    const p = Number.isNaN(t) ? undefined : points.find((x) => x.label === bucketOf(t, weekly));
    if (!p) continue;
    if (r.outcome === 'landed' || r.outcome === 'manual' || r.outcome === 'discarded') p[r.outcome] += 1;
    else if (r.outcome === 'not_landed' || r.outcome === 'failed') p.not_landed += 1;
  }
  return { delivered, total: rows.length, rate: rows.length ? delivered / rows.length : null, counts, points };
}

export interface Autonomy {
  /** finished runs whose interventions are known, and how many of them nobody stepped into */
  known: number; unattended: number; finished: number;
  avg: number | null;
  bars: { key: string; title: string; answers: number; corrections: number; rescues: number }[];
}

/** Did it run on its own: of the finished runs, how many nobody stepped into, and how
 *  often a person stepped in on average (a run whose count is unknown is left out). */
export function autonomy(rows: PipelineRow[]): Autonomy {
  const finished = rows.filter((r) => r.stage !== 'running' && r.outcome !== 'running');
  const known = finished.filter((r) => r.interventions?.total != null);
  const total = known.reduce((s, r) => s + (r.interventions!.total as number), 0);
  return {
    known: known.length, finished: finished.length,
    unattended: known.filter((r) => r.interventions!.total === 0).length,
    avg: known.length ? total / known.length : null,
    bars: known.filter((r) => (r.interventions!.total as number) > 0).map((r) => ({
      key: r.key, title: r.title, answers: r.interventions!.answers,
      corrections: r.interventions!.corrections ?? 0, rescues: r.interventions!.rescues,
    })),
  };
}

export const DIMS = ['Run Isolation', 'Quality', 'Autonomy', 'Speed', 'Token Cost'] as const;

export interface Quality {
  blocked: number; audited: number;
  /** each dimension's BLOCK count, the ones that blocked */
  blockedDims: { dim: string; count: number }[];
  matrix: { dim: string; PASS: number; WARN: number; BLOCK: number }[];
}

/** Was the quality steady: runs with a BLOCK in their audit, and on what. */
export function quality(rows: PipelineRow[]): Quality {
  const audited = rows.filter((r) => r.audit.length > 0);
  const dims = [...DIMS, ...new Set(audited.flatMap((r) => r.audit.map((a) => a.dim)).filter((d) => !(DIMS as readonly string[]).includes(d)))];
  const matrix = dims.map((dim) => {
    const row = { dim, PASS: 0, WARN: 0, BLOCK: 0 };
    for (const r of audited) {
      const v = r.audit.find((a) => a.dim === dim)?.verdict;
      if (v === 'PASS' || v === 'WARN' || v === 'BLOCK') row[v] += 1;
    }
    return row;
  }).filter((m) => m.PASS + m.WARN + m.BLOCK > 0);
  return {
    blocked: audited.filter((r) => r.audit.some((a) => a.verdict === 'BLOCK')).length, audited: audited.length,
    blockedDims: matrix.filter((m) => m.BLOCK > 0).map((m) => ({ dim: m.dim, count: m.BLOCK })),
    matrix,
  };
}

const quantile = (xs: number[], q: number): number | null => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const i = (s.length - 1) * q;
  const lo = Math.floor(i), hi = Math.ceil(i);
  return Math.round(s[lo] + (s[hi] - s[lo]) * (i - lo));
};

export interface Speed {
  median: number | null; p90: number | null; n: number;
  points: { key: string; title: string; profile: string; min: number }[];
}

/** Was it fast: the median time a finished run took, and the P90 (never the mean: one
 *  run of hours would skew it). */
export function speed(rows: PipelineRow[]): Speed {
  const done = rows.filter((r) => r.durationMin != null && r.stage !== 'running');
  const mins = done.map((r) => r.durationMin as number);
  return {
    median: quantile(mins, 0.5), p90: quantile(mins, 0.9), n: done.length,
    points: done.map((r) => ({ key: r.key, title: r.title, profile: r.profile || '—', min: r.durationMin as number })),
  };
}

export interface Cost {
  tokens: number | null; tokensKnown: number; perDelivery: number | null;
  usd: number | null; usdKnown: number; runs: number;
  bars: { key: string; title: string; tokens: number }[];
  top: string | null;
}

/** Was it costly: tokens per delivery (every run's tokens, delivered or not, over the
 *  deliveries), the total, and the $ where it was recorded (with how many runs it covers). */
export function cost(rows: PipelineRow[]): Cost {
  const withTokens = rows.filter((r) => r.tokens != null);
  const tokens = withTokens.length ? withTokens.reduce((s, r) => s + (r.tokens as number), 0) : null;
  const delivered = rows.filter((r) => DELIVERED.includes(r.outcome)).length;
  const withUsd = rows.filter((r) => r.costUsd != null);
  const bars = withTokens.map((r) => ({ key: r.key, title: r.title, tokens: r.tokens as number }))
    .sort((a, b) => b.tokens - a.tokens);
  return {
    tokens, tokensKnown: withTokens.length, perDelivery: tokens != null && delivered ? tokens / delivered : null,
    usd: withUsd.length ? withUsd.reduce((s, r) => s + (r.costUsd as number), 0) : null, usdKnown: withUsd.length,
    runs: rows.length, bars, top: bars[0]?.key ?? null,
  };
}
