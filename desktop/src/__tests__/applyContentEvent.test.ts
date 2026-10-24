/**
 * applyContentEvent — the stream handler's content reducer (design
 * 2026-10-24-chat-stream-structure.md A1).
 *
 * What is tested: each content event maps to its block updater (blocks only), as a
 * pure function: it returns the new message and whether a fresh start continued
 * the block the last provisional block streams (the handler logs that); events
 * without content return the same message reference.
 */
import { describe, it, expect } from 'vitest';
import { applyContentEvent } from '../hooks/useChatStreamingLifecycle';
import type { Message, StreamEvent } from '../types';

const msg = (content: Message['content'] = []): Message => ({ id: 'a', role: 'assistant', content, timestamp: '' });
const ev = (e: Record<string, unknown>) => e as unknown as StreamEvent;
const run = (m: Message, e: Record<string, unknown>, ctx = {}) => applyContentEvent(m, ev(e), ctx).msg;

describe('applyContentEvent', () => {
  it('text and thinking deltas append to the provisional block; fresh opens a new one', () => {
    let m = run(msg(), { type: 'text_delta', text: 'he', block: 'r.1:0' }, { fresh: true });
    m = run(m, { type: 'text_delta', text: 'llo' });
    expect(m.content).toEqual([{ type: 'text', text: 'hello', _streamBlock: 'r.1:0' }]);
    m = run(m, { type: 'thinking_delta', thinking: 'hm' }, { fresh: true });
    expect(m.content.map((b) => b.type)).toEqual(['text', 'thinking']);
  });

  it('a repeated start of the same block continues it and says so; another key opens a block', () => {
    const first = applyContentEvent(msg(), ev({ type: 'text_delta', text: 'a', block: 'r.1:0' }), { fresh: true });
    expect(first.restarted).toBe(false);
    const again = applyContentEvent(first.msg, ev({ type: 'text_delta', text: 'b', block: 'r.1:0' }), { fresh: true });
    expect(again.restarted).toBe(true);
    expect(again.msg.content).toEqual([{ type: 'text', text: 'ab', _streamBlock: 'r.1:0' }]);
    const other = applyContentEvent(again.msg, ev({ type: 'text_delta', text: 'c', block: 'r.1:1' }), { fresh: true });
    expect(other.restarted).toBe(false);
    expect(other.msg.content).toHaveLength(2);
    const plain = applyContentEvent(other.msg, ev({ type: 'text_delta', text: 'd' }));
    expect(plain.restarted).toBe(false);
  });

  it('assistant confirms the provisional block in place and carries the model; tool_result dedups by id', () => {
    let m = run(msg(), { type: 'text_delta', text: 'done' }, { fresh: true });
    m = run(m, { type: 'assistant', model: 'claude-x', content: [{ type: 'text', text: 'done' }] });
    expect(m.content).toHaveLength(1);
    expect(m.model).toBe('claude-x');
    const r = { type: 'tool_result', content: [{ type: 'tool_result', toolUseId: 't1', content: 'ok' }] };
    m = run(run(m, r), r);
    expect(m.content.filter((b) => b.type === 'tool_result')).toHaveLength(1);
  });

  it('turnStart: blocks of earlier turns in a continued bubble are never matched', () => {
    const earlier = msg([{ type: 'text', text: 'same', _confirmed: true } as never]);
    // the new turn's text equals the earlier one: both stay
    const m = run(run(earlier, { type: 'text_delta', text: 'same' }, { fresh: true }),
      { type: 'assistant', content: [{ type: 'text', text: 'same' }] }, { turnStart: 1 });
    expect(m.content.filter((b) => b.type === 'text')).toHaveLength(2);
  });

  it('subagent_progress is a side field, never a block', () => {
    const m = run(msg(), { type: 'subagent_progress', parentToolUseId: 'p', status: 'running', toolUses: 3 });
    expect(m.content).toEqual([]);
    expect(m.subAgentProgress?.p).toMatchObject({ status: 'running', toolUses: 3 });
  });

  it('non-content events and empty payloads return the same message', () => {
    const m = msg([{ type: 'text', text: 'x' }]);
    for (const e of [{ type: 'turn_end' }, { type: 'text_delta', text: '' }, { type: 'assistant' }, { type: 'session_activity' }]) {
      const r = applyContentEvent(m, ev(e));
      expect(r.msg, String(e.type)).toBe(m);
      expect(r.restarted).toBe(false);
    }
  });
});
