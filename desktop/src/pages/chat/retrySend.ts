/**
 * retrySend — re-send a message the backend never accepted (its bubble says
 * "Not sent", docs/design/chat-turn-liveness.md S3).
 *
 * The same request, the same client_id: the backend's send ledger keeps it one
 * message even if the first attempt did reach it. The bubble moves to the end
 * (it is being sent now) with a fresh reply placeholder. A tab whose turn is in
 * progress, or with a request of its own open, does nothing (the caller keeps the
 * Retry button).
 *
 * Key exports:
 * - ``retrySend`` — returns the request to POST and the reply placeholder id, or
 *   null when there is nothing to retry; it has already rearranged the store.
 */
import type { ChatRequest, Message } from '../../types';

export interface RetrySendTab {
  isStreaming: boolean;
  localSending?: boolean;
  /** a reconnect / auto-resend of the tab's send is pending: it, not Retry, goes */
  isReconnecting?: boolean;
  _pendingResendOnRecovery?: boolean;
  sessionId?: string;
  _unsentRequests?: Map<string, ChatRequest>;
}

export interface RetrySendStore {
  readonly messages: readonly Message[];
  remove(pred: (m: Message) => boolean): void;
  appendMany(msgs: Message[]): void;
}

export function retrySend(
  tab: RetrySendTab, store: RetrySendStore, messageId: string,
): { request: ChatRequest; assistantId: string } | null {
  if (tab.isStreaming || tab.localSending || tab.isReconnecting || tab._pendingResendOnRecovery) return null;
  const bubble = store.messages.find((m) => m.id === messageId);
  if (!bubble || bubble.sendState !== 'failed') return null;
  const cid = (bubble.metadata?.client_id as string | undefined) ?? messageId;
  const kept = tab._unsentRequests?.get(cid);
  if (!kept) return null;
  const assistantId = `${cid}-asst`;
  store.remove((m) => m.id === messageId || m.id === assistantId);
  const now = new Date().toISOString();
  store.appendMany([
    { ...bubble, sendState: undefined, timestamp: now },
    { id: assistantId, role: 'assistant', content: [], timestamp: now },
  ]);
  return { request: { ...kept, sessionId: tab.sessionId ?? kept.sessionId }, assistantId };
}
