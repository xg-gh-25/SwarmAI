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
import { forceClearStreamVerdict } from '../hooks/streaming-guards';

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
  /** Absolute turn start — set by setIsStreaming, re-armable on reconnect. */
  streamStartTime?: number;
  /** Reconcile-OWNED backstop clock. ONLY the poll writes it. */
  _idleStreamingSince?: number;
  /** When the queued message was enqueued (for the 60s queue-immunity window). */
  _queuedAt?: number;
  messages: Message[];
  isReconnecting?: boolean;
  isResuming?: boolean;
}

type ReconcileAction = 'skip_not_streaming' | 'skip_drain_or_queue' | 'skip_no_session' | 'skip_backend_streaming' | 'skip_active_backend' | 'skip_too_fresh' | 'clear';

/**
 * Thin ADAPTER over the production decision function `forceClearStreamVerdict`
 * (streaming-guards.ts). There is no longer a hand-copied decision mirror —
 * this delegates the actual judgement to the SAME function the hook calls, then
 * (a) maps the verdict+reason to the granular ReconcileAction the tests assert,
 * and (b) applies the same `_idleStreamingSince` stamp/clear side effects the
 * production poll applies, so the clock-lifecycle tests still hold. This closes
 * the mirror-vs-production drift gap the file's source-guard test warns about.
 */
function reconcileDecision(
  tabState: MockTabState,
  backendIsStreaming: boolean,
  now: number = Date.now(),
  backendState?: string,
): ReconcileAction {
  // Hook-level guard (not part of the pure fn): only streaming tabs are checked.
  if (!tabState.isStreaming) return 'skip_not_streaming';

  const queueAge = tabState._queuedAt ? now - tabState._queuedAt : 0;
  const { verdict, reason } = forceClearStreamVerdict({
    drainPending: !!tabState.drainPending,
    hasQueuedMessage: !!tabState.queuedMessage,
    queueAge,
    hasSessionId: !!tabState.sessionId,
    backendIsStreaming,
    reportedState: backendState,
    activeGuardAge: now - (tabState._reconcileStreamStart ?? 0),
    idleStreamingSince: tabState._idleStreamingSince,
    now,
  });

  // Apply the SAME side effects the production hook applies per verdict.
  if (verdict === 'reset-and-skip') {
    tabState._idleStreamingSince = undefined;
    return reason === 'no_session' ? 'skip_no_session'
      : reason === 'backend_streaming' ? 'skip_backend_streaming'
      : reason === 'active_backend' ? 'skip_active_backend'
      : 'skip_drain_or_queue';
  }
  if (verdict === 'wait-settle') {
    if (tabState._idleStreamingSince === undefined) tabState._idleStreamingSince = now;
    return 'skip_too_fresh';
  }
  // verdict === 'force-clear'
  tabState._idleStreamingSince = undefined;
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

  it('Test 3: genuinely stuck tab (no queue, no drain, stuck-since >30s) → reconcile clears', () => {
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-789',
      drainPending: false,
      queuedMessage: undefined,
      // The reconcile-owned backstop clock has observed the stuck condition for
      // 35s (set on a prior poll) → past the 30s deadline.
      _idleStreamingSince: Date.now() - 35_000,
      _reconcileStreamStart: Date.now() - 35_000,
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

describe('Reconcile force-clear — reconcile-owned backstop immune to reconnect re-arm', () => {
  // Bug: daemon restart mid-thinking → error→reconnecting → setIsStreaming(false)
  // then (true) re-arms BOTH streamStartTime AND _reconcileStreamStart every
  // reconnect → any setIsStreaming-written clock postpones the 30s deadline
  // forever → "Thinking..." stuck (observed 6m16s). Fix: the backstop clock
  // _idleStreamingSince is OWNED by the reconcile poll — no setIsStreaming /
  // reconnect / heal-grace write can reset it, so the deadline always advances.
  // (Adversarial Gate-2 HIGH: the prior streamStartTime anchor was ALSO
  // re-armable via the error→reconnecting path — this is the structural fix.)

  it('Test 7 (RED→GREEN): both setIsStreaming clocks fresh (2s) but stuck-since is old → clears', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-rearm',
      drainPending: false,
      queuedMessage: undefined,
      // BOTH re-armable clocks just re-armed by a reconnect (the case the prior
      // streamStartTime-anchored fix could NOT handle):
      streamStartTime: now - 2_000,
      _reconcileStreamStart: now - 2_000,
      // The poll has been observing the stuck condition for 60s:
      _idleStreamingSince: now - 60_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('clear');
  });

  it('Test 8 (the real fix): multi-poll re-arm loop — deadline advances despite reconnect churn', () => {
    // Simulate a daemon-restart loop: every "poll" the reconnect machinery has
    // just re-armed BOTH setIsStreaming clocks, but the reconcile-owned clock is
    // only written by the poll itself. Across successive polls it must advance to
    // 30s and force-clear — proving immunity to the re-arm loop end-to-end.
    const t0 = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-loop',
      messages: [],
    };
    // Poll 1 @ t0: stuck observed for first time → stamp, too fresh.
    let r = reconcileDecision(tab, false, t0, undefined);
    expect(r).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0);
    // Between polls a reconnect re-arms BOTH setIsStreaming clocks:
    tab.streamStartTime = t0 + 10_000;
    tab._reconcileStreamStart = t0 + 10_000;
    // Poll 2 @ t0+15s: still stuck, clock NOT reset by the re-arm → 15s, too fresh.
    r = reconcileDecision(tab, false, t0 + 15_000, undefined);
    expect(r).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0); // unchanged by reconnect churn
    // Another reconnect re-arms again:
    tab.streamStartTime = t0 + 28_000;
    tab._reconcileStreamStart = t0 + 28_000;
    // Poll 3 @ t0+31s: deadline (from t0) exceeded → force-clear despite both
    // setIsStreaming clocks being only 3s old.
    r = reconcileDecision(tab, false, t0 + 31_000, undefined);
    expect(r).toBe('clear');
    expect(tab._idleStreamingSince).toBeUndefined(); // cleared on force-clear
  });

  it('Test 9 (AC2): backend reports active state (streaming) → NOT cleared, backstop clock reset', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-active-long',
      _idleStreamingSince: now - 300_000,   // had been stuck-since 5 min (stale)
      _reconcileStreamStart: now - 300_000,
      messages: [],
    };
    // Genuinely-active backend (long tool turn) — active-backend guard wins AND
    // resets the backstop clock so the next genuine stall starts fresh.
    expect(reconcileDecision(tab, false, now, 'streaming')).toBe('skip_active_backend');
    expect(tab._idleStreamingSince).toBeUndefined();
  });

  it('Test 10 (AC2): waiting_input active state → NOT cleared (permission prompt pending)', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-waiting',
      _reconcileStreamStart: now - 120_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, 'waiting_input')).toBe('skip_active_backend');
  });

  it('Test 11 (AC2): first stuck observation always settles (30s window from stamp)', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: true,
      sessionId: 'sess-fresh',
      streamStartTime: now - 300_000,       // old absolute clock — irrelevant now
      _reconcileStreamStart: now - 300_000,
      _idleStreamingSince: undefined,        // never observed stuck before
      messages: [],
    };
    // Even though the setIsStreaming clocks are 5 min old, the FIRST time the
    // poll sees the stuck condition it stamps now and waits 30s — a turn that
    // just transitioned to stuck gets a fair settle window.
    expect(reconcileDecision(tab, false, now, undefined)).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(now);
  });

  it('Test 11b (AC2): backend recovers mid-stall → clock resets → no accumulation across blips', () => {
    const t0 = Date.now();
    const tab: MockTabState = { isStreaming: true, sessionId: 'sess-blip', messages: [] };
    // Poll 1: stuck → stamp.
    expect(reconcileDecision(tab, false, t0, undefined)).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0);
    // Poll 2 @ +20s: backend back to streaming → clock cleared.
    expect(reconcileDecision(tab, true, t0 + 20_000, 'streaming')).toBe('skip_backend_streaming');
    expect(tab._idleStreamingSince).toBeUndefined();
    // Poll 3 @ +25s: stuck again → fresh stamp (NOT t0), so only 0s elapsed.
    expect(reconcileDecision(tab, false, t0 + 25_000, undefined)).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0 + 25_000);
  });

  it('Test 11c (adversarial MED #2): clock does NOT age through a queue-immunity gap', () => {
    const t0 = Date.now();
    const tab: MockTabState = { isStreaming: true, sessionId: 'sess-q', messages: [] };
    // Poll 1: stuck observed → stamp t0.
    expect(reconcileDecision(tab, false, t0, undefined)).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0);
    // User queues a message → immunity guard skips AND resets the clock.
    tab.queuedMessage = makeQueuedMessage('follow-up');
    tab._queuedAt = t0 + 1_000;
    expect(reconcileDecision(tab, false, t0 + 5_000, undefined)).toBe('skip_drain_or_queue');
    expect(tab._idleStreamingSince).toBeUndefined(); // reset — does not age through gap
    // Queue drains; tab still stuck at t0+70s. Without the reset this would
    // instantly force-clear (70s > 30s). With it, a FRESH stamp + settle window.
    tab.queuedMessage = undefined;
    tab._queuedAt = undefined;
    expect(reconcileDecision(tab, false, t0 + 70_000, undefined)).toBe('skip_too_fresh');
    expect(tab._idleStreamingSince).toBe(t0 + 70_000);
  });

  it('Test 12 (AC3): a Stopped tab is skip_not_streaming — Stop clear is terminal, never re-armed', () => {
    const now = Date.now();
    const tab: MockTabState = {
      isStreaming: false,
      sessionId: 'sess-stopped',
      _idleStreamingSince: now - 90_000,
      messages: [],
    };
    expect(reconcileDecision(tab, false, now, undefined)).toBe('skip_not_streaming');
  });

  // GUARD against the mirror-vs-production gap (spec-review WARNING): the tests
  // above exercise a hand-copied reconcileDecision mirror, NOT the real hook.
  // This test reads the PRODUCTION source and asserts the structural invariant
  // the fix depends on. Robust to formatting (flexible whitespace) so a benign
  // prettier reflow can't false-fail it (adversarial security MED), but anchored
  // tightly enough that reverting to a re-armable clock fails CI.
  it('Test 14 (production source guard): backstop anchors to reconcile-owned _idleStreamingSince', () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const src = readFileSync(
      resolve(here, '../hooks/useChatStreamingLifecycle.ts'),
      'utf8',
    );
    // The force-clear decision is extracted to forceClearStreamVerdict (pure,
    // test-locked in streaming-guards.test.ts). The hook MUST feed it the
    // reconcile-owned clock — NOT a setIsStreaming-written, re-armable one.
    expect(src).toMatch(/idleStreamingSince:\s*tabState\._idleStreamingSince/);
    // Negative guard: the clock fed in must NOT be a re-armable one.
    expect(src).not.toMatch(/idleStreamingSince:\s*tabState\.streamStartTime/);
    expect(src).not.toMatch(/idleStreamingSince:\s*tabState\._reconcileStreamStart/);
    // And the deadline math itself (now in the extracted pure fn) MUST derive
    // streamAge from idleStreamingSince — locking the invariant where it lives.
    const guardsSrc = readFileSync(
      resolve(here, '../hooks/streaming-guards.ts'),
      'utf8',
    );
    expect(guardsSrc).toMatch(/streamAge =\s*idleStreamingSince === undefined\s*\?\s*0\s*:\s*now - idleStreamingSince/);
    // The clock must be stamped once when stuck is first observed.
    expect(src).toMatch(/tabState\._idleStreamingSince === undefined/);
    // Turn-boundary reset (adversarial HIGH #4): the result handler must clear
    // the clock so a stale stuck-since from this turn can't leak into the next.
    // Assert ≥2 reset sites exist (result handler + immunity/force-clear paths).
    const resetCount = (src.match(/_idleStreamingSince = undefined/g) ?? []).length;
    expect(resetCount).toBeGreaterThanOrEqual(4);
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
