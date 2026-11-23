/**
 * The Swarm Out history's cost section (mock Knowledge/Mockups/2026-11-23-swarm-out-
 * dashboard-redesign-v7.html), as pure functions over /runs and its ``machines``.
 *
 * Two layers over the runs of the history's time range:
 *  - ``model``: each run's own model cost (``result.cost_usd``) and tokens;
 *  - ``all``: model plus machine. An AgentCore run's machine cost is its own estimate
 *    (``run.machine``, backend core/machine_cost). An EC2 Hive bills by the hour while it
 *    is on, busy or idle: its on hours inside the range and inside its record
 *    (``machines.ec2[].on`` since ``recorded_since``) are priced at its hourly price and
 *    shared among its runs of the range by active time; what lies above their active
 *    hours is idle. A run whose machine cost is unknown (an EC2 run its Hive's record did
 *    not see on, e.g. before the record began, or on a Hive with no price; an AgentCore
 *    run with no estimate) is left out of this layer and counted in ``leftOut``.
 *
 * Runs are grouped by kind of work (the Project Agent's mode, plus " · pipeline" when it
 * ran as one; "other" without a Project Agent) and carrier. The verdict compares only
 * kinds where both carriers have at least MIN_ACCEPTED accepted runs: each side's cost per
 * accepted run, weighted by the kind's EC2 accepted runs. Dividing by accepted runs puts
 * what discarded and failed runs burned into the cost.
 */
import type { SwarmOutMachines, SwarmOutRun } from '../../services/swarmOut';
import { outcomeOf } from './historyStats';
import { runWhere, type Carrier } from './where';

export type CostLayer = 'all' | 'model';
export const MIN_ACCEPTED = 5;
export const CARRIERS: readonly Carrier[] = ['ec2', 'agentcore'];

export interface Tokens { input: number; output: number; cache_read: number; cache_write: number }

export interface Side {
  n: number;
  accepted: number;
  model: number;
  /** machine cost while its runs were active, and the EC2 idle share (0 for AgentCore) */
  busy: number;
  idle: number;
  tokens: Tokens;
}

export interface KindRow { kind: string; sides: Partial<Record<Carrier, Side>> }

export interface CostView {
  kinds: KindRow[];
  model: number;
  busy: number;
  /** every EC2 idle dollar of the range, including the hours of Hives with no run in it */
  idle: number;
  /** EC2: on hours in the range and the record, and the hours its runs were active */
  onHours: number;
  activeHours: number;
  tokens: Tokens;
  /** runs left out of the ``all`` layer: their machine cost is unknown */
  leftOut: number;
  /** the earliest EC2 record start, null when there is none */
  recordedSince: string | null;
  /** the runs answer carries machine records (false: an older backend) */
  hasMachines: boolean;
}

export function kindOf(r: SwarmOutRun): string {
  const a = r.project_agent;
  if (!a?.mode) return 'other';
  return a.execution === 'pipeline' ? `${a.mode} · pipeline` : a.mode;
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const zero = (): Tokens => ({ input: 0, output: 0, cache_read: 0, cache_write: 0 });
const addTokens = (t: Tokens, r: SwarmOutRun) => {
  const k = r.result?.tokens;
  if (!k) return;
  t.input += num(k.input); t.output += num(k.output); t.cache_read += num(k.cache_read); t.cache_write += num(k.cache_write);
};

/** Hours of the periods inside [from, to]. */
function hoursIn(periods: [string, string][], from: number, to: number): number {
  let ms = 0;
  for (const [a, b] of periods) {
    const s = Math.max(Date.parse(a), from);
    const e = Math.min(Date.parse(b), to);
    if (!Number.isNaN(s) && !Number.isNaN(e) && e > s) ms += e - s;
  }
  return ms / 3_600_000;
}

/** The cost section's numbers for the runs of one range [from, to]. */
export function costStats(runs: SwarmOutRun[], machines: SwarmOutMachines | null | undefined,
  from: number, to: number, layer: CostLayer): CostView {
  const ec2 = machines?.ec2 ?? [];
  const byHive = new Map(ec2.map((h) => [h.hive_id, h]));
  const machine = new Map<SwarmOutRun, { busy: number; idle: number }>();
  let leftOut = 0;
  let idleAll = 0;
  let onHours = 0;
  let activeHours = 0;
  // EC2: each Hive's hours of the range, shared among its runs by active time
  const onHive = new Map<string, SwarmOutRun[]>();
  for (const r of runs) {
    const carrier = runWhere(r, null).carrier;
    if (carrier === 'agentcore') {
      if (r.machine && typeof r.machine.usd === 'number') machine.set(r, { busy: r.machine.usd, idle: 0 });
      else leftOut += 1;
      continue;
    }
    const h = byHive.get(r.hive_id);
    const sent = Date.parse(r.sent_at);
    // known only when the record saw its Hive on while it ran: a run on a Hive whose hours
    // were not seen (before the record, or EC2 not read) is unknown, never $0
    const ran = Math.max(num(r.result?.active_s) * 1000, 60_000);
    if (!h || h.hourly_usd == null || Number.isNaN(sent) || hoursIn(h.on, sent, sent + ran) <= 0) { leftOut += 1; continue; }
    onHive.set(h.hive_id, [...(onHive.get(h.hive_id) ?? []), r]);
  }
  for (const h of ec2) {
    if (h.hourly_usd == null) continue;
    // its periods begin no earlier than its record (backend core/machine_cost.machines)
    const on = hoursIn(h.on, from, to);
    const cost = on * h.hourly_usd;
    const mine = onHive.get(h.hive_id) ?? [];
    const active = mine.reduce((s, r) => s + num(r.result?.active_s), 0) / 3600;
    const used = Math.min(active, on);
    onHours += on;
    activeHours += used;
    const scale = active > 0 ? used / active : 0;
    let busy = 0;
    for (const r of mine) {
      const b = (num(r.result?.active_s) / 3600) * scale * h.hourly_usd;
      busy += b;
      machine.set(r, { busy: b, idle: 0 });
    }
    const idle = Math.max(0, cost - busy);
    idleAll += idle;
    for (const r of mine) {
      const share = active > 0 ? num(r.result?.active_s) / 3600 / active : 1 / mine.length;
      machine.get(r)!.idle = idle * share;
    }
  }
  const kinds = new Map<string, KindRow>();
  const tokens = zero();
  let model = 0;
  let busyAll = 0;
  for (const r of runs) {
    if (layer === 'all' && !machine.has(r)) continue;
    const kind = kindOf(r);
    const carrier = runWhere(r, null).carrier;
    const row = kinds.get(kind) ?? { kind, sides: {} };
    kinds.set(kind, row);
    const side = row.sides[carrier] ?? { n: 0, accepted: 0, model: 0, busy: 0, idle: 0, tokens: zero() };
    row.sides[carrier] = side;
    side.n += 1;
    if (outcomeOf(r) === 'ok') side.accepted += 1;
    side.model += num(r.result?.cost_usd);
    model += num(r.result?.cost_usd);
    addTokens(side.tokens, r);
    addTokens(tokens, r);
    const m = machine.get(r);
    if (layer === 'all' && m) { side.busy += m.busy; side.idle += m.idle; busyAll += m.busy; }
  }
  const total = (k: KindRow) => CARRIERS.reduce((s, c) => s + (k.sides[c] ? k.sides[c]!.model + k.sides[c]!.busy + k.sides[c]!.idle : 0), 0);
  const starts = ec2.map((h) => Date.parse(h.recorded_since)).filter((t) => !Number.isNaN(t));
  return {
    kinds: [...kinds.values()].sort((a, b) => total(b) - total(a)),
    model, busy: busyAll, idle: layer === 'all' ? idleAll : 0, onHours, activeHours, tokens,
    leftOut: layer === 'all' ? leftOut : 0,
    recordedSince: starts.length ? new Date(Math.min(...starts)).toISOString() : null,
    hasMachines: machines != null,
  };
}

/** A side's dollars per accepted run, split into model, machine in use and idle (both 0
 *  in the "model only" layer, where costStats adds no machine cost); null with no
 *  accepted run. */
export function perAccepted(s: Side | undefined): { model: number; busy: number; idle: number; total: number } | null {
  if (!s || s.accepted === 0) return null;
  const busy = s.busy / s.accepted;
  const idle = s.idle / s.accepted;
  const model = s.model / s.accepted;
  return { model, busy, idle, total: model + busy + idle };
}

export const comparable = (k: KindRow): boolean =>
  CARRIERS.every((c) => (k.sides[c]?.accepted ?? 0) >= MIN_ACCEPTED);

/** The verdict: how much cheaper per accepted run the cheaper carrier is, over the kinds
 *  both can be compared on (EC2-weighted); null when no kind can be. ``pct`` > 0: AgentCore
 *  is cheaper. */
export function verdict(view: CostView): { pct: number; kinds: number; accepted: number } | null {
  let num_ = 0;
  let den = 0;
  let kinds = 0;
  let accepted = 0;
  for (const k of view.kinds) {
    if (!comparable(k)) continue;
    const e = perAccepted(k.sides.ec2)!.total;
    const a = perAccepted(k.sides.agentcore)!.total;
    const w = k.sides.ec2!.accepted;
    num_ += w * (e - a);
    den += w * e;
    kinds += 1;
    accepted += k.sides.ec2!.accepted + k.sides.agentcore!.accepted;
  }
  return kinds && den > 0 ? { pct: (num_ / den) * 100, kinds, accepted } : null;
}
