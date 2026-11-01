/**
 * System settings tab.
 *
 * Backend status, system dependencies, storage paths, MCP link.
 */
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
// getApiBaseUrl: health fetch URL; getBackendPort: status display (Desktop only)
import { tauriService, BackendStatus, getApiBaseUrl, getBackendPort, isDesktop } from '../../services/tauri';
import { systemService } from '../../services/system';
import { defaultDataDir, joinDir } from '../../utils/platform';

const isDev = import.meta.env.DEV;

export default function SystemTab() {
  const { t } = useTranslation();
  // The real data dir, from the backend; the OS default until it answers.
  const [dataDir, setDataDir] = useState('');
  useEffect(() => {
    systemService.getStatus().then((s) => setDataDir(s.dataDir)).catch(() => {});
  }, []);
  const shownDataDir = dataDir ? joinDir(dataDir, '') : defaultDataDir();
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [nodejsVersion, setNodejsVersion] = useState<string | null>(null);
  const [pythonVersion, setPythonVersion] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const loadStatus = async () => {
      try {
        // Prefer Tauri state — includes is_daemon_mode from lib.rs
        const status = await tauriService.getBackendStatus();
        setBackendStatus(status);
      } catch {
        // Fallback: health check only (dev mode, Hive mode, or Tauri unavailable)
        const apiBase = getApiBaseUrl();
        const port = getBackendPort();
        try {
          const resp = await fetch(`${apiBase}/health`, {
            signal: AbortSignal.timeout(2000),
          });
          if (resp.ok) {
            const data = await resp.json();
            const mode = data.mode || 'daemon';
            setBackendStatus({ running: true, port, is_daemon_mode: mode === 'daemon' || mode === 'hive' });
          } else {
            setBackendStatus({ running: false, port, is_daemon_mode: false });
          }
        } catch {
          setBackendStatus({ running: false, port, is_daemon_mode: false });
        }
      }
    };
    loadStatus();
    checkDeps();
  }, []);

  const checkDeps = async () => {
    // Dep checks use Tauri invoke — only available in desktop mode
    if (isDev || !isDesktop()) return;
    setChecking(true);
    try {
      setNodejsVersion(await tauriService.checkNodejsVersion());
    } catch { setNodejsVersion('Not installed'); }
    try {
      setPythonVersion(await tauriService.checkPythonVersion());
    } catch { setPythonVersion('Not installed'); }
    setChecking(false);
  };

  return (
    <div className="space-y-6">
      {/* Backend */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.backendService.title')}</h2>
        {backendStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">{t('settings.backendService.status')}</span>
              <span className={backendStatus.running ? 'text-green-400' : 'text-red-400'}>
                {backendStatus.running ? t('settings.backendService.running') : t('settings.backendService.stopped')}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">{t('settings.backendService.port')}</span>
              <span className="text-[var(--color-text)]">{backendStatus.port}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">{t('ui.SystemTab.mode')}</span>
              <span className={backendStatus.is_daemon_mode ? 'text-green-400' : 'text-[var(--color-text)]'}>
                {backendStatus.is_daemon_mode ? t('ui.SystemTab.daemon') : t('ui.SystemTab.subprocess')}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-[var(--color-text-muted)]">{t('common.status.loading')}</p>
        )}
      </section>

      {/* Dependencies — Desktop only (uses Tauri invoke) */}
      {!isDev && isDesktop() && (
        <section className="bg-[var(--color-card)] rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-[var(--color-text)]">{t('settings.systemDependencies.title')}</h2>
            <button
              onClick={checkDeps}
              disabled={checking}
              className="btn-sm btn-secondary transition-colors disabled:opacity-50"
            >
              {checking ? t('settings.systemDependencies.checking') : t('common.button.refresh')}
            </button>
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Node.js</span>
              <span className={nodejsVersion === 'Not installed' ? 'text-red-400' : 'text-green-400'}>
                {nodejsVersion || t('settings.systemDependencies.checking')}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Python</span>
              <span className={pythonVersion === 'Not installed' ? 'text-red-400' : 'text-green-400'}>
                {pythonVersion || t('settings.systemDependencies.checking')}
              </span>
            </div>
          </div>
        </section>
      )}

      {/* Storage */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">{t('settings.storage.title')}</h2>
        <div className="space-y-3 text-sm" data-testid="system-storage">
          {[
            [t('settings.storage.dataDirectory'), shownDataDir],
            [t('settings.storage.database'), 'data.db (SQLite)'],
            [t('settings.storage.logs'), joinDir(shownDataDir, 'logs')],
          ].map(([label, value]) => (
            <div key={label} className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">{label}</span>
              <span className="text-[var(--color-text)] font-mono text-xs">{value}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
