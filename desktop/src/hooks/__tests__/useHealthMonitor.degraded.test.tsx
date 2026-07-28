/**
 * run_13094a88 — false-offline root-fix (frontend half).
 *
 * A backend-degraded event (daemon PROCESS alive, one /health probe missed = a
 * transient >3s stall) must map to status='degraded', NOT 'disconnected'. That
 * distinction is load-bearing: ChatPage disables the chat input iff
 * status==='disconnected', so 'degraded' keeps inputs ENABLED — a transient stall
 * can no longer nuke the UI. Only a proven death (backend-terminated-restarting,
 * emitted by the Rust watchdog after the miss streak / pid-gone) disables.
 */
import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Desktop mode so the Tauri backend-event effect actually subscribes. Each
// onBackend* spy captures its callback so the test can fire the event directly.
const cbs = vi.hoisted(() => ({
  degraded: undefined as undefined | (() => void),
  terminatedRestarting: undefined as undefined | (() => void),
  resumed: undefined as undefined | ((port: number) => void),
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
    onBackendResumed: vi.fn((cb: (port: number) => void) => {
      cbs.resumed = cb;
      return Promise.resolve(() => {});
    }),
    onBackendTerminated: vi.fn().mockResolvedValue(() => {}),
    onBackendDegraded: vi.fn((cb: () => void) => {
      cbs.degraded = cb;
      return Promise.resolve(() => {});
    }),
  },
}));

// The subscription effect early-returns when isDev (import.meta.env.DEV) is true
// — vitest defaults DEV=true. Force it false so the effect subscribes (matches the
// hive-guard test's stubEnv pattern; the isDesktop()=true mock does the rest).
vi.stubEnv('DEV', false);

// Avoid the real 30s poll fetch interfering with the event-driven assertions.
vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ status: 'healthy', boot_id: 'b1' }),
}));

import { useHealthMonitor } from '../useHealthMonitor';
import { ToastProvider } from '../../contexts/ToastContext';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ToastProvider>{children}</ToastProvider>
);

describe('run_13094a88: backend-degraded → degraded state (inputs stay usable)', () => {
  beforeEach(() => {
    cbs.degraded = undefined;
    cbs.terminatedRestarting = undefined;
    cbs.resumed = undefined;
  });

  it('registers an onBackendDegraded listener in desktop mode', async () => {
    renderHook(() => useHealthMonitor(), { wrapper });
    await waitFor(() => expect(cbs.degraded).toBeTypeOf('function'));
  });

  it('maps a degraded event to status="degraded", NOT "disconnected"', async () => {
    const { result } = renderHook(() => useHealthMonitor(), { wrapper });
    await waitFor(() => expect(cbs.degraded).toBeTypeOf('function'));

    act(() => {
      cbs.degraded!();
    });

    await waitFor(() => expect(result.current.state.status).toBe('degraded'));
    // The load-bearing assertion: NOT disconnected → ChatPage keeps inputs enabled.
    expect(result.current.state.status).not.toBe('disconnected');
  });

  it('escalation: a subsequent terminated-restarting event overrides degraded → disconnected', async () => {
    const { result } = renderHook(() => useHealthMonitor(), { wrapper });
    await waitFor(() => {
      expect(cbs.degraded).toBeTypeOf('function');
      expect(cbs.terminatedRestarting).toBeTypeOf('function');
    });

    act(() => { cbs.degraded!(); });
    await waitFor(() => expect(result.current.state.status).toBe('degraded'));

    // Rust escalates (miss streak reached / pid gone) → real death → disable inputs.
    act(() => { cbs.terminatedRestarting!(); });
    await waitFor(() => expect(result.current.state.status).toBe('disconnected'));
  });

  it('a degraded event does NOT downgrade an already-disconnected (real death) state', async () => {
    const { result } = renderHook(() => useHealthMonitor(), { wrapper });
    await waitFor(() => {
      expect(cbs.degraded).toBeTypeOf('function');
      expect(cbs.terminatedRestarting).toBeTypeOf('function');
    });

    act(() => { cbs.terminatedRestarting!(); });
    await waitFor(() => expect(result.current.state.status).toBe('disconnected'));

    // A late/stray degraded event must not re-enable inputs on a dead backend.
    act(() => { cbs.degraded!(); });
    expect(result.current.state.status).toBe('disconnected');
  });
});
