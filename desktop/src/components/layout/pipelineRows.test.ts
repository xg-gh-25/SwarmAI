/**
 * pipelineRows — every pipeline run as one row, and what Pipelines shows about them
 * (design docs/design/2026-11-01-brain-eval-and-runs.md §4).
 *
 * Questions, one per test:
 * - does a local run become the row its state says (stage, what it asks, outcome), and a
 *   Hive run only when it ran a pipeline (a native one stays in Swarm Out)?
 * - are the sections ordered by what waits longest within to answer > to confirm >
 *   stopped, and is a row that asks nothing never "waiting for you"?
 * - does the filter keep where and Brain, and the Brain chips come from the rows?
 * - do autonomy and speed leave out what a run did not record (never a 0), and are
 *   tokens always in M?
 * - the Line: does each run's track run along its profile's stages with its chip at its
 *   current stage and ↺N where a stage reran; is a Hive run on its Hive one band (its
 *   stage not guessed) and back at RETURN / LAND / JUDGE once its funnel says so; do a
 *   Swarm Run's packages sit under their lead; do the station counts and the sentence
 *   come from the same tracks; is a run without a stage record left without timing?
 * - Eval: do the S7 measures split the window in halves, leave unrecorded measures null
 *   (never 0), and give a Wilson range from k and n?
 * - are a Brain's lanes its live runs only (waiting first, then newest), at most three
 *   with the rest counted, with stage dots only where the engine reported progress?
 */
import { describe, it, expect } from 'vitest';
import type { PipelineRunSummary } from '../../services/pipelines';
import type { SwarmOutRun } from '../../services/swarmOut';
import {
  allRows, autonomy, brainChips, brainLanes, evalByBrain, evalDays, evalHalves, evalMeasures, filterRows, steppedIn, fromHive, fromLocal, lineGroups, sections, speed,
  timeGoes, tokenM, trackOf, wilson,
} from './pipelineRows';

const NOW = Date.parse('2026-11-01T12:00:00Z');
const run = (o: Partial<PipelineRunSummary>): PipelineRunSummary => ({
  id: 'run_a', requirement: 'Fix it', status: 'completed', profile: 'full', progress: '8/8',
  cycleTimeMin: 40, tokensActual: null, createdAt: '2026-10-31T10:00:00Z', updatedAt: '2026-10-31T11:00:00Z',
  pauseKind: null, needsYou: false, landedAs: null, checkpointReason: null, ownerSessionId: 's1',
  notLanded: false, reportPath: null, auditCard: null, finishedAt: '2026-10-31T10:40:00Z', activeS: null,
  billedTokens: 50_000_000, costUsd: null, humanDecisions: 0,
  interventions: { answers: 0, corrections: 0, rescues: 0, total: 0 }, landing: 'landed',
  notLandedReason: null, rework: 0, audit: [], stage: 'reflect', stages: null, stageTimeline: null, knowledge: null, ...o,
});
const hive = (o: Record<string, unknown> = {}, res: Record<string, unknown> | null = {}): SwarmOutRun => ({
  dispatch_id: 'd1', hive_id: 'h1', hive: 'demo', session_id: 'sess', title: 'Hive task', origin: 'swarm-out',
  sent_at: '2026-10-31T09:00:00Z', state: 'stopped', stage: 'accepted',
  project_agent: { brain: 'SwarmAI', mode: 'change', profile: 'bugfix', revision: 'r', delivery: 'bundle', acceptance: null },
  settle: { settled: { at: '2026-10-31T10:00:00Z', outcome: 'passed', released: true } },
  result: res === null ? null : {
    ended: 'completed', ended_at: '2026-10-31T09:30:00Z', tokens: { input: 1_000_000, output: 0, cache_read: 0, cache_write: 0 },
    cost_usd: 1.5, pipeline: { run_id: 'run_h', profile: 'bugfix', status: 'completed', landing: 'landed', landed_sha: null, audit: [] },
    work: { human: { approved: 2, denied: 0, unanswered: 0, answered_questions: 1, said: 1 }, repos: [], changes: [], files: [], files_omitted: 0,
      steps: { total: 1, failed: 0, agents: 0, by_tool: {} } },
    ...res,
  },
  ...o,
}) as unknown as SwarmOutRun;

describe('pipelineRows', () => {
  it('a local run is the row its state says; a Hive run only when it ran a pipeline', () => {
    const q = fromLocal(run({ status: 'paused', pauseKind: 'decision', needsYou: true, landing: null }), 'P');
    expect([q.stage, q.task, q.outcome, q.where, q.brain]).toEqual(['needs_answer', 'answer', 'running', 'local', 'P']);
    const dropped = fromLocal(run({ status: 'abandoned', needsYou: true, landing: null }), 'P');
    expect([dropped.stage, dropped.task, dropped.outcome]).toEqual(['awaiting', 'confirm', 'discarded']);
    const confirmed = fromLocal(run({ status: 'abandoned', landedAs: { repo: 'a'.repeat(40) }, landing: null }), 'P');
    expect([confirmed.stage, confirmed.outcome]).toEqual(['done', 'manual']);
    expect(fromLocal(run({ landing: 'human_override' }), 'P').outcome).toBe('manual');
    expect(fromLocal(run({ notLanded: true, landing: 'not_landed' }), 'P').outcome).toBe('not_landed');
    expect(fromLocal(run({ status: 'paused', pauseKind: 'stopped', needsYou: true }), 'P').task).toBe('resume');
    const h = fromHive(hive())!;
    expect([h.where, h.hive, h.runId, h.outcome, h.brain, h.durationMin]).toEqual(['hive', 'demo', 'run_h', 'landed', 'SwarmAI', 30]);
    expect(h.interventions).toEqual({ answers: 1, corrections: 1, rescues: 0, total: 2 });     // approvals are not interventions
    // a stopped Hive run that only opens its session waits on nobody (Swarm Out's rule)
    const opensOnly = fromHive(hive({ stage: 'stopped', settle: null, actions: [{ key: 'open_session', primary: false }] }))!;
    expect([opensOnly.stage, opensOnly.task]).toEqual(['stopped', null]);
    expect(sections([opensOnly]).done.map((x) => x.key)).toEqual(['d1']);
    const asks = fromHive(hive({ stage: 'stopped', settle: null, actions: [{ key: 'rerun', primary: true }] }))!;
    expect(asks.task).toBe('stopped');
    expect(fromHive(hive({}, { work: { human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 1 } } }))!.interventions?.total).toBeNull();
    // the person's decisions on the run's record, as every "stepped in" reader counts them (swarm-run I13)
    expect(fromHive(hive({ person_decisions: 0 }))!.interventions?.total).toBe(0);
    expect(fromHive(hive({}, { pipeline: null }))).toBeNull();                                  // a native Hive run
    expect(fromHive(hive({}, null))).toBeNull();
    // the Project Agent kind of work a run went under: the summary's (local), the dispatch's (Hive)
    expect(fromLocal(run({ agentMode: 'code-change' }), 'P').agentMode).toBe('code-change');
    expect(fromLocal(run({ agentMode: null }), 'P').agentMode).toBeNull();
    expect(fromHive(hive())!.agentMode).toBe('change');
    expect(fromHive(hive({ project_agent: null }, { project_agent: { brain: 'SwarmAI', mode: 'research' } }))!.agentMode).toBe('research');
    expect(fromHive(hive({ project_agent: null }))!.agentMode).toBeNull();
  });

  it('waiting for you: to answer, then to confirm, then stopped, the longest waiting first', () => {
    const rows = [
      fromLocal(run({ id: 'stop', status: 'paused', pauseKind: 'stopped', needsYou: true, updatedAt: '2026-10-30T00:00:00Z' }), 'P'),
      fromLocal(run({ id: 'ask2', status: 'paused', pauseKind: 'decision', needsYou: true, updatedAt: '2026-10-31T00:00:00Z' }), 'P'),
      fromLocal(run({ id: 'ask1', status: 'paused', pauseKind: 'decision', needsYou: true, updatedAt: '2026-10-29T00:00:00Z' }), 'P'),
      fromLocal(run({ id: 'drop', status: 'abandoned', needsYou: true }), 'P'),
      fromLocal(run({ id: 'go', status: 'running' }), 'P'),
      fromLocal(run({ id: 'cancelled', status: 'abandoned', needsYou: false, landing: null }), 'P'),
    ];
    const s = sections(rows);
    expect(s.you.map((r) => r.key)).toEqual(['ask1', 'ask2', 'drop', 'stop']);
    expect(s.running.map((r) => r.key)).toEqual(['go']);
    expect(s.done.map((r) => r.key)).toContain('cancelled');
  });

  it('the filter keeps where and Brain; the Brain chips come from the rows', () => {
    const rows = allRows([{ project: 'P', runs: [run({ id: 'a' }), run({ id: 'b' })] }, { project: 'Q', runs: [run({ id: 'c' })] }],
      [hive(), hive({ dispatch_id: 'native' }, { pipeline: null })]);
    expect(rows.map((r) => r.key)).toEqual(['a', 'b', 'c', 'd1']);
    expect(filterRows(rows, { where: 'hive', brain: null }).map((r) => r.key)).toEqual(['d1']);
    expect(filterRows(rows, { where: 'local', brain: 'Q' }).map((r) => r.key)).toEqual(['c']);
    expect(brainChips(rows)).toEqual([{ brain: 'P', count: 2 }, { brain: 'Q', count: 1 }, { brain: 'SwarmAI', count: 1 }]);
  });

  it('autonomy and speed leave out what a run did not record, and tokens are in M', () => {
    const rows = [
      fromLocal(run({ id: 'a', cycleTimeMin: 10, billedTokens: 684_000_000, costUsd: 2 }), 'P'),
      fromLocal(run({ id: 'b', cycleTimeMin: 30, billedTokens: null, tokensActual: null, interventions: null, landing: 'not_landed', notLanded: true,
        audit: [{ dim: 'Speed', verdict: 'BLOCK', detail: '' }, { dim: 'Quality', verdict: 'PASS', detail: '' }] }), 'P'),
      fromLocal(run({ id: 'c', cycleTimeMin: 116, billedTokens: 400_000, interventions: { answers: 1, corrections: 2, rescues: 1, total: 4 }, landing: 'human_override' }), 'P'),
      fromLocal(run({ id: 'go', status: 'running', cycleTimeMin: null }), 'P'),
    ];
    const aut = autonomy(rows);
    expect([aut.finished, aut.known, aut.unattended, aut.avg]).toEqual([3, 2, 1, 2]);   // b unknown: left out, not 0
    expect(aut.bars).toEqual([{ key: 'c', title: 'Fix it', answers: 1, corrections: 2, rescues: 1 }]);
    const sp = speed(rows);
    expect([sp.median, sp.p90, sp.n]).toEqual([30, 99, 3]);
    expect([tokenM(684_000_000), tokenM(400_000), tokenM(12_400_000), tokenM(null)]).toEqual(['684M', '0.4M', '12M', '—']);
  });
});


describe('brainLanes', () => {
  const live = (id: string, created: string, o: Partial<PipelineRunSummary> = {}, brain = 'P') =>
    fromLocal(run({ id, status: 'running', progress: '5/8', createdAt: created, finishedAt: null, ...o }), brain);

  it('lists the Brain\'s live runs, waiting first, newest running next, at most three', () => {
    const rows = [
      live('run_old', '2026-11-01T08:00:00Z'),
      live('run_new', '2026-11-01T10:00:00Z'),
      live('run_ask', '2026-11-01T07:00:00Z', { status: 'paused', pauseKind: 'decision', needsYou: true }),
      live('run_mid', '2026-11-01T09:00:00Z'),
      live('run_other', '2026-11-01T11:00:00Z', {}, 'Q'),
      fromLocal(run({ id: 'run_done' }), 'P'),
    ];
    const out = brainLanes(rows, 'P');
    expect(out.lanes.map((l) => l.runId)).toEqual(['run_ask', 'run_new', 'run_mid']);
    expect(out.lanes[0].waiting).toBe(true);
    expect(out.lanes[1].waiting).toBe(false);
    expect(out.more).toBe(1);
    expect(out.total).toBe(4);
  });

  it('draws stage dots only from a reported N/M, never for a Hive run', () => {
    const local = live('run_a', '2026-11-01T08:00:00Z');
    const remote = { ...live('run_h', '2026-11-01T09:00:00Z'), where: 'hive' as const, hive: 'us-east', local: undefined };
    const odd = live('run_x', '2026-11-01T07:00:00Z', { progress: 'review' });
    const out = brainLanes([local, remote, odd], 'P');
    const byId = Object.fromEntries(out.lanes.map((l) => [l.runId, l]));
    expect(byId.run_a.progress).toEqual({ done: 5, total: 8 });
    expect(byId.run_a.place).toBe('local');
    expect(byId.run_h.progress).toBeNull();
    expect(byId.run_h.place).toBe('us-east');
    expect(byId.run_x.progress).toBeNull();
  });

  it('is empty when nothing of the Brain is under way', () => {
    expect(brainLanes([fromLocal(run({}), 'P')], 'P')).toEqual({ lanes: [], more: 0, total: 0 });
  });
});

const STAGES = (profile: string[], at: string, attempts: Record<string, number> = {}) => profile.map((name, i) => ({
  name, status: name === at ? 'open' : i < profile.indexOf(at) ? 'completed' : 'pending', attempts: attempts[name] ?? (i <= profile.indexOf(at) ? 1 : null),
}));
const FULL = ['evaluate', 'think', 'plan', 'build', 'review', 'test', 'deliver', 'reflect'];
const RESEARCH = ['evaluate', 'think', 'deliver', 'reflect'];

describe('the Line', () => {
  it('lineGroups draws each run along its profile', () => {
    const a = fromLocal(run({ id: 'a', status: 'running', stage: 'review', stages: STAGES(FULL, 'review', { review: 2 }) }), 'P');
    const b = fromLocal(run({ id: 'b', status: 'running', profile: 'research', stage: 'deliver', stages: STAGES(RESEARCH, 'deliver') }), 'P');
    const g = lineGroups([a, b]);
    const ta = g[0].items.find((i) => i.track.row.key === 'a')!.track;
    expect([ta.at, ta.kind, ta.rounds]).toEqual(['review', 'run', { review: 2 }]);
    const tb = trackOf(b);
    expect(tb.path).toEqual(RESEARCH);                 // PLAN..TEST are not on a research run's track
    expect(tb.at).toBe('deliver');
  });

  it('a waiting, a stopped and a done run: one meaning per chip', () => {
    const ask = fromLocal(run({ id: 'ask', status: 'paused', pauseKind: 'decision', needsYou: true, stage: 'plan', stages: STAGES(FULL, 'plan') }), 'P');
    const stop = fromLocal(run({ id: 'stop', status: 'paused', pauseKind: 'user_stop' as never, needsYou: true, stage: 'test', stages: STAGES(FULL, 'test') }), 'P');
    expect(trackOf(ask).kind).toBe('you');
    expect(trackOf(stop).kind).toBe('stop');
    expect(lineGroups([fromLocal(run({ id: 'd' }), 'P')])).toEqual([]);   // a finished run is not on the line
  });

  it('a Hive run without a funnel is on its Hive', () => {
    const r = fromHive(hive({ state: 'running', stage: 'running', settle: null }, { ended: null, ended_at: null }))!;
    const t = trackOf(r);
    expect([t.at, t.kind, t.path]).toEqual([null, 'run', []]);     // one band: its stage is not guessed
    const back = fromHive(hive({ state: 'stopped', stage: 'awaiting', settle: null, needs_person: false,
      funnel: { gates: [{ key: 'review', state: 'done', by: 'agent', at: null, ref: null }, { key: 'land', state: 'now', by: 'agent', at: null, ref: null }],
        current: 'land', ask: null, reruns: 0 } }))!;
    expect([trackOf(back).at, trackOf(back).kind, trackOf(back).path]).toEqual(['land', 'judge', ['return', 'land']]);
    const mine = fromHive(hive({ dispatch_id: 'd2', state: 'stopped', stage: 'awaiting', settle: null, needs_person: true,
      funnel: { gates: [{ key: 'judge', state: 'person', by: null, at: null, ref: null }], current: 'judge', ask: null, reruns: 0 } }))!;
    expect([trackOf(mine).at, trackOf(mine).kind]).toEqual(['judge', 'you']);
    // the strip and the line agree: the agent's run is not waiting on you, the person's is
    const s = sections([back, mine]);
    expect(s.you.map((x) => x.key)).toEqual(['d2']);
    expect(s.running.map((x) => x.key)).toEqual(['d1']);
    expect(back.task).toBeNull();
  });

  it('a Hive run with a live stage stands at it along its planned stages; without one it is the band', () => {
    const live = { run_id: 'run_h', stage: 'build', stages: ['evaluate', 'plan', 'build', 'review', 'test', 'deliver', 'reflect'], at: null };
    const r = fromHive(hive({ state: 'running', stage: 'running', settle: null, live_stage: live }, { ended: null, ended_at: null }))!;
    const t = trackOf(r);
    expect([t.at, t.kind, t.path]).toEqual(['build', 'run', live.stages]);
    const unknown = fromHive(hive({ state: 'running', stage: 'running', settle: null, live_stage: { ...live, stage: 'evil' } }, { ended: null, ended_at: null }))!;
    expect(trackOf(unknown).at).toBeNull();
    const ended = fromHive(hive({ live_stage: live }))!;                       // accepted: no live stage used
    expect(ended.pipelineStage).toBeNull();
  });

  it('a Swarm Run\'s work packages sit under their lead', () => {
    const lead = fromLocal(run({ id: 'run_lead', status: 'running', stage: 'build', stages: STAGES(FULL, 'build') }), 'SwarmAI');
    const child = fromHive(hive({ dispatch_id: 'c1', state: 'running', stage: 'running', settle: null, parent_run: 'run_lead', wp: 'W1' },
      { ended: null, ended_at: null }))!;
    const ask = fromLocal(run({ id: 'ask', status: 'paused', pauseKind: 'decision', needsYou: true, stage: 'plan', stages: STAGES(FULL, 'plan'),
      updatedAt: '2026-11-01T10:00:00Z' }), 'SwarmAI');
    const g = lineGroups([lead, child, ask]);
    expect(g).toHaveLength(1);
    const item = g[0].items.find((i) => i.track.row.key === 'run_lead')!;
    expect(item.children.map((c) => c.row.key)).toEqual(['c1']);
    expect(g[0].items.some((i) => i.track.row.key === 'c1')).toBe(false);
    expect([g[0].count, g[0].running, g[0].waiting]).toEqual([3, 2, 1]);
  });

  it('rows without a stage record have no timing', () => {
    const old = fromLocal(run({ id: 'old', stage: null, stages: null, stageTimeline: null, status: 'running' }), 'P');
    expect(trackOf(old).at).toBeNull();
    expect(timeGoes([old]).covered).toBe(0);
    const timed = fromLocal(run({ id: 't', stages: STAGES(FULL, 'reflect', { review: 3 }), stageTimeline: {
      evaluate: { enteredAt: 'x', activeS: 600, waitS: 0 }, review: { enteredAt: 'y', activeS: 1200, waitS: 300 } } }), 'P');
    const untimed = fromLocal(run({ id: 'u', stages: STAGES(FULL, 'reflect', { review: 5 }), stageTimeline: null }), 'P');
    const tg = timeGoes([old, timed, untimed]);         // an untimed run's rounds are not counted
    expect(tg.covered).toBe(1);
    expect(tg.stations.review).toEqual({ agentMin: 20, waitMin: 5, rounds: 2, runs: 1 });
    expect(tg.stations.plan).toEqual({ agentMin: null, waitMin: null, rounds: 0, runs: 0 });
  });
});

describe('Eval', () => {
  it('evalDays: 30 local days by start, nobody / someone stepped in / not finished; steppedIn: the last 15 days, newest first', () => {
    const day = (d: number) => new Date(NOW - d * 86400_000).toISOString();
    const iv = (total: number) => ({ answers: total, corrections: 0, rescues: 0, total });
    const rows = [
      fromLocal(run({ id: 'a', createdAt: day(0), interventions: iv(0) }), 'P'),
      fromLocal(run({ id: 'b', createdAt: day(0), interventions: iv(2) }), 'P'),
      fromLocal(run({ id: 'c', createdAt: day(1), status: 'running', interventions: iv(0) }), 'P'),
      fromLocal(run({ id: 'd', createdAt: day(3), interventions: iv(1) }), 'P'),
      fromLocal(run({ id: 'e', createdAt: day(20), interventions: iv(1) }), 'P'),
      fromLocal(run({ id: 'f', createdAt: day(40), interventions: iv(0) }), 'P'),     // outside the 30 days
    ];
    const days = evalDays(rows, NOW);
    expect(days).toHaveLength(30);
    expect(days[29]).toMatchObject({ auto: 1, stepped: 1, other: 0 });
    expect(days[28]).toMatchObject({ auto: 0, stepped: 0, other: 1 });                // still running
    expect(days.reduce((n, d) => n + d.auto + d.stepped + d.other, 0)).toBe(5);
    expect(steppedIn(rows, NOW).map((r) => r.key)).toEqual(['b', 'd']);
  });

  it('a paused run is not finished: not in the stepped-in list, a grey bar; the halves cut at local midnight', () => {
    const iv = (total: number) => ({ answers: total, corrections: 0, rescues: 0, total });
    const paused = fromLocal(run({ id: 'p', createdAt: new Date(NOW - 3600_000).toISOString(), status: 'paused', pauseKind: 'decision',
      needsYou: true, interventions: iv(1) }), 'P');
    expect(steppedIn([paused], NOW)).toEqual([]);
    expect(evalDays([paused], NOW)[29]).toMatchObject({ auto: 0, stepped: 0, other: 1 });
    expect(evalMeasures([paused], NOW).cur.autonomous.n).toBe(0);
    // the 15th day back, late in the evening: the earlier half; the 14th, just after midnight: the last 15 days
    const at = (back: number, h: number, m: number) => { const d = new Date(NOW); d.setDate(d.getDate() - back); d.setHours(h, m, 0, 0); return d.toISOString(); };
    const early = fromLocal(run({ id: 'e', createdAt: at(15, 23, 50), interventions: iv(1) }), 'P');
    const late = fromLocal(run({ id: 'l', createdAt: at(14, 0, 10), interventions: iv(1) }), 'P');
    const { cut } = evalHalves(NOW);
    expect(Date.parse(late.createdAt) >= cut && Date.parse(early.createdAt) < cut).toBe(true);
    expect(steppedIn([early, late], NOW).map((r) => r.key)).toEqual(['l']);
    const { cur, prev } = evalMeasures([early, late], NOW);
    expect([cur.started, prev.started]).toEqual([1, 1]);
    const days = evalDays([early, late], NOW);
    expect([days[14].stepped, days[15].stepped]).toEqual([1, 1]);         // each in its half's bars
  });

  it('evalMeasures leaves unrecorded measures null', () => {
    const rows = [
      fromLocal(run({ id: 'a', createdAt: '2026-10-30T10:00:00Z', costUsd: null, billedTokens: null, tokensActual: null, interventions: null }), 'P'),
    ];
    const { cur, prev } = evalMeasures(rows, NOW);
    expect(cur.autonomous).toEqual({ k: 0, n: 0, rate: null, range: null });
    expect([cur.human.answers, cur.cost.usd, cur.cost.tokens, cur.knowledge.used, cur.knowledge.new]).toEqual([null, null, null, null, null]);
    expect(cur.started).toBe(1);
    expect(prev.started).toBe(0);
    expect(prev.cycle.median).toBeNull();
  });

  it('splits the 30-day window in halves; ① is k of n with a Wilson range', () => {
    const rows = [
      fromLocal(run({ id: 'a', createdAt: '2026-10-30T10:00:00Z' }), 'P'),
      fromLocal(run({ id: 'b', createdAt: '2026-10-29T10:00:00Z', interventions: { answers: 2, corrections: 1, rescues: 0, total: 3 } }), 'Q'),
      fromLocal(run({ id: 'old', createdAt: '2026-10-10T10:00:00Z' }), 'P'),
    ];
    const { cur, prev } = evalMeasures(rows, NOW);
    expect([cur.autonomous.k, cur.autonomous.n, prev.autonomous.n]).toEqual([1, 2, 1]);
    expect([cur.human.answers, cur.human.unplanned]).toEqual([1, 0.5]);
    const w = wilson(17, 19)!;
    expect([Math.round(w.lo * 100), Math.round(w.hi * 100)]).toEqual([69, 97]);
    expect(wilson(0, 0)).toBeNull();
    expect(evalByBrain(rows).map((b) => [b.brain, b.m.started])).toEqual([['P', 2], ['Q', 1]]);
    const k = evalMeasures([
      fromLocal(run({ id: 'k1', createdAt: '2026-10-30T10:00:00Z', knowledge: { used: 8, new: 3 } }), 'P'),
      fromLocal(run({ id: 'k2', createdAt: '2026-10-30T11:00:00Z', knowledge: { used: 4, new: null } }), 'P'),
    ], NOW).cur.knowledge;
    expect(k).toEqual({ used: 6, new: 3, n: 2, newRuns: 1 });        // a run before REFLECT has no 'new'
  });
});
