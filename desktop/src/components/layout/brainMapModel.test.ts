/**
 * brainMapModel tests — the pure builders behind the Brain map.
 * Properties: counts come from the detail as-is; a brain's Runs are only its own;
 * the needs-you queue holds exactly the Runs with needsYou plus every proposal;
 * Eval never invents a number (null when unmeasured, tokens null unless every Run
 * was measured).
 */
import { describe, it, expect } from 'vitest';
import type { BrainDetail, ReviewData } from '../../services/ddd';
import type { PipelineAnalytics, PipelineRunSummary } from '../../services/pipelines';
import { sectionCounts, projectRuns, runTone, needsYouItems, brainEval, hubSummary, hubNeeds, brainSignal } from './brainMapModel';

const run = (o: Partial<PipelineRunSummary>): PipelineRunSummary => ({
  id: 'run_a', requirement: 'Fix it', status: 'completed', profile: 'bugfix', progress: '8/8',
  cycleTimeMin: 20, tokensActual: 1000, createdAt: '', updatedAt: '2026-10-20T00:00:00Z',
  pauseKind: null, needsYou: false, checkpointReason: null, ownerSessionId: null,
  verified: true, reportPath: null, auditCard: null, ...o,
});
const analytics = (runs: PipelineRunSummary[], project = 'P'): PipelineAnalytics => ({
  window: '30d',
  overall: { totalRuns: 0, completed: 0, completionRate: 0, avgCycleMin: null, tokensActual: null, profileMix: {}, abortedCount: 0 },
  trend: [],
  byProject: [{ project, runCount: runs.length, completionRate: 0, avgCycleMin: null, abortedCount: 0, runs },
    { project: 'Other', runCount: 1, completionRate: 0, avgCycleMin: null, abortedCount: 0, runs: [run({ id: 'run_other' })] }],
});
const detail = {
  name: 'P', kind: 'knowledge',
  sections: [
    { key: 'knowledge', members: [{ path: 'a', gitStatus: 'clean' }], entries: [{}, {}, {}], completeNotBroken: false },
    { key: 'gates', members: [], entries: [], completeNotBroken: true },
    { key: 'delivery', members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
  health: { noise: { reclaimable: 2, rate: 0 }, sinking: 1, trust: null, escalationPending: 0, recall: { value: null, experimental: true }, diagnostics: null, computedAt: null, recentActivity: 4 },
} as unknown as BrainDetail;
const review: ReviewData = {
  last_reviewed_sha: 'a', head_sha: 'b', diff_incomplete: false,
  hunks: [{ file: 'K.md', signature: 's1', tag: 'cultivation·auto-applied', diff_text: '' }, { file: 'G.md', signature: 's2', tag: 'risky·staged', diff_text: '' }],
  proposals: [{ id: 'p1', target_doc: 'TECH.md', target_section: 'Gates', content: 'Add a reviewer', confidence: 0.8, source_run_id: 'run_a' }],
};

describe('brainMapModel', () => {
  it('sectionCounts reads the detail: entries for Knowledge, files elsewhere, empty-but-complete Gates', () => {
    const c = sectionCounts(detail);
    expect(c.knowledge).toBe('3 entries');
    expect(c.gates).toBe('none (ok)');
    expect(c.delivery).toBe('1 file');
    expect(c.identity).toBe('--');
    expect(sectionCounts(null).knowledge).toBe('--');
  });

  it('projectRuns returns only this brain\'s Runs, newest first', () => {
    const r = projectRuns(analytics([run({ id: 'old', updatedAt: '2026-10-01' }), run({ id: 'new', updatedAt: '2026-10-22' })]), 'P');
    expect(r.map((x) => x.id)).toEqual(['new', 'old']);
    expect(projectRuns(null, 'P')).toEqual([]);
    expect(projectRuns(analytics([]), 'Missing')).toEqual([]);
  });

  it('runTone: needs-you wins, an unverified completion is not "landed"', () => {
    expect(runTone(run({ status: 'running', needsYou: true }))).toBe('needs');
    expect(runTone(run({ status: 'running' }))).toBe('running');
    expect(runTone(run({ status: 'completed' }))).toBe('landed');
    expect(runTone(run({ status: 'completed', verified: false }))).toBe('failed');
    expect(runTone(run({ status: 'paused' }))).toBe('stopped');
  });

  it('needsYouItems: a Project Agent draft (or a broken file) waits on the person; a reviewed one does not', () => {
    expect(needsYouItems([], null, { status: 'draft', problems: [] }).map((i) => i.title)).toEqual(['Confirm the Project Agent draft']);
    expect(needsYouItems([], null, { status: 'reviewed', problems: ['bad profile'] })[0]).toMatchObject({ kind: 'agent', detail: 'bad profile' });
    expect(needsYouItems([], null, { status: 'reviewed', problems: [] })).toEqual([]);
    expect(needsYouItems([], null, null)).toEqual([]);
  });

  it('needsYouItems: Runs with needsYou plus every proposal, nothing else', () => {
    const items = needsYouItems([run({ id: 'r1', needsYou: true, pauseKind: 'decision' }), run({ id: 'r2' })], review);
    expect(items.map((i) => i.kind + ':' + i.id)).toEqual(['run:r1', 'proposal:p1']);
    expect(items[0].detail).toContain('question');
  });

  it('brainEval never invents a number', () => {
    const e = brainEval([run({}), run({ id: 'b', tokensActual: null, status: 'running', cycleTimeMin: null })], detail, review);
    expect(e.runs.total).toBe(2);
    expect(e.runs.landed).toBe(1);
    expect(e.runs.completionRate).toBe(1);   // only finished Runs count
    expect(e.runs.tokens).toBeNull();        // one Run unmeasured -> no total
    expect(e.runs.avgCycleMin).toBe(20);
    expect(e.brain).toMatchObject({ entries: 3, sinking: 1, reclaimable: 2, recentActivity: 4, autoApplied: 1, pending: 1 });
    const empty = brainEval([], null, null);
    expect(empty.runs.completionRate).toBeNull();
    expect(empty.runs.avgCycleMin).toBeNull();
    expect(empty.brain.sinking).toBeNull();
  });

  const brain = (name: string, o: Record<string, unknown> = {}, h: Record<string, unknown> = {}) => ({
    name, kind: 'knowledge', lifecycleStage: 'GROW', sectionsPresent: {},
    health: { pending: 0, liveRun: false, sinking: 0, lastChangeRelative: '--', ...h }, ...o,
  }) as never;
  const an = (groups: Record<string, PipelineRunSummary[]>) => ({
    window: '30d', trend: [], overall: {} as never,
    byProject: Object.entries(groups).map(([project, runs]) => ({ project, runs } as never)),
  }) as PipelineAnalytics;

  it('hubSummary: Need you is the attention count (a waiting Run is in it once) plus Project Agent drafts', () => {
    const brains = [
      brain('A', { recentActivity: 5, setupState: 'setting-up', pendingBySource: { paused_run: 1, cultivation: 2 } },
        { pending: 3, liveRun: true, sinking: 3 }),
      brain('B', { recentActivity: 0 }, { sinking: 4 }),
      brain('C'),
    ];
    const a = an({
      A: [run({ needsYou: true, status: 'running' }), run({ id: 'r2' }), run({ id: 'r3', verified: false })],
      Gone: [run({ id: 'x', needsYou: true })],  // not a known brain: not counted
    });
    expect(hubSummary(brains, a)).toEqual({
      brains: 3, needsYou: 3, running: 1, born: 1, learned: 5, learnedBrains: 1, sinking: 7,
      landed: 1, finished: 2, liveRuns: 1,
    });
    // no brain reports recentActivity (old daemon) -> UNKNOWN, never 0
    expect(hubSummary([brain('C')], null).learned).toBeNull();
  });

  it('hubNeeds: Runs waiting on you oldest first, then each brain with proposals, then Project Agents', () => {
    const a = an({ A: [
      run({ id: 'new', needsYou: true, pauseKind: 'decision', requirement: 'Pick a column', updatedAt: '2026-10-25T00:00:00Z' }),
      run({ id: 'old', needsYou: true, pauseKind: 'stopped', requirement: 'Resume me', updatedAt: '2026-10-20T00:00:00Z' }),
    ] });
    const items = hubNeeds([
      brain('A', { projectAgentStatus: 'reviewed', pendingBySource: { paused_run: 3 } }, { pending: 3 }),
      brain('B', { projectAgentStatus: 'draft', pendingBySource: { cultivation: 1, escalation: 2, job: 1 } }, { pending: 4 }),
      brain('C', { projectAgentStatus: 'invalid' })], a);
    // A: 3 Runs wait, the analytics names 2 -> one "1 Run waiting on you" row for the rest
    expect(items.map((i) => i.id)).toEqual(['old', 'new', 'A-runs', 'B-proposals', 'B-escalations', 'B-jobs', 'B-agent', 'C-agent']);
    expect(items[2].text).toBe('1 Run waiting on you');
    expect(items[4].text).toBe('2 escalations to decide');
    expect(items[1].text).toBe('Run question: Pick a column');
    expect(items[3]).toMatchObject({ brain: 'B', kind: 'proposals', text: '1 rule proposal to review' });
    expect(items[6]).toMatchObject({ kind: 'agent', text: 'Project Agent draft to confirm' });
    expect(hubSummary([brain('B', { projectAgentStatus: 'draft' }), brain('C', { projectAgentStatus: 'reviewed' })], null).needsYou).toBe(1);
  });

  it('brainSignal picks the one most urgent thing: needs you > running > being born > last change', () => {
    const a = an({ A: [run({ needsYou: true })] });
    expect(brainSignal(brain('A', {}, { liveRun: true }), a)).toMatchObject({ tone: 'error', rank: 0, text: 'A Run needs you' });
    expect(brainSignal(brain('B', { pendingBySource: { cultivation: 2 } }, { pending: 2 }), a).text).toBe('2 proposals to review');
    expect(brainSignal(brain('B2', { pendingBySource: { escalation: 1 } }, { pending: 1 }), a).text).toBe('1 escalation to decide');
    // a Run the analytics does not list still counts (the attention channel saw it)
    expect(brainSignal(brain('B3', { pendingBySource: { paused_run: 2 } }, { pending: 2 }), null).text).toBe('2 Runs need you');
    // no split (old daemon): never called proposals
    expect(brainSignal(brain('B4', {}, { pending: 2 }), null).text).toBe('2 items in Need You');
    expect(brainSignal(brain('C', { setupState: 'setting-up' }, { liveRun: true }), a)).toMatchObject({ tone: 'primary', rank: 1 });
    expect(brainSignal(brain('D', { setupState: 'setting-up' }), a)).toMatchObject({ tone: 'warning', text: 'Being born' });
    expect(brainSignal(brain('D2', { setupState: 'setting-up', setupProgress: { done: 4, total: 7 } }), a).text)
      .toBe('Being born · 4 of 7 checks');
    expect(brainSignal(brain('G', { projectAgentStatus: 'draft' }, { liveRun: true }), a))
      .toMatchObject({ tone: 'error', rank: 0, text: 'Project Agent to confirm' });
    expect(brainSignal(brain('H', { projectAgentStatus: 'invalid' }), a).text).toBe('Project Agent has problems');
    expect(brainSignal(brain('I', { projectAgentStatus: 'reviewed' }), a).tone).toBe('muted');
    expect(brainSignal(brain('E', {}, { lastChangeRelative: '2h ago' }), a).text).toBe('Last landed 2h ago');
    expect(brainSignal(brain('F'), a)).toMatchObject({ tone: 'muted', rank: 3, text: 'No landed change in 30 days' });
  });
});
