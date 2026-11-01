/**
 * About settings tab.
 *
 * Version, platform, update check, re-run setup wizard.
 */
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
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
import { isDesktop } from '../../services/tauri';
import { defaultDataDir, detectOs, isMac, joinDir } from '../../utils/platform';

const isDev = import.meta.env.DEV;

const OS_NAME = { mac: 'macOS', windows: 'Windows', linux: 'Linux' } as const;

export default function AboutTab() {
  const { t } = useTranslation();
  // The real data dir, from the backend; the OS default until it answers.
  const [dataDir, setDataDir] = useState('');
  useEffect(() => {
    systemService.getStatus().then((s) => setDataDir(s.dataDir)).catch(() => {});
  }, []);
  const [appVersion, setAppVersion] = useState('');
  const [updateState, setUpdateState] = useState<'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'error'>('idle');
  const [availableUpdate, setAvailableUpdate] = useState<Update | null>(null);
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  useEffect(() => {
    if (isDev) {
      setAppVersion('dev');
    } else if (isDesktop()) {
      getVersion().then(setAppVersion).catch(() => setAppVersion('unknown'));
    } else {
      // Hive/browser mode — read version from backend health endpoint
      fetch('/health', { signal: AbortSignal.timeout(2000) })
        .then(r => r.json())
        .then(d => setAppVersion(d.version || 'unknown'))
        .catch(() => setAppVersion('unknown'));
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
        <div className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">{t('settings.about.version')}</span>
            <span className="text-[var(--color-text)]">{appVersion || t('common.loading')}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--color-text-muted)]">{t('settings.about.platform')}</span>
            <span className="text-[var(--color-text)]">{OS_NAME[detectOs()]}</span>
          </div>

          {!isDev && isDesktop() && (
            <div className="pt-3 border-t border-[var(--color-border)]">
              {updateState === 'idle' && (
                <button
                  onClick={handleCheckForUpdates}
                  className="btn-lg btn-secondary w-full transition-colors"
                >
                  <span className="material-symbols-outlined icon-md">update</span>
                  {t('settings.about.checkForUpdates')}
                </button>
              )}
              {updateState === 'checking' && (
                <div className="flex items-center justify-center gap-2 py-2 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined icon-sm animate-spin">progress_activity</span>
                  {t('ui.AboutTab.checking')}
                </div>
              )}
              {updateState === 'available' && availableUpdate && (
                <div className="space-y-3">
                  <div className="text-green-400 flex items-center gap-2">
                    <span className="material-symbols-outlined icon-md">new_releases</span>
                    {t('ui.AboutTab.versionAvailable', { version: availableUpdate.version })}
                  </div>
                  <button
                    onClick={handleDownload}
                    className="btn-lg btn-primary w-full"
                  >
                    {t('settings.about.downloadInstall')}
                  </button>
                </div>
              )}
              {updateState === 'downloading' && updateProgress && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-[var(--color-text-muted)]">
                    <span>{t('settings.about.downloading')}</span>
                    <span>{formatBytes(updateProgress.downloaded)}</span>
                  </div>
                  <div className="h-2 bg-[var(--color-bg)] rounded-full overflow-hidden">
                    <div className="h-full bg-[var(--color-primary)]" style={{ width: `${updateProgress.percentage}%` }} />
                  </div>
                </div>
              )}
              {updateState === 'ready' && (
                <button onClick={() => restartApp()} className="btn-lg btn-primary w-full">
                  {t('settings.about.restartNow')}
                </button>
              )}
              {updateState === 'error' && (
                <div className="text-red-400 text-xs">{updateError}
                  <button onClick={handleCheckForUpdates} className="ml-2 underline">{t('common.button.retry')}</button>
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* Data location */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.about.dataTitle')}</h2>
        <div className="space-y-2 text-sm font-mono">
          <div className="flex items-start gap-2">
            <span className="material-symbols-outlined icon-xs text-[var(--color-text-muted)] mt-0.5">folder</span>
            <div>
              <p className="text-[var(--color-text)]" data-testid="about-data-dir">{dataDir ? joinDir(dataDir, '') : defaultDataDir()}</p>
              <p className="text-xs text-[var(--color-text-muted)] font-sans">
                {t('ui.AboutTab.dataDirContents')}
              </p>
            </div>
          </div>
          {/* The app bundle path is a macOS one; elsewhere the installer chose it. */}
          {isDesktop() && isMac() && (
            <div className="flex items-start gap-2">
              <span className="material-symbols-outlined icon-xs text-[var(--color-text-muted)] mt-0.5">apps</span>
              <div>
                <p className="text-[var(--color-text)] font-mono">/Applications/SwarmAI.app/</p>
                <p className="text-xs text-[var(--color-text-muted)] font-sans">
                  {t('ui.AboutTab.appBinary')}
                </p>
              </div>
            </div>
          )}
        </div>
        <div className="mt-4 flex items-start gap-2 px-3 py-2 rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)]">
          <span className="material-symbols-outlined icon-xs text-status-success mt-0.5">shield</span>
          <p className="text-xs text-[var(--color-text-muted)]">
            {t('ui.AboutTab.updatesSafe')}
          </p>
        </div>
      </section>

      {/* Re-run setup */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-2">{t('ui.AboutTab.setupTitle')}</h2>
        <p className="text-sm text-[var(--color-text-muted)] mb-4">
          {t('ui.AboutTab.setupBody')}
        </p>
        <button
          onClick={handleRerunSetup}
          className="btn-lg btn-secondary transition-colors"
        >
          {t('ui.AboutTab.rerunSetup')}
        </button>
      </section>
    </div>
  );
}
