/**
 * The dispatch track on each Swarm Out run row (./track, RunList's FunnelTrack) and the
 * Hives above the list (design: the factory-line overlays,
 * Knowledge/Designs/2026-11-18-swarmai-factory-line-overlays-mockup.html).
 *
 * Questions:
 *  - does a row's track stand at the station its funnel and state say: ON HIVE while it
 *    runs there, RETURN / LAND / JUDGE once the funnel has a current gate, SENT before
 *    the Hive named its session, JUDGE once it ended;
 *  - is it amber only when the person has something to do, light purple while the
 *    dispatching agent holds it, and listed first when it is the person's;
 *  - do one Swarm Run's work packages sit together under one line;
 *  - does a Hive show as a row with its name, version, memory and load, without a fixed
 *    slot count;
 *  - does a click on a row still open its big card.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { SwarmOutAction, SwarmOutRun } from '../../services/swarmOut';
import i18n from '../../i18n';
import { RunList } from './RunList';
import { dispatchTrack } from './track';

beforeAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { stop: vi.fn(async () => undefined), result: vi.fn(() => new Promise(() => {})),
    runs: vi.fn(() => new Promise(() => {})), fleet: vi.fn(() => new Promise(() => {})) },
}));
vi.mock('./RunOverlay', () => ({ RunOverlay: ({ id }: { id: string }) => <div data-testid="run-card">{id}</div> }));
vi.mock('../../services/awsAccount', () => ({ awsAccountService: { get: () => Promise.resolve({ hive: null }) } }));

const NOW = Date.parse('2026-11-18T12:00:00Z');
const A = (key: SwarmOutAction['key'], primary = false): SwarmOutAction => ({ key, primary });
const gate = (key: 'review' | 'land' | 'judge', state: 'done' | 'now' | 'person') => ({ key, state, by: state === 'person' ? null : 'agent' as const, at: null, ref: null });

function run(over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_run', hive_id: 'h1', hive: 'explorer', session_id: 'hs1', title: 'A task', origin: 'swarm-out',
    sent_at: '2026-11-18T11:00:00Z', state: 'running', state_reason: 'streaming',
    project_agent: { brain: 'SwarmAI', mode: 'code', revision: 'r1', profile: '', delivery: 'bundle', acceptance: null },
    result: null, stage: 'running', actions: [A('stop'), A('open_session')], why: null,
    person_decisions: 0, needs_person: false,
    ...over,
  } as SwarmOutRun;
}

describe('the dispatch track', () => {
  it('stands where the funnel and the state say', () => {
    expect(dispatchTrack(run())).toEqual({ at: 'on_hive', kind: 'run' });
    expect(dispatchTrack(run({ session_id: null, session_unknown: true, state: 'unknown' }))).toEqual({ at: 'sent', kind: 'run' });
    const landing = run({ state: 'stopped', stage: 'awaiting', funnel: { gates: [gate('review', 'done'), gate('land', 'now')], current: 'land', ask: null, reruns: 0 } });
    expect(dispatchTrack(landing)).toEqual({ at: 'land', kind: 'judge' });
    const yours = run({ state: 'stopped', stage: 'awaiting', needs_person: true,
      funnel: { gates: [gate('review', 'done'), gate('land', 'done'), gate('judge', 'person')], current: 'judge', ask: null, reruns: 0 } });
    expect(dispatchTrack(yours)).toEqual({ at: 'judge', kind: 'you' });
    const ended = run({ state: 'stopped', stage: 'accepted',
      funnel: { gates: [gate('review', 'done'), gate('land', 'done'), gate('judge', 'done')], current: null, ask: null, reruns: 0 } });
    expect(dispatchTrack(ended)).toEqual({ at: 'judge', kind: 'done' });
    expect(dispatchTrack(run({ state: 'stopped', stage: 'stopped', needs_person: null, actions: [A('rerun', true)] }))).toEqual({ at: 'on_hive', kind: 'stop' });
  });

  it('each row shows its track; the person\'s one is amber and first', () => {
    const agent = run({ dispatch_id: 'so_agent', title: 'Agent lands it', state: 'stopped', stage: 'awaiting', actions: [A('open_session')],
      funnel: { gates: [gate('review', 'done'), gate('land', 'now')], current: 'land', ask: null, reruns: 0 } });
    const mine = run({ dispatch_id: 'so_mine', title: 'Mine to judge', state: 'stopped', stage: 'awaiting', needs_person: true,
      actions: [A('keep', true), A('revert')],
      funnel: { gates: [gate('review', 'done'), gate('land', 'done'), gate('judge', 'person')], current: 'judge',
        ask: { state: 'open', decide: 'Keep it?', judgment: 'ok', why: 'a tradeoff', landed: { commits: ['abc1234'], files: [] } }, reruns: 0 } });
    render(<RunList runs={[agent, mine]} asks={[]} now={NOW} onReload={vi.fn()} />);
    const rows = screen.getAllByTestId('swarm-out-row');
    expect(rows[0].getAttribute('data-id')).toBe('so_mine');                 // yours, on top
    const t0 = within(rows[0]).getByTestId('swarm-out-row-track');
    expect([t0.getAttribute('data-at'), t0.getAttribute('data-kind')]).toEqual(['judge', 'you']);
    expect(within(rows[0]).getByTestId('swarm-out-row-station').getAttribute('style')).toContain('--color-status-warning');
    const t1 = within(rows[1]).getByTestId('swarm-out-row-track');
    expect([t1.getAttribute('data-at'), t1.getAttribute('data-kind')]).toEqual(['land', 'judge']);
    // the agent's run: its label is the funnel word, in the agent's colour, said once
    expect(within(rows[1]).getByTestId('swarm-out-row-funnel').getAttribute('style')).toContain('--line-judge');
    expect(within(rows[1]).getByTestId('swarm-out-row-funnel').textContent).not.toBe('');
    fireEvent.click(rows[1]);
    expect(screen.getByTestId('run-card').textContent).toBe('so_agent');      // a click still opens its card
  });

  it('one label per fact: the funnel word once, the live stage named on its Hive', () => {
    const judging = run({ dispatch_id: 'so_j', state: 'stopped', stage: 'awaiting', why: 'judge', actions: [A('open_session')],
      funnel: { gates: [gate('review', 'done'), gate('land', 'done'), gate('judge', 'now')], current: 'judge', ask: null, reruns: 0 } });
    const live = run({ dispatch_id: 'so_l', live_stage: { run_id: 'run_x', stage: 'review', stages: [], at: null } });
    render(<RunList runs={[judging, live]} asks={[]} now={NOW} onReload={vi.fn()} />);
    const rows = screen.getAllByTestId('swarm-out-row');
    const j = rows.find((x) => x.getAttribute('data-id') === 'so_j')!;
    expect(within(j).getAllByTestId('swarm-out-row-funnel')).toHaveLength(1);
    expect(within(j).queryByTestId('swarm-out-row-station')).toBeNull();
    const l = rows.find((x) => x.getAttribute('data-id') === 'so_l')!;
    expect(within(l).getByTestId('swarm-out-row-station').textContent).toBe('REVIEW');
    expect(within(l).getByTestId('swarm-out-row-track').getAttribute('title')).toContain('ON HIVE · REVIEW');
  });

  it('one Swarm Run\'s work packages sit together under one line', () => {
    const a = run({ dispatch_id: 'so_a', title: 'W1', parent_run: 'run_lead', wp: 'W1', sent_at: '2026-11-18T11:03:00Z' });
    const other = run({ dispatch_id: 'so_x', title: 'Other', sent_at: '2026-11-18T11:02:00Z' });
    const b = run({ dispatch_id: 'so_b', title: 'W2', parent_run: 'run_lead', wp: 'W2', sent_at: '2026-11-18T11:01:00Z' });
    render(<RunList runs={[a, other, b]} asks={[]} now={NOW} onReload={vi.fn()} />);
    const list = screen.getByTestId('swarm-out-run-list');
    const order = Array.from(list.querySelectorAll('[data-testid="swarm-out-row"], [data-testid="swarm-out-swarm-run"]'))
      .map((e) => e.getAttribute('data-id') ?? 'lead');
    expect(order).toEqual(['lead', 'so_a', 'so_b', 'so_x']);
    expect(screen.getByTestId('swarm-out-swarm-run').textContent).toContain('2 work packages');
  });
});

describe('where tasks can run, in the header', () => {
  it('one pill per place: its name, EC2 or AgentCore, its load on hover, no slot count; a click opens the settings', async () => {
    const { FleetPills } = await import('./FleetPills');
    const opened = vi.fn();
    window.addEventListener('swarm:open-swarm-out-settings', opened);
    render(<FleetPills hidden failed={false} fleet={{ running: 1, hives: [
      { name: 'explorer', status: 'running', region: null, version: '2.3.0-hive8', instance_type: 'm7g.2xlarge',
        reachable: true, streaming: 2, saturated: false, memory_percent: 58, carrier: 'ec2' },
      { name: 'demo', status: 'running', region: null, version: '2.3.0', instance_type: 'm7g.xlarge',
        reachable: false, streaming: null, saturated: null, memory_percent: null, carrier: 'ec2' },
      { name: 'elastic', status: 'ready', region: null, version: '2.3.0', instance_type: 'agentcore',
        reachable: null, streaming: null, saturated: null, memory_percent: null, carrier: 'agentcore' },
    ] }} />);
    const pills = screen.getAllByTestId('so-fleet-pill');
    expect(pills.map((p) => [p.getAttribute('data-carrier'), p.getAttribute('data-tone')]))
      .toEqual([['ec2', 'ok'], ['ec2', 'error'], ['agentcore', 'ok']]);
    expect(pills[0].textContent).toContain('explorer');
    expect(pills[0].getAttribute('title')).toMatch(/2/);
    expect(pills[0].getAttribute('title')).toMatch(/58%/);
    expect(pills[2].textContent).toContain('Elastic Workers');
    expect(screen.getByTestId('so-fleet').textContent).not.toMatch(/\d+ of \d+ slots/);
    fireEvent.click(pills[1]);
    expect(opened).toHaveBeenCalledTimes(1);
    window.removeEventListener('swarm:open-swarm-out-settings', opened);
  });
});
