/**
 * fileChangedBroker (D1, run_5d9178bf) — ONE window listener for swarm:file-changed,
 * fanned out to N subscribers. Replaces 5 raw addEventListener sites (FileEditorCore,
 * FileViewer, useChangeStatus, useReferencedFiles, useCanvasAutoSurface) — the OT01
 * "N listeners each re-parsing + re-matching" fragmentation.
 *
 * Contract:
 *  - attaches EXACTLY ONE window listener, lazily on the first subscribe;
 *  - detaches it when the last subscriber unsubscribes (no leak);
 *  - each subscriber gets the raw CustomEvent (behavior-preserving — consumers keep
 *    their own detail parsing / tab-scope filter, unchanged);
 *  - a THROWING subscriber must NOT break sibling subscribers (fail-open per-fn).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { subscribeFileChanged, __brokerListenerCount } from '../fileChangedBroker';

function fire(detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent('swarm:file-changed', { detail }));
}

beforeEach(() => {
  // ensure a clean broker between tests
  expect(__brokerListenerCount()).toBe(0);
});
afterEach(() => {
  expect(__brokerListenerCount()).toBe(0);
});

describe('fileChangedBroker — single window listener, fan-out', () => {
  it('attaches ONE window listener regardless of subscriber count', () => {
    const spy = vi.spyOn(window, 'addEventListener');
    const u1 = subscribeFileChanged(() => {});
    const u2 = subscribeFileChanged(() => {});
    const u3 = subscribeFileChanged(() => {});
    // Only ONE 'swarm:file-changed' addEventListener across 3 subscribes.
    const fcCalls = spy.mock.calls.filter((c) => c[0] === 'swarm:file-changed');
    expect(fcCalls).toHaveLength(1);
    expect(__brokerListenerCount()).toBe(1);
    u1(); u2(); u3();
    spy.mockRestore();
  });

  it('fans one event out to every subscriber', () => {
    const a = vi.fn();
    const b = vi.fn();
    const ua = subscribeFileChanged(a);
    const ub = subscribeFileChanged(b);
    fire({ path: 'x.ts', operation: 'written' });
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    // the raw event detail is forwarded intact
    expect((a.mock.calls[0][0] as CustomEvent).detail.path).toBe('x.ts');
    ua(); ub();
  });

  it('detaches the window listener when the last subscriber leaves', () => {
    const u1 = subscribeFileChanged(() => {});
    const u2 = subscribeFileChanged(() => {});
    expect(__brokerListenerCount()).toBe(1);
    u1();
    expect(__brokerListenerCount()).toBe(1); // still one subscriber
    u2();
    expect(__brokerListenerCount()).toBe(0); // last gone → detached
  });

  it('a throwing subscriber does NOT break siblings (fail-open per-fn)', () => {
    const good = vi.fn();
    const ubad = subscribeFileChanged(() => { throw new Error('boom'); });
    const ugood = subscribeFileChanged(good);
    expect(() => fire({ path: 'y.ts', operation: 'written' })).not.toThrow();
    expect(good).toHaveBeenCalledTimes(1);
    ubad(); ugood();
  });

  it('unsubscribe is idempotent (double-call safe)', () => {
    const u = subscribeFileChanged(() => {});
    u();
    expect(() => u()).not.toThrow();
    expect(__brokerListenerCount()).toBe(0);
  });
});
