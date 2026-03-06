/**
 * Property-Based Tests for ChatPage Spinner Label — Bug Condition Exploration
 *
 * Tests the `deriveStreamingActivity` pure function exported from ChatPage.tsx.
 * Uses fast-check to generate streaming states where `isStreaming=true` AND the
 * last assistant message has content blocks (text, tool_use, tool_result), then
 * asserts the spinner label reflects the activity state rather than always
 * showing "Thinking...".
 *
 * **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2**
 *
 * Property 1: Fault Condition — Spinner Always Shows "Thinking..." When Content
 * Blocks Exist. On unfixed code this test FAILS because `deriveStreamingActivity`
 * does not exist or always returns null. On fixed code it PASSES.
 */

import { describe, it, expect } from 'vitest';
import * as fc from 'fast-check';
import { deriveStreamingActivity } from '../ChatPage';
import type { Message, ContentBlock, TextContent, ToolUseContent, ToolResultContent } from '../../types';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate a random non-empty, pre-trimmed string for tool / text content. */
const nonEmptyString = fc
  .string({ minLength: 1, maxLength: 30 })
  .map(s => s.trim())
  .filter(s => s.length > 0);

/** Generate a TextContent block. */
const textContentArb: fc.Arbitrary<TextContent> = nonEmptyString.map(text => ({
  type: 'text' as const,
  text,
}));

/** Generate a ToolUseContent block with a given or random name. */
const toolUseContentArb: fc.Arbitrary<ToolUseContent> = nonEmptyString.map(name => ({
  type: 'tool_use' as const,
  id: `toolu_${Math.random().toString(36).slice(2, 10)}`,
  name,
  summary: 'Using tool',
}));

/** Generate a ToolResultContent block. */
const toolResultContentArb: fc.Arbitrary<ToolResultContent> = fc.record({
  type: fc.constant('tool_result' as const),
  toolUseId: fc.string({ minLength: 5, maxLength: 15 }),
  content: fc.option(fc.string(), { nil: undefined }),
  isError: fc.boolean(),
  truncated: fc.boolean(),
});

/**
 * Generate an array of content blocks that contains at least one block of type
 * text, tool_use, or tool_result — i.e. the bug condition content.
 */
const bugConditionContentArb: fc.Arbitrary<ContentBlock[]> = fc
  .tuple(
    fc.oneof(textContentArb, toolUseContentArb, toolResultContentArb),
    fc.array(
      fc.oneof(textContentArb, toolUseContentArb, toolResultContentArb),
      { minLength: 0, maxLength: 5 },
    ),
  )
  .map(([required, rest]) => [required, ...rest]);

/**
 * Generate content blocks where the LAST block is a tool_use with a known name.
 * This targets the "activity indicator shows tool name" property.
 */
const contentWithTrailingToolUseArb: fc.Arbitrary<{
  blocks: ContentBlock[];
  expectedToolName: string;
}> = fc
  .tuple(
    fc.array(
      fc.oneof(textContentArb, toolResultContentArb),
      { minLength: 0, maxLength: 4 },
    ),
    nonEmptyString,
  )
  .map(([prefix, toolName]) => ({
    blocks: [
      ...prefix,
      {
        type: 'tool_use' as const,
        id: `toolu_${Math.random().toString(36).slice(2, 10)}`,
        name: toolName,
        summary: 'Using tool',
      },
    ],
    expectedToolName: toolName,
  }));

/** Build a minimal Message object for testing. */
function makeMessage(
  role: 'user' | 'assistant',
  content: ContentBlock[],
): Message {
  return {
    id: `msg_${Math.random().toString(36).slice(2, 10)}`,
    role,
    content,
    timestamp: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Property 1: Fault Condition — Spinner Label Reflects Activity State
// ---------------------------------------------------------------------------

describe('deriveStreamingActivity — Bug Condition Exploration', () => {
  /**
   * **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1, 2.2**
   *
   * Property 1a: When isStreaming=true AND the last assistant message has
   * content blocks containing a tool_use, the result MUST include the tool
   * name (not null) so the UI can show "Running: {tool}..." instead of
   * "Thinking...".
   */
  it('returns toolName when last content block is tool_use (not "Thinking...")', () => {
    fc.assert(
      fc.property(
        contentWithTrailingToolUseArb,
        ({ blocks, expectedToolName }) => {
          const messages: Message[] = [
            makeMessage('user', [{ type: 'text', text: 'hello' }]),
            makeMessage('assistant', blocks),
          ];

          const result = deriveStreamingActivity(true, messages);

          // Bug condition: the function MUST return non-null with the tool name
          expect(result).not.toBeNull();
          expect(result!.hasContent).toBe(true);
          expect(result!.toolName).toBe(expectedToolName);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 1.2, 2.1, 2.2**
   *
   * Property 1b: When isStreaming=true AND the last assistant message has
   * content blocks but NO tool_use block, the result MUST have
   * hasContent=true and toolName=null — so the UI shows "Processing..."
   * instead of "Thinking...".
   */
  it('returns hasContent=true, toolName=null for text-only content (not "Thinking...")', () => {
    fc.assert(
      fc.property(
        fc.array(textContentArb, { minLength: 1, maxLength: 5 }),
        (textBlocks) => {
          const messages: Message[] = [
            makeMessage('user', [{ type: 'text', text: 'hello' }]),
            makeMessage('assistant', textBlocks),
          ];

          const result = deriveStreamingActivity(true, messages);

          expect(result).not.toBeNull();
          expect(result!.hasContent).toBe(true);
          expect(result!.toolName).toBeNull();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 1.1, 1.4, 2.2, 2.4**
   *
   * Property 1c: When isStreaming=true AND content blocks exist with a
   * tool_use block anywhere in the array, the function MUST identify the
   * LAST tool_use block's name — confirming the activity indicator shows
   * the most recent tool, not the first one.
   */
  it('identifies the LAST tool_use name when multiple tool_use blocks exist', () => {
    fc.assert(
      fc.property(
        nonEmptyString,
        nonEmptyString,
        (firstName, lastName) => {
          // Ensure the two names are different so we can verify "last wins"
          fc.pre(firstName !== lastName);

          const blocks: ContentBlock[] = [
            { type: 'tool_use', id: 'tu_1', name: firstName, summary: 'Using tool' },
            { type: 'text', text: 'intermediate output' },
            { type: 'tool_use', id: 'tu_2', name: lastName, summary: 'Using tool' },
          ];
          const messages: Message[] = [
            makeMessage('user', [{ type: 'text', text: 'do stuff' }]),
            makeMessage('assistant', blocks),
          ];

          const result = deriveStreamingActivity(true, messages);

          expect(result).not.toBeNull();
          expect(result!.toolName).toBe(lastName);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 1.1, 1.2, 1.3, 2.1**
   *
   * Property 1d: For ANY content block array satisfying the bug condition
   * (at least one text, tool_use, or tool_result block), the function MUST
   * return a non-null result with hasContent=true. This is the core
   * exploration property — on unfixed code it would return null (meaning
   * "Thinking..." always shows).
   */
  it('always returns non-null with hasContent=true when bug condition content exists', () => {
    fc.assert(
      fc.property(
        bugConditionContentArb,
        (blocks) => {
          const messages: Message[] = [
            makeMessage('user', [{ type: 'text', text: 'query' }]),
            makeMessage('assistant', blocks),
          ];

          const result = deriveStreamingActivity(true, messages);

          expect(result).not.toBeNull();
          expect(result!.hasContent).toBe(true);
        },
      ),
      { numRuns: 200 },
    );
  });
});

// ---------------------------------------------------------------------------
// Generators — Preservation (non-bug-condition inputs)
// ---------------------------------------------------------------------------

/** Generate an AskUserQuestionContent block (NOT a bug-condition block type). */
const askUserQuestionContentArb: fc.Arbitrary<ContentBlock> = fc.record({
  type: fc.constant('ask_user_question' as const),
  toolUseId: fc.string({ minLength: 5, maxLength: 15 }),
  questions: fc.constant([{
    question: 'Pick one',
    header: 'Choice',
    options: [{ label: 'A', description: 'Option A' }],
    multiSelect: false,
  }] as import('../../types').AskUserQuestion[]),
});

/** Generate a random Message role — user or assistant. */
const roleArb = fc.constantFrom<'user' | 'assistant'>('user', 'assistant');

/**
 * Generate a random content block array that does NOT satisfy the bug
 * condition — i.e. contains no text, tool_use, or tool_result blocks.
 * This means either empty or only ask_user_question blocks.
 */
const nonBugContentArb: fc.Arbitrary<ContentBlock[]> = fc.oneof(
  fc.constant([] as ContentBlock[]),
  fc.array(askUserQuestionContentArb, { minLength: 1, maxLength: 3 }),
);

/**
 * Generate a random array of messages where the last assistant message
 * (if any) has content that does NOT satisfy the bug condition.
 */
const messagesWithNonBugAssistantArb: fc.Arbitrary<Message[]> = fc
  .tuple(
    fc.array(
      fc.tuple(roleArb, nonBugContentArb).map(([role, content]) =>
        makeMessage(role, content),
      ),
      { minLength: 0, maxLength: 4 },
    ),
    nonBugContentArb,
  )
  .map(([prefix, lastContent]) => [
    ...prefix,
    makeMessage('assistant', lastContent),
  ]);

/**
 * Generate a random array of messages with NO assistant messages at all
 * — only user messages.
 */
const userOnlyMessagesArb: fc.Arbitrary<Message[]> = fc
  .array(
    fc.array(
      fc.oneof(textContentArb, toolUseContentArb, toolResultContentArb),
      { minLength: 0, maxLength: 3 },
    ),
    { minLength: 1, maxLength: 5 },
  )
  .map(contentArrays =>
    contentArrays.map(content => makeMessage('user', content)),
  );

/**
 * Generate a random array of messages (any roles, any content) for
 * non-streaming preservation tests.
 */
const anyMessagesArb: fc.Arbitrary<Message[]> = fc.array(
  fc.tuple(
    roleArb,
    fc.oneof(
      bugConditionContentArb,
      nonBugContentArb,
      fc.constant([] as ContentBlock[]),
    ),
  ).map(([role, content]) => makeMessage(role, content)),
  { minLength: 0, maxLength: 6 },
);

// ---------------------------------------------------------------------------
// Property 2: Preservation — Non-Streaming and Empty-Content States Unchanged
// ---------------------------------------------------------------------------

describe('deriveStreamingActivity — Preservation', () => {
  /**
   * **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**
   *
   * Property 2a: When isStreaming=false, deriveStreamingActivity returns
   * null regardless of message content. This preserves the original
   * behavior for completed conversations, idle states, and history viewing.
   */
  it('returns null when isStreaming=false regardless of messages', () => {
    fc.assert(
      fc.property(anyMessagesArb, (messages) => {
        const result = deriveStreamingActivity(false, messages);
        expect(result).toBeNull();
      }),
      { numRuns: 200 },
    );
  });

  /**
   * **Validates: Requirements 3.1**
   *
   * Property 2b: When isStreaming=true AND the last assistant message has
   * an empty content array (no blocks received yet), deriveStreamingActivity
   * returns null — preserving the "Thinking..." spinner for initial wait.
   */
  it('returns null when isStreaming=true and last assistant has empty content', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.tuple(roleArb, nonBugContentArb).map(([role, content]) =>
            makeMessage(role, content),
          ),
          { minLength: 0, maxLength: 4 },
        ),
        (prefix) => {
          const messages: Message[] = [
            ...prefix,
            makeMessage('assistant', []),
          ];
          const result = deriveStreamingActivity(true, messages);
          expect(result).toBeNull();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 3.1, 3.2**
   *
   * Property 2c: When isStreaming=true AND no assistant message exists in
   * the messages array (only user messages), deriveStreamingActivity
   * returns null — preserving the "Thinking..." spinner.
   */
  it('returns null when isStreaming=true and no assistant message exists', () => {
    fc.assert(
      fc.property(userOnlyMessagesArb, (messages) => {
        const result = deriveStreamingActivity(true, messages);
        expect(result).toBeNull();
      }),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 3.1, 3.3, 3.4**
   *
   * Property 2d: When isStreaming=true AND the last assistant message
   * contains ONLY ask_user_question blocks (no text, tool_use, or
   * tool_result), deriveStreamingActivity returns null — these block
   * types do not satisfy the bug condition.
   */
  it('returns null when assistant content has only ask_user_question blocks', () => {
    fc.assert(
      fc.property(
        fc.array(askUserQuestionContentArb, { minLength: 1, maxLength: 3 }),
        (askBlocks) => {
          const messages: Message[] = [
            makeMessage('user', [{ type: 'text', text: 'hello' }]),
            makeMessage('assistant', askBlocks),
          ];
          const result = deriveStreamingActivity(true, messages);
          expect(result).toBeNull();
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
   *
   * Property 2e (composite): For ALL inputs where the bug condition does
   * NOT hold, deriveStreamingActivity returns null. This is the master
   * preservation property — it covers non-streaming, empty content, no
   * assistant messages, and non-bug content block types.
   */
  it('returns null for all non-bug-condition inputs (composite preservation)', () => {
    fc.assert(
      fc.property(messagesWithNonBugAssistantArb, (messages) => {
        // isStreaming=true but last assistant has non-bug content
        const resultStreaming = deriveStreamingActivity(true, messages);
        expect(resultStreaming).toBeNull();

        // isStreaming=false — always null
        const resultNotStreaming = deriveStreamingActivity(false, messages);
        expect(resultNotStreaming).toBeNull();
      }),
      { numRuns: 200 },
    );
  });

  /**
   * **Validates: Requirements 3.1**
   *
   * Property 2f: Empty messages array always returns null regardless of
   * isStreaming state.
   */
  it('returns null for empty messages array', () => {
    fc.assert(
      fc.property(fc.boolean(), (isStreaming) => {
        const result = deriveStreamingActivity(isStreaming, []);
        expect(result).toBeNull();
      }),
      { numRuns: 50 },
    );
  });
});
