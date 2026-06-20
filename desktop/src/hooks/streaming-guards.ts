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
