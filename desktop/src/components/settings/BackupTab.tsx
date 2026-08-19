/**
 * Backup & Sync settings tab.
 *
 * Shows backup status (last backup, repo URL, schedule) and provides
 * manual trigger + configuration. Uses the same patterns as SystemTab.
 */
import { useState, useEffect, useCallback } from 'react';
import { systemService, backupToastFor, BackupStatus, RestoreEvent } from '../../services/system';
import { useToast } from '../../contexts/ToastContext';

export default function BackupTab() {
  const { addToast } = useToast();
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [backing, setBacking] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState('');
  const [token, setToken] = useState('');
  const [restoring, setRestoring] = useState(false);
  const [restoreEvents, setRestoreEvents] = useState<RestoreEvent[]>([]);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await systemService.getBackupStatus();
      setStatus(s);
      if (s.repoUrl) setRepoUrl(s.repoUrl);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const handleBackup = async () => {
    setBacking(true);
    try {
      const result = await systemService.runBackup();
      addToast(backupToastFor(result));
      await fetchStatus();
    } catch (e) {
      addToast({ severity: 'error', message: `Backup failed: ${e instanceof Error ? e.message : 'Unknown error'}` });
    } finally {
      setBacking(false);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await systemService.updateBackupConfig({
        repoUrl: repoUrl || undefined,
        token: token || undefined,
      });
      addToast({ severity: 'success', message: 'Backup configuration saved.' });
      setToken('');
      setConfigOpen(false);
      await fetchStatus();
    } catch {
      addToast({ severity: 'error', message: 'Failed to save config.' });
    }
  };

  const formatTime = (iso: string | null) => {
    if (!iso) return 'Never';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  if (loading) {
    return <div className="text-sm text-[var(--color-text-muted)]">Loading backup status...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Status card */}
      <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]">Workspace Backup</h3>
          <span className={`text-xs px-2 py-0.5 rounded-full ${
            status?.enabled ? 'bg-green-500/10 text-green-500' : 'bg-gray-500/10 text-gray-400'
          }`}>
            {status?.enabled ? 'Active' : 'Disabled'}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-[var(--color-text-muted)]">Last backup</span>
            <p className="text-[var(--color-text)] font-medium">{formatTime(status?.lastBackup ?? null)}</p>
          </div>
          <div>
            <span className="text-[var(--color-text-muted)]">Schedule</span>
            <p className="text-[var(--color-text)] font-medium">{status?.schedule === 'daily_3am' ? 'Daily 3:00 AM' : status?.schedule ?? '—'}</p>
          </div>
          <div className="col-span-2">
            <span className="text-[var(--color-text-muted)]">Repository</span>
            <p className="text-[var(--color-text)] font-medium truncate">{status?.repoUrl || 'Not configured'}</p>
          </div>
        </div>

        <div className="flex gap-2 mt-4">
          <button
            onClick={handleBackup}
            disabled={backing}
            className="px-3 py-1.5 text-sm font-medium rounded-md bg-[var(--color-primary)] text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {backing ? 'Backing up...' : 'Backup Now'}
          </button>
          <button
            onClick={() => setConfigOpen(!configOpen)}
            className="px-3 py-1.5 text-sm font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            Configure
          </button>
        </div>
      </div>

      {/* Config panel (collapsible) */}
      {configOpen && (
        <div className="p-4 rounded-lg border border-[var(--color-border)] space-y-3">
          <h4 className="text-sm font-semibold text-[var(--color-text)]">Backup Configuration</h4>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">GitHub Repository URL</label>
            <input
              type="text"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo.git"
              className="w-full px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
            />
          </div>

          <div>
            <label className="block text-xs text-[var(--color-text-muted)] mb-1">GitHub Token (stored in Keychain)</label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="ghp_..."
              className="w-full px-3 py-1.5 text-sm rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
            />
            <p className="text-xs text-[var(--color-text-muted)] mt-1">
              Token is stored in macOS Keychain, never in plaintext files.
            </p>
          </div>

          <button
            onClick={handleSaveConfig}
            className="px-3 py-1.5 text-sm font-medium rounded-md bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
          >
            Save Configuration
          </button>
        </div>
      )}

      {/* Restore section */}
      <div className="p-4 rounded-lg border border-[var(--color-border)] space-y-3">
        <h4 className="text-sm font-semibold text-[var(--color-text)]">Restore from Backup</h4>
        <p className="text-xs text-[var(--color-text-muted)]">
          Clone a backup repository and restore your workspace, config, and database.
          Only works on a fresh install (no existing data).
        </p>

        {!restoring && (
          <button
            onClick={async () => {
              if (!repoUrl) {
                addToast({ severity: 'warning', message: 'Enter a repo URL first (in Configure above).' });
                return;
              }
              setRestoring(true);
              setRestoreEvents([]);
              try {
                for await (const event of systemService.restoreBackup(repoUrl, token || undefined)) {
                  setRestoreEvents(prev => [...prev, event]);
                  if (event.error) {
                    addToast({ severity: 'error', message: event.error });
                    break;
                  }
                }
              } catch (e) {
                addToast({ severity: 'error', message: `Restore failed: ${e instanceof Error ? e.message : 'Unknown'}` });
              } finally {
                setRestoring(false);
                await fetchStatus();
              }
            }}
            className="px-3 py-1.5 text-sm font-medium rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            {restoreEvents.length > 0 ? 'Retry Restore' : 'Restore from Backup'}
          </button>
        )}

        {(restoring || restoreEvents.length > 0) && (
          <div className="space-y-2">
            {restoreEvents.map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full ${e.error ? 'bg-red-500' : e.progress === 100 ? 'bg-green-500' : 'bg-blue-500 animate-pulse'}`} />
                <span className="text-[var(--color-text-muted)]">{e.stage}</span>
                <span className="text-[var(--color-text)]">{e.detail || e.error || ''}</span>
              </div>
            ))}
            {restoring && (
              <div className="w-full bg-[var(--color-bg)] rounded-full h-1.5">
                <div
                  className="bg-[var(--color-primary)] h-1.5 rounded-full transition-all duration-300"
                  style={{ width: `${restoreEvents[restoreEvents.length - 1]?.progress ?? 0}%` }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Info */}
      <p className="text-xs text-[var(--color-text-muted)]">
        Workspace backup exports your memory, knowledge, projects, config, and conversation history
        to a private GitHub repository. L3 data (embeddings, search indexes) is rebuilt on restore.
      </p>
    </div>
  );
}
