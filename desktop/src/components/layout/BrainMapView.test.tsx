/**
 * BrainMapView + BrainHubHome tests — the Map view of one Brain and the Hub home.
 * Services are mocked at the boundary (ddd hooks). Checks: the map shows real counts
 * and no Run (a Brain holds knowledge only: no Conductor, no run dots, no sources);
 * clicking map parts opens the matching panel or the Browse view; Needs you lists the
 * Brain's own items and never a Run; the Project Agent submits to chat through
 * onDispatch; the needs count is one count; Eval shows two numbers and two sections,
 * "not measured" / "too few (n)" instead of a number nobody has; a local Run's card has
 * the Hive card's cells; the Hub home queues and opens, without Runs.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: undefined }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));
const DETAIL = {
  name: 'P', kind: 'knowledge',
  sections: [
    { key: 'knowledge', members: [], entries: [{}, {}], completeNotBroken: false },
    { key: 'delivery', members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
};
const KNOWLEDGE = { brain: 'P', since: 'week', layers: [], gaps: [], counts: { week: 1, all: 4 },
  withdrawn: { count: 0, items: [] } };
const EVAL = {
  project: 'P', computed_at: '2026-11-01T04:00:00Z', window_days: 30,
  health: {
    target: 0.9, min_sample: 10, rate: 0.8919, scored: 37, since: '2026-10-29T08:34:00Z',
    counts: { useful: 33, weak: 4, withdraw: 0 },
    flagged: [{ unit_id: 'cand-w.unit', dest: 'DDD', title: 'A weak entry', verdict: 'weak', klass: 'lookup',
      reason_en: 'only names one commit', reason_zh: '只绑定到一次提交', by_person: false,
      rel: 'Projects/P/2-understanding/TECH.md', line: 7 }],
    flagged_total: 1, entries: 1931,
    duplicates: { groups: 0, extra: 0, shown: [] },
    gaps: { open: 93, oldest_days: 2, oldest_title: 'Old gap' },
  },
  used: {
    min_sample: 10, runs: 8, citing: 3, rate: null, since: '2026-10-25T00:00:00Z',
    shown: [{ run_id: 'run_c', created_at: '2026-10-31T00:00:00Z', requirement: 'Fix the cap',
      cited: [{ id: 'abcd1234', head: 'Caps count bytes', rel: 'Projects/P/2-understanding/TECH.md', line: 3 }], other_refs: 1 }],
    missed: [{ unit_id: 'u1', run_id: 'run_m', at: '2026-10-31T00:00:00Z', title: 'Same as an entry' }], missed_total: 1,
    written_used: { rate: null, mature_written: 0, mature_used: 0, young_written: 1344, window_days: 14 },
  },
};
let evalData: unknown = EVAL;
vi.mock('../../services/ddd', () => ({
  useBrainDetail: () => ({ data: DETAIL }),
  useBrainKnowledge: () => ({ data: KNOWLEDGE }),
  useBrainEval: () => ({ data: evalData, isError: false }),
  useRunnerAgent: () => ({ data: null }),
  getStarterKind: () => Promise.resolve({ path: '', role: 'DISTILL', why: '', roots: [] }),
}));

import { BrainMapView, BrainAgentView, BrainEvalView, BrainAddKnowledge, BrainNeedsView } from './BrainMapView';
import { BrainHubHome } from './BrainHubHome';


beforeEach(() => {
  evalData = EVAL;
  (DETAIL as Record<string, unknown>).pendingBySource = { paused_run: 1, escalation: 1 };
});

describe('BrainMapView', () => {
  it('draws the map with real counts and no Run: no Conductor, no sources, no run dots', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('brain-map')).toBeTruthy();
    expect(screen.getByTestId('bm-sec-knowledge').textContent).toContain('2 entries');
    expect(screen.getByTestId('bm-brain').textContent).toContain('DDD');   // no git version: a Brain need not be in git
    expect(screen.queryByTestId('bm-conductor')).toBeNull();
    expect(screen.queryByTestId('bm-src-chat')).toBeNull();
    expect(screen.queryByTestId('bm-src-hive')).toBeNull();
    expect(document.querySelector('[data-testid^="bm-run-"]')).toBeNull();
    // the escalation is the Brain's; the waiting Run is not
    expect(screen.getByTestId('bm-you').textContent).toContain('1');
    expect(screen.getByTestId('bm-you').getAttribute('aria-label')).toBe('You: 1 need you');
  });

  it('Needs you sits beside the map with the Brain\'s own items, and the Needs you tab lists the same', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-escalations')).toBeTruthy();
    expect(screen.queryByTestId('bm-need-runs-unlisted')).toBeNull();
    expect(screen.getByTestId('bm-eval-summary').textContent).toContain('89%');
    expect(screen.getByTestId('bm-eval-summary').textContent).toContain('3 / 8');
    cleanup();
    render(<BrainNeedsView name="P" onGoTo={onGoTo} />);
    expect(screen.getByTestId('bm-need-escalations')).toBeTruthy();
  });

  it('an escalation for this Brain opens Need You', () => {
    const shown: string[] = [];
    const listen = (e: Event) => shown.push(e.type);
    window.addEventListener('swarm:show-needs-you', listen);
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('bm-need-escalations').textContent).toContain('1 escalation to decide');
    fireEvent.click(screen.getByTestId('bm-need-escalations').querySelector('button')!);
    expect(shown).toEqual(['swarm:show-needs-you']);
    window.removeEventListener('swarm:show-needs-you', listen);
  });

  it('one needs count: a newly read repository counts on the map and in the panel, a waiting Run does not', () => {
    (DETAIL as Record<string, unknown>).pendingBySource = { paused_run: 1, escalation: 1, local_root: 1 };
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    expect(screen.getByTestId('bm-need-count').textContent).toBe('2');   // the escalation, the repository
    expect(screen.getByTestId('bm-you').textContent).toContain('2');
    expect(screen.getByTestId('bm-need-repos').textContent).toContain('1 repository newly read');
  });

  it('each map part opens its tab', () => {
    const onGoTo = vi.fn();
    render(<BrainMapView name="P" onGoTo={onGoTo} />);
    const go = (id: string) => { fireEvent.click(screen.getByTestId(id)); return onGoTo.mock.calls.at(-1)?.[0]; };
    expect(go('bm-you')).toBe('needs');
    expect(go('bm-brain')).toBe('brain');
    expect(go('bm-sec-gates')).toBe('brain');
    expect(go('bm-agent')).toBe('agent');
    expect(go('bm-reflect')).toBe('reflect');
    expect(go('bm-eval')).toBe('eval');
  });

  it('the map shows status dots, its arrows say what they mean, and Eval its health', () => {
    render(<BrainMapView name="P" onGoTo={vi.fn()} />);
    const map = screen.getByTestId('brain-map');
    for (const label of ['reads', 'writes rules after your OK', 'writes knowledge']) expect(map.textContent).toContain(label);
    expect(map.textContent).not.toContain('each Run uses this version');
    expect(screen.getByTestId('bm-sec-gates').querySelector('[data-tone]')?.getAttribute('data-tone')).toBe('attention');
    expect(screen.getByTestId('bm-reflect').querySelector('[data-tone]')?.getAttribute('data-tone')).toBe('ok');
    expect(screen.getByTestId('bm-reflect').textContent).toContain('1 learned this week');
    expect(screen.getByTestId('bm-eval').textContent).toContain('89% useful');
  });

  it('Eval: two numbers on top, then the two questions; no run statistics', () => {
    const open = vi.fn();
    render(<BrainEvalView name="P" onOpenFile={open} />);
    const top = screen.getByTestId('bm-eval-top-health');
    expect(top.textContent).toContain('89%');
    expect(top.textContent).toContain('target 90% · 37 scored, since 10-29');
    expect(screen.getByTestId('bm-eval-top-used').textContent).toContain('3 / 8');
    expect(screen.getByTestId('bm-eval-top-used').textContent).toContain('too few to rate (8)');
    expect(screen.getByTestId('bm-eval-gaps').textContent).toContain('93 · oldest 2 d');
    expect(screen.getByTestId('bm-eval-written-used').textContent).toContain('nothing written 14 days ago yet');
    // an entry to handle opens its file in the Canvas
    fireEvent.click(screen.getByTestId('bm-eval-flag-cand-w.unit'));
    expect(open).toHaveBeenCalledWith('Projects/P/2-understanding/TECH.md');
    expect(screen.getByTestId('bm-eval-flagged').textContent).toContain('TECH.md:7');
    // a missed use opens its run in Pipelines
    const shown: CustomEvent[] = [];
    const listen = (e: Event) => shown.push(e as CustomEvent);
    window.addEventListener('swarm:show-pipeline', listen);
    fireEvent.click(screen.getByTestId('bm-eval-missed').querySelector('button')!);
    expect(shown[0].detail).toEqual({ runId: 'run_m' });
    window.removeEventListener('swarm:show-pipeline', listen);
    const text = screen.getByTestId('bm-panel-eval').textContent ?? '';
    for (const gone of ['Landed', 'Tokens', 'Average cycle', 'Human decisions', 'UNKNOWN']) expect(text).not.toContain(gone);
  });

  it('Eval: a store nobody could read says not measured, a small sample says too few, never a number', () => {
    evalData = { ...EVAL,
      health: { ...EVAL.health, rate: null, scored: null, counts: null, flagged: [], duplicates: null, gaps: null, entries: null },
      used: { ...EVAL.used, runs: 0, citing: 0, shown: [], missed: null, missed_total: null, written_used: null } };
    render(<BrainEvalView name="P" />);
    expect(screen.getByTestId('bm-eval-top-health').textContent).toContain('not measured');
    expect(screen.getByTestId('bm-eval-top-used').textContent).toContain('no Run wrote requirements or a design');
    expect(screen.getByTestId('bm-eval-dups').textContent).toContain('not measured');
    expect(screen.queryByTestId('bm-eval-flagged')).toBeNull();
    cleanup();
    evalData = { ...EVAL, health: { ...EVAL.health, rate: null, scored: 3 } };
    render(<BrainEvalView name="P" />);
    expect(screen.getByTestId('bm-eval-top-health').textContent).toContain('too few to rate (3)');
    expect(screen.getByTestId('bm-eval-top-health').textContent).not.toContain('%');
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

describe('BrainMapView · Project Agent', () => {
  const AGENT = {
    path: 'project-agent.yaml', revision: 'r1234567', summary: 'Small fixes.', defaultMode: 'code-change',
    reservedForHuman: ['open_pr', 'publish_release', 'push', 'send_external_message'], gates: ['no_git_push'],
    reviewFocus: [{ area: 'security', why: 'constant-time compare', source: 'TECH.md' }],
    modes: [{ id: 'code-change', when: 'a fix', repos: ['lib'],
              setup: [{ command: 'uv sync', source: 'ci.yml', repo: 'lib' }],
              validations: [{ id: 'unit', command: 'pytest -q', source: 'ci.yml', repo: 'lib' }],
              skills: ['s_lib-release'], reservedForHuman: [], gates: ['no_git_push'] },
            { id: 'research', when: 'a question', repos: [], setup: [], validations: [], skills: [],
              reservedForHuman: [], gates: [] }],
    problems: [],
  };
  const openAgent = (onDispatch = vi.fn(() => true)) => {
    render(<BrainAgentView name="P" onDispatch={onDispatch} />);
    return onDispatch;
  };

  it('shows what a Hive run reads, and nothing to confirm or edit', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    openAgent();
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('ready for Hive runs');
    expect(screen.getByTestId('pa-mode-code-change').textContent).toContain('pytest -q');
    expect(screen.getByTestId('pa-mode-code-change').textContent).toContain('uv sync');
    expect(screen.getByTestId('pa-guards').textContent).toContain('push');
    expect(screen.getByTestId('pa-guards').textContent).toContain('no_git_push');
    expect(screen.queryByTestId('bm-agent-confirm')).toBeNull();
    expect(screen.queryByTestId('pa-save')).toBeNull();
  });

  it('a definition with problems says the agent fixes them', () => {
    (DETAIL as Record<string, unknown>).projectAgent = { ...AGENT, problems: ['default_mode is not one of the modes'] };
    openAgent();
    expect(screen.getByTestId('bm-agent-status').textContent).toContain('the agent fixes them');
    expect(screen.getByTestId('bm-agent-problems').textContent).toContain('default_mode');
  });

  it('a change goes to the agent in chat', () => {
    (DETAIL as Record<string, unknown>).projectAgent = AGENT;
    const onDispatch = openAgent();
    expect((screen.getByTestId('bm-agent-note') as HTMLTextAreaElement).tagName).toBe('TEXTAREA');
    fireEvent.change(screen.getByTestId('bm-agent-note'), { target: { value: 'add a release mode' } });
    fireEvent.click(screen.getByTestId('bm-agent-revise'));
    const calls = onDispatch.mock.calls as unknown as string[][];
    expect(calls[0][0]).toContain('add a release mode');
    expect(calls[0][0]).toContain('tell me what you changed');
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
    health: { pending: 0, liveRun: false, lastChangeRelative: '--', ...h }, ...o,
  });
  // P: a Run waits on you (the attention channel's paused_run): not the Brain's.
  // Q: an escalation and two failing jobs. R: a Run going. S: being born. T: quiet.
  const brains = [
    brain('T', { description: 'Quiet one', learnedWeek: 0 }),
    brain('S', { setupState: 'setting-up', learnedWeek: 2 }),
    brain('R', { learnedWeek: 3 }, { liveRun: true }),
    brain('Q', { learnedWeek: 0, pendingBySource: { escalation: 1, job: 2 } }, { pending: 3 }),
    brain('P', { description: 'The SELF brain', learnedWeek: 4, pendingBySource: { paused_run: 1 } }, { pending: 1 }),
  ];
  const order = () => screen.getAllByTestId(/^hub-card-[^a]/).map((e) => e.getAttribute('data-testid'));

  it('one lead line, one attention band; the built-in Brain leads, the rest are alphabetical', () => {
    render(<BrainHubHome brains={brains as never} selfName="P" onOpen={vi.fn()} />);
    expect(screen.getByTestId('hub-count').textContent).toBe('5 Brains');
    expect(screen.getByTestId('hub-ambient').textContent).toContain('+9 learned this week');
    // each Brain item once: Q's escalation + Q's 2 failing jobs; P's waiting Run is
    // Pipelines', not a Brain's; the worst one sets the band's tone
    expect(screen.getByTestId('hub-open-count').textContent).toBe('3 open items');
    expect(screen.getByTestId('hub-attention').getAttribute('data-tone')).toBe('urgent');
    const chips = screen.getAllByTestId(/^hub-chip-/).map((c) => c.textContent);
    expect(chips).toEqual(['1 escalation to decide', '2 other items']);
    expect(order()).toEqual(['hub-card-P', 'hub-card-Q', 'hub-card-R', 'hub-card-S', 'hub-card-T']);
    expect([...screen.getByTestId('brainhub-builtin-wall').children].map((c) => c.getAttribute('data-testid')))
      .toEqual(['hub-card-P']);
    expect(screen.queryByTestId('hub-signal-P')).toBeNull();          // a waiting Run is not the Brain's status
    expect(screen.getByTestId('hub-card-P').querySelector('[data-testid="dddcard-self-tag"]')).toBeTruthy();
    expect(screen.getByTestId('hub-desc-Q').textContent).toContain('No description yet');
    expect(screen.getByTestId('hub-signal-Q').textContent).toBe('1 escalation to decide');
    expect(screen.queryByTestId('hub-signal-R')).toBeNull();          // nor a Run going
    expect(screen.queryByTestId('hub-signal-T')).toBeNull();          // a quiet Brain shows no status
    expect(screen.queryByTestId('hub-eval')).toBeNull();              // no run statistics in the Hub
    expect(screen.queryByTestId('hub-conductor')).toBeNull();
  });

  it('Review opens the queue, whose buttons open the right place; cards never move or filter out', () => {
    const onOpen = vi.fn();
    render(<BrainHubHome brains={brains as never} onOpen={onOpen} />);
    expect(screen.queryByTestId('hub-needs')).toBeNull();
    fireEvent.click(screen.getByTestId('hub-needs-toggle'));
    expect(order()).toEqual(['hub-card-P', 'hub-card-Q', 'hub-card-R', 'hub-card-S', 'hub-card-T']);
    const shown: CustomEvent[] = [];
    const listen = (e: Event) => shown.push(e as CustomEvent);
    window.addEventListener('swarm:show-needs-you', listen);
    expect(screen.queryByTestId('hub-need-go-run_1')).toBeNull();
    expect(screen.getByTestId('hub-need-Q-escalations').textContent).toContain('1 escalation to decide');
    fireEvent.click(screen.getByTestId('hub-need-go-Q-escalations'));    // Need You overlay
    expect(shown[0].type).toBe('swarm:show-needs-you');
    expect(screen.getByTestId('hub-need-Q-jobs').textContent).toContain('2 other items in Need You');
    fireEvent.click(screen.getByTestId('hub-need-go-Q-jobs'));           // Need You overlay
    expect(shown[1].type).toBe('swarm:show-needs-you');
    expect(onOpen).not.toHaveBeenCalled();
    window.removeEventListener('swarm:show-needs-you', listen);
    fireEvent.click(screen.getByTestId('hub-needs-toggle'));
    expect(screen.queryByTestId('hub-needs')).toBeNull();
  });

  it('a Project Agent is never an open item: a broken one is a review signal on its card', () => {
    const onOpen = vi.fn();
    render(<BrainHubHome brains={[brain('T', { projectAgentStatus: 'invalid' })] as never} onOpen={onOpen} />);
    expect(screen.queryByTestId('hub-open-count')).toBeNull();
    expect(screen.getByTestId('hub-signal-T').textContent).toBe('Project Agent has problems');
  });

  it('a repository a Brain started reading is a notice that opens the Brain', () => {
    const onOpen = vi.fn();
    render(<BrainHubHome brains={[brain('T', { pendingBySource: { local_root: 2 } }, { pending: 2 })] as never} onOpen={onOpen} />);
    expect(screen.getByTestId('hub-chip-repos').textContent).toBe('2 repositories newly read');
    expect(screen.getByTestId('hub-signal-T').getAttribute('data-tone')).toBe('notice');
    fireEvent.click(screen.getByTestId('hub-needs-toggle'));
    fireEvent.click(screen.getByTestId('hub-need-go-T-repos'));
    expect(onOpen).toHaveBeenLastCalledWith('T', { view: 'map' });
  });

  it('nothing open: no band. A whole card opens its brain; New Brain is the one page action', () => {
    const onOpen = vi.fn();
    const shown: string[] = [];
    const listen = (e: Event) => shown.push(e.type);
    window.addEventListener('swarm:show-new-brain', listen);
    render(<BrainHubHome brains={[brain('T'), brain('S', { recentActivity: 2 })] as never} onOpen={onOpen} />);
    expect(screen.queryByTestId('hub-attention')).toBeNull();
    fireEvent.click(screen.getByTestId('dddcard-T'));
    expect(onOpen).toHaveBeenLastCalledWith('T');
    expect(screen.queryByTestId('hub-add-knowledge')).toBeNull();     // a Brain's own action, on its page
    fireEvent.click(screen.getByTestId('hub-new-brain'));
    expect(shown).toEqual(['swarm:show-new-brain']);
    window.removeEventListener('swarm:show-new-brain', listen);
  });

  it('a card footer: entries, what it learned, when it last landed', () => {
    render(<BrainHubHome brains={[brain('T', { learnedWeek: 3, typeCounts: { guideline: 10, pitfall: 2 } },
      { lastChangeRelative: '5h ago' })] as never} onOpen={vi.fn()} />);
    expect(screen.getByTestId('hub-facts-T').textContent).toBe('12 entries · +3 this week · landed 5h ago');
    expect(screen.getByTestId('hub-ambient').textContent).toContain('12 knowledge entries');
  });

  it('an old daemon without learnedWeek shows no learned count, never +0', () => {
    render(<BrainHubHome brains={[brain('T')] as never} onOpen={vi.fn()} />);
    expect(screen.getByTestId('hub-ambient').textContent).not.toContain('learned');
  });

});

