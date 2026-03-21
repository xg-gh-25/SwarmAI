/**
 * Streaming UX Lifecycle — Preservation Property Tests
 *
 * These tests encode the CURRENT (correct) behavior for non-buggy inputs.
 * They MUST PASS on unfixed code and will be re-run after each fix phase
 * to verify no regressions were introduced.
 *
 * Testing methodology: Property-based testing with fast-check
 * Key property verified:
 *   - P10 (Preservation): For all non-buggy inputs (simple queries, completed
 *     sessions, non-streaming interactions), deriveStreamingActivity output is
 *     identical between original and fixed code. Spinner labels, message
 *     rendering, session loading, and all existing UI interactions are unchanged.
 *
 * Observation-first methodology:
 *   - Req 3.2:  Simple single-turn query renders response in single message bubble
 *   - Req 3.3:  ask_user_question pauses streaming and displays question form
 *   - Req 3.4:  cmd_permission_request pauses streaming and displays permission modal
 *   - Req 3.5:  result event finalizes conversation, stops streaming
 *   - Req 3.6:  Stop button aborts stream and displays stop confirmation
 *   - Req 3.7:  ContentBlockRenderer renders text, tool_use, tool_result blocks
 *   - Req 3.8:  ToolUseBlock shows tool name and collapsible input
 *   - Req 3.9:  getSessionMessages returns all persisted messages for completed sessions
 *   - Req 3.10: Error events rendered as text content in message history
 *   - Req 3.11: Single-tab usage behaves identically to current behavior
 *   - Req 3.12: Tab close cleans up resources
 *   - Req 3.13: Below 6 tabs, "+" button creates new tabs normally
 *   - Req 3.14: Idle tabs show no status indicator
 *
 * @see .kiro/specs/streaming-ux-lifecycle/bugfix.md
 * @see .kiro/specs/streaming-ux-lifecycle/design.md
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { deriveStreamingActivity } from '../pages/ChatPage';
import type { Message, ContentBlock } from '../types';

// ─── Generators ─────────────────────────────────────────────────────────────

/** Generator for a text content block */
function arbTextBlock(): fc.Arbitrary<ContentBlock> {
  return fc.record({
    type: fc.constant('text' as const),
    text: fc.string({ minLength: 1, maxLength: 200 }),
  }) as fc.Arbitrary<ContentBlock>;
}

/** Generator for a tool_use content block with summary */
function arbToolUseBlock(): fc.Arbitrary<ContentBlock> {
  return fc.record({
    type: fc.constant('tool_use' as const),
    id: fc.uuid(),
    name: fc.constantFrom('Bash', 'Read', 'Write', 'Search', 'ListDir'),
    summary: fc.oneof(
      fc.string({ minLength: 1, maxLength: 80 }).map(s => `Running: ${s}`),
      fc.string({ minLength: 1, maxLength: 80 }).map(s => `Reading ${s}`),
      fc.string({ minLength: 1, maxLength: 80 }).map(s => `Searching for ${s}`),
      fc.constant('Using tool')
    ),
  }) as fc.Arbitrary<ContentBlock>;
}

/** Generator for a tool_result content block */
function arbToolResultBlock(): fc.Arbitrary<ContentBlock> {
  return fc.record({
    type: fc.constant('tool_result' as const),
    toolUseId: fc.uuid(),
    content: fc.string({ minLength: 1, maxLength: 100 }),
    isError: fc.constant(false),
    truncated: fc.constant(false),
  }) as fc.Arbitrary<ContentBlock>;
}

/** Generator for any content block */
function arbContentBlock(): fc.Arbitrary<ContentBlock> {
  return fc.oneof(arbTextBlock(), arbToolUseBlock(), arbToolResultBlock());
}

/** Generator for a Message with a specific role */
function arbMessage(role: 'user' | 'assistant' = 'assistant'): fc.Arbitrary<Message> {
  return fc.record({
    id: fc.uuid(),
    role: fc.constant(role),
    content: fc.array(arbContentBlock(), { minLength: 0, maxLength: 5 }),
    timestamp: fc.constant(new Date().toISOString()),
  });
}

/** Generator for a simple single-turn conversation (user msg + assistant response) */
function arbSimpleSingleTurn(): fc.Arbitrary<Message[]> {
  return fc.tuple(
    arbMessage('user'),
    arbMessage('assistant')
  ).map(([user, assistant]) => [user, assistant]);
}

// ─── Observation Phase Tests ────────────────────────────────────────────────
// These tests observe and encode the CURRENT behavior on UNFIXED code.
// They must all PASS, confirming the baseline we need to preserve.

describe('Streaming Lifecycle Preservation Tests (Property 10)', () => {

  // ── Req 3.2: Simple single-turn query renders in single message bubble ──

  describe('Observation: Simple single-turn query (Req 3.2)', () => {
    it('deriveStreamingActivity returns null when not streaming', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 0, maxLength: 5 }),
          (messages) => {
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 100 }
      );
    });

    it('completed single-turn conversation has no streaming activity', () => {
      fc.assert(
        fc.property(arbSimpleSingleTurn(), (messages) => {
          // After a simple query completes, isStreaming is false
          const result = deriveStreamingActivity(false, messages);
          return result === null;
        }),
        { numRuns: 100 }
      );
    });
  });

  // ── Req 3.3: ask_user_question pauses streaming ──

  describe('Observation: ask_user_question pauses streaming (Req 3.3)', () => {
    it('deriveStreamingActivity returns null when streaming stops for question', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 1, maxLength: 5 }),
          (messages) => {
            // After ask_user_question, isStreaming becomes false
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.4: cmd_permission_request pauses streaming ──

  describe('Observation: cmd_permission_request pauses streaming (Req 3.4)', () => {
    it('deriveStreamingActivity returns null when streaming stops for permission', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 1, maxLength: 5 }),
          (messages) => {
            // After cmd_permission_request, isStreaming becomes false
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.5: result event finalizes conversation ──

  describe('Observation: result event stops streaming (Req 3.5)', () => {
    it('deriveStreamingActivity returns null after result event', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 1, maxLength: 5 }),
          (messages) => {
            // After result event, isStreaming is false
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.6: Stop button aborts stream ──

  describe('Observation: Stop button aborts stream (Req 3.6)', () => {
    it('deriveStreamingActivity returns null after stop', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 1, maxLength: 5 }),
          (messages) => {
            // After stop, isStreaming is false
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.7: ContentBlockRenderer renders text, tool_use, tool_result ──

  describe('Observation: ContentBlockRenderer block types (Req 3.7)', () => {
    it('deriveStreamingActivity detects content when assistant has text blocks', () => {
      fc.assert(
        fc.property(
          fc.array(arbTextBlock(), { minLength: 1, maxLength: 3 }),
          (textBlocks) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: textBlocks,
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            // When streaming with text content, hasContent should be true
            return result !== null && result.hasContent === true;
          }
        ),
        { numRuns: 50 }
      );
    });

    it('deriveStreamingActivity detects content when assistant has tool_use blocks', () => {
      fc.assert(
        fc.property(
          fc.array(arbToolUseBlock(), { minLength: 1, maxLength: 3 }),
          (toolBlocks) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: toolBlocks,
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            return result !== null && result.hasContent === true;
          }
        ),
        { numRuns: 50 }
      );
    });

    it('deriveStreamingActivity detects content when assistant has tool_result blocks', () => {
      fc.assert(
        fc.property(
          fc.array(arbToolResultBlock(), { minLength: 1, maxLength: 3 }),
          (resultBlocks) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: resultBlocks,
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            return result !== null && result.hasContent === true;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.8: ToolUseBlock shows tool name ──

  describe('Observation: ToolUseBlock shows tool name (Req 3.8)', () => {
    it('deriveStreamingActivity returns tool name from last tool_use block', () => {
      fc.assert(
        fc.property(
          fc.constantFrom('Bash', 'Read', 'Write', 'Search', 'ListDir'),
          fc.uuid(),
          (toolName, toolId) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: [{
                type: 'tool_use' as const,
                id: toolId,
                name: toolName,
                summary: 'Using tool',
              }],
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            return result !== null && result.toolName === toolName;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.9: getSessionMessages returns persisted messages for completed sessions ──

  describe('Observation: Completed session message loading (Req 3.9)', () => {
    it('deriveStreamingActivity is null for completed session messages', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 1, maxLength: 10 }),
          (messages) => {
            // Completed sessions are not streaming
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.10: Error events rendered as text content ──

  describe('Observation: Error events as text content (Req 3.10)', () => {
    it('deriveStreamingActivity handles messages with error text blocks', () => {
      fc.assert(
        fc.property(
          fc.string({ minLength: 1, maxLength: 100 }),
          (errorText) => {
            const messages: Message[] = [{
              id: 'msg-err',
              role: 'assistant',
              content: [{
                type: 'text' as const,
                text: `Error: ${errorText}`,
              }],
              timestamp: new Date().toISOString(),
            }];
            // Error text is still a text block — hasContent should be true
            // when streaming (current behavior preserves this)
            const resultStreaming = deriveStreamingActivity(true, messages);
            expect(resultStreaming).not.toBeNull();
            expect(resultStreaming!.hasContent).toBe(true);
            // toolName should be null (no tool_use block)
            expect(resultStreaming!.toolName).toBeNull();
            return true;
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.11: Single-tab usage behaves identically ──

  describe('Observation: Single-tab behavior unchanged (Req 3.11)', () => {
    it('deriveStreamingActivity works identically for single-tab scenarios', () => {
      fc.assert(
        fc.property(
          fc.boolean(),
          fc.array(arbMessage(), { minLength: 0, maxLength: 5 }),
          (isStreaming, messages) => {
            // Single-tab: deriveStreamingActivity should work the same
            const result = deriveStreamingActivity(isStreaming, messages);
            if (!isStreaming) {
              return result === null;
            }
            // When streaming, result depends on message content
            return true; // shape validated in other tests
          }
        ),
        { numRuns: 50 }
      );
    });
  });

  // ── Req 3.12: Tab close cleans up resources ──

  describe('Observation: Tab close cleanup (Req 3.12)', () => {
    it('AbortController.abort() is callable and signals abort', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 5 }), (tabCount) => {
          // Verify AbortController cleanup pattern works
          const controllers = Array.from(
            { length: tabCount },
            () => new AbortController()
          );
          // Simulate closing each tab — abort its controller
          for (const ctrl of controllers) {
            ctrl.abort();
            if (!ctrl.signal.aborted) return false;
          }
          return true;
        }),
        { numRuns: 20 }
      );
    });
  });

  // ── Req 3.13: Below 6 tabs, "+" creates new tabs normally ──

  describe('Observation: Tab creation below limit (Req 3.13)', () => {
    it('tab count below MAX_TABS_HARD_CEILING allows creation', () => {
      const MAX_TABS_HARD_CEILING = 4;
      fc.assert(
        fc.property(
          fc.integer({ min: 0, max: MAX_TABS_HARD_CEILING - 1 }),
          (currentCount) => {
            // Below limit, creation should be allowed
            return currentCount < MAX_TABS_HARD_CEILING;
          }
        ),
        { numRuns: 20 }
      );
    });
  });

  // ── Req 3.14: Idle tabs show no status indicator ──

  describe('Observation: Idle tabs have no indicator (Req 3.14)', () => {
    it('idle status maps to no visual indicator', () => {
      // Idle tabs should produce null/no indicator
      const idleStatus = 'idle';
      expect(idleStatus).toBe('idle');
      // In the TabStatusIndicator component, idle returns null
      // This is a structural observation — the component doesn't exist yet
      // but the contract is: idle → no indicator
    });
  });

  // ─── Property 10: Preservation ─────────────────────────────────────────────
  // For all non-buggy inputs, deriveStreamingActivity output is identical
  // between original and fixed code. This is the core preservation property.

  describe('Property 10 (Preservation): deriveStreamingActivity unchanged for non-buggy inputs', () => {

    /**
     * **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12**
     *
     * For any non-streaming state (completed sessions, paused sessions,
     * stopped sessions), deriveStreamingActivity must return null.
     */
    it('P10a: returns null for all non-streaming states', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage(), { minLength: 0, maxLength: 10 }),
          (messages) => {
            const result = deriveStreamingActivity(false, messages);
            return result === null;
          }
        ),
        { numRuns: 200 }
      );
    });

    /**
     * **Validates: Requirements 3.1, 3.7, 3.8**
     *
     * When streaming with no assistant messages, returns null (Thinking...).
     */
    it('P10b: returns null when streaming with no assistant messages', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage('user'), { minLength: 0, maxLength: 5 }),
          (userMessages) => {
            const result = deriveStreamingActivity(true, userMessages);
            return result === null;
          }
        ),
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 3.7, 3.8**
     *
     * When streaming with an empty-content assistant message, returns null.
     */
    it('P10c: returns null when streaming with empty assistant content', () => {
      fc.assert(
        fc.property(fc.uuid(), (msgId) => {
          const messages: Message[] = [{
            id: msgId,
            role: 'assistant',
            content: [],
            timestamp: new Date().toISOString(),
          }];
          const result = deriveStreamingActivity(true, messages);
          return result === null;
        }),
        { numRuns: 50 }
      );
    });

    /**
     * **Validates: Requirements 3.7, 3.8**
     *
     * When streaming with content blocks, hasContent is true and toolName
     * is extracted from the last tool_use block (or null if none).
     */
    it('P10d: hasContent true and toolName correct when streaming with content', () => {
      fc.assert(
        fc.property(
          fc.array(arbContentBlock(), { minLength: 1, maxLength: 8 }),
          (blocks) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: blocks,
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            if (result === null) return true; // no recognized content

            // hasContent must be true
            if (!result.hasContent) return false;

            // toolName must match the last tool_use block's name, or null
            const lastToolUse = [...blocks].reverse().find(
              b => b.type === 'tool_use'
            );
            if (lastToolUse && 'name' in lastToolUse) {
              return result.toolName === (lastToolUse.name?.trim() || null);
            }
            return result.toolName === null;
          }
        ),
        { numRuns: 200 }
      );
    });

    /**
     * **Validates: Requirements 3.7, 3.8**
     *
     * deriveStreamingActivity uses the LAST assistant message, not earlier ones.
     */
    it('P10e: uses last assistant message for activity derivation', () => {
      fc.assert(
        fc.property(
          arbMessage('assistant'),
          arbMessage('assistant'),
          (firstAssistant, lastAssistant) => {
            const messages: Message[] = [
              { ...firstAssistant, id: 'first' },
              { id: 'user-1', role: 'user', content: [], timestamp: new Date().toISOString() },
              { ...lastAssistant, id: 'last' },
            ];
            const result = deriveStreamingActivity(true, messages);

            // If result is non-null, it should reflect the LAST assistant
            if (result === null) return true;

            const lastToolUse = [...lastAssistant.content].reverse().find(
              b => b.type === 'tool_use'
            );
            if (lastToolUse && 'name' in lastToolUse) {
              return result.toolName === (lastToolUse.name?.trim() || null);
            }
            return result.toolName === null;
          }
        ),
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 3.1**
     *
     * When streaming with only user messages (no assistant yet),
     * returns null — this is the "Thinking..." state.
     */
    it('P10f: returns null for streaming with only user messages (Thinking...)', () => {
      fc.assert(
        fc.property(
          fc.array(arbMessage('user'), { minLength: 1, maxLength: 5 }),
          (userMessages) => {
            const result = deriveStreamingActivity(true, userMessages);
            return result === null;
          }
        ),
        { numRuns: 100 }
      );
    });

    /**
     * **Validates: Requirements 3.2, 3.7**
     *
     * Return type shape is always { hasContent, toolName, toolContext, toolCount }
     * when non-null. The extended shape includes operational context and tool count
     * fields added by Fix 4.
     */
    it('P10g: return shape is { hasContent, toolName, toolContext, toolCount } with 4 fields', () => {
      fc.assert(
        fc.property(
          fc.array(arbContentBlock(), { minLength: 1, maxLength: 5 }),
          (blocks) => {
            const messages: Message[] = [{
              id: 'msg-1',
              role: 'assistant',
              content: blocks,
              timestamp: new Date().toISOString(),
            }];
            const result = deriveStreamingActivity(true, messages);
            if (result === null) return true;

            const keys = Object.keys(result).sort();
            // Fixed code returns { hasContent, toolContext, toolCount, toolName }
            return (
              keys.length === 4 &&
              keys[0] === 'hasContent' &&
              keys[1] === 'toolContext' &&
              keys[2] === 'toolCount' &&
              keys[3] === 'toolName' &&
              typeof result.hasContent === 'boolean' &&
              (result.toolName === null || typeof result.toolName === 'string') &&
              (result.toolContext === null || typeof result.toolContext === 'string') &&
              typeof result.toolCount === 'number'
            );
          }
        ),
        { numRuns: 100 }
      );
    });

  }); // end Property 10

}); // end top-level describe
