/**
 * Property-based tests for session persistence round-trip and version mismatch.
 *
 * What is being tested:
 * - ``persistPendingState`` and ``restorePendingState`` from
 *   ``useChatStreamingLifecycle`` — verifying that persist/restore is a faithful
 *   round-trip (post-truncation) and that version mismatches cause discards.
 *
 * Testing methodology: Property-based testing with fast-check + Vitest
 *
 * Key properties verified:
 * - Property 2: Persist/Restore Round-Trip — persisting then restoring by the
 *   same sessionId produces a deeply equal object (post-truncation payload).
 * - Property 3: Version Mismatch Discards State — if the stored version differs
 *   from the current PERSISTED_STATE_VERSION, restorePendingState returns null
 *   and the sessionStorage entry is removed.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';
import {
  persistPendingState,
  restorePendingState,
  PERSISTED_STATE_VERSION,
  STORAGE_KEY_PREFIX,
  prepareMessagesForStorage,
} from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';
import type { PendingQuestion } from '../../pages/chat/types';

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
  summary: fc.constant('Using tool'),
});

/** Arbitrary for a tool_result content block. */
const arbToolResultContent = fc.record({
  type: fc.constant('tool_result' as const),
  toolUseId: fc.uuid(),
  content: fc.option(fc.string({ maxLength: 300 }), { nil: undefined }),
  isError: fc.boolean(),
  truncated: fc.boolean(),
});

/** Arbitrary for any ContentBlock (text, tool_use, or tool_result). */
const arbContentBlock: fc.Arbitrary<ContentBlock> = fc.oneof(
  arbTextContent,
  arbToolUseContent,
  arbToolResultContent,
);

/** Arbitrary for a Message with a given role. */
function arbMessage(role: 'user' | 'assistant'): fc.Arbitrary<Message> {
  return fc.record({
    id: fc.uuid(),
    role: fc.constant(role),
    content: fc.array(arbContentBlock, { minLength: 1, maxLength: 6 }),
    timestamp: fc.constant(new Date().toISOString()),
    model: fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined }),
  });
}

/** Arbitrary for a non-empty messages array with mixed roles. */
const arbMessages: fc.Arbitrary<Message[]> = fc
  .tuple(
    arbMessage('user'),
    fc.array(
      fc.oneof(arbMessage('user'), arbMessage('assistant')),
      { minLength: 0, maxLength: 5 },
    ),
  )
  .map(([first, rest]) => [first, ...rest]);

/** Arbitrary for a PendingQuestion (must have a string toolUseId). */
const arbPendingQuestion: fc.Arbitrary<PendingQuestion> = fc.record({
  toolUseId: fc.uuid(),
  questions: fc.array(
    fc.record({
      question: fc.string({ minLength: 1, maxLength: 80 }),
      header: fc.string({ minLength: 1, maxLength: 40 }),
      options: fc.array(
        fc.record({
          label: fc.string({ minLength: 1, maxLength: 30 }),
          description: fc.string({ minLength: 0, maxLength: 60 }),
        }),
        { minLength: 0, maxLength: 3 },
      ),
      multiSelect: fc.boolean(),
    }),
    { minLength: 1, maxLength: 3 },
  ),
});

/** Arbitrary for a non-empty sessionId string. */
const arbSessionId = fc.uuid();

// ---------------------------------------------------------------------------
// sessionStorage setup — jsdom provides window.sessionStorage
// ---------------------------------------------------------------------------

beforeEach(() => {
  window.sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// Property 2: Persist/Restore Round-Trip
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 2: Persist/Restore Round-Trip', () => {
  /**
   * **Validates: Requirements 3.5**
   *
   * For any valid PersistedPendingState with matching version, persisting
   * then restoring by the same sessionId SHALL produce a deeply equal
   * object. The round-trip applies to the post-truncation payload (after
   * prepareMessagesForStorage), not the original input.
   */
  it('persist then restore produces deeply equal post-truncation payload', () => {
    fc.assert(
      fc.property(
        arbMessages,
        arbPendingQuestion,
        arbSessionId,
        (messages, pendingQuestion, sessionId) => {
          // Persist — internally calls prepareMessagesForStorage
          persistPendingState(sessionId, messages, pendingQuestion);

          // Restore
          const restored = restorePendingState(sessionId);

          // Build expected post-truncation payload
          const expected = {
            version: PERSISTED_STATE_VERSION,
            messages: prepareMessagesForStorage(messages),
            pendingQuestion,
            sessionId,
          };

          expect(restored).not.toBeNull();
          expect(restored).toEqual(expected);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 3.5**
   *
   * Round-trip holds even for large sessions where tool_result content
   * gets truncated by prepareMessagesForStorage (80+ tool_use blocks).
   */
  it('round-trip holds for large sessions with truncated tool_result content', () => {
    // Build a message array that exceeds the 80 tool_use threshold
    const manyToolUseBlocks: ContentBlock[] = Array.from({ length: 85 }, (_, i) => ({
      type: 'tool_use' as const,
      id: `tool-use-${i}`,
      name: `tool_${i}`,
      summary: 'Using tool',
    }));
    const longToolResult: ContentBlock = {
      type: 'tool_result' as const,
      toolUseId: 'tool-use-0',
      content: 'x'.repeat(500), // Will be truncated to 200 chars + '…'
      isError: false,
      truncated: false,
    };

    const messages: Message[] = [
      {
        id: 'msg-1',
        role: 'assistant',
        content: [...manyToolUseBlocks, longToolResult],
        timestamp: new Date().toISOString(),
      },
    ];
    const pendingQuestion: PendingQuestion = {
      toolUseId: 'tool-use-0',
      questions: [{ question: 'q', header: 'h', options: [], multiSelect: false }],
    };
    const sessionId = 'large-session-test';

    persistPendingState(sessionId, messages, pendingQuestion);
    const restored = restorePendingState(sessionId);

    const expected = {
      version: PERSISTED_STATE_VERSION,
      messages: prepareMessagesForStorage(messages),
      pendingQuestion,
      sessionId,
    };

    expect(restored).not.toBeNull();
    expect(restored).toEqual(expected);

    // Verify truncation actually happened
    const restoredResult = restored!.messages[0].content.find(
      (b) => b.type === 'tool_result',
    );
    expect(restoredResult).toBeDefined();
    if (restoredResult && restoredResult.type === 'tool_result') {
      const raw = restoredResult as unknown as Record<string, unknown>;
      expect(typeof raw.content).toBe('string');
      expect((raw.content as string).length).toBeLessThanOrEqual(201);
    }
  });
});

// ---------------------------------------------------------------------------
// Property 3: Version Mismatch Discards State
// ---------------------------------------------------------------------------

describe('Feature: chat-experience-cleanup, Property 3: Version Mismatch Discards State', () => {
  /**
   * **Validates: Requirements 3.4**
   *
   * For any valid PersistedPendingState persisted with version V, if the
   * stored version is changed to W≠V, restorePendingState SHALL return
   * null and the sessionStorage entry SHALL be removed.
   *
   * Strategy: We persist normally (writes current PERSISTED_STATE_VERSION),
   * then manually tamper with the stored JSON to set a different version
   * before calling restorePendingState.
   */
  it('restorePendingState returns null when stored version differs from current', () => {
    fc.assert(
      fc.property(
        arbMessages,
        arbPendingQuestion,
        arbSessionId,
        // Generate a version that is guaranteed to differ from PERSISTED_STATE_VERSION
        fc.integer({ min: -1000, max: 1000 }).filter((v) => v !== PERSISTED_STATE_VERSION),
        (messages, pendingQuestion, sessionId, wrongVersion) => {
          // Persist with the current (correct) version
          persistPendingState(sessionId, messages, pendingQuestion);

          // Verify it was stored
          const key = `${STORAGE_KEY_PREFIX}${sessionId}`;
          const raw = window.sessionStorage.getItem(key);
          expect(raw).not.toBeNull();

          // Tamper: replace the version in the stored JSON
          const parsed = JSON.parse(raw!);
          parsed.version = wrongVersion;
          window.sessionStorage.setItem(key, JSON.stringify(parsed));

          // Restore should return null due to version mismatch
          const restored = restorePendingState(sessionId);
          expect(restored).toBeNull();

          // The stale entry should have been removed
          expect(window.sessionStorage.getItem(key)).toBeNull();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 3.4**
   *
   * Edge case: a missing version field (e.g., legacy data persisted before
   * schema versioning was added) should also be discarded.
   */
  it('restorePendingState returns null when stored entry has no version field', () => {
    const sessionId = 'no-version-session';
    const key = `${STORAGE_KEY_PREFIX}${sessionId}`;

    // Write a payload that lacks the version field entirely
    const legacyPayload = {
      messages: [
        {
          id: 'msg-1',
          role: 'user',
          content: [{ type: 'text', text: 'hello' }],
          timestamp: new Date().toISOString(),
        },
      ],
      pendingQuestion: {
        toolUseId: 'tu-1',
        questions: [{ question: 'q', header: 'h', options: [], multiSelect: false }],
      },
      sessionId,
    };
    window.sessionStorage.setItem(key, JSON.stringify(legacyPayload));

    const restored = restorePendingState(sessionId);
    expect(restored).toBeNull();
    expect(window.sessionStorage.getItem(key)).toBeNull();
  });
});
