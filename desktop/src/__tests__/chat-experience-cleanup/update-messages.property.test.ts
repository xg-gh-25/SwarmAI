/**
 * Property-based test: updateMessages structural reconciliation invariants.
 *
 * What is being tested:
 * - ``updateMessages`` from ``useChatStreamingLifecycle`` — the structural
 *   reconciliation (replace, not dedup) correctly partitions confirmed vs
 *   unconfirmed blocks, deduplicates tools by ID, and marks new text/thinking
 *   as confirmed.
 *
 * Testing methodology: Property-based testing with fast-check + Vitest
 *
 * Key invariants:
 * 1. Tool blocks are never duplicated (dedup by ID).
 * 2. Unconfirmed text/thinking blocks are replaced by authoritative content.
 * 3. Confirmed text/thinking blocks survive across assistant events.
 * 4. Non-matching message IDs are never modified.
 * 5. Output text/thinking blocks from newContent are always marked _confirmed.
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { updateMessages } from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';

// ---------------------------------------------------------------------------
// fast-check Arbitraries
// ---------------------------------------------------------------------------

const arbTextContent = fc.record({
  type: fc.constant('text' as const),
  text: fc.string({ minLength: 1, maxLength: 100 }),
});

const arbConfirmedTextContent = fc.record({
  type: fc.constant('text' as const),
  text: fc.string({ minLength: 1, maxLength: 100 }),
  _confirmed: fc.constant(true),
});

const arbToolUseContent = fc.record({
  type: fc.constant('tool_use' as const),
  id: fc.uuid(),
  name: fc.string({ minLength: 1, maxLength: 50 }),
  summary: fc.constant('summary'),
  category: fc.constant('file'),
});

const arbToolResultContent = fc.record({
  type: fc.constant('tool_result' as const),
  toolUseId: fc.uuid(),
  content: fc.option(fc.string({ maxLength: 200 }), { nil: undefined }),
  is_error: fc.boolean(),
  truncated: fc.constant(false),
});

const arbContentBlock: fc.Arbitrary<ContentBlock> = fc.oneof(
  arbTextContent,
  arbToolUseContent,
  arbToolResultContent,
) as fc.Arbitrary<ContentBlock>;

const arbNewContentBlock: fc.Arbitrary<ContentBlock> = fc.oneof(
  arbTextContent,
  arbToolUseContent,
  arbToolResultContent,
) as fc.Arbitrary<ContentBlock>;

function arbMessage(role: 'user' | 'assistant'): fc.Arbitrary<Message> {
  return fc.record({
    id: fc.uuid(),
    role: fc.constant(role),
    content: fc.array(arbContentBlock, { minLength: 0, maxLength: 8 }),
    timestamp: fc.constant(new Date().toISOString()),
    model: fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined }),
  }) as fc.Arbitrary<Message>;
}

const arbMessagesWithTarget = fc
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
// Property Tests
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 1: updateMessages Structural Invariants', () => {

  it('non-matching messages are never modified (referential equality)', () => {
    fc.assert(
      fc.property(
        arbMessagesWithTarget,
        fc.array(arbNewContentBlock, { minLength: 1, maxLength: 5 }),
        ({ messages, targetId }, newContent) => {
          const result = updateMessages(messages, targetId, newContent);
          for (let i = 0; i < result.length; i++) {
            if (result[i].id !== targetId) {
              expect(result[i]).toBe(messages[i]); // Same reference
            }
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('tool_use blocks are deduped by id — no duplicates in output', () => {
    fc.assert(
      fc.property(
        arbMessagesWithTarget,
        fc.array(arbToolUseContent as fc.Arbitrary<ContentBlock>, { minLength: 1, maxLength: 5 }),
        ({ messages, targetId }, newContent) => {
          const result = updateMessages(messages, targetId, newContent);
          const target = result.find(m => m.id === targetId)!;
          const toolUseIds = target.content
            .filter(b => b.type === 'tool_use')
            .map(b => (b as Record<string, unknown>).id);
          // No duplicate IDs
          expect(new Set(toolUseIds).size).toBe(toolUseIds.length);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('tool_result blocks are deduped by toolUseId — no duplicates in output', () => {
    fc.assert(
      fc.property(
        arbMessagesWithTarget,
        fc.array(arbToolResultContent as fc.Arbitrary<ContentBlock>, { minLength: 1, maxLength: 5 }),
        ({ messages, targetId }, newContent) => {
          const result = updateMessages(messages, targetId, newContent);
          const target = result.find(m => m.id === targetId)!;
          const toolResultIds = target.content
            .filter(b => b.type === 'tool_result')
            .map(b => (b as Record<string, unknown>).toolUseId);
          // No duplicate toolUseIds
          expect(new Set(toolResultIds).size).toBe(toolResultIds.length);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('text/thinking blocks from newContent are always marked _confirmed', () => {
    fc.assert(
      fc.property(
        arbMessagesWithTarget,
        fc.array(arbTextContent as fc.Arbitrary<ContentBlock>, { minLength: 1, maxLength: 5 }),
        ({ messages, targetId }, newContent) => {
          const result = updateMessages(messages, targetId, newContent);
          const target = result.find(m => m.id === targetId)!;
          // All text blocks from newContent should be confirmed
          const textBlocks = target.content.filter(b => b.type === 'text');
          for (const block of textBlocks) {
            // Every text block in output should be confirmed (either from
            // prior turns or freshly confirmed by this assistant event)
            expect((block as Record<string, unknown>)._confirmed).toBe(true);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('confirmed text blocks survive unless replaced by same-turn re-emission', () => {
    fc.assert(
      fc.property(
        fc.uuid(),
        arbConfirmedTextContent,
        arbTextContent,
        (msgId, confirmedBlock, newBlock) => {
          const messages: Message[] = [{
            id: msgId,
            role: 'assistant',
            content: [confirmedBlock as ContentBlock],
            timestamp: new Date().toISOString(),
          }];

          const result = updateMessages(messages, msgId, [newBlock as ContentBlock]);
          const target = result.find(m => m.id === msgId)!;
          const textBlocks = target.content.filter(b => b.type === 'text');

          const newText = (newBlock as Record<string, unknown>).text as string ?? '';
          const oldText = (confirmedBlock as Record<string, unknown>).text as string ?? '';
          // Must match the actual MIN_DEDUP_LENGTH guard in updateMessages:
          // Short text (< 20 chars): exact match only (prevents false positives)
          // Long text (>= 20 chars): exact match OR startsWith (handles SDK growth)
          const MIN_DEDUP_LENGTH = 20;
          const isSameTurnReEmission = oldText.length >= MIN_DEDUP_LENGTH
            ? (newText === oldText || newText.startsWith(oldText))
            : (newText === oldText);

          if (isSameTurnReEmission) {
            // Same-turn dedup: old text replaced by new (BUG FIX 2026-06-07)
            expect(textBlocks).toHaveLength(1);
            expect(textBlocks[0].text).toBe(newText);
          } else {
            // Different turn: both survive
            expect(textBlocks.some(b => b.text === oldText)).toBe(true);
            expect(textBlocks.some(b => b.text === newText)).toBe(true);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it('absent target ID returns messages unchanged', () => {
    fc.assert(
      fc.property(
        fc.array(arbMessage('user'), { minLength: 1, maxLength: 5 }),
        fc.array(arbNewContentBlock, { minLength: 1, maxLength: 5 }),
        (messages, newContent) => {
          const missingId = 'non-existent-id';
          const result = updateMessages(messages, missingId, newContent);
          // Same references — nothing touched
          expect(result).toEqual(messages);
          for (let i = 0; i < result.length; i++) {
            expect(result[i]).toBe(messages[i]);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
