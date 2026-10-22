/**
 * The client_id of a message the user sends — minted once, here, when the message
 * is created (typed and sent, or queued), and carried with it: every send of that
 * message (a reconnect resend, a queued drain, a drain restored after it failed,
 * a re-send of a message the server handed back) uses the same id, so the
 * backend's send ledger (backend/core/send_requests.py) runs it once.
 *
 * Exports: newClientId.
 */

/** A new message's client_id (also the id of its optimistic user bubble). */
export function newClientId(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
