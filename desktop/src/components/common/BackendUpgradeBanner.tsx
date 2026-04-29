/**
 * Non-blocking banner that reflects background daemon upgrade status.
 *
 * Subscribes to three Tauri events dispatched by the Rust-side
 * `sync_daemon_version_background` helper (see `desktop/src-tauri/src/lib.rs`):
 *   - `backend-upgrading`      — shows "Upgrading backend…" toast
 *   - `backend-upgraded`       — briefly shows "Backend upgraded" then hides (4s)
 *   - `backend-upgrade-failed` — shows "Upgrade failed" with the error,
 *                                 auto-dismisses after 8s
 *
 * Renders OUTSIDE the `BackendStartupOverlay` — the overlay's dismissal is
 * based solely on `/health` readiness, completely independent of whether
 * a background upgrade is in flight. This decoupling is load-bearing for
 * the daemon-startup-timeout-regression fix (v1.9.1): it guarantees that
 * a drifted-but-healthy daemon never causes the overlay to time out.
 *
 * Key exports:
 * - ``BackendUpgradeBanner`` — default export, the banner React component
 */
import { useEffect, useState } from 'react';
import { listen, UnlistenFn } from '@tauri-apps/api/event';
import { isDesktop } from '../../services/tauri';

type BannerState =
  | { kind: 'idle' }
  | { kind: 'upgrading'; from: string; to: string }
  | { kind: 'upgraded'; version: string }
  | { kind: 'failed'; error: string };

interface UpgradingPayload { from: string; to: string }
interface UpgradedPayload  { version: string }

export default function BackendUpgradeBanner() {
  const [state, setState] = useState<BannerState>({ kind: 'idle' });

  useEffect(() => {
    if (!isDesktop()) return;

    const unlisteners: UnlistenFn[] = [];
    let autoHideTimer: ReturnType<typeof setTimeout> | undefined;

    (async () => {
      unlisteners.push(
        await listen<UpgradingPayload>('backend-upgrading', (e) => {
          if (autoHideTimer) clearTimeout(autoHideTimer);
          setState({ kind: 'upgrading', from: e.payload.from, to: e.payload.to });
        }),
      );
      unlisteners.push(
        await listen<UpgradedPayload>('backend-upgraded', (e) => {
          if (autoHideTimer) clearTimeout(autoHideTimer);
          setState({ kind: 'upgraded', version: e.payload.version });
          autoHideTimer = setTimeout(() => setState({ kind: 'idle' }), 4000);
        }),
      );
      unlisteners.push(
        await listen<string>('backend-upgrade-failed', (e) => {
          if (autoHideTimer) clearTimeout(autoHideTimer);
          setState({ kind: 'failed', error: String(e.payload) });
          autoHideTimer = setTimeout(() => setState({ kind: 'idle' }), 8000);
        }),
      );
    })();

    return () => {
      unlisteners.forEach((un) => un());
      if (autoHideTimer) clearTimeout(autoHideTimer);
    };
  }, []);

  if (state.kind === 'idle') return null;

  return (
    <div
      className="fixed top-3 right-3 z-40 max-w-sm px-4 py-2 rounded-lg shadow-lg bg-[var(--color-card)] border border-[var(--color-border)] text-sm"
      role="status"
      aria-live="polite"
    >
      {state.kind === 'upgrading' && (
        <span className="text-[var(--color-text-muted)]">
          Upgrading backend ({state.from} → {state.to})…
        </span>
      )}
      {state.kind === 'upgraded' && (
        <span className="text-[var(--color-success,#22c55e)]">
          Backend upgraded to v{state.version}
        </span>
      )}
      {state.kind === 'failed' && (
        <span className="text-[var(--color-error,#ef4444)]">
          Backend upgrade failed: {state.error}
        </span>
      )}
    </div>
  );
}
