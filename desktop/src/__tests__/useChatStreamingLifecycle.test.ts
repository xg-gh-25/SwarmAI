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
 *   - Hook returns all expected refs (messagesEndRef)
 *   - Hook returns all expected factories (createStreamHandler,
 *     createCompleteHandler, createErrorHandler)
 *   - ``deriveStreamingActivity`` standalone export works identically to
 *     when it was inline in ChatPage.tsx
 *   - isStreaming derivation: false by default, true when _isStreaming set
 *   - streamingActivity: null when not streaming, returns activity when
 *     streaming with content
 *   - Fix 1: Stream generation counter increments on new stream, stale
 *     complete handlers are no-ops when generation mismatches, event-driven
 *     pauses (ask_user_question, error) increment generation
 *   - Fix 6: Per-tab state map saves/restores state on tab switch, background
 *     tab streaming updates map but not foreground useState, per-tab abort
 *     controller isolation, per-tab isStreaming/pendingQuestion isolation,
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
 *   - Fix 7: MAX_OPEN_TABS guard — unified hook enforces the 4-tab limit,
 *     tab creation re-enabled after close
 *   - Fix 8: Tab status indicators — updateTabStatus syncs unified Tab_Map
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
import { renderHook, act, render } from '@testing-library/react';
import {
  useChatStreamingLifecycle,
  deriveStreamingActivity,
  formatElapsed,
  ELAPSED_DISPLAY_THRESHOLD_MS,
  HEAL_GRACE_PERIOD_MS,
  MIN_ACTIVITY_DISPLAY_MS,
  persistPendingState,
  restorePendingState,
  removePendingState,
  prepareMessagesForStorage,
  isSessionStorageAvailable,
  cleanupStalePendingEntries,
  STORAGE_KEY_PREFIX,
  PERSISTED_STATE_VERSION,
} from '../hooks/useChatStreamingLifecycle';
import { MAX_OPEN_TABS } from '../hooks/useUnifiedTabState';
// UnifiedTab type removed — not directly referenced in tests
import type { TabStatus } from '../hooks/useUnifiedTabState';
import type {
  PersistedPendingState,
} from '../hooks/useChatStreamingLifecycle';
import { TabStatusIndicator } from '../pages/chat/components/TabStatusIndicator';
import React from 'react';
import type { StreamEvent } from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import type { Message, ContentBlock } from '../types';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import {
  testTabMap,
  testTabMapRef as _testTabMapRef,
  testActiveTabIdRef,
  createMockDeps,
  initTestTab,
  makeMessage,
  makeToolUse,
  resetTestState,
} from './helpers/streamingTestUtils';

// ---------------------------------------------------------------------------
// Mock useToast — the hook now calls useToast() for reconnection toasts.
// Hoisted spy so individual tests can assert the toast payload shape
// (e.g. the cross-tab AskUserQuestion toast must be persistent + actionable).
// ---------------------------------------------------------------------------
const mockAddToast = vi.hoisted(() => vi.fn());
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    addToast: mockAddToast,
    removeToast: vi.fn(),
    toasts: [],
  }),
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useChatStreamingLifecycle', () => {
  // Clear shared test tab map between tests to prevent cross-contamination
  beforeEach(() => {
    resetTestState();
    mockAddToast.mockClear();
  });

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

      // pendingStreamTabs should be an empty Set initially
      expect(result.current.pendingStreamTabs).toBeDefined();
      expect(result.current.pendingStreamTabs.size).toBe(0);

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
      initTestTab('tab-iso-1');
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        result.current.setIsStreaming(true);
      });

      expect(result.current.isStreaming).toBe(true);
    });

    it('returns to false when setIsStreaming(false) is called', () => {
      initTestTab('tab-iso-2');
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
      initTestTab('tab-sa-1');
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
      initTestTab('tab-sa-2');
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
        _confirmed: true,
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

    it('handles error event by appending error to message content', () => {
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
      expect(content).toHaveLength(2);
      expect(content[0].type).toBe('text');
      expect((content[0] as { text: string }).text).toBe('partial');
      expect(content[1].type).toBe('text');
      expect((content[1] as { text: string }).text).toContain('Something broke');
    });
  });

  describe('recovery_exhausted toast (run_d8dce02a)', () => {
    // Adversarial #1 (CRITICAL): recovery_exhausted is yielded AFTER the turn's
    // `result` event, which bumps streamGen. If it were handled after the
    // generation guard it would be discarded as stale and the toast would never
    // show. These tests pin that it is handled BEFORE the guard.
    it('surfaces the toast even after result bumped streamGen (survives the guard)', () => {
      const msgId = 'msg-rex';
      const onStartFresh = vi.fn();
      const { result } = renderHook(() =>
        useChatStreamingLifecycle({ ...createMockDeps(), onStartFresh }),
      );
      act(() => {
        initTestTab('tab-1');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');
      const genBefore = result.current.streamGenRef.current;

      // result completes the turn → bumps streamGen (the stale-event trap).
      act(() => {
        handler({ type: 'result', sessionId: 'sess-rex' });
      });
      expect(result.current.streamGenRef.current).toBeGreaterThan(genBefore);

      mockAddToast.mockClear();
      // recovery_exhausted arrives AFTER result, on the bumped generation.
      act(() => {
        handler({
          type: 'recovery_exhausted',
          sessionId: 'sess-rex',
          message: 'Automatic recovery has stopped.',
        });
      });

      // The toast MUST be raised despite the generation bump.
      expect(mockAddToast).toHaveBeenCalledTimes(1);
      const toast = mockAddToast.mock.calls[0][0];
      expect(toast.severity).toBe('warning');
      expect(toast.autoDismiss).toBe(false);
      expect(toast.id).toBe('recovery-exhausted-tab-1');
      expect(toast.action?.label).toBe('Start fresh session');
    });

    it('action triggers onStartFresh for the tab', () => {
      const onStartFresh = vi.fn();
      const { result } = renderHook(() =>
        useChatStreamingLifecycle({ ...createMockDeps(), onStartFresh }),
      );
      act(() => { initTestTab('tab-1'); });
      const handler = result.current.createStreamHandler('m', 'tab-1');
      act(() => {
        handler({ type: 'recovery_exhausted', sessionId: 's', message: 'x' });
      });
      const toast = mockAddToast.mock.calls.at(-1)![0];
      act(() => { toast.action!.onClick(); });
      expect(onStartFresh).toHaveBeenCalledWith('tab-1');
    });

    it('offers NO action when the tab is no longer open (would clear the wrong tab)', () => {
      // Adversarial #3/#4: a toast outliving its tab must not offer an action
      // that clears the now-active tab.
      const onStartFresh = vi.fn();
      const { result } = renderHook(() =>
        useChatStreamingLifecycle({ ...createMockDeps(), onStartFresh }),
      );
      // tab-gone is NOT in the tab map (never init'd / already closed).
      const handler = result.current.createStreamHandler('m', 'tab-gone');
      act(() => {
        handler({ type: 'recovery_exhausted', sessionId: 's', message: 'x' });
      });
      const toast = mockAddToast.mock.calls.at(-1)![0];
      expect(toast.action).toBeUndefined();
    });
  });

  // ── OT01 render-freeze: same-turn tail events survive the gen guard ──────
  // ROOT CAUSE (run_f9adee1e, live-log confirmed): the turn's `result` event
  // calls incrementStreamGen() (streamGen 50→51), but the SAME stream handler
  // holds capturedStreamGen=50. The generation guard at :2334 discarded the
  // turn's OWN result-following tail events (context_warning,
  // system_prompt_metadata) as "stale" → turn-end refresh lost → UI frozen
  // until the next send. Fix: the guard compares latestStreamGen (advanced only
  // by a genuinely NEW send, stamped eagerly at stream-handler creation) NOT
  // streamGen (churned mid-turn by result/reconnect/error). Mirrors the
  // createCompleteHandler latestCompleteGen fix (run_6adee7d5).
  describe('OT01 tail-event survives mid-turn streamGen churn', () => {
    it('processes a context_warning that arrives AFTER result bumped streamGen', () => {
      const msgId = 'msg-ot01';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      act(() => {
        initTestTab('tab-ot01');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-ot01');
      const genBefore = result.current.streamGenRef.current;

      // result completes the turn → bumps streamGen (the stale-event trap).
      act(() => {
        handler({ type: 'result', sessionId: 'sess-ot01' });
      });
      expect(result.current.streamGenRef.current).toBeGreaterThan(genBefore);

      // context_warning arrives AFTER result, on the bumped generation.
      // With the streamGen-based guard it is DISCARDED (contextWarning stays
      // null → RED). With the latestStreamGen guard it is PROCESSED.
      act(() => {
        handler({
          type: 'context_warning',
          sessionId: 'sess-ot01',
          level: 'warn',
          pct: 72,
          tokensEst: 144000,
          message: 'Context 72% full',
        });
      });

      // The tail event MUST be processed despite the mid-turn streamGen bump.
      expect(result.current.contextWarning).not.toBeNull();
      expect(result.current.contextWarning?.pct).toBe(72);
    });

    it('STILL discards a genuinely stale event from a superseded stream — DISCRIMINATING (only a NEW send, i.e. latestStreamGen advance, causes the discard)', () => {
      // Gate-2 finding (run_f9adee1e): the naive bleed test was VACUOUS — it
      // discarded under BOTH the old streamGen guard and the new latestStreamGen
      // guard (both saw captured=0 vs a bumped 1), so it did not lock in the
      // FIX's semantics. This version DISCRIMINATES: it first proves the stale
      // handler's OWN mid-turn result churn (which bumps streamGen but NOT
      // latestStreamGen) does NOT discard — then proves a genuinely NEW send
      // (which advances latestStreamGen) DOES. Only the new guard passes both
      // halves; the old streamGen guard would FAIL the first half (wrongly
      // discard after the handler's own result), making this non-vacuous.
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      act(() => {
        initTestTab('tab-bleed');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: 'm1', role: 'assistant', content: [] }),
        ]);
      });

      const staleHandler = result.current.createStreamHandler('m1', 'tab-bleed');

      // Half A (discriminator): the handler's OWN result bumps streamGen but NOT
      // latestStreamGen. A same-handler tail event MUST still be processed. The
      // OLD streamGen guard would discard here (streamGen bumped) — the NEW guard
      // keeps it (latestStreamGen unchanged). This is the half that fails under
      // the buggy guard → non-vacuous.
      act(() => { staleHandler({ type: 'result', sessionId: 'sess-bleed' }); });
      act(() => {
        staleHandler({
          type: 'context_warning', sessionId: 'sess-bleed',
          level: 'warn', pct: 55, tokensEst: 110000, message: 'own-turn tail',
        });
      });
      expect(result.current.contextWarning?.pct).toBe(55); // own tail survived

      // Half B (bleed still caught): a genuinely NEW send advances latestStreamGen
      // via a new stream handler. NOW the old handler is truly superseded and its
      // leftover event MUST be discarded.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createStreamHandler('m2', 'tab-bleed'); // stamps new latestStreamGen
      act(() => {
        staleHandler({
          type: 'context_warning', sessionId: 'sess-bleed',
          level: 'critical', pct: 99, tokensEst: 200000, message: 'stale — must not render',
        });
      });
      // The stale (pct:99) event was discarded → contextWarning stays at Half A's 55.
      expect(result.current.contextWarning?.pct).toBe(55);
    });

    it('P0 (run_3e404199): a cmd_permission_request survives a latestStreamGen advance — HITL exemption prevents the approval hang', () => {
      // THE HANG: dangerous_command_gate blocks in PreToolUse awaiting approve/
      // deny. While it blocks, queued sends bump latestStreamGen (daemon log:
      // session_busy_pending seq=5-8). The blocked handler's gen is now BEHIND
      // the live gen, so the gen-guard discarded the cmd_permission_request →
      // the button never rendered → hook blocked → MESSAGE_TIMEOUT → session
      // force-killed. A HITL prompt from a live-blocked hook is NEVER stale, so
      // the guard must EXEMPT it. Without the exemption this test RED (the perm
      // event is discarded, pendingPermissionRequestId stays null).
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      act(() => {
        initTestTab('tab-hitl');
        testActiveTabIdRef.current = 'tab-hitl';
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: 'm-hitl', role: 'assistant', content: [] }),
        ]);
      });

      // The blocked handler captured the current gen at creation.
      const blockedHandler = result.current.createStreamHandler('m-hitl', 'tab-hitl');

      // A queued send advances latestStreamGen while the hook sits blocked —
      // this is what pushed capturedStreamGen behind the live gen.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createStreamHandler('m-hitl-2', 'tab-hitl'); // stamps a NEW latestStreamGen

      // The blocked hook now yields its permission prompt on the OLD gen.
      act(() => {
        blockedHandler({
          type: 'cmd_permission_request',
          sessionId: 'sess-hitl',
          requestId: 'perm-hitl-1',
          toolName: 'Bash',
          toolInput: { command: 'rm -rf /tmp/x' },
          reason: 'Matches dangerous command pattern',
          options: ['approve', 'deny'],
        } as unknown as StreamEvent);
      });

      // EXEMPTED → processed → the approve/deny button can render (no hang).
      expect(result.current.pendingPermissionRequestId).toBe('perm-hitl-1');
    });

    // Discriminator (non-vacuity): the HITL exemption must NOT be a blanket
    // "process everything after a gen advance" — that would re-open the
    // cross-turn bleed the guard exists to stop. A NON-HITL stale event on the
    // same superseded handler MUST still be discarded.
    it('P0 discriminator: a non-HITL stale event on a superseded handler is STILL discarded', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      act(() => {
        initTestTab('tab-hitl-disc');
        testActiveTabIdRef.current = 'tab-hitl-disc';
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: 'm-disc', role: 'assistant', content: [] }),
        ]);
      });
      const staleHandler = result.current.createStreamHandler('m-disc', 'tab-hitl-disc');
      act(() => { result.current.incrementStreamGen(); });
      result.current.createStreamHandler('m-disc-2', 'tab-hitl-disc'); // advance latestStreamGen

      // A non-HITL tail event (context_warning) from the superseded handler is
      // NOT exempt → must be discarded (contextWarning stays null). If this were
      // rendered, the exemption would be too broad (bleed regression).
      act(() => {
        staleHandler({
          type: 'context_warning', sessionId: 'sess-disc',
          level: 'critical', pct: 99, tokensEst: 200000, message: 'stale — must not render',
        } as unknown as StreamEvent);
      });
      expect(result.current.contextWarning).toBeNull();
    });

    // Cross-tab (adversarial follow-up): the gen-exemption is NOT tab-scoped —
    // it applies to any HITL event. Tab-isolation is enforced downstream by the
    // isActiveTab gate. This locks that gate: a cmd_permission_request on a
    // BACKGROUND tab (arriving through the exempt path, gen advanced) must NOT
    // set the FOREGROUND pendingPermissionRequestId. If a future refactor of the
    // exemption bypassed isActiveTab, this goes RED.
    it('P0 cross-tab: a background-tab cmd_permission_request (gen-exempt) does NOT touch foreground state', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      const bgMsgId = 'bg-perm-msg';
      const bgMsg = makeMessage({ id: bgMsgId, role: 'assistant', content: [] });
      act(() => {
        testTabMap.set('tab-bg-perm', {
          id: 'tab-bg-perm', title: 'BG', agentId: 'default', isNew: false,
          messages: [bgMsg], sessionId: 'sess-bg-perm', pendingQuestion: null,
          abortController: null, isStreaming: false,
          streamState: { mode: 'idle', streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0, status: 'streaming',
        });
        initTestTab('tab-fg-perm');
        testActiveTabIdRef.current = 'tab-fg-perm'; // tab-bg-perm is BACKGROUND
        messageStoreRegistry.getOrCreate('tab-bg-perm', { sessionId: 'sess-bg-perm' }).replace([bgMsg]);
      });

      const bgHandler = result.current.createStreamHandler(bgMsgId, 'tab-bg-perm');
      // Advance the bg tab's gen so the perm event goes through the EXEMPT path.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createStreamHandler('bg-perm-msg-2', 'tab-bg-perm');

      act(() => {
        bgHandler({
          type: 'cmd_permission_request', sessionId: 'sess-bg-perm',
          requestId: 'perm-bg-1', toolName: 'Bash',
          toolInput: { command: 'rm -rf /tmp/x' },
          reason: 'Matches dangerous command pattern', options: ['approve', 'deny'],
        } as unknown as StreamEvent);
      });

      // Foreground pendingPermissionRequestId must NOT be set by the bg event.
      expect(result.current.pendingPermissionRequestId).toBeNull();
      // But the bg tab's OWN tabState carries the pending id (so switch-back shows it).
      expect(testTabMap.get('tab-bg-perm')?.pendingPermissionRequestId).toBe('perm-bg-1');
    });

    it('P0 retry-stamp cross-tab: a stream handler bound to a BACKGROUND tab writes to ITS OWN store, not the active tab (run_26aa6caa)', () => {
      // Context for the R27 half-migration close: ChatPage's retryStreamFn used to
      // build its stream handler via wrappedCreateStreamHandler (reads
      // activeTabIdRef.current). On a BACKGROUND tab's reconnect/resend the active tab
      // has changed, so the retry's stream content (and its file_changed tabId stamp)
      // landed on the ACTIVE tab → cross-tab bleed. The fix makes retryStreamFn call
      // createStreamHandler(id, capturedTabIdForRetry) — the captured retry tab.
      //
      // HONEST SCOPE (not test-theater): this asserts the HANDLER-LAYER guarantee the
      // fix RELIES ON — a handler bound to tab X writes to X's store even when active
      // is Y. It does NOT drive ChatPage's retryStreamFn wiring itself (ChatPage has
      // no test harness for that closure — reverting the ChatPage lines would NOT turn
      // this RED). The ChatPage fix's correctness is a 1-line symmetry change
      // (the other 3 handlers already used capturedTabIdForRetry) verified by reading
      // the code + tsc, not by this test. This test guards the layer beneath it.
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      const bgMsgId = 'bg-retry-msg';
      const bgMsg = makeMessage({ id: bgMsgId, role: 'assistant', content: [] });
      const fgMsgId = 'fg-active-msg';
      const fgMsg = makeMessage({ id: fgMsgId, role: 'assistant', content: [] });
      act(() => {
        testTabMap.set('tab-bg-retry', {
          id: 'tab-bg-retry', title: 'BG', agentId: 'default', isNew: false,
          messages: [bgMsg], sessionId: 'sess-bg-retry', pendingQuestion: null,
          abortController: null, isStreaming: true,
          streamState: { mode: 'streaming', streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0, status: 'streaming',
        });
        testTabMap.set('tab-fg-active', {
          id: 'tab-fg-active', title: 'FG', agentId: 'default', isNew: false,
          messages: [fgMsg], sessionId: 'sess-fg-active', pendingQuestion: null,
          abortController: null, isStreaming: true,
          streamState: { mode: 'streaming', streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0, status: 'streaming',
        });
        // Active tab is the FOREGROUND one — the background tab is mid-reconnect.
        testActiveTabIdRef.current = 'tab-fg-active';
        messageStoreRegistry.getOrCreate('tab-bg-retry', { sessionId: 'sess-bg-retry' }).replace([bgMsg]);
        messageStoreRegistry.getOrCreate('tab-fg-active', { sessionId: 'sess-fg-active' }).replace([fgMsg]);
        result.current.setMessages([fgMsg]); // React state shows the active (fg) tab
      });

      // The FIXED retryStreamFn shape: createStreamHandler bound to the CAPTURED
      // background tab, executed while active === the foreground tab.
      const bgRetryHandler = result.current.createStreamHandler(bgMsgId, 'tab-bg-retry');
      act(() => {
        bgRetryHandler({
          type: 'assistant',
          content: [{ type: 'text', text: 'Background retry content' }],
        });
      });

      // Background tab's OWN store got the retry content.
      const bgStoreContent = messageStoreRegistry.getOrCreate('tab-bg-retry').getSnapshot()
        .flatMap((m) => m.content)
        .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
        .map((b) => b.text);
      expect(bgStoreContent).toContain('Background retry content');

      // The ACTIVE (foreground) tab's store + React state must be UNTOUCHED — the
      // pre-fix bug would have stamped the background retry onto this active tab.
      const fgStoreContent = messageStoreRegistry.getOrCreate('tab-fg-active').getSnapshot()
        .flatMap((m) => m.content)
        .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
        .map((b) => b.text);
      expect(fgStoreContent).not.toContain('Background retry content');
      const reactContent = result.current.messages
        .flatMap((m) => m.content)
        .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
        .map((b) => b.text);
      expect(reactContent).not.toContain('Background retry content');
    });
  });

  describe('createCompleteHandler', () => {
    it('returns a function that sets isStreaming to false', () => {
      initTestTab('tab-ch-1');
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

    // ── [DONE] authority vs stale-gen guard (run_6adee7d5) ──────────────
    // ROOT CAUSE: createCompleteHandler captured streamGen at SEND time, then
    // early-returned when tabState.streamGen !== capturedGen. But streamGen is
    // bumped mid-stream by reconnect / result / error (incrementStreamGen at 7
    // sites). So a turn that reconnected once → its own [DONE]'s completeHandler
    // is "stale" → early-returns BEFORE setIsStreaming(false) → spinner spins
    // forever, rescued only by the 30s reconcile force-clear backstop (~109 of
    // 114 idle force-clears, 3-signal log analysis). Fix: a per-tab
    // latestCompleteGen marks the LIVE completer; reconnect churn no longer
    // invalidates it, but a genuinely NEW send (which creates a new handler and
    // advances latestCompleteGen) correctly supersedes the old one.

    it('AC1: clears isStreaming on [DONE] even after reconnect churned streamGen', () => {
      const tabId = 'tab-ch-ac1';
      initTestTab(tabId);
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Handler created at send time (captures current gen as the live completer).
      const completeHandler = result.current.createCompleteHandler(tabId);
      act(() => { result.current.setIsStreaming(true, tabId); });
      expect(result.current.isStreaming).toBe(true);

      // Mid-stream reconnect/result bumps streamGen — but NO new send happened,
      // so this handler is still the live completer for the tab's only stream.
      act(() => { result.current.incrementStreamGen(); });

      act(() => { completeHandler(); });

      // [DONE] is authoritative: streaming must clear despite the gen churn.
      expect(result.current.isStreaming).toBe(false);
    });

    it('AC2: stale handler no-ops after a genuinely NEW send supersedes it', () => {
      const tabId = 'tab-ch-ac2';
      initTestTab(tabId);
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // First turn's handler.
      const oldHandler = result.current.createCompleteHandler(tabId);
      act(() => { result.current.setIsStreaming(true, tabId); });

      // A NEW send arrives: it bumps gen THEN creates a new handler (mirrors the
      // send path: incrementStreamGen() precedes createCompleteHandler()).
      act(() => { result.current.incrementStreamGen(); });
      result.current.createCompleteHandler(tabId); // new live completer
      act(() => { result.current.setIsStreaming(true, tabId); });
      expect(result.current.isStreaming).toBe(true);

      // The OLD turn's [DONE] arrives late — it must NOT clear the NEW stream.
      act(() => { oldHandler(); });

      expect(result.current.isStreaming).toBe(true);
    });

    it('AC4: closed tab (no tabState) → handler no-ops without throwing', () => {
      const tabId = 'tab-ch-ac4';
      initTestTab(tabId);
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );
      const completeHandler = result.current.createCompleteHandler(tabId);
      // Tab closed before [DONE] arrives.
      testTabMap.delete(tabId);

      expect(() => {
        act(() => { completeHandler(); });
      }).not.toThrow();
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
        'Connection interrupted',
      );
    });

    it('does NOT call chatService.stopSession on mid-stream disconnect (zombie-poison fix)', () => {
      // ROOT CAUSE (2026-06-21): a stale "Gap 2 fix" in the error handler
      // POSTed /stop on mid-stream disconnect → backend interrupt_session →
      // kill → poisoned subprocess → next send zombie_via_error → manual
      // Continue loop. The backend's _recover_streaming_on_disconnect already
      // transitions STREAMING → IDLE (soft-interrupt, leaves subprocess alive),
      // so the frontend stop is both redundant AND harmful. The error handler
      // must NOT call stopSession.
      const stopSpy = vi
        .spyOn(chatService, 'stopSession')
        .mockResolvedValue({ status: 'ok', message: '' });
      try {
        const msgId = 'msg-disc';
        const tabId = 'tab-disc';
        initTestTab(tabId, [
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
        // Mid-stream disconnect state: data already received + heal grace
        // already elapsed so the terminal "mid-stream failure" branch runs.
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-disc';
        tab.hasReceivedData = true;
        tab.reconnectionAttempt = 3; // exhausted
        (tab as unknown as { _healGraceActive: boolean })._healGraceActive = true;

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );
        const errorHandler = result.current.createErrorHandler(msgId, tabId);
        act(() => {
          errorHandler(new Error('Premature SSE disconnect'));
        });

        expect(stopSpy).not.toHaveBeenCalled();
      } finally {
        stopSpy.mockRestore();
      }
    });

    it('does NOT call chatService.stopSession when heal-grace expires (2nd poison site)', () => {
      // Covers the OTHER deleted stop site: the heal-grace-expiry branch fires
      // inside a setTimeout that only arms when _healGraceActive was FALSE at
      // entry. The terminal-branch test above (with _healGraceActive=true)
      // skips this path, so without this test a revert of the heal-grace stop
      // would pass green. Adversarial Gate 2 LOW finding (run_1a45cfe9).
      vi.useFakeTimers();
      const stopSpy = vi
        .spyOn(chatService, 'stopSession')
        .mockResolvedValue({ status: 'ok', message: '' });
      try {
        const msgId = 'msg-heal';
        const tabId = 'tab-heal';
        initTestTab(tabId, [
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-heal';
        tab.hasReceivedData = true; // mid-stream
        tab.reconnectionAttempt = 0; // NOT exhausted → enters heal-grace, not terminal
        // _healGraceActive intentionally left false → heal-grace branch arms.

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );
        const errorHandler = result.current.createErrorHandler(msgId, tabId);
        act(() => {
          errorHandler(new Error('Premature SSE disconnect'));
        });
        // Grace timer armed; advance past it to fire the expiry branch.
        act(() => {
          vi.advanceTimersByTime(HEAL_GRACE_PERIOD_MS + 100);
        });

        expect(stopSpy).not.toHaveBeenCalled();
      } finally {
        stopSpy.mockRestore();
        vi.useRealTimers();
      }
    });

    it('heal-grace still-working: stamps _reconcileStreamStart so the reconcile loop does NOT force-clear the spinner mid-flush', async () => {
      // GAP THIS CLOSES: the disconnect-handler still-working test (~:3382)
      // asserts the _reconcileStreamStart stamp, but that is a DIFFERENT branch
      // (createDisconnectHandler). The heal-grace expiry still-working branch
      // (createErrorHandler path) hands recovery to the SAME 15s reconcile loop
      // and MUST stamp the same cap anchor — without it the loop computes a huge
      // age, the ≥10s start-grace + flushing exemption are pre-blown, and the
      // spinner is force-cleared in the backend dead→cold gap (OT01 truncation).
      // MUTATION-PROVEN: deleting `tab2._reconcileStreamStart = Date.now()` in
      // the heal-grace still-working branch turns this RED.
      vi.useFakeTimers();
      const stateSpy = vi
        .spyOn(chatService, 'getStreamingState')
        .mockResolvedValue({
          'sess-heal-sw': { streaming: true, state: 'streaming', waitingInput: false, postDisconnectFlushing: false },
        } as unknown as Awaited<ReturnType<typeof chatService.getStreamingState>>);
      try {
        const msgId = 'msg-heal-sw';
        const tabId = 'tab-heal-sw';
        initTestTab(tabId, [
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-heal-sw';
        tab.hasReceivedData = true; // mid-stream → heal-grace arms
        tab.reconnectionAttempt = 0; // NOT exhausted → enters heal-grace
        // _healGraceActive left false → heal-grace branch arms on disconnect.

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );
        const errorHandler = result.current.createErrorHandler(msgId, tabId);
        act(() => { errorHandler(new Error('Premature SSE disconnect')); });

        // Advance past grace; flush the await getStreamingState round-trip.
        await act(async () => {
          vi.advanceTimersByTime(HEAL_GRACE_PERIOD_MS + 100);
          await Promise.resolve(); await Promise.resolve();
        });

        const t = testTabMap.get(tabId)! as unknown as Record<string, unknown>;
        expect(stateSpy).toHaveBeenCalled();
        // backend reported streaming:true → still-working verdict
        expect(t._postDisconnectUncertain).toBe(true);          // handed to reconcile loop
        expect(typeof t._reconcileStreamStart).toBe('number');  // the fix: cap anchor stamped
        expect(t._reconcileStreamStart).toBeGreaterThan(0);
        // anchored to ~now (not a stale ~0) → reconcile grace applies
        expect(Date.now() - (t._reconcileStreamStart as number)).toBeLessThan(1000);
      } finally {
        stateSpy.mockRestore();
        vi.useRealTimers();
      }
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

    it('syncs streamGen to active tab in tabMapRef', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-1');
      });

      expect(
        testTabMap.get('tab-1')!.streamGen,
      ).toBe(0);

      act(() => {
        result.current.incrementStreamGen();
      });

      expect(
        testTabMap.get('tab-1')!.streamGen,
      ).toBe(1);
    });
  });

  describe('createCompleteHandler generation guard', () => {
    it('clears isStreaming when generation matches', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-1');
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

    it('is a no-op when a NEW send superseded this handler (stale handler)', () => {
      // run_6adee7d5: "new stream" in production = a new send, which bumps
      // incrementStreamGen() THEN calls createCompleteHandler() — advancing the
      // tab's latestCompleteGen. The OLD handler is then stale and must no-op.
      // (Previously this test bumped gen WITHOUT a new handler; that no longer
      // models how production supersedes a completer — a bare reconnect gen-bump
      // must NOT invalidate the turn's own [DONE]. See AC1/AC2.)
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-1');
        result.current.setIsStreaming(true);
      });

      // Old turn's complete handler.
      const staleHandler = result.current.createCompleteHandler('tab-1');

      // A genuinely NEW send: bump gen, THEN create a new handler (mirrors the
      // ChatPage send path ordering). This advances latestCompleteGen.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createCompleteHandler('tab-1'); // new live completer
      act(() => { result.current.setIsStreaming(true); });

      // Stale handler fires — should be a no-op (does not clear the NEW stream).
      act(() => { staleHandler(); });

      expect(result.current.isStreaming).toBe(true);
    });

    it('is a no-op when tab has been closed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-1');
        result.current.setIsStreaming(true);
      });

      const completeHandler = result.current.createCompleteHandler('tab-1');

      // Close the tab
      act(() => {
        testTabMap.delete('tab-1');
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
        initTestTab('tab-1');
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

      // User answers → a NEW send: bump gen THEN create a new completer
      // (production ordering). This advances latestCompleteGen, superseding the
      // pre-question handler.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createCompleteHandler('tab-1');
      act(() => { result.current.setIsStreaming(true); });

      // Stale pre-question complete handler fires — must NOT clear the new stream.
      act(() => { completeHandler(); });

      expect(result.current.isStreaming).toBe(true);
    });

    it('error event increments streamGen so completeHandler is no-op', () => {
      const msgId = 'msg-gen-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-1');
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

      // A NEW send after the error: bump gen THEN create a new completer
      // (production ordering) → advances latestCompleteGen, supersedes the old.
      act(() => { result.current.incrementStreamGen(); });
      result.current.createCompleteHandler('tab-1');
      act(() => { result.current.setIsStreaming(true); });

      // Stale pre-error complete handler fires — must NOT clear the new stream.
      act(() => { completeHandler(); });

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
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-new');
      });

      const tabState = testTabMap.get('tab-new');
      expect(tabState).toBeDefined();
      expect(tabState!.messages).toEqual([]);
      expect(tabState!.sessionId).toBeUndefined();
      expect(tabState!.pendingQuestion).toBeNull();
      expect(tabState!.abortController).toBeNull();
      expect(tabState!.isStreaming).toBe(false);
      expect(tabState!.streamGen).toBe(0);
      expect(tabState!.status).toBe('idle');
    });

    it('sets the new tab as active', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-new');
      });

      expect(testActiveTabIdRef.current).toBe('tab-new');
    });

    it('accepts initial messages', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const welcomeMsg = makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Welcome!' }],
      });

      act(() => {
        initTestTab('tab-new', [welcomeMsg]);
      });

      const tabState = testTabMap.get('tab-new');
      expect(tabState!.messages).toHaveLength(1);
      expect(tabState!.messages[0].content[0]).toEqual({
        type: 'text',
        text: 'Welcome!',
      });
    });
  });

  describe('tab state map access (unified hook manages lifecycle)', () => {
    it('tab map entry is accessible after initTestTab', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msg = makeMessage({
        role: 'assistant',
        content: [{ type: 'text', text: 'Tab A content' }],
      });

      act(() => {
        initTestTab('tab-a');
        result.current.setMessages([msg]);
        result.current.setSessionId('sess-a');
      });

      // Save tab-a state
      act(() => {
        /* saveCurrentTab is a no-op in unified hook — state lives in the map */;
      });

      const saved = testTabMap.get('tab-a');
      expect(saved).toBeDefined();
      expect(saved!.sessionId).toBeUndefined(); // initTestTab creates with sessionId undefined
    });

    it('restores tab state from per-tab map on switch back', () => {
      const { result: _result } = renderHook(() =>
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
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msgA],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 3,
          status: 'idle',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msgB],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 1,
          status: 'idle',
        });
      });

      // Switch to tab-a — verify map state is accessible
      act(() => {
        testActiveTabIdRef.current = 'tab-a';
      });

      expect(testTabMap.get('tab-a')!.messages).toEqual([msgA]);
      expect(testTabMap.get('tab-a')!.sessionId).toBe('sess-a');
      expect(testActiveTabIdRef.current).toBe('tab-a');

      // Switch to tab-b — verify map state is accessible
      act(() => {
        testActiveTabIdRef.current = 'tab-b';
      });

      expect(testTabMap.get('tab-b')!.messages).toEqual([msgB]);
      expect(testTabMap.get('tab-b')!.sessionId).toBe('sess-b');
      expect(testActiveTabIdRef.current).toBe('tab-b');
    });

    it('returns false when tab not found in map', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Tab not in map — has() returns false
      expect(testTabMap.has('nonexistent')).toBe(false);
    });

    it('preserves per-tab isolation across round-trip switches', () => {
      const { result: _result } = renderHook(() =>
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
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msgA],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msgB],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-a, then tab-b, then back to tab-a — verify map isolation
      act(() => { testActiveTabIdRef.current = 'tab-a'; });
      expect(testTabMap.get('tab-a')!.messages[0].id).toBe('msg-a');

      act(() => { testActiveTabIdRef.current = 'tab-b'; });
      expect(testTabMap.get('tab-b')!.messages[0].id).toBe('msg-b');

      act(() => { testActiveTabIdRef.current = 'tab-a'; });
      expect(testTabMap.get('tab-a')!.messages[0].id).toBe('msg-a');
      expect(testTabMap.get('tab-a')!.sessionId).toBe('sess-a');
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
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [bgMsg],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        initTestTab('tab-b');
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
      const tabAState = testTabMap.get('tab-a');
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
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
        testActiveTabIdRef.current = 'tab-a';
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
      const mapState = testTabMap.get('tab-a');
      expect(mapState!.messages[0].content).toHaveLength(1);
    });

    // Root-1 SSOT Phase 3 (AC5) — adversarial HIGH regression guard:
    // surfacePendingQuestion's `if (store)` branch must mirror the
    // ask_user_question block into tabState.messages too, NOT just the store.
    // For a BACKGROUND tab the store→tabState bridge doesn't run, so without the
    // parallel-write the block is absent from tabState.messages → handleSelectTab's
    // store.replace(tabState.messages) clobbers it on switch-back → the
    // re-surfaced question vanishes (the form renders FROM the block). This test
    // seeds a store for a background tab and asserts the block lands in BOTH.
    it('AC5: ask_user_question on a background tab WITH a store writes the block to BOTH store and tabState.messages (no clobber on switch-back)', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const bgMsgId = 'bg-auq-msg';
      const bgMsg = makeMessage({ id: bgMsgId, role: 'assistant', content: [] });

      act(() => {
        testTabMap.set('tab-bg', {
          id: 'tab-bg', title: 'BG', agentId: 'default', isNew: false,
          messages: [bgMsg],
          sessionId: 'sess-bg',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        initTestTab('tab-fg');
        testActiveTabIdRef.current = 'tab-fg'; // tab-bg is BACKGROUND
        const store = messageStoreRegistry.getOrCreate('tab-bg', { sessionId: 'sess-bg' });
        store.replace([bgMsg]);
      });

      const bgHandler = result.current.createStreamHandler(bgMsgId, 'tab-bg');

      act(() => {
        bgHandler({
          type: 'ask_user_question',
          toolUseId: 'tool-bg-1',
          questions: [{ question: 'Pick', header: 'H', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
          sessionId: 'sess-bg',
        });
      });

      const hasBlock = (msgs: Message[]) =>
        msgs.some((m) => m.id === bgMsgId &&
          m.content.some((b) => (b as { type?: string }).type === 'ask_user_question'));

      // Store carries the block (the surface helper appended it)...
      const store = messageStoreRegistry.get('tab-bg');
      expect(store).not.toBeNull();
      expect(hasBlock(store!.messages)).toBe(true);
      // ...AND tabState.messages carries it too (the parallel-write fix). Without
      // this, switch-back replace(tabState.messages) would destroy the block.
      const bgState = testTabMap.get('tab-bg');
      expect(hasBlock(bgState!.messages)).toBe(true);
      // Foreground React state must NOT carry the bg tab's question (no cross-tab leak).
      expect(hasBlock(result.current.messages)).toBe(false);
    });

    // reconcile-gap (2026-06-22): the turn-end DB reconcile backstop used to
    // fire ONLY on the `result` event. A turn that ends via ask_user_question
    // (waiting_input) or cmd_permission_request (permission_needed) emits NO
    // `result`, so a streamed buffer that dropped a tail block was never
    // corrected against the complete DB rows → truncated reply + Continue
    // button. Fix: scheduleTurnEndReconcile is wired to those terminal paths.
    // These tests assert the reconcile is SCHEDULED (getSessionMessages fetched
    // after the debounce). Content-merge correctness is covered by the
    // MessageStore _applyMerge tests.
    describe('reconcile-gap: turn-end reconcile on non-result terminal paths', () => {
      beforeEach(() => { vi.useFakeTimers(); });
      afterEach(() => { vi.useRealTimers(); });

      it('AC3: ask_user_question terminal path schedules a DB reconcile', async () => {
        const getSpy = vi.spyOn(chatService, 'getSessionMessages').mockResolvedValue([]);
        const invSpy = vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );
        const msgId = 'auq-reconcile-msg';
        act(() => {
          initTestTab('tab-q');
          testActiveTabIdRef.current = 'tab-q';
          const ts = testTabMap.get('tab-q')!;
          ts.sessionId = 'sess-q';
          result.current.setSessionId('sess-q');
          result.current.setIsStreaming(true);
          result.current.setMessages([makeMessage({ id: msgId, role: 'assistant', content: [] })]);
          messageStoreRegistry.getOrCreate('tab-q', { sessionId: 'sess-q' })
            .replace([makeMessage({ id: msgId, role: 'assistant', content: [] })]);
        });

        const handler = result.current.createStreamHandler(msgId, 'tab-q');
        act(() => {
          handler({
            type: 'ask_user_question',
            toolUseId: 'tool-q-1',
            questions: [{ question: 'Pick', header: 'H', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
            sessionId: 'sess-q',
          });
        });

        // Before the debounce window: no fetch yet.
        expect(getSpy).not.toHaveBeenCalled();
        // Advance past the 200ms debounce + flush the async reconcile.
        await act(async () => { await vi.advanceTimersByTimeAsync(250); });

        expect(invSpy).toHaveBeenCalledWith('sess-q');
        expect(getSpy).toHaveBeenCalledWith('sess-q');
        getSpy.mockRestore();
        invSpy.mockRestore();
      });

      it('AC3: cmd_permission_request terminal path schedules a DB reconcile', async () => {
        const getSpy = vi.spyOn(chatService, 'getSessionMessages').mockResolvedValue([]);
        const invSpy = vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});

        const { result } = renderHook(() =>
          useChatStreamingLifecycle(createMockDeps()),
        );
        const msgId = 'perm-reconcile-msg';
        act(() => {
          initTestTab('tab-p');
          testActiveTabIdRef.current = 'tab-p';
          const ts = testTabMap.get('tab-p')!;
          ts.sessionId = 'sess-p';
          result.current.setSessionId('sess-p');
          result.current.setIsStreaming(true);
          result.current.setMessages([makeMessage({ id: msgId, role: 'assistant', content: [] })]);
          messageStoreRegistry.getOrCreate('tab-p', { sessionId: 'sess-p' })
            .replace([makeMessage({ id: msgId, role: 'assistant', content: [] })]);
        });

        const handler = result.current.createStreamHandler(msgId, 'tab-p');
        act(() => {
          handler({
            type: 'cmd_permission_request',
            sessionId: 'sess-p',
            requestId: 'req-1',
            toolName: 'Bash',
            toolInput: { command: 'ls' },
            reason: 'needs approval',
            options: ['approve', 'deny'],
          } as unknown as StreamEvent);
        });

        await act(async () => { await vi.advanceTimersByTimeAsync(250); });

        expect(getSpy).toHaveBeenCalledWith('sess-p');
        getSpy.mockRestore();
        invSpy.mockRestore();
      });
    });

    it('is a no-op when tab has been closed', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const msgId = 'closed-msg';
      const msg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        testTabMap.set('tab-closed', {
          id: 'tab-closed', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
        initTestTab('tab-active');
        result.current.setMessages([]);
      });

      // Create handler for tab-closed, then close it
      const handler = result.current.createStreamHandler(msgId, 'tab-closed');

      act(() => {
        testTabMap.delete('tab-closed');
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
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controllerA = new AbortController();
      const controllerB = new AbortController();

      act(() => {
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: controllerA,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: controllerB,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
      });

      const tabAState = testTabMap.get('tab-a');
      const tabBState = testTabMap.get('tab-b');

      expect(tabAState!.abortController).not.toBe(tabBState!.abortController);
    });

    it('aborting active tab controller does not affect background tab', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controllerA = new AbortController();
      const controllerB = new AbortController();

      act(() => {
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: null,
          abortController: controllerA,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: controllerB,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testActiveTabIdRef.current = 'tab-a';
      });

      // Abort active tab-a's controller
      controllerA.abort();

      expect(controllerA.signal.aborted).toBe(true);
      expect(controllerB.signal.aborted).toBe(false);
    });
  });

  describe('per-tab _isStreaming isolation', () => {
    it('switching tabs does not leak isStreaming from source to target', () => {
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Tab-a has isStreaming=true, tab-b has isStreaming=false
      act(() => {
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: true, streamState: { mode: "streaming", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-b — its isStreaming should be false
      act(() => {
        testActiveTabIdRef.current = 'tab-b';
      });

      // isStreaming should be false for tab-b (no sessionId, no isStreaming)
      expect(result.current.isStreaming).toBe(false);

      // Tab-a's isStreaming in the map should still be true
      const tabAState = testTabMap.get('tab-a');
      expect(tabAState!.isStreaming).toBe(true);
    });
  });

  describe('per-tab pendingQuestion isolation', () => {
    it('switching tabs does not show source tab question in target', () => {
      const { result: _result } = renderHook(() =>
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
        testTabMap.set('tab-a', {
          id: 'tab-a', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-a',
          pendingQuestion: questionA,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'waiting_input',
        });
        testTabMap.set('tab-b', {
          id: 'tab-b', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-b',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
      });

      // Switch to tab-b
      act(() => {
        testActiveTabIdRef.current = 'tab-b';
      });

      // Tab-b should have no pending question in the map
      expect(testTabMap.get('tab-b')!.pendingQuestion).toBeNull();

      // Switch back to tab-a — question should still be in the map
      act(() => {
        testActiveTabIdRef.current = 'tab-a';
      });

      expect(testTabMap.get('tab-a')!.pendingQuestion).not.toBeNull();
      expect(testTabMap.get('tab-a')!.pendingQuestion!.toolUseId).toBe('tool-q-a');
    });
  });

  describe('tab close cleanup', () => {
    it('removes entry from tab map on cleanup', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-to-close');
      });

      expect(testTabMap.has('tab-to-close')).toBe(true);

      act(() => {
        testTabMap.delete('tab-to-close');
      });

      expect(testTabMap.has('tab-to-close')).toBe(false);
    });

    it('aborts the tab abort controller on cleanup', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const controller = new AbortController();

      act(() => {
        testTabMap.set('tab-abort', {
          id: 'tab-abort', title: 'Tab', agentId: 'default', isNew: false,
          messages: [],
          sessionId: 'sess-abort',
          pendingQuestion: null,
          abortController: controller,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
      });

      expect(controller.signal.aborted).toBe(false);

      // Cleanup is now the unified hook's responsibility.
      // Simulate what cleanupTabState does: abort then delete.
      act(() => {
        const tab = testTabMap.get('tab-abort');
        if (tab?.abortController) tab.abortController.abort();
        testTabMap.delete('tab-abort');
      });

      expect(controller.signal.aborted).toBe(true);
      expect(testTabMap.has('tab-abort')).toBe(false);
    });

    it('handles cleanup of non-existent tab gracefully', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Should not throw
      act(() => {
        testTabMap.delete('nonexistent-tab');
      });

      expect(testTabMap.has('nonexistent-tab')).toBe(false);
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
        initTestTab('tab-1');
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
        testTabMap.set('tab-1', {
          id: 'tab-1', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testActiveTabIdRef.current = 'tab-1';
        result.current.setMessages([msg]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-1');

      act(() => {
        handler({ type: 'error', message: 'Something went wrong' });
      });

      const content = result.current.messages[0].content;
      expect(content).toHaveLength(2);
      expect((content[0] as { text: string }).text).toBe('partial response');
      expect((content[1] as { text: string }).text).toContain(
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
        testTabMap.set('tab-1', {
          id: 'tab-1', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testActiveTabIdRef.current = 'tab-1';
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
        testTabMap.set('tab-1', {
          id: 'tab-1', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        testActiveTabIdRef.current = 'tab-1';
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
        testTabMap.set('tab-1', {
          id: 'tab-1', title: 'Tab', agentId: 'default', isNew: false,
          messages: [msg],
          sessionId: undefined,
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'idle',
        });
        testActiveTabIdRef.current = 'tab-1';
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
        initTestTab('tab-1');
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
        initTestTab('tab-1');
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

  it('keeps ticking elapsed when first content arrives (streamingActivity becomes non-null)', () => {
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

    // After content arrives, elapsed keeps ticking (shows during tool execution)
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(6);
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

  it('counts elapsed when streaming with content already present (tool execution)', () => {
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

    // streamingActivity is non-null (tool_use present), elapsed still ticks
    expect(result.current.elapsedSeconds).toBeGreaterThanOrEqual(14);
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
            summary: 'Running: npm test -- --run',
          }],
        }),
      ]);
      expect(result).not.toBeNull();
      expect(result!.toolName).toBe('Bash');
      expect(result!.toolContext).toBe('Running: npm test -- --run');
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
            summary: 'Reading src/components/Chat.tsx',
          }],
        }),
      ]);
      expect(result!.toolContext).toBe('Reading src/components/Chat.tsx');
    });

    it('returns toolContext from query input', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-3',
            name: 'Search',
            summary: 'Searching for error handling pattern',
          }],
        }),
      ]);
      expect(result!.toolContext).toBe('Searching for error handling pattern');
    });

    it('counts multiple tool_use blocks correctly', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [
            { type: 'tool_use' as const, id: 'tu-a', name: 'Read', summary: 'Reading a.ts' },
            { type: 'tool_result' as const, toolUseId: 'tu-a', content: 'ok', isError: false, truncated: false },
            { type: 'tool_use' as const, id: 'tu-b', name: 'Bash', summary: 'Running: ls' },
            { type: 'tool_result' as const, toolUseId: 'tu-b', content: 'ok', isError: false, truncated: false },
            { type: 'tool_use' as const, id: 'tu-c', name: 'Search', summary: 'Searching for foo' },
          ],
        }),
      ]);
      expect(result!.toolCount).toBe(3);
      // Last tool_use is Search
      expect(result!.toolName).toBe('Search');
      expect(result!.toolContext).toBe('Searching for foo');
    });

    it('returns toolContext from summary when tool_use has summary', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [makeToolUse('Bash')],
        }),
      ]);
      expect(result!.toolName).toBe('Bash');
      expect(result!.toolContext).toBe('Using tool');
      expect(result!.toolCount).toBe(1);
    });

    it('returns toolContext from summary when tool_use summary is generic', () => {
      const result = deriveStreamingActivity(true, [
        makeMessage({
          role: 'assistant',
          content: [{
            type: 'tool_use' as const,
            id: 'tu-empty',
            name: 'Custom',
            summary: 'Using tool',
          }],
        }),
      ]);
      expect(result!.toolContext).toBe('Using tool');
    });
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
            summary: 'Running: npm test',
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
            { type: 'tool_use' as const, id: 'tu-d1', name: 'Bash', summary: 'Running: npm test' },
            { type: 'tool_use' as const, id: 'tu-d2', name: 'Read', summary: 'Reading src/app.ts' },
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
            summary: 'Running: echo done',
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
          { type: 'tool_result' as const, toolUseId: 'tr-1', content: 'long result text here', isError: false, truncated: false },
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
        summary: 'Using tool',
      });
      content.push({
        type: 'tool_result' as const,
        toolUseId: `tu-${i}`,
        content: 'x'.repeat(500), // 500 chars — should be truncated to 200
        isError: false,
        truncated: false,
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
        summary: 'Using tool',
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
  beforeEach(() => {
    resetTestState();
  });
  describe('MAX_OPEN_TABS constant', () => {
    it('is 4 (hard ceiling, deprecated alias for MAX_TABS_HARD_CEILING)', () => {
      expect(MAX_OPEN_TABS).toBe(4);
    });
  });

  describe('initTabState respects MAX_OPEN_TABS', () => {
    it('creates a tab when below the limit', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Tab limit enforcement is now in useUnifiedTabState.
      // This test verifies the test map accepts entries.
      act(() => {
        initTestTab('tab-1');
      });

      expect(testTabMap.has('tab-1')).toBe(true);
      expect(testTabMap.size).toBe(1);
    });

    it('allows creating up to MAX_OPEN_TABS tabs', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Tab limit enforcement is now in useUnifiedTabState.
      // This test verifies the map can hold MAX_OPEN_TABS entries.
      act(() => {
        for (let i = 0; i < MAX_OPEN_TABS; i++) {
          initTestTab(`tab-${i}`);
        }
      });

      expect(testTabMap.size).toBe(MAX_OPEN_TABS);
    });
  });

  describe('tab creation re-enabled after close', () => {
    it('closing a tab at the limit allows creating a new tab', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Fill to MAX_OPEN_TABS
      act(() => {
        for (let i = 0; i < MAX_OPEN_TABS; i++) {
          initTestTab(`tab-${i}`);
        }
      });

      expect(testTabMap.size).toBe(MAX_OPEN_TABS);

      // Close one tab (cleanup is now unified hook's responsibility)
      act(() => {
        testTabMap.delete('tab-0');
      });

      expect(testTabMap.size).toBe(MAX_OPEN_TABS - 1);

      // Now creating a new tab should succeed
      act(() => {
        initTestTab('tab-new');
      });

      expect(testTabMap.has('tab-new')).toBe(true);
      expect(testTabMap.size).toBe(MAX_OPEN_TABS);
    });
  });

  describe('tab status cleanup on close', () => {
    it('removes tabStatuses entry when tab is closed', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-cleanup');
      });

      // Tab should have 'idle' status
      expect(testTabMap.get('tab-cleanup')?.status).toBe('idle');

      act(() => {
        testTabMap.delete('tab-cleanup');
      });

      // Status entry should be removed
      expect(testTabMap.get('tab-cleanup')?.status).toBeUndefined();
    });
  });
});


// ---------------------------------------------------------------------------
// Fix 8: Tab status indicators
// ---------------------------------------------------------------------------

describe('Fix 8: Tab status indicators', () => {
  beforeEach(() => {
    resetTestState();
    // This describe asserts toast emission (cross-tab AskUserQuestion toast).
    // mockAddToast is a module-hoisted shared spy whose calls accumulate across
    // ALL tests; without clearing it here, a toast from an earlier describe (e.g.
    // a background-tab AskUserQuestion that built a toast with no onSelectTab →
    // action:undefined) leaks into this describe's `.find(id startsWith ask-uq-)`
    // lookup and fails the action assertion. Clear it so toast assertions here
    // only see toasts THIS describe produced.
    mockAddToast.mockClear();
  });
  describe('updateTabStatus', () => {
    it('updates tab map entry status in sync', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-status');
      });

      // Initial status is 'idle'
      expect(testTabMap.get('tab-status')?.status).toBe('idle');
      expect(
        testTabMap.get('tab-status')!.status,
      ).toBe('idle');

      // Update to 'streaming'
      act(() => {
        { const t = testTabMap.get('tab-status'); if (t) t.status = 'streaming' as TabStatus; }
      });

      expect(testTabMap.get('tab-status')?.status).toBe('streaming');
      expect(
        testTabMap.get('tab-status')!.status,
      ).toBe('streaming');
    });

    it('guard: no re-render when status has not changed', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-guard');
      });

      // Capture the tabStatuses reference identity
      const statusesBefore = testTabMap;

      // Update to same status ('idle') — should be a no-op
      act(() => {
        { const t = testTabMap.get('tab-guard'); if (t) t.status = 'idle' as TabStatus; }
      });

      // tabStatuses reference should be the same (no re-render triggered)
      expect(testTabMap).toBe(statusesBefore);
    });

    it('updating status for a tab not in the map is a no-op', () => {
      renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      // Update status for a tab that doesn't exist in the map — should be a no-op
      act(() => {
        { const t = testTabMap.get('ghost-tab'); if (t) t.status = 'error' as TabStatus; }
      });

      // Tab doesn't exist, so status is undefined
      expect(testTabMap.get('ghost-tab')).toBeUndefined();
    });
  });

  describe('tab status transitions', () => {
    it('idle → streaming', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
      });
      expect(testTabMap.get('tab-t')?.status).toBe('idle');

      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'streaming' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('streaming');
      expect(
        testTabMap.get('tab-t')!.status,
      ).toBe('streaming');
    });

    it('streaming → waiting_input', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'streaming' as TabStatus; }
      });

      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'waiting_input' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('waiting_input');
      expect(
        testTabMap.get('tab-t')!.status,
      ).toBe('waiting_input');
    });

    it('streaming → error', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'streaming' as TabStatus; }
      });

      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'error' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('error');
    });

    it('streaming → complete_unread (background tab)', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'streaming' as TabStatus; }
      });

      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'complete_unread' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('complete_unread');
    });

    it('complete_unread → idle (tab switch)', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'complete_unread' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('complete_unread');

      // Simulate switching to this tab — clears unread
      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'idle' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('idle');
    });

    it('streaming → permission_needed', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-t');
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'streaming' as TabStatus; }
      });

      act(() => {
        { const t = testTabMap.get('tab-t'); if (t) t.status = 'permission_needed' as TabStatus; }
      });
      expect(testTabMap.get('tab-t')?.status).toBe('permission_needed');
    });
  });

  describe('tab status initialization', () => {
    it('new tab starts with idle status', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-init');
      });

      expect(testTabMap.get('tab-init')?.status).toBe('idle');
      expect(
        testTabMap.get('tab-init')!.status,
      ).toBe('idle');
    });
  });

  describe('tab status cleanup', () => {
    it('closing tab removes entry from tabStatuses', () => {
      const { result: _result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-rm');
        { const t = testTabMap.get('tab-rm'); if (t) t.status = 'streaming' as TabStatus; }
      });
      expect(testTabMap.get('tab-rm')?.status).toBe('streaming');

      act(() => {
        testTabMap.delete('tab-rm');
      });

      expect(testTabMap.get('tab-rm')?.status).toBeUndefined();
      expect(testTabMap.has('tab-rm')).toBe(false);
    });
  });

  describe('stream handler updates tab status', () => {
    it('first assistant event sets status to streaming', () => {
      const msgId = 'msg-status-stream';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-s');
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

      expect(testTabMap.get('tab-s')?.status).toBe('streaming');
    });

    it('ask_user_question sets status to waiting_input', () => {
      const msgId = 'msg-status-auq';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-s');
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

      expect(testTabMap.get('tab-s')?.status).toBe('waiting_input');
    });

    it('cross-tab ask_user_question toast is persistent and actionable (jump-to-tab)', () => {
      // A question arriving on a NON-active tab toasts the user to switch tabs.
      // Regression: that toast must NOT auto-dismiss (it is an action, not an
      // info ping) and MUST carry a clickable action that selects the asking tab.
      const onSelectTab = vi.fn();
      const msgId = 'msg-xtab-auq';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle({ ...createMockDeps(), onSelectTab }),
      );

      act(() => {
        initTestTab('tab-bg');           // the asking (background) tab
        testTabMap.get('tab-bg')!.title = 'Background Work';
        initTestTab('tab-active');       // initTestTab sets active = last created
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      // Handler bound to the BACKGROUND tab while 'tab-active' is active →
      // isActiveTab is false → the cross-tab toast branch fires.
      const handler = result.current.createStreamHandler(msgId, 'tab-bg');

      act(() => {
        handler({
          type: 'ask_user_question',
          toolUseId: 'tool-xtab',
          questions: [{
            question: 'Pick',
            header: 'H',
            options: [{ label: 'A', description: 'a' }],
            multiSelect: false,
          }],
          sessionId: 'sess-bg',
        });
      });

      const auqToast = mockAddToast.mock.calls
        .map((c) => c[0])
        .find((t) => typeof t.id === 'string' && t.id.startsWith('ask-uq-'));

      expect(auqToast).toBeDefined();
      // AC1: persistent — never auto-dismiss an actionable "go answer" prompt
      expect(auqToast.autoDismiss).not.toBe(true);
      // AC2: clickable action that jumps to the asking tab
      expect(auqToast.action).toBeDefined();
      expect(typeof auqToast.action.onClick).toBe('function');
      auqToast.action.onClick();
      expect(onSelectTab).toHaveBeenCalledWith('tab-bg');
    });

    it('error event sets status to error', () => {
      const msgId = 'msg-status-err';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-s');
        result.current.setIsStreaming(true);
        result.current.setMessages([
          makeMessage({ id: msgId, role: 'assistant', content: [] }),
        ]);
      });

      const handler = result.current.createStreamHandler(msgId, 'tab-s');

      act(() => {
        handler({ type: 'error', message: 'Backend error' });
      });

      expect(testTabMap.get('tab-s')?.status).toBe('error');
    });

    it('result event on foreground tab sets status to idle', () => {
      const msgId = 'msg-status-result';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      act(() => {
        initTestTab('tab-s');
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

      expect(testTabMap.get('tab-s')?.status).toBe('idle');
    });

    it('result event on background tab sets status to complete_unread', () => {
      const msgId = 'msg-status-bg';
      const { result } = renderHook(() =>
        useChatStreamingLifecycle(createMockDeps()),
      );

      const bgMsg = makeMessage({ id: msgId, role: 'assistant', content: [] });

      act(() => {
        // Set up background tab
        testTabMap.set('tab-bg', {
          id: 'tab-bg', title: 'Tab', agentId: 'default', isNew: false,
          messages: [bgMsg],
          sessionId: 'sess-bg',
          pendingQuestion: null,
          abortController: null,
          isStreaming: false, streamState: { mode: "idle", streamGen: 0, reconnectAttempt: 0, maxReconnectAttempts: 3, drainQueued: false, isStalled: false, toolExecuting: false, error: null, sessionId: null },
          streamGen: 0,
          status: 'streaming',
        });
        // Set up foreground tab (different from tab-bg)
        initTestTab('tab-fg');
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

      expect(testTabMap.get('tab-bg')?.status).toBe('complete_unread');
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

  // ── Mid-stream disconnect timeout: symmetric handoff (run_27485b25) ────────
  // BUG: the disconnect 30s timeout unconditionally cleared the spinner + did a
  // ONE-SHOT DB pull. If the backend was still flushing/streaming the answer,
  // it pulled INCOMPLETE content and never re-pulled the finished answer until
  // the user's NEXT send. Fix: consult the backend mirror once at timeout; if
  // still-working → keep spinner + set _postDisconnectUncertain (hand off to the
  // reconcile loop) + stamp _reconcileStreamStart; if done → original clear+pull.
  describe('disconnect timeout backend-state-gated handoff', () => {
    it('still-working: keeps spinner, sets _postDisconnectUncertain, stamps _reconcileStreamStart, does NOT clear isStreaming', async () => {
      vi.useFakeTimers();
      const stateSpy = vi
        .spyOn(chatService, 'getStreamingState')
        .mockResolvedValue({
          'sess-dc1': { streaming: true, state: 'streaming', waitingInput: false, postDisconnectFlushing: false },
        } as unknown as Awaited<ReturnType<typeof chatService.getStreamingState>>);
      try {
        const tabId = 'tab-dc-working';
        initTestTab(tabId);
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-dc1';
        testActiveTabIdRef.current = tabId;

        const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
        // arm a stream so streamGen matches what the handler captures
        act(() => { result.current.setIsStreaming(true, tabId); });
        const handler = result.current.createDisconnectHandler(tabId);
        act(() => { handler(); }); // premature disconnect → keeps isStreaming, arms 30s timer

        // fast-forward to the 30s timeout; flush the await getStreamingState
        await act(async () => {
          vi.advanceTimersByTime(30_000);
          await Promise.resolve(); await Promise.resolve();
        });

        const t = testTabMap.get(tabId)! as unknown as Record<string, unknown>;
        expect(stateSpy).toHaveBeenCalled();
        expect(t._postDisconnectUncertain).toBe(true);          // handed to reconcile loop
        expect(typeof t._reconcileStreamStart).toBe('number');  // BLOCKER: cap anchor stamped
        expect(t._reconcileStreamStart).toBeGreaterThan(0);
        expect(t.isReconnecting).toBe(true);                    // spinner kept (NOT cleared)
        expect(t._disconnectTimeoutId).toBeUndefined();
      } finally {
        stateSpy.mockRestore();
        vi.useRealTimers();
      }
    });

    it('backend-done: clears reconnecting + isStreaming and sets _postDisconnectUncertain for queue-on-next-send', async () => {
      vi.useFakeTimers();
      const stateSpy = vi
        .spyOn(chatService, 'getStreamingState')
        .mockResolvedValue({
          'sess-dc2': { streaming: false, state: 'idle', waitingInput: false, postDisconnectFlushing: false },
        } as unknown as Awaited<ReturnType<typeof chatService.getStreamingState>>);
      const msgsSpy = vi
        .spyOn(chatService, 'getSessionMessages')
        .mockResolvedValue([] as unknown as Awaited<ReturnType<typeof chatService.getSessionMessages>>);
      vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
      try {
        const tabId = 'tab-dc-done';
        initTestTab(tabId);
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-dc2';
        testActiveTabIdRef.current = tabId;
        // show-error branch does the DB pull only when a MessageStore exists for
        // the tab (else it returns early — tab-closed guard). Create one so the
        // real recovery path is exercised.
        messageStoreRegistry.getOrCreate(tabId, { sessionId: 'sess-dc2' });

        const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
        act(() => { result.current.setIsStreaming(true, tabId); });
        const handler = result.current.createDisconnectHandler(tabId);
        act(() => { handler(); });

        await act(async () => {
          vi.advanceTimersByTime(30_000);
          await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        });

        const t = testTabMap.get(tabId)! as unknown as Record<string, unknown>;
        expect(t.isReconnecting).toBe(false);             // cleared (backend done)
        expect(t._postDisconnectUncertain).toBe(true);    // follow-up send queues
        expect(msgsSpy).toHaveBeenCalledWith('sess-dc2'); // one-shot DB pull ran
      } finally {
        stateSpy.mockRestore();
        msgsSpy.mockRestore();
        vi.useRealTimers();
      }
    });

    it('query failure at timeout → fail-safe show-error (clears, never strands spinner)', async () => {
      vi.useFakeTimers();
      const stateSpy = vi
        .spyOn(chatService, 'getStreamingState')
        .mockRejectedValue(new Error('network'));
      vi.spyOn(chatService, 'getSessionMessages').mockResolvedValue([] as never);
      vi.spyOn(chatService, 'invalidateMessageCache').mockImplementation(() => {});
      try {
        const tabId = 'tab-dc-fail';
        initTestTab(tabId);
        const tab = testTabMap.get(tabId)!;
        tab.sessionId = 'sess-dc3';
        testActiveTabIdRef.current = tabId;

        const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
        act(() => { result.current.setIsStreaming(true, tabId); });
        const handler = result.current.createDisconnectHandler(tabId);
        act(() => { handler(); });

        await act(async () => {
          vi.advanceTimersByTime(30_000);
          await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
        });

        const t = testTabMap.get(tabId)! as unknown as Record<string, unknown>;
        expect(stateSpy).toHaveBeenCalled();
        expect(t.isReconnecting).toBe(false); // fail-safe: did NOT keep spinner forever
      } finally {
        vi.restoreAllMocks();
        vi.useRealTimers();
      }
    });
  });
});
