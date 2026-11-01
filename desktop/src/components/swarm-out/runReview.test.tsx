/**
 * The run list and the run's big card (design docs/design/2026-10-31-swarm-out-run-review.md,
 * verification plan §5, invariant 9).
 *
 * Questions:
 *  - sections: does "waiting for you" hold needs-your-answer > awaiting > stopped, the
 *    longest waiting first; does a settled run leave it; do the Brain chips come from
 *    the rows (no fixed Brain);
 *  - the command panel: does it render exactly the backend's actions (primary first,
 *    discard last, nothing else), and does each command call its route (accept on the
 *    result shown, landed by hand, stop, re-run with the text, discard with the note);
 *  - do re-run and discard open a big box in place (at least 6 rows for a correction),
 *    and does a single-key shortcut never fire while typing in it;
 *  - the list: Open session on every row that may open (none on a discarded one), Stop
 *    on a running one, a click opens the card, J walks the runs waiting for you, and a
 *    settled run moves the card on;
 *  - does an accepted run open its session read-only;
 *  - does the tab's count follow the stage.
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { renderHook } from '@testing-library/react';
import { createElement, useState, type ReactNode } from 'react';
import { swarmOutService, type SwarmOutAction, type SwarmOutRun } from '../../services/swarmOut';
import { CommandPanel, RunOverlay, type PanelMode } from './RunOverlay';
import { RunList } from './RunList';
import { OpenSessionContext, PanelHiddenContext, useOpenRun } from './openSession';
import { brainChips, sections } from './runReview';
import { activityOf } from './useSwarmOutActivity';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    accept: vi.fn(async () => undefined), manualLanded: vi.fn(async () => undefined),
    rerun: vi.fn(async () => undefined), stop: vi.fn(async () => undefined),
    discard: vi.fn(async () => ({ discarded: true, stopped: true })),
    result: vi.fn(() => new Promise(() => {})), answer: vi.fn(),
    returnPrepare: vi.fn(), returnLand: vi.fn(), refresh: vi.fn(),
  },
}));
const svc = vi.mocked(swarmOutService);

const ENDED = '2026-10-30T10:00:00Z';
const NOW = Date.parse('2026-10-31T12:00:00Z');
const SID = '9db44de7-b39e-4a35-b739-85b656a04264';
const sid = (n: number) => SID.replace(/.$/, String(n));

const A = (key: SwarmOutAction['key'], primary = false, variant?: SwarmOutAction['variant']): SwarmOutAction =>
  ({ key, primary, ...(variant ? { variant } : {}) });
const ACTIONS: Record<string, SwarmOutAction[]> = {
  running: [A('stop', true), A('open_session'), A('discard')],
  needs_answer: [A('answer', false, 'hint'), A('open_session'), A('discard')],
  awaiting: [A('accept', true, 'verdict'), A('rerun'), A('open_session'), A('discard')],
  stopped: [A('rerun', true), A('open_session'), A('discard')],
  accepted: [A('open_session', false, 'read_only')],
  discarded: [],
};

function run(id: string, stage: SwarmOutRun['stage'], over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: sid(Number(id.slice(-1)) || 0), title: `task ${id}`,
    origin: 'swarm-out', sent_at: '2026-10-31T08:00:00Z', waiting: stage === 'needs_answer', running: stage === 'running',
    state: stage === 'running' ? 'running' : stage === 'needs_answer' ? 'waiting' : 'stopped',
    project_agent: { brain: 'SwarmAI', mode: 'research', revision: 'r1', profile: '', delivery: 'document',
      acceptance: 'A report on the last 7 days', returns: 'document' },
    result: { ended: 'completed', provisional: false, ended_at: ENDED, turns: 1,
      work: { repos: [], files: [], human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0 },
        steps: { total: 3, failed: 0, agents: 0, by_tool: {} }, changes: [], files_omitted: 0 },
      pipeline: null, checks: null, execution_check: null } as unknown as SwarmOutRun['result'],
    stage, actions: ACTIONS[stage ?? 'discarded'], why: null,
    settle: { verdict: null, code: 'none', why_not: ['verdict'], settled: null },
    ...over,
  };
}

beforeEach(() => { vi.clearAllMocks(); svc.result.mockImplementation(() => new Promise(() => {})); });

// ── sections ────────────────────────────────────────────────────────────────

describe('sections', () => {
  it('waiting for you: answer, then accept, then stopped, the longest waiting first; settled runs leave', () => {
    const runs = [
      run('s1', 'stopped', { result: { ...run('x', 'stopped').result!, ended_at: '2026-10-31T09:00:00Z' } }),
      run('a1', 'awaiting', { result: { ...run('x', 'awaiting').result!, ended_at: '2026-10-31T11:00:00Z' } }),
      run('a2', 'awaiting', { result: { ...run('x', 'awaiting').result!, ended_at: '2026-10-31T07:00:00Z' } }),
      run('q1', 'needs_answer'), run('r1', 'running'),
      run('p1', 'accepted', { settle: { verdict: 'pass', code: 'none', why_not: [], settled: { at: '2026-10-31T11:30:00Z', outcome: 'passed', released: true } } }),
      run('d1', 'discarded', { settle: { verdict: 'abandoned', code: 'none', why_not: [], settled: { at: '2026-10-29T11:30:00Z', outcome: 'abandoned', released: true } } }),
    ];
    const s = sections(runs, [], NOW);
    expect(s.you.map((r) => r.dispatch_id)).toEqual(['q1', 'a2', 'a1', 's1']);
    expect(s.running.map((r) => r.dispatch_id)).toEqual(['r1']);
    expect(s.done.map((r) => r.dispatch_id)).toEqual(['p1']);            // d1 settled 2 days ago: history's
  });

  it('the Brain chips come from the rows, with counts, biggest first', () => {
    const runs = [run('a1', 'awaiting'), run('a2', 'awaiting', { project_agent: { ...run('x', 'awaiting').project_agent!, brain: 'Brand-New Brain' } }),
      run('a3', 'running', { project_agent: { ...run('x', 'awaiting').project_agent!, brain: 'Brand-New Brain' } })];
    expect(brainChips(runs)).toEqual([{ brain: 'Brand-New Brain', count: 2 }, { brain: 'SwarmAI', count: 1 }]);
  });

  it('a run whose records go soon waits for you, whatever its stage', () => {
    const r = run('p1', 'accepted', { cleanup_soon: true });
    expect(sections([r], [], NOW).you.map((x) => x.dispatch_id)).toEqual(['p1']);
  });

  it('the tab counts the runs waiting on a decision, by stage', () => {
    const d = { runs: [run('a1', 'awaiting'), run('s1', 'stopped'), run('s2', 'stopped', { actions: [A('open_session')] }), run('r1', 'running'),
      run('p1', 'accepted', { settle: { verdict: 'pass', code: 'none', why_not: [], settled: { at: ENDED, outcome: 'passed', released: true } } })], asks: [], errors: [] };
    expect(activityOf(d).waiting).toBe(2);       // a1, s1; s2 offers no decision; p1 is settled
    expect(activityOf(d).running).toBe(1);
  });
});

// ── the command panel ───────────────────────────────────────────────────────

/** The panel with its mode held as the card holds it. */
function Panel({ r, onDone }: { r: SwarmOutRun; onDone: (closed: boolean) => void }) {
  const [mode, setMode] = useState<PanelMode>('list');
  return <CommandPanel r={r} full={null} mode={mode} setMode={setMode} onDone={onDone} onShowDeliverable={() => {}} />;
}
const panel = (r: SwarmOutRun, onDone = vi.fn(), open: ((s: string, t: string, ro?: boolean) => void) | null = vi.fn()) => {
  render(<OpenSessionContext.Provider value={open}><Panel r={r} onDone={onDone} /></OpenSessionContext.Provider>);
  return onDone;
};
const shown = () => screen.getAllByTestId(/^swarm-out-cmd-/).map((b) => `${b.dataset.primary ? '*' : ''}${b.getAttribute('data-testid')!.replace('swarm-out-cmd-', '')}`);

describe('the command panel', () => {
  it.each(Object.keys(ACTIONS).filter((k) => ACTIONS[k].length))('renders exactly the %s actions, primary first, discard last', (stage) => {
    panel(run('x1', stage as SwarmOutRun['stage']));
    const want = ACTIONS[stage].filter((a) => a.variant !== 'hint');       // a hint is not a button
    const order = [...want.filter((a) => a.primary), ...want.filter((a) => !a.primary && a.key !== 'discard'), ...want.filter((a) => a.key === 'discard')];
    expect(shown()).toEqual(order.map((a) => `${a.primary ? '*' : ''}${a.key}`));
  });

  it('waiting on an answer, the panel points to the card instead of offering an Answer button', () => {
    panel(run('x1', 'needs_answer'));
    expect(screen.queryByTestId('swarm-out-cmd-answer')).toBeNull();
    expect(screen.getByTestId('swarm-out-command-hint').textContent).toContain('card on the left');
  });

  it('with no wait card here, the hint says where to answer instead', () => {
    render(<CommandPanel r={run('x1', 'needs_answer')} full={null} mode="list" setMode={vi.fn()} onDone={vi.fn()}
      onShowDeliverable={vi.fn()} hasWaits={false} />);
    expect(screen.getByTestId('swarm-out-command-hint').textContent).not.toContain('card on the left');
  });

  it('a button holds only its name and key; what it does is a line apart', () => {
    panel(run('x1', 'awaiting'));
    const accept = screen.getByTestId('swarm-out-cmd-accept');
    expect(accept.textContent).toBe('AcceptA');
    expect(accept.textContent).not.toContain('settled');
    expect(screen.getAllByTestId('swarm-out-command-help').map((h) => h.textContent).join(' ')).toContain('this run is settled');
    expect(screen.getByTestId('swarm-out-cmd-discard').textContent).toBe('Discard…⇧D');
  });

  it.each([['answer_sent', 'Answered, waiting for the Hive'], ['answer_refused', 'Answer not used']])(
    'the state line says %s', (reason, text) => {
      panel(run('x1', reason === 'answer_sent' ? 'running' : 'needs_answer', { state_reason: reason,
        actions: reason === 'answer_sent' ? [A('open_session'), A('discard')] : [A('open_session', true), A('discard')] }));
      expect(screen.getByTestId('swarm-out-review-stage').textContent).toContain(text);
    });

  it.each([['discard', 'swarm-out-review-discard'], ['rerun', 'swarm-out-review-rerun']])(
    '%s confirms in the one sheet: Cancel and the confirm side by side', (key, id) => {
      panel(run('x1', 'awaiting'));
      fireEvent.click(screen.getByTestId(`swarm-out-cmd-${key}`));
      const sheet = screen.getByTestId('confirm-sheet');
      expect(sheet.getAttribute('data-for')).toBe(id);
      const row = screen.getByTestId('swarm-out-review-cancel').parentElement!;
      expect(row.contains(screen.getByTestId('swarm-out-review-submit'))).toBe(true);
      expect((screen.getByTestId('swarm-out-review-box') as HTMLTextAreaElement).rows).toBeGreaterThanOrEqual(4);
    });

  it('a discarded run offers nothing', () => {
    panel(run('x1', 'discarded'));
    expect(screen.queryAllByTestId(/^swarm-out-cmd-/)).toHaveLength(0);
  });

  it('accept passes on the result shown and moves on', async () => {
    const done = panel(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    await waitFor(() => expect(svc.accept).toHaveBeenCalledWith('x1', ENDED));
    expect(done).toHaveBeenCalledWith(true);
  });

  it('commits that cannot be taken as they are: "I merged it by hand" says why and asks once more', async () => {
    panel(run('x1', 'awaiting', { actions: [A('accept', true, 'manual'), A('rerun'), A('open_session'), A('discard')] }));
    const btn = screen.getByTestId('swarm-out-cmd-accept');
    expect(btn.textContent).toContain('I merged it by hand');
    fireEvent.click(btn);
    expect(svc.manualLanded).not.toHaveBeenCalled();
    expect(screen.getByTestId('swarm-out-review-manual').textContent).toContain('not sealed into a bundle');
    fireEvent.click(screen.getByTestId('swarm-out-review-submit'));
    await waitFor(() => expect(svc.manualLanded).toHaveBeenCalledWith('x1'));
  });

  it('re-run sits with accept; only open session is under "Also"', () => {
    panel(run('x1', 'awaiting'));
    const also = screen.getByText('Also').parentElement!;
    expect(within(also).queryByTestId('swarm-out-cmd-rerun')).toBeNull();
    expect(within(also).getByTestId('swarm-out-cmd-open_session')).toBeTruthy();
  });

  it('⌘↩ pressed twice sends the correction once', async () => {
    let finish: () => void = () => {};
    svc.rerun.mockImplementationOnce(() => new Promise<undefined>((r) => { finish = () => r(undefined); }));
    panel(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-rerun'));
    const box = screen.getByTestId('swarm-out-review-box');
    fireEvent.change(box, { target: { value: 'again' } });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    await act(async () => { finish(); });
    expect(svc.rerun).toHaveBeenCalledTimes(1);
  });

  it('accept that lands commits shows the review of each repository in the panel', () => {
    panel(run('x1', 'awaiting', { actions: [A('accept', true, 'land'), A('rerun'), A('discard')] }));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    expect(screen.getByTestId('swarm-out-review-land')).toBeTruthy();
    expect(svc.accept).not.toHaveBeenCalled();
  });

  it('a landing the commits refuse leads to "I landed them by hand"', async () => {
    const base = run('x1', 'awaiting').result!;
    const full = { ...base, work: { ...base.work, repos: [{ dir: 'Hive/work/x/repos/a', url: 'https://github.com/acme/a.git',
      files: [], commits: [], bundle: { key: 'k', sha256: 's', available: true } }] } } as never;
    svc.returnPrepare.mockRejectedValue({ detail: 'the commits do not apply onto main (a conflict)' });
    function WithFull() {
      const [mode, setMode] = useState<PanelMode>('list');
      return <CommandPanel r={run('x1', 'awaiting', { actions: [A('accept', true, 'land'), A('rerun'), A('discard')] })}
        full={full} fullRead mode={mode} setMode={setMode} onDone={vi.fn()} onShowDeliverable={() => {}} />;
    }
    render(<OpenSessionContext.Provider value={vi.fn()}><WithFull /></OpenSessionContext.Provider>);
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    fireEvent.click(await screen.findByTestId('swarm-out-return-by-hand'));
    expect(screen.getByTestId('swarm-out-review-manual')).toBeTruthy();
    fireEvent.click(screen.getByTestId('swarm-out-review-submit'));
    await waitFor(() => expect(svc.manualLanded).toHaveBeenCalledWith('x1'));
  });

  it('with another repository still to land, a refused one offers no "by hand" (it would count for both)', async () => {
    const base = run('x1', 'awaiting').result!;
    const repo = (d: string) => ({ dir: d, url: `https://github.com/acme/${d}.git`, files: [], commits: [],
      bundle: { key: d, sha256: 's', available: true } });
    const full = { ...base, work: { ...base.work, repos: [repo('a'), repo('b')] } } as never;
    svc.returnPrepare.mockRejectedValue({ detail: 'the commits do not apply onto main (a conflict)' });
    function WithFull() {
      const [mode, setMode] = useState<PanelMode>('land');
      return <CommandPanel r={run('x1', 'awaiting', { actions: [A('accept', true, 'land'), A('rerun'), A('discard')] })}
        full={full} fullRead mode={mode} setMode={setMode} onDone={vi.fn()} onShowDeliverable={() => {}} />;
    }
    render(<OpenSessionContext.Provider value={vi.fn()}><WithFull /></OpenSessionContext.Provider>);
    await waitFor(() => expect(screen.getAllByTestId('swarm-out-return-error')).toHaveLength(2));
    expect(screen.queryByTestId('swarm-out-return-by-hand')).toBeNull();
  });

  it('re-run opens a big box in place and sends the text into the session', async () => {
    const done = panel(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-rerun'));
    const box = screen.getByTestId('swarm-out-review-box') as HTMLTextAreaElement;
    expect(Number(box.getAttribute('rows'))).toBeGreaterThanOrEqual(6);
    expect((screen.getByTestId('swarm-out-review-submit') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(box, { target: { value: 'Count by day too' } });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(svc.rerun).toHaveBeenCalledWith('x1', 'Count by day too'));
    expect(done).toHaveBeenCalledWith(false);      // the run goes on: the card stays
  });

  it('discard asks first, says what it removes, takes an optional note', async () => {
    const done = panel(run('x1', 'stopped', { settle: { verdict: null, code: 'unsealed', why_not: ['code'], settled: null } }));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-discard'));
    expect(svc.discard).not.toHaveBeenCalled();
    expect(screen.getByTestId('swarm-out-review-discard').textContent).toContain('never sealed');
    fireEvent.change(screen.getByTestId('swarm-out-review-box'), { target: { value: 'not needed' } });
    fireEvent.click(screen.getByTestId('swarm-out-review-submit'));
    await waitFor(() => expect(svc.discard).toHaveBeenCalledWith('x1', 'not needed'));
    expect(done).toHaveBeenCalledWith(true);
  });

  it('a single key gives its command, but never while typing', async () => {
    panel(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-rerun'));
    const box = screen.getByTestId('swarm-out-review-box');
    fireEvent.keyDown(box, { key: 'a' });
    fireEvent.keyDown(box, { key: 'D', shiftKey: true });
    expect(svc.accept).not.toHaveBeenCalled();
    expect(screen.queryByTestId('swarm-out-review-discard')).toBeNull();
    fireEvent.click(screen.getByTestId('swarm-out-review-cancel'));
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(svc.accept).toHaveBeenCalledWith('x1', ENDED));
  });

  it('no single key fires while the cursor is in any field (an answer box on the card)', () => {
    render(<><input data-testid="other-field" /><OpenSessionContext.Provider value={vi.fn()}>
      <Panel r={run('x1', 'awaiting')} onDone={vi.fn()} /></OpenSessionContext.Provider></>);
    fireEvent.keyDown(screen.getByTestId('other-field'), { key: 'a' });
    fireEvent.keyDown(screen.getByTestId('other-field'), { key: 'D', shiftKey: true });
    expect(svc.accept).not.toHaveBeenCalled();
    expect(screen.queryByTestId('swarm-out-review-discard')).toBeNull();
  });

  it('stop stops and keeps the card', async () => {
    const done = panel(run('x1', 'running'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-stop'));
    await waitFor(() => expect(svc.stop).toHaveBeenCalledWith('x1'));
    expect(done).toHaveBeenCalledWith(false);
  });

  it('a refused command says why', async () => {
    svc.accept.mockRejectedValueOnce({ response: { data: { detail: 'this run is closed' } } });
    panel(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    expect((await screen.findByTestId('swarm-out-review-error')).textContent).toContain('this run is closed');
  });
});

// ── the list and the card ───────────────────────────────────────────────────

const list = (runs: SwarmOutRun[], open: ((s: string, t: string, ro?: boolean) => void) | null = vi.fn(), onReload = vi.fn()) => {
  render(<OpenSessionContext.Provider value={open}><RunList runs={runs} asks={[]} now={NOW} onReload={onReload} /></OpenSessionContext.Provider>);
  return { open, onReload };
};

describe('the run list', () => {
  it('every row that may open has Open session; a running row has Stop; no accept on a row', () => {
    list([run('a1', 'awaiting'), run('r1', 'running')]);
    const rows = screen.getAllByTestId('swarm-out-row');
    expect(rows.map((r) => !!within(r).queryByTestId('swarm-out-row-open'))).toEqual([true, true]);
    expect(rows.map((r) => !!within(r).queryByTestId('swarm-out-row-stop'))).toEqual([false, true]);
    expect(screen.queryByText('Accept')).toBeNull();
  });

  it('Stop on a row stops the run without opening its card', async () => {
    const { onReload } = list([run('r1', 'running')]);
    fireEvent.click(screen.getByTestId('swarm-out-row-stop'));
    await waitFor(() => expect(svc.stop).toHaveBeenCalledWith('r1'));
    expect(onReload).toHaveBeenCalled();
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
  });

  it('a click opens the card; J walks the runs waiting for you; a settled run moves the card on', async () => {
    list([run('a1', 'awaiting'), run('a2', 'awaiting'), run('r1', 'running')]);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    expect(within(overlay).getByTestId('swarm-out-review-position').textContent).toBe('Waiting for you 1 / 2');
    fireEvent.keyDown(window, { key: 'j' });
    expect(within(overlay).getByTestId('swarm-out-review-position').textContent).toBe('Waiting for you 2 / 2');
    fireEvent.click(within(overlay).getByTestId('swarm-out-cmd-accept'));
    await waitFor(() => expect(svc.accept).toHaveBeenCalledTimes(1));
    // the accepted run leaves the order: one left
    await waitFor(() => expect(screen.getByTestId('swarm-out-review-position').textContent).toBe('Waiting for you 1 / 1'));
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    await waitFor(() => expect(svc.accept).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull());   // nothing left: closed
  });

  it('Esc in a box steps back to the commands; J and a click outside cannot throw the text away', async () => {
    list([run('a1', 'awaiting'), run('a2', 'awaiting')]);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    fireEvent.click(within(overlay).getByTestId('swarm-out-cmd-rerun'));
    const box = within(overlay).getByTestId('swarm-out-review-box') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'half written' } });
    fireEvent.click(within(overlay).getByTestId('swarm-out-review-stage'));   // focus off the box
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.mouseDown(overlay);
    expect(screen.getByTestId('swarm-out-review-position').textContent).toBe('Waiting for you 1 / 2');
    expect((screen.getByTestId('swarm-out-review-box') as HTMLTextAreaElement).value).toBe('half written');
    fireEvent.keyDown(screen.getByTestId('swarm-out-review-box'), { key: 'Escape' });
    expect(screen.getByTestId('swarm-out-command-panel')).toBeTruthy();       // back to the commands, card still open
    fireEvent.keyDown(screen.getByTestId('swarm-out-command-panel'), { key: 'Escape' });
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
  });

  it('a half-given answer to the run\'s question is kept from J, a click outside and Esc', async () => {
    const ask = { kind: 'question' as const, hive: 'explorer', hive_id: 'h1', session_id: sid(1), request_id: 'q1', asked_at: '2026-10-31T09:00:00Z',
      dispatch_title: 'task q1', questions: [{ question: 'Which port?', header: 'p', multiSelect: false, options: [] }] };
    render(<OpenSessionContext.Provider value={vi.fn()}>
      <RunList runs={[run('q1', 'needs_answer'), run('a2', 'awaiting')]} asks={[ask]} now={NOW} onReload={vi.fn()} /></OpenSessionContext.Provider>);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    fireEvent.click(within(overlay).getByText('Other (custom answer)'));
    fireEvent.change(within(overlay).getByPlaceholderText(/Enter your custom answer/), { target: { value: '80' } });
    fireEvent.keyDown(document.body, { key: 'j' });
    fireEvent.mouseDown(overlay);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.getByTestId('swarm-out-review-position').textContent).toBe('Waiting for you 1 / 2');
    expect((within(overlay).getByPlaceholderText(/Enter your custom answer/) as HTMLInputElement).value).toBe('80');
    // the card is above the tabs, not inside what came back
    expect(within(overlay).getByTestId('swarm-out-review-waits')).toBeTruthy()
  });

  it('the answer key (W) takes you to the wait card, or with none here to where to answer', async () => {
    const ask = { kind: 'decision' as const, hive: 'explorer', hive_id: 'h1', session_id: sid(1), request_id: 'dc-1',
      asked_at: '2026-10-31T09:00:00Z', question: 'Which?', why: '', options: [
        { id: 'a', choice: 'x', consequence: '', reversible: true, risk: 'low' as const },
        { id: 'b', choice: 'y', consequence: '', reversible: true, risk: 'low' as const }] };
    const { unmount } = render(<OpenSessionContext.Provider value={vi.fn()}>
      <RunList runs={[run('q1', 'needs_answer')]} asks={[ask]} now={NOW} onReload={vi.fn()} /></OpenSessionContext.Provider>);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    const dialog = within(overlay).getByRole('dialog');
    dialog.focus();
    expect(document.activeElement).toBe(dialog);
    fireEvent.keyDown(dialog, { key: 'w' });
    expect(document.activeElement?.tagName).toBe('BUTTON');
    expect(document.activeElement?.textContent).toContain('a — x');          // the card's first option
    unmount();
    render(<OpenSessionContext.Provider value={vi.fn()}>
      <RunList runs={[run('q2', 'needs_answer')]} asks={[]} now={NOW} onReload={vi.fn()} /></OpenSessionContext.Provider>);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    const lone = await screen.findByTestId('swarm-out-review-overlay');
    fireEvent.click(within(lone).getByTestId('swarm-out-review-tab-cost'));
    fireEvent.keyDown(within(lone).getByRole('dialog'), { key: 'w' });
    expect(within(lone).getByTestId('swarm-out-review-tab-deliverable').getAttribute('aria-selected')).toBe('true');
  });

  it('closing the card puts focus back on the row that opened it', async () => {
    list([run('a1', 'awaiting')]);
    const row = screen.getAllByTestId('swarm-out-row')[0];
    row.focus();
    fireEvent.click(row);
    fireEvent.click(await screen.findByTestId('swarm-out-review-close'));
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
    expect(document.activeElement).toBe(screen.getAllByTestId('swarm-out-row')[0]);
  });

  it('a command key pressed in a layer above the card is not the card\'s', () => {
    render(<><div data-testid="above"><button data-testid="above-btn">x</button></div>
      <OpenSessionContext.Provider value={vi.fn()}><RunList runs={[run('a1', 'awaiting')]} asks={[]} now={NOW} onReload={vi.fn()} /></OpenSessionContext.Provider></>);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    fireEvent.keyDown(screen.getByTestId('above-btn'), { key: 'a' });
    expect(svc.accept).not.toHaveBeenCalled();
  });

  it('the card closes when its session opens, or when the panel goes off screen', async () => {
    const open = vi.fn();
    const { rerender } = render(<OpenSessionContext.Provider value={open}><PanelHiddenContext.Provider value={false}>
      <RunList runs={[run('a1', 'awaiting')]} asks={[]} now={NOW} onReload={vi.fn()} /></PanelHiddenContext.Provider></OpenSessionContext.Provider>);
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    fireEvent.click(await screen.findByTestId('swarm-out-review-open'));
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
    fireEvent.click(screen.getAllByTestId('swarm-out-row')[0]);
    await screen.findByTestId('swarm-out-review-overlay');
    rerender(<OpenSessionContext.Provider value={open}><PanelHiddenContext.Provider value={true}>
      <RunList runs={[run('a1', 'awaiting')]} asks={[]} now={NOW} onReload={vi.fn()} /></PanelHiddenContext.Provider></OpenSessionContext.Provider>);
    await waitFor(() => expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull());
  });

  it('Enter on a row\'s Open session button opens the session, not the card', () => {
    const open = vi.fn();
    list([run('a1', 'awaiting')], open);
    const btn = screen.getByTestId('swarm-out-row-open');
    fireEvent.keyDown(btn, { key: 'Enter' });
    expect(screen.queryByTestId('swarm-out-review-overlay')).toBeNull();
    fireEvent.keyDown(screen.getAllByTestId('swarm-out-row')[0], { key: ' ' });
    expect(screen.getByTestId('swarm-out-review-overlay')).toBeTruthy();
  });

  it('a section header counts its rows; recently done is folded', () => {
    list([run('a1', 'awaiting'), run('p1', 'accepted', { settle: { verdict: 'pass', code: 'none', why_not: [], settled: { at: '2026-10-31T11:00:00Z', outcome: 'passed', released: true } } })]);
    expect(screen.getAllByTestId('swarm-out-row')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('swarm-out-section-done'));
    expect(screen.getAllByTestId('swarm-out-row')).toHaveLength(2);
  });
});

describe('opening a run\'s session', () => {
  const wrap = (open: ReturnType<typeof vi.fn>) => ({ children }: { children: ReactNode }) =>
    createElement(OpenSessionContext.Provider, { value: open }, children);

  it('a discarded run cannot open; an accepted one opens read-only; an awaiting one opens', () => {
    const open = vi.fn();
    expect(renderHook(() => useOpenRun(run('d1', 'discarded')), { wrapper: wrap(open) }).result.current).toBeNull();
    const accepted = renderHook(() => useOpenRun(run('p1', 'accepted')), { wrapper: wrap(open) }).result.current;
    act(() => accepted!());
    expect(open).toHaveBeenLastCalledWith(sid(1), 'task p1 · explorer', true);
    const awaiting = renderHook(() => useOpenRun(run('a1', 'awaiting')), { wrapper: wrap(open) }).result.current;
    act(() => awaiting!());
    expect(open).toHaveBeenLastCalledWith(sid(1), 'task a1 · explorer', false);
  });
});

// ── the big card (RunOverlay) ─────────────────────────────────────────────────

describe('the big card', () => {
  const record = (result: unknown) => ({ dispatch_id: 'x1', record: { result, fetch_error: null, checked_at: ENDED, fetched_at: ENDED } }) as never;
  const card = (r: SwarmOutRun, over: Partial<Parameters<typeof RunOverlay>[0]> = {}) => {
    const props = { runs: [r], asks: [], order: [r.dispatch_id], id: r.dispatch_id, now: NOW,
      onSelect: vi.fn(), onSettled: vi.fn(), onClose: vi.fn(), onFileOpened: vi.fn(), onReload: vi.fn(), ...over };
    render(<RunOverlay {...props} />);
    return props;
  };
  const withFile = () => record({ ...run('x1', 'awaiting').result, report: 'Done.', report_chars: 5,
    work: { ...run('x1', 'awaiting').result!.work, files: [{ path: '/hive/r.md', kind: 'content', local: 'Projects/P/deliverables/hive/x/r.md' }] } });

  it('opened from the history, settling a record closes the card and drops it from J / K', async () => {
    const p = card(run('x1', 'awaiting'), { walk: 'records', order: ['x0', 'x1', 'x2'] });
    fireEvent.click(screen.getByTestId('swarm-out-cmd-accept'));
    await waitFor(() => expect(p.onClose).toHaveBeenCalled());
    expect(p.onSettled).toHaveBeenCalledWith('x1');
    expect(p.onSelect).not.toHaveBeenCalled();   // never on to the next record
    expect(screen.getByTestId('swarm-out-review-position').textContent).toBe('Record 2 / 3');
  });

  it('a file opened by a click inside closes it (and the host); one an agent opens does not', async () => {
    svc.result.mockResolvedValue(withFile());
    const p = card(run('x1', 'awaiting'));
    const row = await screen.findByTestId('swarm-out-review-file');
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: 'a.md', by: 'agent' } }));
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: 'b.md' } }));   // not from a click in it
    expect(p.onClose).not.toHaveBeenCalled();
    fireEvent.click(row.querySelector('button')!);
    expect(p.onClose).toHaveBeenCalledTimes(1);
    expect(p.onFileOpened).toHaveBeenCalledTimes(1);
  });

  it('a record read with no result says why, never "loading" forever', async () => {
    svc.result.mockResolvedValue(record(null));
    card(run('x1', 'stopped', { result: null, result_status: 'failed', result_error: 'session_gone' }));
    expect((await screen.findByTestId('swarm-out-review-no-result')).textContent).toContain('no longer');
    expect(screen.queryByText('Loading…')).toBeNull();
  });

  it('the autonomy tab lists what the dispatching agent answered for the person, and why', async () => {
    const base = run('x1', 'awaiting').result!;
    svc.result.mockResolvedValue({ ...record({ ...base, work: { ...base.work, host_handled: 2 } }), host_answers: [
      { at: ENDED, request_id: 'q-1', class: 'question', asked: 'Which DB?', pick: 'sqlite', why: 'the task says local only' },
      { at: ENDED, request_id: 'ap-1', class: 'hive_local', asked: 'chmod 777 /opt/app', pick: 'Approve', why: 'the build needs it' },
    ] } as never);
    card(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-review-tab-autonomy'));
    const rows = await screen.findAllByTestId('swarm-out-review-host-answer');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Which DB?');
    expect(rows[0].textContent).toContain('sqlite');
    expect(rows[0].textContent).toContain('the task says local only');
    expect(rows[1].textContent).toContain('a command on the Hive');
    // listed: the bare count is not shown beside it
    expect(screen.queryByTestId('swarm-out-review-human-host')).toBeNull();
  });

  it('a refresh that fails says so', async () => {
    svc.result.mockResolvedValue(withFile());
    svc.refresh.mockRejectedValueOnce(new Error('down'));
    card(run('x1', 'awaiting'));
    fireEvent.click(screen.getByTestId('swarm-out-review-refresh'));
    expect((await screen.findByTestId('swarm-out-review-refresh-error')).textContent).toBeTruthy();
    svc.refresh.mockResolvedValueOnce({ result: null, result_error: 'session_gone' } as never);
    fireEvent.click(screen.getByTestId('swarm-out-review-refresh'));
    await waitFor(() => expect(screen.getByTestId('swarm-out-review-refresh-error').textContent).toContain('no longer'));
  });
});
