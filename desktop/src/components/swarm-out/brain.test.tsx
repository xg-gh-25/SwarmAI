/**
 * The Project Agent part of Swarm Out runs (design 2026-10-28-project-agent-flow §7 ⑦⑧):
 * a run's Brain and mode on its card, the checks line, Brain gate refusals, and the
 * last week grouped by Brain.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { SwarmOutResult, SwarmOutRun } from '../../services/swarmOut';
import { breakdown, brainLabel, checksLine, executionLine, gateLine, inWindow, lessonsLine } from './live';
import i18n from '../../i18n';

// These cases check the Chinese copy (zh.json); swarmOutI18n.test.tsx checks English.
beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

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

  it('says which checks ran, errored or were not seen, never that they passed or did not run', () => {
    expect(checksLine(res())).toBeNull();
    expect(checksLine(res({ checks: [{ id: 'tests', status: 'ran' }, { id: 'lint', status: 'not_seen' },
      { id: 'build', status: 'errored' }] }))).toBe('校验：tests 跑了 · lint 没看到 · build 报错了');
    expect(checksLine(res({ checks_partial: true, checks: [{ id: 'lint', status: 'not_seen' },
      { id: 'x', status: 'unknown' }] }))).toBe('校验：lint 没看到 · x 跑了但没有结果（只读了最近的轮次）');
    // a record kept before "not seen" says not_run: it reads as not seen, never "没跑"
    const legacy = checksLine(res({ checks: [{ id: 'lint', status: 'not_run' as never }] }));
    expect(legacy).toBe('校验：lint 没看到');
    expect(legacy).not.toContain('没跑');
  });

  it('says what came back as lessons, what is not queued yet, and what CDF did', () => {
    const l = (o = {}) => ({ returned: 0, pending: 0, accepted: false, sent: 0, written: 0, rejected: 0, judging: 0,
      skipped: 0, refused: 0, truncated: false, error: null, ...o });
    expect(lessonsLine(res())).toBeNull();
    expect(lessonsLine(res({ lessons: l() }))).toBeNull();
    expect(lessonsLine(res({ lessons: l({ returned: 3, pending: 2, skipped: 1 }) })))
      .toBe('教训：2 条还没交给本机 CDF（本机知识库暂时不可用，下次检查再交）');
    expect(lessonsLine(res({ lessons: l({ returned: 1, skipped: 1 }) }))).toBe('教训：交回 1 条，都不是这个 Brain 的');
    expect(lessonsLine(res({ lessons: l({ returned: 5, sent: 3, written: 1, rejected: 1, judging: 1, pending: 1, skipped: 1 }) })))
      .toBe('教训：交回 3 条，写进 Brain 1 条、没写入 1 条、处理中 1 条、另有 1 条还没交给本机 CDF（1 条不是这个 Brain 的，没收）');
    expect(lessonsLine(res({ lessons: l({ error: 'not_supported' }) }))).toBe('教训：这台 Hive 还不能交回教训（需要更新 Hive）');
    expect(lessonsLine(res({ lessons: l({ error: 'hive_silent' }) }))).toBe('教训：这次没取回来，下次检查再试');
  });

  it('says when a run did not run the way it was dispatched', () => {
    expect(executionLine(res())).toBeNull();
    expect(executionLine(res({ execution_check: { state: 'matches', why: null } }))).toBeNull();
    expect(executionLine(res({ execution_check: { state: 'not_yet', why: null } }))).toBeNull();
    expect(executionLine(res({ execution_check: { state: 'differs', why: 'made_a_run' } })))
      .toBe('执行方式和派发时定的不同：native 任务建了 pipeline run');
    expect(executionLine(res({ execution_check: { state: 'differs', why: 'other_profile' } })))
      .toBe('执行方式和派发时定的不同：run 用了别的 profile');
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
    const rows = breakdown(inWindow([
      run('1', 'Lib', res({ checks: [{ id: 't', status: 'not_seen' }] })),
      run('2', 'Lib', res({ ended: 'error' })),
      run('3', 'General', res()),
      run('4', null, res()),
      run('5', 'Lib', res(), 200),                     // older than 7 days
      run('6', 'Lib', res({ provisional: true, checks: [{ id: 't', status: 'not_seen' }] })),   // still going
    ], NOW, 7), 'brain');
    expect(rows.map((r) => [r.label, r.agent ?? null, r.tally.runs, r.tally.by.completed, r.tally.by.error, r.tally.checksMissed])).toEqual([
      ['Lib', 'code-change · bugfix', 3, 2, 1, 1], ['通用', 'Runner Agent', 1, 1, 0, 0], ['没走 Project Agent', null, 1, 1, 0, 0]]);
  });
});

describe('the review strip names each check by what the Host saw', () => {
  it('says errored for an errored check and not seen for a legacy not_run, never "not run"', async () => {
    const { machineChecks } = await import('./runReview');
    const { checkStatus } = await import('./live');
    expect(checkStatus('not_run')).toBe('not_seen');
    expect(checkStatus('whatever')).toBe('not_seen');
    expect(checkStatus('errored')).toBe('errored');
    const r = { result: res({ ended: 'completed', checks: [{ id: 'types', status: 'errored' },
      { id: 'lint', status: 'not_run' as never }, { id: 'e2e', status: 'unknown' }] }) } as never;
    const line = machineChecks(r).find((c) => c.key === 'checks');
    expect(line?.ok).toBe(false);
    expect(line?.text).toContain('报错：types');
    expect(line?.text).toContain('没看到：lint');
    expect(line?.text).toContain('跑了但没有结果：e2e');
    expect(line?.text).not.toContain('没跑');
  });

  it('has no "not run" word left in either language', async () => {
    const en = (await import('../../i18n/locales/en.json')).default as Record<string, any>;
    const zh = (await import('../../i18n/locales/zh.json')).default as Record<string, any>;
    for (const d of [en, zh]) {
      expect(d.swarmOut.check.notRun).toBeUndefined();
      expect(d.swarmOut.review.check.checksMissed).toBeUndefined();
    }
  });
});
