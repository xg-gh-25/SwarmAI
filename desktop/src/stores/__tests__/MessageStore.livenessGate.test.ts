/**
 * A2 (run_d2f25153, Gate-1 Finding 1): the MessageStore watchdog is the SECOND
 * blind 90s stream terminator. It must consult the loop-independent liveness
 * verdict (via the shared decideStallAction) so it NEVER force-ends a live-but-
 * silent stream — matching the transport stall timer (single authority).
 *
 * These are the mutation-provable invariants that go RED if the gate is reverted
 * to the old unconditional fire.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { MessageStore } from '../MessageStore';
import { UNKNOWN_REARM_BUDGET_MS } from '../../services/stallPolicy';
import type { Message } from '../../types';

const makeMsg = (id: string, role: 'user' | 'assistant' | 'system'): Message => ({
  id, role, content: [{ type: 'text', text: '' }], timestamp: new Date().toISOString(),
});

describe('MessageStore watchdog — liveness gate (A2 / Gate-1 Finding 1)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("alive → NEVER force-ends, no matter how long silent (the whole fix)", () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'alive' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    // 50 timeout windows of pure silence — old code force-ended at the first.
    vi.advanceTimersByTime(100 * 50);
    expect(store.phase).toBe('streaming'); // alive → re-armed every time, never ended
    store.destroy();
  });

  it("dead → force-ends at one timeout (termination authorized)", () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'dead' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    vi.advanceTimersByTime(101);
    expect(store.phase).toBe('idle');
    store.destroy();
  });

  it("unknown → re-arms under the budget, force-ends at/after it (bounded)", () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'unknown' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    // Just under budget → still streaming (covers cold-start latency).
    vi.advanceTimersByTime(UNKNOWN_REARM_BUDGET_MS - 100);
    expect(store.phase).toBe('streaming');
    // Cross the budget → force-ends.
    vi.advanceTimersByTime(200);
    expect(store.phase).toBe('idle');
    store.destroy();
  });

  it("no verdict wired → defaults to unknown (bounded), NOT the old blind 1-timeout fire", () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100 }); // no isBackendLive
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    // Old behaviour would be idle here; new default (unknown) keeps it streaming
    // well past a single timeout.
    vi.advanceTimersByTime(101);
    expect(store.phase).toBe('streaming');
    store.destroy();
  });

  it("real liveness (touch) resets the silence accumulator so the budget restarts", () => {
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => 'unknown' });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    // Advance near the budget, then a real touch resets the accumulator.
    vi.advanceTimersByTime(UNKNOWN_REARM_BUDGET_MS - 100);
    store.touch();
    // Another near-budget span — still streaming because the accumulator reset.
    vi.advanceTimersByTime(UNKNOWN_REARM_BUDGET_MS - 100);
    expect(store.phase).toBe('streaming');
    store.destroy();
  });

  it("alive verdict can flip to dead mid-stream and then force-end (dynamic verdict)", () => {
    let verdict: 'alive' | 'dead' | 'unknown' = 'alive';
    const store = new MessageStore({ watchdogTimeoutMs: 100, isBackendLive: () => verdict });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    vi.advanceTimersByTime(100 * 5);
    expect(store.phase).toBe('streaming'); // alive → survived
    verdict = 'dead';
    vi.advanceTimersByTime(101); // next fire sees dead → force-ends
    expect(store.phase).toBe('idle');
    store.destroy();
  });
});

describe('MessageStore watchdog — affordance path (Gate-2 HIGH fix)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('alive past the turn-liveness cap → fires onAffordance (visible signal), does NOT force-end', () => {
    const onAffordance = vi.fn();
    // Small timeout so the cap (ALIVE_REARM_CAP_MS) is reached in a few fires;
    // the store uses the real ALIVE_REARM_CAP_MS from stallPolicy.
    const store = new MessageStore({
      watchdogTimeoutMs: 60_000,
      isBackendLive: () => 'alive',
      onAffordance,
    });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    // Advance well past ALIVE_REARM_CAP_MS (600s) → affordance must fire, phase stays streaming.
    vi.advanceTimersByTime(660_000);
    expect(onAffordance).toHaveBeenCalled();
    expect(store.phase).toBe('streaming'); // never auto-killed a live backend
    store.destroy();
  });

  it('affordance fires at most once per silent span (no toast spam)', () => {
    const onAffordance = vi.fn();
    const store = new MessageStore({
      watchdogTimeoutMs: 60_000,
      isBackendLive: () => 'alive',
      onAffordance,
    });
    store.append(makeMsg('1', 'assistant'));
    store.startStreaming('1');
    vi.advanceTimersByTime(660_000);      // cross the cap
    vi.advanceTimersByTime(300_000);      // keep re-arming past it
    expect(onAffordance).toHaveBeenCalledTimes(1); // latched — not once per fire
    store.destroy();
  });
});
