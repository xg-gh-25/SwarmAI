/**
 * A5 (startup hazard): useHealthMonitor's Tauri backend-event effect must not
 * fire in a browser (Hive) boot.
 *
 * The effect subscribes via tauriService.onBackend*() → Tauri `listen()`, which
 * calls window.__TAURI_INTERNALS__.transformCallback — undefined in a browser →
 * throws. The effect was gated only by `isDev` (import.meta.env.DEV), NOT by
 * isDesktop(). In Hive (production browser: isDev=false, no Tauri), the effect
 * ran and fired 4 listen() calls → 4 unhandled promise rejections on every boot.
 * Every sibling (BackendStartupOverlay, the App.tsx overlays) guards with
 * isDesktop(); this hook used isDev. Fix: also early-return when !isDesktop().
 */
import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Browser/Hive: isDesktop=false. getApiBaseUrl/setBackendPort are used elsewhere
// in the hook; give them no-op stubs. The onBackend* spies are the assertion.
// vi.hoisted so the spies exist when the hoisted vi.mock factory references them.
const spies = vi.hoisted(() => ({
  onTerminatedRestarting: vi.fn().mockResolvedValue(() => {}),
  onRestarted: vi.fn().mockResolvedValue(() => {}),
  onResumed: vi.fn().mockResolvedValue(() => {}),
  onTerminated: vi.fn().mockResolvedValue(() => {}),
}));
const { onTerminatedRestarting, onRestarted, onResumed, onTerminated } = spies;

vi.mock('../../services/tauri', () => ({
  isDesktop: () => false, // Hive browser
  getApiBaseUrl: () => 'http://test',
  setBackendPort: vi.fn(),
  tauriService: {
    onBackendTerminatedRestarting: spies.onTerminatedRestarting,
    onBackendRestarted: spies.onRestarted,
    onBackendResumed: spies.onResumed,
    onBackendTerminated: spies.onTerminated,
  },
}));

// Not dev — the OLD guard (isDev) would NOT early-return here; only an
// isDesktop() guard prevents the Tauri subscriptions.
vi.stubEnv('DEV', false);

import { useHealthMonitor } from '../useHealthMonitor';
import { ToastProvider } from '../../contexts/ToastContext';

describe('A5: useHealthMonitor does not subscribe to Tauri events in Hive/browser', () => {
  beforeEach(() => {
    onTerminatedRestarting.mockClear();
    onRestarted.mockClear();
    onResumed.mockClear();
    onTerminated.mockClear();
  });

  it('does not call any tauriService.onBackend* in production browser (!isDesktop)', () => {
    // useHealthMonitor consumes useToast → wrap in ToastProvider (its real host).
    renderHook(() => useHealthMonitor(), {
      wrapper: ({ children }) => <ToastProvider>{children}</ToastProvider>,
    });
    expect(onTerminatedRestarting).not.toHaveBeenCalled();
    expect(onRestarted).not.toHaveBeenCalled();
    expect(onResumed).not.toHaveBeenCalled();
    expect(onTerminated).not.toHaveBeenCalled();
  });
});
