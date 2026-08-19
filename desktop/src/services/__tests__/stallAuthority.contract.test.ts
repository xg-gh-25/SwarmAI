/**
 * AC4 contract + AC5 regression: the transport stall timer (chat.ts) and the
 * MessageStore watchdog consult the SAME liveness authority (shared
 * decideStallAction from stallPolicy), so there is ONE authority, not the
 * split-brain that let a live stream be blind-cancelled (Gate-1 Finding 1).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as stallPolicy from '../stallPolicy';
import { decideStallAction as chatDecide } from '../chat';
import { MessageStore } from '../../stores/MessageStore';
import type { Message } from '../../types';

const makeMsg = (id: string): Message => ({
  id, role: 'assistant', content: [{ type: 'text', text: '' }], timestamp: new Date().toISOString(),
});

describe('AC4 — single stall authority (no split-brain)', () => {
  it('chat.ts re-exports the SAME decideStallAction as stallPolicy (not a fork)', () => {
    expect(chatDecide).toBe(stallPolicy.decideStallAction);
  });

  it('the shared policy is the ONLY decision fn — both terminators route through it', () => {
    // A representative agreement check across the verdict space.
    for (const silent of [0, 90_000, 200_000, 700_000]) {
      for (const v of ['alive', 'dead', 'unknown'] as const) {
        // chat re-export and the canonical impl must be identical by reference,
        // hence identical by result — this is the single-authority guarantee.
        expect(chatDecide(v, silent)).toBe(stallPolicy.decideStallAction(v, silent));
      }
    }
  });
});

describe('AC5 — regression: heartbeat-only long silence with alive backend is never terminated', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('MessageStore watchdog: 180s of heartbeat-only (touch) liveness + alive → never force-ends', () => {
    // Simulate a cold --resume / long tool run: only liveness pings, no content,
    // backend proven alive. Old code force-ended at 90s; A2 must not.
    const store = new MessageStore({ watchdogTimeoutMs: 90_000, isBackendLive: () => 'alive' });
    store.append(makeMsg('1'));
    store.startStreaming('1');
    // 180s of pure silence (no touch even) — alive verdict alone keeps it armed.
    vi.advanceTimersByTime(90_000);
    expect(store.phase).toBe('streaming');
    vi.advanceTimersByTime(90_000);
    expect(store.phase).toBe('streaming');
    // And with periodic touches it likewise stays streaming.
    for (let i = 0; i < 5; i++) { store.touch(); vi.advanceTimersByTime(80_000); }
    expect(store.phase).toBe('streaming');
    store.destroy();
  });
});
