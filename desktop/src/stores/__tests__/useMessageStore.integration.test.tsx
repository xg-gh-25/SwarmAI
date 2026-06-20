/* eslint-disable @typescript-eslint/no-explicit-any */
// Feature: chat-tab-view-isolation, Step 0 (F7) — useMessageStore live-streaming gate
/**
 * useMessageStore live-streaming integration test.
 *
 * BLOCKING GATE artifact for Step 0 (F7): `useMessageStore` was dead code
 * (zero component callers) and the whole design pivots onto it, so this test
 * exercises the hook against a LIVE `MessageStore` (via `messageStoreRegistry`)
 * across a full streaming lifecycle BEFORE any ChatPage rewrite.
 *
 * What is verified:
 * - The hook's reactive `messages` tracks the store at each lifecycle stage
 *   (append → startStreaming/updateLast×N → endStreaming → reconcile).
 * - The rAF-gated notify flush path (and its 100 ms setTimeout fallback) reaches
 *   React, observed via @testing-library `waitFor`.
 * - The hook tolerates `messageStoreRegistry.destroy(tabId)` while subscribed:
 *   no throw, the store's data is released ([]), and unmount cleanup runs clean.
 * - Basic isolation: a second store for a different tabId is NOT observed by the
 *   first hook.
 *
 * Validates: Requirements 2.1, 2.4, 5.3, 11.3.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { messageStoreRegistry } from '../MessageStore';
import { useMessageStore } from '../useMessageStore';
import type { Message, ChatMessage, ContentBlock } from '../../types';

// ─── Test Helpers ───

function makeMsg(
  id: string,
  role: 'user' | 'assistant' | 'system' = 'user',
  text = 'hello',
): Message {
  return { id, role, content: [{ type: 'text', text }], timestamp: new Date().toISOString() };
}

/** Assistant placeholder with no content blocks yet (text deltas will fill it). */
function makePlaceholder(id: string): Message {
  return { id, role: 'assistant', content: [], timestamp: new Date().toISOString() };
}

function makeChatMsg(
  id: string,
  role: 'user' | 'assistant' | 'system' = 'user',
  text = 'hello',
): ChatMessage {
  return {
    id,
    sessionId: 'sess-1',
    role,
    content: [{ type: 'text', text }] as any,
    createdAt: new Date().toISOString(),
  };
}

/**
 * Local mirror of useChatStreamingLifecycle.applyTextDelta — appends a text
 * token to the last unconfirmed text block (or creates one). Defined locally to
 * keep this gate test dependency-free (no heavy hook module import).
 */
function applyTextDelta(msg: Message, text: string): Message {
  const content = [...msg.content];
  const last = content[content.length - 1];
  if (last && last.type === 'text' && !last._confirmed) {
    content[content.length - 1] = { ...last, text: (last.text ?? '') + text };
  } else {
    content.push({ type: 'text', text } as ContentBlock);
  }
  return { ...msg, content };
}

/** Concatenate all text-block text in a message (streamed accumulation). */
function textOf(msg: Message | undefined): string {
  if (!msg) return '';
  return msg.content
    .filter((b) => b.type === 'text')
    .map((b) => (b as any).text ?? '')
    .join('');
}

describe('useMessageStore — live streaming integration (F7 gate)', () => {
  beforeEach(() => {
    // Fresh registry per test so stores never leak across cases.
    messageStoreRegistry.clear();
  });

  afterEach(() => {
    messageStoreRegistry.clear();
  });

  it('tracks the store across a full append → stream → end → reconcile lifecycle', async () => {
    const tabId = 'tab-A';
    const { result } = renderHook(() => useMessageStore(tabId));

    // Hook resolves to a live store immediately (non-null for a real tabId).
    expect(result.current).not.toBeNull();
    const store = result.current!.store;
    expect(store).toBe(messageStoreRegistry.get(tabId));

    // ── 1. append user + assistant placeholder → hook reflects both ──
    const userMsg = makeMsg('u1', 'user', 'hi there');
    const assistantId = 'a1';
    act(() => {
      store.append(userMsg);
      store.append(makePlaceholder(assistantId));
    });

    // rAF-gated notify → React commit observed via waitFor (also covers the
    // 100 ms setTimeout fallback path since waitFor polls past it).
    await waitFor(() => {
      expect(result.current!.messages).toHaveLength(2);
    });
    expect(result.current!.messages[0].id).toBe('u1');
    expect(result.current!.messages[1].id).toBe(assistantId);

    // ── 2. startStreaming + repeated updateLast (text deltas) ──
    act(() => {
      store.startStreaming(assistantId);
    });
    const N = 5;
    act(() => {
      for (let i = 0; i < N; i++) {
        store.updateLast(
          (m) => applyTextDelta(m, 'x'),
          (m) => m.id === assistantId,
        );
      }
    });

    await waitFor(() => {
      const last = result.current!.messages.find((m) => m.id === assistantId);
      expect(textOf(last)).toBe('x'.repeat(N));
    });

    // ── 3. endStreaming + reconcile([...]) → hook reflects reconciled set ──
    act(() => {
      store.endStreaming();
    });
    expect(store.phase).toBe('idle');

    // Reconcile against a DB set: keeps u1 + a1 (matched by id) and adds a new
    // assistant message a2. Default conversion (no injected toDisplayMessage).
    const dbSet: ChatMessage[] = [
      makeChatMsg('u1', 'user', 'hi there'),
      makeChatMsg(assistantId, 'assistant', 'xxxxx'),
      makeChatMsg('a2', 'assistant', 'follow-up'),
    ];
    act(() => {
      store.reconcile(dbSet);
    });

    await waitFor(() => {
      expect(result.current!.messages.map((m) => m.id)).toEqual([
        'u1',
        assistantId,
        'a2',
      ]);
    });
    // The reconciled view equals the store snapshot (settled consistency).
    expect(result.current!.messages.map((m) => m.id)).toEqual(
      store.getSnapshot().map((m) => m.id),
    );
  });

  it('tolerates destroy() while subscribed — no throw, store released, clean unmount', async () => {
    const tabId = 'tab-destroy';
    const { result, unmount } = renderHook(() => useMessageStore(tabId));
    const store = result.current!.store;

    act(() => {
      store.append(makeMsg('m1'));
      store.append(makeMsg('m2'));
    });
    await waitFor(() => {
      expect(result.current!.messages).toHaveLength(2);
    });

    // Destroy the active tab's store while the hook is still subscribed.
    expect(() => {
      act(() => {
        messageStoreRegistry.destroy(tabId);
      });
    }).not.toThrow();

    // Store data released (R11.3) and removed from the registry.
    expect(store.messages).toEqual([]);
    expect(messageStoreRegistry.get(tabId)).toBeUndefined();

    // Hook did not crash — still exposes an array snapshot.
    expect(Array.isArray(result.current!.messages)).toBe(true);

    // Unmount cleanup (unsubscribe) runs without error.
    expect(() => unmount()).not.toThrow();

    // A fresh mount on the same tabId gets a brand-new empty store.
    const { result: result2 } = renderHook(() => useMessageStore(tabId));
    expect(result2.current!.messages).toEqual([]);
  });

  it('isolates two tabs — a second store is not observed by the first hook', async () => {
    const tabA = 'tab-iso-A';
    const tabB = 'tab-iso-B';
    const hookA = renderHook(() => useMessageStore(tabA));
    const hookB = renderHook(() => useMessageStore(tabB));

    const storeA = hookA.result.current!.store;
    const storeB = hookB.result.current!.store;
    expect(storeA).not.toBe(storeB);

    // Write to A's store only → A reflects it, B stays empty.
    act(() => {
      storeA.append(makeMsg('a-only', 'assistant', 'alpha'));
    });
    await waitFor(() => {
      expect(hookA.result.current!.messages).toHaveLength(1);
    });
    expect(hookB.result.current!.messages).toHaveLength(0);

    // Write to B's store only → B reflects it, A unchanged (still just 'a-only').
    act(() => {
      storeB.append(makeMsg('b-only', 'assistant', 'beta'));
    });
    await waitFor(() => {
      expect(hookB.result.current!.messages).toHaveLength(1);
    });
    expect(hookA.result.current!.messages.map((m) => m.id)).toEqual(['a-only']);
    expect(hookB.result.current!.messages.map((m) => m.id)).toEqual(['b-only']);
  });
});
