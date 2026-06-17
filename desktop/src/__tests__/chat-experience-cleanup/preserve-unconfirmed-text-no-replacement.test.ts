/**
 * Bug fix test: Preserve unconfirmed text when assistant event has no text replacement.
 *
 * BUG (2026-06-18): In agentic multi-turn flows, the SDK emits intermediate
 * AssistantMessage events containing only thinking + tool_use (no text block).
 * The old updateMessages() unconditionally dropped all unconfirmed text blocks,
 * wiping streamed text the user was already seeing. The message bubble went blank
 * while the thinking bubble still showed content.
 *
 * FIX: Only drop unconfirmed text/thinking when the authoritative payload actually
 * provides a replacement of that type. Otherwise preserve the streaming provisional.
 *
 * Testing methodology:
 * - Bug condition: assistant event with NO text in newContent → unconfirmed text preserved
 * - Fix check: assistant event WITH text in newContent → unconfirmed text replaced (existing behavior)
 * - Preservation: all other invariants (dedup, confirmed blocks, tool blocks) unchanged
 */

import { describe, it, expect } from 'vitest';
import { updateMessages } from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock, TextContent, ThinkingContent, ToolUseContent } from '../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeMessage(id: string, content: ContentBlock[]): Message {
  return {
    id,
    role: 'assistant',
    content,
    timestamp: new Date().toISOString(),
  };
}

function textBlock(text: string, confirmed = false): TextContent {
  return { type: 'text', text, ...(confirmed ? { _confirmed: true } : {}) } as TextContent;
}

function thinkingBlock(thinking: string, confirmed = false): ThinkingContent {
  return { type: 'thinking', thinking, ...(confirmed ? { _confirmed: true } : {}) } as ThinkingContent;
}

function toolUseBlock(id: string, name = 'Read'): ToolUseContent {
  return { type: 'tool_use', id, name, summary: '', category: 'file' } as ToolUseContent;
}

// ---------------------------------------------------------------------------
// Bug Condition Tests — unconfirmed text preserved when no replacement
// ---------------------------------------------------------------------------

describe('BUG FIX 2026-06-18: Preserve unconfirmed text when assistant event has no text', () => {
  it('unconfirmed text survives when newContent has only thinking + tool_use', () => {
    // Simulate: streaming built up text, then SDK emits assistant event with thinking + tool_use only
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('Agent is thinking...'),      // unconfirmed thinking
        textBlock('Here is my response so far...'), // unconfirmed text (user was seeing this!)
      ]),
    ];

    // newContent: only thinking + tool_use, NO text
    const newContent: ContentBlock[] = [
      thinkingBlock('Agent is thinking...'),
      toolUseBlock('tool-1', 'Read'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // The unconfirmed text block MUST survive (it has no replacement)
    const textBlocks = target.content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBeGreaterThanOrEqual(1);
    expect((textBlocks[0] as TextContent).text).toBe('Here is my response so far...');
  });

  it('unconfirmed thinking is replaced when newContent has thinking (normal case)', () => {
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('Old streaming thinking'),
        textBlock('Streamed text'),
      ]),
    ];

    // newContent has thinking but no text
    const newContent: ContentBlock[] = [
      thinkingBlock('New authoritative thinking'),
      toolUseBlock('tool-1'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Thinking replaced by authoritative version
    const thinkingBlocks = target.content.filter(b => b.type === 'thinking');
    expect(thinkingBlocks.length).toBe(1);
    expect((thinkingBlocks[0] as ThinkingContent).thinking).toBe('New authoritative thinking');
    expect((thinkingBlocks[0] as ThinkingContent)._confirmed).toBe(true);

    // Text preserved (no text in newContent)
    const textBlocks = target.content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBe(1);
    expect((textBlocks[0] as TextContent).text).toBe('Streamed text');
  });

  it('unconfirmed text IS replaced when newContent has text (existing behavior preserved)', () => {
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('thinking'),
        textBlock('old streaming text'),
      ]),
    ];

    // newContent HAS text — should replace unconfirmed text (existing behavior)
    const newContent: ContentBlock[] = [
      textBlock('New authoritative text'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Old unconfirmed text dropped, new text added with _confirmed
    const textBlocks = target.content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBe(1);
    expect((textBlocks[0] as TextContent).text).toBe('New authoritative text');
    expect((textBlocks[0] as TextContent)._confirmed).toBe(true);
  });

  it('empty newContent preserves all unconfirmed blocks', () => {
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('streaming thinking'),
        textBlock('streaming text'),
      ]),
    ];

    // Completely empty newContent — preserve everything
    const newContent: ContentBlock[] = [];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Both blocks preserved
    const textBlocks = target.content.filter(b => b.type === 'text');
    const thinkingBlocks = target.content.filter(b => b.type === 'thinking');
    expect(textBlocks.length).toBe(1);
    expect(thinkingBlocks.length).toBe(1);
  });

  it('tool_use-only newContent preserves both unconfirmed text and thinking', () => {
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('streaming thinking'),
        textBlock('streaming text'),
        toolUseBlock('existing-tool'),
      ]),
    ];

    // Only tool_use in newContent — no text, no thinking
    const newContent: ContentBlock[] = [
      toolUseBlock('new-tool', 'Bash'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Both unconfirmed text and thinking preserved
    const textBlocks = target.content.filter(b => b.type === 'text');
    const thinkingBlocks = target.content.filter(b => b.type === 'thinking');
    expect(textBlocks.length).toBe(1);
    expect(thinkingBlocks.length).toBe(1);
    expect((textBlocks[0] as TextContent).text).toBe('streaming text');
    expect((thinkingBlocks[0] as ThinkingContent).thinking).toBe('streaming thinking');
  });

  it('confirmed blocks are never affected regardless of newContent shape', () => {
    const messages: Message[] = [
      makeMessage('msg-1', [
        thinkingBlock('confirmed thinking from prior turn', true),
        textBlock('confirmed text from prior turn', true),
        textBlock('unconfirmed streaming text'),
      ]),
    ];

    // newContent with text — should drop unconfirmed, keep confirmed
    const newContent: ContentBlock[] = [
      textBlock('new text'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Confirmed blocks survive
    const textBlocks = target.content.filter(b => b.type === 'text');
    const confirmedTexts = textBlocks.filter(b => (b as TextContent)._confirmed);
    expect(confirmedTexts.length).toBeGreaterThanOrEqual(1);

    // Thinking confirmed block survives
    const thinkingBlocks = target.content.filter(b => b.type === 'thinking');
    expect(thinkingBlocks.length).toBe(1);
    expect((thinkingBlocks[0] as ThinkingContent)._confirmed).toBe(true);
  });

  it('no content explosion — same-turn dedup still works with preserved unconfirmed text', () => {
    // Simulate the original 2026-06-07 bug scenario:
    // Multiple assistant events with growing text in the SAME turn
    const messages: Message[] = [
      makeMessage('msg-1', [
        textBlock('Hello, I will help you with this task.', true), // confirmed from prior event
      ]),
    ];

    // Same turn re-emits growing text — dedup should replace, not accumulate
    const newContent: ContentBlock[] = [
      textBlock('Hello, I will help you with this task. Let me start by reading the file.'),
      toolUseBlock('tool-1', 'Read'),
    ];

    const result = updateMessages(messages, 'msg-1', newContent);
    const target = result.find(m => m.id === 'msg-1')!;

    // Should NOT have 2 text blocks — dedup handles same-turn growth
    const textBlocks = target.content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBe(1);
    expect((textBlocks[0] as TextContent).text).toContain('Let me start by reading');
  });
});
