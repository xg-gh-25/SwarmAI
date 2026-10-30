/**
 * brainMapModel tests — the pure builders behind the Brain map.
 * Properties: counts come from the detail as-is; a brain's Runs are only its own;
 * the needs-you queue holds exactly the Runs with needsYou (plus the attention
 * channel's escalations / jobs and a Project Agent draft);
 * Eval never invents a number (null when unmeasured, tokens null unless every Run
 * was measured).
 */
import { describe, it, expect } from 'vitest';
import type { BrainDetail, ReviewData } from '../../services/ddd';
import type { PipelineAnalytics, PipelineRunSummary } from '../../services/pipelines';
import { sectionCounts, projectRuns, runTone, needsYouItems, needsCount, brainHiveRuns, humanPerRun, brainEval, hubSummary, hubNeeds, brainSignal, generalNeed } from './brainMapModel';
import type { BrainSummary, GeneralBrain } from '../../services/ddd';
import type { SwarmOutRun } from '../../services/swarmOut';

const run = (o: Partial<PipelineRunSummary>): PipelineRunSummary => ({
  id: 'run_a', requirement: 'Fix it', status: 'completed', profile: 'bugfix', progress: '8/8',
  cycleTimeMin: 20, tokensActual: 1000, createdAt: '', updatedAt: '2026-10-20T00:00:00Z',
  pauseKind: null, needsYou: false, checkpointReason: null, ownerSessionId: null,
  notLanded: false, reportPath: null, auditCard: null, ...o,
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
  hunks: [{ file: 'K.md', signature: 's1', tag: 'cultivation·auto-applied', diff_text: '' }],
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

  it('runTone: needs-you wins, a completion that did not land is not "landed"', () => {
    expect(runTone(run({ status: 'running', needsYou: true }))).toBe('needs');
    expect(runTone(run({ status: 'running' }))).toBe('running');
    expect(runTone(run({ status: 'completed' }))).toBe('landed');
    expect(runTone(run({ status: 'completed', notLanded: true }))).toBe('failed');
    expect(runTone(run({ status: 'paused' }))).toBe('stopped');
  });

  it('needsYouItems: a Project Agent draft (or a broken file) waits on the person; a reviewed one does not', () => {
    expect(needsYouItems([], { status: 'draft', problems: [] }).map((i) => i.title)).toEqual(['Confirm the Project Agent draft']);
    expect(needsYouItems([], { status: 'reviewed', problems: ['bad profile'] })[0]).toMatchObject({ kind: 'agent', detail: 'bad profile' });
    expect(needsYouItems([], { status: 'reviewed', problems: [] })).toEqual([]);
    expect(needsYouItems([], null)).toEqual([]);
  });

  it('needsYouItems: Runs with needsYou, nothing else', () => {
    const items = needsYouItems([run({ id: 'r1', needsYou: true, pauseKind: 'decision' }), run({ id: 'r2' })]);
    expect(items.map((i) => i.kind + ':' + i.id)).toEqual(['run:r1']);
    expect(items[0].detail).toContain('question');
  });

  it('needsYouItems: what Need You holds beyond the lists is a counted row, and the counts add up to the header', () => {
    const items = needsYouItems([run({ id: 'r1', needsYou: true })], null,
      { paused_run: 2, escalation: 2, job: 1, local_root: 1, other_source: 1 });
    expect(items.map((i) => `${i.id}:${i.count}`)).toEqual(
      ['r1:1', 'runs-unlisted:1', 'escalations:2', 'jobs:1', 'repos:1', 'other:1']);
    expect(needsCount(items)).toBe(2 + 2 + 1 + 1 + 1);   // the attention channel's pending
  });

  it('brainHiveRuns: a Brain\'s Hive runs of the last 30 days, newest first; humanPerRun over the recorded ones', () => {
    const now = Date.parse('2026-10-30T00:00:00Z');
    const h = (id: string, brain: string | null, sent: string, o: Record<string, unknown> = {}) =>
      ({ dispatch_id: id, sent_at: sent, project_agent: brain ? { brain } : null, ...o }) as unknown as SwarmOutRun;
    const got = brainHiveRuns([
      h('old', 'P', '2026-09-01T00:00:00Z'), h('a', 'P', '2026-10-20T00:00:00Z'), h('b', 'P', '2026-10-29T00:00:00Z'),
      h('other', 'Q', '2026-10-29T00:00:00Z'), h('res', null, '2026-10-28T00:00:00Z', { result: { project_agent: { brain: 'P' } } }),
    ], 'P', now);
    expect(got.map((r) => r.dispatch_id)).toEqual(['b', 'res', 'a']);
    const withHuman = h('x', 'P', '2026-10-29T00:00:00Z', { result: { work: { human: { approved: 1, denied: 1, unanswered: 9, answered_questions: 2 } } } });
    expect(humanPerRun([run({ humanDecisions: 0 }), run({ humanDecisions: null })], [withHuman, got[0]])).toEqual({ avg: 2, runs: 2 });
    expect(humanPerRun([], [])).toEqual({ avg: null, runs: 0 });
  });

  it('brainEval never invents a number', () => {
    const e = brainEval([run({}), run({ id: 'b', tokensActual: null, status: 'running', cycleTimeMin: null })], detail, review);
    expect(e.runs.total).toBe(2);
    expect(e.runs.landed).toBe(1);
    expect(e.runs.completionRate).toBe(1);   // only finished Runs count
    expect(e.runs.tokens).toBeNull();        // one Run unmeasured -> no total
    expect(e.runs.avgCycleMin).toBe(20);
    expect(e.brain).toMatchObject({ entries: 3, sinking: 1, reclaimable: 2, recentActivity: 4, autoApplied: 1 });
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
      brain('A', { recentActivity: 5, setupState: 'setting-up', pendingBySource: { paused_run: 1, escalation: 2 } },
        { pending: 3, liveRun: true, sinking: 3 }),
      brain('B', { recentActivity: 0 }, { sinking: 4 }),
      brain('C'),
    ];
    const a = an({
      A: [run({ needsYou: true, status: 'running' }), run({ id: 'r2' }), run({ id: 'r3', notLanded: true })],
      Gone: [run({ id: 'x', needsYou: true })],  // not a known brain: not counted
    });
    expect(hubSummary(brains, a)).toEqual({
      brains: 3, needsYou: 3, running: 1, born: 1, learned: 5, learnedBrains: 1, sinking: 7,
      landed: 1, finished: 2, liveRuns: 1,
    });
    // no brain reports recentActivity (old daemon) -> UNKNOWN, never 0
    expect(hubSummary([brain('C')], null).learned).toBeNull();
  });

  it('hubNeeds: Runs waiting on you oldest first, then each brain\'s escalations and jobs, then Project Agents', () => {
    const a = an({ A: [
      run({ id: 'new', needsYou: true, pauseKind: 'decision', requirement: 'Pick a column', updatedAt: '2026-10-25T00:00:00Z' }),
      run({ id: 'old', needsYou: true, pauseKind: 'stopped', requirement: 'Resume me', updatedAt: '2026-10-20T00:00:00Z' }),
    ] });
    const items = hubNeeds([
      brain('A', { projectAgentStatus: 'reviewed', pendingBySource: { paused_run: 3 } }, { pending: 3 }),
      brain('B', { projectAgentStatus: 'draft', pendingBySource: { escalation: 2, job: 1 } }, { pending: 3 }),
      brain('C', { projectAgentStatus: 'invalid' })], a);
    // A: 3 Runs wait, the analytics names 2 -> one "1 Run waiting on you" row for the rest
    expect(items.map((i) => i.id)).toEqual(['old', 'new', 'A-runs', 'B-escalations', 'B-jobs', 'B-agent', 'C-agent']);
    expect(items[2].text).toBe('1 Run waiting on you');
    expect(items[3]).toMatchObject({ brain: 'B', kind: 'escalations', text: '2 escalations to decide' });
    expect(items[4]).toMatchObject({ brain: 'B', kind: 'jobs', text: '1 other item in Need You' });
    expect(items[1].text).toBe('Run question: Pick a column');
    expect(items[5]).toMatchObject({ kind: 'agent', text: 'Project Agent draft to confirm', severity: 'setup' });
    // severity: a Run or an escalation blocks on the person; a failing job waits for a review
    expect(items.map((i) => i.severity)).toEqual(['urgent', 'urgent', 'urgent', 'urgent', 'review', 'setup', 'review']);
    expect(items[2].count).toBe(1);
    expect(items[3].count).toBe(2);
    expect(hubSummary([brain('B', { projectAgentStatus: 'draft' }), brain('C', { projectAgentStatus: 'reviewed' })], null).needsYou).toBe(1);
  });

  it('a Project Agent changed after it was confirmed waits on the person, like a draft', () => {
    const items = hubNeeds([brain('C', { projectAgentStatus: 'changed' })], null);
    expect(items).toEqual([expect.objectContaining({ kind: 'agent', severity: 'setup',
      text: 'Project Agent changed since you confirmed it: confirm again' })]);
    expect(hubSummary([brain('C', { projectAgentStatus: 'changed' })], null).needsYou).toBe(1);
    expect(brainSignal(brain('C', { projectAgentStatus: 'changed' }), null)).toMatchObject({ tone: 'setup', text: 'Project Agent to confirm again' });
    expect(generalNeed({ name: 'General', projectAgentStatus: 'changed' } as unknown as GeneralBrain))
      .toMatchObject({ severity: 'setup', text: 'Runner Agent changed since you confirmed it: confirm again' });
  });

  it('hubNeeds: a repository a Brain started reading is a notice, not "other items"', () => {
    const items = hubNeeds([brain('R', { pendingBySource: { local_root: 3 } }, { pending: 3 })], null);
    expect(items).toEqual([expect.objectContaining({ kind: 'repos', count: 3, severity: 'notice',
      text: 'now reads 3 new repositories' })]);
  });

  it('brainSignal picks the one most pressing thing: blocked > review > running > setup > notice', () => {
    const a = an({ A: [run({ needsYou: true })] });
    expect(brainSignal(brain('A', {}, { liveRun: true }), a)).toMatchObject({ tone: 'urgent', rank: 0, text: 'A Run needs you' });
    expect(brainSignal(brain('B2', { pendingBySource: { escalation: 1 } }, { pending: 1 }), a).text).toBe('1 escalation to decide');
    // a Run the analytics does not list still counts (the attention channel saw it)
    expect(brainSignal(brain('B3', { pendingBySource: { paused_run: 2 } }, { pending: 2 }), null).text).toBe('2 Runs need you');
    // no split (old daemon): one unnamed bucket
    expect(brainSignal(brain('B4', {}, { pending: 2 }), null).text).toBe('2 items in Need You');
    expect(brainSignal(brain('B', { pendingBySource: { job: 2 } }, { pending: 2 }), a).tone).toBe('review');
    expect(brainSignal(brain('B2', { pendingBySource: { escalation: 1 } }, { pending: 1 }), a).tone).toBe('urgent');
    expect(brainSignal(brain('C', { setupState: 'setting-up' }, { liveRun: true }), a)).toMatchObject({ tone: 'live', rank: 2 });
    expect(brainSignal(brain('D', { setupState: 'setting-up' }), a)).toMatchObject({ tone: 'setup', text: 'Being born' });
    expect(brainSignal(brain('D2', { setupState: 'setting-up', setupProgress: { done: 4, total: 7 } }), a).text)
      .toBe('Being born · 4 of 7 checks');
    expect(brainSignal(brain('D3', { setupState: 'setting-up',
      setupProgress: { done: 4, total: 9, next: '② Knowledge/content' } }), a).text)
      .toBe('Being born · next: write the first knowledge (4/9)');
    expect(brainSignal(brain('D4', { setupState: 'setting-up',
      setupProgress: { done: 8, total: 9, next: '⑦ Something New' } }), a).text)
      .toBe('Being born · next: Something New (8/9)');
    // a Project Agent draft is setup: grey, after a Run in progress
    expect(brainSignal(brain('G', { projectAgentStatus: 'draft' }, { liveRun: true }), a)).toMatchObject({ tone: 'live' });
    expect(brainSignal(brain('G2', { projectAgentStatus: 'draft' }), a))
      .toMatchObject({ tone: 'setup', text: 'Project Agent to confirm' });
    expect(brainSignal(brain('H', { projectAgentStatus: 'invalid' }), a)).toMatchObject({ tone: 'review', text: 'Project Agent has problems' });
    expect(brainSignal(brain('J', { pendingBySource: { local_root: 1 } }, { pending: 1 }), a))
      .toMatchObject({ tone: 'notice', text: 'Reads 1 new repository' });
    // nothing going on: no status (the card's footer carries its recency)
    expect(brainSignal(brain('I', { projectAgentStatus: 'reviewed' }), a)).toMatchObject({ tone: 'calm', text: '' });
    expect(brainSignal(brain('F'), a)).toMatchObject({ tone: 'calm', rank: 5 });
  });
});

describe('the built-in General Brain in Need You (F2)', () => {
  const g = (status: GeneralBrain['projectAgentStatus']): GeneralBrain =>
    ({ name: 'General', description: null, library: { files: 1, bytes: 1 }, projectAgentStatus: status, shadowed: false });
  const shadow = {
    name: 'General', kind: 'knowledge', lifecycleStage: 'GROW', projectAgentStatus: 'draft',
    sectionsPresent: { identity: true, knowledge: true, gates: false, capabilities: false, delivery: true, refresher: false },
    health: { sinking: 0, pending: 0, liveRun: false, lastChangeRelative: '--' },
  } as unknown as BrainSummary;

  it('a draft or a broken Runner Agent is one row and one count; a confirmed one is none', () => {
    for (const st of ['draft', 'invalid'] as const) {
      expect(hubNeeds([], null, g(st)).map((n) => n.id)).toEqual(['builtin-general-agent']);
      expect(hubSummary([], null, g(st)).needsYou).toBe(1);
    }
    for (const st of ['reviewed', null] as const) {
      expect(hubNeeds([], null, g(st))).toEqual([]);
      expect(hubSummary([], null, g(st)).needsYou).toBe(0);
    }
    expect(generalNeed(null)).toBeNull();
  });

  it('a Projects/ Brain with the same name keeps its own row, with another id', () => {
    const rows = hubNeeds([shadow], null, g('draft'));
    expect(rows.map((n) => [n.id, !!n.builtin])).toEqual([['General-agent', false], ['builtin-general-agent', true]]);
    expect(hubSummary([shadow], null, g('draft')).needsYou).toBe(2);
  });
});

