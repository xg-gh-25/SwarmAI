/**
 * Swarm Out panel on real data (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * Questions: with no running Hive, does the setup guide replace the run view (and the
 * header count only real Hives, from the fleet); are the four numbers the panel's
 * rules over /runs and /fleet (and the waiting / running ones the tab badge's own);
 * does each run sit in the group of its state; does the fleet say what each Hive
 * reports (occupied, full, unreachable, stopped); is every part without a data source
 * marked TBD with what is missing, and no sample left anywhere; does the history count
 * runs per local day and list the week's runs by Hive.
 */
// Local-day bucketing is only tested if local time is not UTC.
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutFleet, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { capacity, dailySent, panelNumbers, runGroups } from './live';
import { activityOf, waitingUnasked } from './useSwarmOutActivity';

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), answer: vi.fn(), hives: vi.fn() },
}));

const NOW = new Date(2026, 9, 28, 12, 0).getTime();   // local time
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();

const run = (id: string, state: SwarmOutRun['state'], over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: `task ${id}`,
  origin: 'swarm-out', sent_at: ago(1), state, state_reason: state === 'running' ? 'streaming' : 'idle',
  waiting: state === 'waiting', running: state === 'unknown' ? null : state === 'running', ...over,
});

const hive = (name: string, over: Partial<SwarmOutFleet['hives'][number]> = {}) => ({
  name, status: 'running', region: 'us-east-1', version: '2.3.0-hive2', instance_type: 'm7g.xlarge',
  reachable: true, streaming: 1, max_concurrent: 4, saturated: false, ...over,
});

const FLEET: SwarmOutFleet = {
  running: 3,
  hives: [hive('explorer'), hive('demo', { streaming: 4, saturated: true }), hive('flaky', { reachable: false, streaming: null, max_concurrent: null, saturated: null }),
    hive('old', { status: 'stopped', reachable: null, streaming: null, max_concurrent: null, saturated: null })],
};

const ask = (id: string, refused = false) => ({
  kind: 'approval' as const, hive: 'explorer', hive_id: 'h1', session_id: `s-${id}`, request_id: id,
  asked_at: ago(1), command: 'ls', reason: 'r', tool_name: 'Bash', truncated: false, shown_sha256: 'x',
  dispatch_title: `task ${id}`, ...(refused ? { refused: true } : {}),
});

const RUNS: SwarmOutRuns = {
  runs: [run('a', 'running'), run('b', 'waiting'), run('c', 'stopped'), run('d', 'stopped', { sent_at: ago(30) }),
    run('e', 'unknown', { state_reason: 'hive_silent' }), run('f', 'stopped', { state_reason: 'hive_stopped' })],
  asks: [ask('b'), ask('z', true)],
  errors: [],
};

beforeEach(() => {
  vi.mocked(swarmOutService.runs).mockResolvedValue(RUNS);
  vi.mocked(swarmOutService.fleet).mockResolvedValue(FLEET);
});

describe('the panel numbers', () => {
  it('free slots: over the running Hives that answered, a full one has none, never negative', () => {
    expect(capacity(FLEET)).toEqual({ free: 3, total: 8 });
    expect(capacity({ running: 1, hives: [hive('x', { streaming: 6, max_concurrent: 4, saturated: false })] })).toEqual({ free: 0, total: 4 });
    expect(capacity({ running: 1, hives: [hive('x', { reachable: false })] })).toBeNull();
  });

  it('waiting and running are the tab badge\'s counts; a refused ask is apart', () => {
    const n = panelNumbers(RUNS, FLEET);
    expect({ waiting: n.waiting, running: n.running }).toEqual(activityOf(RUNS));
    expect(n.waiting).toBe(1);          // b; the refused ask z is not waiting here
    expect(n.onHivePage).toBe(1);
    expect(n.running).toBe(1);
    expect(runGroups(RUNS.runs, NOW, RUNS.asks).stopped.map((r) => r.dispatch_id)).toEqual(['c', 'f']);   // d: 30 h ago
  });

  it('a run waiting with no question mirrored counts as waiting and is listed; a refused one is not', () => {
    const d: SwarmOutRuns = { runs: [run('w1', 'waiting'), run('w2', 'waiting')], asks: [ask('w2', true)], errors: [] };
    expect(waitingUnasked(d).map((r) => r.dispatch_id)).toEqual(['w1']);
    expect(activityOf(d).waiting).toBe(1);
    expect(runGroups(d.runs, NOW, d.asks).waitingUnasked.map((r) => r.dispatch_id)).toEqual(['w1']);
  });

  it('a run in an unknown state is in no number; shown within 24 h, or when its session was never named', () => {
    const g = runGroups([run('u1', 'unknown'), run('u2', 'unknown', { sent_at: ago(40) }),
      run('u3', 'unknown', { sent_at: ago(40), session_id: null, session_unknown: true })], NOW);
    expect(g.unknown.map((r) => r.dispatch_id)).toEqual(['u1', 'u3']);
    const n = panelNumbers({ runs: [run('u1', 'unknown')], asks: [], errors: [] }, null);
    expect([n.running, n.waiting]).toEqual([0, 0]);
  });

  it('a run from an older daemon (no state) is grouped by its booleans, as the badge counts it', () => {
    const old = run('o', undefined, { state: undefined, running: true, waiting: false });
    expect(runGroups([old], NOW).running).toHaveLength(1);
    expect(activityOf({ runs: [old], asks: [], errors: [] }).running).toBe(1);
  });

  it('runs per local day, a run just after local midnight on that day', () => {
    const justAfterMidnight = new Date(2026, 9, 28, 0, 30).toISOString();
    const lateYesterday = new Date(2026, 9, 27, 23, 30).toISOString();
    const days = dailySent([run('a', 'stopped', { sent_at: justAfterMidnight }), run('b', 'stopped', { sent_at: lateYesterday })], NOW);
    expect(days).toHaveLength(14);
    expect(days[13]).toEqual({ label: '10/28', count: 1 });
    expect(days[12]).toEqual({ label: '10/27', count: 1 });
  });
});

describe('the run view', () => {
  it('shows the numbers waiting first, the waiting question first, refused ones apart', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    expect((await screen.findByTestId('kpi-free')).textContent).toContain('3/ 8');
    const tiles = screen.getByTestId('kpi-grid').children;
    expect(tiles[0].getAttribute('data-testid')).toBe('kpi-waiting');
    expect(screen.getByTestId('kpi-waiting').textContent).toMatch(/^1\+1 个要去 Hive 页面处理/);
    expect(screen.getByTestId('kpi-running').textContent).toContain('1');
    const waiting = screen.getByTestId('swarm-out-waiting');
    expect(within(waiting).getAllByTestId('swarm-out-ask')).toHaveLength(2);
    expect(within(screen.getByTestId('swarm-out-refused-group')).getAllByTestId('swarm-out-ask')).toHaveLength(1);
  });

  it('puts each run in the group of its state', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findByTestId('kpi-free');
    const group = (title: string) => screen.getAllByTestId('swarm-out-run-group').find((g) => g.getAttribute('data-group') === title)!;
    const titles = (g: HTMLElement) => within(g).queryAllByTestId('swarm-out-run').map((c) => c.getAttribute('data-state') + ':' + c.textContent?.split(/\d/)[0].trim());
    expect(titles(group('在跑'))).toEqual(['running:task a']);
    expect(titles(group('结束了（24 小时内派出的）'))).toEqual(['stopped:task c', 'stopped:task f']);
    expect(within(group('结束了（24 小时内派出的）')).getAllByTestId('swarm-out-run-state')[1].textContent).toContain('Hive 已关机');
    expect(titles(group('状态未知'))).toEqual(['unknown:task e']);
  });

  it('says what each Hive reports', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    const rows = await screen.findAllByTestId('swarm-out-fleet-status');
    expect(rows.map((r) => r.textContent)).toEqual(['占用 1/4', '忙满 · 占用 4/4', '连不上', '已关机']);
  });

  it('a failed fleet read says so instead of loading forever', async () => {
    vi.mocked(swarmOutService.fleet).mockRejectedValue(new Error('down'));
    render(<SwarmOutDashboard now={NOW} />);
    expect((await screen.findByTestId('swarm-out-fleet-failed')).textContent).toContain('读不到机群');
    expect(screen.getByTestId('kpi-free').textContent).toContain('—');
  });

  it('with no runs at all, one line says so (not three empty groups)', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: [], asks: [], errors: [] });
    render(<SwarmOutDashboard now={NOW} />);
    expect(await screen.findByText(/还没有派发过任务/)).toBeTruthy();
    expect(screen.queryAllByTestId('swarm-out-run-group')).toHaveLength(0);
  });

  it('says in Chinese which Hive\'s questions could not be read', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ ...RUNS, errors: ['could not read the questions of Hive demo'] });
    render(<SwarmOutDashboard now={NOW} />);
    expect(await screen.findByText('读不到 Hive demo 的问题：「等你」可能不全')).toBeTruthy();
  });

  it('marks every part without a data source TBD, and no sample is left', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findByTestId('kpi-free');
    const tbd = screen.getAllByTestId('swarm-out-tbd').map((t) => t.textContent ?? '');
    expect(tbd.some((t) => t.includes('已安排') && t.includes('TBD：还没有定时派发'))).toBe(true);
    expect(tbd.some((t) => t.includes('trace'))).toBe(false);   // the Brain of each run is recorded now
    expect(screen.getByTestId('swarm-out-by-brain').textContent).toContain('没走 Project Agent');
    expect(tbd.some((t) => t.includes('结果'))).toBe(false);   // results come from the Hive now
    expect(screen.queryByTestId('swarm-out-sample-notice')).toBeNull();
    expect(document.body.textContent).not.toMatch(/示例|敬请期待/);
  });
});

describe('the history view', () => {
  it('counts runs per day, lists the week by Hive, and marks the audit parts TBD', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
    const groups = await screen.findAllByTestId('history-group');
    expect(groups.map((g) => g.getAttribute('data-group'))).toEqual(['explorer']);
    expect(within(groups[0]).getAllByTestId('history-run')).toHaveLength(6);
    const days = screen.getAllByTestId('swarm-out-day').map((d) => Number(d.getAttribute('data-count')));
    expect(days.reduce((a, b) => a + b, 0)).toBe(6);
    for (const id of ['verdict', 'dimensions', 'occupancy', 'type']) {
      expect(screen.getByTestId(`swarm-out-tbd-${id}`).textContent).toContain('TBD：');
    }
    expect(document.body.textContent).not.toMatch(/示例|敬请期待/);
  });
});

describe('SwarmOutDashboard and the Hives it can dispatch to', () => {
  const fleetIs = (hives: ReturnType<typeof hive>[]) => {
    vi.mocked(swarmOutService.fleet).mockResolvedValue({ hives, running: hives.filter((h) => h.status === 'running').length });
  };

  it('with no Hive: the guide replaces the run view and its button opens Hive', async () => {
    fleetIs([]);
    const onOpenHive = vi.fn();
    render(<SwarmOutDashboard onOpenHive={onOpenHive} now={NOW} />);
    const guide = await screen.findByTestId('swarm-out-hive-guide');
    expect(guide.getAttribute('data-case')).toBe('no-hive');
    expect(screen.queryByTestId('swarm-out-live')).toBeNull();
    expect(screen.getByTestId('swarm-out-hive-count').textContent).toBe('还没有 Hive');
    fireEvent.click(screen.getByTestId('swarm-out-open-hive'));
    expect(onOpenHive).toHaveBeenCalledTimes(1);
  });

  it('with Hives but none running: names them and says to start one', async () => {
    fleetIs([hive('w1', { status: 'stopped', reachable: null })]);
    render(<SwarmOutDashboard now={NOW} />);
    const guide = await screen.findByTestId('swarm-out-hive-guide');
    expect(guide.getAttribute('data-case')).toBe('none-running');
    expect(guide.textContent).toContain('w1（stopped）');
    expect(screen.getByTestId('swarm-out-hive-count').textContent).toBe('1 台 Hive，都没在运行');
  });

  it('before the fleet is known, shows no guide and no count (an unreachable daemon is not "no Hive")', () => {
    vi.mocked(swarmOutService.fleet).mockReturnValue(new Promise(() => {}));
    render(<SwarmOutDashboard now={NOW} />);
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.queryByTestId('swarm-out-hive-count')).toBeNull();
    expect(screen.getByTestId('swarm-out-live')).toBeTruthy();
  });

  it('a failed fleet lookup is not "no Hive": no guide', async () => {
    vi.mocked(swarmOutService.fleet).mockRejectedValue(new Error('daemon down'));
    render(<SwarmOutDashboard now={NOW} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.getByTestId('swarm-out-live')).toBeTruthy();
  });
});

describe('the runs poll', () => {
  it('an older answer arriving after a newer one does not overwrite it', async () => {
    const { renderHook, act, waitFor } = await import('@testing-library/react');
    const { useRuns } = await import('./live');
    let resolveOld: (v: SwarmOutRuns) => void = () => {};
    vi.mocked(swarmOutService.runs)
      .mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }))
      .mockResolvedValueOnce({ runs: [run('new', 'running')], asks: [], errors: [] });
    const { result } = renderHook(() => useRuns(false));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data?.runs[0].dispatch_id).toBe('new'));
    await act(async () => { resolveOld({ runs: [run('old', 'running')], asks: [], errors: [] }); });
    expect(result.current.data?.runs[0].dispatch_id).toBe('new');
  });
});
