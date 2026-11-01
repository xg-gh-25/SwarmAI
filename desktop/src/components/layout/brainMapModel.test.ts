/**
 * brainMapModel tests — the pure builders behind the Brain map.
 * Properties: counts come from the detail as-is; a Brain holds knowledge only, so
 * its needs-you queue, the Hub's queue, its count and its card signal hold the Brain's
 * own waiting items (escalations, jobs, repositories) and never a Run (design
 * 2026-11-01-brain-eval-and-runs §2.1).
 */
import { describe, it, expect } from 'vitest';
import type { BrainDetail } from '../../services/ddd';
import { sectionCounts, needsYouItems, needsCount, hubSummary, hubNeeds, brainSignal, brainPending } from './brainMapModel';
import type { GeneralBrain } from '../../services/ddd';

const detail = {
  name: 'P', kind: 'knowledge',
  sections: [
    { key: 'knowledge', members: [{ path: 'a', gitStatus: 'clean' }], entries: [{}, {}, {}], completeNotBroken: false },
    { key: 'gates', members: [], entries: [], completeNotBroken: true },
    { key: 'delivery', members: [{ path: 'bindings.yaml', gitStatus: 'clean' }], entries: [], completeNotBroken: false },
  ],
  health: { trust: null, escalationPending: 0, recall: { value: null, experimental: true }, diagnostics: null, computedAt: null, recentActivity: 4 },
} as unknown as BrainDetail;
describe('brainMapModel', () => {
  it('sectionCounts reads the detail: entries for Knowledge, files elsewhere, empty-but-complete Gates', () => {
    const c = sectionCounts(detail);
    expect(c.knowledge).toBe('3 entries');
    expect(c.gates).toBe('none (ok)');
    expect(c.delivery).toBe('1 file');
    expect(c.identity).toBe('--');
    expect(sectionCounts(null).knowledge).toBe('--');
  });

  it('needsYouItems: the Brain\'s own waiting items, one counted row per kind; a waiting Run is not one', () => {
    expect(needsYouItems()).toEqual([]);
    const by = { paused_run: 2, escalation: 2, job: 1, local_root: 1, other_source: 1 };
    const items = needsYouItems(by);
    expect(items.map((i) => `${i.id}:${i.count}`)).toEqual(['escalations:2', 'jobs:1', 'repos:1', 'other:1']);
    // the rows add up to the Brain header's count, which leaves the Runs out
    const b = { health: { pending: 7 }, pendingBySource: by } as never;
    expect(needsCount(items)).toBe(brainPending(b));
    expect(brainPending(b)).toBe(5);
  });

  const brain = (name: string, o: Record<string, unknown> = {}, h: Record<string, unknown> = {}) => ({
    name, kind: 'knowledge', lifecycleStage: 'GROW', sectionsPresent: {},
    health: { pending: 0, liveRun: false, lastChangeRelative: '--', ...h }, ...o,
  }) as never;

  it('hubSummary: Need you is the Brains\' own waiting items, never their Runs', () => {
    const brains = [
      brain('A', { learnedWeek: 5, setupState: 'setting-up', pendingBySource: { paused_run: 1, escalation: 2 } },
        { pending: 3, liveRun: true }),
      brain('B', { learnedWeek: 0 }),
      brain('C'),
    ];
    expect(hubSummary(brains)).toEqual({ brains: 3, needsYou: 2, born: 1, learned: 5, learnedBrains: 1 });
    // no brain reports learnedWeek (old daemon) -> UNKNOWN, never 0
    expect(hubSummary([brain('C')]).learned).toBeNull();
    // the General Brain's week is in the total
    const general = { name: 'General', description: null, library: { files: 1, bytes: 1 }, projectAgentStatus: 'ready' as const,
      shadowed: false, learnedWeek: 2 };
    expect(hubSummary(brains, general).learned).toBe(7);
  });

  it('hubNeeds: each brain\'s escalations, jobs and repositories; never a Run or a Project Agent', () => {
    const items = hubNeeds([
      brain('A', { projectAgentStatus: 'ready', pendingBySource: { paused_run: 3 } }, { pending: 3 }),
      brain('B', { projectAgentStatus: 'ready', pendingBySource: { escalation: 2, job: 1 } }, { pending: 3 }),
      brain('C', { projectAgentStatus: 'invalid' })]);
    expect(items.map((i) => i.id)).toEqual(['B-escalations', 'B-jobs']);
    expect(items[0]).toMatchObject({ brain: 'B', kind: 'escalations', text: '2 escalations to decide', count: 2, severity: 'urgent' });
    expect(items[1]).toMatchObject({ brain: 'B', kind: 'jobs', text: '1 other item in Need You', severity: 'review' });
    expect(hubSummary([brain('B', { projectAgentStatus: 'invalid' }), brain('C', { projectAgentStatus: 'ready' })]).needsYou).toBe(0);
  });

  it('hubNeeds: a repository a Brain started reading is a notice, not "other items"', () => {
    const items = hubNeeds([brain('R', { pendingBySource: { local_root: 3 } }, { pending: 3 })]);
    expect(items).toEqual([expect.objectContaining({ kind: 'repos', count: 3, severity: 'notice',
      text: 'now reads 3 new repositories' })]);
  });

  it('brainSignal picks the one most pressing thing of the Brain: blocked > review > setup > notice', () => {
    // a Run waiting or going is not the Brain's status
    expect(brainSignal(brain('A', { pendingBySource: { paused_run: 2 } }, { pending: 2, liveRun: true })))
      .toMatchObject({ tone: 'calm', text: '' });
    expect(brainSignal(brain('B2', { pendingBySource: { escalation: 1 } }, { pending: 1 }))).toMatchObject({ tone: 'urgent', text: '1 escalation to decide' });
    // no split (old daemon): one unnamed bucket
    expect(brainSignal(brain('B4', {}, { pending: 2 })).text).toBe('2 items in Need You');
    expect(brainSignal(brain('B', { pendingBySource: { job: 2 } }, { pending: 2 })).tone).toBe('review');
    expect(brainSignal(brain('D', { setupState: 'setting-up' }))).toMatchObject({ tone: 'setup', text: 'Being born' });
    expect(brainSignal(brain('D2', { setupState: 'setting-up', setupProgress: { done: 4, total: 7 } })).text)
      .toBe('Being born · 4 of 7 checks');
    expect(brainSignal(brain('D3', { setupState: 'setting-up',
      setupProgress: { done: 4, total: 9, next: '② Knowledge/content' } })).text)
      .toBe('Being born · next: write the first knowledge (4/9)');
    expect(brainSignal(brain('D4', { setupState: 'setting-up',
      setupProgress: { done: 8, total: 9, next: '⑦ Something New' } })).text)
      .toBe('Being born · next: Something New (8/9)');
    expect(brainSignal(brain('H', { projectAgentStatus: 'invalid' }))).toMatchObject({ tone: 'review', text: 'Project Agent has problems' });
    expect(brainSignal(brain('J', { pendingBySource: { local_root: 1 } }, { pending: 1 })))
      .toMatchObject({ tone: 'notice', text: 'Reads 1 new repository' });
    expect(brainSignal(brain('F'))).toMatchObject({ tone: 'calm', rank: 5 });
  });
});

describe('the built-in General Brain in Need You (F2)', () => {
  const g = (status: GeneralBrain['projectAgentStatus']): GeneralBrain =>
    ({ name: 'General', description: null, library: { files: 1, bytes: 1 }, projectAgentStatus: status, shadowed: false });

  it('its Runner Agent is never a Need You row, whatever its state', () => {
    for (const st of ['ready', 'invalid', null] as const) {
      expect(hubNeeds([], g(st))).toEqual([]);
      expect(hubSummary([], g(st)).needsYou).toBe(0);
    }
  });
});
