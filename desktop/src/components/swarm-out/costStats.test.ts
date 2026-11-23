/**
 * The history's cost numbers (./costStats; mock v7, the cost section).
 *
 * Questions: (1) is an EC2 Hive's cost its on hours inside the range and its record at its
 * hourly price, shared among its runs by active time, the rest idle; (2) is a run whose
 * machine cost is unknown left out of the "with machine" layer and counted, and kept in
 * "model only"; (3) does the verdict compare only kinds with MIN_ACCEPTED accepted runs on
 * both sides, weighted by EC2 accepted runs; (4) is a pipeline run its own kind of work.
 */
import { describe, it, expect } from 'vitest';
import type { SwarmOutMachines, SwarmOutRun } from '../../services/swarmOut';
import { costStats, kindOf, perAccepted, verdict, MIN_ACCEPTED } from './costStats';

const T = Date.parse('2026-11-23T12:00:00Z');
const h = (n: number) => new Date(T - n * 3600_000).toISOString();
const AC = { vcpu: 2, memory_gib: 8, idle_timeout_s: 900, usd_per_vcpu_hour: 0.0895, usd_per_gib_hour: 0.00945 };

let seq = 0;
function run(o: {
  hive?: string; ac?: boolean; sent: number; active_h?: number; cost?: number; accepted?: boolean;
  mode?: string; execution?: string; machine?: { seconds: number; usd: number } | null;
}): SwarmOutRun {
  seq += 1;
  return {
    dispatch_id: `so_${String(seq).padStart(12, '0')}`, hive_id: o.ac ? 'el' : (o.hive ?? 'h1'), hive: o.ac ? 'elastic' : (o.hive ?? 'h1'),
    session_id: `s${seq}`, title: `t${seq}`, origin: 'swarm-out', sent_at: h(o.sent), state: 'stopped', state_reason: 'idle',
    stage: o.accepted === false ? 'closed' : 'accepted',
    rounds: [{ carrier: o.ac ? 'agentcore' : 'ec2' }],
    project_agent: { brain: 'SwarmAI', mode: o.mode ?? 'code-change', revision: 'r', profile: 'p', acceptance: 'a', execution: o.execution ?? 'native' },
    ...(o.ac ? { machine: o.machine === undefined ? { seconds: 600, usd: 0.5 } : o.machine } : {}),
    result: { active_s: (o.active_h ?? 1) * 3600, cost_usd: o.cost ?? 1, tokens: { input: 10, output: 100, cache_read: 1000, cache_write: 50 } } as SwarmOutRun['result'],
  } as SwarmOutRun;
}

const MACHINES: SwarmOutMachines = {
  ec2: [
    { hive_id: 'h1', name: 'demo', instance_type: 'm7g.xlarge', hourly_usd: 1, recorded_since: h(10), on: [[h(10), h(6)], [h(4), h(0)]] },
    { hive_id: 'h2', name: 'odd', instance_type: 'x9.weird', hourly_usd: null, recorded_since: h(10), on: [[h(10), h(0)]] },
  ],
  agentcore: AC, estimate: true,
};

describe('costStats', () => {
  it('(1) an EC2 Hive: on hours in the range and the record, shared by active time, the rest idle', () => {
    const a = run({ sent: 3, active_h: 1 });
    const b = run({ sent: 2, active_h: 2 });
    const v = costStats([a, b], MACHINES, T - 8 * 3600_000, T, 'all');
    // the range starts at -8h: 2 h of the first period + 4 h of the second = 6 h at $1
    expect(v.onHours).toBeCloseTo(6);
    expect(v.activeHours).toBeCloseTo(3);
    expect(v.busy).toBeCloseTo(3);
    expect(v.idle).toBeCloseTo(3);
    const s = v.kinds[0].sides.ec2!;
    expect(s.busy).toBeCloseTo(3);
    expect(s.idle).toBeCloseTo(3);
    expect(perAccepted(s)!.total).toBeCloseTo((2 + 6) / 2);
  });

  it('(1) active time above the on hours is capped at them: no idle, busy = the hours', () => {
    const a = run({ sent: 1, active_h: 9 });
    const v = costStats([a], MACHINES, T - 8 * 3600_000, T, 'all');
    expect(v.busy).toBeCloseTo(6);
    expect(v.idle).toBeCloseTo(0);
  });

  it('(1) a Hive with no run in the range still has idle hours', () => {
    const v = costStats([run({ ac: true, sent: 1 })], MACHINES, T - 8 * 3600_000, T, 'all');
    expect(v.idle).toBeCloseTo(6);
  });

  it('(2) unknown machine cost is left out of "with machine" and counted, kept in "model only"', () => {
    const runs = [
      run({ sent: 1 }),                       // known
      run({ sent: 11 }),                      // before h1's record began
      run({ sent: 5 }),                       // h1 off then (between its two periods)
      run({ hive: 'h2', sent: 1 }),           // a Hive with no price
      run({ hive: 'h9', sent: 1 }),           // a Hive with no record
      run({ ac: true, sent: 1, machine: null }), // an AgentCore run with no estimate
      run({ ac: true, sent: 1 }),             // known: $0.5
    ];
    const all = costStats(runs, MACHINES, T - 24 * 3600_000, T, 'all');
    expect(all.leftOut).toBe(5);
    expect(all.kinds[0].sides.ec2!.n).toBe(1);
    expect(all.kinds[0].sides.agentcore!.n).toBe(1);
    expect(all.kinds[0].sides.agentcore!.busy).toBeCloseTo(0.5);
    const model = costStats(runs, MACHINES, T - 24 * 3600_000, T, 'model');
    expect(model.leftOut).toBe(0);
    expect(model.kinds[0].sides.ec2!.n).toBe(5);
    expect(model.model).toBeCloseTo(7);
    expect(model.busy + model.idle).toBe(0);
  });

  it('(2) an older backend with no machines: every run left out of "with machine"', () => {
    const v = costStats([run({ sent: 1 }), run({ ac: true, sent: 1, machine: null })], undefined, T - 3600_000 * 24, T, 'all');
    expect(v.hasMachines).toBe(false);
    expect(v.leftOut).toBe(2);
    expect(v.kinds).toEqual([]);
  });
});

describe('verdict', () => {
  const many = (n: number, o: Parameters<typeof run>[0]) => Array.from({ length: n }, () => run(o));

  it('(3) compares only kinds with enough accepted runs on both sides, EC2-weighted', () => {
    const runs = [
      ...many(5, { sent: 1, cost: 2, active_h: 0 }), ...many(5, { ac: true, sent: 1, cost: 1, machine: { seconds: 1, usd: 0 } }),
      ...many(10, { sent: 1, cost: 4, active_h: 0, mode: 'research' }),
      ...many(5, { ac: true, sent: 1, cost: 4, mode: 'research', machine: { seconds: 1, usd: 0 } }),
      ...many(9, { sent: 1, cost: 50, mode: 'write' }), ...many(MIN_ACCEPTED - 1, { ac: true, sent: 1, cost: 1, mode: 'write' }),
    ];
    const v = costStats(runs, MACHINES, T - 3600_000, T, 'model');
    // code-change saves 1 of 2 (weight 5), research 0 of 4 (weight 10); write has too few
    expect(verdict(v)!.pct).toBeCloseTo(((5 * 1) / (5 * 2 + 10 * 4)) * 100);
    expect(verdict(v)!.kinds).toBe(2);
  });

  it('(3) discarded runs cost their side but do not count as accepted', () => {
    const runs = [...many(5, { sent: 1, cost: 2 }), ...many(5, { sent: 1, cost: 2, accepted: false }),
      ...many(5, { ac: true, sent: 1, cost: 2 })];
    const v = costStats(runs, MACHINES, T - 3600_000, T, 'model');
    expect(perAccepted(v.kinds[0].sides.ec2)!.total).toBeCloseTo(4);
    expect(verdict(v)!.pct).toBeCloseTo(50);
  });

  it('(3) no kind with enough on both sides: null', () => {
    const v = costStats([...many(9, { sent: 1 }), ...many(2, { ac: true, sent: 1 })], MACHINES, T - 3600_000, T, 'model');
    expect(verdict(v)).toBeNull();
  });

  it('(4) a pipeline run is its own kind; no Project Agent is "other"', () => {
    expect(kindOf(run({ sent: 1, execution: 'pipeline' }))).toBe('code-change · pipeline');
    expect(kindOf(run({ sent: 1 }))).toBe('code-change');
    expect(kindOf({ ...run({ sent: 1 }), project_agent: undefined })).toBe('other');
  });
});
