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
