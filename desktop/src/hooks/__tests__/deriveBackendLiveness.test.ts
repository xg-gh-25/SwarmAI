/**
 * Tests for deriveBackendLiveness — the pinned BackendStatus → liveness verdict
 * mapping that is the entire safety story of the "no-interrupt-while-alive" fix
 * (run_d2f25153, Gate-1 Finding 4).
 *
 * The load-bearing assertion: 'connected' MUST map to 'alive'. During event-loop
 * starvation the Rust watchdog only polls /health every ~30s, so status stays
 * 'connected' for up to 30s before it flips to 'degraded'. If 'connected' were
 * treated as 'unknown', the bounded-budget-then-kill path would re-create the
 * exact 158s blind-cancel this fix exists to remove.
 */
import { describe, it, expect } from 'vitest';
import { deriveBackendLiveness } from '../useHealthMonitor';
import type { BackendStatus } from '../../types';

describe('deriveBackendLiveness — pinned status→verdict mapping (Gate-1 Finding 4)', () => {
  it("'connected' → 'alive' (steady healthy; watchdog would have degraded/terminated otherwise)", () => {
    expect(deriveBackendLiveness('connected')).toBe('alive');
  });

  it("'degraded' → 'alive' (Rust watchdog PROVED process alive via loop-independent heartbeat)", () => {
    expect(deriveBackendLiveness('degraded')).toBe('alive');
  });

  it("'disconnected' → 'dead' (proven death → cancel is authorized)", () => {
    expect(deriveBackendLiveness('disconnected')).toBe('dead');
  });

  it("'initializing' → 'unknown' (app-boot before first health event — the ONLY unknown)", () => {
    expect(deriveBackendLiveness('initializing')).toBe('unknown');
  });

  it('is total over the BackendStatus union (no status falls through to a wrong default)', () => {
    const all: BackendStatus[] = ['connected', 'degraded', 'disconnected', 'initializing'];
    for (const s of all) {
      const v = deriveBackendLiveness(s);
      expect(['alive', 'dead', 'unknown']).toContain(v);
    }
    // Only 'initializing' may be 'unknown' — the anti-158s-blind-kill invariant.
    const unknowns = all.filter((s) => deriveBackendLiveness(s) === 'unknown');
    expect(unknowns).toEqual(['initializing']);
    // Only 'disconnected' may be 'dead'.
    const deads = all.filter((s) => deriveBackendLiveness(s) === 'dead');
    expect(deads).toEqual(['disconnected']);
  });
});
