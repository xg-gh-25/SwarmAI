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
 * - are the distribution's bars per day until the data spans 14 days, then per week?
 * - does each measure leave out what a run did not record (never a 0), and are tokens
 *   always in M?
 * - are a Brain's lanes its live runs only (waiting first, then newest), at most three
 *   with the rest counted, with stage dots only where the engine reported progress?
 */
import { describe, it, expect } from 'vitest';
import type { PipelineRunSummary } from '../../services/pipelines';
import type { SwarmOutRun } from '../../services/swarmOut';
import {
  allRows, autonomy, brainChips, brainLanes, cost, delivery, distribution, filterRows, fromHive, fromLocal, quality, sections, speed, tokenM,
} from './pipelineRows';

const NOW = Date.parse('2026-11-01T12:00:00Z');
const run = (o: Partial<PipelineRunSummary>): PipelineRunSummary => ({
  id: 'run_a', requirement: 'Fix it', status: 'completed', profile: 'full', progress: '8/8',
  cycleTimeMin: 40, tokensActual: null, createdAt: '2026-10-31T10:00:00Z', updatedAt: '2026-10-31T11:00:00Z',
  pauseKind: null, needsYou: false, landedAs: null, checkpointReason: null, ownerSessionId: 's1',
  notLanded: false, reportPath: null, auditCard: null, finishedAt: '2026-10-31T10:40:00Z', activeS: null,
  billedTokens: 50_000_000, costUsd: null, humanDecisions: 0,
  interventions: { answers: 0, corrections: 0, rescues: 0, total: 0 }, landing: 'landed',
  notLandedReason: null, rework: 0, audit: [], ...o,
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
    expect(fromHive(hive({}, { pipeline: null }))).toBeNull();                                  // a native Hive run
    expect(fromHive(hive({}, null))).toBeNull();
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

  it('the distribution is per day until the data spans 14 days, then per week', () => {
    const rows = [fromLocal(run({ createdAt: '2026-10-30T10:00:00Z' }), 'P'), fromHive(hive({ sent_at: '2026-11-01T01:00:00Z' }))!];
    const d = distribution(rows, NOW);
    expect([d.total, d.local, d.hive, d.weekly]).toEqual([2, 1, 1, false]);
    expect(d.points.map((p) => [p.label, p.local, p.hive])).toEqual([['2026-10-30', 1, 0], ['2026-10-31', 0, 0], ['2026-11-01', 0, 1]]);
    expect(d.byHive).toEqual([{ hive: 'demo', count: 1 }]);
    const old = [...rows, fromLocal(run({ id: 'old', createdAt: '2026-10-10T10:00:00Z' }), 'P')];
    const w = distribution(old, NOW);
    expect(w.weekly).toBe(true);
    expect(w.points.every((p) => new Date(`${p.label}T00:00:00Z`).getUTCDay() === 1)).toBe(true);   // weeks start Monday
    expect(w.points.reduce((s, p) => s + p.local + p.hive, 0)).toBe(3);
  });

  it('each measure leaves out what a run did not record, and tokens are in M', () => {
    const rows = [
      fromLocal(run({ id: 'a', cycleTimeMin: 10, billedTokens: 684_000_000, costUsd: 2 }), 'P'),
      fromLocal(run({ id: 'b', cycleTimeMin: 30, billedTokens: null, tokensActual: null, interventions: null, landing: 'not_landed', notLanded: true,
        audit: [{ dim: 'Speed', verdict: 'BLOCK', detail: '' }, { dim: 'Quality', verdict: 'PASS', detail: '' }] }), 'P'),
      fromLocal(run({ id: 'c', cycleTimeMin: 116, billedTokens: 400_000, interventions: { answers: 1, corrections: 2, rescues: 1, total: 4 }, landing: 'human_override' }), 'P'),
      fromLocal(run({ id: 'go', status: 'running', cycleTimeMin: null }), 'P'),
    ];
    const del = delivery(rows, NOW);
    expect([del.delivered, del.total, del.counts.landed, del.counts.manual, del.counts.not_landed]).toEqual([2, 4, 1, 1, 1]);
    const aut = autonomy(rows);
    expect([aut.finished, aut.known, aut.unattended, aut.avg]).toEqual([3, 2, 1, 2]);   // b unknown: left out, not 0
    expect(aut.bars).toEqual([{ key: 'c', title: 'Fix it', answers: 1, corrections: 2, rescues: 1 }]);
    const q = quality(rows);
    expect([q.blocked, q.audited, q.blockedDims]).toEqual([1, 1, [{ dim: 'Speed', count: 1 }]]);
    const sp = speed(rows);
    expect([sp.median, sp.p90, sp.n]).toEqual([30, 99, 3]);
    const c = cost(rows);
    expect([c.tokensKnown, c.usd, c.usdKnown, c.top]).toEqual([3, 2, 1, 'a']);   // b recorded none: left out
    expect(tokenM(c.perDelivery)).toBe('367M');                    // every run's tokens (684M + 0.4M + 50M) over 2 deliveries
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
