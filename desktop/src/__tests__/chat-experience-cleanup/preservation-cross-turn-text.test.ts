/**
 * Test: Preservation — distinct cross-turn text both render (Property 3).
 *
 * What is being tested:
 *   When two SEPARATE assistant turns each confirm a text block, BOTH confirmed
 *   blocks must survive reconciliation in their original order. This includes
 *   the false-dedup trap: two turns producing the EXACT SAME phrasing
 *   (e.g. "Proceeding with the migration.") must remain 2 distinct confirmed
 *   blocks, NOT be collapsed into 1. Collapsing legitimate repeated phrasing
 *   across turns would silently drop a real response the user sent.
 *
 * Testing methodology:
 *   - Deterministic unit tests driving the real streaming flow
 *     (appendTextDelta accumulation -> authoritative updateMessages event)
 *     against the REAL (fixed) updateMessages import.
 *   - A fast-check property generating two arbitrary non-empty texts (including
 *     equal ones) across two turns, asserting exactly 2 confirmed text blocks
 *     survive in order.
 *
 * Key invariant (design Property 3, Requirements 3.1 / 3.5):
 *   Two turns that each confirm a text block keep BOTH confirmed blocks in
 *   original order; identical phrasing across turns is preserved as 2 blocks.
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

// Helper: run one full turn — stream text, deliver authoritative assistant event
// with text + tool_use (simulating a real agentic turn that always ends with a tool).
// The tool_use creates a turn boundary so the next turn's text isn't deduped.
let _turnToolCounter = 0;
function runTurn(messages: Message[], msgId: string, text: string): Message[] {
  _turnToolCounter++;
  let next = appendTextDelta(messages, msgId, text);
  next = updateMessages(next, msgId, [
    { type: 'text', text } as ContentBlock,
    { type: 'tool_use', id: `tu-prop3-${_turnToolCounter}`, name: 'Read', summary: 'read', category: 'file' } as ContentBlock,
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
    messages = runTurn(messages, msgId, 'Turn 1 text');
    // Turn 2
    messages = runTurn(messages, msgId, 'Turn 2 text');

    const msg = messages.find((m) => m.id === msgId)!;
    const texts = confirmedTexts(msg);
    expect(texts).toHaveLength(2);
    expect(texts[0].text).toBe('Turn 1 text');
    expect(texts[1].text).toBe('Turn 2 text');
    // Both marked confirmed.
    expect((texts[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect((texts[1] as Record<string, unknown>)._confirmed).toBe(true);
  });

  it('two turns with IDENTICAL phrasing stay as 2 separate confirmed blocks', () => {
    const msgId = 'p3-identical';
    let messages: Message[] = [makeAssistantMessage(msgId)];

    const phrase = 'Proceeding with the migration.';

    // Turn 1: user asks, agent says the phrase.
    messages = runTurn(messages, msgId, phrase);
    // Turn 2: a later, legitimately separate turn produces the exact same phrase.
    messages = runTurn(messages, msgId, phrase);

    const msg = messages.find((m) => m.id === msgId)!;
    const texts = confirmedTexts(msg);
    // Regression guard: identical cross-turn phrasing must NOT be deduped to 1.
    expect(texts).toHaveLength(2);
    expect(texts[0].text).toBe(phrase);
    expect(texts[1].text).toBe(phrase);
  });

  it('property: two arbitrary non-empty texts across two turns survive as 2 blocks in order', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 200 }),
        fc.string({ minLength: 1, maxLength: 200 }),
        (text1, text2) => {
          const msgId = 'p3-prop';
          let messages: Message[] = [makeAssistantMessage(msgId)];

          messages = runTurn(messages, msgId, text1);
          messages = runTurn(messages, msgId, text2);

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
