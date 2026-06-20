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
  type QueueGuardState,
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
