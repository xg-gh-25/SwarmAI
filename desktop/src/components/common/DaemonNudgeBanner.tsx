/**
 * Daemon mode onboarding nudge banner.
 *
 * Shown once after backend starts in sidecar mode on macOS.
 * Guides the user to enable 24/7 daemon mode for always-on channels
 * and background jobs. Dismissable with 30-day cooldown (3 strikes
 * = permanent dismiss).
 */
import { useState, useEffect, useCallback } from 'react';
import { tauriService } from '../../services/tauri';
import { getApiBaseUrl } from '../../services/tauri';

const STORAGE_KEYS = {
  dismissed: 'daemon-nudge-dismissed',
  dismissCount: 'daemon-nudge-dismiss-count',
  forever: 'daemon-nudge-forever-dismissed',
} as const;

const COOLDOWN_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
const MAX_DISMISSALS = 3;

function shouldShow(): boolean {
  if (localStorage.getItem(STORAGE_KEYS.forever)) return false;

  const lastDismissed = localStorage.getItem(STORAGE_KEYS.dismissed);
  if (lastDismissed) {
    const elapsed = Date.now() - parseInt(lastDismissed, 10);
    if (elapsed < COOLDOWN_MS) return false;
  }

  return true;
}

export default function DaemonNudgeBanner() {
  const [visible, setVisible] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!shouldShow()) return;

    let unlisten: (() => void) | undefined;

    const setup = async () => {
      try {
        unlisten = await tauriService.onBackendMode((mode) => {
          if (mode === 'sidecar') {
            setVisible(true);
          }
        });
      } catch {
        // Not in Tauri env (dev mode) — don't show
      }
    };
    setup();

    return () => { unlisten?.(); };
  }, []);

  const handleDismiss = useCallback(() => {
    const count = parseInt(localStorage.getItem(STORAGE_KEYS.dismissCount) || '0', 10) + 1;
    localStorage.setItem(STORAGE_KEYS.dismissCount, String(count));

    if (count >= MAX_DISMISSALS) {
      localStorage.setItem(STORAGE_KEYS.forever, '1');
    } else {
      localStorage.setItem(STORAGE_KEYS.dismissed, String(Date.now()));
    }

    setVisible(false);
  }, []);

  const handleInstall = useCallback(async () => {
    setInstalling(true);
    setError('');
    try {
      const apiBase = getApiBaseUrl();
      const resp = await fetch(`${apiBase}/api/system/install-daemon`, {
        method: 'POST',
        signal: AbortSignal.timeout(30000),
      });
      if (resp.ok) {
        setInstalled(true);
        // Auto-dismiss after 3 seconds
        setTimeout(() => setVisible(false), 3000);
      } else {
        const data = await resp.json().catch(() => ({ detail: 'Unknown error' }));
        setError(data.detail || 'Installation failed');
      }
    } catch (e) {
      setError(`Failed to install: ${e}`);
    } finally {
      setInstalling(false);
    }
  }, []);

  if (!visible) return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 max-w-sm animate-fade-in">
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg p-4">
        {installed ? (
          <div className="flex items-center gap-2 text-green-400">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
              <circle cx="10" cy="10" r="10" fill="currentColor" opacity="0.2" />
              <path d="M6 10l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="text-sm font-medium">Daemon installed! Restart the app to activate 24/7 mode.</span>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1">
                <p className="text-sm font-medium text-[var(--color-text)]">
                  Running in sidecar mode
                </p>
                <p className="text-xs text-[var(--color-text-muted)] mt-1">
                  Channels (Slack) and background jobs stop when you close the app.
                  Enable daemon mode for 24/7 operation.
                </p>
              </div>
              <button
                onClick={handleDismiss}
                className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors shrink-0"
                aria-label="Dismiss"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            {error && (
              <p className="text-xs text-red-400 mt-2">{error}</p>
            )}
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleInstall}
                disabled={installing}
                className="px-3 py-1.5 text-xs font-medium bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)] text-white rounded transition-colors disabled:opacity-50"
              >
                {installing ? 'Installing...' : 'Enable Daemon Mode'}
              </button>
              <button
                onClick={handleDismiss}
                className="px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              >
                Maybe Later
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
