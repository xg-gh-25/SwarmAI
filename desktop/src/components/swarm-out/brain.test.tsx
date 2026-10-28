/**
 * The Project Agent part of Swarm Out runs (design 2026-10-28-project-agent-flow §7 ⑦⑧):
 * a run's Brain and mode on its card, the checks line, Brain gate refusals, and the
 * last week grouped by Brain.
 */
import { describe, it, expect } from 'vitest';
import type { SwarmOutResult, SwarmOutRun } from '../../services/swarmOut';
import { brainLabel, checksLine, gateLine, runsByBrain } from './live';

const NOW = Date.parse('2026-10-29T12:00:00Z');
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();
const res = (o: Partial<SwarmOutResult> = {}): SwarmOutResult => ({
  ended: 'completed', provisional: false, outcome_recorded: true, hive_records_outcomes: true, error: null,
  ended_at: null, turns: 1, older_turns_not_read: false, active_s: 10, tokens: null, cost_usd: 0.1,
  cost_partial: false, report_chars: 0, kinds: [],
  work: { repos: null, changes: [], files: [], files_omitted: 0,
    steps: { total: 0, failed: 0, agents: 0, by_tool: {} },
    human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0 } },
  pipeline: null, dropped: 0, ...o,
});
const run = (id: string, brain: string | null, result: SwarmOutResult | null, hours = 1): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h', hive: 'explorer', session_id: 's' + id, title: id, origin: 'swarm-out',
  sent_at: ago(hours), waiting: false, running: false, state: 'stopped', result,
  ...(brain ? { project_agent: { brain, mode: 'code-change', revision: 'r', profile: 'bugfix',
    delivery: 'bundle', acceptance: 'tests pass' } } : {}),
});

describe('the Brain of a run', () => {
  it('names the Brain, the Runner Agent, or no Project Agent', () => {
    expect(brainLabel(run('a', 'Lib', null))).toBe('Lib');
    expect(brainLabel(run('a', 'General', null))).toBe('通用（Runner Agent）');
    expect(brainLabel(run('a', null, null))).toBe('没走 Project Agent');
  });

  it('says which checks ran, errored or did not run, never that they passed', () => {
    expect(checksLine(res())).toBeNull();
    expect(checksLine(res({ checks: [{ id: 'tests', status: 'ran' }, { id: 'lint', status: 'not_run' },
      { id: 'build', status: 'errored' }] }))).toBe('校验：tests 跑了 · lint 没跑 · build 报错了');
    expect(checksLine(res({ checks_partial: true, checks: [{ id: 'lint', status: 'not_run' },
      { id: 'x', status: 'unknown' }] }))).toBe('校验：lint 没看到 · x 跑了但没有结果（只读了最近的轮次）');
  });

  it('counts Brain gate refusals by gate', () => {
    expect(gateLine(res())).toBeNull();
    expect(gateLine(res({ gate_refusals: { no_git_push: 2, integrity: 1, 'error:lint': 1 } })))
      .toBe('Brain gate 拦下 4 次（no_git_push 2、完整性 1、lint 出错 1）');
    const pa = { brain: 'Lib', mode: 'm', profile: 'bugfix', delivery: 'bundle' };
    expect(gateLine(res({ project_agent: pa, gate_refusals: null }))).toBe('Brain gate：这台 Hive 不记录拦截次数');
    expect(gateLine(res({ project_agent: pa, gate_refusals: {} }))).toBeNull();
  });

  it('groups the week by Brain, runs without a Project Agent last', () => {
    const rows = runsByBrain([
      run('1', 'Lib', res({ checks: [{ id: 't', status: 'not_run' }] })),
      run('2', 'Lib', res({ ended: 'error' })),
      run('3', 'General', res()),
      run('4', null, res()),
      run('5', 'Lib', res(), 200),                     // older than 7 days
      run('6', 'Lib', res({ provisional: true, checks: [{ id: 't', status: 'not_run' }] })),   // still going
    ], NOW);
    expect(rows.map((r) => [r.label, r.runs, r.completed, r.failed, r.checksNotRun])).toEqual([
      ['Lib', 3, 2, 1, 1], ['通用（Runner Agent）', 1, 1, 0, 0], ['没走 Project Agent', 1, 1, 0, 0]]);
  });
});
