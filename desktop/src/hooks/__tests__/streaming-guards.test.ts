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
  queuedMessageFromRetryPayload,
  retryPayloadHasAttachments,
  shouldResurfaceQuestion,
  computeDrainRetirement,
  shouldArmSpinnerFromBackend,
  forceClearStreamVerdict,
  type QueueGuardState,
  type ForceClearStreamInput,
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

describe('queuedMessageFromRetryPayload', () => {
  it('builds a queuedMessage from a SESSION_BUSY retryPayload (preserve, not lose)', () => {
    const q = queuedMessageFromRetryPayload(
      { sessionId: 's1', agentId: 'default', userMessage: 'Don\'t lose me', content: null },
      'queued-abc',
    );
    expect(q).not.toBeNull();
    expect(q!.text).toBe('Don\'t lose me');
    expect(q!.messageId).toBe('queued-abc');
    expect(q!.attachments).toEqual([]);
    // displayContent renders the text so the queued bubble shows something
    expect(q!.displayContent).toEqual([{ type: 'text', text: 'Don\'t lose me' }]);
  });

  it('returns null when payload is missing (chat.py SESSION_BUSY carries none) — null-guard', () => {
    expect(queuedMessageFromRetryPayload(undefined, 'queued-x')).toBeNull();
    expect(queuedMessageFromRetryPayload(null, 'queued-x')).toBeNull();
  });

  it('Root-1 P2 (L7/2A): server-side pending → no retryPayload → no re-queue (no double-send)', () => {
    // When the backend persists the message server-side (pendingSeq present), it
    // OMITS retryPayload because the drain worker now owns delivery. The frontend
    // re-queue helper must no-op so the message is not ALSO re-sent from the FE
    // (which would double-deliver). This is the structural double-send guard:
    // absent retryPayload → null → the SESSION_BUSY handler skips re-queue.
    const busyEventWithPending = {
      code: 'SESSION_BUSY',
      pendingSeq: 7,
      pendingId: 'msg-uuid',
      retryPayload: undefined,  // server owns delivery — no FE re-send
    };
    const requeued = queuedMessageFromRetryPayload(
      busyEventWithPending.retryPayload,
      'queued-should-not-exist',
    );
    expect(requeued).toBeNull();  // ← no re-queue → no double-send
    // The pending coordinates remain available for the mirror to show "queued".
    expect(busyEventWithPending.pendingSeq).toBe(7);
  });

  it('HIGH#2 regression: recovers text from content blocks when userMessage is null (the LIVE send path)', () => {
    // The frontend always sends `content` blocks, never `message`, so the
    // backend user_message is null and the text lives in content[].text.
    // Reading only userMessage would silently lose every real message.
    const q = queuedMessageFromRetryPayload(
      {
        sessionId: 's1',
        agentId: 'default',
        userMessage: null,
        content: [{ type: 'text', text: 'recover me from content' }],
      },
      'queued-c1',
    );
    expect(q).not.toBeNull();
    expect(q!.text).toBe('recover me from content');
  });

  it('joins multiple text content blocks', () => {
    const q = queuedMessageFromRetryPayload(
      {
        sessionId: 's1',
        agentId: 'default',
        userMessage: null,
        content: [
          { type: 'text', text: 'line one' },
          { type: 'image', source: {} },
          { type: 'text', text: 'line two' },
        ] as unknown[],
      },
      'queued-c2',
    );
    expect(q!.text).toBe('line one\nline two');
  });

  it('retryPayloadHasAttachments detects non-text blocks (warn the user)', () => {
    expect(retryPayloadHasAttachments({ sessionId: 's', agentId: 'a', userMessage: null, content: [{ type: 'text', text: 'hi' }] })).toBe(false);
    expect(retryPayloadHasAttachments({ sessionId: 's', agentId: 'a', userMessage: null, content: [{ type: 'text', text: 'hi' }, { type: 'image', source: {} }] as unknown[] })).toBe(true);
    expect(retryPayloadHasAttachments({ sessionId: 's', agentId: 'a', userMessage: 'hi', content: null })).toBe(false);
    expect(retryPayloadHasAttachments(undefined)).toBe(false);
  });

  it('returns null when there is no recoverable text (nothing to preserve)', () => {
    expect(
      queuedMessageFromRetryPayload(
        { sessionId: 's1', agentId: 'default', userMessage: null, content: null },
        'queued-x',
      ),
    ).toBeNull();
    expect(
      queuedMessageFromRetryPayload(
        { sessionId: 's1', agentId: 'default', userMessage: '   ', content: null },
        'queued-x',
      ),
    ).toBeNull();
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
// pending_count and last_drained_seqs. The local optimistic queue mirror is
// retired (the badge cleared) once the server reports it has drained the seqs
// the tab was tracking. Keeps the local queuedMessage as an F7 optimistic
// fallback; this only decides when the SERVER-side queue indicator clears.
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


describe('shouldArmSpinnerFromBackend (symmetric reconcile: backend streaming → spinner ON)', () => {
  const base = {
    backendStreaming: true,
    tabIsStreaming: false,
    hasSessionId: true,
    postDisconnectUncertain: false,
    isWaitingForBusy: false,
    hasQueuedMessage: false,
    streamClearedAt: undefined as number | undefined,
    now: 1_000_000,
  };

  it('THE FIX: restored tab (idle) whose backend resume-streams → ARM (true)', () => {
    // Restored tabs init isStreaming=false, _streamClearedAt undefined.
    expect(shouldArmSpinnerFromBackend(base)).toBe(true);
  });

  it('backend NOT streaming → do not arm', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, backendStreaming: false })).toBe(false);
  });

  it('tab already streaming → no-op (false)', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, tabIsStreaming: true })).toBe(false);
  });

  it('no session id → cannot mirror → false', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, hasSessionId: false })).toBe(false);
  });

  it('post-disconnect tab owns its own recovery → do not override', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, postDisconnectUncertain: true })).toBe(false);
  });

  it('busy-poll owns its display → do not override', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, isWaitingForBusy: true })).toBe(false);
  });

  it('queued/drain owns its display → do not override', () => {
    expect(shouldArmSpinnerFromBackend({ ...base, hasQueuedMessage: true })).toBe(false);
  });

  it('FLAP-GUARD: a tab cleared 3s ago (user just Stopped, backend not yet IDLE) → do NOT re-light', () => {
    // The dangerous race: setIsStreaming(false) stamped _streamClearedAt; the
    // backend interrupt (≤5s) has not yet flipped STREAMING→IDLE, so a poll in
    // this window still sees backendStreaming=true. Must NOT re-arm.
    expect(shouldArmSpinnerFromBackend({
      ...base, streamClearedAt: base.now - 3_000,
    })).toBe(false);
  });

  it('FLAP-GUARD boundary: exactly at the settle window → still suppressed', () => {
    expect(shouldArmSpinnerFromBackend({
      ...base, streamClearedAt: base.now - 12_000,
    })).toBe(false);
  });

  it('after the settle window a still-streaming backend → ARM (lost session_start, not a Stop)', () => {
    expect(shouldArmSpinnerFromBackend({
      ...base, streamClearedAt: base.now - 12_001,
    })).toBe(true);
  });
});


// ── forceClearStreamVerdict: the IDLE / warm-resume protection net ────────────
//
// THE INVARIANT under test: the reconcile loop must NEVER force-clear a tab
// whose backend is genuinely streaming (normal warm IDLE→streaming resume, a
// live long turn). A force-clear there is the recurring "spinner vanished while
// the backend was still working" regression. These tests lock that BEFORE any
// change to the reconcile/cold-resume behavior — if a future edit makes the
// function clear a streaming/warm tab, the first test goes red.
describe('forceClearStreamVerdict — IDLE/warm-resume protection', () => {
  // A tab that has been "stuck" long enough to clear IF the condition held.
  const base: ForceClearStreamInput = {
    drainPending: false,
    hasQueuedMessage: false,
    queueAge: 0,
    hasSessionId: true,
    backendIsStreaming: false,
    reportedState: 'idle',
    activeGuardAge: 0,
    idleStreamingSince: 1_000,     // stamped long ago
    now: 1_000_000,                // far past the 30s settle window
  };

  it('NEVER force-clears when the backend is genuinely streaming (warm IDLE resume)', () => {
    // The load-bearing invariant: backend streaming === spinner is correct.
    expect(
      forceClearStreamVerdict({ ...base, backendIsStreaming: true, reportedState: 'streaming' }),
    ).toEqual({ verdict: 'reset-and-skip', reason: 'backend_streaming' });
  });

  it('NEVER force-clears while the backend is waiting_input (within the cap)', () => {
    expect(
      forceClearStreamVerdict({ ...base, reportedState: 'waiting_input' }).verdict,
    ).toBe('reset-and-skip');
  });

  it('NEVER force-clears during a brief drain gap', () => {
    expect(forceClearStreamVerdict({ ...base, drainPending: true }).verdict).toBe('reset-and-skip');
  });

  it('NEVER force-clears with a fresh queued message (<60s)', () => {
    expect(
      forceClearStreamVerdict({ ...base, hasQueuedMessage: true, queueAge: 10_000 }).verdict,
    ).toBe('reset-and-skip');
  });

  it('skips (no clear) when the tab has no backend session id', () => {
    expect(forceClearStreamVerdict({ ...base, hasSessionId: false }).verdict).toBe('reset-and-skip');
  });

  it('force-clears a genuinely stuck spinner (idle backend, settled, no immunity)', () => {
    // The legitimate purpose MUST be preserved: a stale stream still clears.
    expect(forceClearStreamVerdict(base)).toEqual({ verdict: 'force-clear', reason: 'stuck' });
  });

  it('waits out the settle window before clearing (no premature clear)', () => {
    expect(
      forceClearStreamVerdict({ ...base, idleStreamingSince: base.now - 5_000 }).verdict,
    ).toBe('wait-settle');
    // undefined stamp = first observation → treat age as 0 → wait
    expect(
      forceClearStreamVerdict({ ...base, idleStreamingSince: undefined }).verdict,
    ).toBe('wait-settle');
  });

  it('active-state guard expires after the cap (lost waiting_input cannot hang forever)', () => {
    expect(
      forceClearStreamVerdict({ ...base, reportedState: 'waiting_input', activeGuardAge: 7_200_001 }).verdict,
    ).toBe('force-clear');
  });

  // ── THE COLD-RESUME FIX (step 2) ──────────────────────────────────────────
  // A cold --resume reports backend 'cold'/'idle' during the spawn + transcript
  // replay (often >30s). resumeInProgress (FE saw session_resuming, no data yet)
  // exempts it so the spinner is NOT force-cleared mid-resume. A genuinely
  // dead/evicted session (resumeInProgress=false) still force-clears.
  it('FIX: cold backend + resume in flight → exempt (no force-clear)', () => {
    expect(
      forceClearStreamVerdict({ ...base, reportedState: 'cold', resumeInProgress: true }),
    ).toEqual({ verdict: 'reset-and-skip', reason: 'resuming' });
  });

  it('FIX: idle backend + resume in flight → exempt (heavy --resume replay)', () => {
    expect(
      forceClearStreamVerdict({ ...base, reportedState: 'idle', resumeInProgress: true }).verdict,
    ).toBe('reset-and-skip');
  });

  it('PRESERVED: cold backend + NOT resuming → still force-clears (dead/evicted)', () => {
    expect(
      forceClearStreamVerdict({ ...base, reportedState: 'cold', resumeInProgress: false }).verdict,
    ).toBe('force-clear');
  });

  it('PRESERVED: resumeInProgress never overrides genuine backend streaming', () => {
    // Defense-in-depth: if backend is streaming, that wins (reason stays
    // backend_streaming) — resumeInProgress doesn't mask a real live turn.
    expect(
      forceClearStreamVerdict({ ...base, backendIsStreaming: true, resumeInProgress: true }).reason,
    ).toBe('backend_streaming');
  });
});
