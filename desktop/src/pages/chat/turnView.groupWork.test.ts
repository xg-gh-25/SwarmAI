/**
 * groupWork — a turn's two levels (chat display v7.12).
 *
 * What is tested: the pure `groupWork(units, isStreaming, opts)` pass over the units
 * `deriveTurnView` produces.
 * - FIRST level: the opening restatement, WORKING / WORKED blocks, stage rows,
 *   interrupts, the result. A thought, a note or a landmark is never first level on
 *   its own (there is no intent thought, chat display v8).
 * - A block is EVERYTHING between two first-level items: thoughts, notes, landmarks
 *   and process segments, in stream order.
 * - `exclude` (the opening restatement) stays first level.
 * - While streaming only the last block is live; an empty live block holds the
 *   spinner after a stage row; ids survive the stream-end reconcile.
 *
 * Fixtures mirror the persisted shape (tool_use + text + thinking; results while live).
 */
import { describe, it, expect } from 'vitest';
import type { ContentBlock } from '../../types';
import { deriveTurnView, groupWork } from './turnView';
import type { WorkGroup, WorkItem } from './turnView';

const tu = (id: string, summary: string, name = 'Bash', category = 'bash'): ContentBlock =>
  ({ type: 'tool_use', id, name, summary, category }) as ContentBlock;
const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const th = (thinking: string): ContentBlock => ({ type: 'thinking', thinking }) as ContentBlock;
const LONG = 'I need to check this against the design philosophy guidelines and the latest implementation, '
  + 'since this is a design analysis task and the user wants adjustments, not code changes yet.';

const groupsOf = (items: WorkItem[]) => items.filter((i): i is WorkGroup => i.kind === 'group');
const memberShape = (g: WorkGroup) => g.members.map((m) => (m.kind === 'process' ? `p${m.steps.length}` : m.kind[0])).join('');
const shape = (items: WorkItem[]) => items.map((i) => (i.kind === 'group' ? `G(${memberShape(i)})` : i.kind));

describe('groupWork — one block between first-level items', () => {
  it('thoughts, notes and tool calls before the answer are ONE block, in order (the opening thought too)', () => {
    const v = deriveTurnView([th(LONG), tu('a', 'Running: ls'), tx('found the store'), th('next the reader'),
      tu('b', 'Reading /r/a.ts', 'Read', 'read'), th('so it is the reconcile'), tu('c', 'Running: make'), tx('Done.')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(tp1ntp1tp1)', 'outcome']);
  });

  it('a note or a thought never makes a first-level item of its own', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('a note'), tu('b', 'Running: pwd'), th('hm'), tu('c', 'Running: id'), tx('end')], false);
    const items = groupWork(v.units, false);
    expect(items.filter((i) => i.kind === 'note' || i.kind === 'thought')).toEqual([]);
    expect(groupsOf(items)).toHaveLength(1);
  });

  it('a landmark is a member too', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('## ✦ ① EVALUATE\n→ GO'), tu('b', 'Running: pwd'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(p1lp1)', 'outcome']);
  });

  it('an interrupt splits the work and stays first level', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), ask, tu('b', 'Running: pwd'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(p1)', 'interrupt', 'G(p1)', 'outcome']);
  });

  it('a stage row splits the work and stays first level', () => {
    const stage = { type: 'pipeline_stage', runId: 'r', stage: 'evaluate', status: 'completed', ordinal: 1, position: 1,
      total: 8, profile: 'full', next: 'think', nextOrdinal: 2, gate: null, summary: 'GO', at: 'T' } as ContentBlock;
    const v = deriveTurnView([tu('a', 'Running: ls'), stage, tu('b', 'Running: pwd'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(p1)', 'stage', 'G(p1)', 'outcome']);
  });

  it('the excluded restatement stays first level; the opening thought is a member of the work', () => {
    const v = deriveTurnView([th('plan'), tx('I read it as: fix the cache.'), tu('a', 'Running: ls'), tx('done')], false);
    const note = v.units.find((u) => u.kind === 'note')!;
    // the thought before the restatement joins the work it led to
    expect(shape(groupWork(v.units, false, { exclude: note }))).toEqual(['note', 'G(tp1)', 'outcome']);
  });

  it('while streaming (mergeLead false) the thought before the restatement keeps its place above it', () => {
    const v = deriveTurnView([th('plan'), tx('I read it as: fix the cache.'), tu('a', 'Running: ls')], true);
    const note = v.units.find((u) => u.kind === 'note')!;
    expect(shape(groupWork(v.units, true, { exclude: note, mergeLead: false }))).toEqual(['G(t)', 'note', 'G(p1)']);
  });
});

describe('groupWork — the answer-so-far while streaming (option B)', () => {
  it('stays first level; the block of work above it stays live (holdLiveAboveAnswer)', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Checking the store next.')], true);
    const items = groupWork(v.units, true, { holdLiveAboveAnswer: true });
    expect(shape(items)).toEqual(['G(p1)', 'outcome']);
    expect(groupsOf(items)[0].live).toBe(true);
    // without the option the block above stops being live (it folds)
    expect(groupsOf(groupWork(v.units, true))[0].live).toBe(false);
  });

  it('the next tool call: the text is a note in the same block, still live, same id', () => {
    const before = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Checking.')], true).units, true, { holdLiveAboveAnswer: true });
    const after = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Checking.'), tu('b', 'Running: pwd')], true).units, true, { holdLiveAboveAnswer: true });
    expect(shape(after)).toEqual(['G(p1np1)']);
    expect(groupsOf(after)[0].live).toBe(true);
    expect(groupsOf(after)[0].id).toBe(groupsOf(before)[0].id);
  });

  it('a thoughts-only block above an answer is not held open; nor anything once the turn is over', () => {
    const v = deriveTurnView([th('hm'), tx('Here is the answer.')], true);
    expect(groupsOf(groupWork(v.units, true, { holdLiveAboveAnswer: true }))[0].live).toBe(false);
    const done = deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Done.')], false);
    expect(groupsOf(groupWork(done.units, false, { holdLiveAboveAnswer: true }))[0].live).toBe(false);
  });

  it('text units carry a text ordinal that survives the stream-end reconcile', () => {
    const live = deriveTurnView([tx('I read it as X.'), tu('a', 'Running: ls'), tr('a'), tx('note'), tu('b', 'Running: pwd'), tr('b'), tx('done')], false);
    const stored = deriveTurnView([tx('I read it as X.'), tu('a', 'Running: ls'), tx('note'), tu('b', 'Running: pwd'), tx('done')], false);
    const ords = (v: typeof live) => v.units.flatMap((u) => ('textOrdinal' in u ? [`${u.kind}:${u.textOrdinal}`] : []));
    expect(ords(live)).toEqual(['note:0', 'note:1', 'outcome:2']);
    expect(ords(stored)).toEqual(ords(live));
    const ids = (v: typeof live) => groupsOf(groupWork(v.units, false)).map((g) => g.id);
    expect(ids(stored)).toEqual(ids(live));
  });

  it('a note joining a block in front of its first call (intent once pipeline_run lands) keeps the block id', () => {
    const blocks = [tx('I read it as X.'), tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd')];
    const v = deriveTurnView(blocks, true);
    const note = v.units.find((u) => u.kind === 'note')!;
    const excluded = groupsOf(groupWork(v.units, true, { exclude: note }))[0].id;
    const joined = groupsOf(groupWork(v.units, true))[0].id;
    expect(joined).toBe(excluded);
  });

  it('a block that starts with a note keeps its id when a tool call joins it', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const a = deriveTurnView([tu('a', 'Running: ls'), tr('a'), ask, tx('Going with A.'), tu('b', 'Running: pwd'), tx('done')], false);
    const b = deriveTurnView([tu('a', 'Running: ls'), tr('a'), ask, tx('Going with A.'), tu('b', 'Running: pwd'), tu('c', 'Running: id'), tx('done')], false);
    expect(groupsOf(groupWork(a.units, false))[1].id).toBe(groupsOf(groupWork(b.units, false))[1].id);
  });
});

describe('groupWork — live and stable ids', () => {
  it('while streaming only the last block is live', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const blocks = [tu('a', 'Running: ls'), tr('a'), ask, tu('b', 'Running: pwd')];
    const g = groupsOf(groupWork(deriveTurnView(blocks, true).units, true));
    expect(g.map((x) => x.live)).toEqual([false, true]);
  });

  it('the streaming answer stays first level', () => {
    const streaming = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Here are three options:')], true).units, true);
    expect(streaming[streaming.length - 1].kind).toBe('outcome');
  });

  it('block ids are the same while streaming and after the stream-end reconcile', () => {
    const live = [th('first'), tu('a', 'Running: ls'), tr('a'), th('second'), tu('b', 'Running: ls -la'), tr('b')];
    const done = [th('first'), tu('a', 'Running: ls'), th('second'), tu('b', 'Running: ls -la'), tx('done')];
    const ids = (blocks: ContentBlock[], s: boolean) => groupsOf(groupWork(deriveTurnView(blocks, s).units, s)).map((x) => x.id);
    expect(ids(live, true)).toEqual(ids(done, false));
  });

  it('a block keeps its id as later thoughts and calls join it', () => {
    const real = `The ask is to fix the reconcile race in the store. ${'x'.repeat(160)}`;
    const live = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real)], true);
    const done = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real), tu('b', 'Running: ls')], true);
    expect(groupsOf(groupWork(done.units, true))[0].id).toBe(groupsOf(groupWork(live.units, true))[0].id);
  });

  it('a thinking-only block keeps its id when its first tool call lands (no remount: the thought would fold on the spot)', () => {
    const ids = (blocks: ContentBlock[], s: boolean) => groupsOf(groupWork(deriveTurnView(blocks, s).units, s)).map((x) => x.id);
    const thinking = ids([th('weighing the two paths')], true);
    expect(thinking).toHaveLength(1);
    expect(ids([th('weighing the two paths'), tu('a', 'Running: ls')], true)).toEqual(thinking);
    expect(ids([th('weighing the two paths'), tu('a', 'Running: ls'), tr('a'), tx('done')], false)).toEqual(thinking);
  });

  it('a finished turn has no live block', () => {
    const v = deriveTurnView([th('x'), tu('a', 'Running: ls'), tx('done')], false);
    expect(groupsOf(groupWork(v.units, false)).some((g) => g.live)).toBe(false);
  });

  it('streaming with nothing started since a stage row: one empty live block', () => {
    const stage = { type: 'pipeline_stage', runId: 'r', stage: 'evaluate', status: 'completed', ordinal: 1, position: 1,
      total: 3, profile: 'full', next: 'think', nextOrdinal: 2, gate: null, summary: 'GO', at: 'T' } as ContentBlock;
    const items = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), stage], true).units, true);
    const last = items[items.length - 1];
    expect(last.kind === 'group' && last.live && !last.hasTools).toBe(true);
    expect(groupWork([], true)).toHaveLength(1);
  });
});
