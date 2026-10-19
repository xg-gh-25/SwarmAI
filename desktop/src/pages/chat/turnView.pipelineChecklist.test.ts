/**
 * pipelineChecklist — a pipeline turn's checklist lines (chat display v8, XG 2026-10-19).
 *
 * What is tested: the pure function that lays the turn's stage sections
 * (`sectionUnits`) onto the run's planned checklist (`pipeline_run.checklist`,
 * written by the backend). Every planned stage and gate is a line from the first
 * frame (pending until its section arrives), a stage that ran twice is ONE line with
 * two rounds, a resumed turn marks the stages before `current` done, and a gate row
 * the plan did not list sits inside its host stage's line, never as a new line.
 * Also: a later pipeline_run block of the same run updates the plan (the first one
 * is written before EVALUATE sets the profile).
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock, PipelineRunContent, PipelineStageContent } from '../../types';
import { deriveTurnView, pipelineChecklist, sectionUnits } from './turnView';

const RUN = 'run_t1';
const FULL = ['evaluate', 'gate0', 'think', 'plan', 'gate1', 'build', 'review', 'test', 'gate2', 'deliver', 'reflect'];
const checklistOf = (names: string[]) => names.map((s) => ({ stage: s, gate: /^gate\d$/.test(s) }));
const runBlock = (over: Partial<PipelineRunContent> = {}): ContentBlock => ({
  type: 'pipeline_run', runId: RUN, profile: 'full', stages: FULL.filter((s) => !s.startsWith('gate')),
  checklist: checklistOf(FULL), requirement: 'r', current: null, at: '2026-10-19T10:00:00Z', ...over,
}) as ContentBlock;
const row = (stage: string, status: string, over: Partial<PipelineStageContent> = {}): ContentBlock => ({
  type: 'pipeline_stage', runId: RUN, stage, status, ordinal: null, position: null, total: 8,
  profile: 'full', next: null, nextOrdinal: null,
  gate: /^gate\d$/.test(stage) ? { name: stage.toUpperCase(), verdict: 'PASS' } : null,
  summary: null, at: '2026-10-19T10:00:00Z', ...over,
}) as ContentBlock;
const tu = (id: string): ContentBlock => ({ type: 'tool_use', id, name: 'Bash', summary: 'Running: ls', category: 'bash' }) as ContentBlock;

function lines(content: ContentBlock[], streaming = false) {
  const view = deriveTurnView(content, streaming);
  return pipelineChecklist(sectionUnits(view.units, streaming, view.liveStage), view.run);
}
const shape = (ls: ReturnType<typeof lines>) => ls.map((l) => `${l.stage}:${l.status}${l.rounds.length > 1 ? `x${l.rounds.length}` : ''}`);

describe('pipelineChecklist', () => {
  it('lists every planned line from the first frame, pending until its section arrives', () => {
    const ls = lines([runBlock(), row('evaluate', 'in_progress'), tu('a')], true);
    expect(ls.map((l) => l.stage)).toEqual(FULL);
    expect(shape(ls).slice(0, 3)).toEqual(['evaluate:running', 'gate0:pending', 'think:pending']);
  });

  it('marks done, running and pending mid-BUILD; gates done when their row arrived', () => {
    const ls = lines([
      runBlock(),
      row('evaluate', 'in_progress'), tu('a'), row('evaluate', 'completed'), row('gate0', 'completed'),
      row('think', 'in_progress'), tu('b'), row('think', 'completed'),
      row('plan', 'in_progress'), tu('c'), row('plan', 'completed'),
      row('build', 'in_progress'), tu('d'), row('gate1', 'completed'),
    ], true);
    expect(shape(ls)).toEqual([
      'evaluate:done', 'gate0:done', 'think:done', 'plan:done', 'gate1:done', 'build:running',
      'review:pending', 'test:pending', 'gate2:pending', 'deliver:pending', 'reflect:pending']);
  });

  it('a stage that runs again is ONE line with two rounds, running again', () => {
    const ls = lines([
      runBlock(),
      row('plan', 'in_progress'), tu('a'), row('plan', 'completed'),
      row('plan', 'in_progress'), tu('b'),
    ], true);
    const plan = ls.find((l) => l.stage === 'plan')!;
    expect(ls.filter((l) => l.stage === 'plan')).toHaveLength(1);
    expect(plan.status).toBe('running');
    expect(plan.rounds).toHaveLength(2);
  });

  it('a resumed turn: the stages before current are done (earlier), current is running', () => {
    const goal = ['evaluate', 'gate0', 'think', 'plan', 'gate1', 'goal_cycle', 'gate2', 'deliver', 'reflect'];
    const ls = lines([
      runBlock({ profile: 'goal', stages: goal.filter((s) => !s.startsWith('gate')), checklist: checklistOf(goal), current: 'goal_cycle' }),
      tu('a'),
    ], true);
    expect(shape(ls)).toEqual([
      'evaluate:done', 'gate0:done', 'think:done', 'plan:done', 'gate1:done', 'goal_cycle:running',
      'gate2:pending', 'deliver:pending', 'reflect:pending']);
    expect(ls.filter((l) => l.status === 'done').every((l) => l.earlier)).toBe(true);
  });

  it('a gate row the plan did not list sits inside its host line, not as a new line', () => {
    const ls = lines([
      runBlock({ profile: 'docs', checklist: checklistOf(['evaluate', 'think', 'plan', 'gate2', 'deliver', 'reflect']) }),
      row('plan', 'in_progress'), tu('a'), row('plan', 'completed'), row('gate1', 'completed'),
    ]);
    // gate1 guards build / goal_cycle: no host line here, so it is appended at the end
    // (append-only), never inserted above an existing line.
    expect(ls.map((l) => l.stage)).toEqual(['evaluate', 'think', 'plan', 'gate2', 'deliver', 'reflect', 'gate1']);
    expect(ls.some((l) => l.extraGates.length > 0)).toBe(false);
    // With a host line it sits inside it.
    const ls2 = lines([
      runBlock({ profile: 'trivial', checklist: checklistOf(['evaluate', 'build', 'gate2', 'deliver']) }),
      row('build', 'in_progress'), tu('a'), row('gate1', 'completed'),
    ]);
    const build = ls2.find((l) => l.stage === 'build')!;
    expect(build.extraGates.map((g) => g.block.stage)).toEqual(['gate1']);
    expect(ls2.map((l) => l.stage)).toEqual(['evaluate', 'build', 'gate2', 'deliver']);
  });

  it('no checklist (older runs): the run block\'s stage list is the plan — never "3/3" for a run part-way', () => {
    const ls = lines([
      runBlock({ checklist: undefined }),
      row('evaluate', 'completed'), row('gate0', 'completed'), tu('a'), row('think', 'completed'),
    ]);
    expect(ls.map((l) => l.stage)).toEqual(['evaluate', 'think', 'plan', 'build', 'review', 'test', 'deliver', 'reflect']);
    expect(ls.find((l) => l.stage === 'evaluate')!.extraGates).toHaveLength(1); // gate0 sits with the stage it guards
    expect(ls.filter((l) => l.status === 'done').map((l) => l.stage)).toEqual(['evaluate', 'think']);
  });

  it('a later pipeline_run of the same run updates the plan; the first requirement stays', () => {
    const first = runBlock({ profile: null, stages: [], checklist: [], requirement: 'first' });
    const later = runBlock({ requirement: 'later' });
    const view = deriveTurnView([first, row('evaluate', 'in_progress'), tu('a'), later], true);
    expect(view.run?.requirement).toBe('first');
    expect(view.run?.checklist?.map((c) => c.stage)).toEqual(FULL);
    expect(view.run?.profile).toBe('full');
  });
});
