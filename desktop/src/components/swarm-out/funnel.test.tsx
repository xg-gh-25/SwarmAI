/**
 * The decision funnel on a run's card and in the run list (design
 * docs/design/2026-11-02-hive-run-decision-record.md §3.1, §5 R8).
 *
 * Questions:
 *  - does the card's title carry one funnel line: the gates passed and the current one,
 *    with who and when, the landed commit, and amber only when the person is asked;
 *  - is the Autonomy tab a Decisions tab that groups the record by gate as it happened,
 *    one row per decision, its observed facts one click away;
 *  - does an open ask-person show its card above the tabs (question, where it landed,
 *    the agent's judgment, why) with only keep and revert, a multi-line note, and the
 *    person's answer reaching its route;
 *  - in the list: does a run the agent is working through the funnel stay out of
 *    "waiting for you" and say where it is, does an open ask wait for you, and does the
 *    autonomy label count only the person's decisions;
 *  - is the acceptance strip free of amber.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutAction, type SwarmOutDecision, type SwarmOutRun } from '../../services/swarmOut';
import { RunOverlay } from './RunOverlay';
import { RunList } from './RunList';
import { sections } from './runReview';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    accept: vi.fn(async () => undefined), manualLanded: vi.fn(async () => undefined),
    rerun: vi.fn(async () => undefined), stop: vi.fn(async () => undefined),
    discard: vi.fn(async () => ({ discarded: true, stopped: true })),
    answerAsk: vi.fn(async () => undefined),
    result: vi.fn(() => new Promise(() => {})), answer: vi.fn(),
    returnPrepare: vi.fn(), returnLand: vi.fn(), refresh: vi.fn(),
  },
}));
const svc = vi.mocked(swarmOutService);

const ENDED = '2026-11-02T09:50:00Z';
const NOW = Date.parse('2026-11-02T12:00:00Z');
const A = (key: SwarmOutAction['key'], primary = false, variant?: SwarmOutAction['variant']): SwarmOutAction =>
  ({ key, primary, ...(variant ? { variant } : {}) });

function run(over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_aaaaaaaaaaaa', hive_id: 'h1', hive: 'hive-1', session_id: 'hs1', title: 'Fix the docs refresh',
    origin: 'swarm-out', sent_at: '2026-11-02T09:20:00Z', waiting: false, running: false, state: 'stopped',
    project_agent: { brain: 'SwarmAI', mode: 'code', revision: 'r1', profile: '', delivery: 'bundle',
      acceptance: 'Unknown flags no longer rewrite the docs', returns: 'commits' },
    result: { ended: 'completed', provisional: false, ended_at: ENDED, turns: 3,
      work: { repos: [], files: [], human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 2 },
        steps: { total: 3, failed: 0, agents: 0, by_tool: {} }, changes: [], files_omitted: 0 },
      pipeline: null, checks: null, execution_check: null } as unknown as SwarmOutRun['result'],
    stage: 'accepted', actions: [A('open_session', false, 'read_only')], why: null,
    settle: { verdict: 'pass', code: 'landed', why_not: [], settled: null },
    funnel: { gates: [
      { key: 'review', state: 'done', by: 'agent', at: '2026-11-02T09:52:00Z', ref: null },
      { key: 'land', state: 'done', by: 'agent', at: '2026-11-02T09:55:00Z', ref: 'e9c1b37a1aabbccddeeff0011' },
      { key: 'judge', state: 'done', by: 'agent', at: '2026-11-02T09:58:00Z', ref: null },
    ], current: null, ask: null, reruns: 1 },
    person_decisions: 0, needs_person: false,
    ...over,
  };
}

const ASK = { state: 'open' as const, decide: 'Keep the 40 kB language chunk?', judgment: 'Meets the acceptance; keep it',
  why: 'a tradeoff of your priorities', landed: { commits: ['4be20a1aabbccdd'], files: [] } };
const asking = () => run({
  stage: 'awaiting', why: 'ask_person', actions: [A('keep', true), A('revert'), A('open_session')],
  settle: { verdict: null, code: 'landed', why_not: ['verdict'], settled: null }, needs_person: true,
  funnel: { gates: [
    { key: 'review', state: 'done', by: 'agent', at: '2026-11-02T10:31:00Z', ref: null },
    { key: 'land', state: 'done', by: 'agent', at: '2026-11-02T10:33:00Z', ref: '4be20a1aabbccdd' },
    { key: 'judge', state: 'person', by: 'agent', at: '2026-11-02T10:35:00Z', ref: null },
  ], current: 'judge', ask: ASK, reruns: 0 },
});

const DECISIONS: SwarmOutDecision[] = [
  { at: '2026-11-02T09:21:00Z', by: 'agent', decision: 'dispatch', why: 'native is enough', observed: { hive: 'hive-1' } },
  { at: '2026-11-02T09:40:00Z', by: 'agent', decision: 'rerun', why: 'conflicts with main', observed: { reruns: 0 } },
  { at: '2026-11-02T09:52:00Z', by: 'agent', decision: 'review', why: 'read 3 files, 20 tests pass', observed: { ended_at: ENDED } },
  { at: '2026-11-02T09:55:00Z', by: 'agent', decision: 'land', why: 'protected path reviewed', observed: { landed: [{ commit: 'e9c1b37' }] } },
  { at: '2026-11-02T09:58:00Z', by: 'agent', decision: 'accept', why: 'acceptance holds', observed: {} },
];

const card = (r: SwarmOutRun) => render(<RunOverlay runs={[r]} asks={[]} order={[r.dispatch_id]} id={r.dispatch_id} now={NOW}
  onSelect={vi.fn()} onSettled={vi.fn()} onClose={vi.fn()} onFileOpened={vi.fn()} onReload={vi.fn()} />);

beforeEach(() => { vi.clearAllMocks(); svc.result.mockImplementation(() => new Promise(() => {})); });

describe('the funnel on the card', () => {
  it('one line under the title: the gates passed, who and when, the landed commit', () => {
    card(run());
    const line = screen.getByTestId('swarm-out-funnel');
    const gates = within(line).getAllByTestId('swarm-out-funnel-gate');
    expect(gates.map((g) => g.dataset.gate)).toEqual(['review', 'land', 'judge']);
    expect(gates.every((g) => g.dataset.state === 'done')).toBe(true);
    expect(gates[1].textContent).toContain('e9c1b37');
    expect(gates[1].textContent).not.toContain('e9c1b37a1');                // short commit
    expect(line.textContent).toContain('re-ran once before');
  });

  it('nothing while the run is on its Hive', () => {
    card(run({ stage: 'running', funnel: { gates: [], current: null, ask: null, reruns: 0 } }));
    expect(screen.queryByTestId('swarm-out-funnel')).toBeNull();
  });

  it('the Decisions tab groups the record by gate as it happened, the facts one click away', async () => {
    svc.result.mockResolvedValue({ dispatch_id: 'so_aaaaaaaaaaaa', record: null, host_answers: [], decisions: DECISIONS } as never);
    card(run());
    const tab = screen.getByTestId('swarm-out-review-tab-autonomy');
    expect(tab.textContent).toContain('Decisions');
    fireEvent.click(tab);
    const groups = await screen.findAllByTestId('swarm-out-decision-group');
    // the re-run is part of running; the second review starts its own group
    expect(groups.map((g) => g.dataset.gate)).toEqual(['run', 'review', 'land', 'judge']);
    const rows = screen.getAllByTestId('swarm-out-decision');
    expect(rows.map((r) => r.dataset.decision)).toEqual(['dispatch', 'rerun', 'review', 'land', 'accept']);
    expect(rows[3].textContent).toContain('protected path reviewed');
    expect(screen.queryByTestId('swarm-out-decision-observed')).toBeNull();
    fireEvent.click(rows[3]);
    expect(screen.getByTestId('swarm-out-decision-observed').textContent).toContain('e9c1b37');
  });
});

describe('a landed result handed to the person', () => {
  it('its card is above the tabs, and the panel offers only keep and revert', () => {
    card(asking());
    const ask = screen.getByTestId('swarm-out-ask-card');
    expect(ask.textContent).toContain('Keep the 40 kB language chunk?');
    expect(screen.getByTestId('swarm-out-ask-where').textContent).toContain('4be20a1');
    expect(screen.getByTestId('swarm-out-ask-judgment').textContent).toContain('Meets the acceptance');
    expect(screen.getByTestId('swarm-out-ask-why').textContent).toContain('a tradeoff');
    const gates = screen.getAllByTestId('swarm-out-funnel-gate');
    expect(gates[2].dataset.state).toBe('person');
    const cmds = screen.getAllByTestId(/^swarm-out-cmd-/).map((b) => b.getAttribute('data-testid'));
    expect(cmds).toContain('swarm-out-cmd-keep');
    expect(cmds).toContain('swarm-out-cmd-revert');
    expect(cmds).not.toContain('swarm-out-cmd-accept');
  });

  it('revert takes a note in a multi-line box and reaches its route', async () => {
    card(asking());
    fireEvent.click(screen.getByTestId('swarm-out-cmd-revert'));
    const box = screen.getByTestId('swarm-out-review-box') as HTMLTextAreaElement;
    expect(box.tagName).toBe('TEXTAREA');
    expect(box.rows).toBeGreaterThanOrEqual(4);
    fireEvent.change(box, { target: { value: 'too big for the first screen' } });
    fireEvent.click(screen.getByTestId('swarm-out-review-submit'));
    await waitFor(() => expect(svc.answerAsk).toHaveBeenCalledWith('so_aaaaaaaaaaaa', 'revert', 'too big for the first screen'));
  });

  it('the acceptance strip is not amber', () => {
    card(asking());
    const strip = screen.getByTestId('swarm-out-review-acceptance');
    expect(strip.getAttribute('style') ?? '').not.toContain('warning');
  });
});

describe('the list', () => {
  it('a run the agent works through the funnel is not waiting for you; an open ask is', () => {
    const working = run({ dispatch_id: 'so_bbbbbbbbbbbb', stage: 'awaiting', why: 'judge',
      actions: [A('accept', true, 'verdict'), A('rerun'), A('open_session'), A('discard')],
      settle: { verdict: null, code: 'landed', why_not: ['verdict'], settled: null }, needs_person: false });
    const ask = { ...asking(), dispatch_id: 'so_cccccccccccc' };
    const s = sections([working, ask], [], NOW);
    expect(s.you.map((r) => r.dispatch_id)).toEqual(['so_cccccccccccc']);
    expect(s.running.map((r) => r.dispatch_id)).toEqual(['so_bbbbbbbbbbbb']);
    render(<RunList runs={[working, ask]} asks={[]} now={NOW} onReload={vi.fn()} />);
    const rows = screen.getAllByTestId('swarm-out-row');
    const w = rows.find((r) => r.dataset.id === 'so_bbbbbbbbbbbb')!;
    expect(within(w).getByTestId('swarm-out-row-funnel').textContent).toBe('Agent judging');
    expect(within(w).queryByTestId('swarm-out-row-tag')).toBeNull();
    const a = rows.find((r) => r.dataset.id === 'so_cccccccccccc')!;
    expect(within(a).getByTestId('swarm-out-row-tag').textContent).toBe('Keep or revert');
  });

  it('the autonomy label counts only the person\'s decisions', () => {
    // the Hive saw two messages after the task (the agent's corrections): not the person
    render(<RunList runs={[run({ person_decisions: 0, stage: 'awaiting', why: 'judge',
      actions: [A('accept', true, 'verdict'), A('open_session'), A('discard')] })]} asks={[]} now={NOW} onReload={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-row-autonomy').textContent).toBe(i18n.t('swarmOut.review.autonomy.full'));
  });
});
