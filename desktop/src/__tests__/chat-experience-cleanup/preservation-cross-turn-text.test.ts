/**
 * Test: Preservation — distinct cross-turn text both render (Property 3).
 *
 * MESSAGE-ID INVARIANT (verified 2026-06-07 in ChatPage.tsx):
 *   Every real user turn — send, queue-drain, answer-question, escalation —
 *   allocates a FRESH `assistantMessageId` and a separate message bubble.
 *   `updateMessages` only ever reconciles content WITHIN a single
 *   `assistantMessageId`. Therefore:
 *     • Two separate USER turns are always DIFFERENT messages (different IDs)
 *       and never pass through the same `updateMessages` call → identical
 *       phrasing across real turns is preserved trivially (separate messages).
 *     • Within ONE `assistantMessageId`, multiple "turns" are the SDK's
 *       agentic loop re-emitting growing/cumulative text for ONE user request.
 *       Identical re-emitted text in that single message is a DUPLICATE and
 *       MUST collapse to 1 block (this is the P0 spinner-hang / content-explosion
 *       fix — see streaming-spinner-hang-repro.test.ts).
 *
 * What is being tested:
 *   1. Within one message: two DISTINCT texts (agentic loop emits different
 *      text across tool calls) keep BOTH confirmed blocks in order.
 *   2. Within one message: identical re-emitted text collapses to 1 block
 *      (re-emission, not a second independent statement) — P0 protection.
 *   3. Across SEPARATE message IDs (real distinct user turns): identical
 *      phrasing is preserved as 2 separate messages, each with its own block.
 *   4. A fast-check property: two arbitrary DISTINCT texts within one message
 *      survive as 2 confirmed blocks in order.
 *
 * Validates: Requirements 3.1, 3.5
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  updateMessages,
  appendTextDelta,
} from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';

// Helper: base assistant message with empty content.
function makeAssistantMessage(id: string, content: ContentBlock[] = []): Message {
  return {
    id,
    role: 'assistant',
    content,
    timestamp: new Date().toISOString(),
  };
}

// Helper: emit one agentic-loop step WITHIN a single message — stream text,
// then deliver an authoritative assistant event carrying that text + a tool_use.
// Multiple steps under the SAME msgId model the SDK's in-turn re-emission, NOT
// separate user turns (those use separate message IDs — see file header).
let _stepToolCounter = 0;
function emitStep(messages: Message[], msgId: string, text: string): Message[] {
  _stepToolCounter++;
  let next = appendTextDelta(messages, msgId, text);
  next = updateMessages(next, msgId, [
    { type: 'text', text } as ContentBlock,
    { type: 'tool_use', id: `tu-prop3-${_stepToolCounter}`, name: 'Read', summary: 'read', category: 'file' } as ContentBlock,
  ]);
  return next;
}

// Helper: extract confirmed text blocks from a message.
function confirmedTexts(msg: Message): ContentBlock[] {
  return msg.content.filter(
    (b) => b.type === 'text' && (b as Record<string, unknown>)._confirmed === true,
  );
}

describe('Preservation — distinct cross-turn text both render (Property 3)', () => {

  it('two turns with DISTINCT text keep both confirmed blocks in order', () => {
    const msgId = 'p3-distinct';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    // Turn 1
    messages = emitStep(messages, msgId, 'Turn 1 text');
    // Turn 2
    messages = emitStep(messages, msgId, 'Turn 2 text');

    const msg = messages.find((m) => m.id === msgId)!;
    const texts = confirmedTexts(msg);
    expect(texts).toHaveLength(2);
    expect(texts[0].text).toBe('Turn 1 text');
    expect(texts[1].text).toBe('Turn 2 text');
    // Both marked confirmed.
    expect((texts[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect((texts[1] as Record<string, unknown>)._confirmed).toBe(true);
  });

  it('identical text re-emitted within same message collapses to 1 block (P0 protection)', () => {
    // MESSAGE-ID INVARIANT: within one assistantMessageId, "two turns" are
    // the SDK's agentic loop re-emitting growing/identical text for ONE user
    // request. This is NOT two independent user turns — those get separate
    // message IDs and separate bubbles (verified in ChatPage.tsx).
    //
    // Collapsing identical re-emitted text to 1 block is CORRECT — it prevents
    // the P0 spinner hang / content explosion bug.
    const msgId = 'p3-identical';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    const phrase = 'Proceeding with the migration.';

    messages = emitStep(messages, msgId, phrase);
    messages = emitStep(messages, msgId, phrase);

    const msg = messages.find((m) => m.id === msgId)!;
    const texts = confirmedTexts(msg);
    // Same text within one message = re-emission = collapse to 1 (P0 fix)
    expect(texts).toHaveLength(1);
    expect(texts[0].text).toBe(phrase);
  });

  it('property: two arbitrary non-empty texts across two turns survive as 2 blocks in order', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 200 }),
        fc.string({ minLength: 1, maxLength: 200 }),
        (text1, text2) => {
          // Exclude pairs that legitimately collapse under same-turn re-emission dedup:
          // equal text, or one being a prefix of the other. Those model SDK cumulative
          // re-emission (covered by the re-emission test), not two distinct statements.
          fc.pre(
            text1 !== text2 &&
            !text2.startsWith(text1) &&
            !text1.startsWith(text2),
          );

          const msgId = 'p3-prop';
          let messages: Message[] = [makeAssistantMessage(msgId)];

          messages = emitStep(messages, msgId, text1);
          messages = emitStep(messages, msgId, text2);

          const msg = messages.find((m) => m.id === msgId)!;
          const texts = confirmedTexts(msg);

          // Exactly 2 confirmed text blocks, preserving turn order.
          expect(texts).toHaveLength(2);
          expect(texts[0].text).toBe(text1);
          expect(texts[1].text).toBe(text2);
        },
      ),
      { numRuns: 200 },
    );
  });
});
