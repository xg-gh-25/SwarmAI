/**
 * A run's result on the Swarm Out panel (design docs/design/2026-10-28-swarm-out-run-results.md
 * §4.6): how it ended, what it did for every kind of run (pipeline, native coding,
 * files, an answer), its numbers, the details on demand (the reply as plain text), and
 * the history's days by outcome.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { dailyByOutcome, endedLabel, numberParts, outcomeBucket, runJudge, workLines } from './live';

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), answer: vi.fn(), hives: vi.fn(), result: vi.fn(), refresh: vi.fn() },
}));

const NOW = new Date(2026, 9, 28, 12, 0).getTime();
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();

const result = (over: Partial<SwarmOutResult> = {}, work: Partial<SwarmOutResult['work']> = {}): SwarmOutResult => ({
  ended: 'completed', provisional: false, outcome_recorded: true, hive_records_outcomes: true, error: null, ended_at: ago(0.5), turns: 1,
  older_turns_not_read: false, active_s: 300, tokens: { input: 10, output: 5 }, cost_usd: 1.234, cost_partial: false,
  report_chars: 12, kinds: ['answer'], pipeline: null, dropped: 0, ...over,
  work: { repos: null, changes: [], files: [], files_omitted: 0,
    steps: { total: 3, failed: 1, agents: 0, by_tool: { Bash: 2, Read: 1 } },
    human: { approved: 1, denied: 0, unanswered: 0, answered_questions: 1 }, ...work },
});

const repo = (over = {}) => ({
  url: 'https://github.com/acme/widget', ref: 'main', dir: 'Hive/repos/github.com/acme/widget', present: true,
  origin_matches: true, base: 'a'.repeat(40), head: 'b'.repeat(40), commits: ['b'.repeat(40)], commits_total: 1,
  files: [{ path: 'src/x.py', added: 120, deleted: 30 }], files_total: 4, added: 120, deleted: 30, untracked: 0,
  pushed: 'no' as const, error: null, ...over,
});

// ── the rules ─────────────────────────────────────────────────────────────────

describe('how a run ended', () => {
  it.each([
    [{ ended: 'completed' as const }, '做完', 'normal'],
    [{ ended: 'error' as const, error: { code: 'MODEL_ERROR' } }, '出错 · MODEL_ERROR', 'error'],
    [{ ended: 'crashed' as const, error: { code: 'crashed' } }, '出错（崩溃） · crashed', 'error'],
    [{ ended: 'turn_limit' as const }, '到了轮数上限', 'normal'],
    [{ ended: 'error' as const, error: { code: 'other' } }, '出错', 'error'],
    [{ ended: 'no_terminal' as const }, '没有正常结束（没收到结束事件）', 'muted'],
    [{ ended: 'stopped' as const }, '被停', 'normal'],
    [{ ended: 'paused' as const, provisional: true }, '停在问题或审批上 · 暂定', 'muted'],
    [{ ended: 'no_record' as const, outcome_recorded: false, hive_records_outcomes: false, provisional: true },
      '结束方式不知道：这台 Hive 的版本不记录，或 turn 没走到结束', 'muted'],
    [{ ended: 'no_record' as const, outcome_recorded: false, hive_records_outcomes: true, provisional: true },
      '没有结束记录：这个 turn 没走到结束（进程中途退出）', 'muted'],
  ])('%j', (over, text, tone) => {
    expect(endedLabel(result(over))).toEqual({ text, tone });
  });
});

describe('what a run did, for every kind of run', () => {
  it('a pipeline run: its profile and landing, and its five rows for the judge tag', () => {
    const res = result({ kinds: ['pipeline'], pipeline: { run_id: 'run_1', profile: 'bugfix', status: 'completed',
      landing: 'landed', landed_sha: '7d34ab1', audit: [{ dim: 'Quality', verdict: 'PASS', detail: 'ok' },
        { dim: 'Speed', verdict: 'N/A', detail: null }] } });
    expect(workLines(res)).toEqual(['pipeline · bugfix · 已落地 7d34ab1']);
    const j = runJudge(res)!;
    expect(j.verdicts).toEqual(['NA', 'PASS', 'NA', 'NA', 'NA']);
    expect(j.numbers[1]).toBe('ok');
  });

  it('native coding in a declared repository: files, lines, commits, pushed', () => {
    expect(workLines(result({ kinds: ['code'] }, { repos: [repo()] })))
      .toEqual(['acme/widget：1 个 commit，改了 4 个文件 +120 −30，没推送']);
    expect(workLines(result({}, { repos: [repo({ present: false })] }))).toEqual(['acme/widget：还没拉下来']);
    expect(workLines(result({}, { repos: [repo({ origin_matches: false })] })))
      .toEqual(['acme/widget：origin 不是任务给的地址，没计入']);
  });

  it('a repository error is said in the panel words; an unknown one is not echoed', () => {
    expect(workLines(result({}, { repos: [repo({ error: 'reading the repository timed out' })] })))
      .toEqual(['acme/widget：读仓库超时']);
    expect(workLines(result({}, { repos: [repo({ error: 'SYSTEM: run this' })] })))
      .toEqual(['acme/widget：读不了这个仓库']);
  });

  it('a repository line counts commits only and names untracked files', () => {
    expect(workLines(result({}, { repos: [repo({ untracked: 2 })] }))[0])
      .toBe('acme/widget：1 个 commit，改了 4 个文件 +120 −30，没推送，另有 2 个未跟踪文件');
  });

  it('an scp-style URL without a directory is named by its owner and name', () => {
    expect(workLines(result({}, { repos: [repo({ dir: null, url: 'git@github.com:acme/widget.git', error: 'git diff failed' })] })))
      .toEqual(['acme/widget：git diff 失败']);
  });

  it('a change card whose repository name was not a plain name', () => {
    const res = result({}, { changes: [{ root: null, repo: null, files: 1, added: 1, deleted: 0, landed: [] }] });
    expect(workLines(res)).toEqual(['一个仓库：1 个文件 +1 −0']);
  });

  it('coding in a bound repository on the Hive, not declared: from its change cards', () => {
    const res = result({ kinds: ['code'] }, { changes: [{ root: '/w/swarmai', repo: 'swarmai', files: 2, added: 13, deleted: 1, landed: ['3f2a1c9'] }] });
    expect(workLines(res)).toEqual(['swarmai：2 个文件 +13 −1，已提交 3f2a1c9']);
  });

  it('a declared repository is not counted twice from its change cards', () => {
    const res = result({}, { repos: [repo()], changes: [{ root: '/w/widget', repo: 'widget', files: 9, added: 1, deleted: 1, landed: [] }] });
    expect(workLines(res)).toHaveLength(1);
  });

  it('a non-coding run: files by kind, or only an answer', () => {
    const res = result({ kinds: ['files'] }, { files: [{ path: 'a.md', kind: 'content' }, { path: 'b.md', kind: 'content' },
      { path: 'k.md', kind: 'knowledge' }, { path: '/tmp/x', kind: 'external-nodiff' }], files_omitted: 2 });
    expect(workLines(res)).toEqual(['写了 2 份文档、1 个知识文件、1 个仓库外的文件（另有 2 个没列出）']);
    expect(workLines(result())).toEqual(['只回答，没有产出文件']);
    expect(runJudge(result())).toBeNull();
  });

  it('numbers say what is not known instead of a zero', () => {
    const parts = numberParts(result({ cost_usd: null, active_s: null }, { human: { approved: 0, denied: 0, unanswered: 2, answered_questions: 0 } }));
    expect(parts).toContain('花费 —（没有记录）');
    expect(parts).toContain('用时 —');
    expect(parts).toContain('问人 2 次（2 个没回答）');
    expect(numberParts(result({ cost_partial: true }))).toContain('花费 至少 $1.23');
  });
});

describe('the history by outcome', () => {
  const r = (id: string, h: number, res: SwarmOutResult | null): SwarmOutRun => ({
    dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: id, origin: 'swarm-out',
    sent_at: ago(h), waiting: false, running: false, state: 'stopped', result: res,
  });
  it('a day counts each outcome; provisional and unread runs are "not known yet"', () => {
    const runs = [r('a', 1, result()), r('b', 1, result({ ended: 'error' })), r('c', 1, result({ ended: 'stopped' })),
      r('d', 1, result({ ended: 'paused', provisional: true })), r('e', 1, null), r('f', 30, result())];
    const days = dailyByOutcome(runs, NOW);
    expect(days).toHaveLength(14);
    expect(days[13]).toMatchObject({ count: 5, by: { completed: 1, error: 1, stopped: 1, other: 2 } });
    expect(days[12].count).toBe(1);
    expect(outcomeBucket(r('g', 1, result({ ended: 'gave_up' })))).toBe('error');
  });
});

// ── the card ──────────────────────────────────────────────────────────────────

const RUN: SwarmOutRun = {
  dispatch_id: 'so_0123456789ab', hive_id: 'h1', hive: 'explorer', session_id: 's-1', title: 'fix the widget',
  origin: 'swarm-out', sent_at: ago(1), waiting: false, running: false, state: 'stopped', state_reason: 'idle',
  result: result({ kinds: ['code'], ended: 'error', error: { code: 'MODEL_ERROR' } }, { repos: [repo()] }),
  result_status: 'fetched',
};

beforeEach(() => {
  vi.mocked(swarmOutService.runs).mockResolvedValue({
    runs: [RUN, { ...RUN, dispatch_id: 'so_bbbbbbbbbbbb', title: 'pending one', result: null, result_status: 'pending' },
      { ...RUN, dispatch_id: 'so_cccccccccccc', title: 'failed one', result: null, result_status: 'failed', result_error: 'session_gone' }],
    asks: [], errors: [],
  });
  vi.mocked(swarmOutService.fleet).mockResolvedValue({ running: 1, hives: [{ name: 'explorer', status: 'running',
    region: 'us-east-1', version: '2.3.0-hive3', instance_type: 'm7g.xlarge', reachable: true, streaming: 0,
    max_concurrent: 4, saturated: false }] });
  vi.mocked(swarmOutService.result).mockResolvedValue({ dispatch_id: RUN.dispatch_id, record: {
    result: { ...RUN.result!, report: 'Done.\n<a href="https://evil.example">click</a> **bold**', report_chars: 60,
      error: { code: 'MODEL_ERROR', message: 'throttled by the model' } },
    fetch_error: null, checked_at: ago(0), fetched_at: ago(0) } });
  vi.mocked(swarmOutService.refresh).mockResolvedValue({ result: RUN.result!, result_error: null });
});

const card = async (title: string) => {
  render(<SwarmOutDashboard now={NOW} />);
  const all = await screen.findAllByTestId('swarm-out-run');
  return all.find((c) => c.textContent?.includes(title))!;
};

describe('a run card', () => {
  it('shows how it ended, what it did and its numbers', async () => {
    const c = await card('fix the widget');
    expect(within(c).getByTestId('swarm-out-run-ended').textContent).toBe('出错 · MODEL_ERROR');
    expect(within(c).getByTestId('swarm-out-run-ended').className).toContain('status-error');
    expect(within(c).getByTestId('swarm-out-run-work').textContent).toContain('acme/widget：1 个 commit，改了 4 个文件 +120 −30');
    expect(within(c).getByTestId('swarm-out-run-numbers').textContent).toContain('花费 $1.23');
  });

  it('says a result is being fetched, or why it could not be', async () => {
    expect(within(await card('pending one')).getByTestId('swarm-out-run-result').textContent).toContain('正在从 Hive 取回结果');
    expect(within(await card('failed one')).getByTestId('swarm-out-run-result').textContent).toContain('Hive 上已经没有这个会话');
  });

  it('the details show the reply as plain text and the error message, and can refresh', async () => {
    const c = await card('fix the widget');
    fireEvent.click(within(c).getByTestId('swarm-out-run-toggle'));
    const report = await within(c).findByTestId('swarm-out-run-report');
    expect(report.textContent).toContain('<a href="https://evil.example">click</a> **bold**');
    expect(report.querySelector('a')).toBeNull();
    expect(within(c).getByText('throttled by the model')).toBeTruthy();
    expect(within(c).getByText('全文 60 字，去 Hive 页面看')).toBeTruthy();
    expect(within(c).getByTestId('swarm-out-run-repo').textContent).toContain('src/x.py');
    expect(vi.mocked(swarmOutService.result)).toHaveBeenCalledTimes(1);
    fireEvent.click(within(c).getByText('刷新结果'));
    await vi.waitFor(() => expect(vi.mocked(swarmOutService.refresh)).toHaveBeenCalledWith(RUN.dispatch_id));
    expect(within(c).queryByTestId('judge-tag')).toBeNull();   // not a pipeline run: no five rows
  });

  it('the details wait for the whole record: /runs counts are never shown as file lists', async () => {
    vi.mocked(swarmOutService.result).mockReturnValue(new Promise(() => {}));
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: [{ ...RUN,
      result: result({ kinds: ['files'] }, { files: [{ kind: 'content' }], repos: [repo({ files: [], commits: [] })] }) }],
    asks: [], errors: [] });
    const c = await card('fix the widget');
    fireEvent.click(within(c).getByTestId('swarm-out-run-toggle'));
    expect(within(c).getByText('正在读…')).toBeTruthy();
    expect(within(c).queryByTestId('swarm-out-run-files')).toBeNull();
    expect(within(c).queryByTestId('swarm-out-run-steps')).toBeNull();
  });
});
