/**
 * The Swarm Out history view (design Knowledge/Mockups/2026-11-23-swarm-out-dashboard-
 * redesign-v6.html, the History view) and its numbers (./historyStats).
 *
 * Questions, one per invariant: (1) is a run "accepted with no one stepping in" only when
 * it was accepted and nobody stepped in, a discarded one never; (2) does one time range,
 * 7 days by default, drive every number (verdict, split, bars, groups, records); (3) past
 * a month are the bars weeks; (4) is the verdict next to the same length of time before;
 * (5) does a group row filter the records, and its ✕ undo it; (6) does a record open the
 * run's big card, J walking the list; (7) does a group by "where it ran" name EC2 or
 * AgentCore. The older pure helpers of ./live keep their own cases below.
 */
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { HistoryView } from './HistoryView';
import { bars, groups, inRange, matches, outcomeOf, previousRange, summarize, unattended } from './historyStats';
import { breakdown, dimensionCounts, inWindow, matchesFilter, runType, spendText, tallyRuns } from './live';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), result: vi.fn(), refresh: vi.fn(), answer: vi.fn(), hives: vi.fn() },
}));

const NOW = new Date(2026, 9, 29, 22, 0).getTime();
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();
const human = (o: Partial<SwarmOutResult['work']['human']> = {}) => ({ approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0, ...o });
const result = (o: Partial<SwarmOutResult> = {}, h = human()): SwarmOutResult => ({
  ended: 'completed', provisional: false, outcome_recorded: true, hive_records_outcomes: true, error: null,
  ended_at: ago(0.5), turns: 1, older_turns_not_read: false, active_s: 60,
  tokens: { input: 10, output: 90, cache_read: 900, cache_write: 0 }, cost_usd: 1, cost_partial: false,
  report_chars: 0, kinds: ['answer'], pipeline: null, dropped: 0, ...o,
  work: { repos: null, changes: [], files: [], files_omitted: 0, steps: { total: 1, failed: 0, agents: 0, by_tool: {} }, human: h },
});
const run = (id: string, res: SwarmOutResult | null, o: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: `so_${id.padEnd(12, '0')}`, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: `task ${id}`,
  origin: 'swarm-out', sent_at: ago(1), state: 'stopped', state_reason: 'idle',
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
  run('f', null, { state: 'unknown' }),
  run('g', null, { state: 'running' }),
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

  it('(2) done unattended means nobody stepped in', () => {
    const t = tallyRuns(inWindow(RUNS, NOW, 7));
    // a's question went unanswered: the run waited, nobody stepped in (design
    // 2026-11-01-brain-eval-and-runs §3), so a, b and c are done unattended
    expect(t.unattended).toBe(3);
    expect(t.unattended).toBeLessThanOrEqual(t.by.completed);
    expect([t.loops, t.unanswered]).toEqual([0, 1]);
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
    expect([ids('error'), ids('stopped'), ids('human'), ids('unknown')]).toEqual([['e'], ['d'], [], ['f', 'g']]);
  });

  it('stepping in is an answer or a message; approving or refusing a command is not', () => {
    const one = (h: Partial<SwarmOutResult['work']['human']>) => matchesFilter(run('x', result({}, human(h))), 'human');
    expect([one({ approved: 2, denied: 1 }), one({ unanswered: 1 }), one({ answered_questions: 1 }), one({ said: 1 })])
      .toEqual([false, false, true, true]);
  });
});

// the view's runs: each with the stage the backend gives it
const at = (d: number, h = 2) => new Date(NOW - d * 86400_000 - h * 3600_000).toISOString();
const V: SwarmOutRun[] = [
  run('va', result({ active_s: 600, cost_usd: 2 }), { stage: 'accepted', sent_at: at(0), project_agent: GENERAL }),
  run('vb', result({ active_s: 1200, cost_usd: 4 }), { stage: 'accepted', sent_at: at(1), person_decisions: 2 }),
  run('vc', result({ active_s: 300, cost_usd: 1 }), { stage: 'closed', why: 'discarded', sent_at: at(2) }),
  run('vd', result({ ended: 'stopped', active_s: 60 }), { stage: 'stopped', sent_at: at(3) }),
  run('ve', null, { stage: 'running', state: 'running', sent_at: at(0, 1) }),
  run('vf', result({ active_s: 900 }), { stage: 'accepted', sent_at: at(10), rounds: [{ carrier: 'agentcore' }], hive: 'elastic' }),
  run('vg', result({ active_s: 900 }), { stage: 'accepted', sent_at: at(12) }),
  run('vh', result({ active_s: 900 }), { stage: 'accepted', sent_at: at(70) }),
];

describe('the numbers (historyStats)', () => {
  it('(1) accepted with no one stepping in: a discarded or stepped-into run never counts', () => {
    expect(V.map(outcomeOf)).toEqual(['ok', 'ok', 'off', 'warn', 'open', 'ok', 'ok', 'ok']);
    expect(V.map(unattended)).toEqual([true, false, false, false, false, true, true, true]);
    const s = summarize(V.slice(0, 5));
    expect([s.n, s.unattended, s.by]).toEqual([5, 1, { ok: 2, off: 1, warn: 1, open: 1 }]);
    expect(s.medianMin).toBe(8);              // the ended runs only: 1, 5, 10, 20 minutes (7.5)
    expect([s.cost, s.costMissing]).toEqual([8, 1]);    // vd recorded no cost; ve has no result
  });

  it('(3) a range past a month is drawn by week, the last week ending today', () => {
    expect(bars(V, NOW, 7).bars).toHaveLength(7);
    expect(bars(V, NOW, 7).weekly).toBe(false);
    const w = bars(V, NOW, 90);
    expect([w.weekly, w.bars.length]).toEqual([true, 13]);
    expect(w.bars.reduce((n, b) => n + b.by.ok + b.by.off + b.by.warn + b.by.open, 0)).toBe(8);
  });

  it('(2) "all" counts the runs the bars count: a start that cannot be read or lies ahead is left out', () => {
    const odd = [...V, run('vx', null, { sent_at: 'not a time' }), run('vy', null, { sent_at: new Date(NOW + 3600_000).toISOString() })];
    expect(inRange(odd, NOW, null).map((r) => r.title)).toEqual(V.map((r) => r.title));
  });

  it('(4) the time before is the same length, just before the range', () => {
    expect(previousRange(V, NOW, 7).map((r) => r.title)).toEqual(['task vf', 'task vg']);
    expect(previousRange(V, NOW, null)).toEqual([]);
  });

  it('(7) grouped by where it ran: EC2 or AgentCore and the name', () => {
    expect(groups(V, 'where', null).map((g) => [g.key, g.sum.n])).toEqual([['EC2 · explorer', 7], ['AgentCore · Elastic Workers', 1]]);
    expect(V.filter((r) => matches(r, 'human')).map((r) => r.title)).toEqual(['task vb']);
    expect(V.filter((r) => matches(r, 'off')).map((r) => r.title)).toEqual(['task vc']);
  });
});

describe('the view', () => {
  beforeEach(() => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: V, asks: [], errors: [] });
    vi.mocked(swarmOutService.result).mockResolvedValue({ dispatch_id: V[0].dispatch_id, record: { result: { ...V[0].result!, report: 'the reply' },
      fetch_error: null, checked_at: ago(0), fetched_at: ago(0) } });
  });

  it('(2) 7 days by default; the range drives the verdict, the split, the bars, the groups and the records', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    const v = await screen.findByTestId('history-verdict');
    expect(v.getAttribute('data-value')).toBe('1');
    expect(v.textContent).toContain('/ 5');
    expect(screen.getByTestId('history-window').querySelector('[aria-pressed="true"]')!.textContent).toBe('7 天');
    expect(screen.getByTestId('history-range-text').textContent).toContain('5 个任务');
    expect(screen.getByTestId('history-count-off').textContent).toContain('1');
    expect(screen.getAllByTestId('history-bar')).toHaveLength(7);
    expect(screen.getAllByTestId('so-done-row')).toHaveLength(5);
    expect(screen.getAllByTestId('history-group').map((g) => g.dataset.group)).toEqual(['无', '通用']);
    fireEvent.click(within(screen.getByTestId('history-window')).getByText('30 天'));
    expect(screen.getByTestId('history-verdict').getAttribute('data-value')).toBe('3');
    expect(screen.getByTestId('history-verdict').textContent).toContain('/ 7');
    expect(screen.getAllByTestId('history-bar')).toHaveLength(30);
    expect(screen.getAllByTestId('so-done-row')).toHaveLength(7);
    fireEvent.click(within(screen.getByTestId('history-window')).getByText('90 天'));
    expect(screen.getByTestId('history-bars').dataset.weekly).toBe('true');
    expect(screen.getAllByTestId('so-done-row')).toHaveLength(8);
  });

  it('(4) the verdict sits next to the same length of time before it', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    expect((await screen.findByTestId('history-prev')).textContent).toBe('前 7 天 2 / 2');
  });

  it('counts the tasks refused at intake, and how many needed a private network', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: V, asks: [], errors: [], refusals: { total: 4, private: 3 } });
    render(<HistoryView hidden={false} now={NOW} />);
    expect((await screen.findByTestId('history-refusals')).textContent).toBe('拒收 4 次 · 其中需要私有网络的 3 次');
  });

  it('(5) a group row filters the records, the filters on top; its ✕ undoes it', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    fireEvent.click(screen.getAllByTestId('history-group').find((g) => g.dataset.group === '通用')!);
    expect(screen.getAllByTestId('so-done-row')).toHaveLength(1);
    expect(screen.getByTestId('history-group-chip').textContent).toBe('通用 ✕');
    fireEvent.click(screen.getByTestId('history-group-chip'));
    fireEvent.click(screen.getAllByTestId('history-filter').find((c) => c.dataset.filter === 'human')!);
    expect(screen.getAllByTestId('so-done-row').map((r) => r.dataset.title)).toEqual(['task vb']);
    expect(screen.getByTestId('history-record-count').textContent).toBe('1');
  });

  it('(6) a record opens the run\'s big card; J walks the records', async () => {
    render(<HistoryView hidden={false} now={NOW} />);
    await screen.findByTestId('history-verdict');
    const rows = screen.getAllByTestId('so-done-row');
    fireEvent.click(rows[0]);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    expect((await within(overlay).findByTestId('swarm-out-review-report')).textContent).toContain('the reply');
    expect(within(overlay).getByTestId('swarm-out-review-position').textContent).toBe(`记录 1 / ${rows.length}`);
    fireEvent.keyDown(window, { key: 'j' });
    expect(within(overlay).getByRole('dialog').getAttribute('aria-label')).toContain(rows[1].dataset.title!);
    fireEvent.click(within(overlay).getByTestId('swarm-out-review-close'));
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
  });
});
