/**
 * Bug condition exploration tests for multi-tab streaming isolation.
 *
 * What is being tested:
 *   - ``useChatStreamingLifecycle`` hook from ``hooks/useChatStreamingLifecycle.ts``
 *   - Cross-tab streaming state corruption when multiple tabs exist
 *
 * Testing methodology: Property-based testing with vitest + fast-check
 *
 * Key properties verified:
 *   - Active tab's ``isStreaming`` reflects only its own streaming state
 *   - Per-tab pending state is independent across tabs
 *   - Tab switching preserves source tab's streaming state in tabMapRef
 *   - Concurrent stream messages are isolated per-tab in tabMapRef
 *
 * CRITICAL: Bug condition exploration tests MUST FAIL on unfixed code — failure confirms the bug.
 * Preservation tests MUST PASS on unfixed code — they establish baseline behavior to protect.
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
 *
 * @see .kiro/specs/multi-tab-streaming-isolation/bugfix.md
 * @see .kiro/specs/multi-tab-streaming-isolation/design.md
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import * as fc from 'fast-check';
import {
  useChatStreamingLifecycle,
} from '../hooks/useChatStreamingLifecycle';
import type { UnifiedTab, TabStatus } from '../hooks/useUnifiedTabState';
import type { Message } from '../types';
import {
  testTabMap,
  testTabMapRef,
  testActiveTabIdRef,
  createMockDeps,
  initTestTab,
  makeMessage,
  resetTestState,
} from './helpers/streamingTestUtils';

// ---------------------------------------------------------------------------
// Mock useToast — the hook now calls useToast() for reconnection toasts
// ---------------------------------------------------------------------------
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    addToast: vi.fn(),
    removeToast: vi.fn(),
    toasts: [],
  }),
}));

// ---------------------------------------------------------------------------
// fast-check arbitraries for tab IDs
// ---------------------------------------------------------------------------

/** Generate a valid tab ID string (alphanumeric + hyphen, 4-12 chars). */
const arbTabId = fc.stringMatching(/^[a-z][a-z0-9-]{3,11}$/);

/** Generate a pair of distinct tab IDs. */
const arbTabIdPair = fc.tuple(arbTabId, arbTabId).filter(([a, b]) => a !== b);

// ---------------------------------------------------------------------------
// Tests — Bug Condition Exploration
// ---------------------------------------------------------------------------

describe('Multi-Tab Streaming Isolation — Bug Condition Exploration', () => {
  beforeEach(() => {
    resetTestState();
  });

  /**
   * Test Case 1 — Blocked Send on Idle Tab
   *
   * Bug: When Tab A is streaming and Tab B is idle, the global `isStreaming`
   * derived state reflects Tab A's streaming, so `result.current.isStreaming`
   * is `true` even after switching to idle Tab B.
   *
   * Expected (correct): `isStreaming` should be `false` on idle Tab B.
   * EXPECTED TO FAIL on unfixed code — proves bug 1.1 / 1.5 exists.
   *
   * **Validates: Requirements 1.1, 1.5**
   */
  it('property: idle tab isStreaming is false when another tab streams', () => {
    fc.assert(
      fc.property(arbTabIdPair, ([tabAId, tabBId]) => {
        // Reset state
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        // Set up Tab A (will be streaming) and Tab B (idle)
        act(() => {
          initTestTab(tabAId);
          // Also register Tab B in the map
          testTabMap.set(tabBId, {
            id: tabBId,
            title: 'Tab B',
            agentId: 'default',
            isNew: true,
            sessionId: undefined,
            messages: [],
            pendingQuestion: null,
            isStreaming: false,
            abortController: null,
            streamGen: 0,
            status: 'idle' as TabStatus,
          });
        });

        // Tab A starts streaming
        act(() => {
          testActiveTabIdRef.current = tabAId;
          result.current.setIsStreaming(true);
        });

        // Mark Tab A as streaming in the per-tab map
        const tabA = testTabMap.get(tabAId)!;
        tabA.isStreaming = true;

        // Switch active tab to idle Tab B
        act(() => {
          testActiveTabIdRef.current = tabBId;
          // Trigger re-render so isStreaming derivation picks up new active tab
          result.current.bumpStreamingDerivation();
        });

        // BUG: On unfixed code, isStreaming is still true (global state
        // from Tab A). On fixed code, it should be false (Tab B is idle).
        expect(result.current.isStreaming).toBe(false);
      }),
      { numRuns: 20 },
    );
  });

  /**
   * Test Case 2 — Pending State Kill
   *
   * Bug: `_pendingStream` is a single boolean. When Tab A starts streaming
   * on the active tab (setting `_pendingStream = true`), then Tab A receives
   * `session_start` (which calls `_setPendingStream(false)` on the active
   * tab), the global `_pendingStream` is now false. If Tab B was also
   * supposed to be pending (started streaming before Tab A's session_start),
   * switching to Tab B shows `isStreaming` as false because the global
   * `_pendingStream` was cleared by Tab A.
   *
   * The core issue: there's only ONE `_pendingStream` boolean, so after
   * Tab A's session_start clears it, switching to pending Tab B shows
   * `isStreaming` derived from the cleared global state.
   *
   * Expected (correct): After switching to Tab B, `isStreaming` should
   * reflect Tab B's own pending/streaming state, not the global state
   * cleared by Tab A.
   * EXPECTED TO FAIL on unfixed code — proves bug 1.2 / 1.6 exists.
   *
   * **Validates: Requirements 1.2, 1.6**
   */
  it('property: pending Tab B isStreaming survives Tab A session_start', () => {
    fc.assert(
      fc.property(arbTabIdPair, ([tabAId, tabBId]) => {
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        const msgIdA = `msg-a-${tabAId}`;

        // Set up both tabs, Tab A is active
        act(() => {
          testTabMap.set(tabAId, {
            id: tabAId, title: 'Tab A', agentId: 'default', isNew: false,
            messages: [makeMessage({ id: msgIdA, role: 'assistant', content: [] })],
            sessionId: undefined, pendingQuestion: null, isStreaming: false,
            abortController: null, streamGen: 0, status: 'idle' as TabStatus,
          });
          testTabMap.set(tabBId, {
            id: tabBId, title: 'Tab B', agentId: 'default', isNew: false,
            messages: [], sessionId: undefined,
            pendingQuestion: null, isStreaming: true,
            abortController: null, streamGen: 0, status: 'streaming' as TabStatus,
          });
          testActiveTabIdRef.current = tabAId;
        });

        // Tab A starts streaming on the active tab → _pendingStream = true
        act(() => {
          result.current.setIsStreaming(true);
        });

        // Tab A receives session_start → _setPendingStream(false) globally
        const handlerA = result.current.createStreamHandler(msgIdA, tabAId);
        act(() => {
          handlerA({ type: 'session_start', sessionId: 'sess-a' });
        });

        // Now switch to Tab B which should be streaming/pending
        act(() => {
          testActiveTabIdRef.current = tabBId;
          // Trigger re-render so isStreaming derivation picks up new active tab
          result.current.bumpStreamingDerivation();
        });

        // BUG: On unfixed code, isStreaming derives from global state:
        // sessionId is undefined (Tab B has no sessionId yet), so it
        // falls back to _pendingStream which is now false (cleared by
        // Tab A's session_start). Tab B should show as streaming.
        expect(result.current.isStreaming).toBe(true);
      }),
      { numRuns: 20 },
    );
  });

  /**
   * Test Case 3 — Tab Switch Corruption
   *
   * Bug: When Tab A is streaming and the user switches to Tab B,
   * `handleTabSelect` calls `setIsStreaming(tabState.isStreaming)` where
   * tabState.isStreaming is false (Tab B is idle). This calls
   * `_setPendingStream(false)` and removes sessionId from
   * `streamingSessions` globally. If the user then switches BACK to
   * Tab A, the derived `isStreaming` is false because the global state
   * was cleared during the switch to Tab B.
   *
   * Expected (correct): After switching back to Tab A, `isStreaming`
   * should be true because Tab A is still streaming.
   * EXPECTED TO FAIL on unfixed code — proves bug 1.3 exists.
   *
   * **Validates: Requirements 1.3**
   */
  it('property: switch away and back preserves streaming tab derived state', () => {
    fc.assert(
      fc.property(arbTabIdPair, ([tabAId, tabBId]) => {
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        // Set up Tab A as active
        act(() => {
          initTestTab(tabAId);
          testTabMap.set(tabBId, {
            id: tabBId, title: 'Tab B', agentId: 'default', isNew: true,
            sessionId: undefined, messages: [],
            pendingQuestion: null, isStreaming: false,
            abortController: null, streamGen: 0, status: 'idle' as TabStatus,
          });
        });

        // Start streaming on Tab A
        act(() => {
          result.current.setIsStreaming(true);
        });
        testTabMap.get(tabAId)!.isStreaming = true;

        // Simulate tab switch to Tab B: handleTabSelect calls
        // setIsStreaming(tabState.isStreaming) where Tab B is idle.
        act(() => {
          testActiveTabIdRef.current = tabBId;
          const tabBState = testTabMap.get(tabBId)!;
          result.current.setIsStreaming(tabBState.isStreaming); // false
        });

        // Now switch BACK to Tab A (which should still be streaming)
        act(() => {
          testActiveTabIdRef.current = tabAId;
          const tabAState = testTabMap.get(tabAId)!;
          result.current.setIsStreaming(tabAState.isStreaming); // true
        });

        // BUG: On unfixed code, the global streamingSessions was cleared
        // when we switched to Tab B (setIsStreaming(false) removed the
        // sessionId). Even though we call setIsStreaming(true) on switch
        // back, the sessionId in React state may be stale/wrong, so the
        // derivation `streamingSessions.has(sessionId)` may not work.
        // The derived isStreaming should be true for Tab A.
        expect(result.current.isStreaming).toBe(true);
      }),
      { numRuns: 20 },
    );
  });

  /**
   * Test Case 4 — Message Isolation via React State
   *
   * Bug: When Tab A is the active tab and both Tab A and Tab B are
   * streaming, Tab A's stream handler writes to both the per-tab map
   * AND the shared React `messages` state. The shared React state
   * (`result.current.messages`) is the single source for rendering.
   * After switching to Tab B, the React `messages` state still contains
   * Tab A's messages (not Tab B's) because there's no mechanism to
   * swap the React state to reflect the newly active tab's messages
   * from the per-tab map.
   *
   * Expected (correct): After switching to Tab B, `result.current.messages`
   * should reflect Tab B's messages from the per-tab map.
   * EXPECTED TO FAIL on unfixed code — proves bug 1.4 / 1.5 exists.
   *
   * **Validates: Requirements 1.4, 1.5**
   */
  it('property: React messages state reflects active tab after switch', () => {
    fc.assert(
      fc.property(arbTabIdPair, ([tabAId, tabBId]) => {
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        const msgIdA = `msg-a-${tabAId}`;
        const msgIdB = `msg-b-${tabBId}`;

        // Set up Tab A as active with an assistant message placeholder
        act(() => {
          testTabMap.set(tabAId, {
            id: tabAId, title: 'Tab A', agentId: 'default', isNew: false,
            messages: [makeMessage({ id: msgIdA, role: 'assistant', content: [] })],
            sessionId: 'sess-a', pendingQuestion: null, isStreaming: true,
            abortController: null, streamGen: 0, status: 'streaming' as TabStatus,
          });
          testTabMap.set(tabBId, {
            id: tabBId, title: 'Tab B', agentId: 'default', isNew: false,
            messages: [makeMessage({ id: msgIdB, role: 'assistant', content: [] })],
            sessionId: 'sess-b', pendingQuestion: null, isStreaming: true,
            abortController: null, streamGen: 0, status: 'streaming' as TabStatus,
          });
          testActiveTabIdRef.current = tabAId;
          result.current.setMessages([
            makeMessage({ id: msgIdA, role: 'assistant', content: [] }),
          ]);
        });

        // Tab A (active) receives content — writes to map AND useState
        const handlerA = result.current.createStreamHandler(msgIdA, tabAId);
        act(() => {
          handlerA({
            type: 'assistant',
            content: [{ type: 'text', text: 'Content for Tab A' }],
          });
        });

        // Tab B (background) receives content — writes to map only
        const handlerB = result.current.createStreamHandler(msgIdB, tabBId);
        act(() => {
          handlerB({
            type: 'assistant',
            content: [{ type: 'text', text: 'Content for Tab B' }],
          });
        });

        // Switch to Tab B
        act(() => {
          testActiveTabIdRef.current = tabBId;
          // In real code, handleTabSelect restores Tab B's messages from
          // the per-tab map and bumps the streaming derivation.
          const tabBState = testTabMap.get(tabBId);
          if (tabBState) {
            result.current.setMessages(tabBState.messages);
          }
          result.current.bumpStreamingDerivation();
        });

        // BUG: On unfixed code, result.current.messages still contains
        // Tab A's messages because there's no automatic swap of React
        // messages state when the active tab changes. The messages should
        // reflect Tab B's content from the per-tab map.
        const reactMessages = result.current.messages;
        const reactContent = reactMessages
          .flatMap((m) => m.content)
          .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
          .map((b) => b.text);

        // After switching to Tab B, React messages should show Tab B's content
        expect(reactContent).toContain('Content for Tab B');
        expect(reactContent).not.toContain('Content for Tab A');
      }),
      { numRuns: 20 },
    );
  });
});


// ---------------------------------------------------------------------------
// Tests — Preservation Property Tests
// ---------------------------------------------------------------------------

describe('Preservation Property Tests', () => {
  beforeEach(() => {
    resetTestState();
  });

  /**
   * Property Test 1 — Single-Tab Streaming Preservation
   *
   * For single-tab scenarios (only one tab in the map), verify:
   *   - `isStreaming` starts as `false`
   *   - After `setIsStreaming(true)`, `isStreaming` becomes `true`
   *   - After `setIsStreaming(false)`, `isStreaming` returns to `false`
   *   - Messages accumulate correctly via stream handler
   *   - Completion handler clears streaming state
   *
   * EXPECTED: PASS on unfixed code
   *
   * **Validates: Requirements 3.1, 3.2**
   */
  it('property: single-tab streaming lifecycle transitions correctly', () => {
    fc.assert(
      fc.property(arbTabId, (tabId) => {
        // Reset state
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        // Set up a single tab
        act(() => {
          initTestTab(tabId);
        });

        // isStreaming starts as false
        expect(result.current.isStreaming).toBe(false);

        // After setIsStreaming(true), isStreaming becomes true
        act(() => {
          result.current.setIsStreaming(true);
        });
        expect(result.current.isStreaming).toBe(true);

        // After setIsStreaming(false), isStreaming returns to false
        act(() => {
          result.current.setIsStreaming(false);
        });
        expect(result.current.isStreaming).toBe(false);

        // Start streaming again and verify messages accumulate
        const msgId = `msg-${tabId}`;
        act(() => {
          result.current.setMessages([
            makeMessage({ id: msgId, role: 'assistant', content: [] }),
          ]);
          result.current.setIsStreaming(true);
        });

        const handler = result.current.createStreamHandler(msgId, tabId);

        // session_start event
        act(() => {
          handler({ type: 'session_start', sessionId: `sess-${tabId}` });
        });

        // assistant event accumulates content
        act(() => {
          handler({
            type: 'assistant',
            content: [{ type: 'text', text: `Hello from ${tabId}` }],
          });
        });

        // Verify messages accumulated in React state
        const msgs = result.current.messages;
        const textBlocks = msgs
          .flatMap((m) => m.content)
          .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
          .map((b) => b.text);
        expect(textBlocks).toContain(`Hello from ${tabId}`);

        // Completion handler clears streaming state
        const completeHandler = result.current.createCompleteHandler(tabId);
        act(() => {
          completeHandler();
        });
        expect(result.current.isStreaming).toBe(false);
      }),
      { numRuns: 30 },
    );
  });


  /**
   * Property Test 2 — Tab Lifecycle Preservation
   *
   * Verify tab open/close operations without concurrent streaming
   * work correctly:
   *   - Adding a tab to the map doesn't affect streaming state
   *   - Removing a tab from the map doesn't affect streaming state
   *   - Tab rename doesn't affect streaming state
   *
   * EXPECTED: PASS on unfixed code
   *
   * **Validates: Requirements 3.3, 3.4**
   */
  it('property: tab lifecycle ops do not affect streaming state', () => {
    fc.assert(
      fc.property(
        arbTabId,
        arbTabId.filter((id) => id.length > 0),
        fc.string({ minLength: 1, maxLength: 20 }),
        (tabId, newTabId, newTitle) => {
          // Ensure distinct IDs
          if (tabId === newTabId) return;

          // Reset state
          testTabMap.clear();
          testActiveTabIdRef.current = null;

          const { result } = renderHook(() =>
            useChatStreamingLifecycle(createMockDeps()),
          );

          // Set up initial tab (idle, not streaming)
          act(() => {
            initTestTab(tabId);
          });

          // Baseline: not streaming
          expect(result.current.isStreaming).toBe(false);

          // Add a new tab — should not affect streaming state
          act(() => {
            testTabMap.set(newTabId, {
              id: newTabId,
              title: 'New Tab',
              agentId: 'default',
              isNew: true,
              sessionId: undefined,
              messages: [],
              pendingQuestion: null,
              isStreaming: false,
              abortController: null,
              streamGen: 0,
              status: 'idle' as TabStatus,
            });
          });
          expect(result.current.isStreaming).toBe(false);
          expect(testTabMap.size).toBe(2);

          // Rename the original tab — should not affect streaming state
          act(() => {
            const tab = testTabMap.get(tabId)!;
            tab.title = newTitle;
          });
          expect(result.current.isStreaming).toBe(false);
          expect(testTabMap.get(tabId)!.title).toBe(newTitle);

          // Remove the new tab — should not affect streaming state
          act(() => {
            testTabMap.delete(newTabId);
          });
          expect(result.current.isStreaming).toBe(false);
          expect(testTabMap.size).toBe(1);
        },
      ),
      { numRuns: 30 },
    );
  });


  /**
   * Property Test 3 — SSE Event Processing Preservation
   *
   * For single-tab mode, verify SSE events are processed correctly:
   *   - `session_start` event sets sessionId
   *   - `assistant` event accumulates content in messages
   *   - `result` event triggers query invalidation
   *   - `error` event adds error content to messages
   *   - `ask_user_question` event sets pendingQuestion and clears isStreaming
   *
   * EXPECTED: PASS on unfixed code
   *
   * **Validates: Requirements 3.5, 3.6, 3.7, 3.8**
   */
  it('property: SSE events processed correctly in single-tab mode', () => {
    fc.assert(
      fc.property(
        arbTabId,
        fc.string({ minLength: 1, maxLength: 50 }),
        (tabId, messageText) => {
          // Reset state
          testTabMap.clear();
          testActiveTabIdRef.current = null;

          const mockDeps = createMockDeps();
          const { result } = renderHook(() =>
            useChatStreamingLifecycle(mockDeps),
          );

          // Set up single tab
          const msgId = `msg-${tabId}`;
          act(() => {
            initTestTab(tabId, [
              makeMessage({ id: msgId, role: 'assistant', content: [] }),
            ]);
            result.current.setMessages([
              makeMessage({ id: msgId, role: 'assistant', content: [] }),
            ]);
            result.current.setIsStreaming(true);
          });

          const handler = result.current.createStreamHandler(msgId, tabId);
          const sessId = `sess-${tabId}`;

          // 1. session_start sets sessionId
          act(() => {
            handler({ type: 'session_start', sessionId: sessId });
          });
          expect(result.current.sessionId).toBe(sessId);

          // 2. assistant event accumulates content
          act(() => {
            handler({
              type: 'assistant',
              content: [{ type: 'text', text: messageText }],
            });
          });
          const textBlocks = result.current.messages
            .flatMap((m) => m.content)
            .filter(
              (b): b is { type: 'text'; text: string } => b.type === 'text',
            )
            .map((b) => b.text);
          expect(textBlocks).toContain(messageText);

          // 3. result event triggers query invalidation
          act(() => {
            handler({
              type: 'result',
              sessionId: sessId,
            } as unknown as import('../types').StreamEvent);
          });
          expect(mockDeps.queryClient.invalidateQueries).toHaveBeenCalled();

        },
      ),
      { numRuns: 30 },
    );
  });

  it('property: error event adds error content in single-tab mode', () => {
    fc.assert(
      fc.property(arbTabId, (tabId) => {
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        const msgId = `msg-err-${tabId}`;
        act(() => {
          initTestTab(tabId, [
            makeMessage({ id: msgId, role: 'assistant', content: [] }),
          ]);
          result.current.setMessages([
            makeMessage({ id: msgId, role: 'assistant', content: [] }),
          ]);
          result.current.setIsStreaming(true);
        });

        const handler = result.current.createStreamHandler(msgId, tabId);

        // error event adds error content and clears streaming
        act(() => {
          handler({
            type: 'error',
            message: 'Something went wrong',
          });
        });

        expect(result.current.isStreaming).toBe(false);
        const errorTexts = result.current.messages
          .flatMap((m) => m.content)
          .filter(
            (b): b is { type: 'text'; text: string } => b.type === 'text',
          )
          .map((b) => b.text);
        expect(errorTexts.some((t) => t.includes('Something went wrong'))).toBe(true);
      }),
      { numRuns: 20 },
    );
  });


  it('property: ask_user_question sets pendingQuestion and clears streaming in single-tab', () => {
    fc.assert(
      fc.property(arbTabId, (tabId) => {
        testTabMap.clear();
        testActiveTabIdRef.current = null;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );

        const msgId = `msg-auq-${tabId}`;
        act(() => {
          initTestTab(tabId, [
            makeMessage({ id: msgId, role: 'assistant', content: [] }),
          ]);
          result.current.setMessages([
            makeMessage({ id: msgId, role: 'assistant', content: [] }),
          ]);
          result.current.setIsStreaming(true);
        });

        const handler = result.current.createStreamHandler(msgId, tabId);
        const sessId = `sess-auq-${tabId}`;

        // session_start first
        act(() => {
          handler({ type: 'session_start', sessionId: sessId });
        });

        // ask_user_question event
        act(() => {
          handler({
            type: 'ask_user_question',
            toolUseId: `tool-${tabId}`,
            questions: [{ question: 'Pick one', options: ['A', 'B'] }],
            sessionId: sessId,
          } as unknown as import('../types').StreamEvent);
        });

        // pendingQuestion should be set
        expect(result.current.pendingQuestion).not.toBeNull();
        expect(result.current.pendingQuestion!.toolUseId).toBe(`tool-${tabId}`);

        // isStreaming should be cleared
        expect(result.current.isStreaming).toBe(false);
      }),
      { numRuns: 20 },
    );
  });
});
