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
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
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
  /** Absolute turn start — set once per turn, NOT reset by reconnect re-arm. */
  streamStartTime?: number;
  messages: Message[];
  isReconnecting?: boolean;
  isResuming?: boolean;
}

type ReconcileAction = 'skip_not_streaming' | 'skip_drain_or_queue' | 'skip_no_session' | 'skip_backend_streaming' | 'skip_active_backend' | 'skip_too_fresh' | 'clear';

/** Backend states meaning "subprocess alive and working" — never force-clear. */
const ACTIVE_BACKEND_STATES = new Set(['waiting_input', 'streaming']);

/**
 * Pure function that mirrors the reconcile decision logic
 * (useChatStreamingLifecycle.ts L1371-1411).
 *
 * Two clocks with DIFFERENT semantics:
 * - `_reconcileStreamStart` — re-armed on every setIsStreaming(true) (incl.
 *   reconnect/heal-grace). Correct for the ACTIVE-backend guard: a freshly
 *   reconnected, genuinely-active backend is not stale.
 * - `streamStartTime` — absolute turn start, set once (never reset by reconnect).
 *   Correct for the hard-deadline BACKSTOP: a daemon-restart loop that keeps
 *   re-arming _reconcileStreamStart must NOT be able to postpone the backstop
 *   forever.
 */
function reconcileDecision(
  tabState: MockTabState,
  backendIsStreaming: boolean,
  now: number = Date.now(),
  backendState?: string,
): ReconcileAction {
  // L1371: only check streaming tabs
  if (!tabState.isStreaming) return 'skip_not_streaming';

  // drain/queue immunity
  if (tabState.drainPending || tabState.queuedMessage) return 'skip_drain_or_queue';

  // L1385-1386: need session ID
  if (!tabState.sessionId) return 'skip_no_session';

  // L1391: backend still streaming → skip
  if (backendIsStreaming) return 'skip_backend_streaming';

  // L1401-1406: active-backend guard — re-armable clock is CORRECT here.
  const activeGuardAge = now - (tabState._reconcileStreamStart ?? 0);
  if (backendState && ACTIVE_BACKEND_STATES.has(backendState) && activeGuardAge < 7_200_000) {
    return 'skip_active_backend';
  }

  // L1410: hard-deadline backstop — anchored to ABSOLUTE turn start so a
  // reconnect re-arm of _reconcileStreamStart cannot postpone it forever.
  const streamAge = now - (tabState.streamStartTime ?? tabState._reconcileStreamStart ?? 0);
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

describe('Reconcile force-clear — absolute deadline immune to reconnect re-arm', () => {
  // Bug: daemon restart mid-thinking → onError → setIsStreaming(true) re-arms
  // _reconcileStreamStart every reconnect → the 30s hard-deadline is postponed
  // forever → "Thinking..." stuck (observed 6m16s). Fix: hard-deadline anchors
  // to absolute streamStartTime, not the re-armable _reconcileStreamStart.

  it('Test 7 (RED→GREEN): re-arm loop — old turn (60s) but _reconcileStreamStart just re-armed (2s) → clears via absolute deadline', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-rearm',
      drainPending: false,
      queuedMessage: undefined,
      streamStartTime: now - 60_000,        // absolute turn start: 60s ago (genuinely stuck)
      _reconcileStreamStart: now - 2_000,   // re-armed 2s ago by a reconnect
      messages: [],
    };
    // backend idle, no active state → must force-clear despite fresh _reconcileStreamStart.
    // Against the OLD logic (streamAge = now - _reconcileStreamStart = 2s) this
    // returned 'skip_too_fresh' — the stuck-forever bug.
    const action = reconcileDecision(tab, false, now, undefined);
    expect(action).toBe('clear');
  });

  it('Test 8: repeated re-arms over minutes still clear (absolute clock unaffected)', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-rearm-loop',
      streamStartTime: now - 360_000,       // 6 minutes (matches the screenshot)
      _reconcileStreamStart: now - 500,     // just re-armed half a second ago
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('clear');
  });

  it('Test 9 (AC2): backend reports active state (streaming) → NOT cleared even if absolute age is old', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-active-long',
      streamStartTime: now - 300_000,       // 5 min old absolute
      _reconcileStreamStart: now - 300_000,
      messages: [],
    };
    // Genuinely-active backend (long tool turn) — active-backend guard wins.
    expect(reconcileDecision(tab, false, now, 'streaming')).toBe('skip_active_backend');
  });

  it('Test 10 (AC2): waiting_input active state → NOT cleared (permission prompt pending)', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-waiting',
      streamStartTime: now - 120_000,
      _reconcileStreamStart: now - 120_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, 'waiting_input')).toBe('skip_active_backend');
  });

  it('Test 11 (AC2): fresh absolute stream (<30s) → NOT cleared (settle window preserved)', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-fresh',
      streamStartTime: now - 15_000,        // 15s absolute — within settle window
      _reconcileStreamStart: now - 15_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('skip_too_fresh');
  });

  it('Test 12 (AC3): a Stopped tab is skip_not_streaming — Stop clear is terminal, never re-armed', () => {
    const now = Date.now();
    // After handleStop: setIsStreaming(false) → isStreaming=false. The force-clear
    // path is never even reached; reconcile leaves it alone.
    const tab: MockTabState = {
      isStreaming: false,
      sessionId: 'sess-stopped',
      streamStartTime: now - 90_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('skip_not_streaming');
  });

  it('Test 13 (defensive): streamStartTime unset falls back to _reconcileStreamStart', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-fallback',
      streamStartTime: undefined,           // somehow unset
      _reconcileStreamStart: now - 45_000,  // 45s via fallback
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('clear');
  });

  // GUARD against the mirror-vs-production gap (spec-review WARNING): the tests
  // above exercise a hand-copied reconcileDecision mirror, NOT the real hook.
  // This test reads the PRODUCTION source and asserts the structural invariant
  // the fix depends on, so a future regression (reverting the backstop to the
  // re-armable clock, or dropping the set-once guard) fails CI even though the
  // mirror tests would still pass.
  it('Test 14 (production source guard): hard-deadline streamAge anchors to absolute streamStartTime', () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(
      resolve(here, '../hooks/useChatStreamingLifecycle.ts'),
      'utf8',
    );
    // The force-clear backstop must compute streamAge from streamStartTime
    // (absolute, re-arm-immune), with _reconcileStreamStart only as fallback.
    expect(src).toMatch(
      /const streamAge = Date\.now\(\) - \(tabState\.streamStartTime \?\? tabState\._reconcileStreamStart \?\? 0\)/,
    );
    // And streamStartTime must remain set-once-per-turn (guarded), or the
    // "absolute" property the backstop relies on would silently break.
    expect(src).toMatch(/if \(!tabState\.streamStartTime\)/);
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
