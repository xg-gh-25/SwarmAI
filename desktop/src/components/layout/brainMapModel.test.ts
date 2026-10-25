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
import { sectionCounts, projectRuns, runTone, needsYouItems, brainEval, hubSummary } from './brainMapModel';

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

  it('hubSummary totals pending and live brains', () => {
    const b = (pending: number, liveRun: boolean) => ({ health: { pending, liveRun, sinking: 0, lastChangeRelative: '' } });
    expect(hubSummary([b(2, true), b(0, false), b(1, true)] as never)).toEqual({ brains: 3, needsYou: 3, live: 2 });
  });
});
