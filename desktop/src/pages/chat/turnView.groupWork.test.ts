/**
 * groupWork — folding a turn's process into work groups (chat display v7.11).
 *
 * What is tested: the pure `groupWork(units, isStreaming)` pass over the units
 * `deriveTurnView` produces, and the intent rule `deriveTurnView` owns.
 * - A group is a thought plus the tool calls it leads to; it closes at a note to
 *   the user, a landmark / stage / interrupt / outcome.
 * - A note ending in ":" is the NEXT group's title, never an item of its own.
 * - A thought after tool calls that leads to no further tool call before the
 *   next boundary reflects on that work and stays in the group.
 * - While streaming only the last group is live; group ids survive the
 *   stream-end reconcile (they come from the first member's stable key).
 * - The intent (Approach) is the first >=160-char thought within the first two
 *   tool calls, else the first thought before any tool call; it is not grouped.
 *
 * Fixtures mirror the persisted shape (tool_use + text + thinking; results only
 * while live).
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
const shape = (items: WorkItem[]) => items.map((i) => (i.kind === 'group'
  ? `G(${i.members.map((m) => (m.kind === 'thought' ? 't' : `p${m.steps.length}`)).join('')})`
  : i.kind));

describe('groupWork — boundaries', () => {
  it('a thought and the tool calls it leads to form one group; the next lead thought opens another', () => {
    const v = deriveTurnView([th('read the store'), tu('a', 'Reading /r/a.ts', 'Read', 'read'), tu('b', 'Reading /r/b.ts', 'Read', 'read'),
      th('now search the caller'), tu('c', 'Running: grep -n foo /r/c.ts'), tx('done')], false);
    // the short opener before any tool call is the intent (passed through)
    expect(shape(groupWork(v.units, false))).toEqual(['thought', 'G(p2)', 'G(tp1)', 'outcome']);
  });

  it('a thought that reflects on finished work (no tool call before the next note) stays in the group', () => {
    const v = deriveTurnView([th('run the test'), tu('a', 'Running: pytest t.py'), th('as expected it fails'),
      tx('RED as expected.'), tu('b', 'Editing /r/x.py', 'Edit', 'edit'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['thought', 'G(p1t)', 'note', 'G(p1)', 'outcome']);
  });

  it('a note ending in ":" titles the next group and is not an item of its own', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('Now the two docs:'), tu('b', 'Editing /r/plan.md', 'Edit', 'edit'), tx('done')], false);
    const items = groupWork(v.units, false);
    expect(shape(items)).toEqual(['G(p1)', 'G(p1)', 'outcome']);
    expect(groupsOf(items)[1].title).toBe('Now the two docs');
  });

  it('a note to the user splits groups and stays visible', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('The store loses the thought on reconcile.'), tu('b', 'Running: ls -la'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(p1)', 'note', 'G(p1)', 'outcome']);
  });

  it('a stage row splits groups', () => {
    const stage = { type: 'pipeline_stage', runId: 'run_x', stage: 'plan', status: 'completed', ordinal: 3, position: 3, total: 8,
      profile: 'full', next: 'build', nextOrdinal: 5, gate: null, summary: null, at: '2026-10-18T10:00:00+08:00' } as unknown as ContentBlock;
    const v = deriveTurnView([tu('a', 'Running: ls'), stage, tu('b', 'Running: ls -la'), tx('done')], false);
    expect(shape(groupWork(v.units, false))).toEqual(['G(p1)', 'stage', 'G(p1)', 'outcome']);
  });

  it('a group with only thoughts has no tool calls', () => {
    const v = deriveTurnView([tu('a', 'Running: ls'), tx('Here is the plan.'), th('weigh the options'), tx('Answer.')], false);
    const g = groupsOf(groupWork(v.units, false));
    expect(g.map((x) => x.hasTools)).toEqual([true, false]);
  });
});

describe('groupWork — live and stable ids', () => {
  it('while streaming only the last group is live', () => {
    const blocks = [th('first'), tu('a', 'Running: ls'), tr('a'), th('second'), tu('b', 'Running: ls -la')];
    const g = groupsOf(groupWork(deriveTurnView(blocks, true).units, true));
    expect(g.map((x) => x.live)).toEqual([false, true]);
  });

  it('the streaming answer is never a title, even when it ends in ":" (Gate-2: no flip)', () => {
    // "Here are three options:" is the answer streaming, not a lead-in: it would flip
    // into a card and back on the next token. It titles work only once a tool follows.
    const streaming = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Recording files, then VERIFY:')], true).units, true);
    expect(streaming[streaming.length - 1].kind).toBe('outcome');
    const answer = groupWork(deriveTurnView([tx('Here are three options:')], true).units, true);
    expect(answer.map((i) => i.kind)).toEqual(['outcome']);
    const followed = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Recording files, then VERIFY:'), tu('b', 'Running: make')], true).units, true);
    const g = groupsOf(followed);
    expect(g[g.length - 1].title).toBe('Recording files, then VERIFY');
  });

  it('a multi-line or long ":" note stays a note — a one-line title would hide the rest', () => {
    const multi = 'Root cause: the store drops X on reconcile.\n\nNow fixing the two files:';
    const items = groupWork(deriveTurnView([tu('a', 'Running: ls'), tx(multi), tu('b', 'Running: make'), tx('done')], false).units, false);
    expect(items.some((i) => i.kind === 'note' && i.text === multi)).toBe(true);
    expect(groupsOf(items).every((g) => !g.title)).toBe(true);
    const long = `${'x'.repeat(130)}:`;
    const items2 = groupWork(deriveTurnView([tu('a', 'Running: ls'), tx(long), tu('b', 'Running: make'), tx('done')], false).units, false);
    expect(items2.some((i) => i.kind === 'note')).toBe(true);
  });

  it('when the intent moves to a later thought, the work group keeps its id (Gate-2: no remount)', () => {
    const real = `The ask is to fix the reconcile race in the store. ${'x'.repeat(160)}`;
    // while the long thought still streams it is not the intent yet …
    const live = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real)], true);
    expect(live.units.find((u) => u.kind === 'thought' && u.intent)).toMatchObject({ text: 'Let me look at the image first.' });
    const before = groupsOf(groupWork(live.units, true))[0].id;
    // … once it is finished it is, and the read's group is still the same group.
    const done = deriveTurnView([th('Let me look at the image first.'), tu('a', 'Reading /r/a.png', 'Read', 'read'), tr('a'), th(real), tu('b', 'Running: ls')], true);
    expect(done.units.find((u) => u.kind === 'thought' && u.intent)).toMatchObject({ text: real });
    expect(groupsOf(groupWork(done.units, true))[0].id).toBe(before);
  });

  it('group ids are the same while streaming and after the stream-end reconcile', () => {
    const live = [th('first'), tu('a', 'Running: ls'), tr('a'), th('second'), tu('b', 'Running: ls -la'), tr('b')];
    const done = [th('first'), tu('a', 'Running: ls'), th('second'), tu('b', 'Running: ls -la'), tx('done')];
    const ids = (blocks: ContentBlock[], s: boolean) => groupsOf(groupWork(deriveTurnView(blocks, s).units, s)).map((x) => x.id);
    expect(ids(live, true)).toEqual(ids(done, false));
  });

  it('a finished turn has no live group', () => {
    const v = deriveTurnView([th('x'), tu('a', 'Running: ls'), tx('done')], false);
    expect(groupsOf(groupWork(v.units, false)).some((g) => g.live)).toBe(false);
  });
});

describe('intent (Approach) — owned by deriveTurnView, not grouped', () => {
  const intentText = (blocks: ContentBlock[]) => {
    const u = deriveTurnView(blocks, false).units.find((x) => x.kind === 'thought' && x.intent);
    return u && u.kind === 'thought' ? u.text : undefined;
  };

  it('a short opener before the first tool call gives way to the first long thought within two tool calls', () => {
    expect(intentText([th('Let me look at the image first.'), tu('a', 'Reading /r/shot.png', 'Read', 'read'), th(LONG),
      tu('b', 'Running: git log'), tx('done')])).toBe(LONG);
  });

  it('a long thought before any tool call is the intent', () => {
    expect(intentText([th(LONG), tu('a', 'Running: ls'), th(`${LONG} again`), tx('done')])).toBe(LONG);
  });

  it('a long thought after the second tool call is not the intent; the short opener is', () => {
    expect(intentText([th('peek first'), tu('a', 'Running: ls'), tu('b', 'Running: ls -la'), tx('Checking:'),
      tu('c', 'Running: pwd'), th(LONG), tx('done')])).toBe('peek first');
  });

  it('the intent thought is passed through, never inside a group', () => {
    const v = deriveTurnView([th(LONG), tu('a', 'Running: ls'), tx('done')], false);
    const items = groupWork(v.units, false);
    expect(items[0].kind).toBe('thought');
    expect(groupsOf(items).flatMap((g) => g.members).some((m) => m.kind === 'thought' && m.intent)).toBe(false);
  });
});

describe('groupWork — the live placeholder (Gate: spec-compliance)', () => {
  const STAGE = {
    type: 'pipeline_stage', runId: 'run_t', stage: 'evaluate', status: 'completed', ordinal: 1, position: 1, total: 3,
    profile: 'full', next: 'think', nextOrdinal: 2, gate: null, summary: 'GO', at: '2026-10-18T00:00:00Z',
  } as ContentBlock;

  it('streaming with nothing started since a boundary: one empty live group holds the spinner', () => {
    const units = deriveTurnView([tu('a', 'Running: ls'), tr('a'), STAGE], true).units;
    const items = groupWork(units, true);
    const last = items[items.length - 1];
    expect(last.kind).toBe('group');
    expect(last.kind === 'group' && last.live && !last.hasTools).toBe(true);
    expect(groupWork([], true)).toHaveLength(1);
  });

  it('no placeholder once the turn is over, nor after the answer or a pending question starts', () => {
    expect(groupWork(deriveTurnView([tu('a', 'Running: ls'), STAGE], false).units, false).some((i) => i.kind === 'group' && i.live)).toBe(false);
    const answering = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), tx('Here is the answer')], true).units, true);
    expect(answering[answering.length - 1].kind).toBe('outcome');
    const ask = { type: 'ask_user_question', toolUseId: 'q', questions: [] } as unknown as ContentBlock;
    const asking = groupWork(deriveTurnView([tu('a', 'Running: ls'), tr('a'), ask], true).units, true);
    expect(asking[asking.length - 1].kind).toBe('interrupt');
  });
});
