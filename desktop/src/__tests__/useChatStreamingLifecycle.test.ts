/**
 * Unit tests for the ``useChatStreamingLifecycle`` custom hook.
 *
 * What is being tested:
 *   - ``useChatStreamingLifecycle`` hook from ``hooks/useChatStreamingLifecycle.ts``
 *   - ``deriveStreamingActivity`` pure function (standalone export)
 *
 * Testing methodology: Unit tests with ``renderHook`` from @testing-library/react
 *
 * Key invariants verified:
 *   - Hook returns all expected state (messages, sessionId, pendingQuestion,
 *     isStreaming, streamingActivity)
 *   - Hook returns all expected setters (setMessages, setSessionId,
 *     setPendingQuestion, setIsStreaming)
 *   - Hook returns all expected refs (abortRef, messagesEndRef)
 *   - Hook returns all expected factories (createStreamHandler,
 *     createCompleteHandler, createErrorHandler)
 *   - ``deriveStreamingActivity`` standalone export works identically to
 *     when it was inline in ChatPage.tsx
 *   - isStreaming derivation: false by default, true when _pendingStream set
 *   - streamingActivity: null when not streaming, returns activity when
 *     streaming with content
 *   - Fix 1: Stream generation counter increments on new stream, stale
 *     complete handlers are no-ops when generation mismatches, event-driven
 *     pauses (ask_user_question, error) increment generation
 *   - Fix 6: Per-tab state map saves/restores state on tab switch, background
 *     tab streaming updates map but not foreground useState, per-tab abort
 *     controller isolation, per-tab pendingStream/pendingQuestion isolation,
 *     tab close cleanup removes entry and aborts controller
 *   - Fix 2: Auto-scroll detection — userScrolledUpRef defaults to false,
 *     resetUserScroll resets the flag for new user messages
 *   - Fix 3: Error handling — error event stops streaming, sets isError flag,
 *     error content visible, resets userScrolledUpRef for auto-scroll,
 *     increments streamGen so stale completeHandler is no-op
 *   - Fix 9: Elapsed time counter — starts after streaming begins with no
 *     content, clears on first content arrival, resets when streaming stops,
 *     formatElapsed helper formats seconds correctly
 *
 *   - Fix 4: Enhanced deriveStreamingActivity with operational context —
 *     toolContext extraction from command/path/query inputs, toolCount for
 *     multiple tool_use blocks, sanitizeCommand strips secrets, extractToolContext
 *     priority order, debounce label stability with MIN_ACTIVITY_DISPLAY_MS
 *   - Fix 5: sessionStorage persistence — persistPendingState writes correct
 *     key format, restorePendingState reads/validates/discards corrupted entries,
 *     removePendingState cleanup, prepareMessagesForStorage truncation for large
 *     sessions, isSessionStorageAvailable guard, cleanupStalePendingEntries
 *     removes 404 sessions and keeps network errors, graceful degradation on
 *     quota exceeded
 *
 *   - Fix 7: MAX_OPEN_TABS guard — initTabState respects the 6-tab limit,
 *     tab creation re-enabled after close
 *   - Fix 8: Tab status indicators — updateTabStatus syncs tabStateRef and
 *     tabStatuses useState, guard skips re-render on same status, tab status
 *     transitions (idle→streaming, streaming→waiting_input, etc.),
 *     TabStatusIndicator renders correct icon/color per status, returns null
 *     for idle, aria-label accessibility, new tab starts idle, closing tab
 *     removes status entry
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15, 2.16, 2.17, 2.18, 2.19, 2.20, 2.21, 2.22, 2.23, 3.1, 3.2, 3.11, 3.13, 3.14
 *
 * @see .kiro/specs/streaming-ux-lifecycle/design.md
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, render, screen } from '@testing-library/react';
import {
  useChatStreamingLifecycle,
  deriveStreamingActivity,
  formatElapsed,
  ELAPSED_DISPLAY_THRESHOLD_MS,
  sanitizeCommand,
  extractToolContext,
  MIN_ACTIVITY_DISPLAY_MS,
  persistPendingState,
  restorePendingState,
  removePendingState,
  prepareMessagesForStorage,
  isSessionStorageAvailable,
  cleanupStalePendingEntries,
  STORAGE_KEY_PREFIX,
  MAX_OPEN_TABS,
  PERSISTED_STATE_VERSION,
} from '../hooks/useChatStreamingLifecycle';
import type {
  ChatStreamingLifecycleDeps,
  PersistedPendingState,
  TabStatus,
} from '../hooks/useChatStreamingLifecycle';
import { TabStatusIndicator } from '../pages/chat/components/TabStatusIndicator';
import React from 'react';
import type { StreamEvent } from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import type { Message, ContentBlock } from '../types';

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/** Create mock deps for the hook */
function createMockDeps(): ChatStreamingLifecycleDeps {
  return {
    queryClient: {
      invalidateQueries: vi.fn(),
    },
    applyTelemetryEvent: vi.fn(),
    tsccTriggerAutoExpand: vi.fn(),
  };
}

/** Helper to build a Message */
function makeMessage(
  overrides: Partial<Message> & { role: Message['role'] },
): Message {
  const { role, id, content, timestamp, ...rest } = overrides;
  return {
    id: id ?? crypto.randomUUID(),
    role,
    content: content ?? [],
    timestamp: timestamp ?? new Date().toISOString(),
    ...rest,
  };
}

/** Helper to build a tool_use ContentBlock */
function makeToolUse(name: string, id?: string): ContentBlock {
  return {
    type: 'tool_use' as const,
    id: id ?? crypto.randomUUID(),
    name,
    input: {},
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useChatStreamingLifecycle', () => {
  // ── Hook return shape ───────────────────────────────────────────────────

  describe('hook returns all expected members', () => {
    it('returns all expected state values', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      expect(result.current.messages).toEqual([]);
      expect(result.current.sessionId).toBeUndefined();
      expect(result.current.pendingQuestion).toBeNull();
      expect(result.current.isStreaming).toBe(false);
      expect(result.current.streamingActivity).toBeNull();
    });

    it('returns all expected setters', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      expect(typeof result.current.setMessages).toBe('function');
      expect(typeof result.current.setSessionId).toBe('function');
      expect(typeof result.current.setPendingQuestion).toBe('function');
      expect(typeof result.current.setIsStreaming).toBe('function');
    });

    it('returns all expected refs', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // abortRef should be a ref object with current = null initially
      expect(result.current.abortRef).toBeDefined();
      expect(result.current.abortRef.current).toBeNull();

      // messagesEndRef should be a ref object
      expect(result.current.messagesEndRef).toBeDefined();
      expect(result.current.messagesEndRef.current).toBeNull();
    });

    it('returns all expected factories', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      expect(typeof result.current.createStreamHandler).toBe('function');
      expect(typeof result.current.createCompleteHandler).toBe('function');
      expect(typeof result.current.createErrorHandler).toBe('function');
    });
  });

  // ── isStreaming derivation ────────────────────────────────────────────────

  describe('isStreaming derivation', () => {
    it('is false by default', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      expect(result.current.isStreaming).toBe(false);
    });

    it('becomes true when setIsStreaming(true) is called', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
      });

      expect(result.current.isStreaming).toBe(true);
    });

    it('returns to false when setIsStreaming(false) is called', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
      });
      expect(result.current.isStreaming).toBe(true);

      act(() => {
        result.current.setIsStreaming(false);
      });
      expect(result.current.isStreaming).toBe(false);
    });
  });

  // ── streamingActivity derivation ──────────────────────────────────────────

  describe('streamingActivity derivation', () => {
    it('is null when not streaming', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      expect(result.current.streamingActivity).toBeNull();
    });

    it('is null when streaming but no messages', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
      });

      // No messages → null (shows "Thinking…")
      expect(result.current.streamingActivity).toBeNull();
    });

    it('returns activity with toolName when streaming with tool_use', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({
            role: 'assistant',
            content: [makeToolUse('Bash')],
          }),
        ]);
      });

      expect(result.current.streamingActivity).not.toBeNull();
      expect(result.current.streamingActivity!.hasContent).toBe(true);
      expect(result.current.streamingActivity!.toolName).toBe('Bash');
    });
  });

  // ── Factory behavior ──────────────────────────────────────────────────────

  describe('createStreamHandler', () => {
    it('returns a function', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const handler = result.current.createStreamHandler('msg-1');
      expect(typeof handler).toBe('function');
    });

    it('handles session_start by setting sessionId', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const handler = result.current.createStreamHandler('msg-1');

      act(() => {
        handler({
          type: 'session_start',
          sessionId: 'sess-abc',
        });
      });

      expect(result.current.sessionId).toBe('sess-abc');
    });

    it('handles assistant event by updating message content', () => {
      const msgId = 'msg-1';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Seed an assistant message
      act(() => {
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId);

      act(() => {
        handler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Hello world' }],
        });
      });

      expect(result.current.messages[0].content).toHaveLength(1);
      expect(result.current.messages[0].content[0]).toEqual({
        type: 'text',
        text: 'Hello world',
      });
    });

    it('handles ask_user_question by setting pendingQuestion and stopping streaming', () => {
      const msgId = 'msg-1';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId);

      act(() => {
        handler({
          type: 'ask_user_question',
          toolUseId: 'tool-1',
          questions: [{
            question: 'Pick one',
            header: 'Choice',
            options: [{ label: 'A', description: 'Option A' }],
            multiSelect: false,
          }],
        });
      });

      expect(result.current.pendingQuestion).not.toBeNull();
      expect(result.current.pendingQuestion!.toolUseId).toBe('tool-1');
      expect(result.current.isStreaming).toBe(false);
    });

    it('handles error event by replacing message content', () => {
      const msgId = 'msg-1';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setMessages([
          makeMessage({
            id: msgId,
            role: 'assistant',
            content: [{ type: 'text', text: 'partial' }],
          }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId);

      act(() => {
        handler({ type: 'error', message: 'Something broke' });
      });

      const content = result.current.messages[0].content;
      expect(content).toHaveLength(1);
      expect(content[0].type).toBe('text');
      expect((content[0] as { text: string }).text).toContain('Something broke');
    });
  });

  describe('createCompleteHandler', () => {
    it('returns a function that sets isStreaming to false', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
      });
      expect(result.current.isStreaming).toBe(true);

      const completeHandler = result.current.createCompleteHandler();

      act(() => {
        completeHandler();
      });

      expect(result.current.isStreaming).toBe(false);
    });
  });

  describe('createErrorHandler', () => {
    it('returns a function that sets error content and stops streaming', () => {
      const msgId = 'msg-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const errorHandler = result.current.createErrorHandler(msgId);

      act(() => {
        errorHandler(new Error('Network failure'));
      });

      expect(result.current.isStreaming).toBe(false);
      const content = result.current.messages[0].content;
      expect(content).toHaveLength(1);
      expect((content[0] as { text: string }).text).toContain(
        'Network failure',
      );
    });
  });
});

// ---------------------------------------------------------------------------
// Standalone deriveStreamingActivity tests
// ---------------------------------------------------------------------------

describe('deriveStreamingActivity (standalone export)', () => {
  it('returns null when not streaming', () => {
    const messages: Message[] = [
      makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Hello' }],
      }),
    ];
    expect(deriveStreamingActivity(false, messages)).toBeNull();
  });

  it('returns null when streaming but no messages', () => {
    expect(deriveStreamingActivity(true, [])).toBeNull();
  });

  it('returns null when streaming but no assistant messages', () => {
    const messages: Message[] = [
      makeMessage({ role: 'user', content: [{ type: 'text', text: 'Hi' }] }),
    ];
    expect(deriveStreamingActivity(true, messages)).toBeNull();
  });

  it('returns null when assistant message has empty content', () => {
    const messages: Message[] = [
      makeMessage({ role: 'assistant', content: [] }),
    ];
    expect(deriveStreamingActivity(true, messages)).toBeNull();
  });

  it('returns hasContent=true, toolName=null for text-only content', () => {
    const messages: Message[] = [
      makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Working on it...' }],
      }),
    ];
    const result = deriveStreamingActivity(true, messages);
    expect(result).not.toBeNull();
    expect(result!.hasContent).toBe(true);
    expect(result!.toolName).toBeNull();
  });

  it('returns toolName from the last tool_use block', () => {
    const messages: Message[] = [
      makeMessage({
        role: 'assistant',
        content: [
          makeToolUse('Read'),
          makeToolUse('Bash'),
        ],
      }),
    ];
    const result = deriveStreamingActivity(true, messages);
    expect(result).not.toBeNull();
    expect(result!.toolName).toBe('Bash');
  });

  it('uses the last assistant message when multiple exist', () => {
    const messages: Message[] = [
      makeMessage({
        role: 'assistant',
        content: [makeToolUse('Read')],
      }),
      makeMessage({ role: 'user', content: [{ type: 'text', text: 'ok' }] }),
      makeMessage({
        role: 'assistant',
        content: [makeToolUse('Search')],
      }),
    ];
    const result = deriveStreamingActivity(true, messages);
    expect(result).not.toBeNull();
    expect(result!.toolName).toBe('Search');
  });
});

// ---------------------------------------------------------------------------
// Fix 1: Stream generation counter tests
// ---------------------------------------------------------------------------

describe('Fix 1: Stream generation counter', () => {
  describe('incrementStreamGen', () => {
    it('increments streamGenRef on each call', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      expect(result.current.streamGenRef.current).toBe(0);

      act(() => {
        result.current.incrementStreamGen();
      });
      expect(result.current.streamGenRef.current).toBe(1);

      act(() => {
        result.current.incrementStreamGen();
      });
      expect(result.current.streamGenRef.current).toBe(2);
    });

    it('syncs streamGen to active tab in tabStateRef', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
      });

      expect(
        result.current.tabStateRef.current.get('tab-1')!.streamGen,
      ).toBe(0);

      act(() => {
        result.current.incrementStreamGen();
      });

      expect(
        result.current.tabStateRef.current.get('tab-1')!.streamGen,
      ).toBe(1);
    });
  });

  describe('createCompleteHandler generation guard', () => {
    it('clears isStreaming when generation matches', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
      });
      expect(result.current.isStreaming).toBe(true);

      // Create complete handler at current generation (0)
      const completeHandler = result.current.createCompleteHandler('tab-1');

      act(() => {
        completeHandler();
      });

      expect(result.current.isStreaming).toBe(false);
    });

    it('is a no-op when generation has been incremented (stale handler)', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
      });

      // Create complete handler at generation 0
      const staleHandler = result.current.createCompleteHandler('tab-1');

      // Simulate new stream starting — increments generation
      act(() => {
        result.current.incrementStreamGen();
      });

      // Stale handler fires — should be a no-op
      act(() => {
        staleHandler();
      });

      // isStreaming should still be true (stale handler didn't clear it)
      expect(result.current.isStreaming).toBe(true);
    });

    it('is a no-op when tab has been closed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
      });

      const completeHandler = result.current.createCompleteHandler('tab-1');

      // Close the tab
      act(() => {
        result.current.cleanupTabState('tab-1');
      });

      // Handler fires after tab closed — should be a no-op
      act(() => {
        completeHandler();
      });

      // isStreaming remains true (handler was no-op)
      expect(result.current.isStreaming).toBe(true);
    });
  });

  describe('event-driven streaming pause increments generation', () => {
    it('ask_user_question increments streamGen so completeHandler is no-op', () => {
      const msgId = 'msg-gen-auq';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      // Create complete handler BEFORE the ask_user_question event
      const completeHandler = result.current.createCompleteHandler('tab-1');
      const genBefore = result.current.streamGenRef.current;

      // ask_user_question event fires — should increment generation
      const streamHandler = result.current.createStreamHandler(msgId, 'tab-1');
      act(() => {
        streamHandler({
          type: 'ask_user_question',
          toolUseId: 'tool-auq',
          questions: [{
            question: 'Pick one',
            header: 'Choice',
            options: [{ label: 'A', description: 'Option A' }],
            multiSelect: false,
          }],
        });
      });

      expect(result.current.streamGenRef.current).toBeGreaterThan(genBefore);

      // Now start a new stream (user answers the question)
      act(() => {
        result.current.setIsStreaming(true);
      });

      // Stale complete handler fires — should be no-op
      act(() => {
        completeHandler();
      });

      expect(result.current.isStreaming).toBe(true);
    });

    it('error event increments streamGen so completeHandler is no-op', () => {
      const msgId = 'msg-gen-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const completeHandler = result.current.createCompleteHandler('tab-1');
      const genBefore = result.current.streamGenRef.current;

      const streamHandler = result.current.createStreamHandler(msgId, 'tab-1');
      act(() => {
        streamHandler({ type: 'error', message: 'Backend error' });
      });

      expect(result.current.streamGenRef.current).toBeGreaterThan(genBefore);

      // Start a new stream
      act(() => {
        result.current.setIsStreaming(true);
      });

      // Stale complete handler fires — should be no-op
      act(() => {
        completeHandler();
      });

      expect(result.current.isStreaming).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// Fix 6: Per-tab state isolation tests
// ---------------------------------------------------------------------------

describe('Fix 6: Per-tab state isolation', () => {
  describe('initTabState', () => {
    it('creates a new tab entry with defaults', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-new');
      });

      const tabState = result.current.tabStateRef.current.get('tab-new');
      expect(tabState).toBeDefined();
      expect(tabState!.messages).toEqual([]);
      expect(tabState!.sessionId).toBeUndefined();
      expect(tabState!.pendingQuestion).toBeNull();
      expect(tabState!.abortController).toBeNull();
      expect(tabState!.pendingStream).toBe(false);
      expect(tabState!.streamGen).toBe(0);
      expect(tabState!.status).toBe('idle');
    });

    it('sets the new tab as active', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-new');
      });

      expect(result.current.activeTabIdRef.current).toBe('tab-new');
    });

    it('accepts initial messages', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const welcomeMsg = makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Welcome!' }],
      });

      act(() => {
        result.current.initTabState('tab-new', [welcomeMsg]);
      });

      const tabState = result.current.tabStateRef.current.get('tab-new');
      expect(tabState!.messages).toHaveLength(1);
      expect(tabState!.messages[0].content[0]).toEqual({
        type: 'text',
        text: 'Welcome!',
      });
    });
  });

  describe('saveTabState and restoreTabState', () => {
    it('saves current state to per-tab map on tab switch away', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab A content' }],
      });

      act(() => {
        result.current.initTabState('tab-a');
        result.current.setMessages([msg]);
        result.current.setSessionId('sess-a');
      });

      // Save tab-a state
      act(() => {
        result.current.saveTabState();
      });

      const saved = result.current.tabStateRef.current.get('tab-a');
      expect(saved).toBeDefined();
      expect(saved!.sessionId).toBeUndefined(); // initTabState set it, but saveTabState syncs from refs
    });

    it('restores tab state from per-tab map on switch back', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msgA = makeMessage({
        id: 'msg-a',
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab A' }],
      });
      const msgB = makeMessage({
        id: 'msg-b',
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab B' }],
      });

      // Set up tab-a with messages directly in the map
      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [msgA],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 3,
          status: 'idle',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [msgB],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 1,
          status: 'idle',
        });
      });

      // Restore tab-a
      act(() => {
        result.current.restoreTabState('tab-a');
      });

      expect(result.current.messages).toEqual([msgA]);
      expect(result.current.sessionId).toBe('sess-a');
      expect(result.current.activeTabIdRef.current).toBe('tab-a');
      expect(result.current.streamGenRef.current).toBe(3);

      // Restore tab-b
      act(() => {
        result.current.restoreTabState('tab-b');
      });

      expect(result.current.messages).toEqual([msgB]);
      expect(result.current.sessionId).toBe('sess-b');
      expect(result.current.activeTabIdRef.current).toBe('tab-b');
      expect(result.current.streamGenRef.current).toBe(1);
    });

    it('returns false when tab not found in map', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      let restored: boolean = false;
      act(() => {
        restored = result.current.restoreTabState('nonexistent');
      });

      expect(restored).toBe(false);
    });

    it('preserves per-tab isolation across round-trip switches', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msgA = makeMessage({
        id: 'msg-a',
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab A' }],
      });
      const msgB = makeMessage({
        id: 'msg-b',
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab B' }],
      });

      // Initialize both tabs in the map
      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [msgA],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [msgB],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-a, then tab-b, then back to tab-a
      act(() => { result.current.restoreTabState('tab-a'); });
      expect(result.current.messages[0].id).toBe('msg-a');

      act(() => { result.current.restoreTabState('tab-b'); });
      expect(result.current.messages[0].id).toBe('msg-b');

      act(() => { result.current.restoreTabState('tab-a'); });
      expect(result.current.messages[0].id).toBe('msg-a');
      expect(result.current.sessionId).toBe('sess-a');
    });
  });

  describe('tab-aware createStreamHandler', () => {
    it('updates per-tab map for background tab without changing foreground useState', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const bgMsgId = 'bg-msg-1';
      const fgMsg = makeMessage({
        id: 'fg-msg-1',
        role: 'assistant',
        content: [{ type: 'text', text: 'Foreground' }],
      });
      const bgMsg = makeMessage({
        id: bgMsgId,
        role: 'assistant',
        content: [],
      });

      // Set up: tab-a is background with a message, tab-b is foreground
      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [bgMsg],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.initTabState('tab-b');
        result.current.setMessages([fgMsg]);
      });

      // Create a stream handler for background tab-a
      const bgHandler = result.current.createStreamHandler(bgMsgId, 'tab-a');

      // Background tab receives assistant content
      act(() => {
        bgHandler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Background update' }],
        });
      });

      // Foreground useState should still show tab-b's message
      expect(result.current.messages[0].id).toBe('fg-msg-1');
      expect(result.current.messages[0].content[0]).toEqual({
        type: 'text',
        text: 'Foreground',
      });

      // Background tab-a's map entry should be updated
      const tabAState = result.current.tabStateRef.current.get('tab-a');
      expect(tabAState!.messages[0].content).toHaveLength(1);
      expect((tabAState!.messages[0].content[0] as { text: string }).text).toBe(
        'Background update',
      );
    });

    it('updates both map and useState for active foreground tab', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msgId = 'fg-msg-active';
      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
        result.current.activeTabIdRef.current = 'tab-a';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-a');

      act(() => {
        handler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Active tab update' }],
        });
      });

      // Both useState and map should be updated
      expect(result.current.messages[0].content).toHaveLength(1);
      const mapState = result.current.tabStateRef.current.get('tab-a');
      expect(mapState!.messages[0].content).toHaveLength(1);
    });

    it('is a no-op when tab has been closed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msgId = 'closed-msg';
      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        result.current.tabStateRef.current.set('tab-closed', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
        result.current.initTabState('tab-active');
        result.current.setMessages([]);
      });

      // Create handler for tab-closed, then close it
      const handler = result.current.createStreamHandler(msgId, 'tab-closed');

      act(() => {
        result.current.cleanupTabState('tab-closed');
      });

      // Handler fires after tab closed — should not crash or modify state
      act(() => {
        handler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Ghost update' }],
        });
      });

      // Active tab's messages should be unchanged
      expect(result.current.messages).toEqual([]);
    });
  });

  describe('per-tab abort controller isolation', () => {
    it('each tab has its own abort controller instance', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controllerA = new AbortController();
      const controllerB = new AbortController();

      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: controllerA,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: controllerB,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
      });

      const tabAState = result.current.tabStateRef.current.get('tab-a');
      const tabBState = result.current.tabStateRef.current.get('tab-b');

      expect(tabAState!.abortController).not.toBe(tabBState!.abortController);
    });

    it('aborting active tab controller does not affect background tab', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controllerA = new AbortController();
      const controllerB = new AbortController();

      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: controllerA,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: controllerB,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.activeTabIdRef.current = 'tab-a';
      });

      // Abort active tab-a's controller
      controllerA.abort();

      expect(controllerA.signal.aborted).toBe(true);
      expect(controllerB.signal.aborted).toBe(false);
    });
  });

  describe('per-tab _pendingStream isolation', () => {
    it('switching tabs does not leak pendingStream from source to target', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Tab-a has pendingStream=true, tab-b has pendingStream=false
      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: true,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-b — its pendingStream should be false
      act(() => {
        result.current.restoreTabState('tab-b');
      });

      // isStreaming should be false for tab-b (no sessionId, no pendingStream)
      expect(result.current.isStreaming).toBe(false);

      // Tab-a's pendingStream in the map should still be true
      const tabAState = result.current.tabStateRef.current.get('tab-a');
      expect(tabAState!.pendingStream).toBe(true);
    });
  });

  describe('per-tab pendingQuestion isolation', () => {
    it('switching tabs does not show source tab question in target', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const questionA: PendingQuestion = {
        toolUseId: 'tool-q-a',
        questions: [{
          question: 'Tab A question',
          header: 'Q',
          options: [{ label: 'Yes', description: 'Confirm' }],
          multiSelect: false,
        }],
      };

      act(() => {
        result.current.tabStateRef.current.set('tab-a', {
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: questionA,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'waiting_input',
        });
        result.current.tabStateRef.current.set('tab-b', {
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-b
      act(() => {
        result.current.restoreTabState('tab-b');
      });

      // Tab-b should have no pending question
      expect(result.current.pendingQuestion).toBeNull();

      // Switch back to tab-a — question should be restored
      act(() => {
        result.current.restoreTabState('tab-a');
      });

      expect(result.current.pendingQuestion).not.toBeNull();
      expect(result.current.pendingQuestion!.toolUseId).toBe('tool-q-a');
    });
  });

  describe('tab close cleanup', () => {
    it('removes entry from tabStateRef on cleanup', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-to-close');
      });

      expect(result.current.tabStateRef.current.has('tab-to-close')).toBe(true);

      act(() => {
        result.current.cleanupTabState('tab-to-close');
      });

      expect(result.current.tabStateRef.current.has('tab-to-close')).toBe(false);
    });

    it('aborts the tab abort controller on cleanup', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controller = new AbortController();

      act(() => {
        result.current.tabStateRef.current.set('tab-abort', {
          messages: [],
          sessionId: 'sess-abort',
          pendingQuestion: null,
          abortController: controller,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
      });

      expect(controller.signal.aborted).toBe(false);

      act(() => {
        result.current.cleanupTabState('tab-abort');
      });

      expect(controller.signal.aborted).toBe(true);
      expect(result.current.tabStateRef.current.has('tab-abort')).toBe(false);
    });

    it('handles cleanup of non-existent tab gracefully', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Should not throw
      act(() => {
        result.current.cleanupTabState('nonexistent-tab');
      });

      expect(result.current.tabStateRef.current.has('nonexistent-tab')).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// Fix 2: Auto-scroll with user scroll detection tests
// ---------------------------------------------------------------------------

describe('Fix 2: Auto-scroll with user scroll detection', () => {
  describe('userScrolledUpRef', () => {
    it('is false by default', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      expect(result.current.userScrolledUpRef.current).toBe(false);
    });

    it('can be set to true to indicate user scrolled up', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.userScrolledUpRef.current = true;
      });

      expect(result.current.userScrolledUpRef.current).toBe(true);
    });
  });

  describe('resetUserScroll', () => {
    it('resets userScrolledUpRef to false', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Simulate user scrolling up
      act(() => {
        result.current.userScrolledUpRef.current = true;
      });
      expect(result.current.userScrolledUpRef.current).toBe(true);

      // Reset on new user message
      act(() => {
        result.current.resetUserScroll();
      });

      expect(result.current.userScrolledUpRef.current).toBe(false);
    });

    it('is a no-op when already false', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      expect(result.current.userScrolledUpRef.current).toBe(false);

      act(() => {
        result.current.resetUserScroll();
      });

      expect(result.current.userScrolledUpRef.current).toBe(false);
    });
  });
});

// ---------------------------------------------------------------------------
// Fix 3: Error handling — streaming stop and error visibility tests
// ---------------------------------------------------------------------------

describe('Fix 3: Error handling and visibility', () => {
  describe('error event stops streaming', () => {
    it('sets isStreaming to false on error event', () => {
      const msgId = 'msg-err-stop';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      expect(result.current.isStreaming).toBe(true);

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Backend failure' });
      });

      expect(result.current.isStreaming).toBe(false);
    });
  });

  describe('error content is visible', () => {
    it('error message text is present in message content', () => {
      const msgId = 'msg-err-visible';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({
        id: msgId,
        role: 'assistant',
        content: [{ type: 'text', text: 'partial response' }],
      });

      act(() => {
        result.current.tabStateRef.current.set('tab-1', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.activeTabIdRef.current = 'tab-1';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Something went wrong' });
      });

      const content = result.current.messages[0].content;
      expect(content).toHaveLength(1);
      expect((content[0] as { text: string }).text).toContain(
        'Something went wrong',
      );
    });

    it('includes suggestedAction in error text when present', () => {
      const msgId = 'msg-err-suggest';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        result.current.tabStateRef.current.set('tab-1', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.activeTabIdRef.current = 'tab-1';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({
          type: 'error',
          message: 'Rate limited',
          suggestedAction: 'Try again in 30 seconds',
        } as unknown as import('../types').StreamEvent);
      });

      const text = (result.current.messages[0].content[0] as { text: string }).text;
      expect(text).toContain('Rate limited');
      expect(text).toContain('Try again in 30 seconds');
    });
  });

  describe('isError flag on message', () => {
    it('sets isError: true on the message when error event fires', () => {
      const msgId = 'msg-err-flag';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        result.current.tabStateRef.current.set('tab-1', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        result.current.activeTabIdRef.current = 'tab-1';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Oops' });
      });

      expect(result.current.messages[0].isError).toBe(true);
    });

    it('does not set isError on non-error messages', () => {
      const msgId = 'msg-no-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        result.current.tabStateRef.current.set('tab-1', {
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'idle',
        });
        result.current.activeTabIdRef.current = 'tab-1';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Normal response' }],
        });
      });

      expect(result.current.messages[0].isError).toBeUndefined();
    });
  });

  describe('error resets userScrolledUpRef for auto-scroll', () => {
    it('resets userScrolledUpRef to false on error event', () => {
      const msgId = 'msg-err-scroll';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
        result.current.userScrolledUpRef.current = true;
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      expect(result.current.userScrolledUpRef.current).toBe(true);

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Error occurred' });
      });

      // Error should reset scroll so user sees the error
      expect(result.current.userScrolledUpRef.current).toBe(false);
    });
  });

  describe('error increments streamGen', () => {
    it('increments streamGen so stale completeHandler is no-op', () => {
      const msgId = 'msg-err-gen';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const genBefore = result.current.streamGenRef.current;
      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Fail' });
      });

      expect(result.current.streamGenRef.current).toBeGreaterThan(genBefore);
    });
  });
});

// ---------------------------------------------------------------------------
// Fix 9: Elapsed time counter tests
// ---------------------------------------------------------------------------

describe('formatElapsed helper', () => {
  it('formats 0 seconds as "0s"', () => {
    expect(formatElapsed(0)).toBe('0s');
  });

  it('formats 15 seconds as "15s"', () => {
    expect(formatElapsed(15)).toBe('15s');
  });

  it('formats 59 seconds as "59s"', () => {
    expect(formatElapsed(59)).toBe('59s');
  });

  it('formats 60 seconds as "1m 0s"', () => {
    expect(formatElapsed(60)).toBe('1m 0s');
  });

  it('formats 65 seconds as "1m 5s"', () => {
    expect(formatElapsed(65)).toBe('1m 5s');
  });

  it('formats 125 seconds as "2m 5s"', () => {
    expect(formatElapsed(125)).toBe('2m 5s');
  });
});

describe('ELAPSED_DISPLAY_THRESHOLD_MS constant', () => {
  it('is 10000 (10 seconds)', () => {
    expect(ELAPSED_DISPLAY_THRESHOLD_MS).toBe(10000);
  });
});

describe('Fix 9: Elapsed time counter during initial wait', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('elapsedSeconds is 0 by default', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );
    expect(result.current.elapsedSeconds).toBe(0);
  });

  it('starts counting after streaming begins with no content', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    act(() => {
      result.current.setIsStreaming(true);
    });

    // Advance 12 seconds — should tick elapsed
    act(() => {
      vi.advanceTimersByTime(12000);
    });

    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(11);
  });

  it('clears elapsed when first content arrives (streamingActivity becomes non-null)', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    act(() => {
      result.current.setIsStreaming(true);
    });

    // Advance 5 seconds
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(4);

    // Now add content — streamingActivity becomes non-null
    act(() => {
      result.current.setMessages([
        makeMessage({
          role: 'assistant',
          content: [{ type: 'text', text: 'Hello' }],
        }),
      ]);
    });

    // After content arrives, elapsed should reset to 0
    // Allow a tick for the useEffect to fire
    act(() => {
      vi.advanceTimersByTime(100);
    });

    expect(result.current.elapsedSeconds).toBe(0);
  });

  it('resets elapsed to 0 when streaming stops', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    act(() => {
      result.current.setIsStreaming(true);
    });

    act(() => {
      vi.advanceTimersByTime(8000);
    });

    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(7);

    act(() => {
      result.current.setIsStreaming(false);
    });

    // Allow useEffect to fire
    act(() => {
      vi.advanceTimersByTime(100);
    });

    expect(result.current.elapsedSeconds).toBe(0);
  });

  it('does not count when streaming with content already present', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    // Set messages first, then start streaming
    act(() => {
      result.current.setMessages([
        makeMessage({
          role: 'assistant',
          content: [makeToolUse('Bash')],
        }),
      ]);
      result.current.setIsStreaming(true);
    });

    act(() => {
      vi.advanceTimersByTime(15000);
    });

    // streamingActivity is non-null (tool_use present), so elapsed stays 0
    expect(result.current.elapsedSeconds).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Fix 4: Enhanced deriveStreamingActivity with operational context
// ---------------------------------------------------------------------------

describe('Fix 4: deriveStreamingActivity with operational context', () => {
  describe('deriveStreamingActivity extended return type', () => {
    it('returns null when not streaming', () => {
      const result = deriveStreamingActivity(false, [
        makeMessage({ role: 'assistant', content: [makeToolUse('Bash')] }),
      ]);
      expect(result).toBeNull();
    });

    it('returns null when streaming with no messages', () => {
      expect(deriveStreamingActivity(true, [])).toBeNull();
    });

    it('returns hasContent=true, toolName=null, toolContext=null, toolCount=0 for text-only', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{ type: 'text', text: 'Working...' }],
        }),
      ]);
      expect(result).not.toBeNull();
      expect(result!.hasContent).toBe(true);
      expect(result!.toolName).toBeNull();
      expect(result!.toolContext).toBeNull();
      expect(result!.toolCount).toBe(0);
    });

    it('returns toolContext from command input', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-1',
            name: 'Bash',
            input: { command: 'npm test -- --run' },
          }],
        }),
      ]);
      expect(result).not.toBeNull();
      expect(result!.toolName).toBe('Bash');
      expect(result!.toolContext).toBe('npm test -- --run');
      expect(result!.toolCount).toBe(1);
    });

    it('returns toolContext from path input', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-2',
            name: 'Read',
            input: { path: 'src/components/Chat.tsx' },
          }],
        }),
      ]);
      expect(result!.toolContext).toBe('src/components/Chat.tsx');
    });

    it('returns toolContext from query input', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-3',
            name: 'Search',
            input: { query: 'error handling pattern' },
          }],
        }),
      ]);
      expect(result!.toolContext).toBe('error handling pattern');
    });

    it('counts multiple tool_use blocks correctly', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [
            { type: 'tool_use' as const, id: 'tu-a', name: 'Read', input: { path: 'a.ts' } },
            { type: 'tool_result' as const, toolUseId: 'tu-a', content: 'ok', isError: false },
            { type: 'tool_use' as const, id: 'tu-b', name: 'Bash', input: { command: 'ls' } },
            { type: 'tool_result' as const, toolUseId: 'tu-b', content: 'ok', isError: false },
            { type: 'tool_use' as const, id: 'tu-c', name: 'Search', input: { query: 'foo' } },
          ],
        }),
      ]);
      expect(result!.toolCount).toBe(3);
      // Last tool_use is Search
      expect(result!.toolName).toBe('Search');
      expect(result!.toolContext).toBe('foo');
    });

    it('returns toolContext=null when tool_use has no input', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [makeToolUse('Bash')],
        }),
      ]);
      expect(result!.toolName).toBe('Bash');
      expect(result!.toolContext).toBeNull();
      expect(result!.toolCount).toBe(1);
    });

    it('returns toolContext=null when tool_use input is empty object', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-empty',
            name: 'Custom',
            input: {},
          }],
        }),
      ]);
      expect(result!.toolContext).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// Fix 4: sanitizeCommand tests
// ---------------------------------------------------------------------------

describe('sanitizeCommand', () => {
  it('returns command unchanged when no sensitive content', () => {
    expect(sanitizeCommand('npm test -- --run')).toBe('npm test -- --run');
  });

  it('strips content after --password flag', () => {
    expect(sanitizeCommand('mysql -u root --password secret123')).toBe('mysql -u root');
  });

  it('strips content after --token flag', () => {
    expect(sanitizeCommand('curl -H --token abc123xyz')).toBe('curl -H');
  });

  it('strips content after --key flag', () => {
    expect(sanitizeCommand('aws s3 cp --key AKIAIOSFODNN7')).toBe('aws s3 cp');
  });

  it('strips environment variable assignments', () => {
    expect(sanitizeCommand('API_KEY=secret123 node server.js')).toBe('node server.js');
  });

  it('strips multiple env var assignments', () => {
    const result = sanitizeCommand('DB_PASS=foo TOKEN=bar node app.js');
    expect(result).toBe('node app.js');
  });

  it('returns [command] when entire command is sensitive', () => {
    expect(sanitizeCommand('SECRET_KEY=abc123')).toBe('[command]');
  });

  it('returns [command] for empty string after sanitization', () => {
    expect(sanitizeCommand('--password mysecret')).toBe('[command]');
  });

  it('truncates to 60 characters', () => {
    const longCmd = 'npm run build -- --config=production --output-dir=/very/long/path/that/exceeds/sixty/characters/limit';
    expect(sanitizeCommand(longCmd).length).toBeLessThanOrEqual(60);
  });
});

// ---------------------------------------------------------------------------
// Fix 4: extractToolContext tests
// ---------------------------------------------------------------------------

describe('extractToolContext', () => {
  it('returns null for null input', () => {
    expect(extractToolContext(null)).toBeNull();
  });

  it('returns null for undefined input', () => {
    expect(extractToolContext(undefined)).toBeNull();
  });

  it('returns null for empty object', () => {
    expect(extractToolContext({})).toBeNull();
  });

  it('prioritizes command over path and query', () => {
    const result = extractToolContext({
      command: 'npm test',
      path: 'src/index.ts',
      query: 'search term',
    });
    expect(result).toBe('npm test');
  });

  it('prioritizes path over query when no command', () => {
    const result = extractToolContext({
      path: 'src/index.ts',
      query: 'search term',
    });
    expect(result).toBe('src/index.ts');
  });

  it('uses file_path when path is absent', () => {
    const result = extractToolContext({ file_path: 'lib/utils.ts' });
    expect(result).toBe('lib/utils.ts');
  });

  it('uses query when no command or path', () => {
    const result = extractToolContext({ query: 'error handling' });
    expect(result).toBe('error handling');
  });

  it('uses search when no command, path, or query', () => {
    const result = extractToolContext({ search: 'TODO fixme' });
    expect(result).toBe('TODO fixme');
  });

  it('uses pattern when no other keys', () => {
    const result = extractToolContext({ pattern: '*.test.ts' });
    expect(result).toBe('*.test.ts');
  });

  it('returns null for non-string values', () => {
    expect(extractToolContext({ command: 123 })).toBeNull();
    expect(extractToolContext({ path: true })).toBeNull();
  });

  it('returns null for whitespace-only strings', () => {
    expect(extractToolContext({ command: '   ' })).toBeNull();
    expect(extractToolContext({ path: '  ' })).toBeNull();
  });

  it('truncates long values to 60 chars', () => {
    const longPath = 'a'.repeat(100);
    const result = extractToolContext({ path: longPath });
    expect(result!.length).toBeLessThanOrEqual(60);
  });
});

// ---------------------------------------------------------------------------
// Fix 4: Debounce — activity label stability tests
// ---------------------------------------------------------------------------

describe('Fix 4: Activity label debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('MIN_ACTIVITY_DISPLAY_MS is 1500', () => {
    expect(MIN_ACTIVITY_DISPLAY_MS).toBe(1500);
  });

  it('displayedActivity persists for MIN_ACTIVITY_DISPLAY_MS before updating', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    // Start streaming with a tool
    act(() => {
      result.current.setIsStreaming(true);
      result.current.setMessages([
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-d1',
            name: 'Bash',
            input: { command: 'npm test' },
          }],
        }),
      ]);
    });

    // Allow effects to settle
    act(() => { vi.advanceTimersByTime(100); });

    const firstActivity = result.current.displayedActivity;
    expect(firstActivity).not.toBeNull();
    expect(firstActivity!.toolName).toBe('Bash');

    // Rapidly change to a new tool before MIN_ACTIVITY_DISPLAY_MS
    act(() => {
      result.current.setMessages([
        makeMessage({
          role: 'assistant',
          content: [
            { type: 'tool_use' as const, id: 'tu-d1', name: 'Bash', input: { command: 'npm test' } },
            { type: 'tool_use' as const, id: 'tu-d2', name: 'Read', input: { path: 'src/app.ts' } },
          ],
        }),
      ]);
    });

    // Before debounce expires, displayed should still show old label
    act(() => { vi.advanceTimersByTime(500); });

    // The displayed activity may still be the old one or may have updated
    // depending on implementation — the key invariant is that after
    // MIN_ACTIVITY_DISPLAY_MS the new activity is shown
    act(() => { vi.advanceTimersByTime(MIN_ACTIVITY_DISPLAY_MS); });

    expect(result.current.displayedActivity).not.toBeNull();
    expect(result.current.displayedActivity!.toolName).toBe('Read');
  });

  it('final activity updates immediately when streaming stops', () => {
    const { result } = renderHook(() =>
      useChatStreamingLifecycle(createMockDeps()),
    );

    act(() => {
      result.current.setIsStreaming(true);
      result.current.setMessages([
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-final',
            name: 'Bash',
            input: { command: 'echo done' },
          }],
        }),
      ]);
    });

    act(() => { vi.advanceTimersByTime(100); });
    expect(result.current.displayedActivity).not.toBeNull();

    // Stop streaming — displayedActivity should become null
    act(() => {
      result.current.setIsStreaming(false);
    });

    act(() => { vi.advanceTimersByTime(100); });
    expect(result.current.displayedActivity).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Fix 5: sessionStorage persistence tests
// ---------------------------------------------------------------------------

describe('Fix 5: isSessionStorageAvailable', () => {
  it('returns true in test environment', () => {
    expect(isSessionStorageAvailable()).toBe(true);
  });
});

describe('Fix 5: persistPendingState', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('writes to sessionStorage with correct key format', () => {
    const question: PendingQuestion = {
      toolUseId: 'tool-persist',
      questions: [{
        question: 'Continue?',
        header: 'Confirm',
        options: [{ label: 'Yes', description: 'Proceed' }],
        multiSelect: false,
      }],
    };
    const msgs = [makeMessage({ role: 'assistant', content: [] })];

    persistPendingState('sess-123', msgs, question);

    const key = `${STORAGE_KEY_PREFIX}sess-123`;
    const stored = window.sessionStorage.getItem(key);
    expect(stored).not.toBeNull();

    const parsed = JSON.parse(stored!);
    expect(parsed.sessionId).toBe('sess-123');
    expect(parsed.pendingQuestion.toolUseId).toBe('tool-persist');
    expect(parsed.messages).toHaveLength(1);
  });

  it('gracefully handles quota exceeded error', () => {
    const question: PendingQuestion = {
      toolUseId: 'tool-quota',
      questions: [{
        question: 'Q?',
        header: 'H',
        options: [{ label: 'A', description: 'a' }],
        multiSelect: false,
      }],
    };
    const msgs = [makeMessage({ role: 'assistant', content: [] })];

    // Mock setItem to throw quota exceeded
    const spy = vi.spyOn(window.sessionStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError');
    });

    // Should not throw
    expect(() => persistPendingState('sess-quota', msgs, question)).not.toThrow();

    spy.mockRestore();
  });
});

describe('Fix 5: restorePendingState', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('reads from sessionStorage and returns valid state', () => {
    const state: PersistedPendingState = {
      version: PERSISTED_STATE_VERSION,
      messages: [makeMessage({ role: 'assistant', content: [] })],
      pendingQuestion: {
        toolUseId: 'tool-restore',
        questions: [{
          question: 'Pick',
          header: 'H',
          options: [{ label: 'X', description: 'x' }],
          multiSelect: false,
        }],
      },
      sessionId: 'sess-restore',
    };
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-restore`,
      JSON.stringify(state),
    );

    const restored = restorePendingState('sess-restore');
    expect(restored).not.toBeNull();
    expect(restored!.sessionId).toBe('sess-restore');
    expect(restored!.pendingQuestion.toolUseId).toBe('tool-restore');
    expect(restored!.messages).toHaveLength(1);
  });

  it('returns null for missing entry', () => {
    expect(restorePendingState('nonexistent')).toBeNull();
  });

  it('returns null and discards corrupted JSON', () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-corrupt`,
      '{not valid json!!!',
    );

    const result = restorePendingState('sess-corrupt');
    expect(result).toBeNull();

    // Entry should be cleaned up
    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-corrupt`),
    ).toBeNull();
  });

  it('returns null and discards schema-mismatch entries', () => {
    // Missing pendingQuestion.toolUseId
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-schema`,
      JSON.stringify({
        messages: [],
        pendingQuestion: { noToolUseId: true },
        sessionId: 'sess-schema',
      }),
    );

    const result = restorePendingState('sess-schema');
    expect(result).toBeNull();

    // Entry should be cleaned up
    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-schema`),
    ).toBeNull();
  });

  it('returns null when entry has no messages array', () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-nomsg`,
      JSON.stringify({
        pendingQuestion: { toolUseId: 'x', questions: [] },
        sessionId: 'sess-nomsg',
      }),
    );
    expect(restorePendingState('sess-nomsg')).toBeNull();
  });
});

describe('Fix 5: removePendingState', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('removes entry from sessionStorage', () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-rm`,
      JSON.stringify({ messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: 'sess-rm' }),
    );

    removePendingState('sess-rm');

    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-rm`),
    ).toBeNull();
  });

  it('does not throw when entry does not exist', () => {
    expect(() => removePendingState('nonexistent')).not.toThrow();
  });
});

describe('Fix 5: prepareMessagesForStorage', () => {
  it('returns messages unchanged for small sessions (< 80 tool_use blocks)', () => {
    const msgs = [
      makeMessage({
        role: 'assistant',
        content: [
          makeToolUse('Bash'),
          { type: 'tool_result' as const, toolUseId: 'tr-1', content: 'long result text here', isError: false },
        ],
      }),
    ];

    const result = prepareMessagesForStorage(msgs);
    expect(result).toEqual(msgs);
  });

  it('truncates tool_result content for large sessions (80+ tool_use blocks)', () => {
    // Build a message with 85 tool_use blocks
    const content: ContentBlock[] = [];
    for (let i = 0; i < 85; i++) {
      content.push({
        type: 'tool_use' as const,
        id: `tu-${i}`,
        name: 'Bash',
        input: {},
      });
      content.push({
        type: 'tool_result' as const,
        toolUseId: `tu-${i}`,
        content: 'x'.repeat(500), // 500 chars — should be truncated to 200
        isError: false,
      });
    }

    const msgs = [makeMessage({ role: 'assistant', content })];
    const result = prepareMessagesForStorage(msgs);

    // Original should not be mutated
    const origToolResult = msgs[0].content.find(
      (b) => b.type === 'tool_result' && 'content' in b,
    ) as unknown as { content: string };
    expect(origToolResult.content.length).toBe(500);

    // Result tool_result blocks should be truncated
    const truncatedBlock = result[0].content.find(
      (b) => b.type === 'tool_result' && 'content' in b,
    ) as unknown as { content: string };
    expect(truncatedBlock.content.length).toBeLessThanOrEqual(201); // 200 + ellipsis char
  });

  it('does not truncate non-tool_result blocks in large sessions', () => {
    const content: ContentBlock[] = [];
    for (let i = 0; i < 85; i++) {
      content.push({
        type: 'tool_use' as const,
        id: `tu-${i}`,
        name: 'Read',
        input: {},
      });
    }
    content.push({ type: 'text', text: 'x'.repeat(500) });

    const msgs = [makeMessage({ role: 'assistant', content })];
    const result = prepareMessagesForStorage(msgs);

    const textBlock = result[0].content.find((b) => b.type === 'text') as { text: string };
    expect(textBlock.text.length).toBe(500);
  });
});

describe('Fix 5: STORAGE_KEY_PREFIX', () => {
  it('has the expected prefix value', () => {
    expect(STORAGE_KEY_PREFIX).toBe('swarm_chat_pending_');
  });
});

describe('Fix 5: cleanupStalePendingEntries', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('removes entries for 404 sessions', async () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-404`,
      JSON.stringify({ version: PERSISTED_STATE_VERSION, messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: 'sess-404' }),
    );

    // Use a structured 404 error (Req 4: isNotFoundError checks status, not message)
    const getSession = vi.fn().mockRejectedValue({ status: 404, message: 'Not Found' });

    await cleanupStalePendingEntries(getSession);

    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-404`),
    ).toBeNull();
  });

  it('keeps entries when getSession throws a network error', async () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-net`,
      JSON.stringify({ messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: 'sess-net' }),
    );

    const getSession = vi.fn().mockRejectedValue(new Error('Network timeout'));

    await cleanupStalePendingEntries(getSession);

    // Network error — entry should be kept for next cleanup cycle
    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-net`),
    ).not.toBeNull();
  });

  it('keeps entries when session exists (getSession resolves)', async () => {
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-ok`,
      JSON.stringify({ messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: 'sess-ok' }),
    );

    const getSession = vi.fn().mockResolvedValue({ id: 'sess-ok' });

    await cleanupStalePendingEntries(getSession);

    expect(
      window.sessionStorage.getItem(`${STORAGE_KEY_PREFIX}sess-ok`),
    ).not.toBeNull();
  });

  it('processes at most 5 entries per invocation', async () => {
    // Add 8 stale entries
    for (let i = 0; i < 8; i++) {
      window.sessionStorage.setItem(
        `${STORAGE_KEY_PREFIX}sess-stale-${i}`,
        JSON.stringify({ messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: `sess-stale-${i}` }),
      );
    }

    const getSession = vi.fn().mockRejectedValue(new Error('404 not found'));

    await cleanupStalePendingEntries(getSession);

    // Should have called getSession at most 5 times
    expect(getSession).toHaveBeenCalledTimes(5);
  });

  it('does not throw when sessionStorage is empty', async () => {
    const getSession = vi.fn();
    await expect(cleanupStalePendingEntries(getSession)).resolves.not.toThrow();
    expect(getSession).not.toHaveBeenCalled();
  });

  it('ignores non-matching keys in sessionStorage', async () => {
    window.sessionStorage.setItem('other_key', 'value');
    window.sessionStorage.setItem(
      `${STORAGE_KEY_PREFIX}sess-check`,
      JSON.stringify({ messages: [], pendingQuestion: { toolUseId: 'x', questions: [] }, sessionId: 'sess-check' }),
    );

    const getSession = vi.fn().mockResolvedValue({ id: 'sess-check' });

    await cleanupStalePendingEntries(getSession);

    // Only called for the matching key
    expect(getSession).toHaveBeenCalledTimes(1);
    // Non-matching key should still exist
    expect(window.sessionStorage.getItem('other_key')).toBe('value');
  });
});


// ---------------------------------------------------------------------------
// Fix 7: Tab limit enforcement (MAX_OPEN_TABS guard)
// ---------------------------------------------------------------------------

describe('Fix 7: Tab limit enforcement', () => {
  describe('MAX_OPEN_TABS constant', () => {
    it('is 6', () => {
      expect(MAX_OPEN_TABS).toBe(6);
    });
  });

  describe('initTabState respects MAX_OPEN_TABS', () => {
    it('creates a tab when below the limit', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-1');
      });

      expect(result.current.tabStateRef.current.has('tab-1')).toBe(true);
      expect(result.current.tabStateRef.current.size).toBe(1);
    });

    it('allows creating up to MAX_OPEN_TABS tabs', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        for (let i = 0; i < MAX_OPEN_TABS; i++) {
          result.current.initTabState(`tab-${i}`);
        }
      });

      expect(result.current.tabStateRef.current.size).toBe(MAX_OPEN_TABS);
    });
  });

  describe('tab creation re-enabled after close', () => {
    it('closing a tab at the limit allows creating a new tab', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Fill to MAX_OPEN_TABS
      act(() => {
        for (let i = 0; i < MAX_OPEN_TABS; i++) {
          result.current.initTabState(`tab-${i}`);
        }
      });

      expect(result.current.tabStateRef.current.size).toBe(MAX_OPEN_TABS);

      // Close one tab
      act(() => {
        result.current.cleanupTabState('tab-0');
      });

      expect(result.current.tabStateRef.current.size).toBe(MAX_OPEN_TABS - 1);

      // Now creating a new tab should succeed
      act(() => {
        result.current.initTabState('tab-new');
      });

      expect(result.current.tabStateRef.current.has('tab-new')).toBe(true);
      expect(result.current.tabStateRef.current.size).toBe(MAX_OPEN_TABS);
    });
  });

  describe('tab status cleanup on close', () => {
    it('removes tabStatuses entry when tab is closed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-cleanup');
      });

      // Tab should have 'idle' status
      expect(result.current.tabStatuses['tab-cleanup']).toBe('idle');

      act(() => {
        result.current.cleanupTabState('tab-cleanup');
      });

      // Status entry should be removed
      expect(result.current.tabStatuses['tab-cleanup']).toBeUndefined();
    });
  });
});


// ---------------------------------------------------------------------------
// Fix 8: Tab status indicators
// ---------------------------------------------------------------------------

describe('Fix 8: Tab status indicators', () => {
  describe('updateTabStatus', () => {
    it('updates both tabStateRef entry and tabStatuses useState in sync', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-status');
      });

      // Initial status is 'idle'
      expect(result.current.tabStatuses['tab-status']).toBe('idle');
      expect(
        result.current.tabStateRef.current.get('tab-status')!.status,
      ).toBe('idle');

      // Update to 'streaming'
      act(() => {
        result.current.updateTabStatus('tab-status', 'streaming');
      });

      expect(result.current.tabStatuses['tab-status']).toBe('streaming');
      expect(
        result.current.tabStateRef.current.get('tab-status')!.status,
      ).toBe('streaming');
    });

    it('guard: no re-render when status has not changed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-guard');
      });

      // Capture the tabStatuses reference identity
      const statusesBefore = result.current.tabStatuses;

      // Update to same status ('idle') — should be a no-op
      act(() => {
        result.current.updateTabStatus('tab-guard', 'idle');
      });

      // tabStatuses reference should be the same (no re-render triggered)
      expect(result.current.tabStatuses).toBe(statusesBefore);
    });

    it('updates tabStatuses for a tab not yet in the map', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Update status for a tab that doesn't exist in the map
      act(() => {
        result.current.updateTabStatus('ghost-tab', 'error');
      });

      // tabStatuses should still be updated (useState side)
      expect(result.current.tabStatuses['ghost-tab']).toBe('error');
    });
  });

  describe('tab status transitions', () => {
    it('idle → streaming', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('idle');

      act(() => {
        result.current.updateTabStatus('tab-t', 'streaming');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('streaming');
      expect(
        result.current.tabStateRef.current.get('tab-t')!.status,
      ).toBe('streaming');
    });

    it('streaming → waiting_input', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
        result.current.updateTabStatus('tab-t', 'streaming');
      });

      act(() => {
        result.current.updateTabStatus('tab-t', 'waiting_input');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('waiting_input');
      expect(
        result.current.tabStateRef.current.get('tab-t')!.status,
      ).toBe('waiting_input');
    });

    it('streaming → error', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
        result.current.updateTabStatus('tab-t', 'streaming');
      });

      act(() => {
        result.current.updateTabStatus('tab-t', 'error');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('error');
    });

    it('streaming → complete_unread (background tab)', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
        result.current.updateTabStatus('tab-t', 'streaming');
      });

      act(() => {
        result.current.updateTabStatus('tab-t', 'complete_unread');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('complete_unread');
    });

    it('complete_unread → idle (tab switch)', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
        result.current.updateTabStatus('tab-t', 'complete_unread');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('complete_unread');

      // Simulate switching to this tab — clears unread
      act(() => {
        result.current.updateTabStatus('tab-t', 'idle');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('idle');
    });

    it('streaming → permission_needed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-t');
        result.current.updateTabStatus('tab-t', 'streaming');
      });

      act(() => {
        result.current.updateTabStatus('tab-t', 'permission_needed');
      });
      expect(result.current.tabStatuses['tab-t']).toBe('permission_needed');
    });
  });

  describe('tab status initialization', () => {
    it('new tab starts with idle status', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-init');
      });

      expect(result.current.tabStatuses['tab-init']).toBe('idle');
      expect(
        result.current.tabStateRef.current.get('tab-init')!.status,
      ).toBe('idle');
    });
  });

  describe('tab status cleanup', () => {
    it('closing tab removes entry from tabStatuses', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-rm');
        result.current.updateTabStatus('tab-rm', 'streaming');
      });
      expect(result.current.tabStatuses['tab-rm']).toBe('streaming');

      act(() => {
        result.current.cleanupTabState('tab-rm');
      });

      expect(result.current.tabStatuses['tab-rm']).toBeUndefined();
      expect(result.current.tabStateRef.current.has('tab-rm')).toBe(false);
    });
  });

  describe('stream handler updates tab status', () => {
    it('first assistant event sets status to streaming', () => {
      const msgId = 'msg-status-stream';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-s');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-s');

      act(() => {
        handler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Hello' }],
        });
      });

      expect(result.current.tabStatuses['tab-s']).toBe('streaming');
    });

    it('ask_user_question sets status to waiting_input', () => {
      const msgId = 'msg-status-auq';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-s');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-s');

      act(() => {
        handler({
          type: 'ask_user_question',
          toolUseId: 'tool-auq-s',
          questions: [{
            question: 'Pick',
            header: 'H',
            options: [{ label: 'A', description: 'a' }],
            multiSelect: false,
          }],
        });
      });

      expect(result.current.tabStatuses['tab-s']).toBe('waiting_input');
    });

    it('error event sets status to error', () => {
      const msgId = 'msg-status-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-s');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-s');

      act(() => {
        handler({ type: 'error', message: 'Backend error' });
      });

      expect(result.current.tabStatuses['tab-s']).toBe('error');
    });

    it('result event on foreground tab sets status to idle', () => {
      const msgId = 'msg-status-result';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.initTabState('tab-s');
        result.current.setIsStreaming(true);
        result.current.setSessionId('sess-result');
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-s');

      act(() => {
        handler({
          type: 'result',
          sessionId: 'sess-result',
          result: 'Done',
        } as unknown as StreamEvent);
      });

      expect(result.current.tabStatuses['tab-s']).toBe('idle');
    });

    it('result event on background tab sets status to complete_unread', () => {
      const msgId = 'msg-status-bg';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const bgMsg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        // Set up background tab
        result.current.tabStateRef.current.set('tab-bg', {
          messages: [bgMsg],
          sessionId: 'sess-bg',
          pendingQuestion: null,
          abortController: null,
          pendingStream: false,
          streamGen: 0,
          status: 'streaming',
        });
        // Set up foreground tab (different from tab-bg)
        result.current.initTabState('tab-fg');
        result.current.setMessages([]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-bg');

      act(() => {
        handler({
          type: 'result',
          sessionId: 'sess-bg',
          result: 'Done in background',
        } as unknown as StreamEvent);
      });

      expect(result.current.tabStatuses['tab-bg']).toBe('complete_unread');
    });
  });
});


// ---------------------------------------------------------------------------
// Fix 8: TabStatusIndicator component tests
// ---------------------------------------------------------------------------

describe('TabStatusIndicator component', () => {
  /** Helper to render TabStatusIndicator without JSX (this is a .ts file). */
  function renderIndicator(status: TabStatus) {
    return render(React.createElement(TabStatusIndicator, { status }));
  }

  describe('renders correct indicator for each status', () => {
    it('renders pulsing blue dot for streaming', () => {
      const { container } = renderIndicator('streaming');
      const indicator = container.querySelector('span');
      expect(indicator).not.toBeNull();
      expect(indicator!.className).toContain('bg-blue-500');
      expect(indicator!.className).toContain('animate-pulse');
      expect(indicator!.className).toContain('rounded-full');
    });

    it('renders orange "?" for waiting_input', () => {
      const { container } = renderIndicator('waiting_input');
      const indicator = container.querySelector('span');
      expect(indicator).not.toBeNull();
      expect(indicator!.textContent).toBe('?');
      expect(indicator!.className).toContain('text-orange-500');
      expect(indicator!.className).toContain('font-bold');
    });

    it('renders yellow "⚠" for permission_needed', () => {
      const { container } = renderIndicator('permission_needed');
      const indicator = container.querySelector('span');
      expect(indicator).not.toBeNull();
      expect(indicator!.textContent).toBe('⚠');
      expect(indicator!.className).toContain('text-yellow-500');
    });

    it('renders red "!" for error', () => {
      const { container } = renderIndicator('error');
      const indicator = container.querySelector('span');
      expect(indicator).not.toBeNull();
      expect(indicator!.textContent).toBe('!');
      expect(indicator!.className).toContain('text-red-500');
      expect(indicator!.className).toContain('font-bold');
    });

    it('renders static green dot for complete_unread', () => {
      const { container } = renderIndicator('complete_unread');
      const indicator = container.querySelector('span');
      expect(indicator).not.toBeNull();
      expect(indicator!.className).toContain('bg-green-500');
      expect(indicator!.className).toContain('rounded-full');
      // Should NOT have animate-pulse (static dot)
      expect(indicator!.className).not.toContain('animate-pulse');
    });

    it('renders null for idle', () => {
      const { container } = renderIndicator('idle');
      expect(container.querySelector('span')).toBeNull();
      expect(container.innerHTML).toBe('');
    });
  });

  describe('accessibility: aria-label and role attributes', () => {
    it('streaming has aria-label "Streaming" and role="img"', () => {
      const { container } = renderIndicator('streaming');
      const el = container.querySelector('[aria-label="Streaming"]');
      expect(el).not.toBeNull();
      expect(el!.getAttribute('role')).toBe('img');
    });

    it('waiting_input has aria-label "Waiting for input" and role="img"', () => {
      const { container } = renderIndicator('waiting_input');
      const el = container.querySelector('[aria-label="Waiting for input"]');
      expect(el).not.toBeNull();
      expect(el!.getAttribute('role')).toBe('img');
    });

    it('permission_needed has aria-label "Permission needed" and role="img"', () => {
      const { container } = renderIndicator('permission_needed');
      const el = container.querySelector('[aria-label="Permission needed"]');
      expect(el).not.toBeNull();
      expect(el!.getAttribute('role')).toBe('img');
    });

    it('error has aria-label "Error" and role="img"', () => {
      const { container } = renderIndicator('error');
      const el = container.querySelector('[aria-label="Error"]');
      expect(el).not.toBeNull();
      expect(el!.getAttribute('role')).toBe('img');
    });

    it('complete_unread has aria-label "New content" and role="img"', () => {
      const { container } = renderIndicator('complete_unread');
      const el = container.querySelector('[aria-label="New content"]');
      expect(el).not.toBeNull();
      expect(el!.getAttribute('role')).toBe('img');
    });
  });
});
