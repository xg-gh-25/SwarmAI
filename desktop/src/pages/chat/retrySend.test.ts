/**
 * retrySend (docs/design/chat-turn-liveness.md S3): Retry of a "Not sent" message
 * sends the kept request again with the same client_id, moves the bubble to the
 * end with a fresh reply placeholder, and does nothing while the tab is busy or
 * when there is nothing to retry.
 */
import { describe, it, expect } from 'vitest';
import { retrySend, type RetrySendStore } from './retrySend';
import type { ChatRequest, Message } from '../../types';

const at = '2026-10-24T00:00:00.000Z';
function storeOf(msgs: Message[]): RetrySendStore & { messages: Message[] } {
  const st = {
    messages: msgs,
    remove(pred: (m: Message) => boolean) { st.messages = st.messages.filter((m) => !pred(m)); },
    appendMany(add: Message[]) { st.messages = [...st.messages, ...add]; },
  };
  return st;
}
const req: ChatRequest = { agentId: 'a', message: 'hi', clientId: 'local-x' };
const failed: Message = { id: 'local-x', role: 'user', content: [{ type: 'text', text: 'hi' }], timestamp: at, sendState: 'failed' };
const errBubble: Message = { id: 'local-x-asst', role: 'assistant', content: [{ type: 'text', text: 'unreachable' }], timestamp: at, isError: true };
const later: Message = { id: 'local-y', role: 'user', content: [{ type: 'text', text: 'later' }], timestamp: at };

describe('retrySend', () => {
  it('sends the kept request with the same client_id, the tab session, bubble moved to the end', () => {
    const store = storeOf([failed, errBubble, later]);
    const plan = retrySend({ isStreaming: false, sessionId: 'S1', _unsentRequests: new Map([['local-x', req]]) }, store, 'local-x');
    expect(plan).toEqual({ request: { ...req, sessionId: 'S1' }, assistantId: 'local-x-asst' });
    expect(store.messages.map((m) => m.id)).toEqual(['local-y', 'local-x', 'local-x-asst']);
    expect(store.messages[1].sendState).toBeUndefined();
    expect(store.messages[2].content).toEqual([]);
  });

  it('does nothing while a turn runs, a request of the tab is open, or a reconnect / auto-resend is pending', () => {
    const kept = new Map([['local-x', req]]);
    for (const tab of [{ isStreaming: true, _unsentRequests: kept }, { isStreaming: false, localSending: true, _unsentRequests: kept },
      { isStreaming: false, isReconnecting: true, _unsentRequests: kept },
      { isStreaming: false, _pendingResendOnRecovery: true, _unsentRequests: kept }]) {
      const store = storeOf([failed, errBubble]);
      expect(retrySend(tab, store, 'local-x')).toBeNull();
      expect(store.messages).toEqual([failed, errBubble]);
    }
  });

  it('does nothing without a kept request or for a message that is not marked', () => {
    expect(retrySend({ isStreaming: false }, storeOf([failed]), 'local-x')).toBeNull();
    expect(retrySend({ isStreaming: false, _unsentRequests: new Map([['local-y', req]]) },
      storeOf([later]), 'local-y')).toBeNull();
  });
});
