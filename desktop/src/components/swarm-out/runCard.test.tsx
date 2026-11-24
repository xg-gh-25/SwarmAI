/**
 * The run card redesign (Knowledge/Designs/2026-11-18-swarm-out-run-card-redesign-mockup.html).
 *
 * Questions:
 *  - is running painted with a fixed status blue, never the accent, in both themes;
 *  - does a running card lead with the dispatch track (ON HIVE marked), say what the
 *    agent is doing as plain text, show what it was sent as (the dispatching agent's
 *    summary, never labelled as the person's ask) and the acceptance as one row per
 *    clause, with no Open session in the header;
 *  - does a pipeline run name its stage in ON HIVE and show its stage strip;
 *  - are only the tabs that hold something drawn, a running run's work so far included;
 *  - does the command panel say what is needed first, keep stop outlined (never solid),
 *    make accept the solid green and discard a link that still opens its confirmation.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { SwarmOutAction, SwarmOutRun } from '../../services/swarmOut';
import { RunOverlay } from './RunOverlay';
import { TONE_VAR } from './tone';
import { acceptanceClauses, visibleTabs } from './runCardRules';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    accept: vi.fn(async () => undefined), manualLanded: vi.fn(async () => undefined),
    rerun: vi.fn(async () => undefined), stop: vi.fn(async () => undefined),
    discard: vi.fn(async () => ({ discarded: true, stopped: true })),
    answerAsk: vi.fn(async () => undefined),
    result: vi.fn(() => new Promise(() => {})), answer: vi.fn(),
    swarmRun: vi.fn(async () => { throw new Error('not read'); }),
    returnPrepare: vi.fn(), returnLand: vi.fn(), refresh: vi.fn(),
  },
}));

const NOW = Date.parse('2026-11-18T15:04:00Z');
const A = (key: SwarmOutAction['key'], primary = false, variant?: SwarmOutAction['variant']): SwarmOutAction =>
  ({ key, primary, ...(variant ? { variant } : {}) });
const ACCEPTANCE = 'The runner, scoring and compare subcommands land as commits with tests; every S1 candidate has an outcome;  ; report.md gives recall per arm';

function running(over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_0a1511c01151', hive_id: 'h1', hive: 'xg-swarm-remote-demo', session_id: 'hs1',
    title: '评审专家 benchmark phase 1 全做', origin: 'swarm-out', sent_at: '2026-11-18T14:37:12Z',
    state: 'running', state_reason: 'streaming', result: null, result_status: 'pending',
    project_agent: { brain: 'SwarmAI', mode: 'code-change', revision: 'r1', profile: '', acceptance: ACCEPTANCE,
      returns: 'commits', execution: 'native' },
    repos: [{ url: 'https://github.com/x/SwarmAI.git', ref: 'main', dir: 'Hive/work/x', sha: '891178117894778f', name: 'swarmai' }],
    brief: '确认全部 S1 案例，建好 runner 和打分，跑 30 个案例 × 3 次，出 recall 报告。',
    timings: [{ step: 'choosing a Hive', seconds: 1.0 }, { step: 'starting the session', seconds: 14.3 }],
    live_now: { text: 'Writing the compare subcommand next.', tool: 'Edit', object: 'backend/scripts/compare.py',
      checked_at: '2026-11-18T15:02:00Z' },
    live_stage: null,
    stage: 'running', actions: [A('stop', true), A('open_session'), A('discard')], why: null,
    settle: { code: 'none', why_not: ['verdict'], settled: null },
    funnel: { gates: [], current: null, ask: null, reruns: 0 }, person_decisions: 0, needs_person: false,
    ...over,
  } as SwarmOutRun;
}

const card = (r: SwarmOutRun) => render(<RunOverlay runs={[r]} asks={[]} order={[r.dispatch_id]} id={r.dispatch_id} now={NOW}
  onSelect={vi.fn()} onSettled={vi.fn()} onClose={vi.fn()} onFileOpened={vi.fn()} onReload={vi.fn()} />);

describe('the running tone', () => {
  it('running tone is a fixed status token in both themes', () => {
    expect(TONE_VAR.busy).toBe('var(--color-status-running)');
    expect(TONE_VAR.busy).not.toContain('--color-primary');
    const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');
    const light = css.slice(css.indexOf(':root {'), css.indexOf(':root.dark {'));
    const dark = css.slice(css.indexOf(':root.dark {'));
    expect(light).toMatch(/--color-status-running:\s*#[0-9a-f]{6}/i);
    expect(dark).toMatch(/--color-status-running:\s*#[0-9a-f]{6}/i);
    // no accent rule re-points it
    expect(css).not.toMatch(/data-accent[^{]*\{[^}]*--color-status-running/);
  });
});

describe('the rules', () => {
  it('splits the acceptance into its clauses, each unchanged', () => {
    expect(acceptanceClauses(ACCEPTANCE)).toEqual([
      'The runner, scoring and compare subcommands land as commits with tests',
      'every S1 candidate has an outcome', 'report.md gives recall per arm']);
    expect(acceptanceClauses('one clause')).toEqual(['one clause']);
    expect(acceptanceClauses(null)).toEqual([]);
  });
});

describe('a running card', () => {
  it('a running card leads with the track, the now line, the ask and the criteria', () => {
    card(running());
    const hero = screen.getByTestId('swarm-out-card-track');
    expect(hero.dataset.at).toBe('on_hive');
    expect(hero.dataset.kind).toBe('run');
    const stations = within(hero).getAllByTestId('swarm-out-card-station');
    expect(stations.map((s) => s.dataset.state)).toEqual(['passed', 'here', 'ahead', 'ahead', 'ahead']);
    expect(stations[0].textContent).toContain('delivered · dispatch took 15s');   // the dispatch steps come before delivery
    expect(stations[1].textContent).toContain('native');
    expect(screen.getByTestId('swarm-out-card-now-text').textContent).toBe('Writing the compare subcommand next.');
    expect(screen.getByTestId('swarm-out-card-now-tool').textContent).toBe('Editbackend/scripts/compare.py');
    expect(screen.getByTestId('swarm-out-card-now').textContent).toContain('read 2m');
    // the dispatching agent's summary, labelled as that (never as the person's words)
    expect(screen.getByTestId('swarm-out-card-ask').textContent).toContain('Sent as');
    expect(screen.getByTestId('swarm-out-card-ask').textContent).not.toContain('Your ask');
    expect(screen.getByTestId('swarm-out-card-ask').textContent).toContain('确认全部 S1 案例');
    expect(screen.getAllByTestId('swarm-out-card-criterion').map((x) => x.textContent))
      .toEqual(['1The runner, scoring and compare subcommands land as commits with tests', '2every S1 candidate has an outcome', '3report.md gives recall per arm']);
    // the header: one pill, the repo on the meta line, no Open session
    expect(screen.getByTestId('swarm-out-review-pill').textContent).toContain('Running');
    expect(screen.queryByTestId('swarm-out-review-open')).toBeNull();
    expect(screen.getAllByTestId('swarm-out-cmd-open_session')).toHaveLength(1);
  });

  it('the Hive\'s words are text, never markup', () => {
    card(running({ live_now: { text: '<img src=x onerror=alert(1)> **bold** [link](http://e.x)', tool: null, object: null, checked_at: '2026-11-18T15:02:00Z' } }));
    const text = screen.getByTestId('swarm-out-card-now-text');
    expect(text.textContent).toBe('<img src=x onerror=alert(1)> **bold** [link](http://e.x)');
    expect(text.querySelector('img, a, strong')).toBeNull();
    expect(screen.queryByTestId('swarm-out-card-now-tool')).toBeNull();
  });

  it('no now line when nothing was read, or once the run stopped', () => {
    card(running({ live_now: null }));
    expect(screen.queryByTestId('swarm-out-card-now')).toBeNull();
  });

  it('a pipeline run names its stage in ON HIVE and shows its stages', () => {
    card(running({ live_stage: { run_id: 'run_x', stage: 'build', stages: ['evaluate', 'plan', 'build', 'review'], at: null } }));
    expect(screen.getAllByTestId('swarm-out-card-station')[1].textContent).toContain('ON HIVE · BUILD');
    const strip = screen.getByTestId('swarm-out-card-stages');
    expect([...strip.children].map((c) => (c as HTMLElement).dataset.state)).toEqual(['done', 'done', 'now', 'ahead']);
  });
});

describe('the tabs', () => {
  it('only tabs that hold something are shown', () => {
    card(running());
    const tabs = screen.getAllByTestId(/^swarm-out-review-tab-[a-z]+$/).map((x) => x.dataset.testid);
    expect(tabs).toEqual(['swarm-out-review-tab-timings']);
    fireEvent.click(screen.getByTestId('swarm-out-review-tab-timings'));
    expect(screen.getByTestId('swarm-out-review-timings').textContent).toContain('choosing a Hive');
    const r = running();
    expect(visibleTabs(r, null, 2)).toEqual(['autonomy', 'timings']);
    expect(visibleTabs({ ...r, stage: 'awaiting', timings: [] }, null, null)).toEqual(['deliverable']);
    const res = { work: { steps: { total: 4 } }, lessons: { returned: 1 } } as unknown as NonNullable<SwarmOutRun['result']>;
    expect(visibleTabs({ ...r, stage: 'awaiting', timings: [] }, res, 0)).toEqual(['deliverable', 'autonomy', 'process', 'cost', 'lessons']);
    expect(visibleTabs({ ...r, stage: 'awaiting', timings: [] }, { ...res, work: { steps: { total: 0 } }, lessons: { returned: 0 } } as never, 0))
      .toEqual(['deliverable', 'autonomy', 'cost']);
    // running with work already back: what came back so far stays one click away
    const some = (work: object, report?: string) => ({ work: { steps: { total: 0 }, files: [], repos: [], ...work }, lessons: { returned: 0 }, report }) as never;
    expect(visibleTabs(r, some({ repos: [{ commits_total: 2 }] }), 0)).toContain('deliverable');
    expect(visibleTabs(r, some({ files: [{ path: 'a.md' }] }), 0)).toContain('deliverable');
    expect(visibleTabs(r, some({}, 'a reply so far'), 0)).toContain('deliverable');
    expect(visibleTabs(r, some({ repos: [{ commits_total: 0 }] }), 0)).not.toContain('deliverable');
  });
});

describe('the command panel', () => {
  it('the rail says what is needed and weighs its commands', () => {
    card(running());
    const state = screen.getByTestId('swarm-out-rail-state');
    expect(state.dataset.kind).toBe('run');
    expect(state.textContent).toContain('Nothing needed from you');
    const stop = screen.getByTestId('swarm-out-cmd-stop');
    expect(stop.className).not.toContain('btn-danger');
    expect(stop.className).not.toContain('btn-primary');
    expect(stop.getAttribute('style') ?? '').toContain('transparent');
    // discard is a link at the bottom, and still opens its confirmation
    const discard = screen.getByTestId('swarm-out-cmd-discard');
    expect(discard.className).not.toMatch(/btn-/);
    fireEvent.click(discard);
    expect(screen.getByTestId('swarm-out-review-discard')).toBeTruthy();
  });

  it('back and waiting on you: accept is the one solid green, the state says it is yours', () => {
    card(running({ state: 'stopped', state_reason: 'idle', stage: 'awaiting', needs_person: true, live_now: null,
      actions: [A('accept', true, 'verdict'), A('rerun'), A('open_session'), A('discard')],
      funnel: { gates: [{ key: 'review', state: 'done', by: 'agent', at: '2026-11-18T15:00:00Z', ref: null },
        { key: 'land', state: 'person', by: null, at: null, ref: null }], current: 'land', ask: null, reruns: 0 } }));
    const state = screen.getByTestId('swarm-out-rail-state');
    expect(state.dataset.kind).toBe('you');
    expect(state.textContent).toContain('Your turn');
    expect(screen.getByTestId('swarm-out-cmd-accept').getAttribute('style') ?? '').toContain('var(--color-status-success)');
    expect(screen.queryByTestId('swarm-out-card-now')).toBeNull();
    const stations = screen.getAllByTestId('swarm-out-card-station');
    expect(stations.map((x) => x.dataset.state)).toEqual(['passed', 'passed', 'passed', 'here', 'ahead']);
    expect(screen.getByTestId('swarm-out-card-track').dataset.kind).toBe('you');
  });
});
