/**
 * Bug Condition Exploration Test — Chat Duplicate Response (UNFIXED merge logic).
 *
 * What is tested:
 *   Reproduces the duplicate-render defect described in
 *   `.kiro/specs/chat-duplicate-response-fix/bugfix.md` (C(X)) against a
 *   FAITHFUL in-test reconstruction of the PRE-FIX `updateMessages` merge:
 *   an `endsWith`-based substring dedup guard with a `length >= 50` threshold
 *   that APPENDS the authoritative `assistant` text as a NEW block whenever the
 *   suffix match fails. The real (fixed) `updateMessages` is intentionally NOT
 *   used here — the old logic is reconstructed locally so the bug can be
 *   observed and documented.
 *
 * Methodology:
 *   Deterministic, scoped table-driven cases. Each case feeds a
 *   streamed-then-authoritative sequence satisfying `isBugCondition` into the
 *   reconstructed OLD merge and asserts the duplication is observable (2 text
 *   blocks / doubled clipboard output). These assertions PASS by asserting the
 *   bug reproduces — that is the goal of a bugfix exploration test.
 *
 * Counterexamples documented (prove the bug existed):
 *   1. Trailing-newline mismatch — streamed "…\n", authoritative "…" (no "\n").
 *      `existingText.endsWith(incoming)` fails on the trailing "\n", so the
 *      authoritative text is appended as a 2nd block → response rendered twice.
 *   2. Prefix/interior overlap — streamed and authoritative share a long common
 *      prefix but diverge at the tail, so there is no clean suffix relationship;
 *      `endsWith` fails → 2nd block appended → duplicate.
 *   Both also propagate to clipboard: `extractMessageText`-style join over two
 *   text blocks returns the response text twice.
 *
 * Key invariant under OLD logic (the defect): the substring-containment guard
 * only matches a CLEAN suffix relationship, so any whitespace/boundary or
 * prefix/interior divergence between streamed and authoritative text yields a
 * duplicate text block.
 */

import { describe, it, expect } from 'vitest';
import type { ContentBlock } from '../../types';

// ---------------------------------------------------------------------------
// Reconstructed PRE-FIX merge (the OLD, buggy `updateMessages` text branch).
//
// Mirrors the pre-fix heuristic: when an `assistant` event delivers a text
// block, the old code checked whether the last accumulated (streamed) text
// block already "contained" the incoming text via `endsWith` substring match,
// gated by a `length >= 50` threshold. If the suffix match succeeded it left
// the existing block alone; otherwise it APPENDED the incoming text as a NEW
// block. We reconstruct ONLY that text-merge branch — enough to reproduce the
// duplication. We do NOT touch the real `updateMessages`.
// ---------------------------------------------------------------------------

const OLD_DEDUP_MIN_LENGTH = 50; // raised 20 -> 50 in commit ec3bf3f0

/** Pre-fix suffix-containment dedup guard. */
function oldTextAlreadyRendered(existingText: string, incomingText: string): boolean {
  // Only treat as already-rendered when there is a CLEAN suffix relationship
  // above the length threshold. (This is the fragile heuristic that misses
  // whitespace/boundary and prefix/interior mismatches.)
  if (incomingText.length < OLD_DEDUP_MIN_LENGTH) return false;
  return existingText.endsWith(incomingText) || incomingText.endsWith(existingText);
}

/**
 * Reconstructed OLD merge: reconcile one `assistant` event's content into the
 * existing blocks using the pre-fix substring-containment heuristic for text.
 * tool_use / tool_result are appended (no id dedup needed for these cases).
 */
function oldUpdateMessages(
  existing: ContentBlock[],
  newContent: ContentBlock[],
): ContentBlock[] {
  const result = [...existing];

  for (const incoming of newContent) {
    if (incoming.type === 'text') {
      const incomingText = incoming.text ?? '';
      // Find the last existing text block (the streamed/accumulated one).
      let lastTextIdx = -1;
      for (let i = result.length - 1; i >= 0; i--) {
        if (result[i].type === 'text') {
          lastTextIdx = i;
          break;
        }
      }
      if (lastTextIdx >= 0) {
        const existingText = (result[lastTextIdx] as { text?: string }).text ?? '';
        if (oldTextAlreadyRendered(existingText, incomingText)) {
          // Considered already rendered — leave as-is (correct case).
          continue;
        }
      }
      // Suffix guard failed (or no prior text) — append as a NEW block.
      // THIS is the defect: streamed text + authoritative text both kept.
      result.push(incoming);
    } else {
      result.push(incoming);
    }
  }

  return result;
}

/** Mirror of AssistantMessageView.extractMessageText: join all text blocks. */
function extractMessageText(content: ContentBlock[]): string {
  return content
    .filter((b): b is ContentBlock & { type: 'text'; text: string } => b.type === 'text')
    .map((b) => b.text)
    .join('\n');
}

describe('Bug condition exploration — duplicate text on UNFIXED merge logic', () => {

  // Case 1 — Trailing-newline mismatch (primary trigger).
  // streamed "…\n", authoritative "…": endsWith fails on the "\n".
  it('counterexample 1: trailing-newline mismatch yields 2 text blocks', () => {
    const authoritative = 'Now I have the full picture. Let me synthesize the findings into a design proposal.';
    const streamed = authoritative + '\n'; // streaming artifact: trailing newline

    // Streamed text already accumulated into one provisional text block.
    const existing: ContentBlock[] = [{ type: 'text', text: streamed } as ContentBlock];

    // Authoritative assistant event (no trailing newline).
    const result = oldUpdateMessages(existing, [
      { type: 'text', text: authoritative } as ContentBlock,
    ]);

    const textBlocks = result.filter((b) => b.type === 'text');
    // BUG: suffix guard fails on the "\n", authoritative appended as 2nd block.
    expect(textBlocks).toHaveLength(2);
  });

  // Case 2 — Prefix/interior overlap (shared prefix, divergent tail → no clean suffix).
  it('counterexample 2: prefix/interior overlap yields 2 text blocks', () => {
    const sharedPrefix = 'Now I have the full picture. Let me synthesize the findings into a design proposal. ';
    const streamed = sharedPrefix + 'Streaming tail token that diverges here.';
    const authoritative = sharedPrefix + 'Authoritative final wording differs at the end.';

    const existing: ContentBlock[] = [{ type: 'text', text: streamed } as ContentBlock];

    const result = oldUpdateMessages(existing, [
      { type: 'text', text: authoritative } as ContentBlock,
    ]);

    const textBlocks = result.filter((b) => b.type === 'text');
    // BUG: shared prefix but no clean suffix → endsWith fails → 2nd block appended.
    expect(textBlocks).toHaveLength(2);
  });

  // Case 3 — Long multi-turn text (~1450 chars) streamed then re-delivered.
  it('counterexample 3: long multi-turn text re-delivered yields doubled render', () => {
    const longText = 'Now I have the full picture. '.repeat(50); // ~1450 chars
    const streamed = longText + '\n'; // trailing newline from streaming

    const existing: ContentBlock[] = [{ type: 'text', text: streamed } as ContentBlock];

    const result = oldUpdateMessages(existing, [
      { type: 'text', text: longText } as ContentBlock,
    ]);

    const textBlocks = result.filter((b) => b.type === 'text');
    // BUG: long response stored twice (doubled render).
    expect(textBlocks).toHaveLength(2);
    // Both copies of the leading sentence are present in the rendered blocks.
    expect(textBlocks[0].text).toContain('Now I have the full picture.');
    expect(textBlocks[1].text).toContain('Now I have the full picture.');
  });

  // Case 4 — Clipboard propagation via extractMessageText (filter→map→join).
  it('counterexample 4: extractMessageText returns the response text twice', () => {
    const authoritative = 'Now I have the full picture. Let me synthesize the findings into a design proposal.';
    const streamed = authoritative + '\n';

    const existing: ContentBlock[] = [{ type: 'text', text: streamed } as ContentBlock];

    const result = oldUpdateMessages(existing, [
      { type: 'text', text: authoritative } as ContentBlock,
    ]);

    const copied = extractMessageText(result);
    // BUG: the joined clipboard text contains the authoritative response twice.
    const occurrences = copied.split('Let me synthesize the findings into a design proposal.').length - 1;
    expect(occurrences).toBe(2);
  });
});
