/**
 * streaming-guards.ts — pure predicates for the chat send path.
 *
 * Single responsibility: decide whether a user's send must be QUEUED (the
 * session is busy or in an uncertain post-disconnect state) rather than sent
 * directly. Extracting this as a pure function makes the decision unit-testable
 * against the exact tab states the SSE-disconnect bug produces.
 *
 * Background: a long agent turn can outlive the SSE connection. When the
 * connection drops mid-stream the backend subprocess stays alive (still
 * STREAMING), but the frontend's heal-grace timer eventually expires and used
 * to mark the tab fully idle (isStreaming=false). A follow-up send then escaped
 * to the normal send path → backend SessionBusyError → the orphan DB row was
 * deleted → the user's message was silently lost.
 *
 * The fix has two halves:
 *   1. On heal-grace expiry, set `_postDisconnectUncertain` instead of going
 *      fully idle, so the tab still reflects "backend may be streaming."
 *   2. `shouldQueueSend` includes that flag, so the follow-up is queued (and
 *      drained when the backend genuinely finishes) instead of escaping.
 */

import type { ContentBlock, UnifiedAttachment } from '../types';

/** Shape of the retryPayload the backend attaches to a SESSION_BUSY (and
 *  QUEUE_TIMEOUT) SSE event so the frontend can recover the user's message. */
export interface SessionBusyRetryPayload {
  sessionId: string;
  agentId: string;
  userMessage: string | null;
  content: unknown[] | null;
}

/** The queuedMessage shape consumed by drainQueuedMessage. */
export interface QueuedMessage {
  text: string;
  attachments: UnifiedAttachment[];
  displayContent: ContentBlock[];
  messageId: string;
}

/** Extract the user's text from retryPayload, falling back to content blocks.
 *
 * CRITICAL: the live send path transmits `content` blocks, NOT `message`
 * (see chat.ts streamChat — it uses requestBody.content for any real message
 * and only uses requestBody.message as a legacy fallback). So on the SESSION_BUSY
 * path the backend's `user_message` is almost always null and the text lives in
 * `content[].text`. Reading only `userMessage` would silently lose every real
 * message — the exact failure this fix exists to prevent. We therefore prefer
 * userMessage but fall back to joining the text-type content blocks.
 */
function extractRetryText(payload: SessionBusyRetryPayload): string {
  const direct = (payload.userMessage ?? '').trim();
  if (direct) return direct;
  if (Array.isArray(payload.content)) {
    const joined = payload.content
      .map((block) => {
        if (block && typeof block === 'object' && 'text' in block) {
          const t = (block as { text?: unknown }).text;
          return typeof t === 'string' ? t : '';
        }
        return '';
      })
      .filter(Boolean)
      .join('\n');
    return joined.trim();
  }
  return '';
}

/** True if the retryPayload carried non-text content blocks (image/document).
 *  On the SESSION_BUSY re-queue path the original send already left the client,
 *  so the composer no longer holds these — the caller should WARN the user that
 *  an attachment couldn't be auto-recovered (text IS recovered; blobs are not). */
export function retryPayloadHasAttachments(
  payload: SessionBusyRetryPayload | null | undefined,
): boolean {
  if (!payload || !Array.isArray(payload.content)) return false;
  return payload.content.some(
    (block) =>
      block != null &&
      typeof block === 'object' &&
      'type' in block &&
      (block as { type?: unknown }).type !== 'text',
  );
}

/**
 * Build a queuedMessage from a SESSION_BUSY retryPayload so a busy-session send
 * is never lost. Returns null when there is nothing recoverable (payload absent
 * — e.g. the chat.py "Cannot send() in state" SESSION_BUSY carries none — or no
 * non-empty text in either userMessage or content). Callers MUST null-guard.
 *
 * Recovers TEXT only. Binary attachments (image/document blocks) are NOT
 * recovered — the backend round-trips text-path hints, not blobs, and on this
 * path the composer was already cleared by the original send. Callers should use
 * retryPayloadHasAttachments() to warn the user when blobs were dropped.
 */
export function queuedMessageFromRetryPayload(
  payload: SessionBusyRetryPayload | null | undefined,
  messageId: string,
): QueuedMessage | null {
  if (!payload) return null;
  const text = extractRetryText(payload);
  if (!text) return null;
  return {
    text,
    attachments: [],
    displayContent: [{ type: 'text', text }],
    messageId,
  };
}

/** Minimal slice of UnifiedTab needed to decide queue-vs-send. */
export interface QueueGuardState {
  readonly isStreaming: boolean;
  isWaitingForBusy?: boolean;
  isReconnecting?: boolean;
  _healGraceActive?: boolean;
  /** Set on heal-grace expiry: the SSE connection is gone but the backend
   *  subprocess may still be streaming. Cleared when a new stream starts or the
   *  reconcile loop confirms the backend is idle. While true, sends must queue
   *  so they never escape to a normal send → SESSION_BUSY → orphan delete. */
  _postDisconnectUncertain?: boolean;
}

/**
 * True when a user's send must be QUEUED rather than sent directly.
 *
 * Covers every state in which the backend session may still be busy:
 *  - isStreaming: actively generating
 *  - isWaitingForBusy: SESSION_BUSY recovery poll in progress
 *  - isReconnecting: SSE connection-phase retry in progress
 *  - _healGraceActive: mid-stream disconnect, within the self-heal grace window
 *  - _postDisconnectUncertain: heal-grace expired but backend may still stream
 *    (THE bug-state — all visible flags false, backend not confirmed idle)
 */
export function shouldQueueSend(tab: QueueGuardState): boolean {
  return (
    tab.isStreaming ||
    tab.isWaitingForBusy === true ||
    tab.isReconnecting === true ||
    tab._healGraceActive === true ||
    tab._postDisconnectUncertain === true
  );
}

// ── Root-1 SSOT Phase 3: AC5 — lost AskUserQuestion re-surface ──────

/** Inputs to the re-surface decision (from the streaming-state read API +
 *  the tab's current pending question). */
export interface ResurfaceQuestionInput {
  /** Backend reports state === 'waiting_input'. */
  backendWaitingInput: boolean;
  /** Authoritative pending question payload from the read API (or null). */
  backendPendingQuestion: { toolUseId: string; questions: unknown[] } | null;
  /** The toolUseId the tab currently shows as pending (null if none). */
  currentPendingToolUseId: string | null;
  /** toolUseId of a question the user JUST answered, if any. Suppresses
   *  re-surface of that same question for the one poll window during which the
   *  backend mirror may still report waiting_input before it transitions. */
  answeredToolUseId?: string | null;
}

/**
 * Decide whether to re-surface an AskUserQuestion from the authoritative
 * backend mirror. The reconcile loop polls every 15s; this gate makes the
 * re-surface idempotent (keyed on toolUseId) so a still-open question does not
 * flap, and respects an answer-in-flight signal so a just-answered question is
 * not re-injected before the backend transitions out of waiting_input.
 *
 * Re-surface ONLY when ALL hold:
 *  - backend is waiting_input (the question is genuinely open server-side)
 *  - a pending_question payload exists (there is something to render)
 *  - the payload's toolUseId differs from what the tab already shows (idempotent)
 *  - the payload's toolUseId is not the one the user just answered (no flap)
 */
export function shouldResurfaceQuestion(input: ResurfaceQuestionInput): boolean {
  const { backendWaitingInput, backendPendingQuestion, currentPendingToolUseId, answeredToolUseId } = input;
  if (!backendWaitingInput) return false;
  if (!backendPendingQuestion) return false;
  // Defense-in-depth (the chat.ts boundary already maps permission prompts to
  // null): an AskUserQuestion MUST have ≥1 question. A command-permission prompt
  // shares the WAITING_INPUT state but carries no questions — it has its own
  // render path and must never be re-surfaced as a question.
  if (!Array.isArray(backendPendingQuestion.questions) || backendPendingQuestion.questions.length === 0) {
    return false;
  }
  const id = backendPendingQuestion.toolUseId;
  if (!id) return false;
  if (id === currentPendingToolUseId) return false; // already shown — idempotent
  if (answeredToolUseId && id === answeredToolUseId) return false; // answer-in-flight
  return true;
}

// ── Root-1 SSOT Phase 3: AC4 — server pending_count / last_drained_seqs mirror ──

/** Inputs to the drain-retirement decision. */
export interface DrainRetirementInput {
  /** The last_drained_seqs the tab observed on the previous poll. */
  priorDrainedSeqs: number[];
  /** The last_drained_seqs the backend reports now. */
  currentDrainedSeqs: number[];
  /** The backend's current pending_count (messages still unsent server-side). */
  serverPendingCount: number;
}

/** Result of the drain-retirement decision. */
export interface DrainRetirementResult {
  /** True if the server drained a NEW seq since the prior observation — the
   *  local optimistic queue mirror for the drained message(s) should retire. */
  retire: boolean;
  /** The server's current pending_count, surfaced for the "queued" badge. */
  serverPendingCount: number;
}

/**
 * Mirror the server's drain progress. The local optimistic queue mirror retires
 * (the per-message "queued" affordance clears) once the server confirms it has
 * drained a seq the tab was tracking — i.e. currentDrainedSeqs contains a seq
 * not present in priorDrainedSeqs. serverPendingCount is passed through so the
 * caller can drive a session-level "N queued" badge from authoritative state.
 *
 * This does NOT remove the local queuedMessage path (kept as the F7 optimistic
 * fallback); it only decides when the SERVER-owned queue indicator clears.
 */
export function computeDrainRetirement(input: DrainRetirementInput): DrainRetirementResult {
  const { priorDrainedSeqs, currentDrainedSeqs, serverPendingCount } = input;
  const prior = new Set(priorDrainedSeqs);
  // A drain is signalled by a NEW seq appearing...
  const additive = currentDrainedSeqs.some((seq) => !prior.has(seq));
  // ...OR by a RESET: the backend clears last_drained_seqs to [] at the start of
  // each new turn (session_unit drops the stale hint) and REPLACES (not appends)
  // per drain. So [4,5] → [] means the prior drain completed and a new turn began;
  // a subsequent turn can even re-drain the same low seq numbers. Treating only
  // additive changes as retire would leave a queue mirror stuck forever on a
  // non-streaming tab (it can't reach the >60s force-clear). A shrink-to-empty
  // after a non-empty prior is therefore also a retire signal.
  const resetToEmpty = priorDrainedSeqs.length > 0 && currentDrainedSeqs.length === 0;
  return { retire: additive || resetToEmpty, serverPendingCount };
}


// ── Symmetric reconcile: backend-streaming → arm the spinner ──────────────────

/** Inputs to the idle→streaming re-arm decision (reconcile loop). */
export interface ArmSpinnerInput {
  /** Backend mirror reports state === 'streaming' for this tab's session. */
  backendStreaming: boolean;
  /** The tab currently shows a spinner (isStreaming flag). */
  tabIsStreaming: boolean;
  /** The tab has a backend session id to mirror. */
  hasSessionId: boolean;
  /** Tab is in post-disconnect recovery (owns its own reconcile path). */
  postDisconnectUncertain: boolean;
  /** Tab is in SESSION_BUSY polling (busy-poll owns its display). */
  isWaitingForBusy: boolean;
  /** Tab has a queued/optimistic message (queue/drain owns its display). */
  hasQueuedMessage: boolean;
  /** Timestamp of the last setIsStreaming(false), or undefined if never. */
  streamClearedAt: number | undefined;
  /** Current time (injected for deterministic tests). */
  now: number;
  /** Settle window after a clear during which a re-arm is suppressed (ms). */
  settleMs?: number;
}

/**
 * Decide whether the reconcile loop should turn the spinner ON for a tab whose
 * frontend shows idle while the backend mirror reports it is actively streaming.
 *
 * This is the SYMMETRIC partner of the force-clear path (which turns a stuck
 * spinner OFF when the backend is idle). It fixes "response renders but no
 * spinner" after a restart — restored tabs init isStreaming=false while their
 * backend session resume-streams, and no SSE session_start arrives to set it.
 *
 * Arm ONLY when ALL hold:
 *  - backend is genuinely streaming (authoritative)
 *  - the tab is NOT already showing a spinner (no-op otherwise)
 *  - the tab has a session id to mirror
 *  - the tab is not in a path that owns its own display (post-disconnect,
 *    busy-poll, queued/drain) — those must not be overridden
 *  - the flap-guard window has elapsed since the last clear, so a user Stop
 *    (frontend idle) is not re-lit during the ~5s before the backend goes IDLE
 *
 * Safe by construction: if a re-arm is ever wrong, the backend will report idle
 * and the force-clear path turns the spinner back off — never a permanent hang.
 */
export function shouldArmSpinnerFromBackend(input: ArmSpinnerInput): boolean {
  const {
    backendStreaming, tabIsStreaming, hasSessionId,
    postDisconnectUncertain, isWaitingForBusy, hasQueuedMessage,
    streamClearedAt, now, settleMs = 12_000,
  } = input;
  if (!backendStreaming) return false;
  if (tabIsStreaming) return false;       // already showing — no-op
  if (!hasSessionId) return false;
  if (postDisconnectUncertain) return false;
  if (isWaitingForBusy) return false;
  if (hasQueuedMessage) return false;
  if (now - (streamClearedAt ?? 0) <= settleMs) return false; // flap-guard
  return true;
}


// ── Force-clear reconcile decision (extracted for test-locking) ───────────────

/** Inputs to the force-clear decision (the reconcile loop's stuck-spinner path).
 *  All values are read by the caller from tab state + the backend mirror; the
 *  decision itself is pure so the IDLE/warm-resume invariant can be locked by a
 *  unit test (a force-clear during a live turn was the recurring regression). */
export interface ForceClearStreamInput {
  /** Tab has a drain scheduled (result arrived, new stream not yet started). */
  drainPending: boolean;
  /** Tab has an optimistic queued message. */
  hasQueuedMessage: boolean;
  /** ms since the message was queued (0 if none). */
  queueAge: number;
  /** Tab has a backend session id to mirror. */
  hasSessionId: boolean;
  /** Backend mirror reports state === 'streaming' for this tab's session. */
  backendIsStreaming: boolean;
  /** Backend mirror's reported state string (e.g. 'cold','idle','streaming',
   *  'waiting_input'), or undefined if the session is missing/evicted. */
  reportedState: string | undefined;
  /** A resume is genuinely IN FLIGHT for this tab (the FE saw session_resuming
   *  and has not yet received data; bounded by its own 60s resume timeout).
   *  During a cold --resume the backend mirror reports 'cold'/'idle' for the
   *  spawn + transcript-replay window (often >30s on heavy sessions) even though
   *  it IS working — without this the reconciler force-clears the spinner mid-
   *  resume ("resume needs two sends / spinner vanished"). This flag
   *  disambiguates a spawning resume from a genuinely dead/evicted session. */
  resumeInProgress?: boolean;
  /** Backend mirror reports the subprocess is still flushing a long turn
   *  post-disconnect (CLEAN-IDLE — state==='idle', streaming===false — but ALIVE,
   *  finishing the answer into the DB). This is the OT01 case: heal-grace expiry
   *  kept the spinner (isStreaming stays true) and handed off here, but the unit
   *  reports clean-idle, so without this exemption the reconcile loop force-clears
   *  the spinner MID-FLUSH and the answer appears truncated until a later tick.
   *  Same CLEAN-IDLE-but-alive family as `active_backend`; reuses the same
   *  activeGuardAge/activeGuardMaxMs (120-min) cap — NO new timer (Gate-1 B4/Q4). */
  postDisconnectFlushing?: boolean;
  /** ms since the tab's reconcile stream-start stamp (for the active-state cap). */
  activeGuardAge: number;
  /** Reconcile-owned stamp of when the stuck condition was first observed. */
  idleStreamingSince: number | undefined;
  /** SEND-owned absolute hard-cap clock (OT01 route-A): when this turn's stream
   *  genuinely started. Distinct from idleStreamingSince — it is NOT reset by the
   *  reconcile loop's reset-and-skip churn, so under abort+recycle churn (where the
   *  backend momentarily reports streaming between turns and keeps restarting the
   *  settle window) it still provides an absolute upper bound. Consulted ONLY after
   *  all four alive guards (backend_streaming/active_backend/flushing/resuming), so
   *  it can NEVER force-clear a genuinely-live turn. undefined → hard-cap disabled
   *  (opt-in): a tab that never stamped it falls through to the normal settle path. */
  streamingSinceHardStart?: number;
  /** Current time (injected for deterministic tests). */
  now: number;
  /** Queue immunity window (ms). Default 60s. */
  queueImmunityMs?: number;
  /** Settle window before force-clear (ms). Default 30s. */
  settleMs?: number;
  /** Cap on the active-state guard (ms) so a lost waiting_input can't hang
   *  forever. Default 120min. */
  activeGuardMaxMs?: number;
  /** Absolute hard-cap (ms) measured from streamingSinceHardStart. Once a
   *  frontend-streaming tab whose backend is NOT alive (not streaming/active/
   *  flushing/resuming) exceeds this, force-clear EVEN IF churn keeps resetting
   *  the settle clock. Default 120s. Fires only after the four alive guards. */
  hardCapMs?: number;
}

/** Verdict for the reconcile loop's stuck-spinner path.
 *  - 'reset-and-skip': condition does NOT hold — clear the backstop clock, skip.
 *  - 'wait-settle': stuck but within the settle window — keep the clock, skip
 *    (caller stamps idleStreamingSince if undefined).
 *  - 'force-clear': stuck past the settle window — force endStreaming. */
export type ForceClearVerdict = 'reset-and-skip' | 'wait-settle' | 'force-clear';

/** Why the verdict was reached. Observable so tests (and logs) can assert the
 *  exact guard that fired without a duplicated decision mirror. */
export type ForceClearReason =
  | 'drain_or_queue'    // drain gap or fresh (<60s) queued message
  | 'no_session'        // tab has no backend session id
  | 'backend_streaming' // backend mirror reports streaming — spinner is correct
  | 'active_backend'    // backend in an active state (waiting_input) within cap
  | 'flushing'          // backend clean-idle but flushing a long turn post-disconnect (OT01)
  | 'resuming'          // a resume is in flight — cold/idle is spawn, not stuck
  | 'too_fresh'         // stuck but within the settle window
  | 'hard_cap'          // churn-immune absolute cap exceeded (backend not alive) → force-clear
  | 'stuck';            // stuck past the settle window → force-clear

/** Result of the force-clear decision: the verdict (drives the hook) plus the
 *  reason (single-source observability — no separate decision mirror). */
export interface ForceClearDecision {
  verdict: ForceClearVerdict;
  reason: ForceClearReason;
}

/**
 * Decide whether the reconcile loop should force-clear a tab's spinner.
 *
 * INVARIANT (the one this exists to protect): a tab whose backend is genuinely
 * streaming — i.e. a normal warm IDLE→streaming resume, a live long turn — must
 * NEVER be force-cleared. `backendIsStreaming` (and the active-state guard for
 * waiting_input) short-circuit to 'reset-and-skip' before any settle/clear path.
 *
 * Faithful extraction of the prior inline logic (order preserved):
 *   drain → queue(<60s) → no-sid → backend-streaming → active-state(capped)
 *   → settle-window → force-clear.
 */
export function forceClearStreamVerdict(input: ForceClearStreamInput): ForceClearDecision {
  const {
    drainPending, hasQueuedMessage, queueAge, hasSessionId,
    backendIsStreaming, reportedState, resumeInProgress, activeGuardAge,
    postDisconnectFlushing = false,
    idleStreamingSince, streamingSinceHardStart, now,
    queueImmunityMs = 60_000, settleMs = 30_000, activeGuardMaxMs = 7_200_000,
    hardCapMs = 120_000,
  } = input;

  // Drain gap / fresh queue / no session = intentional or unknown, never stuck.
  if (drainPending) return { verdict: 'reset-and-skip', reason: 'drain_or_queue' };
  if (hasQueuedMessage && queueAge < queueImmunityMs) {
    return { verdict: 'reset-and-skip', reason: 'drain_or_queue' };
  }
  if (!hasSessionId) return { verdict: 'reset-and-skip', reason: 'no_session' };

  // Backend genuinely streaming → the spinner is CORRECT. Never clear.
  if (backendIsStreaming) return { verdict: 'reset-and-skip', reason: 'backend_streaming' };

  // Active backend states (subprocess alive + working) are exempt, time-capped
  // so a lost waiting_input event can't hang the spinner forever.
  const ACTIVE_BACKEND_STATES = new Set(['waiting_input', 'streaming']);
  if (reportedState && ACTIVE_BACKEND_STATES.has(reportedState)
      && activeGuardAge < activeGuardMaxMs) {
    return { verdict: 'reset-and-skip', reason: 'active_backend' };
  }

  // Post-disconnect flush (OT01): the unit is CLEAN-IDLE (streaming=false,
  // state='idle') but its subprocess is still finishing a long turn into the DB
  // after the SSE dropped. Heal-grace expiry deliberately KEPT the spinner
  // (isStreaming stays true) for exactly this case — so force-clearing here would
  // re-introduce the truncated-content / premature-stop half of OT01. Same
  // alive-but-clean-idle family as active_backend; bounded by the SAME 120-min
  // cap so a stuck flushing flag can't hang the spinner forever (no new timer).
  if (postDisconnectFlushing && activeGuardAge < activeGuardMaxMs) {
    return { verdict: 'reset-and-skip', reason: 'flushing' };
  }

  // A resume genuinely in flight: the backend reports 'cold'/'idle' during the
  // spawn + --resume replay window, but it IS working. Exempt so the spinner is
  // not force-cleared mid-resume. Bounded by the FE's own 60s resume timeout,
  // which clears isResuming → the reconciler resumes force-clearing if it then
  // turns out genuinely stuck. A dead/evicted session has isResuming=false and
  // still force-clears below.
  if (resumeInProgress) return { verdict: 'reset-and-skip', reason: 'resuming' };

  // HARD-CAP (OT01 route-A): we are PAST all four alive guards, so the backend is
  // provably NOT alive (not streaming / not active / not flushing / not resuming).
  // The settle clock (idleStreamingSince) is reset to undefined on every
  // reset-and-skip tick, so under abort+recycle churn it never accumulates to
  // settleMs and force-clear is never reached — the "stuck 10+ min" case. The
  // SEND-owned streamingSinceHardStart is NOT reset by that churn, so once it
  // exceeds hardCapMs we force-clear regardless of the settle window. Opt-in:
  // undefined (tab never stamped it) falls through to the normal settle path.
  // Placed AFTER the alive guards by construction — it can never clear a live turn.
  if (streamingSinceHardStart !== undefined && now - streamingSinceHardStart > hardCapMs) {
    return { verdict: 'force-clear', reason: 'hard_cap' };
  }

  // Stuck condition holds (frontend streaming, backend not). Honor the settle
  // window anchored to the reconcile-owned stamp.
  const streamAge = idleStreamingSince === undefined ? 0 : now - idleStreamingSince;
  if (streamAge < settleMs) return { verdict: 'wait-settle', reason: 'too_fresh' };
  return { verdict: 'force-clear', reason: 'stuck' };
}


// ── Heal-grace expiry: honest error signal (OT01) ────────────────────────────

/** Inputs to the heal-grace expiry decision. Read by the caller from the live
 *  backend mirror (chatService.getStreamingState) for the tab's session, plus a
 *  fail-safe for the query itself. Pure so the alive-vs-dead decision can be
 *  unit-locked — surfacing a "Connection lost" error while the backend turn was
 *  STILL producing the answer was the OT01 user-facing bug. */
export interface HealGraceExpiryInput {
  /** Backend mirror reports state === 'streaming' for this session. */
  backendIsStreaming: boolean;
  /** Backend mirror reports state === 'waiting_input'. */
  backendWaitingInput: boolean;
  /** Backend mirror reports the subprocess is still flushing a long turn
   *  post-disconnect (CLEAN-IDLE but alive). The signal the old code lacked. */
  postDisconnectFlushing: boolean;
  /** The getStreamingState() query itself failed/threw at expiry. Fail-safe:
   *  we cannot prove the backend is alive, so do NOT keep the spinner forever —
   *  fall through to the error path (never strand the user). */
  queryFailed: boolean;
}

/** Verdict for heal-grace expiry.
 *  - 'still-working': backend is provably alive (streaming/waiting/flushing) →
 *    keep the spinner, NO error toast; the 15s reconcile loop owns recovery.
 *  - 'show-error': backend is genuinely done/dead (or the liveness query failed)
 *    → run the existing error path (clear streaming + error toast). */
export type HealGraceVerdict = 'still-working' | 'show-error';

/**
 * Decide what heal-grace expiry should do, given the authoritative backend state.
 *
 * INVARIANT (the OT01 bug this exists to kill): when the backend turn is still
 * alive — streaming, waiting on input, OR flushing a long turn post-disconnect —
 * expiry must NOT show a "Connection lost" error and must NOT stop the spinner.
 * The error is only honest when the backend is genuinely done (or we cannot tell,
 * in which case fail-safe to the error so the spinner never hangs forever).
 *
 * This is a ONE-SHOT decision (not a re-arming poller): it consults state once at
 * expiry and hands ongoing recovery to the existing 15s reconcile owner via
 * `_postDisconnectUncertain` — so there is no second owner of the recovery clock
 * and no competing cap (Gate-1 B4/Q4). The long-turn ceiling is the reconcile
 * loop's existing 120-min cap, not a new short timer (Gate-1 Q4).
 */
export function healGraceExpiryVerdict(input: HealGraceExpiryInput): HealGraceVerdict {
  const { backendIsStreaming, backendWaitingInput, postDisconnectFlushing, queryFailed } = input;
  // Fail-safe: a failed liveness query means we cannot prove the backend is
  // alive → show the error (never keep a spinner up on an unprovable state).
  if (queryFailed) return 'show-error';
  if (backendIsStreaming || backendWaitingInput || postDisconnectFlushing) {
    return 'still-working';
  }
  return 'show-error';
}

// ── Store↔React desync convergence (OT01 sibling path) ───────────────────────

/** Inputs to the desync-convergence decision. The reconcile loop fires this when
 *  a tab shows isStreaming=true but its MessageStore flipped to phase='idle'
 *  (the watchdog's 90s silent endStreaming). Normally that divergence means the
 *  turn is OVER and the spinner should converge to idle. The exception is OT01:
 *  the turn's SSE dropped but the backend is STILL alive flushing the answer —
 *  converging there drops the spinner mid-flush (truncated content). */
export interface DesyncConvergeInput {
  /** MessageStore reports phase==='idle' for this tab. */
  storeIdle: boolean;
  /** ms since the tab's reconcile stream-start stamp (same anchor as the
   *  force-clear 120-min cap). Distinguishes a just-started turn (<10s grace,
   *  store not yet 'streaming') from a real watchdog-fire desync. */
  streamStartAge: number;
  /** Backend mirror reports state==='streaming'. */
  backendIsStreaming: boolean;
  /** Backend mirror reports state==='waiting_input'. */
  backendWaitingInput: boolean;
  /** Backend mirror reports the subprocess is still flushing a long turn
   *  post-disconnect (clean-idle but ALIVE) — the OT01 signal. */
  postDisconnectFlushing: boolean;
  /** Grace before treating idle-store-while-streaming as a desync. Default 10s
   *  (a just-sent turn awaits buildContentArray before store.startStreaming). */
  graceMs?: number;
  /** Cap: above this the turn is presumed dead regardless of an alive mirror, so
   *  a stuck backend flag can't hang the spinner. Default 120min (matches the
   *  force-clear activeGuardMaxMs). */
  capMs?: number;
}

/**
 * Decide whether the reconcile loop should CONVERGE a store↔React desync
 * (force isStreaming=false because the store says the turn is over).
 *
 * Returns true → converge (stop the spinner). false → leave it (either too fresh,
 * or the backend is provably still alive and converging would truncate).
 *
 * INVARIANT (OT01): never converge while the backend is genuinely alive
 * (streaming / waiting_input / post-disconnect flushing) within the cap — that is
 * the long-turn-outlives-SSE case the spinner must survive. Past the cap, a stuck
 * alive-flag cannot hang the spinner: converge anyway.
 */
export function desyncConvergeVerdict(input: DesyncConvergeInput): boolean {
  const {
    storeIdle, streamStartAge, backendIsStreaming, backendWaitingInput,
    postDisconnectFlushing, graceMs = 10_000, capMs = 7_200_000,
  } = input;
  if (!storeIdle) return false;            // store still streaming → no desync
  if (streamStartAge < graceMs) return false;  // just-started turn → not a desync
  const backendAlive = backendIsStreaming || backendWaitingInput || postDisconnectFlushing;
  if (backendAlive && streamStartAge < capMs) return false;  // alive → keep spinner
  return true;                             // genuinely over (or past cap) → converge
}
