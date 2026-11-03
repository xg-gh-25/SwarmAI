/**
 * The Swarm Run card (design docs/design/2026-10-31-swarm-run.md §5.9; invariants I12,
 * I13). Questions:
 *  - is it the Swarm Out run list, filtered to this run's children (another run's
 *    children and ordinary runs are not on it), under the run's head line;
 *  - is a child waiting for its lead run not "waiting for you", and does its row offer no
 *    accept or rerun (the backend's actions are rendered as they are);
 *  - are the packages not dispatched yet listed, with what each waits for;
 *  - does "Stop all" call the run's stop, and show only while a child runs;
 *  - does a chat swarm_run block render this card (a dispatch block still renders the
 *    dispatch card).
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutRun, type SwarmRunView } from '../../../../services/swarmOut';
import { SwarmRunCard } from '../SwarmRunCard';
import { _resetDispatchPoll } from '../DispatchCard';
import i18n from '../../../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../../../services/swarmOut', () => ({
  swarmOutService: {
    runs: vi.fn(), swarmRun: vi.fn(), stopSwarmRun: vi.fn(async () => ({ stopped: ['so_000000000001'], not_stopped: [], lead_turn_stopped: true })),
    stop: vi.fn(), result: vi.fn(() => new Promise(() => {})),
  },
}));
const svc = vi.mocked(swarmOutService);
const RUN = 'run_0123456789ab';

function child(id: string, over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `9db44de7-b39e-4a35-b739-85b656a0426${id.slice(-1)}`,
    title: `W${id.slice(-1)} · part ${id.slice(-1)}`, origin: 'swarm-out', sent_at: '2026-11-03T08:00:00Z',
    state: 'running', state_reason: 'streaming', stage: 'running',
    actions: [{ key: 'stop', primary: true }, { key: 'open_session', primary: false }, { key: 'discard', primary: false }],
    project_agent: { brain: 'SwarmAI', mode: 'code-change', revision: 'r', profile: '', delivery: 'bundle', acceptance: 'a', returns: 'commits' },
    result: null, parent_run: RUN, wp: `W${id.slice(-1)}`, needs_person: false,
    ...over,
  } as SwarmOutRun;
}

const VIEW: SwarmRunView = {
  run_id: RUN, project: 'SwarmAI', title: 'Add the export command', profile: 'full', status: 'active', stage: 'build',
  children: 2, planned: 3, not_dispatched: [{ wp: 'W3', title: 'docs', after: ['W1', 'W2'] }], children_cost_usd: 1.25,
};

beforeEach(() => {
  vi.clearAllMocks();
  _resetDispatchPoll();
  svc.swarmRun.mockResolvedValue(VIEW);
  svc.runs.mockResolvedValue({
    runs: [
      child('so_000000000001'),
      // waiting for its lead run's take: not the person's
      child('so_000000000002', { state: 'stopped', state_reason: 'idle', stage: 'awaiting',
        actions: [{ key: 'open_session', primary: false }, { key: 'discard', primary: false }] }),
      child('so_000000000009', { parent_run: 'run_ffffffffffff', title: 'another run' }),
      child('so_00000000000a', { parent_run: null, title: 'an ordinary run' }),
    ],
    asks: [], answered: [],
  } as never);
});

describe('SwarmRunCard', () => {
  it('lists only this run’s children under the run’s head, the waiting one not as yours', async () => {
    render(<SwarmRunCard block={{ type: 'swarm_run', runId: RUN, project: 'SwarmAI' }} />);
    await screen.findByText('Swarm Run · Add the export command');
    expect(screen.getByTestId('swarm-run-meta').textContent).toContain('SwarmAI · lead run full · 2 children');
    expect(screen.getByTestId('swarm-run-meta').textContent).toContain('$1.25');
    await waitFor(() => expect(screen.getByText('W1 · part 1')).toBeTruthy());
    expect(screen.getByText('W2 · part 2')).toBeTruthy();
    expect(screen.queryByText('another run')).toBeNull();
    expect(screen.queryByText('an ordinary run')).toBeNull();
    const you = screen.getByTestId('swarm-out-section-you');
    expect(within(you).getByText('0')).toBeTruthy();
    expect(screen.queryByTestId('swarm-out-cmd-accept')).toBeNull();
    const pending = screen.getByTestId('swarm-run-pending');
    expect(within(pending).getByText('W3 · docs')).toBeTruthy();
    expect(within(pending).getByText('waits for W1, W2')).toBeTruthy();
  });

  it('stops the whole run from its head while a child runs', async () => {
    render(<SwarmRunCard block={{ type: 'swarm_run', runId: RUN, project: 'SwarmAI' }} />);
    fireEvent.click(await screen.findByTestId('swarm-run-stop-all'));
    await waitFor(() => expect(svc.stopSwarmRun).toHaveBeenCalledWith(RUN));
  });

  it('shows no Stop all once nothing runs', async () => {
    svc.runs.mockResolvedValue({ runs: [child('so_000000000002', { state: 'stopped', stage: 'accepted', why: 'swarm_run' })], asks: [], answered: [] } as never);
    render(<SwarmRunCard block={{ type: 'swarm_run', runId: RUN, project: 'SwarmAI' }} />);
    await screen.findByText('Swarm Run · Add the export command');
    await waitFor(() => expect(screen.getByText('W2 · part 2')).toBeTruthy());
    expect(screen.queryByTestId('swarm-run-stop-all')).toBeNull();
  });
});
