/**
 * The runs a chat session dispatched, in its tab (design "the runs this session
 * dispatched, visible in the dispatching tab" r2 §8, invariants 1-4).
 *
 * Questions:
 *  1. does the Canvas Runs board list exactly the runs whose dispatched_from is the
 *     session and the asks of those runs (no questions of other sessions, no loose
 *     ones), and follow another session id when the tab's session changes;
 *  2. do the tab's runs mark, the board's amber card, the track's chip and the pinned
 *     badge give one answer to "does it need you" for the same run, null needs_person
 *     included (runReview.waitsOnPerson);
 *  3. does a run opened from the Canvas board open the same big card, with the same
 *     commands, as from the Swarm Out board;
 *  4. is there one /api/swarm-out/runs poll while the Canvas board, a dispatch card and
 *     the pinned badge are all mounted.
 */
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { renderHook } from '@testing-library/react';
import type { SwarmOutAction, SwarmOutAsk, SwarmOutRun, SwarmOutRuns } from '../../services/swarmOut';
import { swarmOutService } from '../../services/swarmOut';
import i18n from '../../i18n';
import { RunBoard } from './RunBoard';
import { CanvasRuns, CanvasRunsTab } from '../file-viewer/CanvasRuns';
import { DispatchCard } from '../../pages/chat/components/DispatchCard';
import { activityOf, useSwarmOutActivity } from './useSwarmOutActivity';
import { runsMarkOf } from './sessionRuns';
import { waitsOnPerson } from './runReview';
import { dispatchTrack } from './track';
import { POLL_MS, _resetSharedPolls } from './runsPoll';
import type { SwarmOutDispatchContent } from '../../types';

vi.mock('../../services/swarmOut', () => {
  const never = () => new Promise(() => {});
  const svc: Record<string, unknown> = { runs: vi.fn(), fleet: vi.fn(async () => ({ hives: [] })) };
  return {
    swarmOutService: new Proxy(svc, {
      get: (t, k: string) => (k in t ? t[k] : (t[k] = vi.fn(never))),
    }),
  };
});
const runsFn = vi.mocked(swarmOutService.runs);

beforeAll(async () => { await i18n.changeLanguage('en'); });
beforeEach(() => { runsFn.mockReset(); _resetSharedPolls(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const NOW = Date.parse('2026-11-24T06:00:00Z');
const SENT = '2026-11-24T05:00:00Z';
const ENDED = '2026-11-24T05:30:00Z';
const A = (key: SwarmOutAction['key'], primary = false, variant?: SwarmOutAction['variant']): SwarmOutAction =>
  ({ key, primary, ...(variant ? { variant } : {}) });
const ACTIONS: Record<string, SwarmOutAction[]> = {
  running: [A('stop', true), A('open_session'), A('discard')],
  needs_answer: [A('answer', false, 'hint'), A('open_session'), A('discard')],
  awaiting: [A('accept', true, 'verdict'), A('rerun'), A('open_session'), A('discard')],
  stopped: [A('rerun', true), A('open_session'), A('discard')],
  accepted: [A('open_session', false, 'read_only')],
};

function run(id: string, stage: NonNullable<SwarmOutRun['stage']>, over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `hs-${id}`, title: `task ${id}`,
    origin: 'swarm-out', sent_at: SENT,
    state: stage === 'running' ? 'running' : stage === 'needs_answer' ? 'waiting' : 'stopped', state_reason: 'idle',
    project_agent: { brain: 'SwarmAI', mode: 'code-change', revision: 'r1', profile: 'full', acceptance: 'tests', returns: 'commits' },
    result: { ended: 'completed', provisional: false, ended_at: ENDED, turns: 1,
      work: { repos: [], files: [], human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0 },
        steps: { total: 3, failed: 0, agents: 0, by_tool: {} }, changes: [], files_omitted: 0 },
      pipeline: null, checks: null, execution_check: null } as unknown as SwarmOutRun['result'],
    stage, actions: ACTIONS[stage] ?? [], why: null,
    settle: { code: 'none', why_not: ['verdict'], settled: null },
    dispatched_from: 'S1',
    ...over,
  };
}
const ask = (r: SwarmOutRun, id = 'q'): SwarmOutAsk => ({
  kind: 'question', hive: r.hive, hive_id: r.hive_id, session_id: r.session_id as string, request_id: `${r.dispatch_id}-${id}`,
  asked_at: ENDED, questions: [{ question: `Which way for ${r.title}?`, header: 'Q', multiSelect: false, options: [{ label: 'left', description: '' }] }],
} as unknown as SwarmOutAsk);
const data = (runs: SwarmOutRun[], asks: SwarmOutAsk[] = []): SwarmOutRuns => ({ runs, asks, answered: [], errors: [] });
const source = (d: SwarmOutRuns) => ({ data: d, failed: false, reload: () => {} });

describe('1. the Canvas Runs board lists exactly this session\'s runs and their asks', () => {
  const mine = run('m1', 'needs_answer', { needs_person: null, title: 'Mine waits' });
  const mine2 = run('m2', 'running', { needs_person: false, title: 'Mine runs' });
  const other = run('o1', 'running', { dispatched_from: 'S2', title: 'Other session' });
  const panel = run('p1', 'running', { dispatched_from: null, title: 'From the panel' });
  const d = data([mine, mine2, other, panel], [ask(mine), ask(other),
    // a question of no listed run: the Swarm Out board answers it at the top, never the session's
    { ...ask(other, 'x'), session_id: 'loose-session', request_id: 'loose' } as SwarmOutAsk]);

  it('shows only the session\'s runs, their asks, and no loose questions', () => {
    render(<RunBoard scope="session" sessionId="S1" source={source(d)} now={NOW} />);
    const titles = screen.getAllByTestId('so-run-title').map((b) => b.textContent);
    expect(titles.sort()).toEqual(['Mine runs', 'Mine waits']);
    expect(screen.queryByTestId('so-loose')).toBeNull();
    expect(screen.getByText('Which way for Mine waits?')).toBeTruthy();
    expect(screen.queryByText('Which way for Other session?')).toBeNull();
    expect(screen.getByTestId('so-session-verdict').dataset.you).toBe('1');
    expect(screen.getByTestId('so-session-footer')).toBeTruthy();
  });

  it('the verdict says Swarm follows only the runs its agent answers for', () => {
    const orphan = run('n1', 'running', { needs_person: null, title: 'No live session' });
    const followed = run('f1', 'running', { needs_person: false, title: 'Followed' });
    render(<RunBoard scope="session" sessionId="S1" source={source(data([orphan, followed]))} now={NOW} />);
    const v = screen.getByTestId('so-session-verdict');
    expect([v.dataset.you, v.dataset.follows, v.dataset.others]).toEqual(['0', '1', '1']);
    expect(v.textContent).toBe('1 running, Swarm follows them · when they finish it reviews, lands and judges them, nothing needed from you · 1 running');
    // the card of the run nobody answers for has no "Swarm follows it" pill
    const cards = screen.getAllByTestId('so-run-card');
    const pill = (title: string) => !!within(cards.find((c) => c.textContent?.includes(title))!).queryByTestId('so-follow-pill');
    expect([pill('No live session'), pill('Followed')]).toEqual([false, true]);
  });

  it('follows the session id when the tab changes session', () => {
    const { rerender } = render(<RunBoard scope="session" sessionId="S1" source={source(d)} now={NOW} />);
    rerender(<RunBoard scope="session" sessionId="S2" source={source(d)} now={NOW} />);
    expect(screen.getAllByTestId('so-run-title').map((b) => b.textContent)).toEqual(['Other session']);
    rerender(<RunBoard scope="session" sessionId={null} source={source(d)} now={NOW} />);
    expect(screen.queryAllByTestId('so-run-title')).toEqual([]);
  });

  it('the Swarm Out board still lists every run and the loose question', () => {
    render(<RunBoard source={source(d)} now={NOW} />);
    expect(screen.getAllByTestId('so-run-title')).toHaveLength(4);
    expect(screen.getByTestId('so-loose')).toBeTruthy();
    expect(screen.queryByTestId('so-session-verdict')).toBeNull();
  });

  it('a session with no listed run has no mark (and so no Runs tab)', () => {
    expect(runsMarkOf(d, 'S9', NOW)).toBeNull();
    expect(runsMarkOf(d, null, NOW)).toBeNull();
    expect(runsMarkOf(d, 'S1', NOW)).toEqual({ runs: 2, live: 2, waits: true });
  });
});

describe('2. one waitsOnPerson answer everywhere', () => {
  // each case alone in the session: the mark, the card, the track and the badge agree
  const cases: [string, SwarmOutRun, SwarmOutAsk[]][] = [
    ['agent answers, awaiting', run('c1', 'awaiting', { needs_person: false }), []],
    ['handed to the person', run('c2', 'awaiting', { needs_person: true }), []],
    ['no live session, accept to give', run('c3', 'awaiting', { needs_person: null }), []],
    ['no live session, only open', run('c4', 'awaiting', { needs_person: null, actions: [A('open_session')] }), []],
    ['no live session, stopped with re-run', run('c5', 'stopped', { needs_person: null }), []],
    ['no live session, a question', run('c6', 'needs_answer', { needs_person: null }), []],
    ['agent answers its own question', run('c7', 'needs_answer', { needs_person: false }), []],
    ['running', run('c8', 'running', { needs_person: null }), []],
  ];
  // an ask of the run (the ones that wait on an answer)
  for (const c of cases) if (c[1].stage === 'needs_answer') c[2] = [ask(c[1])];

  it.each(cases)('%s', (_name, r, asks) => {
    const d = data([r], asks);
    const you = waitsOnPerson(r);
    // the tab's runs mark
    expect(runsMarkOf(d, 'S1', NOW)?.waits).toBe(you);
    // the pinned badge counts it (by itself or by its ask) exactly when it waits on you
    expect(activityOf(d).waiting > 0).toBe(you);
    // the track's chip
    expect(dispatchTrack(r).kind === 'you').toBe(you);
    // the board's card, in the Canvas and in Swarm Out
    for (const scope of ['session', 'all'] as const) {
      const { unmount } = render(<RunBoard scope={scope} sessionId="S1" source={source(d)} now={NOW} />);
      const card = screen.getByTestId('so-run-card');
      expect(!!within(card).queryByTestId('so-you-pill')).toBe(you);
      expect(card.getAttribute('data-kind') === 'you').toBe(you);
      unmount();
    }
  });
});

describe('3. the same big card from Canvas Runs and from Swarm Out', () => {
  it('opens RunOverlay with the same commands', async () => {
    const r = run('b1', 'awaiting', { needs_person: true, title: 'Big card' });
    runsFn.mockResolvedValue(data([r]));
    const commands = async () => {
      fireEvent.click(await screen.findByTestId('so-run-title'));
      const dialog = await screen.findByRole('dialog');
      return within(dialog).queryAllByTestId(/^swarm-out-cmd-/).map((b) => b.getAttribute('data-testid'));
    };
    render(<CanvasRuns sessionId="S1" />);
    const fromCanvas = await commands();
    cleanup();
    _resetSharedPolls();
    render(<RunBoard now={NOW} />);
    const fromSwarmOut = await commands();
    expect(fromCanvas.length).toBeGreaterThan(0);
    expect(fromCanvas).toEqual(fromSwarmOut);
  });
});

describe('the dispatch card\'s way to the Canvas Runs', () => {
  const block = (id: string) => ({ type: 'swarm_out_dispatch', ok: true, dispatchId: id, title: 'Card', brief: null, hive: 'explorer',
    hiveReason: null, brain: 'SwarmAI', mode: 'code-change', revision: null, brainSync: null, execution: null, executionWhy: null,
    profile: null, repos: [], acceptance: null, delivery: null, sessionId: 'hs-x', at: SENT, error: null, task: null } as unknown as SwarmOutDispatchContent);
  it('shows "View in Canvas" only in the chat of the session that dispatched the run', async () => {
    runsFn.mockResolvedValue(data([run('v1', 'running', { title: 'Card' })]));
    const { rerender } = render(<DispatchCard block={block('v1')} sessionId="S1" />);
    expect(await screen.findByTestId('dispatch-view-canvas')).toBeTruthy();
    rerender(<DispatchCard block={block('v1')} sessionId="S2" />);
    expect(screen.queryByTestId('dispatch-view-canvas')).toBeNull();
    rerender(<DispatchCard block={block('v1')} />);
    expect(screen.queryByTestId('dispatch-view-canvas')).toBeNull();
  });
});

describe('the Runs tab when the session\'s last run goes', () => {
  it('brings Artifacts back once the answer lists none, never before an answer', async () => {
    let answer: (d: SwarmOutRuns) => void = () => {};
    runsFn.mockImplementationOnce(() => new Promise((res) => { answer = res; }));
    const onGone = vi.fn();
    render(<CanvasRunsTab sessionId="S1" selected onSelect={() => {}} onGone={onGone} />);
    await act(async () => { await Promise.resolve(); });
    expect(onGone).not.toHaveBeenCalled();                // no answer yet: nothing is known
    await act(async () => { answer(data([run('x1', 'running', { dispatched_from: 'S2' })])); });
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('canvas-tab-runs')).toBeNull();
  });
});

describe('4. one shared runs poll', () => {
  it('Canvas Runs, a dispatch card and the badge make one request per interval', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] });
    vi.setSystemTime(NOW);
    const r = run('d1', 'running', { needs_person: false, title: 'Polled once' });
    runsFn.mockResolvedValue(data([r]));
    const block = { type: 'swarm_out_dispatch', ok: true, dispatchId: 'd1', title: 'Polled once', brief: null, hive: 'explorer',
      hiveReason: null, brain: 'SwarmAI', mode: 'code-change', revision: null, brainSync: null, execution: null, executionWhy: null,
      profile: null, repos: [], acceptance: null, delivery: null, sessionId: 'hs-d1', at: SENT, error: null, task: null } as unknown as SwarmOutDispatchContent;
    render(<><CanvasRuns sessionId="S1" /><DispatchCard block={block} /></>);
    renderHook(() => useSwarmOutActivity());
    await act(async () => { await Promise.resolve(); });
    expect(runsFn).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(POLL_MS); await Promise.resolve(); });
    expect(runsFn).toHaveBeenCalledTimes(2);
    // every reader shows the one answer
    expect(screen.getByTestId('so-run-title').textContent).toBe('Polled once');
    expect(screen.getByTestId('dispatch-live')).toBeTruthy();
  });
});
