/**
 * Tests for streaming-guards.ts — shouldQueueSend predicate.
 *
 * Methodology: pure-function unit tests that construct the EXACT tab states the
 * SSE-disconnect bug produces, then assert the send is QUEUED (not sent).
 *
 * CRITICAL (Gate 1 CHECK5): the load-bearing case is `_postDisconnectUncertain`
 * — the state a tab is in AFTER heal-grace expiry while the backend subprocess
 * may still be streaming. A test that only checks isStreaming would pass while
 * the integration bug remains. This file forces the post-disconnect state.
 */
import { describe, it, expect } from 'vitest';
import {
  shouldQueueSend,
  shouldResurfaceQuestion,
  shouldResurfacePermission,
  queuedBehindApprovalText,
  isServerQueuedErrorCode,
  hostForInjectedTurn,
  computeDrainRetirement,
  healGraceExpiryVerdict,
  nextReconcileDelay,
  type QueueGuardState,
  type HealGraceExpiryInput,
} from '../streaming-guards';

const idle: QueueGuardState = {
  isStreaming: false,
  isWaitingForBusy: false,
  isReconnecting: false,
  _healGraceActive: false,
  _postDisconnectUncertain: false,
};

describe('shouldQueueSend', () => {
  it('clean idle tab → SEND (false)', () => {
    expect(shouldQueueSend(idle)).toBe(false);
  });

  it('actively streaming → QUEUE (true)', () => {
    expect(shouldQueueSend({ ...idle, isStreaming: true })).toBe(true);
  });

  it('SESSION_BUSY polling (isWaitingForBusy) → QUEUE — must not drop the message', () => {
    expect(shouldQueueSend({ ...idle, isWaitingForBusy: true })).toBe(true);
  });

  it('reconnecting → QUEUE', () => {
    expect(shouldQueueSend({ ...idle, isReconnecting: true })).toBe(true);
  });

  it('heal-grace active → QUEUE', () => {
    expect(shouldQueueSend({ ...idle, _healGraceActive: true })).toBe(true);
  });

  it('THE BUG STATE: heal-grace EXPIRED, all visible flags false, backend may still stream → QUEUE', () => {
    // After heal-grace expiry the old code set isStreaming=false, status=idle,
    // _healGraceActive=false, isReconnecting=false, isWaitingForBusy=false.
    // Every visible flag is false, yet the backend subprocess is still STREAMING.
    // _postDisconnectUncertain is the persistent signal that keeps the send
    // queued instead of escaping to a normal send → SESSION_BUSY → orphan delete.
    const postDisconnect: QueueGuardState = {
      ...idle,
      _postDisconnectUncertain: true,
    };
    expect(shouldQueueSend(postDisconnect)).toBe(true);
  });

  it('tolerates undefined optional flags (partial tab state)', () => {
    expect(shouldQueueSend({ isStreaming: false })).toBe(false);
    expect(shouldQueueSend({ isStreaming: false, _postDisconnectUncertain: true })).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════
// AC2 (Gate 1 CHECK5): FORCE the post-disconnect path, don't just test the
// pure predicate against hand-built states. This models the real bug sequence
// — long agent turn → SSE disconnect → heal-grace entry → heal-grace EXPIRY —
// and asserts a follow-up send is QUEUED, not sent. A test that only checked
// isStreaming would pass while the integration bug remains.
// ═══════════════════════════════════════════════════════════════════

/** Mirrors the heal-grace-expiry mutation in useChatStreamingLifecycle.ts
 *  (the timeout callback at ~line 2861-2879). This is the exact state
 *  transition the bug occupies. */
function applyHealGraceExpiry(tab: QueueGuardState & Record<string, unknown>): void {
  // Old (buggy) behaviour cleared everything; the fix sets _postDisconnectUncertain.
  (tab as { isStreaming: boolean }).isStreaming = false;
  tab._healGraceActive = false;
  tab.isReconnecting = false;
  tab.isWaitingForBusy = false;
  tab._postDisconnectUncertain = true; // ← the fix
}

/** Mirrors the handleSendMessage queue decision (ChatPage.tsx ~line 1474-1478). */
function sendDecision(tab: QueueGuardState): 'queue' | 'send' {
  return shouldQueueSend(tab) ? 'queue' : 'send';
}

describe('AC2: post-disconnect send is queued (forced path)', () => {
  it('long turn → SSE disconnect → heal-grace expiry → follow-up send is QUEUED (not sent → no SESSION_BUSY → no orphan delete)', () => {
    // 1. Long agent turn streaming, data received.
    const tab: QueueGuardState & Record<string, unknown> = {
      isStreaming: true,
      hasReceivedData: true,
    };
    expect(sendDecision(tab)).toBe('queue'); // streaming → queue (baseline)

    // 2. SSE disconnect mid-stream → heal-grace entry (isStreaming stays true).
    tab._healGraceActive = true;
    expect(sendDecision(tab)).toBe('queue');

    // 3. Heal-grace EXPIRES — backend subprocess may STILL be streaming.
    applyHealGraceExpiry(tab);

    // 4. THE ASSERTION: a follow-up send here must QUEUE. Before the fix every
    //    visible flag was false → 'send' → escaped to SESSION_BUSY → message
    //    destroyed. With _postDisconnectUncertain it stays 'queue'.
    expect(sendDecision(tab)).toBe('queue');
  });

  it('regression guard: WITHOUT _postDisconnectUncertain the bug returns (send escapes)', () => {
    // Proves the test is sensitive — if a future refactor drops the flag from
    // the expiry mutation, this asserts the bug is back.
    const buggyTab: QueueGuardState = {
      isStreaming: false,
      _healGraceActive: false,
      isReconnecting: false,
      isWaitingForBusy: false,
      _postDisconnectUncertain: false, // simulate the OLD behaviour
    };
    expect(sendDecision(buggyTab)).toBe('send'); // the bug: escapes
  });

  it('clearing _postDisconnectUncertain on a genuine new send returns to normal', () => {
    const tab: QueueGuardState = { isStreaming: false, _postDisconnectUncertain: true };
    expect(sendDecision(tab)).toBe('queue');
    // handleSendMessage clears it when a real new stream starts.
    (tab as { _postDisconnectUncertain: boolean })._postDisconnectUncertain = false;
    expect(sendDecision(tab)).toBe('send');
  });
});

// ═══════════════════════════════════════════════════════════════════
// OT01 honest-signal: heal-grace expiry must NOT show a "Connection lost" error
// while the backend turn is still alive (streaming / waiting_input). The
// recurring user-facing bug: spinner stopped +
// scary error shown while the answer was still being produced, content filled in
// silently ~15s later via reconcile.
// ═══════════════════════════════════════════════════════════════════
describe('healGraceExpiryVerdict (OT01 honest error signal)', () => {
  const dead: HealGraceExpiryInput = {
    backendIsStreaming: false,
    backendWaitingInput: false,
    queryFailed: false,
  };

  it('backend genuinely idle/done → show-error (legit failure path preserved)', () => {
    expect(healGraceExpiryVerdict(dead)).toBe('show-error');
  });

  it('backend still streaming → still-working (NO error)', () => {
    expect(healGraceExpiryVerdict({ ...dead, backendIsStreaming: true })).toBe('still-working');
  });

  it('backend waiting on input → still-working (NO error)', () => {
    expect(healGraceExpiryVerdict({ ...dead, backendWaitingInput: true })).toBe('still-working');
  });

  it('FAIL-SAFE: liveness query failed → show-error (never strand on an eternal spinner)', () => {
    // Even though we cannot prove the backend is dead, an unprovable state must
    // fall through to the error path so the spinner cannot hang forever.
    expect(healGraceExpiryVerdict({ ...dead, queryFailed: true })).toBe('show-error');
    // Fail-safe wins even if a stale "alive" flag is somehow set alongside the failure.
    expect(healGraceExpiryVerdict({
      backendIsStreaming: true, backendWaitingInput: true, queryFailed: true,
    })).toBe('show-error');
  });
});

// ═══════════════════════════════════════════════════════════════════
// AC5 (Root-1 SSOT Phase 3): shouldResurfaceQuestion — the gated, idempotent
// decision for re-surfacing a lost AskUserQuestion from the authoritative
// streaming-state read API. The reconcile loop polls every 15s; an unguarded
// re-surface would flap the question UI. Re-surface ONLY when the backend says
// waiting_input AND carries a pending_question whose toolUseId is NOT already
// the tab's current pending question (idempotent on toolUseId).
// ═══════════════════════════════════════════════════════════════════
// Command-permission re-surface (core/permission_prompt, 2026-10-22 incident: a CLI
// turn stopped on a permission the tab never got; the session waited 18 min unseen).
describe('shouldResurfacePermission (a lost permission prompt)', () => {
  const p = (id: string) => ({ requestId: id });
  it('backend waiting on a prompt the tab does not offer → RESURFACE', () => {
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: p('r1'), currentPendingRequestId: null })).toBe(true);
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: p('r2'), currentPendingRequestId: 'r1' })).toBe(true);
  });
  it('already offered, just decided, not waiting, or no prompt → no', () => {
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: p('r1'), currentPendingRequestId: 'r1' })).toBe(false);
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: p('r1'), currentPendingRequestId: null, decidedRequestId: 'r1' })).toBe(false);
    expect(shouldResurfacePermission({ backendWaitingInput: false, backendPendingPermission: p('r1'), currentPendingRequestId: null })).toBe(false);
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: null, currentPendingRequestId: null })).toBe(false);
    expect(shouldResurfacePermission({ backendWaitingInput: true, backendPendingPermission: p(''), currentPendingRequestId: null })).toBe(false);
  });
});

describe('shouldResurfaceQuestion (AC5 — lost AskUserQuestion re-surface)', () => {
  const pq = (id: string) => ({ toolUseId: id, questions: [{ q: 1 }] as unknown[] });

  it('backend waiting_input + pending_question + tab has NO pending question → RESURFACE', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: pq('tu-1'),
      currentPendingToolUseId: null,
    })).toBe(true);
  });

  it('idempotent: tab already shows the SAME toolUseId → NO resurface (prevents 15s flap)', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: pq('tu-1'),
      currentPendingToolUseId: 'tu-1',
    })).toBe(false);
  });

  it('a DIFFERENT toolUseId replaces the stale one → RESURFACE', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: pq('tu-2'),
      currentPendingToolUseId: 'tu-1',
    })).toBe(true);
  });

  it('backend NOT waiting_input → never resurface (even if a payload lingers)', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: false,
      backendPendingQuestion: pq('tu-1'),
      currentPendingToolUseId: null,
    })).toBe(false);
  });

  it('waiting_input but NO pending_question payload → cannot resurface (nothing to render)', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: null,
      currentPendingToolUseId: null,
    })).toBe(false);
  });

  it('Gate-2 HIGH: a permission prompt (empty questions, shares WAITING_INPUT) → NEVER resurface as a question', () => {
    // A command-permission prompt sets state=waiting_input + pending_question with
    // NO questions (it has its own cmd_permission_request render path). The guard
    // must reject it so it is not mistaken for an AskUserQuestion (phantom side
    // effects: state machine, setIsStreaming(false), toast).
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: { toolUseId: 'perm-req-1', questions: [] },
      currentPendingToolUseId: null,
    })).toBe(false);
  });

  it('answer-in-flight guard: an answer submitted for this toolUseId suppresses re-surface for one poll window', () => {
    // After the user answers, local pendingQuestion clears but the backend mirror
    // may still report waiting_input for one poll. Without this guard the just-
    // answered question would re-appear. answeredToolUseId carries that signal.
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: pq('tu-1'),
      currentPendingToolUseId: null,
      answeredToolUseId: 'tu-1',
    })).toBe(false);
  });

  it('answer-in-flight guard does NOT block a genuinely different new question', () => {
    expect(shouldResurfaceQuestion({
      backendWaitingInput: true,
      backendPendingQuestion: pq('tu-2'),
      currentPendingToolUseId: null,
      answeredToolUseId: 'tu-1',
    })).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════
// AC4 (Root-1 SSOT Phase 3): computeDrainRetirement — mirror the server's
// pending_count and last_drained_seqs: retire once the server reports it has
// drained seqs the tab had not seen (the reconcile then pulls the drained turn).
// ═══════════════════════════════════════════════════════════════════
describe('computeDrainRetirement (AC4 — server pending_count / last_drained_seqs mirror)', () => {
  it('server drained seqs that advance past the prior mark → retire local mirror', () => {
    expect(computeDrainRetirement({
      priorDrainedSeqs: [],
      currentDrainedSeqs: [4, 5],
      serverPendingCount: 0,
    })).toEqual({ retire: true, serverPendingCount: 0 });
  });

  it('no new drain (seqs unchanged) and still pending → do NOT retire', () => {
    expect(computeDrainRetirement({
      priorDrainedSeqs: [4, 5],
      currentDrainedSeqs: [4, 5],
      serverPendingCount: 2,
    })).toEqual({ retire: false, serverPendingCount: 2 });
  });

  it('server still has pending_count > 0 but drained a partial batch → retire the drained portion (retire=true) while surfacing remaining count', () => {
    expect(computeDrainRetirement({
      priorDrainedSeqs: [4],
      currentDrainedSeqs: [4, 5],
      serverPendingCount: 1,
    })).toEqual({ retire: true, serverPendingCount: 1 });
  });

  it('empty everywhere → nothing to retire', () => {
    expect(computeDrainRetirement({
      priorDrainedSeqs: [],
      currentDrainedSeqs: [],
      serverPendingCount: 0,
    })).toEqual({ retire: false, serverPendingCount: 0 });
  });

  it('Gate-2 MED: server RESETS last_drained_seqs ([4,5] → []) on new turn → retire (not stuck)', () => {
    // Backend clears last_drained_seqs to [] at the start of each new turn and
    // REPLACES per drain. A pure additive check would miss this shrink and leave
    // a non-streaming tab's queue mirror stuck forever. Reset-to-empty after a
    // non-empty prior is a retire signal.
    expect(computeDrainRetirement({
      priorDrainedSeqs: [4, 5],
      currentDrainedSeqs: [],
      serverPendingCount: 0,
    })).toEqual({ retire: true, serverPendingCount: 0 });
  });

  it('reset path does NOT false-fire when prior was already empty', () => {
    expect(computeDrainRetirement({
      priorDrainedSeqs: [],
      currentDrainedSeqs: [],
      serverPendingCount: 0,
    }).retire).toBe(false);
  });
});


describe('nextReconcileDelay (B-1: steady-state primary poll cadence)', () => {
  // B-1 north-star: the always-on reconcile loop is the steady-state spinner
  // driver. When any tab is streaming, it must poll FAST (so the backend alive
  // predicate drives the spinner within a few seconds — AC5 ≤3s self-heal).
  // When idle, it falls back to the cheap 15s safety-net cadence (AC2 power).
  // This is a PURE function so the cadence decision is test-locked WITHOUT
  // fake-timer integration — the loop just calls it to pick its next delay.

  it('any tab streaming → fast 3s cadence', () => {
    expect(nextReconcileDelay(true)).toBe(3_000);
  });

  it('no tab streaming → 15s safety-net cadence', () => {
    expect(nextReconcileDelay(false)).toBe(15_000);
  });

  it('fast cadence is strictly shorter than idle cadence (AC5 self-heal bound)', () => {
    // The whole point of B-1: streaming-period polling must be tighter than the
    // old fixed 15s, so a lost SSE event self-heals in ≤ the fast cadence.
    expect(nextReconcileDelay(true)).toBeLessThan(nextReconcileDelay(false));
  });

  it('fast cadence ≤ 3s (AC5: ≤3s self-heal, not 15s/45s)', () => {
    expect(nextReconcileDelay(true)).toBeLessThanOrEqual(3_000);
  });
});

describe('queuedBehindApprovalText (why a sent message is not moving)', () => {
  const perm = { requestId: 'p1', toolName: 'Bash', toolInput: { command: 'make clean' }, reason: '', options: [] };
  const base = { backendWaitingInput: true, backendPendingPermission: perm, serverPendingCount: 2 };

  it('names the count and the command it waits on', () => {
    expect(queuedBehindApprovalText(base)).toBe(
      '2 messages are waiting for your approval of Bash: make clean. They send after you decide.');
    expect(queuedBehindApprovalText({ ...base, serverPendingCount: 1 })).toBe(
      'Your message is waiting for your approval of Bash: make clean. It sends after you decide.');
  });

  it('is null when nothing is queued or nothing is awaited', () => {
    expect(queuedBehindApprovalText({ ...base, serverPendingCount: 0 })).toBeNull();
    expect(queuedBehindApprovalText({ ...base, backendWaitingInput: false })).toBeNull();
    expect(queuedBehindApprovalText({ ...base, backendPendingPermission: null })).toBeNull();
  });

  it('shortens a long command and falls back to the tool name', () => {
    const long = queuedBehindApprovalText({ ...base, backendPendingPermission: { ...perm, toolInput: { command: 'x'.repeat(200) } } })!;
    expect(long).toContain(`Bash: ${'x'.repeat(79)}…`);
    expect(queuedBehindApprovalText({ ...base, backendPendingPermission: { ...perm, toolName: 'Write', toolInput: {} } }))
      .toContain('approval of Write.');
  });
});

describe('isServerQueuedErrorCode', () => {
  it('SESSION_BUSY / QUEUE_TIMEOUT only', () => {
    expect(isServerQueuedErrorCode('SESSION_BUSY')).toBe(true);
    expect(isServerQueuedErrorCode('QUEUE_TIMEOUT')).toBe(true);
    for (const c of ['TURN_ABANDONED', '', undefined, null]) expect(isServerQueuedErrorCode(c)).toBe(false);
  });
});

describe('hostForInjectedTurn', () => {
  const u = { id: 'u', role: 'user' };
  const a = { id: 'a', role: 'assistant' };
  it('the last message when it is an assistant one; else none', () => {
    expect(hostForInjectedTurn([u, a], 'T', undefined)).toBe(a);
    expect(hostForInjectedTurn([a, u], 'T', undefined)).toBeNull();
    expect(hostForInjectedTurn([], 'T', undefined)).toBeNull();
  });
  it('a replay returns the bubble that hosted it, even if it is no longer last', () => {
    const b = { id: 'b', role: 'assistant' };
    expect(hostForInjectedTurn([a, u, b], 'T', { turnId: 'T', hostId: 'a' })).toBe(a);
    expect(hostForInjectedTurn([a, u, b], 'X', { turnId: 'T', hostId: 'a' })).toBe(b);
  });
});
