/**
 * The Swarm Out history's numbers (design: Knowledge/Mockups/2026-11-23-swarm-out-
 * dashboard-redesign-v6.html, the History view), as pure functions over /runs.
 *
 * One time range drives every number on the page (RANGES; 7 days by default): the
 * verdict, the split bar, the bars, the groups and the records. The range is whole local
 * calendar days, today included (live.inWindow); "all" starts at the first run's day.
 *
 * Each run is one of four outcomes, from the stage the backend gives it (runReview.stageOf):
 *  - ``ok``: accepted (its result was taken);
 *  - ``off``: closed (discarded, reverted, expired, its Hive gone);
 *  - ``warn``: stopped and nobody took it on;
 *  - ``open``: still running, or waiting on an answer or an acceptance.
 * The verdict counts the runs accepted with no one stepping in (``unattended``): a
 * discarded run is never "done", whatever its Hive said about how it ended. How often a
 * person stepped in is the run's decision record (``person_decisions``) when there is one,
 * else what its result counted (live.interventionsOf); a run that cannot tell is not
 * unattended.
 */
import type { SwarmOutFleet, SwarmOutRun } from '../../services/swarmOut';
import { agentFacts, interventionsOf, inWindow } from './live';
import { stageOf } from './runReview';
import { runWhere } from './where';

export const RANGES = [7, 30, 60, 90, null] as const;
export type Range = typeof RANGES[number];
export const DEFAULT_RANGE: Range = 7;

export type Outcome = 'ok' | 'off' | 'warn' | 'open';

export function outcomeOf(r: SwarmOutRun): Outcome {
  const s = stageOf(r);
  if (s === 'accepted') return 'ok';
  if (s === 'closed') return 'off';
  if (s === 'stopped') return 'warn';
  return 'open';
}

/** How often the person stepped in (see the header); null when the run cannot tell. */
export function personSteps(r: SwarmOutRun): number | null {
  if (typeof r.person_decisions === 'number') return r.person_decisions;
  return r.result ? interventionsOf(r.result) : null;
}

export const unattended = (r: SwarmOutRun): boolean => outcomeOf(r) === 'ok' && personSteps(r) === 0;

export interface Summary {
  n: number;
  by: Record<Outcome, number>;
  unattended: number;
  /** the median active minutes of the runs that ended, null when none says */
  medianMin: number | null;
  cost: number;
  /** runs whose cost is unknown or only partly known (the sum is then "at least") */
  costMissing: number;
}

export function summarize(runs: SwarmOutRun[]): Summary {
  const by: Record<Outcome, number> = { ok: 0, off: 0, warn: 0, open: 0 };
  const active: number[] = [];
  let un = 0, cost = 0, costMissing = 0;
  for (const r of runs) {
    const o = outcomeOf(r);
    by[o] += 1;
    if (unattended(r)) un += 1;
    const res = r.result;
    if (o !== 'open' && res?.active_s != null) active.push(res.active_s);
    if (!res || res.cost_usd == null) costMissing += 1;
    else { cost += res.cost_usd; if (res.cost_partial) costMissing += 1; }
  }
  active.sort((a, b) => a - b);
  const m = Math.floor(active.length / 2);
  const med = active.length === 0 ? null : active.length % 2 ? active[m] : (active[m - 1] + active[m]) / 2;
  return { n: runs.length, by, unattended: un, medianMin: med == null ? null : Math.round(med / 60), cost, costMissing };
}

const dayStart = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
const pad = (n: number) => String(n).padStart(2, '0');
const md = (t: number) => { const d = new Date(t); return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}`; };

/** The first day of the range (local midnight): ``days - 1`` days before today, or the
 *  first run's day for "all" (today when there is none). */
export function rangeStart(runs: SwarmOutRun[], now: number, days: Range): number {
  if (days != null) {
    const d = new Date(dayStart(now));
    d.setDate(d.getDate() - (days - 1));
    return d.getTime();
  }
  const ts = runs.map((r) => Date.parse(r.sent_at)).filter((t) => !Number.isNaN(t));
  return dayStart(ts.length ? Math.min(...ts) : now);
}

/** The runs of the range. "All" keeps only runs with a readable start no later than now,
 *  the same runs the bars and the dated ranges count. */
export const inRange = (runs: SwarmOutRun[], now: number, days: Range): SwarmOutRun[] => (days == null
  ? runs.filter((r) => { const t = Date.parse(r.sent_at); return !Number.isNaN(t) && t <= now; })
  : inWindow(runs, now, days));

/** The runs of the same length of time just before the range; empty for "all". */
export function previousRange(runs: SwarmOutRun[], now: number, days: Range): SwarmOutRun[] {
  if (days == null) return [];
  const start = rangeStart(runs, now, days);
  const d = new Date(start);
  d.setDate(d.getDate() - days);
  const from = d.getTime();
  return runs.filter((r) => { const t = Date.parse(r.sent_at); return !Number.isNaN(t) && t >= from && t < start; });
}

export interface Bar { label: string; by: Record<Outcome, number> }

/** The range's runs per day, or per week (seven days, the last one ending today) once
 *  the range is longer than a month; oldest first. */
export function bars(runs: SwarmOutRun[], now: number, days: Range): { bars: Bar[]; weekly: boolean; from: string; to: string } {
  const start = rangeStart(runs, now, days);
  const today = dayStart(now);
  const dayList: number[] = [];
  for (let d = new Date(start); d.getTime() <= today; d.setDate(d.getDate() + 1)) dayList.push(d.getTime());
  const empty = (): Record<Outcome, number> => ({ ok: 0, off: 0, warn: 0, open: 0 });
  const perDay = new Map(dayList.map((t) => [t, empty()]));
  for (const r of runs) {
    const t = Date.parse(r.sent_at);
    if (Number.isNaN(t) || t > now) continue;
    const b = perDay.get(dayStart(t));
    if (b) b[outcomeOf(r)] += 1;
  }
  const from = md(start), to = md(today);
  if (dayList.length <= 31) return { bars: dayList.map((t) => ({ label: md(t), by: perDay.get(t)! })), weekly: false, from, to };
  const out: Bar[] = [];
  for (let end = dayList.length; end > 0; end -= 7) {
    const chunk = dayList.slice(Math.max(0, end - 7), end);
    const by = empty();
    for (const t of chunk) for (const k of Object.keys(by) as Outcome[]) by[k] += perDay.get(t)![k];
    out.unshift({ label: `${md(chunk[0])}–${md(chunk[chunk.length - 1])}`, by });
  }
  return { bars: out, weekly: true, from, to };
}

export type GroupDim = 'brain' | 'agent' | 'where';

/** A run's group along one dimension: its Brain, its Project Agent, or where it ran. */
export function groupKey(r: SwarmOutRun, dim: GroupDim, fleet: SwarmOutFleet | null | undefined): string {
  const f = agentFacts(r);
  if (dim === 'brain') return f.brain;
  if (dim === 'agent') return f.none ? f.agent : `${f.brain} · ${f.agent}`;
  const w = runWhere(r, fleet);
  return `${w.carrier === 'agentcore' ? 'AgentCore' : w.carrier === 'ssh' ? 'SSH' : 'EC2'} · ${w.name}`;
}

export interface Group { key: string; sum: Summary }

/** The runs split along one dimension, the biggest group first. */
export function groups(runs: SwarmOutRun[], dim: GroupDim, fleet: SwarmOutFleet | null | undefined): Group[] {
  const by = new Map<string, SwarmOutRun[]>();
  for (const r of runs) {
    const k = groupKey(r, dim, fleet);
    by.set(k, [...(by.get(k) ?? []), r]);
  }
  return [...by].map(([key, rs]) => ({ key, sum: summarize(rs) }))
    .sort((a, b) => b.sum.n - a.sum.n || a.key.localeCompare(b.key));
}

export type RecordFilter = 'all' | 'human' | 'off' | 'warn';
export const RECORD_FILTERS: readonly RecordFilter[] = ['all', 'human', 'off', 'warn'];

export function matches(r: SwarmOutRun, f: RecordFilter): boolean {
  if (f === 'all') return true;
  if (f === 'human') return (personSteps(r) ?? 0) > 0;
  return outcomeOf(r) === f;
}
