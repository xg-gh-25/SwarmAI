/**
 * streaming-guards.ts — pure predicates for the chat send path.
 *
 * Single responsibility: decide whether a user's send goes the BUSY way (the
 * session is busy or in an uncertain post-disconnect state: the message is POSTed
 * on its own and the server queues it, docs/design/chat-turn-liveness.md S3)
 * rather than as this tab's own turn. Extracting this as a pure function makes the decision unit-testable
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

import type { PendingPermission } from '../types';

/** Minimal slice of UnifiedTab needed to decide busy-send vs send. */
export interface QueueGuardState {
  /** a turn of the session is in progress (backend activity) */
  readonly isStreaming: boolean;
  /** this tab's own POST is open */
  readonly localSending?: boolean;
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
 * True when a user's send must not start this tab's own turn (it goes as a busy
 * send: POSTed on its own, queued by the server).
 *
 * Covers every state in which the backend session may still be busy:
 *  - isStreaming: a turn of the session is in progress (backend activity)
 *  - localSending: this tab's own POST is still open
 *  - isWaitingForBusy: SESSION_BUSY recovery poll in progress
 *  - isReconnecting: SSE connection-phase retry in progress
 *  - _healGraceActive: mid-stream disconnect, within the self-heal grace window
 *  - _postDisconnectUncertain: heal-grace expired but backend may still stream
 *    (THE bug-state — all visible flags false, backend not confirmed idle)
 */
export function shouldQueueSend(tab: QueueGuardState): boolean {
  return (
    tab.isStreaming ||
    tab.localSending === true ||
    tab.isWaitingForBusy === true ||
    tab.isReconnecting === true ||
    tab._healGraceActive === true ||
    tab._postDisconnectUncertain === true
  );
}

/**
 * May the tab send the continuation of its session's turn (an answer, a permission
 * decision)? Not while its own request is open; not while a turn runs that is not
 * waiting for input. While the session waits for input the bee is on (its activity
 * is waiting_input), and that is exactly when the continuation is due.
 */
export function canContinueTurn(tab: { isStreaming: boolean; localSending?: boolean; activity?: string }): boolean {
  if (tab.localSending) return false;
  return !tab.isStreaming || tab.activity === 'waiting_input';
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

// ── Command-permission re-surface (core/permission_prompt, 2026-10-22) ──────────

/** Inputs to the permission re-surface decision. */
export interface ResurfacePermissionInput {
  backendWaitingInput: boolean;
  /** The prompt the backend is blocked on (streaming-state), if any. */
  backendPendingPermission: { requestId: string } | null | undefined;
  /** The prompt this tab already offers (tabState.pendingPermissionRequestId). */
  currentPendingRequestId: string | null;
  /** The prompt the user just decided (decision in flight: the backend may still
   *  report waiting_input for a moment). */
  decidedRequestId?: string | null;
}

/**
 * Re-surface a command-permission prompt the tab never got (its live event went to
 * a CLI-initiated turn this tab did not render, another tab, or was lost to a
 * reload). Same gate as a question: ONLY when the backend is waiting on that exact
 * request, the tab does not already offer it, and the user has not just decided
 * it. Idempotent, so the 15s poll never flaps the card.
 */
export function shouldResurfacePermission(input: ResurfacePermissionInput): boolean {
  const { backendWaitingInput, backendPendingPermission, currentPendingRequestId, decidedRequestId } = input;
  if (!backendWaitingInput || !backendPendingPermission) return false;
  const id = backendPendingPermission.requestId;
  if (!id) return false;
  if (id === currentPendingRequestId) return false;
  if (decidedRequestId && id === decidedRequestId) return false;
  return true;
}

/** Inputs to {@link queuedBehindApprovalText}. */
export interface QueuedBehindApprovalInput {
  backendWaitingInput: boolean;
  backendPendingPermission: PendingPermission | null;
  /** Messages the server holds as pending for this session. */
  serverPendingCount: number;
}

/**
 * Why a sent message is not moving, when the answer is "it waits on an
 * approval" (2026-10-22: two messages sat 18 min behind a prompt with no
 * explanation). Null when nothing is queued or the session waits on nothing.
 */
export function queuedBehindApprovalText(input: QueuedBehindApprovalInput): string | null {
  const { backendWaitingInput, backendPendingPermission: perm, serverPendingCount } = input;
  if (!backendWaitingInput || !perm?.requestId) return null;
  const n = Math.max(serverPendingCount, 0);
  if (n === 0) return null;
  const cmd = typeof perm.toolInput?.command === 'string' ? perm.toolInput.command.trim() : '';
  const what = cmd ? `${perm.toolName}: ${cmd.length > 80 ? `${cmd.slice(0, 79)}…` : cmd}` : perm.toolName;
  const msgs = n === 1 ? 'Your message is' : `${n} messages are`;
  const after = n === 1 ? 'It sends' : 'They send';
  return `${msgs} waiting for your approval of ${what}. ${after} after you decide.`;
}

/**
 * True for an error whose message the backend kept as pending and will deliver
 * itself (SESSION_BUSY, QUEUE_TIMEOUT): the send must never be re-POSTed, or the
 * message is stored and answered twice.
 */
export function isServerQueuedErrorCode(code: string | undefined | null): boolean {
  return code === 'SESSION_BUSY' || code === 'QUEUE_TIMEOUT';
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
 * Mirror the server's drain progress: retire = the server drained a seq the tab
 * had not seen (currentDrainedSeqs has one priorDrainedSeqs lacks, or the list
 * reset to empty). The reconcile loop then pulls the drained turn from the DB
 * when the subscription did not render it. serverPendingCount is passed through.
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
  /** The getStreamingState() query itself failed/threw at expiry. Fail-safe:
   *  we cannot prove the backend is alive, so do NOT keep the spinner forever —
   *  fall through to the error path (never strand the user). */
  queryFailed: boolean;
}

/** Verdict for heal-grace expiry.
 *  - 'still-working': backend is provably alive (streaming/waiting) →
 *    keep the spinner, NO error toast; the 15s reconcile loop owns recovery.
 *  - 'show-error': backend is genuinely done/dead (or the liveness query failed)
 *    → run the existing error path (clear streaming + error toast). */
export type HealGraceVerdict = 'still-working' | 'show-error';

/**
 * Decide what heal-grace expiry should do, given the authoritative backend state.
 *
 * INVARIANT (the OT01 bug this exists to kill): when the backend turn is still
 * alive — streaming or waiting on input — expiry must NOT show a "Connection
 * lost" error and must NOT stop the spinner.
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
  const { backendIsStreaming, backendWaitingInput, queryFailed } = input;
  // Fail-safe: a failed liveness query means we cannot prove the backend is
  // alive → show the error (never keep a spinner up on an unprovable state).
  if (queryFailed) return 'show-error';
  if (backendIsStreaming || backendWaitingInput) {
    return 'still-working';
  }
  return 'show-error';
}

/**
 * The reconcile poll's next delay: 3s while any tab shows a turn in progress
 * (the poll is the fallback for a lost push of the session's activity), 15s
 * otherwise. Cadence only: the activity itself decides the bee
 * (docs/design/chat-turn-liveness.md S3).
 *
 * @param anyTabStreaming  true iff at least one tab is currently streaming
 * @returns next poll delay in ms (3s streaming, 15s idle)
 */
export const RECONCILE_FAST_MS = 3_000;
export const RECONCILE_IDLE_MS = 15_000;
export function nextReconcileDelay(anyTabStreaming: boolean): number {
  return anyTabStreaming ? RECONCILE_FAST_MS : RECONCILE_IDLE_MS;
}

/**
 * The bubble a CLI-initiated (``injected``) turn continues, or null for a new one.
 *
 * The reloaded history merges consecutive assistant rows into one message
 * (routers/chat.py _merge_consecutive_assistant_messages), so the live view does
 * the same: an injected turn right after an assistant message renders into it.
 * A replay of a turn already hosted returns its host again. After a user message
 * (or with nothing shown) the turn gets its own bubble.
 */
export function hostForInjectedTurn<M extends { id: string; role: string }>(
  messages: readonly M[],
  turnId: string | undefined,
  hosted: { turnId: string; hostId: string } | undefined,
): M | null {
  if (hosted && turnId && hosted.turnId === turnId) {
    const again = messages.find((m) => m.id === hosted.hostId);
    if (again) return again;
  }
  // A Waiting steer or a queued message at the bottom is not sent yet: the
  // turn renders above it, into the bubble before it (chat-steer frontend §8).
  let i = messages.length - 1;
  while (i >= 0 && isUnsentUser(messages[i])) i -= 1;
  const last = messages[i];
  return last && last.role === 'assistant' ? last : null;
}

/** The id of the first user bubble after a turn's own prompt that is a steer
 *  (read: ``metadata.steered``) or not sent yet (Waiting / queued): where a
 *  replay of that turn puts its first bubble (chat-steer frontend §8). The
 *  prompt is the user bubble ``turnClientId`` names (``{cid}-asst``), else the
 *  last user bubble that is neither. */
export function firstSteerOfTurn(
  messages: readonly { id: string; role: string }[], turnClientId: string | undefined,
): string | null {
  const isSteer = (m: { id: string; role: string }) => m.role === 'user'
    && (isUnsentUser(m) || !!(m as { metadata?: { steered?: unknown } | null }).metadata?.steered);
  const own = turnClientId?.endsWith('-asst') ? turnClientId.slice(0, -5) : undefined;
  let start = own ? messages.findIndex((m) => m.role === 'user'
    && (m.id === own || (m as { metadata?: { client_id?: unknown } | null }).metadata?.client_id === own)) : -1;
  if (start < 0) {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user' && !isSteer(messages[i])) { start = i; break; }
    }
  }
  for (let i = start + 1; i < messages.length; i += 1) if (isSteer(messages[i])) return messages[i].id;
  return null;
}

/** A user bubble the CLI has not read yet: a Waiting steer or a queued message
 *  (the same rule UserMessageView labels by). A read steer is a sent message. */
export function isUnsentUser(msg: { role: string }): boolean {
  const m = msg as { role: string; isQueued?: boolean; steerWaiting?: boolean;
    metadata?: { steered?: unknown } | null };
  return m.role === 'user' && !m.metadata?.steered && (!!m.isQueued || !!m.steerWaiting);
}
