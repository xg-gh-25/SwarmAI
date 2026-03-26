/**
 * System settings tab.
 *
 * Backend status, system dependencies, storage paths, MCP link.
 */
import { useState, useEffect } from 'react';
import { tauriService, BackendStatus, getBackendPort } from '../../services/tauri';

const isDev = import.meta.env.DEV;

function getPlatformInfo() {
  const userAgent = navigator.userAgent.toLowerCase();
  const platform = userAgent.includes('win') ? 'Windows' :
                   userAgent.includes('mac') ? 'macOS' : 'Linux';
  return {
    platform,
    dataDir: '~/.swarm-ai/',
    skillsDir: '~/.swarm-ai/skills/',
    logsDir: '~/.swarm-ai/logs/',
  };
}

const platformInfo = getPlatformInfo();

export default function SystemTab() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus | null>(null);
  const [nodejsVersion, setNodejsVersion] = useState<string | null>(null);
  const [pythonVersion, setPythonVersion] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const loadStatus = async () => {
      const port = getBackendPort();
      try {
        const resp = await fetch(`http://localhost:${port}/health`, {
          signal: AbortSignal.timeout(2000),
        });
        setBackendStatus({ running: resp.ok, port });
      } catch {
        setBackendStatus({ running: false, port });
      }
    };
    loadStatus();
    checkDeps();
  }, []);

  const checkDeps = async () => {
    if (isDev) return;
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
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Backend Service</h2>
        {backendStatus ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Status</span>
              <span className={backendStatus.running ? 'text-green-400' : 'text-red-400'}>
                {backendStatus.running ? 'Running' : 'Stopped'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Port</span>
              <span className="text-[var(--color-text)]">{backendStatus.port}</span>
            </div>
          </div>
        ) : (
          <p className="text-[var(--color-text-muted)]">Loading...</p>
        )}
      </section>

      {/* Dependencies */}
      {!isDev && (
        <section className="bg-[var(--color-card)] rounded-lg p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-[var(--color-text)]">System Dependencies</h2>
            <button
              onClick={checkDeps}
              disabled={checking}
              className="px-3 py-1 text-xs bg-[var(--color-bg)] text-[var(--color-text-muted)] rounded hover:bg-[var(--color-primary)] hover:text-white transition-colors disabled:opacity-50"
            >
              {checking ? 'Checking...' : 'Refresh'}
            </button>
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Node.js</span>
              <span className={nodejsVersion === 'Not installed' ? 'text-red-400' : 'text-green-400'}>
                {nodejsVersion || 'Checking...'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[var(--color-text-muted)]">Python</span>
              <span className={pythonVersion === 'Not installed' ? 'text-red-400' : 'text-green-400'}>
                {pythonVersion || 'Checking...'}
              </span>
            </div>
          </div>
        </section>
      )}

      {/* Storage */}
      <section className="bg-[var(--color-card)] rounded-lg p-6">
        <h2 className="text-lg font-semibold text-[var(--color-text)] mb-4">Storage</h2>
        <div className="space-y-3 text-sm">
          {[
            ['Data Directory', platformInfo.dataDir],
            ['Database', 'data.db (SQLite)'],
            ['Logs', platformInfo.logsDir],
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
