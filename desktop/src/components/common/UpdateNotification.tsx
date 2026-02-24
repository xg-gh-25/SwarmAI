import { useState, useEffect, useCallback } from 'react';
import { Update } from '@tauri-apps/plugin-updater';
import {
  checkForUpdates,
  downloadAndInstallUpdate,
  restartApp,
  getUpdateInfo,
  formatBytes,
  UpdateProgress,
} from '../../services/updater';
import Button from './Button';
import Modal from './Modal';

type UpdateState = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error';

export default function UpdateNotification() {
  const [state, setState] = useState<UpdateState>('idle');
  const [update, setUpdate] = useState<Update | null>(null);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // Check for updates on mount
  useEffect(() => {
    const doCheck = async () => {
      setState('checking');
      try {
        const result = await checkForUpdates();
        if (result) {
          setUpdate(result);
          setState('available');
        } else {
          setState('idle');
        }
      } catch (err) {
        console.error('Update check failed:', err);
        setState('idle');
      }
    };

    // Small delay to not block initial app load
    const timer = setTimeout(doCheck, 2000);
    return () => clearTimeout(timer);
  }, []);

  const handleDownload = useCallback(async () => {
    if (!update) return;

    setState('downloading');
    setError(null);

    try {
      await downloadAndInstallUpdate(update, (p) => {
        setProgress(p);
      });
      setState('ready');
    } catch (err) {
      console.error('Download failed:', err);
      setError(err instanceof Error ? err.message : 'Download failed');
      setState('error');
    }
  }, [update]);

  const handleRestart = useCallback(async () => {
    try {
      await restartApp();
    } catch (err) {
      console.error('Restart failed:', err);
      setError(err instanceof Error ? err.message : 'Restart failed');
    }
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
  }, []);

  // Don't show if dismissed or no update
  if (dismissed || state === 'idle' || state === 'checking') {
    return null;
  }

  const updateInfo = update ? getUpdateInfo(update) : null;

  return (
    <Modal
      isOpen={!dismissed && (state === 'available' || state === 'downloading' || state === 'ready' || state === 'error')}
      onClose={handleDismiss}
      title="Update Available"
      size="md"
    >
      <div className="space-y-4">
        {/* Version info */}
        {updateInfo && (
          <div className="flex items-center gap-3">
            <span className="material-symbols-outlined text-3xl text-primary">system_update</span>
            <div>
              <p className="text-[var(--color-text)] font-medium">
                Version {updateInfo.version} is available
              </p>
              {updateInfo.date && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  Released: {new Date(updateInfo.date).toLocaleDateString()}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Release notes */}
        {updateInfo?.body && (
          <div className="bg-[var(--color-bg)] rounded-lg p-4 max-h-48 overflow-y-auto">
            <p className="text-sm text-[var(--color-text-muted)] whitespace-pre-wrap">{updateInfo.body}</p>
          </div>
        )}

        {/* Progress bar */}
        {state === 'downloading' && progress && (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-[var(--color-text-muted)]">Downloading...</span>
              <span className="text-[var(--color-text)]">
                {progress.total
                  ? `${formatBytes(progress.downloaded)} / ${formatBytes(progress.total)}`
                  : formatBytes(progress.downloaded)}
              </span>
            </div>
            <div className="h-2 bg-[var(--color-bg)] rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${progress.percentage}%` }}
              />
            </div>
          </div>
        )}

        {/* Ready message */}
        {state === 'ready' && (
          <div className="flex items-center gap-2 text-status-success">
            <span className="material-symbols-outlined">check_circle</span>
            <span>Update downloaded. Restart to apply.</span>
          </div>
        )}

        {/* Error message */}
        {state === 'error' && error && (
          <div className="flex items-center gap-2 text-status-error">
            <span className="material-symbols-outlined">error</span>
            <span>{error}</span>
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-3 pt-2">
          {state === 'available' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                Later
              </Button>
              <Button variant="primary" onClick={handleDownload} icon="download">
                Update Now
              </Button>
            </>
          )}

          {state === 'downloading' && (
            <Button variant="secondary" disabled>
              Downloading...
            </Button>
          )}

          {state === 'ready' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                Later
              </Button>
              <Button variant="primary" onClick={handleRestart} icon="restart_alt">
                Restart Now
              </Button>
            </>
          )}

          {state === 'error' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                Close
              </Button>
              <Button variant="primary" onClick={handleDownload} icon="refresh">
                Retry
              </Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
