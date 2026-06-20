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

/**
 * Build a queuedMessage from a SESSION_BUSY retryPayload so a busy-session send
 * is never lost. Returns null when there is nothing recoverable (payload absent
 * — e.g. the chat.py "Cannot send() in state" SESSION_BUSY carries none — or no
 * non-empty text). Callers MUST null-guard.
 *
 * Note: attachments are not recoverable from the payload (the backend doesn't
 * round-trip binary blobs), so re-queue preserves text only. Text is the part
 * that was silently destroyed before; attachments remain in the composer.
 */
export function queuedMessageFromRetryPayload(
  payload: SessionBusyRetryPayload | null | undefined,
  messageId: string,
): QueuedMessage | null {
  if (!payload) return null;
  const text = (payload.userMessage ?? '').trim();
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
