/**
 * Bug-condition tests: Reconcile race condition (b7f5522b regression).
 *
 * Two regressions introduced 2026-06-07:
 * 1. Pipeline stops mid-execution — reconcile kills drain-in-progress
 * 2. User messages disappear — reconcile DB refetch overwrites queued messages
 *
 * Root cause: reconcile's "stuck" heuristic (backend=IDLE + frontend=streaming +
 * streamAge>10s → force clear) doesn't account for the "drain gap" state where
 * isStreaming is intentionally held true between result event and drain start.
 *
 * These tests verify the bug-condition matrix:
 * - Error behavior (what was happening) → must NOT happen
 * - Expected behavior (what should happen) → must happen
 * - Must-not-change behavior (reconcile still catches genuinely stuck tabs)
 *
 * Testing methodology: Unit tests that simulate the reconcile decision logic
 * by constructing tabState objects and verifying skip/clear decisions.
 */

import { describe, it, expect } from 'vitest';
import type { ContentBlock, Message } from '../types';

// ---------------------------------------------------------------------------
// Helpers: simulate the reconcile decision logic extracted from
// useChatStreamingLifecycle.ts L975-1030
// ---------------------------------------------------------------------------

interface MockTabState {
  isStreaming: boolean;
  sessionId?: string;
  drainPending?: boolean;
  queuedMessage?: { text: string; attachments: never[]; displayContent: ContentBlock[]; messageId: string };
  _reconcileStreamStart?: number;
  messages: Message[];
  isReconnecting?: boolean;
  isResuming?: boolean;
}

type ReconcileAction = 'skip_not_streaming' | 'skip_drain_or_queue' | 'skip_no_session' | 'skip_backend_streaming' | 'skip_too_fresh' | 'clear';

/**
 * Pure function that mirrors the reconcile decision logic.
 * Returns the action reconcile would take for a given tab.
 */
function reconcileDecision(
  tabState: MockTabState,
  backendIsStreaming: boolean,
  now: number = Date.now(),
): ReconcileAction {
  // L977: only check streaming tabs
  if (!tabState.isStreaming) return 'skip_not_streaming';

  // NEW: drain/queue immunity
  if (tabState.drainPending || tabState.queuedMessage) return 'skip_drain_or_queue';

  // L979: need session ID
  if (!tabState.sessionId) return 'skip_no_session';

  // L984-986: backend still streaming → skip
  if (backendIsStreaming) return 'skip_backend_streaming';

  // L989-990: stream too fresh (< 30s)
  const streamAge = now - (tabState._reconcileStreamStart ?? 0);
  if (streamAge < 30_000) return 'skip_too_fresh';

  // All guards passed → force clear
  return 'clear';
}

/**
 * Simulate DB refetch merge logic (preserves queued messages).
 */
function mergeDbMessages(dbMessages: Message[], localMessages: Message[]): Message[] {
  const localQueued = localMessages.filter(
    (m) => (m as Message & { isQueued?: boolean }).isQueued || m.id.startsWith('queued-'),
  );
  return localQueued.length > 0 ? [...dbMessages, ...localQueued] : dbMessages;
}

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

function makeQueuedMessage(text = 'user follow-up') {
  return {
    text,
    attachments: [] as never[],
    displayContent: [{ type: 'text' as const, text }],
    messageId: `queued-${Date.now()}`,
  };
}

function makeMessage(id: string, role: 'user' | 'assistant' = 'assistant', isQueued = false): Message {
  return {
    id,
    role,
    content: [{ type: 'text', text: `msg-${id}` }] as ContentBlock[],
    timestamp: new Date().toISOString(),
    ...(isQueued ? { isQueued: true } : {}),
  } as Message;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Reconcile race condition — drain gap immunity', () => {
  it('Test 1: drainPending=true → reconcile skips (never kills drain-in-progress)', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-123',
      drainPending: true,
      _reconcileStreamStart: Date.now() - 60_000, // 60s old — would normally be "stuck"
      messages: [],
    };

    const action = reconcileDecision(tab, false); // backend=IDLE
    expect(action).toBe('skip_drain_or_queue');
  });

  it('Test 2: queuedMessage exists → reconcile skips (preserves queued user input)', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-456',
      drainPending: false,
      queuedMessage: makeQueuedMessage('my important question'),
      _reconcileStreamStart: Date.now() - 45_000, // 45s old
      messages: [],
    };

    const action = reconcileDecision(tab, false); // backend=IDLE
    expect(action).toBe('skip_drain_or_queue');
  });

  it('Test 3: genuinely stuck tab (no queue, no drain, >30s) → reconcile clears', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-789',
      drainPending: false,
      queuedMessage: undefined,
      _reconcileStreamStart: Date.now() - 35_000, // 35s old, past threshold
      messages: [],
    };

    const action = reconcileDecision(tab, false); // backend=IDLE
    expect(action).toBe('clear');
  });

  it('Test 4: DB refetch preserves queued messages (merge, not replace)', () => {
    const dbMessages = [
      makeMessage('db-1', 'user'),
      makeMessage('db-2', 'assistant'),
    ];
    const localMessages = [
      makeMessage('db-1', 'user'),
      makeMessage('db-2', 'assistant'),
      makeMessage('queued-abc', 'user', true), // queued, only in frontend
    ];

    const result = mergeDbMessages(dbMessages, localMessages);

    // DB messages present
    expect(result.some(m => m.id === 'db-1')).toBe(true);
    expect(result.some(m => m.id === 'db-2')).toBe(true);
    // Queued message preserved
    expect(result.some(m => m.id === 'queued-abc')).toBe(true);
    expect(result.length).toBe(3);
  });

  it('Test 5: streamAge < 30s → reconcile skips (prevents false positive during tool execution)', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-tool',
      drainPending: false,
      queuedMessage: undefined,
      _reconcileStreamStart: Date.now() - 15_000, // only 15s — within threshold
      messages: [],
    };

    const action = reconcileDecision(tab, false); // backend=IDLE
    expect(action).toBe('skip_too_fresh');
  });

  it('Test 6: backend still streaming → reconcile always skips regardless of age', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-active',
      drainPending: false,
      queuedMessage: undefined,
      _reconcileStreamStart: Date.now() - 120_000, // 2 minutes old
      messages: [],
    };

    const action = reconcileDecision(tab, true); // backend=STREAMING
    expect(action).toBe('skip_backend_streaming');
  });
});

describe('Reconcile race condition — edge cases', () => {
  it('drainPending + queuedMessage both set → still skips (double protection)', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-both',
      drainPending: true,
      queuedMessage: makeQueuedMessage(),
      _reconcileStreamStart: Date.now() - 90_000,
      messages: [],
    };

    const action = reconcileDecision(tab, false);
    expect(action).toBe('skip_drain_or_queue');
  });

  it('DB refetch with no queued messages → returns DB messages unchanged', () => {
    const dbMessages = [
      makeMessage('db-1', 'user'),
      makeMessage('db-2', 'assistant'),
    ];
    const localMessages = [
      makeMessage('db-1', 'user'),
      makeMessage('db-2', 'assistant'),
    ];

    const result = mergeDbMessages(dbMessages, localMessages);
    expect(result).toEqual(dbMessages); // no merge needed, same ref
  });

  it('non-streaming tab → reconcile skips immediately', () => {
    const tab: MockTabState = {
      isStreaming: false,
      sessionId: 'sess-idle',
      messages: [],
    };

    const action = reconcileDecision(tab, false);
    expect(action).toBe('skip_not_streaming');
  });
});
