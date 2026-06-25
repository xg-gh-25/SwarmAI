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
