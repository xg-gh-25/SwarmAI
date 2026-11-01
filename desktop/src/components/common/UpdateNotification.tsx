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
import { useTranslation } from 'react-i18next';

type UpdateState = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error';

export default function UpdateNotification() {
  const { t } = useTranslation();
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
        console.warn('[Updater] Update check failed (expected in dev builds):', err);
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
      title={t('ui.UpdateNotification.title')}
      size="md"
    >
      <div className="space-y-4">
        {/* Version info */}
        {updateInfo && (
          <div className="flex items-center gap-3">
            <span className="material-symbols-outlined icon-lg text-primary">system_update</span>
            <div>
              <p className="text-[var(--color-text)] font-medium">
                {t('ui.UpdateNotification.versionAvailable', { version: updateInfo.version })}
              </p>
              {updateInfo.date && (
                <p className="text-sm text-[var(--color-text-muted)]">
                  {t('ui.UpdateNotification.released', { date: new Date(updateInfo.date).toLocaleDateString() })}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Data safety assurance */}
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)]">
          <span className="material-symbols-outlined icon-xs text-status-success mt-0.5">shield</span>
          <p className="text-xs text-[var(--color-text-muted)]">
            {t('ui.UpdateNotification.dataSafe')}
          </p>
        </div>

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
              <span className="text-[var(--color-text-muted)]">{t('ui.UpdateNotification.downloading')}</span>
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
            <span className="material-symbols-outlined icon-md">check_circle</span>
            <span>{t('ui.UpdateNotification.downloaded')}</span>
          </div>
        )}

        {/* Error message */}
        {state === 'error' && error && (
          <div className="flex items-center gap-2 text-status-error">
            <span className="material-symbols-outlined icon-md">error</span>
            <span>{error}</span>
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-3 pt-2">
          {state === 'available' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                {t('ui.UpdateNotification.later')}
              </Button>
              <Button variant="primary" onClick={handleDownload} icon="download">
                {t('ui.UpdateNotification.updateNow')}
              </Button>
            </>
          )}

          {state === 'downloading' && (
            <Button variant="secondary" disabled>
              {t('ui.UpdateNotification.downloading')}
            </Button>
          )}

          {state === 'ready' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                {t('ui.UpdateNotification.later')}
              </Button>
              <Button variant="primary" onClick={handleRestart} icon="restart_alt">
                {t('ui.UpdateNotification.restartNow')}
              </Button>
            </>
          )}

          {state === 'error' && (
            <>
              <Button variant="ghost" onClick={handleDismiss}>
                {t('common.button.close')}
              </Button>
              <Button variant="primary" onClick={handleDownload} icon="refresh">
                {t('common.button.retry')}
              </Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
