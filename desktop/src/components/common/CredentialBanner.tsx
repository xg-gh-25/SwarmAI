/**
 * Global banner that surfaces expired AWS credentials.
 *
 * Unlike {@link BackendUpgradeBanner} (Tauri-event driven), this is
 * HEALTH-POLL driven: it reads `health.auth` from {@link useHealth} (set by
 * the 30s `/health` poll in `useHealthMonitor`). It renders ONLY when
 * `auth === 'expired'` — a definitive credential-expiry signal from the
 * backend's STS pre-flight. `valid` / `unknown` / undefined → renders nothing
 * (no banner on transient/network/startup ambiguity — fail-open by design).
 *
 * Why it exists: when isengard/ADA tokens expire, the main inference path used
 * to stall ("spinner spins forever") with no visible cause. The backend now
 * fails fast AND reports auth=expired here so the user sees an actionable
 * `mwinit -f` instruction instead of an opaque hang.
 *
 * Key exports:
 * - ``CredentialBanner`` — default export, the banner React component
 */
import { useHealth } from '../../contexts/HealthContext';

export default function CredentialBanner() {
  const { health } = useHealth();

  // Render ONLY on a definitive expired signal. valid/unknown/undefined → nothing.
  if (health.auth !== 'expired') return null;

  return (
    <div
      className="fixed top-3 left-1/2 -translate-x-1/2 z-50 max-w-md px-4 py-2.5 rounded-lg shadow-lg bg-[var(--color-card)] border border-[var(--color-error,#ef4444)] text-sm"
      role="alert"
      aria-live="assertive"
    >
      <div className="flex items-start gap-2">
        <span className="text-[var(--color-error,#ef4444)] font-medium shrink-0">
          AWS credentials expired
        </span>
        <span className="text-[var(--color-text-muted)]">
          Run{' '}
          <code className="px-1 py-0.5 rounded bg-[var(--color-bg)] text-[var(--color-text)] font-mono">
            mwinit -f
          </code>{' '}
          in a terminal to refresh, then retry your message.
        </span>
      </div>
    </div>
  );
}
