/**
 * sectionUnits — every stage and gate that ran is its own row, and a stage row folds
 * exactly that stage's work (XG 2026-10-19, items 1 + 2).
 *
 * What is tested:
 * - a gate row (gate0 / gate1 / gate2) takes no children: the work before it carries
 *   on to the next stage row, so gate1 + build landing together gives BUILD its work
 *   (the bug: BUILD showed 0s and empty, its work sat under GATE 1)
 * - a stage row's start (`prevAt`) is the previous NON-gate row
 * - a model-written "## ✦ <stage>" landmark is not rendered (the backend row says it),
 *   but a gate BLOCK landmark and the final "━━━" summary still are
 * - `stageCounts`: steps · files changed · commands of a stage's work
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock, PipelineStageContent } from '../../types';
import { deriveTurnView, sectionUnits, stageCounts, stripStageHeading } from './turnView';

const tu = (id: string, summary = 'Running: ls', name = 'Bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category: 'bash' }) as ContentBlock;
const res = (id: string) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError: false, outcome: 'ok' }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const row = (stage: string, at: string, over: Partial<PipelineStageContent> = {}): PipelineStageContent => ({
  type: 'pipeline_stage', runId: 'run_t1', stage, status: 'completed', ordinal: null,
  position: stage.startsWith('gate') ? null : 1, total: 8, profile: 'full', next: null, nextOrdinal: null,
  gate: stage.startsWith('gate') ? { name: stage.toUpperCase().replace('GATE', 'GATE '), verdict: 'PASS' } : null,
  summary: null, at, ...over,
});
const T = (m: number) => `2026-10-19T09:${String(m).padStart(2, '0')}:00+08:00`;

function sections(content: ContentBlock[], streaming = false) {
  const view = deriveTurnView(content, streaming);
  return sectionUnits(view.units, streaming, view.liveStage);
}
const stageRows = (secs: ReturnType<typeof sections>) =>
  secs.flatMap((s) => (s.kind === 'stage' ? [s] : []));

describe('sectionUnits — gate rows take no work', () => {
  it('gate1 + build at one instant: BUILD gets the work, GATE 1 none', () => {
    const secs = stageRows(sections([
      row('plan', T(1)), tu('a'), res('a'), tu('b'), res('b'),
      row('gate1', T(5)), row('build', T(5)),
    ]));
    expect(secs.map((s) => s.block.stage)).toEqual(['plan', 'gate1', 'build']);
    expect(secs[1].children).toEqual([]);
    const build = secs[2];
    expect(build.children.length).toBeGreaterThan(0);
    expect(stageCounts(build.children).steps).toBe(2);
    expect(build.prevAt).toBe(T(1)); // from PLAN, not from the GATE 1 row
  });

  it('gate0 after evaluate and gate2 before deliver hold no work', () => {
    const secs = stageRows(sections([
      tu('a'), res('a'), row('evaluate', T(1)), row('gate0', T(1)),
      tu('b'), res('b'), row('think', T(2)),
      tu('c'), res('c'), row('gate2', T(9)), row('deliver', T(9)),
    ]));
    expect(secs.map((s) => [s.block.stage, s.children.length > 0])).toEqual([
      ['evaluate', true], ['gate0', false], ['think', true], ['gate2', false], ['deliver', true],
    ]);
    expect(secs[2].prevAt).toBe(T(1));
    expect(secs[4].prevAt).toBe(T(2));
  });

  it('a gate row landing alone mid-stage keeps that stage\'s work for the stage row', () => {
    const secs = stageRows(sections([
      row('plan', T(1)), tu('a'), res('a'), row('gate1', T(3)), tu('b'), res('b'), row('goal_cycle', T(8)),
    ]));
    expect(secs.map((s) => s.block.stage)).toEqual(['plan', 'gate1', 'goal_cycle']);
    expect(secs[1].children).toEqual([]);
    expect(stageCounts(secs[2].children).steps).toBe(2);
  });
});

describe('sectionUnits — stage landmarks', () => {
  const allText = (secs: ReturnType<typeof sections>) => JSON.stringify(secs);

  it('a "## ✦ <stage>" landmark the model wrote is not rendered anywhere', () => {
    const secs = sections([
      tu('a'), res('a'), row('build', T(4)), tx('## ✦ ⑤ BUILD\n→ 3 files changed'), tu('b'), res('b'), row('review', T(6)),
    ]);
    expect(allText(secs)).not.toContain('✦ ⑤ BUILD');
  });

  it('a gate BLOCK landmark and the final summary stay', () => {
    const secs = sections([
      tu('a'), res('a'), row('plan', T(1)), tx('## ★ Gate 1: BLOCK — plan misses the reader path'),
      tu('b'), res('b'), row('build', T(4)), tx('━━━━━━\n**✅ Pipeline 完成**'),
    ]);
    expect(allText(secs)).toContain('Gate 1: BLOCK');
    expect(allText(secs)).toContain('Pipeline 完成');
  });

  it('a turn with no stage rows is untouched (a landmark there is ordinary text)', () => {
    const secs = sections([tu('a'), res('a'), tx('## ✦ just a heading')]);
    expect(allText(secs)).toContain('just a heading');
  });
});

describe('stageCounts', () => {
  it('counts tool calls, changed files and shell commands', () => {
    const view = deriveTurnView([
      tu('a', 'Reading /r/a.ts', 'Read'), res('a'),
      tu('b', 'Writing to /r/b.ts', 'Edit'), res('b'),
      tu('c', 'Running: make'), res('c'),
    ], false);
    expect(stageCounts(view.units)).toEqual({ steps: 3, files: 1, commands: 1 });
  });
});

// ── Start-based sections (AC2a–AC2d) ──────────────────────────────────────────

const start = (stage: string, at: string, position: number | null = 1): PipelineStageContent =>
  row(stage, at, { status: 'in_progress', position, gate: null });
const runBlock = (current: string | null, at = T(0)): ContentBlock => ({
  type: 'pipeline_run', runId: 'run_t1', profile: 'goal', stages: ['evaluate', 'think', 'plan', 'goal_cycle', 'deliver', 'reflect'],
  requirement: 'fix the display', current, at,
}) as ContentBlock;
const th = (t: string): ContentBlock => ({ type: 'thinking', thinking: t }) as ContentBlock;
const toolIds = (units: { kind: string }[]) => units.flatMap((u) =>
  (u.kind === 'process' ? (u as { steps: { block: { id: string } }[] }).steps.map((s) => s.block.id) : []));

describe('(a) each stage owns the work from its start to the next start', () => {
  it('start rows open sections; the work after a start is that stage\'s', () => {
    const secs = stageRows(sections([
      start('evaluate', T(1)), tu('a'), res('a'), row('evaluate', T(3)),
      tu('b'), res('b'), // after evaluate ended, before think started: still EVALUATE's
      start('think', T(4), 2), tu('c'), res('c'), row('think', T(6), { position: 2 }), tx('done'),
    ]));
    expect(secs.map((s) => [s.block.stage, s.block.status, toolIds(s.children)])).toEqual([
      ['evaluate', 'completed', ['a', 'b']], ['think', 'completed', ['c']],
    ]);
    expect(secs[0].startedAt).toBe(T(1));
    expect(secs[1].startedAt).toBe(T(4));
  });

  it('a repeated start of the running stage does not open a second row', () => {
    const secs = stageRows(sections([start('build', T(1)), tu('a'), res('a'), start('build', T(2)), tu('b'), res('b')], true));
    expect(secs).toHaveLength(1);
    expect(toolIds(secs[0].children)).toEqual(['a', 'b']);
  });
});

describe('(b) the end block updates the row in place', () => {
  it('same section, same children, before and after the stage ends', () => {
    const before = [start('build', T(1)), tu('a'), res('a'), tu('b'), res('b')];
    const live = stageRows(sections(before, true));
    const done = stageRows(sections([...before, row('build', T(9))], true));
    expect(live.map((s) => [s.block.stage, s.block.status])).toEqual([['build', 'in_progress']]);
    expect(done.map((s) => [s.block.stage, s.block.status])).toEqual([['build', 'completed']]);
    expect(toolIds(done[0].children)).toEqual(toolIds(live[0].children));
    expect(done[0].startedAt).toBe(T(1));
  });

  it('while streaming, the started stage is the live one', () => {
    const view = deriveTurnView([start('build', T(1)), tu('a')], true);
    expect(view.liveStage?.stage).toBe('build');
  });
});

describe('(c) a resumed turn shows the run\'s current stage', () => {
  it('no stage rows: the run block\'s current stage is open, live, and holds the work', () => {
    const content = [runBlock('goal_cycle'), tu('a'), res('a'), tu('b')];
    const view = deriveTurnView(content, true);
    expect(view.liveStage?.stage).toBe('goal_cycle');
    const secs = stageRows(sectionUnits(view.units, true, view.liveStage, { keepLeadText: false }));
    expect(secs.map((s) => [s.block.stage, s.block.status])).toEqual([['goal_cycle', 'in_progress']]);
    expect(toolIds(secs[0].children)).toEqual(['a', 'b']);
  });

  it('a fresh run (no current stage yet) falls back to the plan\'s first stage', () => {
    expect(deriveTurnView([runBlock(null), tu('a')], true).liveStage?.stage).toBe('evaluate');
  });
});

describe('(d) first level of a pipeline turn: no WORKED before the first stage or after the last', () => {
  it('thoughts before the first stage go into it; work after the last row stays under it; the result stays on top', () => {
    const view = deriveTurnView([
      runBlock(null), th('reading the ask'), tu('pre'), res('pre'),
      start('evaluate', T(1)), tu('a'), res('a'), row('evaluate', T(2)),
      tu('post'), res('post'), tx('All done.'),
    ], false);
    const secs = sectionUnits(view.units, false, view.liveStage, { keepLeadText: false });
    const top = secs.filter((s) => s.kind === 'unit').map((s) => (s.kind === 'unit' ? s.unit.kind : ''));
    expect(top).toEqual(['outcome']);
    const [ev] = stageRows(secs);
    expect(toolIds(ev.children)).toEqual(['pre', 'a', 'post']);
    expect(ev.children.some((u) => u.kind === 'thought')).toBe(true);
  });

  it('end-based (older session): work after the last row stays under it once the turn is over', () => {
    const secs = sections([tu('a'), res('a'), row('build', T(4)), tu('b'), res('b'), tx('done')]);
    expect(secs.filter((s) => s.kind === 'unit').map((s) => (s.kind === 'unit' ? s.unit.kind : ''))).toEqual(['outcome']);
    expect(toolIds(stageRows(secs)[0].children)).toEqual(['a', 'b']);
  });
});

describe('gate rows sit next to the stage they guard (start-based)', () => {
  it('GATE 0 after EVALUATE, GATE 1 before BUILD, GATE 2 before DELIVER; none holds work', () => {
    const secs = stageRows(sections([
      start('evaluate', T(1)), tu('a'), res('a'), row('evaluate', T(2)), row('gate0', T(2)),
      start('build', T(3)), tu('b'), res('b'), row('gate1', T(5)), row('build', T(5)),
      start('deliver', T(6)), tu('c'), res('c'), row('gate2', T(8)), row('deliver', T(8)), tx('done'),
    ]));
    expect(secs.map((s) => s.block.stage)).toEqual(['evaluate', 'gate0', 'gate1', 'build', 'gate2', 'deliver']);
    expect(secs.filter((s) => s.block.stage.startsWith('gate')).every((s) => s.children.length === 0)).toBe(true);
    expect(toolIds(secs[3].children)).toEqual(['b']);
  });
});

describe('stripStageHeading — only the heading the row already says goes', () => {
  it('keeps the text after a stage heading and its → lines', () => {
    expect(stripStageHeading('## ✦ ⑤ BUILD\n→ done\n\nNext I spawn 2 reviewers because X')).toBe('Next I spawn 2 reviewers because X');
    expect(stripStageHeading('## ✦ ⑤ BUILD\n→ done')).toBe('');
  });
  it('a gate heading that is not a pass keeps its reasons; a pass goes', () => {
    expect(stripStageHeading('## ★ Gate 1: WARN — findings A, B')).toBe('## ★ Gate 1: WARN — findings A, B');
    expect(stripStageHeading('## ★ Gate 0: SUPPORTED')).toBe('');
  });
  it('text without a stage heading is unchanged', () => {
    expect(stripStageHeading('Plain note\n## ✦ later')).toBe('Plain note\n## ✦ later');
  });
  it('in a pipeline turn the rest of a heading text is shown', () => {
    const secs = sections([row('plan', T(1)), tu('a'), res('a'), tx('## ✦ ⑤ BUILD\n→ ok\n\nKEEP_THIS_LINE'), tu('b'), res('b'), row('build', T(4))]);
    expect(JSON.stringify(secs)).toContain('KEEP_THIS_LINE');
  });
});

describe('round-2 adversarial findings', () => {
  it('H1: the answer streaming after a stage ended stays at the top, not in the (folding) stage', () => {
    const secs = sections([start('reflect', T(1)), tu('a'), res('a'), row('reflect', T(2)), tx('Pipeline done — summary…')], true);
    const top = secs.filter((s) => s.kind === 'unit').map((s) => (s.kind === 'unit' ? s.unit.kind : ''));
    expect(top).toEqual(['outcome']);
    expect(stageRows(secs)[0].children.some((u) => u.kind === 'outcome')).toBe(false);
  });

  it('M2: a run announced by the call that also starts its current stage gives one row', () => {
    // run block written from the AFTER state: current = deliver, started by this very call
    const content = [runBlock('deliver'), tu('a'), res('a'), row('goal_cycle', T(2)), start('deliver', T(2)), tu('b'), res('b')];
    const view = deriveTurnView(content, true);
    const secs = stageRows(sectionUnits(view.units, true, view.liveStage, { keepLeadText: false }));
    expect(secs.map((s) => [s.block.stage, s.block.status])).toEqual([['goal_cycle', 'completed'], ['deliver', 'in_progress']]);
    expect(toolIds(secs[0].children)).toEqual(['a']);
    expect(toolIds(secs[1].children)).toEqual(['b']);
  });

  it('L2: work after a final SKIPPED row stays under the last row that ran', () => {
    const secs = stageRows(sections([start('test', T(1)), tu('a'), res('a'), row('test', T(2)), row('review', T(2), { status: 'skipped' }), tu('b'), res('b'), tx('done')]));
    expect(secs.map((s) => [s.block.stage, toolIds(s.children)])).toEqual([['test', ['a', 'b']], ['review', []]]);
  });

  it('L2: a NO-GO or a later PASS on the heading line does not make a gate heading a pass', () => {
    expect(stripStageHeading('## ★ Gate 0: NO-GO — wrong frame')).toBe('## ★ Gate 0: NO-GO — wrong frame');
    expect(stripStageHeading('## ★ Gate 1: WARN (checks 1-3 PASS)')).toBe('## ★ Gate 1: WARN (checks 1-3 PASS)');
    expect(stripStageHeading('## ★ Gate 2: **PASS**')).toBe('');
  });
});

describe('Gate-2 findings', () => {
  it('HIGH: an uppercase non-pass gate heading (the pipeline\'s own format) keeps its reasons', () => {
    expect(stripStageHeading('## ★ GATE 1: BLOCK | AC3 has no test')).toBe('## ★ GATE 1: BLOCK | AC3 has no test');
    expect(stripStageHeading('## ★ GATE 0: WRONG-FRAME\n→ reframe')).toBe('## ★ GATE 0: WRONG-FRAME\n→ reframe');
    expect(stripStageHeading('## ★ GATE 2: PASS')).toBe('');
    const view = deriveTurnView([row('plan', T(1)), tu('a'), res('a'), tx('## ★ GATE 1: BLOCK | AC3 has no test'), tu('b')], true);
    expect(view.units.some((u) => u.kind === 'landmark' && u.blocks)).toBe(true);
  });

  it('MED: an end block with no start takes the work after the previous started stage ended', () => {
    const secs = stageRows(sections([start('build', T(1)), tu('a'), res('a'), row('build', T(2)), tu('b'), res('b'), row('review', T(3)), tx('done')]));
    expect(secs.map((s) => [s.block.stage, toolIds(s.children)])).toEqual([['build', ['a']], ['review', ['b']]]);
  });
});
