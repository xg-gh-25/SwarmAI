/**
 * deriveTurnView: how a turn that ENDED reports its unfinished calls, and what
 * an Agent step keeps of its sub-agent after the stream (XG 2026-10-18 list #3, #4).
 *
 * What is tested (pure projection):
 * - a finished turn whose calls record results: every call with no result and
 *   nothing written after it is 'unknown' (stopped / cut off) — parallel calls
 *   together, not only the last. Older history recorded no results: only the last
 *   call is unknown there (a later call is the only sign an earlier one returned)
 * - a call followed by text, or with a result, is done; streaming keeps 'pending'
 * - an Agent step's sub-agent outcome: live progress while streaming, else the
 *   persisted `result.subagent`; a failed / stopped sub-agent leaves its
 *   unfinished child steps 'unknown', a completed one 'done'
 */
import { describe, it, expect } from 'vitest';
import { deriveTurnView } from './turnView';
import type { ProcessUnit, ToolStep } from './turnView';
import type { ContentBlock, SubAgentProgress } from '../../types';

const tu = (id: string, name = 'Bash', parent?: string): ContentBlock =>
  ({ type: 'tool_use', id, name, summary: `Running: ${id}`, category: 'bash', ...(parent ? { parentToolUseId: parent } : {}) }) as ContentBlock;
const tr = (id: string, extra: Record<string, unknown> = {}): ContentBlock =>
  ({ type: 'tool_result', toolUseId: id, isError: false, outcome: 'ok', ...extra }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;

const steps = (content: ContentBlock[], streaming = false, progress?: Record<string, SubAgentProgress>) =>
  deriveTurnView(content, streaming, progress).units
    .filter((u): u is ProcessUnit => u.kind === 'process')
    .flatMap((u) => u.steps as ToolStep[]);
const statusOf = (content: ContentBlock[], streaming = false) =>
  Object.fromEntries(steps(content, streaming).map((s) => [s.block.id, s.status]));

describe('a turn that ended mid-work', () => {
  it('parallel calls without results are all unknown, not only the last', () => {
    expect(statusOf([tu('x'), tr('x'), tx('go'), tu('a'), tu('b'), tu('c')]))
      .toEqual({ x: 'done', a: 'unknown', b: 'unknown', c: 'unknown' });
  });
  it('older history without any results: only the last call is unknown', () => {
    expect(statusOf([tu('a'), tu('b'), tu('c')])).toEqual({ a: 'done', b: 'done', c: 'unknown' });
  });
  it('a result or later text still proves done', () => {
    expect(statusOf([tu('a'), tu('b'), tr('b')])).toEqual({ a: 'unknown', b: 'done' });
    expect(statusOf([tu('a'), tu('b'), tx('both back')])).toEqual({ a: 'done', b: 'done' });
  });
  it('while streaming they are pending', () => {
    expect(statusOf([tu('a'), tu('b')], true)).toEqual({ a: 'pending', b: 'pending' });
  });
});

describe('an Agent step keeps its sub-agent outcome', () => {
  const agent = (sub: Record<string, unknown> | undefined) => [
    tu('ag', 'Agent'), tu('s1', 'Read', 'ag'), tr('s1', { parentToolUseId: 'ag' }), tu('s2', 'Read', 'ag'),
    tr('ag', sub ? { subagent: sub } : {}), tx('done'),
  ];
  const agentStep = (content: ContentBlock[], streaming = false, progress?: Record<string, SubAgentProgress>) =>
    steps(content, streaming, progress).find((s) => s.block.id === 'ag')!;

  it('after the stream: the persisted outcome, and a stopped sub-agent leaves its open step unknown', () => {
    const s = agentStep(agent({ status: 'stopped', toolUses: 2, durationMs: 9000, summary: 'Stopped early' }));
    expect(s.progress).toEqual({ parentToolUseId: 'ag', status: 'stopped', toolUses: 2, durationMs: 9000, summary: 'Stopped early' });
    expect(s.children!.map((c) => c.status)).toEqual(['done', 'unknown']);
  });
  it('a completed sub-agent: its open steps count as done', () => {
    const s = agentStep(agent({ status: 'completed', toolUses: 2 }));
    expect(s.progress!.status).toBe('completed');
    expect(s.children!.map((c) => c.status)).toEqual(['done', 'done']);
  });
  it('no outcome recorded: no progress, and the old rule (the parent finished → done)', () => {
    const s = agentStep(agent(undefined));
    expect(s.progress).toBeUndefined();
    expect(s.children!.map((c) => c.status)).toEqual(['done', 'done']);
  });
  it('while streaming the live progress wins; once over, live progress is ignored', () => {
    const live = { ag: { parentToolUseId: 'ag', status: 'running', toolUses: 5 } as SubAgentProgress };
    const content = [tu('ag', 'Agent'), tu('s1', 'Read', 'ag')];
    expect(agentStep(content, true, live).progress).toEqual(live.ag);
    expect(agentStep(agent({ status: 'failed' }), false, live).progress!.status).toBe('failed');
  });
});
