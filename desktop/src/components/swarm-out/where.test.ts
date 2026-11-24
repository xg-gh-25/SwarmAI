/**
 * Where a run ran and its time facts (./where).
 *
 * Questions: does the run's own last round decide EC2 or AgentCore, then its Hive's
 * carrier in the fleet, then the Elastic Workers location's name, else EC2; is the
 * start written MM/DD HH:mm in local time; does ``runTime`` say what each number measures:
 * the running time while the Hive works (its open round only), the Hive work and the
 * wait on the agent or the person (since the result became final) once it is back, the
 * work, its rounds and an at-least mark once settled.
 */
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect } from 'vitest';
import type { SwarmOutFleet, SwarmOutRun } from '../../services/swarmOut';
import { groupedId, minutesText, runTime, runWhere, stamp, waitText } from './where';

const run = (over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: 'so_1', hive_id: 'h', hive: 'demo', session_id: 's', title: 't', origin: 'swarm-out',
  sent_at: '2026-11-23T06:02:00Z', state: 'running', state_reason: 'streaming', ...over,
});
const fleet = (carrier: 'ec2' | 'agentcore'): SwarmOutFleet => ({ running: 1, hives: [{ name: 'demo', status: 'running', region: null,
  version: null, instance_type: null, reachable: true, streaming: 0, saturated: false, memory_percent: null, carrier }] });

describe('where', () => {
  it('the last round first, then the fleet, then the location name, else EC2', () => {
    expect(runWhere(run({ rounds: [{ carrier: 'ec2' }, { carrier: 'agentcore' }] }), fleet('ec2'))).toEqual({ carrier: 'agentcore', name: 'Elastic Workers' });
    expect(runWhere(run(), fleet('agentcore')).carrier).toBe('agentcore');
    expect(runWhere(run({ hive: 'elastic' }), null).carrier).toBe('agentcore');
    expect(runWhere(run(), null)).toEqual({ carrier: 'ec2', name: 'demo' });
  });

  it('the start as MM/DD HH:mm, local time; the duration in minutes', () => {
    expect(stamp('2026-11-23T06:02:00Z')).toBe('11/23 14:02');
    expect(stamp(null)).toBe('');
    expect([minutesText(0), minutesText(18), minutesText(null)]).toEqual(['<1 min', '18 min', '—']);
    expect([waitText(40), waitText(125), waitText(60 * 50), waitText(null)]).toEqual(['40 min', '2 h', '2 d', '—']);
    expect(groupedId('533267412361')).toBe('5332 6741 2361');
  });
});

const NOW = Date.parse('2026-11-23T08:20:30Z');
const res = (over: Record<string, unknown> = {}) => ({ ended: 'completed', provisional: false, ended_at: '2026-11-23T06:20:00Z',
  final_at: '2026-11-23T06:20:30Z', turns: 1, active_s: 18 * 60, active_partial: false, turn_spans: [], ...over }) as never;

describe('runTime: what each number measures', () => {
  it('while the Hive works: the running time of its open round, else since it was sent', () => {
    expect(runTime(run({ sent_at: '2026-11-23T08:08:00Z' }), NOW)).toEqual({ phase: 'running', min: 12 });
    // a re-run 18 h after the first round: only the open round runs now
    const rerun = run({ sent_at: '2026-11-22T14:00:00Z', result: res({ turn_spans: [
      { start: '2026-11-22T14:00:00Z', end: '2026-11-22T14:01:00Z', active_s: 60, by: 'dispatch' },
      { start: '2026-11-23T08:15:00Z', end: null, active_s: null, by: 'agent' }] }) });
    expect(runTime(rerun, NOW)).toEqual({ phase: 'running', min: 5 });
  });

  it('a result back, not settled: the work, and the wait on the agent or on you since it became final', () => {
    // ended 06:20, came back final at 07:20 (the Host was offline): the agent's wait is from 07:20
    const agent = run({ stage: 'awaiting', state: 'stopped', needs_person: false, result: res({ final_at: '2026-11-23T07:20:00Z' }) });
    expect(runTime(agent, NOW)).toEqual({ phase: 'waiting', worked: 18, partial: false, rounds: 1, who: 'agent', waited: 60 });
    const you = run({ stage: 'awaiting', state: 'stopped', needs_person: true, result: res({ final_at: null }) });
    expect(runTime(you, NOW)).toMatchObject({ phase: 'waiting', who: 'person', waited: 120 });   // from ended_at
    // a question: since the first one asked, on you
    const asks = [{ kind: 'question', hive: 'demo', hive_id: 'h', session_id: 's', request_id: 'q', asked_at: '2026-11-23T07:40:00Z' }] as never;
    expect(runTime(run({ stage: 'needs_answer', state: 'waiting', result: res({ provisional: true }) }), NOW, asks))
      .toMatchObject({ phase: 'waiting', who: 'person', waited: 40 });
  });

  it('settled: the Hive work, its rounds, at least when a turn had no duration', () => {
    const two = run({ stage: 'accepted', state: 'stopped', result: res({ turns: 2, active_s: 90 }) });
    expect(runTime(two, NOW)).toEqual({ phase: 'done', worked: 2, partial: false, rounds: 2 });
    expect(runTime(run({ stage: 'closed', state: 'stopped', result: res({ active_partial: true }) }), NOW))
      .toMatchObject({ phase: 'done', worked: 18, partial: true });
    expect(runTime(run({ stage: 'accepted', state: 'stopped', result: null }), NOW)).toEqual({ phase: 'done', worked: null, partial: false, rounds: 0 });
    // a stopped run sits under "Recently done" and in the history: its work, never a wait
    // that grows for ever (nor a dispatch from before stages, read as stopped)
    expect(runTime(run({ stage: 'stopped', state: 'stopped', result: res() }), NOW)).toEqual({ phase: 'done', worked: 18, partial: false, rounds: 1 });
    expect(runTime(run({ state: 'stopped', result: res() }), NOW).phase).toBe('done');
  });
});
