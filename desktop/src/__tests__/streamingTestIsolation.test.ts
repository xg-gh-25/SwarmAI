/**
 * Test isolation for the streaming harness: a tab's pending timers end with the
 * test (resetTestState clears them, as closeTab does in the app).
 *
 * What is tested: a turn that ends in one test schedules the 200 ms turn-end
 * reconcile; a later test must not see that DB read. Before the fix it did, which
 * made the sessionTurnWiring resync case fail in full-suite runs only (the timer
 * fired inside its window when the worker was slow; 2026-10-24,
 * docs/design/2026-10-24-chat-stream-structure.md A0).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { StreamEvent } from '../types';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  const conn = new Set<(c: boolean) => void>();
  return {
    connected: true,
    listeners,
    conn,
    joined: [] as string[],
    emit(e: unknown) { for (const l of listeners) l(e); },
    setConnected(v: boolean) { this.connected = v; for (const c of conn) c(v); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => fake.connected,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: (cb: (c: boolean) => void) => { fake.conn.add(cb); return () => fake.conn.delete(cb); },
    watch: (ids: string[]) => { fake.joined.push(...ids); },
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { chatService } from '../services/chat';
import {
  testTabMap, createMockDeps, initTestTab, makeMessage, resetTestState,
} from './helpers/streamingTestUtils';

const env = (turnId: string, eventSeq: number, extra: Record<string, unknown>) =>
  ({ sessionId: 'S', turnId, eventSeq, ...extra });
const text = (t: string) => ({ type: 'assistant', content: [{ type: 'text', text: t }] });
const lastAssistant = (tabId: string, rendered?: { role: string }[]) => {
  const ms = (rendered ?? messageStoreRegistry.get(tabId)?.messages ?? []) as { role: string; id: string; content: unknown; metadata?: Record<string, unknown> }[];
  return [...ms].reverse().find((m) => m.role === 'assistant');
};

describe('a turn-end reconcile timer does not outlive its test', () => {
  beforeEach(() => { resetTestState(); fake.connected = true; fake.listeners.clear(); fake.conn.clear(); });

  it('A: a turn on session S ends', () => {
    renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => { initTestTab('t1'); });
    testTabMap.get('t1')!.sessionId = 'S';
    act(() => {
      fake.emit(env('I', 1, { type: 'turn_start', source: 'injected', turnClientId: 'injected-q-asst' }));
      fake.emit(env('I', 2, text('done')));
      fake.emit(env('I', 3, { type: 'result' }));
      fake.emit(env('I', 4, { type: 'turn_end' }));
    });
  });

  it('B: a later test sees a DB read it did not cause', async () => {
    const tail = vi.spyOn(chatService, 'getReconcileTailWithRev').mockResolvedValue({ messages: [], rev: 1 });
    await new Promise((r) => setTimeout(r, 300));
    expect(tail).not.toHaveBeenCalled();
    tail.mockRestore();
  });
});
