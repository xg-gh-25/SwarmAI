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
 *  - ``autonomy`` and ``speed``, read by the Eval measures;
 *  - the Line (``trackOf``, ``lineGroups``, ``stationCounts``, ``verdictOf``,
 *    ``timeGoes``) and the Eval (``evalMeasures``, ``evalByBrain``, ``wilson``), in the
 *    CNTech & IVT S7 order;
 *  - ``brainLanes``: the runs of one Brain going now, one lane each, for the Brain map's
 *    loop (design 2026-11-01-brain-eval-and-runs §2.4: the map reads these same rows,
 *    never a second reading of the runs).
 * A value a run did not record is null and is left out of a measure (with the count it
 * covers), never counted as 0. Tokens are shown in M only (``tokenM``).
 */
import type { PipelineRunSummary, PipelineStageInfo, PipelineStageTime } from '../../services/pipelines';
import type { SwarmOutAsk, SwarmOutRun } from '../../services/swarmOut';
import { brainLabel, interventionsOf, tokenTotal } from '../swarm-out/live';
import { actionsOf, DONE_STAGES, stageOf, waitingSince as hiveWaitingSince } from '../swarm-out/runReview';
import { dispatchTrack, type ChipKind } from '../swarm-out/track';

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
  /** the kind of work of the Brain's Project Agent the run went under; null: none recorded */
  agentMode: string | null;
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
  /** the pipeline stage it is at (a local run); null when not recorded or on a Hive */
  pipelineStage: string | null;
  /** its profile's stages in order with status and attempts (a local run), else null */
  stages: PipelineStageInfo[] | null;
  /** per stage entered: when, and seconds working / waiting on a person; null if none */
  timeline: Record<string, PipelineStageTime> | null;
  /** a Swarm Run's work package: the lead pipeline run's id, and the package */
  parentRun: string | null;
  wp: string | null;
  /** entries given and written back (a local run); null when not read */
  knowledge: { used: number | null; new: number | null } | null;
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
    agentMode: r.agentMode ?? null, profile: r.profile, stage, task, outcome, createdAt: r.createdAt, endedAt: r.finishedAt,
    since: r.updatedAt || r.createdAt, durationMin: r.cycleTimeMin, tokens: r.billedTokens ?? r.tokensActual,
    costUsd: r.costUsd,
    interventions: iv ? { answers: iv.answers, corrections: iv.corrections, rescues: iv.rescues, total: iv.total } : null,
    audit: r.audit.map((a) => ({ dim: a.dim, verdict: a.verdict })), ownerSessionId: r.ownerSessionId,
    pipelineStage: r.stage ?? null, stages: r.stages ?? null, timeline: r.stageTimeline ?? null,
    parentRun: null, wp: null, knowledge: r.knowledge ?? null, local: r,
  };
}

/** A Hive run that ran a pipeline (null for a native one: it stays in Swarm Out). */
export function fromHive(r: SwarmOutRun, asks: SwarmOutAsk[] = []): PipelineRow | null {
  const res = r.result;
  const runId = res?.pipeline?.run_id ?? null;
  if (!runId) return null;
  const s = stageOf(r);
  const live = !DONE_STAGES.includes(s);
  // Swarm Out's rule (runReview.sections): when the dispatching session answers for the
  // run (needs_person is a boolean) it waits for you only when the backend says so; the
  // agent taking it through the funnel is working on it, not waiting on you. Otherwise a
  // run waits for you only when it asks something: a stopped or awaiting run that only
  // opens its session is done.
  const answered = typeof r.needs_person === 'boolean' && live;
  const asksYou = answered ? r.needs_person === true
    : s === 'needs_answer' || actionsOf(r).some((a) => a.key !== 'open_session');
  const stage: RowStage = answered
    ? (r.needs_person ? (s === 'running' ? 'needs_answer' : s as RowStage) : 'running')
    : s === 'needs_answer' || s === 'awaiting' || s === 'stopped' || s === 'running' ? s : 'done';
  const task: RowTask | null = !asksYou ? null
    : stage === 'needs_answer' ? 'answer' : stage === 'awaiting' ? 'accept' : stage === 'stopped' ? 'stopped' : null;
  const settled = r.settle?.settled?.outcome;
  const outcome: Outcome = s === 'running' ? 'running'
    : settled === 'passed' ? 'landed' : settled === 'abandoned' ? 'discarded' : settled ? 'other'
      : s === 'stopped' ? 'failed' : 'other';
  const ended = res?.ended_at ?? null;
  const start = ms(r.sent_at), end = ms(ended);
  const human = res?.work.human;
  return {
    key: r.dispatch_id, runId, where: 'hive', hive: r.hive, title: r.title, brain: brainLabel(r),
    agentMode: r.project_agent?.mode ?? res?.project_agent?.mode ?? null, profile: res?.pipeline?.profile ?? r.project_agent?.profile ?? '', stage, task, outcome,
    createdAt: r.sent_at, endedAt: s === 'running' ? null : ended, since: hiveWaitingSince(r, asks) ?? r.sent_at,
    durationMin: Number.isNaN(start) || Number.isNaN(end) || s === 'running' ? null : Math.round((end - start) / 60000),
    tokens: res ? tokenTotal(res) : null, costUsd: res?.cost_usd ?? null,
    interventions: res && human ? {
      answers: human.answered_questions, corrections: human.said ?? null, rescues: 0,
      // the person's decisions on the run's record, the rule every reader of "stepped in"
      // uses (runReview.autonomyWord); the counts from the session only for older records
      total: r.person_decisions ?? interventionsOf(res),
    } : null,
    audit: (res?.pipeline?.audit ?? []).map((a) => ({ dim: a.dim, verdict: a.verdict ?? '' })),
    ownerSessionId: r.session_id,
    // the stage it is in while it runs, as this host last read it (never guessed)
    pipelineStage: live ? r.live_stage?.stage ?? null : null,
    stages: live && r.live_stage ? r.live_stage.stages.map((name) => ({ name, status: null, attempts: null })) : null,
    timeline: null, parentRun: r.parent_run ?? null, wp: r.wp ?? null, knowledge: null, hive_run: r,
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

export interface Lane {
  key: string;
  runId: string | null;
  where: Where;
  /** local: "local"; Hive: the Hive's name */
  place: string;
  title: string;
  /** waits on the person (it asks something), else going */
  waiting: boolean;
  /** stage progress of a local run ("5/8" from the engine); null for a Hive run, whose
   *  stage the Hive does not report until it ends: no dots are drawn for it */
  progress: { done: number; total: number } | null;
  row: PipelineRow;
}

export interface BrainLanes { lanes: Lane[]; more: number; total: number }

function progressOf(r: PipelineRow): Lane['progress'] {
  const m = /^(\d+)\s*\/\s*(\d+)$/.exec(r.local?.progress ?? '');
  if (!m) return null;
  const done = Number(m[1]), total = Number(m[2]);
  return total > 0 && done >= 0 && done <= total ? { done, total } : null;
}

/** The runs of *brain* going now (waiting on you first, then the newest running), at
 *  most *max* lanes; ``more`` counts the rest, ``total`` all of them. A done run is not
 *  a lane: the loop shows what is under way. */
export function brainLanes(rows: PipelineRow[], brain: string, max = 3): BrainLanes {
  const s = sections(rows.filter((r) => r.brain === brain));
  const live = [...s.you, ...s.running];
  const lanes = live.slice(0, max).map((r): Lane => ({
    key: r.key, runId: r.runId, where: r.where, place: r.where === 'hive' ? (r.hive || 'Hive') : 'local',
    title: r.title, waiting: s.you.includes(r), progress: progressOf(r), row: r,
  }));
  return { lanes, more: Math.max(0, live.length - lanes.length), total: live.length };
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

// ── the Line (design: the factory-line overlays, Knowledge/Designs/2026-11-18-
//    swarmai-factory-line-overlays-mockup.html) ────────────────────────────────

/** The pipeline stages, the same for every Brain, then where a Hive run comes back. */
export const PIPELINE_STATIONS = ['evaluate', 'think', 'plan', 'build', 'review', 'test', 'deliver', 'reflect'] as const;
export const RETURN_STATIONS = ['return', 'land', 'judge'] as const;
export const STATIONS = [...PIPELINE_STATIONS, ...RETURN_STATIONS] as const;
export type Station = typeof STATIONS[number];
/** the machine gates: they decide from checks, never a person */
export const GATES: Partial<Record<Station, string>> = { evaluate: 'Gate-0', review: 'Gate-2' };

export interface Track {
  row: PipelineRow;
  /** the station the chip sits at; null: a Hive run on its Hive, its stage not reported
   *  (the chip spans the pipeline stations as one band) */
  at: Station | null;
  kind: ChipKind;
  /** the stations its track runs along (its profile's stages; a Hive run: the band and the
   *  return stations it reached) */
  path: Station[];
  /** extra attempts at a stage: ↺N with N its attempts */
  rounds: Partial<Record<Station, number>>;
}

const isStation = (x: string | null | undefined): x is Station => !!x && (STATIONS as readonly string[]).includes(x);

/** One run in flight as a track: where its chip sits, its colour, the stations it runs along. */
export function trackOf(r: PipelineRow): Track {
  if (r.where === 'hive' && r.hive_run) {
    const t = dispatchTrack(r.hive_run);
    // on its Hive with a stage read from there: it sits at that stage, along its planned
    // stages (none drawn when they were not read)
    if (t.at === 'on_hive' && isStation(r.pipelineStage) && (PIPELINE_STATIONS as readonly string[]).includes(r.pipelineStage)) {
      return { row: r, at: r.pipelineStage, kind: t.kind, path: (r.stages ?? []).map((x) => x.name).filter(isStation), rounds: {} };
    }
    const back = (RETURN_STATIONS as readonly string[]).includes(t.at) ? t.at as Station : null;
    const path: Station[] = back ? RETURN_STATIONS.slice(0, RETURN_STATIONS.indexOf(back as typeof RETURN_STATIONS[number]) + 1) : [];
    return { row: r, at: back, kind: t.kind, path, rounds: {} };
  }
  const kind: ChipKind = r.stage === 'running' ? 'run' : r.stage === 'stopped' ? 'stop' : r.stage === 'done' ? 'done' : 'you';
  const path = (r.stages ?? []).map((s) => s.name).filter(isStation);
  const rounds: Partial<Record<Station, number>> = {};
  for (const s of r.stages ?? []) if (isStation(s.name) && (s.attempts ?? 0) > 1) rounds[s.name] = s.attempts as number;
  return { row: r, at: isStation(r.pipelineStage) ? r.pipelineStage : null, kind, path, rounds };
}

export interface LineItem { track: Track; children: Track[] }
export interface LineGroup { brain: string; items: LineItem[]; running: number; waiting: number; count: number }

/** The runs in flight (waiting on you, running, stopped and asking) by Brain: a Hive run
 *  that is a work package of a local run sits under it. A Brain with more runs first. */
export function lineGroups(rows: PipelineRow[]): LineGroup[] {
  const s = sections(rows);
  const live = [...s.you, ...s.running];
  const leads = new Map(live.filter((r) => r.where === 'local' && r.runId).map((r) => [r.runId as string, r]));
  const items = new Map<string, LineItem>();
  const order: LineItem[] = [];
  for (const r of live) {
    if (r.parentRun && leads.has(r.parentRun)) continue;
    const item = { track: trackOf(r), children: [] as Track[] };
    items.set(r.key, item); order.push(item);
  }
  for (const r of live) {
    if (!r.parentRun || !leads.has(r.parentRun)) continue;
    items.get(leads.get(r.parentRun)!.key)?.children.push(trackOf(r));
  }
  const by = new Map<string, LineItem[]>();
  for (const it of order) by.set(it.track.row.brain, [...(by.get(it.track.row.brain) ?? []), it]);
  return [...by.entries()].map(([brain, list]) => {
    const all = list.flatMap((i) => [i.track, ...i.children]);
    return { brain, items: list, count: all.length,
      running: all.filter((t) => t.kind === 'run' || t.kind === 'judge').length,
      waiting: all.filter((t) => t.kind === 'you').length };
  }).sort((a, b) => (b.count - a.count) || a.brain.localeCompare(b.brain));
}

/** Each station's header: the runs at it and how many of them wait on the person. */
export function stationCounts(groups: LineGroup[]): Record<Station, { n: number; you: number }> {
  const out = Object.fromEntries(STATIONS.map((s) => [s, { n: 0, you: 0 }])) as Record<Station, { n: number; you: number }>;
  for (const t of groups.flatMap((g) => g.items.flatMap((i) => [i.track, ...i.children]))) {
    if (!t.at) continue;
    out[t.at].n += 1;
    if (t.kind === 'you') out[t.at].you += 1;
  }
  return out;
}

export interface Verdict {
  onLine: number; running: number; waiting: number; stopped: number;
  oldest: { title: string; station: Station | null; since: string } | null;
}

/** The one sentence above the Line, from the same tracks. */
export function verdictOf(groups: LineGroup[]): Verdict {
  const all = groups.flatMap((g) => g.items.flatMap((i) => [i.track, ...i.children]));
  const you = all.filter((t) => t.kind === 'you').sort((a, b) => ms(a.row.since) - ms(b.row.since));
  return {
    onLine: all.length, running: all.filter((t) => t.kind === 'run' || t.kind === 'judge').length,
    waiting: you.length, stopped: all.filter((t) => t.kind === 'stop').length,
    oldest: you[0] ? { title: you[0].row.title, station: you[0].at, since: you[0].row.since } : null,
  };
}

const median = (xs: number[]): number | null => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

export interface StationTime { agentMin: number | null; waitMin: number | null; rounds: number; runs: number }

/** Where time goes, per pipeline station, over the rows that recorded a timeline: the
 *  median minutes working and waiting on a person per run that entered it, the extra
 *  rounds there, and how many runs it covers. ``covered`` 0: nothing is recorded yet. */
export function timeGoes(rows: PipelineRow[]): { stations: Record<string, StationTime>; covered: number } {
  const timed = rows.filter((r) => r.timeline && Object.keys(r.timeline).length > 0);
  const stations: Record<string, StationTime> = {};
  for (const st of PIPELINE_STATIONS) {
    const entries = timed.map((r) => r.timeline![st]).filter((e): e is PipelineStageTime => !!e);
    const agent = entries.map((e) => e.activeS).filter((x): x is number => x != null).map((x) => x / 60);
    const wait = entries.map((e) => e.waitS).filter((x): x is number => x != null).map((x) => x / 60);
    // extra rounds (attempts beyond the first), over the same timed runs ``covered`` counts
    const rounds = timed.reduce((n, r) => n + Math.max(0, (r.stages?.find((x) => x.name === st)?.attempts ?? 1) - 1), 0);
    stations[st] = { agentMin: median(agent), waitMin: median(wait), rounds, runs: entries.length };
  }
  return { stations, covered: timed.length };
}

// ── Eval, in the CNTech & IVT S7 order ────────────────────────────────────────

/** A 95% Wilson interval for k of n; null when n is 0. */
export function wilson(k: number, n: number): { lo: number; hi: number } | null {
  if (n <= 0) return null;
  const z = 1.96, p = k / n, d = 1 + (z * z) / n;
  const c = p + (z * z) / (2 * n), w = z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return { lo: Math.max(0, (c - w) / d), hi: Math.min(1, (c + w) / d) };
}

export interface EvalPeriod {
  /** ① autonomous: k of n finished runs with a known count; rate null when n is 0 */
  autonomous: { k: number; n: number; rate: number | null; range: { lo: number; hi: number } | null };
  /** ② per run with a known count: answers, and unplanned (corrections + rescues) */
  human: { answers: number | null; unplanned: number | null; n: number };
  /** ③ per run that records it: entries given (used) and cognition units written back
   *  (new); each average null when no run records it, with how many runs it covers */
  knowledge: { used: number | null; new: number | null; n: number; newRuns: number };
  /** ④ per run where recorded, and how many runs that covers */
  cost: { usd: number | null; usdRuns: number; tokens: number | null; tokenRuns: number; runs: number };
  /** ⑥ runs started in the period */
  started: number;
  /** ⑤ minutes, finished runs */
  cycle: { median: number | null; p90: number | null; n: number };
  /** where runs waited on a person: per station, how many runs waited there */
  waitedAt: { station: Station; runs: number }[];
}

function periodOf(rows: PipelineRow[]): EvalPeriod {
  const a = autonomy(rows);
  const known = rows.filter((r) => r.stage !== 'running' && r.interventions?.total != null);
  const withCorr = known.filter((r) => r.interventions!.corrections != null);
  const avg = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);
  const usd = rows.filter((r) => r.costUsd != null), tok = rows.filter((r) => r.tokens != null);
  const sp = speed(rows);
  const waitedAt = PIPELINE_STATIONS.map((st) => ({
    station: st as Station,
    runs: rows.filter((r) => (r.timeline?.[st]?.waitS ?? 0) > 0).length,
  })).filter((x) => x.runs > 0).sort((x, y) => y.runs - x.runs);
  return {
    autonomous: { k: a.unattended, n: a.known, rate: a.known ? a.unattended / a.known : null, range: wilson(a.unattended, a.known) },
    human: { answers: avg(known.map((r) => r.interventions!.answers)),
      unplanned: avg(withCorr.map((r) => (r.interventions!.corrections as number) + r.interventions!.rescues)), n: known.length },
    knowledge: (() => {
      const k = rows.filter((r) => r.knowledge?.used != null), w = rows.filter((r) => r.knowledge?.new != null);
      return { used: avg(k.map((r) => r.knowledge!.used as number)), new: avg(w.map((r) => r.knowledge!.new as number)), n: k.length, newRuns: w.length };
    })(),
    cost: { usd: avg(usd.map((r) => r.costUsd as number)), usdRuns: usd.length,
      tokens: avg(tok.map((r) => r.tokens as number)), tokenRuns: tok.length, runs: rows.length },
    started: rows.length,
    cycle: { median: sp.median, p90: sp.p90, n: sp.n },
    waitedAt,
  };
}

export const EVAL_HALF_DAYS = 15;

/** The S7 measures for the last 15 days and the 15 before (the 30-day window's halves),
 *  each over the runs started in it. */
export function evalMeasures(rows: PipelineRow[], now: number): { cur: EvalPeriod; prev: EvalPeriod } {
  const cut = now - EVAL_HALF_DAYS * DAY_MS, start = now - 2 * EVAL_HALF_DAYS * DAY_MS;
  const t = (r: PipelineRow) => ms(r.createdAt);
  return {
    cur: periodOf(rows.filter((r) => t(r) >= cut)),
    prev: periodOf(rows.filter((r) => t(r) >= start && t(r) < cut)),
  };
}

/** The same measures per Brain over the whole window, the Brains with more runs first. */
export function evalByBrain(rows: PipelineRow[]): { brain: string; m: EvalPeriod }[] {
  return brainChips(rows).map((c) => ({ brain: c.brain, m: periodOf(rows.filter((r) => r.brain === c.brain)) }));
}
