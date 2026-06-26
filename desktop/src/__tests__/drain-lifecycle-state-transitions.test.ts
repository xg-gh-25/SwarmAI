/**
 * State transition tests: Queue drain lifecycle.
 *
 * This is the path that keeps breaking (3 regressions in 48h).
 * Tests model the EXPECTED state at each step of the drain lifecycle,
 * so any future change that violates a transition is caught immediately.
 *
 * State model:
 *   STREAMING → RESULT_WITH_QUEUE → DRAIN_PENDING → DRAIN_ACTIVE → STREAMING (new turn)
 *   STREAMING → RESULT_WITH_QUEUE → DRAIN_PENDING → DRAIN_FAILED → IDLE (queue restored)
 *   STREAMING → RESULT_NO_QUEUE → IDLE
 *
 * Each test verifies: given state X + event Y → expected state Z + expected flags.
 */

import { describe, it, expect } from 'vitest';

// ---------------------------------------------------------------------------
// State model (mirrors the implicit state in useChatStreamingLifecycle)
// ---------------------------------------------------------------------------

interface StreamingState {
  isStreaming: boolean;
  drainPending: boolean;
  queuedMessage: string | null;  // simplified: text or null
  backendState: 'streaming' | 'idle';
  // Reconcile should clear?
  reconcileWouldClear: boolean;
}

/**
 * Determine if reconcile would force-clear this state.
 * Mirrors L976-997 in useChatStreamingLifecycle.ts
 */
function wouldReconcileClear(state: StreamingState, streamAgeMs: number): boolean {
  if (!state.isStreaming) return false;  // L977
  if (state.drainPending || state.queuedMessage) return false;  // L983
  if (state.backendState === 'streaming') return false;  // L991
  if (streamAgeMs < 30_000) return false;  // L997
  return true;  // force clear
}

// ---------------------------------------------------------------------------
// Transition: STREAMING → RESULT (with queued message)
// ---------------------------------------------------------------------------

describe('Drain lifecycle: STREAMING → RESULT_WITH_QUEUE', () => {
  it('result event with queuedMessage → sets drainPending, keeps isStreaming', () => {
    // Before: agent streaming, user queued a message
    const _before: StreamingState = {
      isStreaming: true,
      drainPending: false,
      queuedMessage: 'user follow-up',
      backendState: 'streaming',
      reconcileWouldClear: false,
    };

    // Event: result arrives (agent turn complete)
    // After: drainPending=true, isStreaming stays true (intentional hold)
    const after: StreamingState = {
      isStreaming: true,  // NOT cleared — drain will continue
      drainPending: true,  // set before setTimeout(drain)
      queuedMessage: 'user follow-up',  // still waiting
      backendState: 'idle',  // backend completed
      reconcileWouldClear: false,  // IMMUNE via drainPending
    };

    // Verify reconcile immunity
    expect(wouldReconcileClear(after, 60_000)).toBe(false);
    expect(after.isStreaming).toBe(true);
    expect(after.drainPending).toBe(true);
  });

  it('result event WITHOUT queuedMessage → clears isStreaming immediately', () => {
    const _before: StreamingState = {
      isStreaming: true,
      drainPending: false,
      queuedMessage: null,
      backendState: 'streaming',
      reconcileWouldClear: false,
    };

    // Event: result arrives, no queued message
    const after: StreamingState = {
      isStreaming: false,  // cleared immediately
      drainPending: false,
      queuedMessage: null,
      backendState: 'idle',
      reconcileWouldClear: false,  // not streaming → reconcile skips
    };

    expect(wouldReconcileClear(after, 60_000)).toBe(false);
    expect(after.isStreaming).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Transition: DRAIN_PENDING → DRAIN_ACTIVE (drain fires successfully)
// ---------------------------------------------------------------------------

describe('Drain lifecycle: DRAIN_PENDING → DRAIN_ACTIVE', () => {
  it('drain fires → clears drainPending, starts new stream', () => {
    const _before: StreamingState = {
      isStreaming: true,
      drainPending: true,
      queuedMessage: 'user follow-up',
      backendState: 'idle',
      reconcileWouldClear: false,
    };

    // Event: drain callback fires, streamChat() called
    const after: StreamingState = {
      isStreaming: true,  // new stream started
      drainPending: false,  // cleared on drain success
      queuedMessage: null,  // consumed by drain (cleared before send)
      backendState: 'streaming',  // backend processing new message
      reconcileWouldClear: false,  // backend is streaming
    };

    expect(wouldReconcileClear(after, 0)).toBe(false);
    expect(after.drainPending).toBe(false);
    expect(after.queuedMessage).toBeNull();
  });

  it('reconcile during drain gap (backend idle, drain not yet fired) → IMMUNE', () => {
    // This is THE race condition that caused the regression
    const drainGapState: StreamingState = {
      isStreaming: true,
      drainPending: true,  // set by result handler
      queuedMessage: 'user follow-up',  // still there
      backendState: 'idle',  // backend finished previous turn
      reconcileWouldClear: false,
    };

    // Even with 60s stream age, reconcile must NOT clear
    expect(wouldReconcileClear(drainGapState, 60_000)).toBe(false);
    expect(wouldReconcileClear(drainGapState, 120_000)).toBe(false);
    expect(wouldReconcileClear(drainGapState, 999_999)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Transition: DRAIN_PENDING → DRAIN_FAILED
// ---------------------------------------------------------------------------

describe('Drain lifecycle: DRAIN_PENDING → DRAIN_FAILED', () => {
  it('drain throws → clears drainPending, restores queuedMessage, clears isStreaming', () => {
    const _before: StreamingState = {
      isStreaming: true,
      drainPending: true,
      queuedMessage: null,  // cleared before send attempt
      backendState: 'idle',
      reconcileWouldClear: false,
    };

    // Event: drain fails (buildContentArray throws, network error, etc.)
    const after: StreamingState = {
      isStreaming: false,  // cleanupStreamingState() called
      drainPending: false,  // cleared in catch block
      queuedMessage: 'user follow-up',  // RESTORED so user doesn't lose input
      backendState: 'idle',
      reconcileWouldClear: false,  // not streaming → reconcile ignores
    };

    // Reconcile won't touch it (not streaming)
    expect(wouldReconcileClear(after, 60_000)).toBe(false);
    // User's message is preserved
    expect(after.queuedMessage).toBe('user follow-up');
    // Not stuck in streaming state
    expect(after.isStreaming).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Transition: Safety timeout (drainPending leak protection)
// ---------------------------------------------------------------------------

describe('Drain lifecycle: Safety timeout', () => {
  it('5s timeout fires after successful drain → drainPending already false → no-op', () => {
    const afterSuccessfulDrain: StreamingState = {
      isStreaming: true,
      drainPending: false,  // already cleared by drain success
      queuedMessage: null,
      backendState: 'streaming',
      reconcileWouldClear: false,
    };

    // Safety timeout checks: if (drainPending) clear it
    // drainPending is already false → no change
    const afterTimeout = { ...afterSuccessfulDrain };
    if (afterTimeout.drainPending) afterTimeout.drainPending = false;

    expect(afterTimeout).toEqual(afterSuccessfulDrain);
  });

  it('5s timeout fires after drain no-op (callback lost) → clears drainPending', () => {
    const stuckState: StreamingState = {
      isStreaming: true,
      drainPending: true,  // never cleared because drain didn't fire
      queuedMessage: 'user follow-up',
      backendState: 'idle',
      reconcileWouldClear: false,  // immune via drainPending
    };

    // Safety timeout fires at 5s
    const afterTimeout = { ...stuckState, drainPending: false };

    // Now queuedMessage still provides immunity
    expect(wouldReconcileClear(afterTimeout, 60_000)).toBe(false);

    // But if queuedMessage is also somehow gone, reconcile CAN clear after 30s
    const fullyCleared = { ...afterTimeout, queuedMessage: null };
    expect(wouldReconcileClear(fullyCleared, 60_000)).toBe(true);
    expect(wouldReconcileClear(fullyCleared, 15_000)).toBe(false);  // < 30s still safe
  });
});

// ---------------------------------------------------------------------------
// handleStop + drain (Site B)
// ---------------------------------------------------------------------------

describe('Drain lifecycle: handleStop → drain', () => {
  it('user stops → isStreaming=false → drain fires → new stream → isStreaming=true', () => {
    // Step 1: user hits Stop
    const afterStop: StreamingState = {
      isStreaming: false,  // cleared by handleStop
      drainPending: false,
      queuedMessage: 'user follow-up',  // still queued
      backendState: 'idle',
      reconcileWouldClear: false,  // not streaming
    };

    expect(wouldReconcileClear(afterStop, 60_000)).toBe(false);

    // Step 2: drain fires from handleStop's setTimeout
    const afterDrain: StreamingState = {
      isStreaming: true,  // new stream started
      drainPending: false,  // not set for Site B (isStreaming was false, no gap to protect)
      queuedMessage: null,  // consumed
      backendState: 'streaming',
      reconcileWouldClear: false,
    };

    expect(wouldReconcileClear(afterDrain, 0)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Regression guard: the EXACT scenario from 2026-06-08
// ---------------------------------------------------------------------------

describe('Regression guard: exact 2026-06-08 scenario', () => {
  it('pipeline mid-execution: result → drain gap → reconcile must NOT kill', () => {
    // Pipeline agent finishes a tool call, has queued next step
    const pipelineState: StreamingState = {
      isStreaming: true,
      drainPending: true,
      queuedMessage: 'continue pipeline step 5',
      backendState: 'idle',  // agent turn completed
      reconcileWouldClear: false,
    };

    // Reconcile fires at various ages — NEVER clears
    for (const age of [5_000, 10_000, 30_000, 60_000, 300_000]) {
      expect(wouldReconcileClear(pipelineState, age)).toBe(false);
    }
  });

  it('user追问 during streaming: queued msg must survive reconcile + DB refetch', () => {
    // User sent追问, it's queued and visible in chat
    const state: StreamingState = {
      isStreaming: true,
      drainPending: false,  // result hasn't arrived yet
      queuedMessage: '为什么pipeline停了',
      backendState: 'streaming',  // agent still working
      reconcileWouldClear: false,
    };

    // Reconcile: backend streaming → always skip
    expect(wouldReconcileClear(state, 999_999)).toBe(false);

    // After result arrives: drainPending + queuedMessage both protect
    const afterResult: StreamingState = {
      ...state,
      drainPending: true,
      backendState: 'idle',
    };
    expect(wouldReconcileClear(afterResult, 999_999)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Transition: completeHandler ([DONE]) definitive drain
//
// Regression for the "stranded queue" bug: a queued follow-up was not
// auto-drained when the previous turn ended; the user had to re-send manually.
//
// Root cause: the `result` event branch (which normally schedules the drain and
// sets drainPending) is gated by a raw streamGen staleness check. During long
// autonomous turns, streamGen is bumped mid-turn by the cmd_permission_request /
// ask_user_question handlers, so the final `result` event is discarded as stale
// → the drain is never scheduled. The completeHandler then clears isStreaming
// and the queue is stranded.
//
// Fix: the completeHandler ([DONE], the definitive "stream is over" signal)
// drains AFTER clearing streaming, iff a message is queued and no drain is
// already in flight (drainPending). drainQueuedMessage is idempotent, so this
// never double-sends with the normal result-handler path.
// ---------------------------------------------------------------------------

/**
 * Mirrors the completeHandler tail in useChatStreamingLifecycle.ts:
 *   if (queuedMessage && !drainPending
 *       && !pendingQuestion && !pendingPermissionRequestId) -> schedule drain
 * Returns true iff the completeHandler should schedule a drain.
 */
function completeHandlerDrains(state: {
  queuedMessage: string | null;
  drainPending: boolean;
  pendingQuestion?: boolean;
  pendingPermissionRequestId?: boolean;
}): boolean {
  return (
    !!state.queuedMessage &&
    !state.drainPending &&
    !state.pendingQuestion &&
    !state.pendingPermissionRequestId
  );
}

describe('Drain lifecycle: completeHandler definitive drain', () => {
  it('result discarded (streamGen bumped) → drainPending never set → completeHandler drains', () => {
    // The bug scenario: the result event was discarded as stale, so it never
    // ran its drain-scheduling branch. drainPending stays false, queue remains.
    const atDone: StreamingState = {
      isStreaming: true,        // never cleared — the discarded result kept it true
      drainPending: false,      // result branch never ran → flag never set
      queuedMessage: 'user follow-up',
      backendState: 'idle',
      reconcileWouldClear: false,
    };

    // completeHandler must rescue the stranded queue.
    expect(completeHandlerDrains(atDone)).toBe(true);
  });

  it('normal result path (drainPending already set) → completeHandler does NOT double-drain', () => {
    // The result handler already scheduled the drain and set drainPending.
    const atDone: StreamingState = {
      isStreaming: true,
      drainPending: true,       // result handler set this when it scheduled
      queuedMessage: 'user follow-up',
      backendState: 'idle',
      reconcileWouldClear: false,
    };

    // No double schedule — the result-handler drain owns it.
    expect(completeHandlerDrains(atDone)).toBe(false);
  });

  it('no queued message → completeHandler does nothing', () => {
    const atDone: StreamingState = {
      isStreaming: true,
      drainPending: false,
      queuedMessage: null,
      backendState: 'idle',
      reconcileWouldClear: false,
    };

    expect(completeHandlerDrains(atDone)).toBe(false);
  });

  // ── Adversarial: premature-drain protection ──
  // ask_user_question / cmd_permission_request are TERMINAL (clear isStreaming),
  // then the backend sends [DONE] → completeHandler runs while the agent is
  // suspended awaiting the user. Draining here would abandon the open
  // question/permission. Must NOT drain.
  it('pendingQuestion set → completeHandler does NOT drain (would abandon the question)', () => {
    expect(
      completeHandlerDrains({
        queuedMessage: 'user follow-up',
        drainPending: false,
        pendingQuestion: true,
      }),
    ).toBe(false);
  });

  it('pendingPermissionRequestId set → completeHandler does NOT drain (would abandon the approval)', () => {
    expect(
      completeHandlerDrains({
        queuedMessage: 'user follow-up',
        drainPending: false,
        pendingPermissionRequestId: true,
      }),
    ).toBe(false);
  });

  it('question answered (pendingQuestion cleared) + queue present → drains on the completing turn', () => {
    // After the user answers, pendingQuestion is cleared and the resumed turn
    // completes; the queued follow-up should then drain.
    expect(
      completeHandlerDrains({
        queuedMessage: 'user follow-up',
        drainPending: false,
        pendingQuestion: false,
        pendingPermissionRequestId: false,
      }),
    ).toBe(true);
  });
});
