/**
 * Fix-Checking Test — FIXED updateMessages stores each turn's text exactly once.
 *
 * What is tested (Property 1 — Bug Condition / Expected Behavior):
 *   For every input where the bug condition holds (streamed text reconciled
 *   AGAIN by the authoritative `assistant` event), the REAL (fixed)
 *   `updateMessages` from `../../hooks/useChatStreamingLifecycle` stores the
 *   turn's text such that `countLogicalOccurrences(message, turnText) == 1`.
 *   The provisional streamed text/thinking is dropped and the authoritative
 *   block is appended once, marked `_confirmed`. Neither the rendered blocks
 *   nor `extractMessageText()` contain the response more than once.
 *
 * Methodology:
 *   Deterministic table-driven cases plus a fast-check property over the
 *   trailing-newline / non-suffix mismatch family. Each case drives the real
 *   streaming path: accumulate text via `appendTextDelta`, then reconcile with
 *   `updateMessages` using the authoritative assistant event.
 *
 * Cases covered (the NEW ones not already in multi-turn-text-dedup.test.ts;
 * a2 whitespace / a8 identical-cross-turn / P0 long-text are not duplicated):
 *   1. Trailing-newline mismatch — streamed "<text>\n", authoritative "<text>"
 *      → exactly 1 text block == authoritative (req 2.2).
 *   2. Prefix/interior overlap — streamed shares prefix but diverges at the
 *      tail → exactly 1 text block == authoritative (req 2.2).
 *   3. SDK-session-remap-mid-turn — `updateMessages` is keyed by
 *      assistantMessageId (NOT SDK session id, stable per Isolation Principle 8);
 *      a single assistant event for that id renders text exactly once regardless
 *      of any SDK session re-map (req 2.4).
 *   4. Clipboard non-duplication — `extractMessageText`-style join returns the
 *      response exactly once (req 2.3).
 *
 * Key invariant: the surviving text block equals the authoritative text and
 * carries `_confirmed: true`; `countLogicalOccurrences == 1` for every
 * bug-condition input. EXPECTED OUTCOME: all assertions PASS on fixed code.
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  updateMessages,
  appendTextDelta,
} from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Create a base assistant message with optional starting content. */
function makeAssistantMessage(id: string, content: ContentBlock[] = []): Message {
  return {
    id,
    role: 'assistant',
    content,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Count logical occurrences of `turnText` across a message's text blocks.
 * A block counts once if its text === turnText, or once for each time turnText
 * appears as a substring (to catch a single block that concatenated the
 * response twice). Sums across all text blocks so 2 separate blocks each equal
 * to turnText returns 2.
 */
function countLogicalOccurrences(message: Message, turnText: string): number {
  let count = 0;
  for (const block of message.content) {
    if (block.type !== 'text') continue;
    const text = block.text ?? '';
    if (text === turnText) {
      count += 1;
    } else if (turnText.length > 0) {
      // Count non-overlapping substring occurrences within this block.
      count += text.split(turnText).length - 1;
    }
  }
  return count;
}

/** Mirror of AssistantMessageView.extractMessageText: filter→map→join('\n'). */
function extractMessageText(message: Message): string {
  return message.content
    .filter((b): b is ContentBlock & { type: 'text'; text: string } => b.type === 'text')
    .map((b) => b.text)
    .join('\n');
}

/** Run the real streaming path: stream `streamed`, then reconcile `authoritative`. */
function streamThenReconcile(
  msgId: string,
  streamed: string,
  authoritative: string,
): Message {
  let messages: Message[] = [makeAssistantMessage(msgId)];
  messages = appendTextDelta(messages, msgId, streamed);
  messages = updateMessages(messages, msgId, [
    { type: 'text', text: authoritative } as ContentBlock,
  ]);
  return messages.find((m) => m.id === msgId)!;
}

/** True if the streamed/authoritative pair satisfies the bug condition. */
function isBugCondition(streamed: string, authoritative: string): boolean {
  // The turn's authoritative text was already rendered (provisionally) via
  // streaming, but streamed !== authoritative (so the old suffix heuristic
  // could miss it). Both non-trivial.
  return (
    authoritative.length > 0 &&
    streamed !== authoritative &&
    streamed.includes(authoritative.slice(0, Math.min(20, authoritative.length)))
  );
}

describe('Fix-checking — fixed updateMessages stores each turn text exactly once', () => {

  // Case 1 — Trailing-newline mismatch (req 2.2).
  it('case 1: trailing-newline mismatch → exactly 1 confirmed text block', () => {
    const authoritative =
      'Now I have the full picture. Let me synthesize the findings into a design proposal.';
    const streamed = authoritative + '\n'; // streaming artifact

    expect(isBugCondition(streamed, authoritative)).toBe(true);

    const msg = streamThenReconcile('fc1', streamed, authoritative);
    const textBlocks = msg.content.filter((b) => b.type === 'text');

    expect(textBlocks).toHaveLength(1);
    expect(textBlocks[0].text).toBe(authoritative);
    expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect(countLogicalOccurrences(msg, authoritative)).toBe(1);
  });

  // Case 2 — Prefix/interior overlap (req 2.2).
  it('case 2: prefix/interior overlap → exactly 1 confirmed text block', () => {
    const sharedPrefix =
      'Now I have the full picture. Let me synthesize the findings into a design proposal. ';
    const streamed = sharedPrefix + 'Streaming tail token that diverges here.';
    const authoritative = sharedPrefix + 'Authoritative final wording differs at the end.';

    expect(isBugCondition(streamed, authoritative)).toBe(true);

    const msg = streamThenReconcile('fc2', streamed, authoritative);
    const textBlocks = msg.content.filter((b) => b.type === 'text');

    // Streamed provisional text dropped; only the authoritative block remains.
    expect(textBlocks).toHaveLength(1);
    expect(textBlocks[0].text).toBe(authoritative);
    expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect(countLogicalOccurrences(msg, authoritative)).toBe(1);
    // The diverging streamed tail is gone — not duplicated.
    expect(countLogicalOccurrences(msg, streamed)).toBe(0);
  });

  // Case 3 — SDK-session-remap-mid-turn (req 2.4).
  // updateMessages is keyed by assistantMessageId, not SDK session id. Model
  // an SDK re-map as: the same assistantMessageId receives a single
  // authoritative event after streaming → text rendered exactly once.
  it('case 3: single authoritative event for one message id renders text once regardless of SDK session id', () => {
    const msgId = 'fc3-stable-app-session';
    const authoritative =
      'Synthesis complete after the SDK session was re-mapped to a new id mid-turn.';
    const streamed = authoritative + '\n';

    expect(isBugCondition(streamed, authoritative)).toBe(true);

    // Stream provisional text under the stable app message id.
    let messages: Message[] = [makeAssistantMessage(msgId)];
    messages = appendTextDelta(messages, msgId, streamed);

    // SDK session re-map mid-turn: the authoritative assistant event for this
    // turn is still processed once, keyed by the stable assistantMessageId.
    messages = updateMessages(messages, msgId, [
      { type: 'text', text: authoritative } as ContentBlock,
    ]);

    const msg = messages.find((m) => m.id === msgId)!;
    const textBlocks = msg.content.filter((b) => b.type === 'text');

    expect(textBlocks).toHaveLength(1);
    expect(textBlocks[0].text).toBe(authoritative);
    expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect(countLogicalOccurrences(msg, authoritative)).toBe(1);
  });

  // Case 4 — Clipboard non-duplication (req 2.3).
  it('case 4: extractMessageText returns the response exactly once', () => {
    const authoritative =
      'Now I have the full picture. Let me synthesize the findings into a design proposal.';
    const streamed = authoritative + '\n';

    const msg = streamThenReconcile('fc4', streamed, authoritative);
    const copied = extractMessageText(msg);

    const occurrences =
      copied.split('Let me synthesize the findings into a design proposal.').length - 1;
    expect(occurrences).toBe(1);
    expect(copied).toBe(authoritative);
  });
});

describe('Fix-checking property — every bug-condition input stores turn text once', () => {

  // Generator: an authoritative turn text (>= 20 chars so it is a realistic
  // turn, above the dedup floor) plus a streamed variant that satisfies the
  // bug condition via a trailing-whitespace or non-suffix-divergent tail.
  const bugConditionInputs = fc
    .record({
      base: fc.string({ minLength: 20, maxLength: 400 }),
      // Mismatch flavor: trailing whitespace, or a divergent appended tail.
      flavor: fc.constantFrom('trailingNewline', 'trailingSpaces', 'divergentTail'),
      tail: fc.string({ minLength: 1, maxLength: 40 }),
    })
    .map(({ base, flavor, tail }) => {
      const authoritative = base;
      let streamed: string;
      if (flavor === 'trailingNewline') {
        streamed = authoritative + '\n';
      } else if (flavor === 'trailingSpaces') {
        streamed = authoritative + '   ';
      } else {
        // Shared prefix, divergent tail — no clean suffix relationship.
        streamed = authoritative + tail;
      }
      return { streamed, authoritative };
    })
    .filter(({ streamed, authoritative }) => isBugCondition(streamed, authoritative));

  it('Property 1: countLogicalOccurrences(turnText) == 1 and block marked _confirmed', () => {
    fc.assert(
      fc.property(bugConditionInputs, ({ streamed, authoritative }) => {
        const msg = streamThenReconcile('prop-fc', streamed, authoritative);
        const textBlocks = msg.content.filter((b) => b.type === 'text');

        // Exactly one text block, equal to authoritative, marked _confirmed.
        expect(textBlocks).toHaveLength(1);
        expect(textBlocks[0].text).toBe(authoritative);
        expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);

        // The turn's text is stored exactly once.
        expect(countLogicalOccurrences(msg, authoritative)).toBe(1);

        // Clipboard join contains the authoritative response exactly once.
        expect(extractMessageText(msg)).toBe(authoritative);
      }),
      { numRuns: 300 },
    );
  });
});
