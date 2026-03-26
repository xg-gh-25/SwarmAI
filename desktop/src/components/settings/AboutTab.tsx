/**
 * About settings tab.
 *
 * Version, platform, update check, re-run setup wizard.
 */
import { useState, useEffect } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import { systemService } from '../../services/system';
import {
  checkForUpdates,
  downloadAndInstallUpdate,
  restartApp,
  formatBytes,
  UpdateProgress,
} from '../../services/updater';
import { Update } from '@tauri-apps/plugin-updater';

const isDev = import.meta.env.DEV;

function getPlatform(): string {
  const ua = navigator.userAgent.toLowerCase();
  return ua.includes('win') ? 'Windows' : ua.includes('mac') ? 'macOS' : 'Linux';
}

export default function AboutTab() {
  const [appVersion, setAppVersion] = useState('');
  const [updateState, setUpdateState] = useState<'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error'>('idle');
  const [availableUpdate, setAvailableUpdate] = useState<Update | null>(null);
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  useEffect(() => {
    if (!isDev) {
      getVersion().then(setAppVersion).catch(() => setAppVersion('unknown'));
    } else {
      setAppVersion('dev');
    }
  }, []);

  const handleCheckForUpdates = async () => {
    setUpdateState('checking');
    setUpdateError(null);
    try {
      const update = await checkForUpdates();
      if (update) {
        setAvailableUpdate(update);
        setUpdateState('available');
      } else {
        setUpdateState('idle');
      }
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : 'Failed');
      setUpdateState('error');
    }
  };

  const handleDownload = async () => {
    if (!availableUpdate) return;
    setUpdateState('downloading');
    try {
      await downloadAndInstallUpdate(availableUpdate, setUpdateProgress);
      setUpdateState('ready');
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : 'Failed');
      setUpdateState('error');
    }
  };

  const handleRerunSetup = async () => {
    await systemService.resetOnboarding();
    window.location.reload();
  };

  return (
    <div className="space-y-6">
      {/* Version info */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">About</h2>
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Version</span>
            <span className="text-[var(--color-text)]">{appVersion || 'Loading...'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">Platform</span>
            <span className="text-[var(--color-text)]">{getPlatform()}</span>
          </div>

          {!isDev && (
            <div className="pt-3 border-t border-[var(--color-border)]">
              {updateState === 'idle' && (
                <button
                  onClick={handleCheckForUpdates}
                  className="w-full px-4 py-2 bg-[var(--color-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors flex items-center justify-center gap-2"
                >
                  <span className="material-symbols-outlined text-lg">update</span>
                  Check for Updates
                </button>
              )}
              {updateState === 'checking' && (
                <div className="flex items-center justify-center gap-2 py-2 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined animate-spin text-lg">progress_activity</span>
                  Checking...
                </div>
              )}
              {updateState === 'available' && availableUpdate && (
                <div className="space-y-3">
                  <div className="text-green-400 flex items-center gap-2">
                    <span className="material-symbols-outlined">new_releases</span>
                    Version {availableUpdate.version} available
                  </div>
                  <button
                    onClick={handleDownload}
                    className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg"
                  >
                    Download & Install
                  </button>
                </div>
              )}
              {updateState === 'downloading' && updateProgress && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-[var(--color-text-muted)]">
                    <span>Downloading...</span>
                    <span>{formatBytes(updateProgress.downloaded)}</span>
                  </div>
                  <div className="h-2 bg-[var(--color-bg)] rounded-full overflow-hidden">
                    <div className="h-full bg-[var(--color-primary)]" style={{ width: `${updateProgress.percentage}%` }} />
                  </div>
                </div>
              )}
              {updateState === 'ready' && (
                <button onClick={() => restartApp()} className="w-full px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg">
                  Restart Now
                </button>
              )}
              {updateState === 'error' && (
                <div className="text-red-400 text-xs">{updateError}
                  <button onClick={handleCheckForUpdates} className="ml-2 underline">Retry</button>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* Re-run setup */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">Setup</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          Re-run the initial setup wizard to reconfigure authentication, channels, or troubleshoot issues.
        </p>
        <button
          onClick={handleRerunSetup}
          className="px-4 py-2 bg-[var(--color-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--color-primary)] hover:text-white transition-colors text-sm"
        >
          Re-run Setup Wizard
        </button>
      </section>
    </div>
  );
}
