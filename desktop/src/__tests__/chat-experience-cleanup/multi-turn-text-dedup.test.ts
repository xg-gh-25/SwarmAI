/**
 * Test: Multi-turn text deduplication in updateMessages.
 *
 * What is being tested:
 *   When an agentic response spans multiple turns (text → tool_use → text),
 *   text_delta events accumulate ALL text into a single block, but the
 *   AssistantMessage events arrive per-turn with only that turn's text.
 *   The updateMessages function must NOT add duplicate text blocks.
 *
 * Testing methodology: Deterministic unit tests reproducing the exact
 * multi-turn flow that causes visible text duplication in the chat window.
 *
 * Key invariant: After all AssistantMessage events are processed, the
 * message should contain NO duplicate text content — text that was already
 * rendered via streaming must not be added again from AssistantMessage.
 */

import { describe, it, expect } from 'vitest';
import {
  updateMessages,
  appendTextDelta,
  blockKey,
} from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';

// Helper: create a base assistant message with empty content
function makeAssistantMessage(id: string, content: ContentBlock[] = []): Message {
  return {
    id,
    role: 'assistant',
    content,
    timestamp: new Date().toISOString(),
  };
}

describe('Multi-turn text deduplication', () => {
  /**
   * Reproduces the exact bug:
   * 1. text_delta builds "Hello world" (turn 1 text)
   * 2. AssistantMessage arrives with [{type: "text", text: "Hello world"}, {type: "tool_use", ...}]
   *    → updateMessages deduplicates text (same content), adds tool_use ✓
   * 3. text_delta appends " Final answer" → text becomes "Hello world Final answer"
   * 4. AssistantMessage arrives with [{type: "text", text: " Final answer"}]
   *    → BUG: blockKey("text: Final answer") ≠ existing blockKey("text:Hello world Final answer")
   *    → " Final answer" added as NEW block → user sees it twice
   */
  it('does NOT add duplicate text when AssistantMessage text is a suffix of streamed text', () => {
    const msgId = 'assistant-1';

    // Step 1: Start with empty assistant placeholder
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Step 2: text_delta accumulates turn 1 text
    messages = appendTextDelta(messages, msgId, 'Hello world');

    // Step 3: AssistantMessage for turn 1 (text + tool_use)
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: 'Hello world' } as ContentBlock,
      { type: 'tool_use', id: 'tool-1', name: 'WebFetch', summary: 'fetch', category: 'web' } as ContentBlock,
    ]);

    // Verify: text deduped, tool_use added
    const msg1 = messages.find(m => m.id === msgId)!;
    expect(msg1.content.filter(b => b.type === 'text')).toHaveLength(1);
    expect(msg1.content.filter(b => b.type === 'tool_use')).toHaveLength(1);

    // Step 4: text_delta accumulates turn 2 text (appended to existing block)
    messages = appendTextDelta(messages, msgId, '\n\nFinal answer with details.');

    // Step 5: AssistantMessage for turn 2 (only turn 2's text)
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: '\n\nFinal answer with details.' } as ContentBlock,
    ]);

    // CRITICAL ASSERTION: Should have exactly TWO text blocks (one per turn,
    // created by appendTextDelta when last block was tool_use) — but NOT three
    // (the AssistantMessage text must be deduped against the streaming text).
    const msg2 = messages.find(m => m.id === msgId)!;
    const textBlocks = msg2.content.filter(b => b.type === 'text');
    expect(textBlocks).toHaveLength(2);
    expect(textBlocks[0].text).toBe('Hello world');
    expect(textBlocks[1].text).toBe('\n\nFinal answer with details.');
  });

  it('does NOT add duplicate text when turn 2 text matches end of accumulated text', () => {
    const msgId = 'assistant-2';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Simulate: agent produces a summary, then uses WebFetch, then reformats
    const turn1Text = '## Summary\n\nThis article discusses AI.';
    const turn2Text = '\n\n## Detailed Analysis\n\nThe key points are:\n1. Point A\n2. Point B';

    // Stream turn 1
    messages = appendTextDelta(messages, msgId, turn1Text);

    // AssistantMessage turn 1 with tool_use
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: turn1Text } as ContentBlock,
      { type: 'tool_use', id: 'tool-2', name: 'Read', summary: 'read', category: 'file' } as ContentBlock,
    ]);

    // tool_result arrives
    messages = updateMessages(messages, msgId, [
      { type: 'tool_result', toolUseId: 'tool-2', content: 'file content...', is_error: false, truncated: false } as ContentBlock,
    ]);

    // Stream turn 2
    messages = appendTextDelta(messages, msgId, turn2Text);

    // AssistantMessage turn 2 — ONLY has turn 2's text
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: turn2Text } as ContentBlock,
    ]);

    // Should have exactly 2 text blocks (one per turn — appendTextDelta creates
    // a new block when the last content block is tool_result, not text).
    // The KEY assertion: NOT 3 blocks (no duplicate from AssistantMessage).
    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    expect(textBlocks).toHaveLength(2);
    expect(textBlocks[0].text).toBe(turn1Text);
    expect(textBlocks[1].text).toBe(turn2Text);
  });

  it('handles case where appendTextDelta builds combined text before AssistantMessage arrives', () => {
    // This is the TRUE bug scenario: text_delta from turn 2 arrives and
    // appends to the streaming text block BEFORE the turn-1 AssistantMessage
    // has been processed (e.g., on fast responses where events batch).
    // OR: the SDK sends accumulated text in the AssistantMessage that includes
    // text already rendered from streaming.
    const msgId = 'assistant-2b';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Stream accumulated text (turn 1 + turn 2 in one text block)
    // Use text > 20 chars so the endsWith dedup fires
    messages = appendTextDelta(messages, msgId, 'Here is the initial summary of the article. ');
    messages = appendTextDelta(messages, msgId, 'And here is the detailed analysis with more context and reasoning.');

    // Now AssistantMessage arrives with just turn 2's text
    // (the SDK's per-turn content, >= 20 chars)
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: 'And here is the detailed analysis with more context and reasoning.' } as ContentBlock,
    ]);

    // Should NOT add a duplicate — turn 2 text is a suffix of the accumulated block
    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    expect(textBlocks).toHaveLength(1);
    expect(textBlocks[0].text).toBe('Here is the initial summary of the article. And here is the detailed analysis with more context and reasoning.');
  });

  it('still adds genuinely new text blocks (no false dedup)', () => {
    const msgId = 'assistant-3';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Stream some text
    messages = appendTextDelta(messages, msgId, 'Original text');

    // AssistantMessage with DIFFERENT text that is NOT a suffix
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: 'Completely different text that was never streamed' } as ContentBlock,
    ]);

    // This should add the new block (it's genuinely new, not a streaming duplicate)
    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    // Two text blocks: one from streaming, one genuinely new
    expect(textBlocks).toHaveLength(2);
  });

  it('does NOT false-dedup short text that happens to be a suffix (length < 20)', () => {
    const msgId = 'assistant-3b';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Existing text ends with "Security"
    messages = appendTextDelta(messages, msgId, 'Topics: Error handling, Performance, Security');

    // New turn legitimately starts with "Security" — short, should NOT be deduped
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: 'Security' } as ContentBlock,
    ]);

    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    // "Security" is < 20 chars, so the endsWith check is skipped — kept as new block
    expect(textBlocks).toHaveLength(2);
  });

  it('does NOT false-dedup text of equal length (exact match handled by blockKey)', () => {
    const msgId = 'assistant-3c';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    const text = 'This is exactly the same text that appears in both places for testing';
    messages = appendTextDelta(messages, msgId, text);

    // Same text — should be deduped by blockKey exact match, NOT endsWith
    messages = updateMessages(messages, msgId, [
      { type: 'text', text } as ContentBlock,
    ]);

    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    expect(textBlocks).toHaveLength(1);
  });

  it('handles single-turn responses correctly (no regression)', () => {
    const msgId = 'assistant-4';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Stream text
    messages = appendTextDelta(messages, msgId, 'Simple response');

    // AssistantMessage with same text — should dedup
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: 'Simple response' } as ContentBlock,
    ]);

    const msg = messages.find(m => m.id === msgId)!;
    const textBlocks = msg.content.filter(b => b.type === 'text');
    expect(textBlocks).toHaveLength(1);
    expect(textBlocks[0].text).toBe('Simple response');
  });

  it('tool_use and tool_result dedup still works with exact ID match', () => {
    const msgId = 'assistant-5';
    let messages: Message[] = [makeAssistantMessage(msgId, [
      { type: 'tool_use', id: 'tu-1', name: 'Bash', summary: 'run', category: 'shell' } as ContentBlock,
    ])];

    // Same tool_use ID should be deduped
    messages = updateMessages(messages, msgId, [
      { type: 'tool_use', id: 'tu-1', name: 'Bash', summary: 'run', category: 'shell' } as ContentBlock,
      { type: 'tool_use', id: 'tu-2', name: 'Read', summary: 'read', category: 'file' } as ContentBlock,
    ]);

    const msg = messages.find(m => m.id === msgId)!;
    expect(msg.content.filter(b => b.type === 'tool_use')).toHaveLength(2); // tu-1 + tu-2
  });
});
