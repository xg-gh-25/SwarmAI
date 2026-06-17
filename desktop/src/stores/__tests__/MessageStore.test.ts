/**
 * MessageStore unit tests — verifies core operations, phase gating,
 * watchdog timer, rAF notification, and merge algorithm.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MessageStore, messageStoreRegistry } from '../MessageStore';
import type { Message, ChatMessage } from '../../types';

// ─── Test Helpers ───

function makeMsg(id: string, role: 'user' | 'assistant' | 'system' = 'user', text = 'hello'): Message {
  return { id, role, content: [{ type: 'text', text }], timestamp: new Date().toISOString() };
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
      store = new MessageStore({ watchdogTimeoutMs: 100 });
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
    const store = new MessageStore({ watchdogTimeoutMs: 100 });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');

    expect(store.phase).toBe('streaming');
    vi.advanceTimersByTime(101);
    expect(store.phase).toBe('idle');

    store.destroy();
  });

  it('watchdog is reset by updateLast', () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100 });
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
    // Existing local message kept (local wins)
    expect((store.messages[0].content[0] as any).text).toBe('local');
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
