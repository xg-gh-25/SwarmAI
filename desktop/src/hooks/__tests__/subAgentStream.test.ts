/**
 * Sub-agent stream plumbing — the pure helpers behind "a running sub-agent
 * keeps the chat alive" (XG 2026-10-18).
 *
 * What is tested:
 * - applySubAgentProgress: merges `subagent_progress` events per Agent id on a
 *   side field (never a content block, so the streaming text block and block
 *   indices are untouched); later events update, absent fields are kept
 * - openToolState: which tool calls still wait for a result — any tool, and an
 *   open Agent (the longest stall threshold); answered calls and
 *   AskUserQuestion do not count
 * - toCamelCaseContentBlock: `parent_tool_use_id` → `parentToolUseId` on
 *   tool_use and tool_result blocks (live SSE and history share it)
 */
import { describe, it, expect } from 'vitest';
import { applySubAgentProgress, openToolState } from '../useChatStreamingLifecycle';
import { toCamelCaseContentBlock } from '../../services/chat';
import type { ContentBlock, Message, StreamEvent } from '../../types';

const msg = (content: ContentBlock[]): Message =>
  ({ id: 'm', role: 'assistant', content, timestamp: '2026-10-18T00:00:00Z' });
const tu = (id: string, name: string, extra: Record<string, unknown> = {}) =>
  ({ type: 'tool_use', id, name, summary: name, ...extra }) as ContentBlock;
const tr = (id: string) => ({ type: 'tool_result', toolUseId: id, isError: false }) as ContentBlock;

describe('applySubAgentProgress', () => {
  it('keeps the latest status per Agent id on a side field, content untouched', () => {
    const base = msg([{ type: 'text', text: 'streaming' } as ContentBlock]);
    const ev = (e: Partial<StreamEvent>) => ({ type: 'subagent_progress', ...e }) as StreamEvent;
    const a = applySubAgentProgress(base, ev({ parentToolUseId: 'ag', status: 'running', description: 'Review' }));
    const b = applySubAgentProgress(a, ev({ parentToolUseId: 'ag', toolUses: 3, durationMs: 4000, lastToolName: 'Read' }));
    const c = applySubAgentProgress(b, ev({ parentToolUseId: 'ag', status: 'completed', summary: 'ok' }));
    expect(c.content).toBe(base.content);
    expect(c.subAgentProgress?.ag).toEqual({
      parentToolUseId: 'ag', status: 'completed', description: 'Review',
      toolUses: 3, durationMs: 4000, lastToolName: 'Read', summary: 'ok',
    });
    expect(applySubAgentProgress(base, ev({ status: 'running' }))).toBe(base);
  });
});

describe('openToolState', () => {
  it('counts calls still waiting for a result; an open Agent is flagged', () => {
    expect(openToolState(msg([tu('a', 'Bash'), tr('a')]))).toEqual({ tool: false, agent: false });
    expect(openToolState(msg([tu('a', 'Bash')]))).toEqual({ tool: true, agent: false });
    expect(openToolState(msg([tu('ag', 'Agent', { category: 'agent' }), tu('s', 'Read', { parentToolUseId: 'ag' }), tr('s')])))
      .toEqual({ tool: true, agent: true });
    expect(openToolState(msg([tu('q', 'AskUserQuestion')]))).toEqual({ tool: false, agent: false });
    expect(openToolState(undefined)).toEqual({ tool: false, agent: false });
  });
});

describe('toCamelCaseContentBlock — sub-agent parent', () => {
  it('maps parent_tool_use_id on tool_use and tool_result', () => {
    const use = toCamelCaseContentBlock({ type: 'tool_use', id: 's', name: 'Read', summary: 'x', parent_tool_use_id: 'ag' });
    expect(use).toMatchObject({ parentToolUseId: 'ag' });
    expect('parent_tool_use_id' in use).toBe(false);
    const res = toCamelCaseContentBlock({ type: 'tool_result', tool_use_id: 's', is_error: false, parent_tool_use_id: 'ag' });
    expect(res).toMatchObject({ toolUseId: 's', isError: false, parentToolUseId: 'ag' });
    expect('parent_tool_use_id' in res).toBe(false);
  });
});
