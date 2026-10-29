/**
 * BrainMapView + BrainHubHome tests — the Map view of one Brain and the Hub home.
 * Services are mocked at the boundary (ddd hooks, pipeline analytics). Checks: the
 * map shows real counts and this brain's Runs only; clicking map parts opens the
 * matching panel or the Browse view; Needs you lists Runs + proposals; the Project
 * Agent submits to chat through onDispatch; unbuilt parts are labelled mock; Eval
 * says UNKNOWN for what nobody measures; the Hub home filters, queues and opens.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockAnalytics = vi.fn();
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: mockAnalytics() }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
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
const confirmProjectAgent = vi.fn();
const editProjectAgent = vi.fn();
vi.mock('../../services/ddd', () => ({
  useBrainDetail: () => ({ data: DETAIL }),
  useReview: () => ({ data: REVIEW }),
  useRunnerAgent: () => ({ data: null }),
  confirmProjectAgent: (...a: unknown[]) => confirmProjectAgent(...a),
  editProjectAgent: (...a: unknown[]) => editProjectAgent(...a),
  getStarterKind: () => Promise.resolve({ path: '', role: 'DISTILL', why: '', roots: [] }),
}));
vi.mock('../../services/pipelines', () => ({ pipelinesService: { fetchAnalytics: vi.fn() } }));

import { BrainMapView, BrainAgentView, BrainRunsView, BrainEvalView, BrainAddKnowledge, BrainNeedsView } from './BrainMapView';
import { BrainHubHome } from './BrainHubHome';

const run = (id: string, o: Record<string, unknown> = {}) => ({
  id, requirement: `Req ${id}`, status: 'completed', profile: 'bugfix', progress: '8/8', cycleTimeMin: 10,
  tokensActual: null, createdAt: '', updatedAt: '2026-10-20', pauseKind: null, needsYou: false,
  checkpointReason: null, ownerSessionId: null, notLanded: false, reportPath: null, auditCard: null, ...o,
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

  it('Needs you sits beside the map; Review opens Reflect · Evolution, and the Needs you tab lists the same', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-run_1')).toBeTruthy();
    fireEvent.click(screen.getByTestId('bm-need-p1').querySelector('button')!);
    expect(onGoTo).toHaveBeenCalledWith('reflect');
    expect(screen.getByTestId('bm-eval-summary').textContent).toContain('1 / 2');
    cleanup();
    render(<BrainNeedsView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-run_1')).toBeTruthy();
    expect(screen.getByTestId('bm-need-p1')).toBeTruthy();
  });

  it('an escalation for this Brain is listed and opens Need You; a Run opens on its own drawer', () => {
    (DETAIL as Record<string, unknown>).pendingBySource = { escalation: 1, cultivation: 1, paused_run: 1 };
    const shown: CustomEvent[] = [];
    const listen = (e: Event) => shown.push(e as CustomEvent);
    window.addEventListener('swarm:show-needs-you', listen);
    window.addEventListener('swarm:show-pipeline', listen);
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('bm-need-escalations').textContent).toContain('1 escalation to decide');
    fireEvent.click(screen.getByTestId('bm-need-escalations').querySelector('button')!);
    fireEvent.click(screen.getByTestId('bm-need-run_1').querySelector('button')!);
    expect(shown.map((e) => e.type)).toEqual(['swarm:show-needs-you', 'swarm:show-pipeline']);
    expect(shown[1].detail).toEqual({ runId: 'run_1' });
    window.removeEventListener('swarm:show-needs-you', listen);
    window.removeEventListener('swarm:show-pipeline', listen);
    delete (DETAIL as Record<string, unknown>).pendingBySource;
  });

  it('the map draws the unbuilt sources and lanes as mocks', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('bm-src-0').querySelector('[data-testid="bm-map-mock"]')).toBeNull();   // Chat tab is real
    expect(screen.getByTestId('bm-src-1').querySelector('[data-testid="bm-map-mock"]')).toBeTruthy(); // Slack
    expect(screen.getByTestId('bm-conductor').textContent).toContain('Local Worker and Hive are mocks');
  });

  it('each map part opens its tab', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    const go = (id: string) => { fireEvent.click(screen.getByTestId(id)); return onGoTo.mock.calls.at(-1)?.[0]; };
    expect(go('bm-you')).toBe('needs');
    expect(go('bm-brain')).toBe('brain');
    expect(go('bm-sec-gates')).toBe('brain');
    expect(go('bm-agent')).toBe('agent');
    expect(go('bm-src-1')).toBe('agent');
    expect(go('bm-conductor')).toBe('runs');
    expect(go('bm-reflect')).toBe('reflect');
    expect(go('bm-eval')).toBe('eval');
  });

  it('the map shows status dots and its arrows say what they mean', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    const map = screen.getByTestId('brain-map');
    for (const label of ['drafts, you submit', 'writes rules after your OK', 'writes knowledge', 'each Run uses this version']) {
      expect(map.textContent).toContain(label);
    }
    // a Run waits on you -> the Conductor dot says so; a proposal -> Gates and Reflect
    expect(screen.getByTestId('bm-conductor').querySelector('[data-tone]')?.getAttribute('data-tone')).toBe('needs');
    expect(screen.getByTestId('bm-sec-gates').querySelector('[data-tone]')?.getAttribute('data-tone')).toBe('needs');
    expect(screen.getByTestId('bm-reflect').querySelector('[data-tone]')?.getAttribute('data-tone')).toBe('needs');
  });

  it('Conductor · Runs and Eval tabs show this Brain only', () => {
    render(<BrainRunsView name="P" />);
    expect(screen.getByTestId('bm-runrow-run_2')).toBeTruthy();
    expect(screen.queryByTestId('bm-runrow-run_9')).toBeNull();
    cleanup();
    render(<BrainEvalView name="P" />);
    expect(screen.getByTestId('bm-panel-eval').textContent).toContain('UNKNOWN');
  });

  it('the Project Agent page submits a request to chat and shows no mock intake', async () => {
    const onDispatch = vi.fn(() => true);
    render(<BrainAgentView name="P" onDispatch={onDispatch} />);
    expect(screen.queryAllByTestId('bm-mock')).toHaveLength(0);
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

describe('BrainMapView · Project Agent form', () => {
  const field = (o: Record<string, unknown>) => ({
    help: 'what it is for', kindLabel: { edit: 'Editable', brain: 'From the Brain', you: 'Your choice', fixed: 'Fixed' }[o.kind as string],
    required: false, removable: false, editable: o.kind !== 'fixed' && o.shape !== 'view', example: '', columns: [], scope: 'shared', ...o,
  });
  const FORM = {
    sections: [
      { id: 'identity', title: 'Identity', purpose: 'Who it is.', fields: [
        field({ key: 'summary', label: 'Summary', kind: 'edit', shape: 'text', required: true, value: 'Small fixes.', path: 'identity.summary' }),
        field({ key: 'repos', label: 'Repositories', kind: 'brain', shape: 'view', editable: false,
          value: [{ name: 'lib', local: '~/src/lib', remote: 'https://github.com/acme/lib.git' }] })] },
      { id: 'modes', title: 'Modes', purpose: 'Kinds of work.', fields: [
        field({ key: 'when', label: 'Used when', kind: 'edit', shape: 'text', required: true, scope: 'mode',
          values: { 'code-change': 'a fix' }, paths: { 'code-change': 'modes[code-change].when' } }),
        field({ key: 'mode_repos', label: 'Repositories', kind: 'brain', shape: 'names', scope: 'mode', editable: false,
          values: { 'code-change': ['lib'] }, paths: { 'code-change': 'modes[code-change].repos' } }),
        field({ key: 'validations', label: 'Checks a run must pass', kind: 'brain', shape: 'rows', removable: true, scope: 'mode',
          columns: ['id', 'command', 'cwd', 'source'],
          values: { 'code-change': [{ id: 'unit', command: 'pytest', cwd: '' }, { id: 'lint', command: 'ruff', cwd: '' }] },
          paths: { 'code-change': 'modes[code-change].validations' } })] },
      { id: 'guardrails', title: 'Guardrails', purpose: 'Limits.', fields: [
        field({ key: 'reserved_for_human', label: 'Always ask me before', kind: 'you', shape: 'names', required: true,
          value: ['push'], path: 'reserved_for_human', choices: [{ value: 'push', label: 'Push to a remote' }, { value: 'open_pr', label: 'Open a pull request' }] })] },
      { id: 'intake', title: 'Intake', purpose: 'How work comes in.', fields: [
        field({ key: 'handles', label: 'Typical requests', kind: 'edit', shape: 'list', required: true, value: ['fix a bug'], path: 'intake.handles' })] },
      { id: 'profiles', title: 'Pipeline profiles', purpose: 'Reference.', fields: [
        field({ key: 'profiles', label: 'Profiles', kind: 'fixed', shape: 'view', editable: false,
          value: [{ name: 'bugfix', for: 'a defect', stages: ['build', 'test'], modes: ['code-change'] }] })] },
    ],
    modes: [{ id: 'code-change', standard: true, isDefault: true, placements: ['chat-tab'], ownPlacements: false }],
    missing: [], skeleton: false, enforced: 'The guardrails of this definition are enforced when the agent runs on a Hive.',
  };
  const AGENT = {
    path: 'project-agent.yaml', status: 'draft', revision: 'r1', summary: 'Small fixes.',
    identity: { summary: 'Small fixes.', inScope: [], outOfScope: [], source: 'TECH.md' },
    problems: [], form: FORM,
  };
  const openAgent = (onDispatch = vi.fn(() => true)) => {
    render(<BrainAgentView name="P" onDispatch={onDispatch} />);
    return onDispatch;
  };
  beforeEach(() => { confirmProjectAgent.mockReset(); editProjectAgent.mockReset(); });

  it('shows the backend\'s sections in order, each field with who may change it', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    openAgent();
    const ids = screen.getAllByTestId(/^pa-section-/).map((el) => el.getAttribute('data-testid'));
    expect(ids).toEqual(['pa-section-identity', 'pa-section-modes', 'pa-section-guardrails', 'pa-section-intake', 'pa-section-profiles']);
    expect(screen.getByTestId('pa-field-summary').textContent).toContain('Editable');
    expect(screen.getByTestId('pa-field-repos').textContent).toContain('From the Brain');
    expect(screen.getByTestId('pa-repos').textContent).toContain('github.com/acme/lib');
    expect(screen.getByTestId('pa-enforced').textContent).toContain('on a Hive');
  });

  it('saves the changes in one request, rows dropped highest first, then confirms', async () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    editProjectAgent.mockResolvedValue({ revision: 'r2', status: 'draft' });
    confirmProjectAgent.mockResolvedValue({ revision: 'r3', reviewedAt: 't', reviewedBy: 'me', aliasesAdded: [], sourceCheck: { ok: true, missing: [] } });
    const onDispatch = openAgent();
    fireEvent.change(screen.getByTestId('pa-input-identity.summary'), { target: { value: 'Fixes to the signer.' } });
    const rows = screen.getByTestId('pa-rows-modes[code-change].validations');
    for (const b of Array.from(rows.querySelectorAll('button[title="Remove"]'))) fireEvent.click(b);
    expect(screen.getByTestId('pa-count').textContent).toBe('3 changes');
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();          // save first
    fireEvent.click(screen.getByTestId('pa-save'));
    await waitFor(() => expect(editProjectAgent).toHaveBeenCalledWith('P', 'r1', [
      { path: 'identity.summary', value: 'Fixes to the signer.' },
      { path: 'modes[code-change].validations', remove: 1 },
      { path: 'modes[code-change].validations', remove: 0 }]));
    fireEvent.click(screen.getByTestId('bm-agent-confirm'));
    await waitFor(() => expect(confirmProjectAgent).toHaveBeenCalledWith('P', 'r1'));
    expect(onDispatch).not.toHaveBeenCalled();
  });

  it('cannot confirm while a required field is empty', () => {
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, form: { ...FORM, missing: ['Typical requests'] } };
    openAgent();
    expect((screen.getByTestId('bm-agent-confirm') as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('pa-missing').textContent).toContain('Typical requests');
  });

  it('a new revision starts the form afresh', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    const { rerender } = render(<BrainAgentView name="P" onDispatch={vi.fn(() => true)} />);
    fireEvent.change(screen.getByTestId('pa-input-identity.summary'), { target: { value: 'x' } });
    expect(screen.getByTestId('pa-count')).toBeTruthy();
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, revision: 'r9' };
    rerender(<BrainAgentView name="P" onDispatch={vi.fn(() => true)} />);
    expect(screen.queryByTestId('pa-count')).toBeNull();
  });

  it('a revise note goes to chat; a confirmed definition has no confirm button', () => {
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, status: 'reviewed' };
    const onDispatch = openAgent();
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('confirmed');
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
    expect(calls[0][0]).toContain('project-agent-draft');
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
    render(<BrainAddKnowledge name="P" onDispatch={onDispatch} />);
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
    render(<BrainAddKnowledge name="P" onDispatch={() => false} />);
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
  // P: a Run needs you (analytics above, and the attention channel's paused_run).
  // Q: two rule proposals and an escalation. R: running. S: being born. T: quiet.
  const brains = [
    brain('T', { description: 'Quiet one', recentActivity: 0 }),
    brain('S', { setupState: 'setting-up', recentActivity: 2 }),
    brain('R', { recentActivity: 3 }, { liveRun: true }),
    brain('Q', { recentActivity: 0, pendingBySource: { cultivation: 2, escalation: 1 } }, { pending: 3 }),
    brain('P', { description: 'The SELF brain', recentActivity: 4, pendingBySource: { paused_run: 1 } }, { pending: 1 }),
  ];
  const order = () => screen.getAllByTestId(/^hub-card-[^a]/).map((e) => e.getAttribute('data-testid'));

  it('summary row: counts, learned + sinking totals; cards with something to do come first', () => {
    render(<BrainHubHome brains={brains as never} selfName="P" onOpen={vi.fn()} />);
    // each item once: P's Run (counted by the attention channel, not again from the
    // analytics) + Q's 2 proposals + Q's escalation
    expect(screen.getByTestId('hub-filter-need').textContent).toContain('4');
    expect(screen.getByTestId('hub-filter-running').textContent).toContain('1');
    expect(screen.getByTestId('hub-filter-born').textContent).toContain('1');
    expect(screen.getByTestId('hub-sum-learned').textContent).toContain('+9');
    expect(screen.getByTestId('hub-sum-sinking').textContent).toContain('5');
    expect(order()).toEqual(['hub-card-P', 'hub-card-Q', 'hub-card-R', 'hub-card-S', 'hub-card-T']);
    expect(screen.getByTestId('hub-signal-P').textContent).toBe('A Run needs you');
    expect(screen.getByTestId('hub-card-P').querySelector('[data-testid="dddcard-self-tag"]')).toBeTruthy();
    expect(screen.getByTestId('hub-desc-Q').textContent).toContain('No description yet');
    expect(screen.getByTestId('hub-signal-Q').textContent).toBe('2 proposals to review');
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
    const shown: CustomEvent[] = [];
    const listen = (e: Event) => shown.push(e as CustomEvent);
    window.addEventListener('swarm:show-pipeline', listen);
    window.addEventListener('swarm:show-needs-you', listen);
    fireEvent.click(screen.getByTestId('hub-need-go-run_1'));            // that Run's drawer
    expect(shown[0].type).toBe('swarm:show-pipeline');
    expect(shown[0].detail).toEqual({ runId: 'run_1' });
    fireEvent.click(screen.getByTestId('hub-need-go-Q-proposals'));
    expect(onOpen).toHaveBeenLastCalledWith('Q', { view: 'review' });
    expect(screen.getByTestId('hub-need-Q-escalations').textContent).toContain('1 escalation to decide');
    fireEvent.click(screen.getByTestId('hub-need-go-Q-escalations'));    // Need You overlay
    expect(shown[1].type).toBe('swarm:show-needs-you');
    window.removeEventListener('swarm:show-pipeline', listen);
    window.removeEventListener('swarm:show-needs-you', listen);
  });

  it('a Project Agent draft is a Need-you item that opens the Project Agent panel', () => {
    const onOpen = vi.fn();
    render(<BrainHubHome brains={[brain('T', { projectAgentStatus: 'draft' })] as never} onOpen={onOpen} />);
    expect(screen.getByTestId('hub-filter-need').textContent).toContain('1');
    expect(screen.getByTestId('hub-signal-T').textContent).toBe('Project Agent to confirm');
    fireEvent.click(screen.getByTestId('hub-filter-need'));
    fireEvent.click(screen.getByTestId('hub-need-go-T-agent'));
    expect(onOpen).toHaveBeenLastCalledWith('T', { view: 'map', panel: 'agent' });
  });

  it('a whole card opens its brain; Add knowledge picks a brain and opens its Add panel; New Brain opens the form', () => {
    const onOpen = vi.fn();
    const shown: string[] = [];
    const listen = (e: Event) => shown.push(e.type);
    window.addEventListener('swarm:show-new-brain', listen);
    render(<BrainHubHome brains={brains as never} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('dddcard-T'));
    expect(onOpen).toHaveBeenLastCalledWith('T');
    expect(screen.queryByTestId('hub-card-add-T')).toBeNull();   // no hidden hover action on a card
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

