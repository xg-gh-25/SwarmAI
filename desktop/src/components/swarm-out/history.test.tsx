/**
 * The Swarm Out history view (design docs/design/2026-10-29-swarm-out-history.md §8).
 *
 * Questions, one per invariant of the design: (1) do the outcomes add up to the runs,
 * with every rate over the runs whose ending is known, written out; (2) is a run done
 * unattended only when no person stepped in at all; (3) is a spend "at least" whenever
 * a run's cost or tokens are missing; (4) does a run get exactly one type, a pipeline
 * before files; (5) does a clicked table row filter the records, the outcome chips on
 * top of it, and its ✕ undo it; (6) does a record open into the run card itself;
 * (7) are the stale TBDs gone (and the run view's by-Brain list); (8) does the window
 * change the bars and the numbers.
 */
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { HistoryView } from './HistoryView';
import { breakdown, dimensionCounts, inWindow, matchesFilter, runType, spendText, tallyRuns } from './live';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), result: vi.fn(), refresh: vi.fn(), answer: vi.fn(), hives: vi.fn() },
}));

const NOW = new Date(2026, 9, 29, 22, 0).getTime();
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();
const human = (o: Partial<SwarmOutResult['work']['human']> = {}) => ({ approved: 0, denied: 0, unanswered: 0, answered_questions: 0, ...o });
const result = (o: Partial<SwarmOutResult> = {}, h = human()): SwarmOutResult => ({
  ended: 'completed', provisional: false, outcome_recorded: true, hive_records_outcomes: true, error: null,
  ended_at: ago(0.5), turns: 1, older_turns_not_read: false, active_s: 60,
  tokens: { input: 10, output: 90, cache_read: 900, cache_write: 0 }, cost_usd: 1, cost_partial: false,
  report_chars: 0, kinds: ['answer'], pipeline: null, dropped: 0, ...o,
  work: { repos: null, changes: [], files: [], files_omitted: 0, steps: { total: 1, failed: 0, agents: 0, by_tool: {} }, human: h },
});
const run = (id: string, res: SwarmOutResult | null, o: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: `so_${id.padEnd(12, '0')}`, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: `task ${id}`,
  origin: 'swarm-out', sent_at: ago(1), waiting: false, running: false, state: 'stopped', state_reason: 'idle',
  result: res, result_status: res ? 'fetched' : 'pending', ...o,
});
const audit = [{ dim: 'Run Isolation', verdict: 'PASS', detail: null }, { dim: 'Quality', verdict: 'WARN', detail: null }];
const PIPE = { run_id: null, profile: 'research', status: 'completed', landing: 'no_code', landed_sha: null, audit };
const GENERAL = { brain: 'General', mode: 'research', revision: 'r', profile: 'research', delivery: 'bundle', acceptance: 'x' };

const RUNS: SwarmOutRun[] = [
  run('a', result({ kinds: ['pipeline', 'files'], pipeline: PIPE, active_s: 300, cost_usd: 3.7 }, human({ unanswered: 1 })), { project_agent: GENERAL, hive: 'demo' }),
  run('b', result({ active_s: 26 })),
  run('c', result({ active_s: 10, kinds: ['code'] })),
  run('d', result({ ended: 'stopped', kinds: [], active_s: 84 })),
  run('e', result({ ended: 'error', kinds: ['files'], cost_usd: null, tokens: null })),
  run('f', null, { state: 'unknown', running: null }),
  run('g', null, { state: 'running', running: true }),
  run('h', result({ active_s: 6, tokens: { input: 1, output: 2, cache_read: null, cache_write: null } }), { sent_at: ago(24 * 20) }),
];

describe('the numbers', () => {
  it('(1) the outcomes add up to the runs; rates are over the known endings', () => {
    const t = tallyRuns(inWindow(RUNS, NOW, 7));
    expect(t.runs).toBe(7);
    expect(Object.values(t.by).reduce((a, b) => a + b, 0)).toBe(t.runs);
    expect(t.by).toEqual({ completed: 3, error: 1, stopped: 1, other: 1, running: 1 });
    expect(t.known).toBe(5);
  });

  it('(2) done unattended means no human loop at all', () => {
    const t = tallyRuns(inWindow(RUNS, NOW, 7));
    expect(t.unattended).toBe(2);                     // a needed a person: done, not unattended
    expect(t.unattended).toBeLessThanOrEqual(t.by.completed);
    expect([t.loops, t.unanswered]).toEqual([1, 1]);
    expect(t.medianActive).toBe(60);                  // the ended runs only: 10, 26, 60, 84, 300
  });

  it('(3) a spend is "at least" whenever a cost or the tokens are missing', () => {
    const all = tallyRuns(inWindow(RUNS, NOW, 7));
    expect(spendText(all)).toBe('≥ 4K token · ≥ $6.70');   // e, f, g have no cost or tokens
    expect(spendText(tallyRuns([RUNS[1], RUNS[2]]))).toBe('2K token · $2.00');
    expect(spendText(tallyRuns([RUNS[1], RUNS[4]]))).toBe('≥ 1K token · ≥ $1.00');   // e ended with no cost recorded
    expect(spendText(tallyRuns([RUNS[5]]))).toBe('— token · —');
    expect(spendText(tallyRuns([RUNS[7]]))).toBe('— token · $1.00');   // a record from before the cache was counted
  });

  it('(4) one type per run, a pipeline before files', () => {
    expect(RUNS.map((r) => runType(r).label)).toEqual(['pipeline · research', '只回答', '改代码', '没有产出', '写文件', '未知', '未知', '只回答']);
  });

  it('the breakdown and the five dimensions', () => {
    const w = inWindow(RUNS, NOW, 7);
    expect(breakdown(w, 'brain').map((g) => [g.label, g.tally.runs])).toEqual([['通用', 1], ['没走 Project Agent', 6]]);
    expect(breakdown(w, 'hive').map((g) => [g.key, g.tally.runs])).toEqual([['explorer', 6], ['demo', 1]]);
    expect(breakdown(w, 'type').at(-1)!.key).toBe('unknown');
    expect(breakdown(w, 'brain')[0].tally.judge).toEqual({ PASS: 0, WARN: 1, BLOCK: 0 });
    const dims = dimensionCounts(w)!;
    expect(dims.find((d) => d.dim === 'Quality')!.counts.WARN).toBe(1);
    expect(dims.find((d) => d.dim === 'Speed')!.counts.NA).toBe(1);
    expect(dimensionCounts([RUNS[1]])).toBeNull();
  });

  it('the record filters', () => {
    const w = inWindow(RUNS, NOW, 7);
    const ids = (f: Parameters<typeof matchesFilter>[1]) => w.filter((r) => matchesFilter(r, f)).map((r) => r.title.slice(5));
    expect([ids('error'), ids('stopped'), ids('human'), ids('unknown')]).toEqual([['e'], ['d'], ['a'], ['f', 'g']]);
  });
});

describe('the view', () => {
  beforeEach(() => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: RUNS, asks: [], errors: [] });
    vi.mocked(swarmOutService.result).mockResolvedValue({ dispatch_id: RUNS[0].dispatch_id, record: { result: { ...RUNS[0].result!, report: 'the reply' },
      fetch_error: null, checked_at: ago(0), fetched_at: ago(0) } });
  });

  it('the verdict names its denominator; one number stands out', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    const v = await screen.findByTestId('history-verdict');
    expect(v.textContent).toBe('2 个无人值守做完 · 共 7 个 run：3 个做完、1 个失败、1 个停下、1 个结束情况未知、1 个在跑');
    expect(screen.getByTestId('history-sum-unattended').textContent).toBe('2/5无人值守做完（已知结局 5 个，40%）');
  });

  it('counts the tasks refused at intake, and how many needed a private network', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: RUNS, asks: [], errors: [], refusals: { total: 4, private: 3 } });
    render(<HistoryView hidden={false} now={NOW} />);
    expect((await screen.findByTestId('history-refusals')).textContent).toBe('拒收 4 次 · 其中需要私有网络的 3 次');
  });

  it('(5) a table row filters the records, the chips on top; its ✕ undoes it', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    expect(screen.getAllByTestId('history-run')).toHaveLength(7);
    const noPa = screen.getAllByTestId('history-group').find((g) => g.dataset.group === '')!;
    fireEvent.click(noPa);
    expect(screen.getAllByTestId('history-run')).toHaveLength(6);
    expect(screen.getByTestId('history-group-chip').textContent).toBe('Brain: 没走 Project Agent ✕');
    fireEvent.click(screen.getAllByTestId('history-filter').find((c) => c.dataset.filter === 'unknown')!);
    expect(screen.getAllByTestId('history-run').map((r) => r.textContent?.match(/task (\w)/)?.[1])).toEqual(['f', 'g']);
    fireEvent.click(screen.getByTestId('history-group-chip'));
    expect(screen.queryByTestId('history-group-chip')).toBeNull();
    fireEvent.click(screen.getAllByTestId('history-filter').find((c) => c.dataset.filter === 'all')!);
    expect(screen.getAllByTestId('history-run')).toHaveLength(7);
  });

  it('(6) a record opens into the run card itself', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    const row = screen.getAllByTestId('history-run').find((r) => r.textContent?.includes('task a'))!;
    fireEvent.click(within(row).getByRole('button', { expanded: false }));
    const cardEl = within(row).getByTestId('swarm-out-run');
    expect(within(cardEl).getByTestId('judge-tag').textContent).toContain('! 1 WARN');
    fireEvent.click(within(cardEl).getByTestId('swarm-out-run-toggle'));
    expect((await within(cardEl).findByTestId('swarm-out-run-report')).textContent).toContain('the reply');
  });

  it('(7) no stale TBD: only occupancy has no source', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    expect(screen.getAllByTestId(/^swarm-out-tbd/).map((e) => e.dataset.testid)).toEqual(['swarm-out-tbd-occupancy']);
    expect(document.body.textContent).not.toMatch(/派发记录里没有|跨 run 的汇总还没做|不带任务类型/);
  });

  it('(8) the window changes the bars and the numbers', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    expect(screen.getAllByTestId('swarm-out-day')).toHaveLength(7);
    fireEvent.click(within(screen.getByTestId('history-window')).getByText('近 30 天'));
    expect(screen.getAllByTestId('swarm-out-day')).toHaveLength(30);
    expect(screen.getByTestId('history-verdict').textContent).toContain('共 8 个 run');
    expect(screen.getAllByTestId('history-run')).toHaveLength(8);
  });
});
