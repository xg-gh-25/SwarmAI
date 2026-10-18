/**
 * groupWork — a turn's two levels (chat display v7.12).
 *
 * What is tested: the pure `groupWork(units, isStreaming, opts)` pass over the units
 * `deriveTurnView` produces.
 * - FIRST level: the intent, WORKING / WORKED blocks, stage rows, interrupts, the
 *   result. A thought, a note or a landmark is never first level on its own.
 * - A block is EVERYTHING between two first-level items: thoughts, notes, landmarks
 *   and process segments, in stream order.
 * - `exclude` (the intent restatement) and the intent thought (`keepIntent`) stay
 *   first level; with keepIntent false the intent thought is an ordinary member.
 * - While streaming only the last block is live; an empty live block holds the
 *   spinner after a stage row; ids survive the stream-end reconcile.
 * - The intent rule `deriveTurnView` owns (first >=160-char thought within two tool
 *   calls, else the first thought before any) still decides which thought that is.
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
  it('thoughts, notes and tool calls between the intent and the answer are ONE block, in order', () => {
    const v = deriveTurnView([th(LONG), tu('a', 'Running: ls'), tx('found the store'), th('next the reader'),
      tu('b', 'Reading /r/a.ts', 'Read', 'read'), th('so it is the reconcile'), tu('c', 'Running: make'), tx('Done.')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['thought', 'G(p1ntp1tp1)', 'outcome']);
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

  it('the excluded restatement stays first level; with another intent source the opening thought is a member', () => {
    const v = deriveTurnView([th('plan'), tx('I read it as: fix the cache.'), tu('a', 'Running: ls'), tx('done')], false);
    const note = v.units.find((u) => u.kind === 'note')!;
    // the thought before the restatement joins the work it led to, after the intent
    expect(shape(groupWork(v.units, false, { exclude: note, keepIntent: false }))).toEqual(['note', 'G(tp1)', 'outcome']);
    expect(shape(groupWork(v.units, false, { exclude: note }))).toEqual(['thought', 'note', 'G(p1)', 'outcome']);
  });

  it('while streaming (mergeLead false) the thought before the restatement keeps its place above it', () => {
    const v = deriveTurnView([th('plan'), tx('I read it as: fix the cache.'), tu('a', 'Running: ls')], true);
    const note = v.units.find((u) => u.kind === 'note')!;
    expect(shape(groupWork(v.units, true, { exclude: note, keepIntent: false, mergeLead: false }))).toEqual(['G(t)', 'note', 'G(p1)']);
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

  it('when the intent moves to a later thought the block keeps its id', () => {
    const real = `The ask is to fix the reconcile race in the store. ${'x'.repeat(160)}`;
    const live = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real)], true);
    const done = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real), tu('b', 'Running: ls')], true);
    expect(groupsOf(groupWork(done.units, true))[0].id).toBe(groupsOf(groupWork(live.units, true))[0].id);
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
