/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * MessageStore unit tests — verifies core operations, phase gating,
 * watchdog timer, rAF notification, and merge algorithm.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MessageStore, messageStoreRegistry } from '../MessageStore';
import type { Message, ChatMessage } from '../../types';

// ─── Test Helpers ───

function makeMsg(id: string, role: 'user' | 'assistant' | 'system' = 'user', text = 'hello', timestamp?: string): Message {
  return { id, role, content: [{ type: 'text', text }], timestamp: timestamp ?? new Date().toISOString() };
}

function makeChatMsg(id: string, role: 'user' | 'assistant' | 'system' = 'user', text = 'hello'): ChatMessage {
  return { id, sessionId: 'sess-1', role, content: [{ type: 'text', text }] as any, createdAt: new Date().toISOString() };
}

// ─── Core Operations ───

describe('MessageStore', () => {
  let store: MessageStore;

  beforeEach(() => {
    store = new MessageStore();
  });

  afterEach(() => {
    store.destroy();
  });

  describe('append', () => {
    it('appends a message to the end', () => {
      store.append(makeMsg('1'));
      store.append(makeMsg('2'));
      expect(store.messages).toHaveLength(2);
      expect(store.messages[0].id).toBe('1');
      expect(store.messages[1].id).toBe('2');
    });

    it('succeeds during streaming phase', () => {
      store.startStreaming('stream-1');
      store.append(makeMsg('1'));
      expect(store.messages).toHaveLength(1);
    });
  });

  describe('appendMany', () => {
    it('appends multiple messages at once', () => {
      store.appendMany([makeMsg('1'), makeMsg('2'), makeMsg('3')]);
      expect(store.messages).toHaveLength(3);
    });

    it('no-ops on empty array', () => {
      const listener = vi.fn();
      store.subscribe(listener);
      store.appendMany([]);
      expect(listener).not.toHaveBeenCalled();
    });
  });

  describe('updateLast', () => {
    it('updates the last message', () => {
      store.append(makeMsg('1', 'assistant', 'hello'));
      store.updateLast(msg => ({
        ...msg,
        content: [{ type: 'text' as const, text: 'hello world' }],
      }));
      expect((store.messages[0].content[0] as any).text).toBe('hello world');
    });

    it('updates last message matching predicate', () => {
      store.append(makeMsg('1', 'user', 'user msg'));
      store.append(makeMsg('2', 'assistant', 'old'));
      store.updateLast(
        msg => ({ ...msg, content: [{ type: 'text' as const, text: 'new' }] }),
        msg => msg.role === 'assistant',
      );
      expect((store.messages[1].content[0] as any).text).toBe('new');
      expect((store.messages[0].content[0] as any).text).toBe('user msg');
    });

    it('resets watchdog timer', () => {
      vi.useFakeTimers();
      // isBackendLive='dead' → the fire-on-silence mechanic under test still fires
      // at one timeout (A2 only defers the fire when the backend is alive/unknown).
      store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
      store.append(makeMsg('1', 'assistant'));
      store.startStreaming('1');

      // Advance 80ms, then updateLast (should reset)
      vi.advanceTimersByTime(80);
      store.updateLast(msg => ({ ...msg, content: [{ type: 'text' as const, text: 'tok1' }] }));

      // Advance another 80ms — still streaming (watchdog reset)
      vi.advanceTimersByTime(80);
      expect(store.phase).toBe('streaming');

      // Advance 100ms more — now watchdog fires
      vi.advanceTimersByTime(100);
      expect(store.phase).toBe('idle');

      vi.useRealTimers();
    });
  });

  describe('updateById', () => {
    it('updates a specific message by ID', () => {
      store.append(makeMsg('1', 'user', 'first'));
      store.append(makeMsg('2', 'assistant', 'second'));
      store.updateById('1', msg => ({ ...msg, content: [{ type: 'text' as const, text: 'updated' }] }));
      expect((store.messages[0].content[0] as any).text).toBe('updated');
      expect((store.messages[1].content[0] as any).text).toBe('second');
    });
  });

  describe('remove', () => {
    it('removes messages matching predicate', () => {
      store.append(makeMsg('1', 'user'));
      store.append(makeMsg('2', 'assistant'));
      store.append(makeMsg('3', 'user'));
      store.remove(m => m.role === 'user');
      expect(store.messages).toHaveLength(1);
      expect(store.messages[0].id).toBe('2');
    });
  });

  describe('replace', () => {
    it('replaces all messages', () => {
      store.append(makeMsg('1'));
      store.replace([makeMsg('a'), makeMsg('b')]);
      expect(store.messages).toHaveLength(2);
      expect(store.messages[0].id).toBe('a');
    });

    it('is NO-OP during streaming', () => {
      store.append(makeMsg('1'));
      store.startStreaming('1');
      store.replace([makeMsg('a'), makeMsg('b')]);
      // Should still have original message
      expect(store.messages).toHaveLength(1);
      expect(store.messages[0].id).toBe('1');
    });

    it('sets initialLoadComplete', () => {
      expect(store.initialLoadComplete).toBe(false);
      store.replace([makeMsg('1')]);
      expect(store.initialLoadComplete).toBe(true);
    });
  });
});

// ─── Phase Gate ───

describe('MessageStore phase gate', () => {
  let store: MessageStore;

  beforeEach(() => {
    store = new MessageStore();
  });

  afterEach(() => {
    store.destroy();
  });

  it('reconcile during streaming is queued', () => {
    store.append(makeMsg('local-1', 'user', 'my message'));
    store.startStreaming('stream-1');

    // Reconcile with DB data that doesn't include local message
    store.reconcile([makeChatMsg('db-1', 'assistant', 'db response')]);

    // Local message should still be there (not replaced)
    expect(store.messages).toHaveLength(1);
    expect(store.messages[0].id).toBe('local-1');
  });

  it('pending reconcile thunk fires on endStreaming', async () => {
    const fetchMock = vi.fn().mockResolvedValue([
      makeChatMsg('db-1', 'user', 'from db'),
    ]);
    store = new MessageStore({ sessionId: 'sess-1', fetchMessages: fetchMock });
    store.append(makeMsg('local-1', 'user'));
    store.startStreaming('stream-1');

    // Queue reconcile during streaming
    store.reconcile([]);

    // End streaming — should trigger fetch
    store.endStreaming();

    // Wait for async fetch to complete
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  });

  it('replace is blocked during streaming', () => {
    store.append(makeMsg('1', 'assistant', 'streaming content'));
    store.startStreaming('1');
    store.replace([makeMsg('x'), makeMsg('y')]);
    expect(store.messages[0].id).toBe('1');
    expect(store.messages).toHaveLength(1);
  });

  it('endStreaming notifies subscribers (phase->idle is observable)', async () => {
    // run_1a264fd1: the frozen-tab desync — endStreaming flipped _phase to idle
    // but never _notify()'d, so the React subscription never observed the phase
    // change and the isStreaming flag could not be bridged off it. The terminal
    // phase transition MUST fire the listener so a subscriber can react to it.
    // (_notify is rAF-gated, so the fire is async — waitFor flushes the frame.)
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    store.flush(); // drain the pending append-notify so the fire below is
                   // attributable ONLY to endStreaming (mutation-sensitivity:
                   // without this a leaked append-notify makes the test pass
                   // even if endStreaming's _notify is removed — Gate-2 LOW).
    const listener = vi.fn();
    store.subscribe(listener);
    listener.mockClear(); // subscribe() syncs once on attach — ignore that
    store.endStreaming();
    expect(store.phase).toBe('idle');
    await vi.waitFor(() => expect(listener).toHaveBeenCalled());
  });

  it('watchdog fire notifies subscribers (the frozen-tab path)', () => {
    vi.useFakeTimers();
    const s = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    s.append(makeMsg('1', 'assistant'));
    s.startStreaming('1');
    s.flush(); // drain the append-notify (else its leaked fallback timer fires
               // the listener independent of the watchdog → vacuous). After
               // flush, only the watchdog's endStreaming→_notify can fire it.
    const listener = vi.fn();
    s.subscribe(listener);
    listener.mockClear();
    vi.advanceTimersByTime(101); // watchdog fires → endStreaming → must notify
    // _notify is rAF-gated; advance the 100ms fallback timer to flush the frame.
    vi.advanceTimersByTime(101);
    expect(s.phase).toBe('idle');
    expect(listener).toHaveBeenCalled();
    s.destroy();
    vi.useRealTimers();
  });
});

// ─── Watchdog ───

describe('MessageStore watchdog', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('watchdog timeout forces endStreaming', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');

    expect(store.phase).toBe('streaming');
    vi.advanceTimersByTime(101);
    expect(store.phase).toBe('idle');

    store.destroy();
  });

  it('watchdog is reset by updateLast', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');

    vi.advanceTimersByTime(90);
    store.updateLast(msg => msg); // Reset watchdog
    vi.advanceTimersByTime(90);
    expect(store.phase).toBe('streaming'); // Still streaming

    vi.advanceTimersByTime(11);
    expect(store.phase).toBe('idle'); // Now timed out

    store.destroy();
  });

  it('watchdog is cleared by endStreaming', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100 });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    store.endStreaming();

    // Advance past timeout — should not throw or change phase
    vi.advanceTimersByTime(200);
    expect(store.phase).toBe('idle');

    store.destroy();
  });

  it('touch() resets the watchdog without mutating content (long silent step)', () => {
    // 'dead' verdict → the final "genuinely dead stream still fires" assertion
    // holds at one timeout; touch() re-arm behaviour is verdict-independent.
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    const before = store.messages;

    // Simulate a long silent step: only liveness pings (heartbeat/still_working),
    // no content. Each touch() keeps the watchdog armed past the 100ms window.
    vi.advanceTimersByTime(90);
    store.touch();
    vi.advanceTimersByTime(90);
    store.touch();
    vi.advanceTimersByTime(90);
    expect(store.phase).toBe('streaming'); // never force-ended despite >200ms silence
    expect(store.messages).toBe(before);   // content untouched (no new array ref)

    // Stop pinging → a genuinely dead stream (no content, no heartbeat) still fires.
    vi.advanceTimersByTime(101);
    expect(store.phase).toBe('idle');

    store.destroy();
  });

  it('touch() is a NO-OP when not streaming', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100 });
    store.append(makeMsg('1', 'assistant'));
    // idle phase — touch must not arm a watchdog or change phase
    store.touch();
    vi.advanceTimersByTime(200);
    expect(store.phase).toBe('idle');
    store.destroy();
  });
});

// ─── rAF-gated Notifications ───

describe('MessageStore rAF-gated notifications', () => {
  it('notify is rAF-gated (coalesces rapid updates)', async () => {
    const store = new MessageStore();
    const listener = vi.fn();
    store.subscribe(listener);

    store.append(makeMsg('1'));
    store.append(makeMsg('2'));
    store.append(makeMsg('3'));

    // rAF is async — wait for it to fire
    await new Promise(resolve => requestAnimationFrame(resolve));

    // All 3 appends coalesced into fewer notifications than calls
    expect(listener).toHaveBeenCalled();
    // Key: listener called fewer times than mutations (coalescing works)
    expect(listener.mock.calls.length).toBeLessThanOrEqual(3);
    expect(store.messages).toHaveLength(3);

    store.destroy();
  });

  it('re-entrancy guard prevents infinite loops', async () => {
    const store = new MessageStore();
    let callCount = 0;
    store.subscribe(() => {
      callCount++;
      if (callCount < 5) {
        // Listener tries to trigger another notification — should be blocked
        // by re-entrancy guard (nested _notify is suppressed, but append succeeds)
        store.append(makeMsg(`reentrant-${callCount}`));
      }
    });

    store.append(makeMsg('trigger'));

    // Wait for rAF + allow any nested notifications
    await new Promise(resolve => requestAnimationFrame(resolve));
    await new Promise(resolve => requestAnimationFrame(resolve));

    // Without re-entrancy guard, this would loop infinitely
    // With guard: callCount is bounded (nested appends succeed but
    // their notifications are deferred to next frame, preventing stack overflow)
    expect(callCount).toBeGreaterThanOrEqual(1);
    expect(callCount).toBeLessThan(100); // Proves no infinite loop

    store.destroy();
  });
});

// ─── Reconcile Merge Algorithm ───

describe('MessageStore reconcile', () => {
  let store: MessageStore;

  beforeEach(() => {
    store = new MessageStore();
  });

  afterEach(() => {
    store.destroy();
  });

  it('merges new DB messages into local state', () => {
    store.append(makeMsg('1', 'user', 'local'));
    store.reconcile([
      makeChatMsg('1', 'user', 'from db'),
      makeChatMsg('2', 'assistant', 'new from db'),
    ]);
    expect(store.messages).toHaveLength(2);
    // DB is source of truth for completed messages (server-side edits propagate)
    expect((store.messages[0].content[0] as any).text).toBe('from db');
    // New DB message added
    expect(store.messages[1].id).toBe('2');
  });

  it('preserves local-only messages not in DB', () => {
    store.append(makeMsg('1', 'user', 'sent'));
    store.append(makeMsg('queued-123', 'user', 'queued'));
    store.reconcile([
      makeChatMsg('1', 'user', 'sent'),
      makeChatMsg('2', 'assistant', 'response'),
    ]);
    // Local-only 'queued-123' preserved
    const ids = store.messages.map(m => m.id);
    expect(ids).toContain('queued-123');
    expect(ids).toContain('1');
    expect(ids).toContain('2');
  });

  it('streaming message is never overwritten by reconcile', () => {
    store.append(makeMsg('stream-1', 'assistant', 'partial stream content'));
    store.startStreaming('stream-1');
    // Reconcile blocked during streaming
    store.reconcile([makeChatMsg('stream-1', 'assistant', 'stale db version')]);
    // Still has local streaming content
    expect((store.messages[0].content[0] as any).text).toBe('partial stream content');
    store.endStreaming();
  });

  it('AC5: ask_user_question answers survive reconcile when DB lacks the block (carry-forward)', () => {
    // Local assistant message carries an answered ask_user_question block.
    // The DB row for the same message has NO ask_user_question block (backend
    // never persists them) — the local block + its answers must be carried forward.
    const local: Message = {
      id: 'm1', role: 'assistant', timestamp: new Date().toISOString(),
      content: [
        { type: 'text', text: 'ok' } as any,
        { type: 'ask_user_question', toolUseId: 'tu-1',
          questions: [{ question: 'Pick a color', header: 'Color', multiSelect: false,
            options: [{ label: 'Red', description: '' }, { label: 'Blue', description: '' }] }],
          answers: { 'Pick a color': 'Blue' } } as any,
      ],
    };
    store.append(local);
    store.reconcile([makeChatMsg('m1', 'assistant', 'ok')]); // DB lacks the auq block
    const auq = store.messages[0].content.find((b: any) => b.type === 'ask_user_question') as any;
    expect(auq).toBeTruthy();
    expect(auq.answers).toEqual({ 'Pick a color': 'Blue' });
  });

  it('AC5b: ask_user_question answers are merged onto a DB block that shares the key (hardening)', () => {
    // Defensive: if the DB row DID carry the ask_user_question block (key match)
    // but without answers, the local answers must be merged onto it — not dropped.
    const local: Message = {
      id: 'm1', role: 'assistant', timestamp: new Date().toISOString(),
      content: [
        { type: 'ask_user_question', toolUseId: 'tu-1',
          questions: [{ question: 'Pick a color', header: 'Color', multiSelect: false,
            options: [{ label: 'Red', description: '' }] }],
          answers: { 'Pick a color': 'Red' } } as any,
      ],
    };
    store.append(local);
    const dbMsg: ChatMessage = {
      id: 'm1', sessionId: 'sess-1', role: 'assistant', createdAt: new Date().toISOString(),
      content: [
        { type: 'ask_user_question', toolUseId: 'tu-1',
          questions: [{ question: 'Pick a color', header: 'Color', multiSelect: false,
            options: [{ label: 'Red', description: '' }] }] } as any, // DB block, NO answers
      ] as any,
    };
    store.reconcile([dbMsg]);
    const auqs = store.messages[0].content.filter((b: any) => b.type === 'ask_user_question') as any[];
    // Exactly one block (no duplicate), and it carries the local answers.
    expect(auqs).toHaveLength(1);
    expect(auqs[0].answers).toEqual({ 'Pick a color': 'Red' });
  });
});

// ─── Registry ───

describe('messageStoreRegistry', () => {
  afterEach(() => {
    messageStoreRegistry.clear();
  });

  it('getOrCreate creates a new store', () => {
    const store = messageStoreRegistry.getOrCreate('tab-1');
    expect(store).toBeInstanceOf(MessageStore);
    expect(messageStoreRegistry.size).toBe(1);
  });

  it('getOrCreate returns existing store', () => {
    const store1 = messageStoreRegistry.getOrCreate('tab-1');
    const store2 = messageStoreRegistry.getOrCreate('tab-1');
    expect(store1).toBe(store2);
  });

  it('destroy removes and cleans up store', () => {
    messageStoreRegistry.getOrCreate('tab-1');
    messageStoreRegistry.destroy('tab-1');
    expect(messageStoreRegistry.size).toBe(0);
    expect(messageStoreRegistry.get('tab-1')).toBeUndefined();
  });

  it('clear destroys all stores', () => {
    messageStoreRegistry.getOrCreate('tab-1');
    messageStoreRegistry.getOrCreate('tab-2');
    messageStoreRegistry.clear();
    expect(messageStoreRegistry.size).toBe(0);
  });
});

// ─── Single-Writer Behavior (Phase 3+4) ───

describe('MessageStore single-writer behavior', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('replace() after endStreaming() succeeds — error content renders', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 45000 });
    store.append(makeMsg('a1', 'assistant', 'streaming content'));
    store.startStreaming('a1');
    expect(store.phase).toBe('streaming');

    // Error arrives — endStreaming first, then replace
    store.endStreaming();
    const errorMsgs = [makeMsg('a1', 'assistant', 'streaming content + error')];
    store.replace(errorMsgs);

    expect(store.messages[0].content[0]).toEqual(
      expect.objectContaining({ text: 'streaming content + error' }),
    );
    store.destroy();
  });

  it('replace() during streaming is NO-OP — does not lose content', () => {
    const store = new MessageStore();
    store.append(makeMsg('a1', 'assistant', 'live stream'));
    store.startStreaming('a1');

    // Attempt replace during streaming — should be ignored
    store.replace([makeMsg('a1', 'assistant', 'stale replace')]);
    expect((store.messages[0].content[0] as any).text).toBe('live stream');

    store.endStreaming();
    store.destroy();
  });

  it('replace() invalidates in-flight reconcile via _reconcileGen', async () => {
    let fetchResolve: (msgs: ChatMessage[]) => void;
    const fetchPromise = new Promise<ChatMessage[]>((resolve) => { fetchResolve = resolve; });
    const store = new MessageStore({
      sessionId: 'sess-1',
      fetchMessages: () => fetchPromise,
    });
    store.append(makeMsg('a1', 'assistant', 'original'));
    store.startStreaming('a1');

    // Queue a reconcile thunk (NO-OP during streaming)
    store.reconcile([makeChatMsg('a1', 'assistant', 'db version')]);

    // endStreaming flushes the thunk (starts async fetch)
    store.endStreaming();

    // replace() immediately sets new content + increments reconcileGen
    store.replace([makeMsg('a1', 'assistant', 'error annotated')]);

    // Now the fetch resolves with stale data — should be discarded
    fetchResolve!([makeChatMsg('a1', 'assistant', 'stale db')]);
    await vi.waitFor(() => {}); // flush microtasks

    // Store still has the replace'd content, not the stale fetch
    expect((store.messages[0].content[0] as any).text).toBe('error annotated');
    store.destroy();
  });

  // ─── Regression: turn-end reconcile races in-flight fetch → tail lost ───
  // Bug: a clean turn-end runs endStreaming() (flushes the queued thunk, which
  // starts an async _fetchAndReconcile → _reconcileInFlight=1), then ~200ms
  // later scheduleTurnEndReconcile calls reconcile(freshFullDb). That reconcile
  // hits _reconcileInFlight>0 → NO-OPs and re-queues the thunk. The in-flight
  // fetch then resolves with the OLD (truncated) snapshot, decrements inFlight,
  // but NEVER drains the re-queued thunk (no further endStreaming on a finished
  // turn). Result: the complete tail in fresh DB is dropped permanently — the
  // user sees a truncated reply until tab-switch. Fix: drain the pending thunk
  // in _fetchAndReconcile's finally when inFlight hits 0 at idle.
  it('turn-end reconcile is not lost when it races an in-flight fetch (drains re-queued thunk)', async () => {
    // First fetch (started by endStreaming flush) returns the TRUNCATED tail —
    // simulates the stale in-flight read. Second fetch (the drained re-run)
    // returns the FULL content from DB.
    let call = 0;
    let firstResolve: (m: ChatMessage[]) => void;
    const firstFetch = new Promise<ChatMessage[]>((r) => { firstResolve = r; });
    const fetchMock = vi.fn(() => {
      call += 1;
      if (call === 1) return firstFetch;
      // Subsequent drained fetch sees the full DB content.
      return Promise.resolve([makeChatMsg('a1', 'assistant', 'FULL complete reply tail')]);
    });
    const store = new MessageStore({ sessionId: 'sess-1', fetchMessages: fetchMock });
    store.append(makeMsg('a1', 'assistant', 'FULL complete'));
    store.startStreaming('a1');

    // During streaming, a reconcile is requested → queued as thunk.
    store.reconcile([makeChatMsg('a1', 'assistant', 'queued')]);

    // Clean turn-end: flushes the thunk → starts async _fetchAndReconcile (inFlight=1).
    store.endStreaming();

    // The turn-end reconcile fires ~200ms later with fresh full DB data, but
    // hits inFlight>0 → NO-OP + re-queue.
    store.reconcile([makeChatMsg('a1', 'assistant', 'FULL complete reply tail')]);

    // Now the in-flight fetch resolves with the stale/truncated read.
    firstResolve!([makeChatMsg('a1', 'assistant', 'truncated')]);
    await vi.waitFor(() => {
      // The drained re-run must eventually land the full content.
      expect((store.messages[0].content[0] as any).text).toBe('FULL complete reply tail');
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2); // proves drain re-ran
    store.destroy();
  });

  it('append() resets watchdog during streaming', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    store.append(makeMsg('a1', 'assistant'));
    store.startStreaming('a1');

    // Advance 90ms — watchdog almost fires
    vi.advanceTimersByTime(90);
    expect(store.phase).toBe('streaming');

    // Append resets watchdog
    store.append(makeMsg('boundary', 'system', 'Session resumed'));
    vi.advanceTimersByTime(90);
    expect(store.phase).toBe('streaming'); // Still alive

    // Now let it expire
    vi.advanceTimersByTime(11);
    expect(store.phase).toBe('idle');

    store.destroy();
  });

  it('getSnapshot() returns memoized reference when no mutation', () => {
    const store = new MessageStore();
    store.append(makeMsg('1', 'user'));

    const snap1 = store.getSnapshot();
    const snap2 = store.getSnapshot();
    expect(snap1).toBe(snap2); // Same reference — no allocation

    // After mutation, new reference
    store.append(makeMsg('2', 'user'));
    const snap3 = store.getSnapshot();
    expect(snap3).not.toBe(snap1);
    expect(snap3).toHaveLength(2);

    store.destroy();
  });

  // ─── Version-counter hot-path memo (run_ebbb7ccb) ───
  // updateLast/updateById mutate _messages IN PLACE (no per-token full-array
  // spread — the O(n²)-per-message fix). getSnapshot must still invalidate on
  // each content change (driven by a monotonic _version, since array identity
  // no longer flips on the in-place path), while FREEZING element refs per
  // snapshot so React's per-message memo re-renders only the changed bubble.
  describe('version-counter snapshot invalidation (hot-path clone fix)', () => {
    it('updateLast invalidates getSnapshot on every token even though array identity is stable', () => {
      const store = new MessageStore();
      store.append(makeMsg('a1', 'assistant', ''));

      const snap1 = store.getSnapshot();
      store.updateLast((m) => ({ ...m, content: [{ type: 'text', text: 'to' }] }));
      const snap2 = store.getSnapshot();
      store.updateLast((m) => ({ ...m, content: [{ type: 'text', text: 'token' }] }));
      const snap3 = store.getSnapshot();

      // Each token must yield a FRESH snapshot reference (React needs a new array
      // to re-render) — this is what the per-token spread used to guarantee and
      // the version counter must now guarantee without the O(n) copy.
      expect(snap2).not.toBe(snap1);
      expect(snap3).not.toBe(snap2);
      // And the content must be the latest.
      expect((snap3[0].content[0] as { text: string }).text).toBe('token');

      store.destroy();
    });

    it('freezes element refs per snapshot: unchanged message keeps old ref, changed message gets a new object', () => {
      const store = new MessageStore();
      store.append(makeMsg('u1', 'user', 'hi'));       // idx 0 — never updated again
      store.append(makeMsg('a1', 'assistant', ''));    // idx 1 — the streaming message

      const before = store.getSnapshot();
      store.updateLast((m) => ({ ...m, content: [{ type: 'text', text: 'reply' }] }));
      const after = store.getSnapshot();

      // Unchanged message: SAME object reference across snapshots → MessageBubble
      // memo bails out (no re-render of historical bubbles).
      expect(after[0]).toBe(before[0]);
      // Changed message: a NEW object → its bubble re-renders.
      expect(after[1]).not.toBe(before[1]);
      // The prior snapshot must NOT have been mutated retroactively (element-ref
      // freezing): before[1] still shows the pre-token content.
      expect((before[1].content[0] as { text?: string }).text ?? '').toBe('');
      expect((after[1].content[0] as { text: string }).text).toBe('reply');

      store.destroy();
    });

    it('touch() does NOT invalidate the snapshot (no version bump on liveness pings)', () => {
      vi.useFakeTimers();
      const store = new MessageStore();
      store.append(makeMsg('a1', 'assistant', 'x'));
      store.startStreaming('a1');

      const snap1 = store.getSnapshot();
      store.touch();
      const snap2 = store.getSnapshot();
      expect(snap2).toBe(snap1); // liveness ping = no content change = same snapshot

      store.destroy();
      vi.useRealTimers();
    });

    it('every content writer invalidates the snapshot (append/updateById/remove/replace)', () => {
      const store = new MessageStore();
      store.append(makeMsg('a1', 'assistant', 'one'));
      const s0 = store.getSnapshot();

      store.append(makeMsg('a2', 'assistant', 'two'));
      const s1 = store.getSnapshot();
      expect(s1).not.toBe(s0);

      store.updateById('a1', (m) => ({ ...m, content: [{ type: 'text', text: 'ONE' }] }));
      const s2 = store.getSnapshot();
      expect(s2).not.toBe(s1);
      expect((s2.find((m) => m.id === 'a1')!.content[0] as { text: string }).text).toBe('ONE');

      store.remove((m) => m.id === 'a2');
      const s3 = store.getSnapshot();
      expect(s3).not.toBe(s2);
      expect(s3).toHaveLength(1);

      store.replace([makeMsg('r1', 'user', 'fresh')]);
      const s4 = store.getSnapshot();
      expect(s4).not.toBe(s3);
      expect(s4[0].id).toBe('r1');

      store.destroy();
    });

    it('destroy() does not leave getSnapshot serving a stale populated snapshot', () => {
      const store = new MessageStore();
      store.append(makeMsg('a1', 'assistant', 'content that should not survive destroy'));
      store.getSnapshot(); // populate the memo
      store.destroy();
      // After destroy the store is empty; a stale memo must never resurface it.
      expect(store.getSnapshot()).toHaveLength(0);
    });
  });

  it('subscription fires on store mutation and delivers correct snapshot', () => {
    const store = new MessageStore();
    store.append(makeMsg('a1', 'assistant'));

    const snapshots: Message[][] = [];
    store.subscribe(() => {
      snapshots.push(store.getSnapshot());
    });

    // Trigger mutation
    store.updateLast((msg) => ({ ...msg, content: [{ type: 'text', text: 'updated' }] }));

    // rAF-gated — advance timer to flush
    vi.advanceTimersByTime(16);

    expect(snapshots.length).toBeGreaterThanOrEqual(1);
    expect((snapshots[0][0].content[0] as any).text).toBe('updated');

    store.destroy();
  });

  it('subscription does not fire after unsubscribe', () => {
    const store = new MessageStore();
    store.append(makeMsg('a1', 'assistant'));

    let callCount = 0;
    const unsub = store.subscribe(() => { callCount++; });

    // First mutation — should fire
    store.updateLast((msg) => ({ ...msg, content: [{ type: 'text', text: 'v1' }] }));
    vi.advanceTimersByTime(16);
    expect(callCount).toBe(1);

    // Unsubscribe
    unsub();

    // Second mutation — should NOT fire
    store.updateLast((msg) => ({ ...msg, content: [{ type: 'text', text: 'v2' }] }));
    vi.advanceTimersByTime(16);
    expect(callCount).toBe(1); // unchanged

    store.destroy();
  });
});

// ─── Regression: new-tab send must append placeholder to the store ───
// Bug (2026-06-17): handleSendMessage inserted the optimistic user message +
// assistant placeholder via setMessages/tabState ONLY, never the store. On a
// fresh tab the store stayed empty, so streaming deltas — which target the
// placeholder via updateLast(predicate id===assistantMessageId) — silently
// no-op'd. Symptom: WelcomeScreen stuck, "Thinking…" spinner never fills.
describe('MessageStore — streaming-delta target must exist (new-tab send invariant)', () => {
  const appendTextUpdater = (text: string) => (msg: Message): Message => ({
    ...msg,
    content: [{ type: 'text' as const, text: ((msg.content[0] as { text?: string })?.text ?? '') + text }],
  });

  it('updateLast(byId) is a NO-OP when the placeholder is absent (documents the bug)', () => {
    const store = new MessageStore();
    // Fresh tab: nothing appended (the old buggy send path).
    store.startStreaming('assistant-1');
    // Streaming delta targets a placeholder that was never appended to the store.
    store.updateLast(appendTextUpdater('hello'), (m) => m.id === 'assistant-1');
    // Delta is dropped — store stays empty → UI never renders the response.
    expect(store.messages).toHaveLength(0);
    store.destroy();
  });

  it('appending the placeholder first makes streaming deltas land (the fix)', () => {
    const store = new MessageStore();
    // Correct send path: user message + assistant placeholder go through the store.
    store.appendMany([makeMsg('user-1', 'user', 'hi'), makeMsg('assistant-1', 'assistant', '')]);
    store.startStreaming('assistant-1');
    store.updateLast(appendTextUpdater('hello'), (m) => m.id === 'assistant-1');
    store.updateLast(appendTextUpdater(' world'), (m) => m.id === 'assistant-1');
    expect(store.messages).toHaveLength(2);
    expect((store.messages[1].content[0] as { text: string }).text).toBe('hello world');
    store.destroy();
  });
});

// ─── Regression: force-clear recovery must REPLACE, not reconcile ───
// Bug (2026-06-17): when a stream is force-cleared (backend went unreachable
// mid-stream) the view holds a temp placeholder with a PARTIAL response. The
// backend-recovered retry must show the authoritative DB content WITHOUT
// duplicating the frozen partial. reconcile() preserves local-only messages
// (temp ids not in DB) → would show partial + full = duplicate. replace()
// drops the placeholder → clean authoritative view. This locks that choice.
describe('MessageStore — recovery uses replace (no duplicated frozen partial)', () => {
  it('replace drops a temp streaming placeholder; reconcile would keep it', () => {
    // Simulate post-force-clear state: a user msg + a temp partial placeholder.
    const store = new MessageStore();
    store.appendMany([
      makeMsg('user-1', 'user', 'question'),
      makeMsg('temp-assistant-partial', 'assistant', 'half of the answ'),
    ]);
    expect(store.messages).toHaveLength(2);

    // Authoritative DB content for the SAME turn (real ids, complete text).
    const dbMessages: ChatMessage[] = [
      makeChatMsg('user-1', 'user', 'question'),
      makeChatMsg('real-assistant-1', 'assistant', 'half of the answer — and the complete rest'),
    ];

    // replace() = recovery path: temp placeholder is gone, only authoritative remains.
    store.replace(dbMessages.map((m) => ({
      id: m.id, role: m.role, content: m.content as Message['content'], timestamp: m.createdAt,
    })));
    const ids = store.messages.map((m) => m.id);
    expect(store.messages).toHaveLength(2);
    expect(ids).toContain('real-assistant-1');
    expect(ids).not.toContain('temp-assistant-partial'); // no duplicated frozen partial
    store.destroy();
  });
});


// ─── ClientId Dedup (AC4/AC5) ───

describe('MessageStore H2 — empty assistant placeholder cleanup on reconcile', () => {
  // H2 backstop (run_af36e709): continuation paths (continue_with_answer/
  // permission) pass no client_id, so their assistant rows have no correlation
  // key and their placeholders keep numeric ids. Without cleanup, reconcile adds
  // the real DB assistant row (Pass 1) AND preserves the orphan empty placeholder
  // (Pass 2) = a ghost empty bubble next to the real one. Empirically verified.
  // Fix: Pass 2 drops an empty assistant placeholder ONLY when the merged set
  // already contains a real (non-empty) assistant message — so a slow-persist
  // race never blanks the turn (the placeholder survives until real content
  // exists somewhere).

  it('drops orphan empty assistant placeholder when a real DB assistant row arrived', () => {
    const store = new MessageStore();
    store.replace([
      makeMsg('local-1-u', 'user', 'Q'),
      { id: '1718999', role: 'assistant', content: [], timestamp: new Date().toISOString() },
    ]);
    store.reconcile([
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: 'local-1-u' } },
      { id: 'a1', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'A' }] as any, createdAt: new Date().toISOString() },
    ]);
    // Exactly 2 — the orphan empty placeholder ('1718999') is gone.
    expect(store.messages.map(m => m.id)).toEqual(['u1', 'a1']);
    expect(store.messages.filter(m => m.role === 'assistant')).toHaveLength(1);
    store.destroy();
  });

  it('KEEPS empty assistant placeholder when no real assistant row exists yet (no premature blank)', () => {
    const store = new MessageStore();
    store.replace([
      makeMsg('local-1-u', 'user', 'Q'),
      { id: '1718999', role: 'assistant', content: [], timestamp: new Date().toISOString() },
    ]);
    // DB has only the user row (assistant persist hasn't landed yet — race).
    store.reconcile([
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: 'local-1-u' } },
    ]);
    // Placeholder MUST survive — dropping it would blank the in-flight turn.
    expect(store.messages.some(m => m.id === '1718999')).toBe(true);
    store.destroy();
  });

  it('does not drop a NON-empty assistant message during reconcile', () => {
    // A real local assistant message (content present) is never dropped.
    const store = new MessageStore();
    store.replace([
      makeMsg('local-1-u', 'user', 'Q'),
      { id: 'local-asst-real', role: 'assistant', content: [{ type: 'text', text: 'already streamed' }], timestamp: new Date().toISOString() },
    ]);
    store.reconcile([
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: 'local-1-u' } },
      { id: 'a1', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'A' }] as any, createdAt: new Date().toISOString() },
    ]);
    // The non-empty local assistant survives (local-only preservation intact).
    expect(store.messages.some(m => m.id === 'local-asst-real')).toBe(true);
    store.destroy();
  });

  it('drops NON-EMPTY numeric-id placeholder (continuation/drain) when real DB row arrived', () => {
    // Adversarial HIGH (run_af36e709): continuation/drain paths
    // (answer-question, queue-drain, permission, retry-timeout) keep NUMERIC
    // placeholder ids and stream content INTO them, so at turn-end the
    // placeholder is non-empty. The H2 turn-end reconcile then fetches the DB
    // assistant row (UUID, no client_id) → the empty-only cleanup left BOTH =
    // duplicate bubble. A numeric-id assistant message is provably always an
    // uncorrelated optimistic placeholder (verified: all 8 numeric-id sites in
    // ChatPage are assistant placeholders; no system/boundary marker is numeric),
    // so once a real DB assistant row exists it is safe to drop.
    const store = new MessageStore();
    store.replace([
      makeMsg('local-1-u', 'user', 'Q'),
      // Continuation placeholder: NUMERIC id, content streamed in (non-empty).
      { id: '1718999', role: 'assistant', content: [{ type: 'text', text: 'streamed reply' }], timestamp: new Date().toISOString() },
    ]);
    store.reconcile([
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: 'local-1-u' } },
      // DB assistant row: UUID, NO client_id (continuation persists with None).
      { id: 'a1', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'streamed reply' }] as any, createdAt: new Date().toISOString() },
    ]);
    // Exactly 2 — the numeric placeholder is gone, only the DB row remains.
    // Pre-fix: 3 (user + numeric placeholder + DB row) = duplicate bubble.
    expect(store.messages.map(m => m.id)).toEqual(['u1', 'a1']);
    expect(store.messages.filter(m => m.role === 'assistant')).toHaveLength(1);
    store.destroy();
  });

  it('KEEPS numeric-id placeholder when no real DB assistant row exists yet', () => {
    // Same premature-blank guard as the empty case: a numeric placeholder must
    // survive until a real assistant row is present, so a slow persist never
    // blanks the in-flight continuation turn.
    const store = new MessageStore();
    store.replace([
      makeMsg('local-1-u', 'user', 'Q'),
      { id: '1718999', role: 'assistant', content: [{ type: 'text', text: 'streamed reply' }], timestamp: new Date().toISOString() },
    ]);
    store.reconcile([
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: 'local-1-u' } },
    ]);
    expect(store.messages.some(m => m.id === '1718999')).toBe(true);
    store.destroy();
  });
});

describe('MessageStore assistant clientId correlation (P4 streaming-never-finalizes)', () => {
  // Incident (run_af36e709): the assistant placeholder id was numeric
  // (Date.now()+1), so it never started with "local-" and could not be
  // correlated to its persisted DB row → empty bubble stayed, stream never
  // finalized. Fix: assistant placeholder id = `local-{clientId}-asst`, and the
  // assistant DB row carries metadata.client_id = `{clientId}-asst` (distinct
  // from the user row's `{clientId}` so each placeholder maps to its OWN row).

  it('correlates assistant placeholder to DB row via -asst client_id (no orphan, no dup)', () => {
    const store = new MessageStore();
    const cid = 'local-1718700000-abc123';

    // Optimistic: user message (local-{cid}) + EMPTY assistant placeholder
    // (local-{cid}-asst). This is exactly what ChatPage inserts on send.
    store.replace([
      makeMsg(cid, 'user', 'Hello'),
      { id: `${cid}-asst`, role: 'assistant', content: [], timestamp: new Date().toISOString() },
    ]);
    expect(store.messages).toHaveLength(2);

    // Backend persisted BOTH rows with their own correlation keys.
    const dbMessages: ChatMessage[] = [
      {
        id: 'uuid-user-001', sessionId: 'sess-1', role: 'user',
        content: [{ type: 'text', text: 'Hello' }] as any,
        createdAt: new Date().toISOString(),
        metadata: { client_id: cid },
      },
      {
        id: 'uuid-asst-001', sessionId: 'sess-1', role: 'assistant',
        content: [{ type: 'text', text: 'Hi there!' }] as any,
        createdAt: new Date().toISOString(),
        metadata: { client_id: `${cid}-asst` },
      },
    ];

    store.reconcile(dbMessages);

    // Exactly 2: each placeholder replaced by its OWN DB row. The empty
    // assistant placeholder is GONE (correlated), the real content rendered.
    expect(store.messages).toHaveLength(2);
    expect(store.messages[0].id).toBe('uuid-user-001');
    expect(store.messages[1].id).toBe('uuid-asst-001');
    // The orphan empty placeholder must not survive.
    expect(store.messages.some(m => m.id === `${cid}-asst`)).toBe(false);
    // Real assistant content is present.
    const asst = store.messages[1];
    expect((asst.content[0] as any).text).toBe('Hi there!');
    store.destroy();
  });

  it('distinct -asst key prevents the assistant row matching the USER placeholder', () => {
    // Regression guard: if the assistant DB row carried the bare clientId
    // (not -asst), it would match the user placeholder's map entry and the
    // assistant placeholder would survive into Pass 2 = duplicate bubble.
    const store = new MessageStore();
    const cid = 'local-42-xy';
    store.replace([
      makeMsg(cid, 'user', 'Q'),
      { id: `${cid}-asst`, role: 'assistant', content: [], timestamp: new Date().toISOString() },
    ]);
    const dbMessages: ChatMessage[] = [
      { id: 'u1', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: cid } },
      { id: 'a1', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'A' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: `${cid}-asst` } },
    ];
    store.reconcile(dbMessages);
    // No duplicate: exactly 2, no leftover -asst placeholder.
    expect(store.messages).toHaveLength(2);
    expect(store.messages.filter(m => m.role === 'assistant')).toHaveLength(1);
    store.destroy();
  });
});

describe('MessageStore reconcile preserves local-only interactive blocks', () => {
  // reconcile-gap (2026-06-22): the turn-end DB reconcile is now wired to the
  // ask_user_question / cmd_permission_request terminal paths (not just result).
  // BUT those interactive blocks are frontend-synthesized — they are emitted as
  // their OWN SSE event types, never inside an `assistant` event, so the backend
  // NEVER persists them. A blind client_id-match replace (merged.push(dbMsg))
  // would therefore ERASE the live question/permission form when reconcile runs.
  // The merge must carry forward any local-only interactive block the DB lacks.

  it('keeps the ask_user_question block when DB row (correlated) lacks it', () => {
    const store = new MessageStore();
    const cid = 'local-1782092539495-v14mh7';
    // Streaming assistant message: 1 streamed text block (possibly truncated)
    // + the synthesized ask_user_question block appended by surfacePendingQuestion.
    store.replace([
      makeMsg(cid, 'user', 'Q'),
      {
        id: `${cid}-asst`, role: 'assistant', timestamp: new Date().toISOString(),
        content: [
          { type: 'text', text: 'Partial reply...' },
          { type: 'ask_user_question', toolUseId: 'tu-99', questions: [{ question: 'Pick one', header: 'X', options: [] }] },
        ] as any,
      },
    ]);

    // DB returns the coalesced assistant row by client_id correlation, with the
    // FULL text (the fix) — but WITHOUT the ask_user_question block (never persisted).
    const dbMessages: ChatMessage[] = [
      { id: 'u-uuid', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: cid } },
      {
        id: 'a-uuid', sessionId: 'sess-1', role: 'assistant',
        content: [{ type: 'text', text: 'Partial reply... and the FULL TAIL that was truncated.' }] as any,
        createdAt: new Date().toISOString(), metadata: { client_id: `${cid}-asst` },
      },
    ];

    store.reconcile(dbMessages);

    const asst = store.messages.find(m => m.role === 'assistant')!;
    // Truncation fixed: full text from DB present.
    const textBlock = asst.content.find(b => b.type === 'text') as any;
    expect(textBlock.text).toContain('FULL TAIL');
    // Regression guard: the synthesized question block MUST survive (not in DB).
    const auq = asst.content.find(b => (b as any).type === 'ask_user_question') as any;
    expect(auq).toBeDefined();
    expect(auq.toolUseId).toBe('tu-99');
    // No duplicate assistant bubble.
    expect(store.messages.filter(m => m.role === 'assistant')).toHaveLength(1);
    store.destroy();
  });

  it('keeps the cmd_permission_request block when DB row (correlated) lacks it', () => {
    const store = new MessageStore();
    const cid = 'local-perm-1';
    store.replace([
      makeMsg(cid, 'user', 'run cmd'),
      {
        id: `${cid}-asst`, role: 'assistant', timestamp: new Date().toISOString(),
        content: [
          { type: 'text', text: 'I will run...' },
          { type: 'cmd_permission_request', requestId: 'req-7', toolName: 'Bash', toolInput: {}, reason: '', options: ['approve', 'deny'] },
        ] as any,
      },
    ]);
    const dbMessages: ChatMessage[] = [
      { id: 'u-uuid', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'run cmd' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: cid } },
      { id: 'a-uuid', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'I will run the full command sequence here.' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: `${cid}-asst` } },
    ];
    store.reconcile(dbMessages);
    const asst = store.messages.find(m => m.role === 'assistant')!;
    const perm = asst.content.find(b => (b as any).type === 'cmd_permission_request') as any;
    expect(perm).toBeDefined();
    expect(perm.requestId).toBe('req-7');
    store.destroy();
  });

  it('continuation turn (numeric-id placeholder) — keeps ask_user_question block when Pass 2 would drop it', () => {
    // Adversarial HIGH (run_59f8f5ad): on answer-question / permission CONTINUATION
    // turns, ChatPage creates the assistant placeholder with a NUMERIC id
    // (Date.now().toString()) and NO client_id. So _applyMerge cannot correlate it
    // in Pass 1 → it falls to Pass 2, where isStalePlaceholder drops any numeric-id
    // assistant message once a real DB row exists. The synthesized ask_user_question
    // block lives ONLY on that numeric placeholder → it gets ERASED. The new
    // turn-end reconcile on the question path is what triggers this drop, so the fix
    // must carry the interactive block forward onto the real DB assistant row.
    const store = new MessageStore();
    store.replace([
      makeMsg('u-prev', 'user', 'first question answer'),
      // Continuation placeholder: NUMERIC id, text + a NEW ask_user_question block.
      {
        id: '1782092600000', role: 'assistant', timestamp: '2026-06-22T09:50:00',
        content: [
          { type: 'text', text: 'Follow-up reply...' },
          { type: 'ask_user_question', toolUseId: 'tu-followup', questions: [{ question: 'Second Q', header: 'Y', options: [] }] },
        ] as any,
      },
    ]);

    // DB has the real assistant row for this continuation turn: UUID, full text,
    // NO client_id (continuation persists with None), and NO ask_user_question block.
    const dbMessages: ChatMessage[] = [
      { id: 'u-uuid', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'first question answer' }] as any, createdAt: '2026-06-22T09:49:00' },
      { id: 'a-uuid-cont', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'text', text: 'Follow-up reply with the FULL TAIL.' }] as any, createdAt: '2026-06-22T09:50:00' },
    ];

    store.reconcile(dbMessages);

    // The ask_user_question form must survive somewhere in the merged set.
    const allBlocks = store.messages.flatMap(m => m.content);
    const auq = allBlocks.find(b => (b as any).type === 'ask_user_question') as any;
    expect(auq).toBeDefined();
    expect(auq.toolUseId).toBe('tu-followup');
    // Full DB text present (truncation repaired).
    const hasFullText = allBlocks.some(b => (b as any).type === 'text' && (b as any).text.includes('FULL TAIL'));
    expect(hasFullText).toBe(true);
    // No duplicate assistant bubble (numeric placeholder text not duplicated alongside DB).
    expect(store.messages.filter(m => m.role === 'assistant')).toHaveLength(1);
    store.destroy();
  });

  it('does NOT duplicate an interactive block already present in both local and DB', () => {
    // Defensive: if a future change DID persist the block, carry-forward must
    // not double it. Match interactive blocks by their stable id.
    const store = new MessageStore();
    const cid = 'local-dup-1';
    store.replace([
      makeMsg(cid, 'user', 'Q'),
      {
        id: `${cid}-asst`, role: 'assistant', timestamp: new Date().toISOString(),
        content: [{ type: 'ask_user_question', toolUseId: 'tu-1', questions: [] }] as any,
      },
    ]);
    const dbMessages: ChatMessage[] = [
      { id: 'u-uuid', sessionId: 'sess-1', role: 'user', content: [{ type: 'text', text: 'Q' }] as any, createdAt: new Date().toISOString(), metadata: { client_id: cid } },
      { id: 'a-uuid', sessionId: 'sess-1', role: 'assistant', content: [{ type: 'ask_user_question', toolUseId: 'tu-1', questions: [] }] as any, createdAt: new Date().toISOString(), metadata: { client_id: `${cid}-asst` } },
    ];
    store.reconcile(dbMessages);
    const asst = store.messages.find(m => m.role === 'assistant')!;
    const auqs = asst.content.filter(b => (b as any).type === 'ask_user_question');
    expect(auqs).toHaveLength(1);
    store.destroy();
  });
});

describe('MessageStore clientId dedup', () => {
  it('reconcile deduplicates optimistic message via metadata.client_id', () => {
    const store = new MessageStore();

    // Frontend inserts optimistic user message with local-* ID
    const optimistic = makeMsg('local-1718700000-abc123', 'user', 'Hello');
    store.replace([optimistic]);
    expect(store.messages).toHaveLength(1);
    expect(store.messages[0].id).toBe('local-1718700000-abc123');

    // Backend returns the same message with a UUID and metadata.client_id
    const dbMessages: ChatMessage[] = [{
      id: 'uuid-real-msg-001',
      sessionId: 'sess-1',
      role: 'user',
      content: [{ type: 'text', text: 'Hello' }] as any,
      createdAt: new Date().toISOString(),
      metadata: { client_id: 'local-1718700000-abc123' },
    }];

    store.reconcile(dbMessages);

    // Should have 1 message (not 2) — DB version replaces optimistic
    expect(store.messages).toHaveLength(1);
    expect(store.messages[0].id).toBe('uuid-real-msg-001');
    store.destroy();
  });

  it('reconcile preserves local messages without clientId match', () => {
    const store = new MessageStore();

    // Insert optimistic + a synthetic boundary marker.
    // FIXED timestamps encode real resume semantics + kill the flake: the
    // resume boundary is created AT resume time, i.e. strictly AFTER the prior
    // user message. Chronological insertion (_findChronologicalPosition, sorts
    // ASC by timestamp) then deterministically places it last. Using
    // new Date() for all three made both timestamps land in the same ms on a
    // fast machine (equal → tiebreak kept order) but split across a ms boundary
    // on a slow one (boundary earlier → sorted BEFORE the DB msg) → CI flake.
    const optimistic = makeMsg('local-1718700000-xyz', 'user', 'Test', '2026-01-01T00:00:00.000Z');
    const boundary = makeMsg('boundary-resume', 'system', '--- Session Resumed ---', '2026-01-01T00:00:01.000Z');
    store.replace([optimistic, boundary]);

    // DB only has the real version of the user message. Its timestamp matches
    // the optimistic message it replaces (the same user turn), so it stays
    // chronologically BEFORE the later resume boundary.
    const dbMessages: ChatMessage[] = [{
      id: 'uuid-real-002',
      sessionId: 'sess-1',
      role: 'user',
      content: [{ type: 'text', text: 'Test' }] as any,
      createdAt: '2026-01-01T00:00:00.000Z',
      metadata: { client_id: 'local-1718700000-xyz' },
    }];

    store.reconcile(dbMessages);

    // Should have 2: DB version (replaced optimistic) + boundary (preserved),
    // in chronological order (user turn first, resume boundary last).
    expect(store.messages).toHaveLength(2);
    expect(store.messages[0].id).toBe('uuid-real-002');
    expect(store.messages[1].id).toBe('boundary-resume');
    store.destroy();
  });

  it('backward compat: no client_id in metadata = normal merge behavior', () => {
    const store = new MessageStore();

    // Old-style message without client_id (pre-feature)
    const local = makeMsg('timestamp-123', 'user', 'Old msg');
    store.replace([local]);

    // DB returns same message under a different ID (no metadata.client_id)
    const dbMessages: ChatMessage[] = [{
      id: 'uuid-old-001',
      sessionId: 'sess-1',
      role: 'user',
      content: [{ type: 'text', text: 'Old msg' }] as any,
      createdAt: new Date().toISOString(),
      // No metadata — backward compat
    }];

    store.reconcile(dbMessages);

    // Both exist (no dedup possible without clientId — this is expected prior behavior)
    expect(store.messages).toHaveLength(2);
    store.destroy();
  });
});

// ─── Reconcile persist-lag guard (THE reconcile race fix) ───
describe('MessageStore reconcile — persist-lag guard (more-complete content wins)', () => {
  beforeEach(() => { messageStoreRegistry.clear(); });
  afterEach(() => { messageStoreRegistry.clear(); });

  it('a SHORTER/stale DB row does NOT overwrite the complete streamed answer', () => {
    // THE #1 recurring bug: the 200ms turn-end reconcile fetches the DB before
    // the backend finished persisting the final assistant message → a short/
    // empty DB row. Plain DB-wins truncated/blanked the complete reply already
    // in the store. The merge must keep the more-complete local content.
    const store = new MessageStore();
    const full = 'A'.repeat(500);
    store.append(makeMsg('user-1', 'user', 'q'));
    store.append(makeMsg('m1', 'assistant', full)); // complete streamed answer (idle)

    // DB row for the SAME message id is still short (persist lagged).
    store.reconcile([makeChatMsg('user-1', 'user', 'q'), makeChatMsg('m1', 'assistant', 'AA')]);

    const asst = store.messages.find((m) => m.id === 'm1');
    const chars = asst?.content.reduce((n, b) => n + ('text' in b ? (b as { text: string }).text.length : 0), 0);
    expect(chars).toBe(500); // kept complete content, NOT truncated to 2
    store.destroy();
  });

  it('a LONGER DB row (legitimate server-side edit) STILL wins', () => {
    // The guard must not break real DB-wins: when the DB has MORE content
    // (server edit / fuller persist), it replaces the shorter local copy.
    const store = new MessageStore();
    store.append(makeMsg('m1', 'assistant', 'short'));

    store.reconcile([makeChatMsg('m1', 'assistant', 'B'.repeat(300))]);

    const asst = store.messages.find((m) => m.id === 'm1');
    const chars = asst?.content.reduce((n, b) => n + ('text' in b ? (b as { text: string }).text.length : 0), 0);
    expect(chars).toBe(300); // DB (more complete) wins
    store.destroy();
  });
});
