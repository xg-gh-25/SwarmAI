/**
 * Property-based test: updateMessages behavioral equivalence.
 *
 * What is being tested:
 * - ``updateMessages`` from ``useChatStreamingLifecycle`` — the optimized Set-based
 *   implementation produces identical output to the original O(n×m) nested-iteration
 *   approach for all valid inputs.
 *
 * Testing methodology: Property-based testing with fast-check + Vitest
 * Key property: For any valid message array, assistant message ID, array of new
 * content blocks (text, tool_use, tool_result), and optional model string, the
 * optimized implementation SHALL produce output identical to the original.
 *
 * **Validates: Requirements 2.5**
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { updateMessages, blockKey } from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';

// ---------------------------------------------------------------------------
// Original (reference) implementation — O(n×m) nested .some() approach
// ---------------------------------------------------------------------------

/**
 * Reference implementation of updateMessages using the original nested-iteration
 * algorithm. This is the behavioral baseline that the optimized Set-based
 * implementation must match exactly.
 */
function originalUpdateMessages(
  currentMessages: Message[],
  assistantMessageId: string,
  newContent: ContentBlock[],
  model?: string,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;

    // Original O(n×m) nested .some() dedup logic
    const filteredContent = newContent.filter((newBlock) => {
      return !msg.content.some((existing) => {
        if (existing.type === 'tool_use' && newBlock.type === 'tool_use') {
          return existing.id === newBlock.id;
        }
        if (existing.type === 'tool_result' && newBlock.type === 'tool_result') {
          return existing.toolUseId === newBlock.toolUseId;
        }
        if (existing.type === 'text' && newBlock.type === 'text') {
          return existing.text === newBlock.text;
        }
        return false;
      });
    });

    if (filteredContent.length === 0) return msg;
    return {
      ...msg,
      content: [...msg.content, ...filteredContent],
      ...(model ? { model } : {}),
      // Clear isError when new non-error content arrives (auto-retry recovery)
      ...(msg.isError ? { isError: false } : {}),
    };
  });
}

// ---------------------------------------------------------------------------
// fast-check Arbitraries
// ---------------------------------------------------------------------------

/** Arbitrary for a text content block. */
const arbTextContent = fc.record({
  type: fc.constant('text' as const),
  text: fc.string({ minLength: 1, maxLength: 100 }),
});

/** Arbitrary for a tool_use content block. */
const arbToolUseContent = fc.record({
  type: fc.constant('tool_use' as const),
  id: fc.uuid(),
  name: fc.string({ minLength: 1, maxLength: 50 }),
  input: fc.constant({} as Record<string, unknown>),
});

/** Arbitrary for a tool_result content block. */
const arbToolResultContent = fc.record({
  type: fc.constant('tool_result' as const),
  toolUseId: fc.uuid(),
  content: fc.option(fc.string({ maxLength: 200 }), { nil: undefined }),
  isError: fc.boolean(),
});

/** Arbitrary for any ContentBlock (text, tool_use, or tool_result). */
const arbContentBlock: fc.Arbitrary<ContentBlock> = fc.oneof(
  arbTextContent,
  arbToolUseContent,
  arbToolResultContent,
);

/** Arbitrary for a Message with a given role and content blocks. */
function arbMessage(role: 'user' | 'assistant'): fc.Arbitrary<Message> {
  return fc.record({
    id: fc.uuid(),
    role: fc.constant(role),
    content: fc.array(arbContentBlock, { minLength: 0, maxLength: 8 }),
    timestamp: fc.constant(new Date().toISOString()),
    model: fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined }),
  });
}

/**
 * Arbitrary for a message array that contains at least one assistant message.
 * Returns both the array and the ID of a randomly chosen assistant message
 * to use as the target for updateMessages.
 */
const arbMessagesWithTarget: fc.Arbitrary<{
  messages: Message[];
  targetId: string;
}> = fc
  .tuple(
    fc.array(arbMessage('user'), { minLength: 0, maxLength: 3 }),
    arbMessage('assistant'),
    fc.array(
      fc.oneof(arbMessage('user'), arbMessage('assistant')),
      { minLength: 0, maxLength: 3 },
    ),
  )
  .map(([before, target, after]) => ({
    messages: [...before, target, ...after],
    targetId: target.id,
  }));

// ---------------------------------------------------------------------------
// Property Test
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 1: updateMessages Behavioral Equivalence', () => {
  /**
   * **Validates: Requirements 2.5**
   *
   * For any valid message array, any assistant message ID present in that
   * array, any array of new content blocks (containing arbitrary mixes of
   * text, tool_use, and tool_result blocks), and any optional model string,
   * the optimized Set-based updateMessages implementation SHALL produce
   * output identical to the original nested-iteration implementation.
   */
  it('optimized Set-based implementation matches original nested-iteration for all inputs', () => {
    fc.assert(
      fc.property(
        arbMessagesWithTarget,
        fc.array(arbContentBlock, { minLength: 0, maxLength: 10 }),
        fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined }),
        ({ messages, targetId }, newContent, model) => {
          const optimized = updateMessages(messages, targetId, newContent, model);
          const reference = originalUpdateMessages(messages, targetId, newContent, model);

          expect(optimized).toEqual(reference);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 2.5**
   *
   * When the target assistant message ID does not exist in the array,
   * both implementations should return messages unchanged.
   */
  it('both implementations return unchanged messages when target ID is absent', () => {
    fc.assert(
      fc.property(
        fc.array(arbMessage('user'), { minLength: 1, maxLength: 5 }),
        fc.array(arbContentBlock, { minLength: 1, maxLength: 5 }),
        (messages, newContent) => {
          const missingId = 'non-existent-id';
          const optimized = updateMessages(messages, missingId, newContent);
          const reference = originalUpdateMessages(messages, missingId, newContent);

          expect(optimized).toEqual(reference);
        },
      ),
      { numRuns: 100 },
    );
  });
});
