/**
 * Tests for decideStallAction — the liveness-gated stall decision that replaces
 * the blind 90s reader.cancel() (run_d2f25153, approach A2).
 *
 * The stall timer is now a TRIGGER, not a cancel authority. On fire it consults
 * the loop-independent backend-liveness verdict and returns one of:
 *   - 'rearm'  → keep waiting, DO NOT cancel (backend is alive)
 *   - 'cancel' → terminate the stream (backend is proven dead/wedged, OR unknown
 *                past its bounded budget, OR alive past the turn-liveness cap...
 *                see below — a cap breach surfaces an affordance, not a cancel)
 *   - 'affordance' → alive but silent past the turn-liveness cap: surface a
 *                'still working — Stop?' hint, DO NOT auto-cancel (Gate-1 Finding 5)
 *
 * Invariants under test:
 *   AC1/AC2: alive within cap → never cancel (no matter how long silent)
 *   AC3:     dead → cancel
 *   AC6:     mapping is consumed correctly (alive/dead/unknown)
 *   AC7:     unknown → rearm until UNKNOWN budget, THEN cancel (not first fire, not never)
 *   AC8:     alive past ALIVE cap → affordance (not cancel, not infinite silent rearm)
 */
import { describe, it, expect } from 'vitest';
import {
  decideStallAction,
  UNKNOWN_REARM_BUDGET_MS,
  ALIVE_REARM_CAP_MS,
  STALL_TIMEOUT_MS,
} from '../chat';

describe('decideStallAction — liveness-gated stall (A2)', () => {
  it('AC1/AC2: alive within cap → rearm (never cancel a live stream, however long silent)', () => {
    // First fire (one stall interval of silence), backend alive.
    expect(decideStallAction('alive', STALL_TIMEOUT_MS)).toBe('rearm');
    // Many intervals of silence, still well under the cap → still rearm.
    expect(decideStallAction('alive', ALIVE_REARM_CAP_MS - 1)).toBe('rearm');
  });

  it('AC3: dead → cancel immediately on first fire', () => {
    expect(decideStallAction('dead', STALL_TIMEOUT_MS)).toBe('cancel');
  });

  it('AC7: unknown → rearm before the budget, cancel at/after the budget', () => {
    // Below budget → keep waiting (covers cold-start first-token latency).
    expect(decideStallAction('unknown', STALL_TIMEOUT_MS)).toBe('rearm');
    expect(decideStallAction('unknown', UNKNOWN_REARM_BUDGET_MS - 1)).toBe('rearm');
    // At/after budget → treat as dead.
    expect(decideStallAction('unknown', UNKNOWN_REARM_BUDGET_MS)).toBe('cancel');
    expect(decideStallAction('unknown', UNKNOWN_REARM_BUDGET_MS + 1)).toBe('cancel');
  });

  it('AC8: alive past the turn-liveness cap → affordance (NOT cancel, NOT silent rearm)', () => {
    expect(decideStallAction('alive', ALIVE_REARM_CAP_MS)).toBe('affordance');
    expect(decideStallAction('alive', ALIVE_REARM_CAP_MS + 1)).toBe('affordance');
  });

  it('bounds are sane: unknown budget >= cold-start first-token latency (>=60s); alive cap far exceeds one stall interval', () => {
    expect(UNKNOWN_REARM_BUDGET_MS).toBeGreaterThanOrEqual(60_000);
    expect(ALIVE_REARM_CAP_MS).toBeGreaterThan(STALL_TIMEOUT_MS);
  });

  it('alive never yields cancel below the cap (the core no-blind-kill invariant)', () => {
    for (const silent of [STALL_TIMEOUT_MS, 120_000, 300_000, ALIVE_REARM_CAP_MS - 1]) {
      expect(decideStallAction('alive', silent)).not.toBe('cancel');
    }
  });
});
