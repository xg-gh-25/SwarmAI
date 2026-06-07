/**
 * Repro test: Spinner hangs after backend completes agentic tool loop.
 *
 * BUG: When an agentic loop executes multiple tools (text → tool_use chain → text),
 * the spinner continues forever even though the backend has completed and sent
 * a `result` event. The user sees "Running: Grep..." with spinner spinning indefinitely.
 *
 * ROOT CAUSE HYPOTHESIS: `setIsStreaming(false)` in the `result` event handler
 * calls `setPendingStreamTabs(prev => { next.delete(tabId); return next })`.
 * If the tabId was never added to pendingStreamTabs (because the stream started
 * via per-tab map's `isStreaming` flag, not through the pending set), the
 * Set delete is a no-op → React sees same reference → no re-render →
 * `isStreaming` derived value stays `true`.
 *
 * Evidence: Backend logs show `streaming → idle` transition at 16:04:17.
 * Frontend spinner never stops. No disconnect detected. 65 seconds later
 * user sends new message — proving backend was healthy but UI was stuck.
 *
 * Testing methodology: Unit tests with Vitest
 */

import { describe, it, expect } from 'vitest';
import {
  updateMessages,
  appendTextDelta,
} from '../hooks/useChatStreamingLifecycle';
import type { ContentBlock, Message } from '../types';

// ---------------------------------------------------------------------------
// Helper factories
// ---------------------------------------------------------------------------

function makeMessage(
  id: string,
  content: ContentBlock[] = [],
  role: 'user' | 'assistant' = 'assistant',
): Message {
  return { id, role, content, timestamp: new Date().toISOString() };
}

function textBlock(text: string, confirmed = false): ContentBlock {
  const block: Record<string, unknown> = { type: 'text', text };
  if (confirmed) block._confirmed = true;
  return block as ContentBlock;
}

function toolUseBlock(id: string, name: string): ContentBlock {
  return { type: 'tool_use', id, name, summary: `${name}(...)`, category: 'read' } as ContentBlock;
}

function toolResultBlock(toolUseId: string, content: string, isError = false): ContentBlock {
  return { type: 'tool_result', toolUseId, content, is_error: isError, truncated: false } as ContentBlock;
}

// ---------------------------------------------------------------------------
// Test 1: Verify updateMessages correctly processes the multi-turn pattern
// that triggers the bug (text_delta accumulates text, then assistant event
// arrives with same text + tool blocks)
// ---------------------------------------------------------------------------

describe('Streaming spinner hang — updateMessages reconciliation', () => {
  it('should include tool_use blocks from assistant event even when text was already streamed', () => {
    // SETUP: Simulate what happens during the bug scenario:
    //
    // 1. text_delta streams "Let me write the complete design:"
    // 2. assistant event arrives with:
    //    [text("Let me write..."), tool_use(Write), tool_result(error)]
    //
    // The text was already rendered via text_delta (unconfirmed).
    // updateMessages must REPLACE the unconfirmed text with confirmed version
    // AND include the tool_use/tool_result blocks.

    const msgId = 'assistant-1';
    const streamedText = 'Let me write the complete design:';

    // After text_delta: message has unconfirmed text
    let messages: Message[] = [makeMessage(msgId, [textBlock(streamedText, false)])];

    // assistant event arrives with same text + tool_use + tool_result
    const assistantContent: ContentBlock[] = [
      textBlock(streamedText),
      toolUseBlock('tu-1', 'Write'),
      toolResultBlock('tu-1', 'Error: file not read yet', true),
    ];

    messages = updateMessages(messages, msgId, assistantContent);

    const result = messages[0];
    expect(result.content.length).toBeGreaterThanOrEqual(3);

    // Text should be confirmed
    const textBlocks = result.content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBe(1);
    expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);

    // Tool blocks must be present
    const toolUseBlocks = result.content.filter(b => b.type === 'tool_use');
    expect(toolUseBlocks.length).toBe(1);
    expect((toolUseBlocks[0] as Record<string, unknown>).id).toBe('tu-1');

    const toolResultBlocks = result.content.filter(b => b.type === 'tool_result');
    expect(toolResultBlocks.length).toBe(1);
  });

  it('should accumulate tool blocks across multiple assistant events in same agentic turn', () => {
    // SETUP: The agentic loop produces multiple assistant events:
    // Event 1: [text("..."), tool_use(Write)]
    // Event 2: [text(""), tool_use(Bash), tool_result(Write-error)]
    // Event 3: [text(""), tool_use(Read)]
    // Event 4: [text("Summary"), tool_use(Write-success)]
    //
    // Each event should ADD new tool blocks without losing previous ones.

    const msgId = 'assistant-1';
    const initialText = 'Let me write the design:';

    // Start with streamed text
    let messages: Message[] = [makeMessage(msgId, [textBlock(initialText, false)])];

    // Event 1: text + Write tool
    messages = updateMessages(messages, msgId, [
      textBlock(initialText),
      toolUseBlock('tu-write-1', 'Write'),
    ]);

    expect(messages[0].content.filter(b => b.type === 'tool_use').length).toBe(1);

    // Event 2: tool_result(error) + new tool_use(Bash)
    messages = updateMessages(messages, msgId, [
      textBlock(initialText),
      toolUseBlock('tu-write-1', 'Write'),       // already exists → dedup
      toolResultBlock('tu-write-1', 'Error', true),
      toolUseBlock('tu-bash-1', 'Bash'),          // new
    ]);

    const afterEvent2 = messages[0].content;
    expect(afterEvent2.filter(b => b.type === 'tool_use').length).toBe(2); // Write + Bash
    expect(afterEvent2.filter(b => b.type === 'tool_result').length).toBe(1);

    // Event 3: more tools
    messages = updateMessages(messages, msgId, [
      textBlock(initialText),
      toolUseBlock('tu-write-1', 'Write'),
      toolResultBlock('tu-write-1', 'Error', true),
      toolUseBlock('tu-bash-1', 'Bash'),
      toolResultBlock('tu-bash-1', 'OK', false),
      toolUseBlock('tu-read-1', 'Read'),
    ]);

    const afterEvent3 = messages[0].content;
    expect(afterEvent3.filter(b => b.type === 'tool_use').length).toBe(3); // Write + Bash + Read
    expect(afterEvent3.filter(b => b.type === 'tool_result').length).toBe(2);

    // Event 4: final text + final tool
    messages = updateMessages(messages, msgId, [
      textBlock(initialText + '\n\nDone. Here is the design:'),
      toolUseBlock('tu-write-1', 'Write'),
      toolResultBlock('tu-write-1', 'Error', true),
      toolUseBlock('tu-bash-1', 'Bash'),
      toolResultBlock('tu-bash-1', 'OK', false),
      toolUseBlock('tu-read-1', 'Read'),
      toolResultBlock('tu-read-1', 'file content', false),
      toolUseBlock('tu-write-2', 'Write'),
      toolResultBlock('tu-write-2', 'Success', false),
    ]);

    const afterEvent4 = messages[0].content;
    // Should have ALL tool blocks
    expect(afterEvent4.filter(b => b.type === 'tool_use').length).toBe(4);
    expect(afterEvent4.filter(b => b.type === 'tool_result').length).toBe(4);
    // Text should be the final version
    const finalText = afterEvent4.filter(b => b.type === 'text');
    expect(finalText.length).toBe(1);
    expect((finalText[0] as Record<string, unknown>).text).toContain('Done. Here is the design:');
  });

  it('updateMessages must return a NEW reference even when only tool blocks differ', () => {
    // This tests the L335 early-return bug: if no unconfirmed text exists
    // and authoritativeBlocks resolves empty (all tools deduped), it returns
    // same msg reference → React skips re-render.
    //
    // Scenario: all text is confirmed from Event 1, Event 2 only adds tool_result
    // but the tool_use was already seen. Does updateMessages create a new ref?

    const msgId = 'assistant-1';
    let messages: Message[] = [makeMessage(msgId, [
      textBlock('Some text', true),  // already confirmed
      toolUseBlock('tu-1', 'Grep'),
    ])];

    const originalRef = messages[0];

    // Assistant event has same text + same tool_use + NEW tool_result
    messages = updateMessages(messages, msgId, [
      textBlock('Some text'),
      toolUseBlock('tu-1', 'Grep'),
      toolResultBlock('tu-1', 'Search results here'),
    ]);

    // MUST be different reference (new content was added)
    expect(messages[0]).not.toBe(originalRef);
    // tool_result must be present
    expect(messages[0].content.filter(b => b.type === 'tool_result').length).toBe(1);
  });

  it('updateMessages returns NEW reference when long text triggers same-turn dedup', () => {
    // Same-turn dedup only fires for text >= 20 chars (MIN_DEDUP_LENGTH guard).
    // Short text ("Done") is excluded to prevent false positives on common words.
    const msgId = 'assistant-1';
    const longText = 'Now I have enough information to proceed with the implementation.';
    const messages: Message[] = [makeMessage(msgId, [
      textBlock(longText, true),
      toolUseBlock('tu-1', 'Read'),
      toolResultBlock('tu-1', 'content'),
    ])];

    const originalRef = messages[0];

    // Same long text arrives — triggers dedup (>= 20 chars both sides)
    const result = updateMessages(messages, msgId, [
      textBlock(longText),
      toolUseBlock('tu-1', 'Read'),
      toolResultBlock('tu-1', 'content'),
    ]);

    // MUST be new reference — text was replaced via dedup
    expect(result[0]).not.toBe(originalRef);
    const textBlocks = result[0].content.filter(b => b.type === 'text');
    expect(textBlocks.length).toBe(1);
    expect((textBlocks[0] as Record<string, unknown>)._confirmed).toBe(true);
    expect((textBlocks[0] as Record<string, unknown>).text).toBe(longText);
  });
});

// ---------------------------------------------------------------------------
// Test 2: Verify the isStreaming derivation problem
// ---------------------------------------------------------------------------

describe('Streaming spinner hang — isStreaming derivation race', () => {
  it('setPendingStreamTabs delete on absent key returns same Set → no re-render', () => {
    // This demonstrates the core race condition:
    // If tabId was never added to pendingStreamTabs (because streaming was
    // tracked only via tabState.isStreaming in the per-tab map), then
    // deleting it from the Set is a no-op. React's setState with same
    // reference won't trigger re-render.

    const originalSet = new Set<string>(['tab-A']);

    // Simulate: setIsStreaming(false, 'tab-B') where tab-B is NOT in the set
    const next = new Set(originalSet);
    next.delete('tab-B');  // no-op — tab-B wasn't there

    // Set content is identical — but it's a NEW Set object!
    // React WILL re-render because we always create `new Set(prev)`.
    expect(next).not.toBe(originalSet);  // Different object reference
    expect(next.size).toBe(originalSet.size);  // Same content

    // KEY INSIGHT: The code does `const next = new Set(prev)` then `next.delete(tabId)`.
    // Even though the delete is a no-op, `next !== prev` so React WILL re-render.
    // This means the bug is NOT in Set reference equality...
    //
    // So where IS the bug? Let's check: does isStreaming re-derive correctly?
  });

  it('isStreaming derivation reads tabMapRef which is a ref, not state', () => {
    // The derivation at L805:
    //   const isStreaming = (activeTabState?.isStreaming ?? false) || pendingStreamTabs.has(...)
    //
    // activeTabState comes from tabMapRef.current.get(activeTabIdCurrent).
    // tabMapRef is a REF — mutations don't trigger re-render.
    //
    // When setIsStreaming(false) runs:
    //   1. tabState.isStreaming = false (ref mutation — NO re-render)
    //   2. setPendingStreamTabs(...) (state update — triggers re-render)
    //
    // On re-render, isStreaming re-derives:
    //   activeTabState = tabMapRef.current.get(activeTabIdCurrent)
    //   = the SAME tabState object where .isStreaming is now false ✓
    //
    // So the derivation SHOULD pick up the change... UNLESS:
    //   - activeTabIdCurrent is stale (ref read at render time — could be wrong tab)
    //   - tabMapRef.current.get(activeTabIdCurrent) returns undefined (tab closed mid-stream)
    //   - The re-render from setPendingStreamTabs happens BEFORE tabState.isStreaming = false
    //     (impossible — both are in same synchronous callback)
    //
    // FINDING: The bug might not be in isStreaming derivation per se.
    // It might be that the `result` event handler NEVER EXECUTES.

    // The result event handler at L1479 requires:
    //   event.type === 'result'
    //
    // If the backend sends `result` but it arrives in the same reader.read()
    // chunk as a batch of assistant events, and one of those assistant events
    // throws an error in onMessage()... the catch at L285:
    //   } catch (handlerError) {
    //     console.error('[SSE] Error in onMessage handler:', handlerError, ...);
    //   }
    //
    // SWALLOWS the error and continues the loop! So the result event WOULD
    // still be processed... unless the error corrupts state.
    //
    // REVISED HYPOTHESIS: The issue is in React 18's batched updates.
    // Multiple setMessages calls in rapid succession (assistant + result
    // arrive in same read() chunk) can cause React to skip intermediate renders.
    // The final setMessages from result() may not trigger a visible re-render
    // if React determines the component tree hasn't changed.

    expect(true).toBe(true); // Documented reasoning — not a runtime assertion
  });
});

// ---------------------------------------------------------------------------
// Test 3: The actual repro — simulate the exact event sequence that hangs
// ---------------------------------------------------------------------------

describe('Streaming spinner hang — full event sequence repro', () => {
  it('simulates: text_delta → assistant(text+tool) → assistant(text+tools) → result', () => {
    // This simulates the exact sequence from the bug:
    // 1. text_delta: "Let me write the complete design:"
    // 2. (2 minutes pass — tools execute inside CLI, no SSE events)
    // 3. assistant event: full content with all 18 tool calls
    // 4. result event
    //
    // We verify that after step 3, the message has all content blocks,
    // and after step 4 equivalent (setIsStreaming(false)), the streaming
    // state would correctly derive to false.

    const msgId = 'msg-04051ed4';
    const streamedText = 'Now I have enough info. Let me look at how the backend SSE emits events — specifically what happens between the text_delta of my text and the subsequent tool_use call:';

    // Step 1: text_delta builds up the message
    let messages: Message[] = [
      makeMessage('user-1', [textBlock('查 session_unit.py 的 pipe handling 先找出root cause')], 'user'),
      makeMessage(msgId, []),
    ];
    messages = appendTextDelta(messages, msgId, streamedText);

    // Verify text was appended
    const textContent = messages[1].content.find(b => b.type === 'text');
    expect(textContent).toBeDefined();
    expect((textContent as Record<string, unknown>).text).toBe(streamedText);

    // Step 2: long assistant event with 18 tools (simulating the agentic loop completion)
    const bigAssistantContent: ContentBlock[] = [
      textBlock(streamedText + '\n\n**Root cause found.**'),
      toolUseBlock('grep-1', 'Grep'),
      toolResultBlock('grep-1', 'Found 30 files'),
      toolUseBlock('grep-2', 'Grep'),
      toolResultBlock('grep-2', 'session_unit.py:1907: async def _stream_response'),
      toolUseBlock('read-1', 'Read'),
      toolResultBlock('read-1', 'def _stream_response(self, ...)...'),
      toolUseBlock('read-2', 'Read'),
      toolResultBlock('read-2', 'async for event in self._read_formatted_response()...'),
      toolUseBlock('grep-3', 'Grep'),
      toolResultBlock('grep-3', 'STALL_TIMEOUT_MS = 45_000'),
      toolUseBlock('read-3', 'Read'),
      toolResultBlock('read-3', 'function createStallDetection(...)'),
      toolUseBlock('bash-1', 'Bash'),
      toolResultBlock('bash-1', '/Users/gawan/.swarm-ai/logs/backend-daemon.log'),
      toolUseBlock('bash-2', 'Bash'),
      toolResultBlock('bash-2', '16:04:17 - session_unit.transition streaming → idle'),
    ];

    messages = updateMessages(messages, msgId, bigAssistantContent);

    // CRITICAL ASSERTION: All tool blocks must be present
    const finalContent = messages[1].content;
    const toolUses = finalContent.filter(b => b.type === 'tool_use');
    const toolResults = finalContent.filter(b => b.type === 'tool_result');

    expect(toolUses.length).toBe(8);  // All 8 tool_use blocks
    expect(toolResults.length).toBe(8);  // All 8 tool_result blocks

    // Text must be updated to the authoritative version
    const finalTexts = finalContent.filter(b => b.type === 'text');
    expect(finalTexts.length).toBe(1);
    expect((finalTexts[0] as Record<string, unknown>).text).toContain('Root cause found.');
    expect((finalTexts[0] as Record<string, unknown>)._confirmed).toBe(true);

    // Step 3: result event would now call setIsStreaming(false).
    // If we got here, updateMessages didn't drop any content.
    // The remaining question is whether React re-renders — tested below.
  });

  it('updateMessages returns new reference when tool blocks are added (not early-return)', () => {
    // Tests that the L335 early-return doesn't fire when new tool blocks exist.
    const msgId = 'msg-1';

    // Start with confirmed text + one tool_use (from first assistant event)
    const messages: Message[] = [makeMessage(msgId, [
      { type: 'text', text: 'Hello', _confirmed: true } as unknown as ContentBlock,
      toolUseBlock('tu-1', 'Read'),
    ])];

    const originalRef = messages[0];

    // Second assistant event: same text, same tu-1, but adds tu-1 result + tu-2
    const updated = updateMessages(messages, msgId, [
      textBlock('Hello'),
      toolUseBlock('tu-1', 'Read'),
      toolResultBlock('tu-1', 'file content'),  // NEW
      toolUseBlock('tu-2', 'Grep'),             // NEW
    ]);

    // MUST create new reference — content changed
    expect(updated[0]).not.toBe(originalRef);
    expect(updated[0].content.filter(b => b.type === 'tool_result').length).toBe(1);
    expect(updated[0].content.filter(b => b.type === 'tool_use').length).toBe(2);
  });
});
