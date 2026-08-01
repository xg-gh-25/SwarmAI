/**
 * run_4f32022a — false-offline, the last unclosed path (JS-poll half).
 *
 * The Rust watchdog was made heartbeat-aware (ce9f4c18): while the daemon PROCESS
 * is alive but the event loop is stalled, it emits `backend-degraded` every ~3s
 * instead of declaring death. But the JS 30s poll in useHealthMonitor is a SECOND,
 * INDEPENDENT offline judge: on 2 consecutive /health failures it flips to
 * 'disconnected' (disables chat input) WITHOUT consulting that liveness signal.
 * So a *sustained* stall (loop CPU-bound >30s, two polls each >5s) would still let
 * the JS poll independently declare 'disconnected' and override the Rust-supplied
 * 'degraded' — re-disabling inputs on a process that is provably alive.
 *
 * Fix: the degraded handler records lastDegradedAt; handleFailure, before flipping
 * to disconnected, checks whether a degraded (liveness) signal arrived within a
 * recent window (Rust emits every 3s during a stall). If so → map to 'degraded',
 * NOT 'disconnected' → inputs stay enabled. A real death (terminated event) clears
 * the timestamp so a genuine outage is never masked. On Hive/browser (no Tauri
 * events) the timestamp stays 0 → exact current behavior (zero regression).
 */
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const cbs = vi.hoisted(() => ({
  degraded: undefined as undefined | (() => void),
  terminatedRestarting: undefined as undefined | (() => void),
}));

vi.mock('../../services/tauri', () => ({
  isDesktop: () => true,
  getApiBaseUrl: () => 'http://test',
  setBackendPort: vi.fn(),
  tauriService: {
    onBackendTerminatedRestarting: vi.fn((cb: () => void) => {
      cbs.terminatedRestarting = cb;
      return Promise.resolve(() => {});
    }),
    onBackendRestarted: vi.fn().mockResolvedValue(() => {}),
    onBackendResumed: vi.fn().mockResolvedValue(() => {}),
    onBackendTerminated: vi.fn().mockResolvedValue(() => {}),
    onBackendDegraded: vi.fn((cb: () => void) => {
      cbs.degraded = cb;
      return Promise.resolve(() => {});
    }),
  },
}));

vi.stubEnv('DEV', false);

import { useHealthMonitor } from '../useHealthMonitor';
import { ToastProvider } from '../../contexts/ToastContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ToastProvider>{children}</ToastProvider>
);

/** Make every /health fetch fail (network/timeout) to drive handleFailure. */
function stubFailingFetch() {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('timeout')));
}

describe('run_4f32022a: JS poll respects recent heartbeat/degraded liveness signal', () => {
  beforeEach(() => {
    cbs.degraded = undefined;
    cbs.terminatedRestarting = undefined;
    stubFailingFetch();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('2 poll failures AFTER a recent degraded signal → stays degraded (inputs usable), NOT disconnected', async () => {
    // failureThreshold=2, immediate check on mount already fired once (fetch fails).
    const { result } = renderHook(
      () => useHealthMonitor({ intervalMs: 1_000_000, failureThreshold: 2 }),
      { wrapper },
    );
    await waitFor(() => expect(cbs.degraded).toBeTypeOf('function'));

    // Rust proved the process alive moments ago (heartbeat-fresh → degraded).
    act(() => { cbs.degraded!(); });
    await waitFor(() => expect(result.current.state.status).toBe('degraded'));

    // Now drive JS poll failures past the threshold. Because a degraded signal
    // arrived within the liveness window, these must NOT escalate to disconnected.
    await act(async () => { await result.current.checkNow(); });
    await act(async () => { await result.current.checkNow(); });

    expect(result.current.state.status).toBe('degraded');
    expect(result.current.state.status).not.toBe('disconnected');
  });

  it('regression: 2 poll failures with NO liveness signal → disconnected (Hive/browser path unchanged)', async () => {
    const { result } = renderHook(
      () => useHealthMonitor({ intervalMs: 1_000_000, failureThreshold: 2 }),
      { wrapper },
    );
    await waitFor(() => expect(cbs.degraded).toBeTypeOf('function'));

    // No degraded event fired → no liveness signal → must behave exactly as before.
    await act(async () => { await result.current.checkNow(); });
    await act(async () => { await result.current.checkNow(); });

    await waitFor(() => expect(result.current.state.status).toBe('disconnected'));
  });

  it('window EXPIRY: a degraded signal older than the 10s window no longer masks → poll failures reach disconnected', async () => {
    vi.useFakeTimers();
    try {
      const { result } = renderHook(
        () => useHealthMonitor({ intervalMs: 1_000_000, failureThreshold: 2 }),
        { wrapper },
      );
      await vi.waitFor(() => expect(cbs.degraded).toBeTypeOf('function'));

      // Liveness proven at T0.
      act(() => { cbs.degraded!(); });
      await vi.waitFor(() => expect(result.current.state.status).toBe('degraded'));

      // Advance PAST the 10s liveness window with NO further degraded events
      // (the Rust watchdog stopped proving liveness — the real signature of a
      // backend that went from busy to gone).
      await act(async () => { await vi.advanceTimersByTimeAsync(11_000); });

      // Now poll failures must escalate to disconnected (window expired).
      await act(async () => { await result.current.checkNow(); });
      await act(async () => { await result.current.checkNow(); });

      expect(result.current.state.status).toBe('disconnected');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a real death (terminated) clears the liveness signal → later poll failures DO reach disconnected', async () => {
    const { result } = renderHook(
      () => useHealthMonitor({ intervalMs: 1_000_000, failureThreshold: 2 }),
      { wrapper },
    );
    await waitFor(() => {
      expect(cbs.degraded).toBeTypeOf('function');
      expect(cbs.terminatedRestarting).toBeTypeOf('function');
    });

    // Liveness seen...
    act(() => { cbs.degraded!(); });
    await waitFor(() => expect(result.current.state.status).toBe('degraded'));

    // ...then a genuine death signal (miss streak / pid gone). Must disable + clear
    // the liveness stamp so it cannot mask the outage on subsequent polls.
    act(() => { cbs.terminatedRestarting!(); });
    await waitFor(() => expect(result.current.state.status).toBe('disconnected'));

    await act(async () => { await result.current.checkNow(); });
    await act(async () => { await result.current.checkNow(); });
    expect(result.current.state.status).toBe('disconnected');
  });
});
