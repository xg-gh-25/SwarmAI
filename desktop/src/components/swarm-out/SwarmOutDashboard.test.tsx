/**
 * Swarm Out panel on real data (design Knowledge/Mockups/2026-11-23-swarm-out-dashboard-
 * redesign-v6.html).
 *
 * Questions: without an AWS account, does the setup guide replace the run view and
 * connect the account right there (never sending the person to the App's Settings); is
 * the run view two parts only, running then recently done (72 hours), with a run that
 * needs the person marked and answered on its own card; does every card name its Brain ·
 * Project Agent, where it ran (EC2 or AgentCore) and its start time with its duration;
 * does the header say what each place reports; is no TBD or sample left anywhere; does
 * the history count the range and list its runs (history.test.tsx goes further); does the
 * Settings view get the narrow layout on a narrow panel, like the other views?
 */
// Local-day bucketing is only tested if local time is not UTC.
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';
import { _resetSharedPolls } from './runsPoll';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutFleet, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { sections } from './runReview';
import { activityOf, waitingUnasked } from './useSwarmOutActivity';
import i18n from '../../i18n';

// These cases check the Chinese copy (zh.json); swarmOutI18n.test.tsx checks English.
beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), answer: vi.fn(), hives: vi.fn() },
}));
const mockAccount = vi.fn();
vi.mock('../awsAccount/ConnectAccount', () => ({ default: () => <div data-testid="connect-account-flow" /> }));
vi.mock('../../services/awsAccount', () => ({
  awsAccountService: { get: (...a: unknown[]) => mockAccount(...a), elasticStatus: () => new Promise(() => {}) },
  problemOf: (e: unknown) => ({ kind: 'error', message: String(e) }),
}));

const NOW = new Date(2026, 9, 28, 12, 0).getTime();   // local time
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();

const run = (id: string, state: SwarmOutRun['state'], over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: `task ${id}`,
  origin: 'swarm-out', sent_at: ago(1), state, state_reason: state === 'running' ? 'streaming' : 'idle', ...over,
});

const hive = (name: string, over: Partial<SwarmOutFleet['hives'][number]> = {}) => ({
  name, status: 'running', region: 'us-east-1', version: '2.3.0-hive2', instance_type: 'm7g.xlarge',
  reachable: true, streaming: 1, saturated: false, memory_percent: 60, ...over,
});

const FLEET: SwarmOutFleet = {
  running: 3,
  hives: [hive('explorer'), hive('demo', { streaming: 4, saturated: true, memory_percent: 93 }), hive('flaky', { reachable: false, streaming: null, saturated: null, memory_percent: null }),
    hive('old', { status: 'stopped', reachable: null, streaming: null, saturated: null, memory_percent: null })],
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
  _resetSharedPolls();   // the shared poll keeps its last answer between readers
  vi.mocked(swarmOutService.runs).mockResolvedValue(RUNS);
  vi.mocked(swarmOutService.fleet).mockResolvedValue(FLEET);
  mockAccount.mockReset();
  mockAccount.mockRejectedValue(new Error('no account page here'));
});

describe('the run sections and the tab badge', () => {
  it('a run waiting with no question mirrored counts as waiting and is listed; a refused one is not', () => {
    const d: SwarmOutRuns = { runs: [run('w1', 'waiting'), run('w2', 'waiting')], asks: [ask('w2', true)], errors: [] };
    expect(waitingUnasked(d).map((r) => r.dispatch_id)).toEqual(['w1']);
    expect(activityOf(d).waiting).toBe(1);
    expect(sections(d.runs, d.asks, NOW).you.map((r) => r.dispatch_id).sort()).toEqual(['w1', 'w2']);   // both rows wait on you
  });

  it('a run with no stage (an older dispatch) is grouped by its state alone, as the badge counts it', () => {
    // the row carries no running / waiting booleans any more: `state` is the one field read
    const old = run('o', 'running');
    expect('running' in old || 'waiting' in old).toBe(false);
    expect(sections([old], [], NOW).running).toHaveLength(1);
    expect(activityOf({ runs: [old], asks: [], errors: [] }).running).toBe(1);
    // an unknown run is neither running nor waiting, in the list and in the badge
    const unk = run('u', 'unknown', { state_reason: 'hive_updating' });
    expect(sections([unk], [], NOW).running).toHaveLength(0);
    expect(activityOf({ runs: [unk], asks: [], errors: [] })).toEqual({ running: 0, waiting: 0 });
  });
});

describe('the run view', () => {
  it('two parts only: what runs (what needs you first, marked on its own card), then what finished', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    const cards = await screen.findAllByTestId('so-run-card');
    expect(cards.map((c) => `${c.getAttribute('data-kind')}:${c.getAttribute('data-id')}`)).toEqual(['you:b', 'run:a']);
    expect(within(cards[0]).getByTestId('so-you-pill').textContent).toBe('回答问题');
    // b's question is answered on b's own card; there is no separate "waiting for you" list
    expect(within(cards[0]).getByTestId('so-card-asks').querySelectorAll('[data-testid="swarm-out-ask"]')).toHaveLength(1);
    expect(screen.queryByTestId('swarm-out-waiting')).toBeNull();
    expect(screen.getByTestId('so-you-count').textContent).toBe('1 个等你');
    // the refused one is answered on its Hive's own page
    expect(within(screen.getByTestId('so-on-hive-page')).getAllByTestId('swarm-out-ask')).toHaveLength(1);
    const board = screen.getByTestId('so-run-board');
    expect([...board.querySelectorAll(':scope > section')].map((e) => e.getAttribute('data-testid'))).toEqual(['so-running', 'so-recent']);
  });

  it('a card that waits on you offers no Stop; a run not settled is a card even with nothing to do here', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: [
      run('y', 'waiting', { stage: 'needs_answer', actions: [{ key: 'stop', primary: false }, { key: 'open_session', primary: false }] }),
      run('w', 'stopped', { stage: 'awaiting', actions: [{ key: 'open_session', primary: false }] }),
    ], asks: [ask('y')], errors: [] });
    render(<SwarmOutDashboard now={NOW} />);
    const cards = await screen.findAllByTestId('so-run-card');
    expect(cards.map((c) => c.getAttribute('data-id')).sort()).toEqual(['w', 'y']);
    const y = cards.find((c) => c.getAttribute('data-id') === 'y')!;
    expect(within(y).queryByTestId('so-stop')).toBeNull();
    expect(screen.queryAllByTestId('so-done-row')).toHaveLength(0);
  });

  it('recently done is the last 72 hours, the latest first', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ ...RUNS, runs: [...RUNS.runs, run('g', 'stopped', { sent_at: ago(80) })] });
    render(<SwarmOutDashboard now={NOW} />);
    const rows = await screen.findAllByTestId('so-done-row');
    expect(rows.map((r) => r.dataset.title).sort()).toEqual(['task c', 'task d', 'task e', 'task f']);   // g: 80 h ago
    expect(rows.at(-1)!.dataset.title).toBe('task d');
  });

  it('every card says Brain · Project Agent, EC2 or AgentCore with the place, and its start with its duration', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: [
      run('p', 'running', { project_agent: { brain: 'SwarmAI', mode: 'code-change', revision: 'r', profile: null, delivery: 'bundle', acceptance: 'x' },
        sent_at: new Date(2026, 9, 28, 11, 42).toISOString() }),
      run('q', 'running', { hive: 'elastic', rounds: [{ carrier: 'agentcore' }] }),
    ], asks: [], errors: [] });
    render(<SwarmOutDashboard now={NOW} />);
    const [p, q] = await screen.findAllByTestId('so-run-card');
    expect(within(p).getByTestId('so-brain').textContent).toBe('SwarmAI · code-change');
    expect(within(p).getByTestId('so-where').getAttribute('data-carrier')).toBe('ec2');
    expect(within(p).getByTestId('so-where').textContent).toBe('EC2explorer');
    expect(within(p).getByTestId('so-when').textContent).toBe('10/28 11:42·运行 18 min');   // a running card's running time
    expect(within(q).getByTestId('so-where').textContent).toBe('AgentCoreElastic Workers');
  });

  it('the header says what each place reports, on hover', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    const pills = await screen.findAllByTestId('so-fleet-pill');
    expect(pills.map((r) => r.getAttribute('title'))).toEqual(['1 个在跑 · 内存占用 60%', '内存吃紧 · 4 个在跑，新任务会排队', '连不上', '已关机 · 要用时自动启动']);
    expect(pills.map((r) => r.getAttribute('data-tone'))).toEqual(['ok', 'attention', 'error', 'unknown']);
  });

  it('Hives short of what the person asked for: one pill, and it opens Swarm Out settings', async () => {
    mockAccount.mockResolvedValue({ state: 'connected', hive: { state: 'short', short: 2, desired: 3, counted: 1 } });
    const opened: string[] = [];
    const listen = (e: Event) => opened.push(e.type);
    window.addEventListener('swarm:open-swarm-out-settings', listen);
    try {
      render(<SwarmOutDashboard now={NOW} />);
      const pill = await screen.findByTestId('so-fleet-short');
      expect(pill.textContent).toContain('少 2 台');
      fireEvent.click(pill);
      expect(opened).toEqual(['swarm:open-swarm-out-settings']);
    } finally {
      window.removeEventListener('swarm:open-swarm-out-settings', listen);
    }
  });

  it('a shortfall the person dismissed adds no pill, as in Needs You', async () => {
    mockAccount.mockResolvedValue({ state: 'connected', hive: { state: 'short', short: 1, desired: 1, counted: 0, dismissed: true } });
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('so-fleet-pill');
    await waitFor(() => expect(mockAccount).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); });            // the account read has answered
    expect(screen.queryByTestId('so-fleet-short')).toBeNull();
  });

  it('a ready fleet, or an unknown one, adds no pill', async () => {
    mockAccount.mockResolvedValue({ hive: { state: 'ready', desired: 1, counted: 1 } });
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('so-fleet-pill');
    expect(screen.queryByTestId('so-fleet-short')).toBeNull();
  });

  it('a failed fleet read says so instead of loading forever', async () => {
    vi.mocked(swarmOutService.fleet).mockRejectedValue(new Error('down'));
    render(<SwarmOutDashboard now={NOW} />);
    expect((await screen.findByTestId('so-fleet-failed')).textContent).toContain('读不到机群');
  });

  it('with no runs at all, each part says it in one line', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ runs: [], asks: [], errors: [] });
    render(<SwarmOutDashboard now={NOW} />);
    expect((await screen.findByTestId('so-running-empty')).textContent).toContain('没有在跑的任务');
    expect(screen.getByText('过去 72 小时没有做完的任务。')).toBeTruthy();
  });

  it('says in Chinese which Hive\'s questions could not be read', async () => {
    vi.mocked(swarmOutService.runs).mockResolvedValue({ ...RUNS, errors: ['could not read the questions of Hive demo'] });
    render(<SwarmOutDashboard now={NOW} />);
    expect(await screen.findByText('读不到 Hive demo 的问题：「等你」可能不全')).toBeTruthy();
  });

  it('no part says TBD, and no sample is left', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('so-run-card');
    expect(screen.queryAllByTestId(/tbd/)).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/TBD|示例|敬请期待/);
  });
});

describe('the history view', () => {
  it('counts the range and lists every run in it; no TBD', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    fireEvent.click(screen.getByRole('button', { name: '历史' }));
    await screen.findByTestId('history-range-bar');
    expect(screen.getAllByTestId('so-done-row')).toHaveLength(6);
    const days = screen.getAllByTestId('history-bar').map((d) => Number(d.getAttribute('data-count')));
    expect(days).toHaveLength(7);
    expect(days.reduce((a, b) => a + b, 0)).toBe(6);
    expect(document.body.textContent).not.toMatch(/TBD|示例|敬请期待/);
  });
});

describe('SwarmOutDashboard and the Hives it can dispatch to', () => {
  const fleetIs = (hives: ReturnType<typeof hive>[]) => {
    vi.mocked(swarmOutService.fleet).mockResolvedValue({ hives, running: hives.filter((h) => h.status === 'running').length });
  };

  it('without an AWS account: the guide replaces the run view and connects the account right there', async () => {
    fleetIs([]);
    mockAccount.mockResolvedValue({ state: 'unconnected', account: null, hive: null });
    const settings = vi.fn();
    window.addEventListener('swarm:open-settings', settings);
    try {
      render(<SwarmOutDashboard now={NOW} />);
      const guide = await screen.findByTestId('swarm-out-hive-guide');
      expect(guide.getAttribute('data-case')).toBe('no-account');
      expect(screen.queryByTestId('so-run-board')).toBeNull();
      fireEvent.click(screen.getByTestId('swarm-out-connect-account'));
      expect(within(guide).getByTestId('connect-account-flow')).toBeTruthy();
      expect(settings).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('swarm:open-settings', settings);
    }
  });

  it('with Hives but none running: the run view stays (a dispatch starts one)', async () => {
    fleetIs([hive('w1', { status: 'stopped', reachable: null })]);
    render(<SwarmOutDashboard now={NOW} />);
    expect(await screen.findByTestId('so-run-board')).toBeTruthy();
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect((await screen.findAllByTestId('so-fleet-pill')).map((p) => p.getAttribute('data-tone'))).toEqual(['unknown']);
  });

  it('before the fleet is known, shows no guide and no pills (an unreachable daemon is not "no Hive")', () => {
    vi.mocked(swarmOutService.fleet).mockReturnValue(new Promise(() => {}));
    render(<SwarmOutDashboard now={NOW} />);
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.queryByTestId('so-fleet')).toBeNull();
    expect(screen.getByTestId('so-run-board')).toBeTruthy();
  });

  it('a failed fleet lookup is not "no Hive": no guide', async () => {
    vi.mocked(swarmOutService.fleet).mockRejectedValue(new Error('daemon down'));
    render(<SwarmOutDashboard now={NOW} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('swarm-out-hive-guide')).toBeNull();
    expect(screen.getByTestId('so-run-board')).toBeTruthy();
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

  it('on a narrow panel the Settings view is narrow too, like the other views', async () => {
    const Real = globalThis.ResizeObserver;
    class Narrow {
      constructor(private cb: ResizeObserverCallback) {}
      observe() { this.cb([{ contentRect: { width: 600 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      unobserve() {}
      disconnect() {}
    }
    globalThis.ResizeObserver = Narrow as unknown as typeof ResizeObserver;
    try {
      mockAccount.mockReturnValue(new Promise(() => {}));
      render(<SwarmOutDashboard now={NOW} />);
      expect(screen.getByTestId('swarm-out-dashboard').dataset.compact).toBe('true');
      fireEvent.click(screen.getByRole('button', { name: i18n.t('swarmOut.view.settings') }));
      expect((await screen.findByTestId('swarm-out-settings')).dataset.compact).toBe('true');
    } finally {
      globalThis.ResizeObserver = Real;
    }
  });
});
