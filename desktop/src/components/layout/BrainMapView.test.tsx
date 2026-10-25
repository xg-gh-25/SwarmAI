/**
 * BrainMapView + BrainHubHome tests — the Map view of one Brain and the Hub home.
 * Services are mocked at the boundary (ddd hooks, pipeline analytics). Checks: the
 * map shows real counts and this brain's Runs only; clicking map parts opens the
 * matching panel or the Browse view; Needs you lists Runs + proposals; the Project
 * Agent submits to chat through onDispatch; unbuilt parts are labelled mock; Eval
 * says UNKNOWN for what nobody measures; the Hub home filters, queues and opens.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockAnalytics = vi.fn();
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: mockAnalytics() }),
}));
const DETAIL = {
  name: 'P', kind: 'knowledge',
  sections: [
    { key: 'knowledge', members: [], entries: [{}, {}], completeNotBroken: false },
    { key: 'delivery', members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
};
const REVIEW = {
  last_reviewed_sha: 'a', head_sha: 'abcdef1234', diff_incomplete: false,
  hunks: [{ file: 'Projects/P/K.md', signature: 's1', tag: 'cultivation·auto-applied', diff_text: '' }],
  proposals: [{ id: 'p1', target_doc: 'TECH.md', target_section: 'Gates', content: 'Add a concurrency reviewer', confidence: 0.8, source_run_id: 'run_x' }],
};
vi.mock('../../services/ddd', () => ({
  useBrainDetail: () => ({ data: DETAIL }),
  useReview: () => ({ data: REVIEW }),
}));
vi.mock('../../services/pipelines', () => ({ pipelinesService: { fetchAnalytics: vi.fn() } }));

import { BrainMapView } from './BrainMapView';
import { BrainHubHome } from './BrainHubHome';

const run = (id: string, o: Record<string, unknown> = {}) => ({
  id, requirement: `Req ${id}`, status: 'completed', profile: 'bugfix', progress: '8/8', cycleTimeMin: 10,
  tokensActual: null, createdAt: '', updatedAt: '2026-10-20', pauseKind: null, needsYou: false,
  checkpointReason: null, ownerSessionId: null, verified: true, reportPath: null, auditCard: null, ...o,
});

beforeEach(() => {
  mockAnalytics.mockReturnValue({
    window: '30d', overall: {}, trend: [],
    byProject: [
      { project: 'P', runs: [run('run_1', { needsYou: true, pauseKind: 'decision', status: 'running' }), run('run_2')] },
      { project: 'Other', runs: [run('run_9')] },
    ],
  });
});

describe('BrainMapView', () => {
  it('draws the map with real counts and only this brain\'s Runs', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('brain-map')).toBeTruthy();
    expect(screen.getByTestId('bm-sec-knowledge').textContent).toContain('2 entries');
    expect(screen.getByTestId('bm-brain').textContent).toContain('abcdef1');
    expect(screen.getByTestId('bm-run-run_1')).toBeTruthy();
    expect(screen.queryByTestId('bm-run-run_9')).toBeNull();
    expect(screen.getByTestId('bm-you').textContent).toContain('2');   // 1 Run + 1 proposal
  });

  it('Needs you lists the waiting Run and the proposal; Review goes to the Review view', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-run_1')).toBeTruthy();
    fireEvent.click(screen.getByTestId('bm-need-p1').querySelector('button')!);
    expect(onGoTo).toHaveBeenCalledWith('review');
  });

  it('clicking map parts opens the matching panel; a section opens Browse', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    fireEvent.click(screen.getByTestId('bm-conductor'));
    expect(screen.getByTestId('bm-panel-runs')).toBeTruthy();
    expect(screen.getByTestId('bm-runrow-run_2')).toBeTruthy();
    fireEvent.click(screen.getByTestId('bm-reflect'));
    expect(screen.getByTestId('bm-panel-reflect').textContent).toContain('Add a concurrency reviewer');
    fireEvent.click(screen.getByTestId('bm-eval'));
    expect(screen.getByTestId('bm-panel-eval').textContent).toContain('UNKNOWN');
    fireEvent.click(screen.getByTestId('bm-sec-gates'));
    expect(onGoTo).toHaveBeenCalledWith('browse');
  });

  it('the Project Agent submits a request to chat and marks unbuilt places as mock', async () => {
    const onDispatch = vi.fn(() => true);
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} />);
    fireEvent.click(screen.getByTestId('bm-agent'));
    expect(screen.getAllByTestId('bm-mock').length).toBeGreaterThan(0);
    const submit = screen.getByTestId('bm-agent-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId('bm-agent-req'), { target: { value: 'Add jittered backoff to retries' } });
    fireEvent.click(submit);
    expect(onDispatch).toHaveBeenCalledTimes(1);
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('Use the pipeline on P');
    await waitFor(() => expect(screen.getByTestId('bm-agent-sent').textContent).toContain('Sent to chat'));
  });
});

describe('BrainMapView · Project Agent definition', () => {
  const AGENT = {
    path: 'project-agent.yaml', status: 'draft', summary: 'Small fixes behind the service layer.',
    defaultProfile: 'bugfix', profileRules: [{ when: 'config-only change', profile: 'direct' }],
    reviewFocus: [{ area: 'concurrency', why: 'refunds settle asynchronously', source: 'TECH.md' }],
    validations: [{ id: 'unit', command: 'pytest -q', source: 'validations.json' }],
    placements: ['chat-tab'], landing: { approval: 'human', via: 'github-pr' }, sources: ['TECH.md'], problems: [],
  };
  const openAgent = (onDispatch = vi.fn(() => true)) => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} />);
    fireEvent.click(screen.getByTestId('bm-agent'));
    return onDispatch;
  };

  it('shows the generated definition with its sources, and confirm goes to chat', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    const onDispatch = openAgent();
    const def = screen.getByTestId('bm-agent-def');
    for (const s of ['bugfix', 'direct', 'concurrency', 'TECH.md', 'pytest -q', 'you approve every landing']) {
      expect(def.textContent).toContain(s);
    }
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('draft');
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('status: reviewed');
  });

  it('a revise note goes to chat; a reviewed definition has no confirm button', () => {
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, status: 'reviewed' };
    const onDispatch = openAgent();
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();
    fireEvent.change(screen.getByTestId('bm-agent-note'), { target: { value: 'use full for API changes' } });
    fireEvent.click(screen.getByTestId('bm-agent-revise'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('use full for API changes');
  });

  it('no definition yet offers to generate it from the DDD', () => {
    (DETAIL as Record<string, unknown>).projectAgent = null;
    const onDispatch = openAgent();
    fireEvent.click(screen.getByTestId('bm-agent-generate'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('P3b');
    delete (DETAIL as Record<string, unknown>).projectAgent;
  });
});

describe('BrainMapView · Continue setup and Add knowledge', () => {
  it('a Brain still setting up shows Continue setup, which sends the resume prompt and closes the Hub', () => {
    (DETAIL as Record<string, unknown>).setupState = 'setting-up';
    const onDispatch = vi.fn((_m: string, _o?: { autoSend?: boolean }) => true);
    const onRequestClose = vi.fn();
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} onRequestClose={onRequestClose} />);
    fireEvent.click(screen.getByTestId('bm-setup-resume'));
    expect(onDispatch.mock.calls[0][0]).toContain('Continue setting up the "P" Brain');
    expect(onDispatch.mock.calls[0][1]).toEqual({ autoSend: true });
    expect(onRequestClose).toHaveBeenCalled();
    delete (DETAIL as Record<string, unknown>).setupState;
  });

  it('a ready Brain has no Continue setup', () => {
    (DETAIL as Record<string, unknown>).setupState = 'ready';
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.queryByTestId('bm-setup-banner')).toBeNull();
    delete (DETAIL as Record<string, unknown>).setupState;
  });

  it('Add knowledge collects material by role and sends one routed prompt', () => {
    const onDispatch = vi.fn((_m: string, _o?: { autoSend?: boolean }) => true);
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={onDispatch} />);
    fireEvent.click(screen.getByTestId('bm-add-open'));
    const send = screen.getByTestId('bm-add-send') as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const input = screen.getByTestId('bm-add-material-input');
    for (const v of ['https://wiki.acme.dev/runbook', '/Users/me/notes/']) {
      fireEvent.change(input, { target: { value: v } });
      fireEvent.keyDown(input, { key: 'Enter' });
    }
    expect(screen.getByTestId('bm-add').textContent).toContain('1 to distill · 1 to shelve');
    fireEvent.click(send);
    const msg = onDispatch.mock.calls[0][0];
    expect(msg).toContain('Add knowledge to the "P" Brain');
    expect(msg).toContain('- https://wiki.acme.dev/runbook');
    expect(msg).toContain('s_library');
    // sent: the picker is cleared for the next batch
    expect(screen.getByTestId('bm-add-empty')).toBeTruthy();
  });

  it('when no chat tab takes it, the material stays and the reason is shown', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} onDispatch={() => false} />);
    fireEvent.click(screen.getByTestId('bm-add-open'));
    const input = screen.getByTestId('bm-add-material-input');
    fireEvent.change(input, { target: { value: '/Users/me/doc.md' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(screen.getByTestId('bm-add-send'));
    expect(screen.getByTestId('bm-add-failed')).toBeTruthy();
    expect(screen.queryByTestId('bm-add-empty')).toBeNull();
  });
});

describe('BrainHubHome', () => {
  const brain = (name: string, o: Record<string, unknown> = {}, h: Record<string, unknown> = {}) => ({
    name, kind: 'knowledge', lifecycleStage: 'GROW',
    sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: true, delivery: true, refresher: false },
    health: { sinking: 1, pending: 0, liveRun: false, lastChangeRelative: '--', ...h }, ...o,
  });
  // P: a Run needs you (analytics above). Q: a proposal. R: running. S: being born. T: quiet.
  const brains = [
    brain('T', { description: 'Quiet one', recentActivity: 0 }),
    brain('S', { setupState: 'setting-up', recentActivity: 2 }),
    brain('R', { recentActivity: 3 }, { liveRun: true }),
    brain('Q', { recentActivity: 0 }, { pending: 2 }),
    brain('P', { description: 'The SELF brain', recentActivity: 4 }),
  ];
  const order = () => screen.getAllByTestId(/^hub-card-[^a]/).map((e) => e.getAttribute('data-testid'));

  it('summary row: counts, learned + sinking totals; cards with something to do come first', () => {
    render(<BrainHubHome brains={brains as never} selfName="P" onOpen={vi.fn()} />);
    expect(screen.getByTestId('hub-filter-need').textContent).toContain('3');   // 1 Run + 2 proposals
    expect(screen.getByTestId('hub-filter-running').textContent).toContain('1');
    expect(screen.getByTestId('hub-filter-born').textContent).toContain('1');
    expect(screen.getByTestId('hub-sum-learned').textContent).toContain('+9');
    expect(screen.getByTestId('hub-sum-sinking').textContent).toContain('5');
    expect(order()).toEqual(['hub-card-P', 'hub-card-Q', 'hub-card-R', 'hub-card-S', 'hub-card-T']);
    expect(screen.getByTestId('hub-signal-P').textContent).toBe('A Run needs you');
    expect(screen.getByTestId('hub-card-P').querySelector('[data-testid="dddcard-self-tag"]')).toBeTruthy();
    expect(screen.getByTestId('hub-desc-Q').textContent).toContain('No description yet');
    expect(screen.getByTestId('hub-eval').textContent).toContain('1 of 1 finished Runs landed');
  });

  it('a count filters the cards; Need you also opens the queue, whose buttons open the right place', () => {
    const onOpen = vi.fn();
    render(<BrainHubHome brains={brains as never} onOpen={onOpen} />);
    expect(screen.queryByTestId('hub-needs')).toBeNull();
    fireEvent.click(screen.getByTestId('hub-filter-running'));
    expect(order()).toEqual(['hub-card-R']);
    fireEvent.click(screen.getByTestId('hub-filter-need'));
    expect(order()).toEqual(['hub-card-Q', 'hub-card-P']);   // same rank: incoming order
    fireEvent.click(screen.getByTestId('hub-need-go-run_1'));
    expect(onOpen).toHaveBeenLastCalledWith('P', { view: 'map', panel: 'need' });
    fireEvent.click(screen.getByTestId('hub-need-go-Q-proposals'));
    expect(onOpen).toHaveBeenLastCalledWith('Q', { view: 'review' });
  });

  it('cards open their brain; Add knowledge picks a brain and opens its Add panel; New Brain opens the form', () => {
    const onOpen = vi.fn();
    const shown: string[] = [];
    const listen = (e: Event) => shown.push(e.type);
    window.addEventListener('swarm:show-new-brain', listen);
    render(<BrainHubHome brains={brains as never} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('dddcard-T'));
    expect(onOpen).toHaveBeenLastCalledWith('T');
    fireEvent.click(screen.getByTestId('hub-card-add-T'));
    expect(onOpen).toHaveBeenLastCalledWith('T', { view: 'map', panel: 'add' });
    fireEvent.click(screen.getByTestId('hub-add-knowledge'));
    fireEvent.click(screen.getByTestId('hub-add-pick-S'));
    expect(onOpen).toHaveBeenLastCalledWith('S', { view: 'map', panel: 'add' });
    expect(screen.queryByTestId('hub-add-pick')).toBeNull();
    fireEvent.click(screen.getByTestId('hub-new-brain'));
    expect(shown).toEqual(['swarm:show-new-brain']);
    window.removeEventListener('swarm:show-new-brain', listen);
  });

  it('an old daemon without recentActivity shows LEARNED as UNKNOWN, not +0', () => {
    render(<BrainHubHome brains={[brain('T')] as never} onOpen={vi.fn()} />);
    expect(screen.getByTestId('hub-sum-learned').textContent).toContain('UNKNOWN');
  });
});

describe('BrainMapView initialPanel', () => {
  it('opens on the panel the Hub asked for', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} initialPanel="add" />);
    expect(screen.getByTestId('bm-add')).toBeTruthy();
  });
});
