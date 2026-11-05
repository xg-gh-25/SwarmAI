/**
 * HiveBrains — the "Brains" line of a running Hive's card in Hive Fleet
 * (design docs/design/2026-10-28-project-agent-flow.md §6).
 *
 * It answers two decisions: which Brains can I send to this Hive, and does any of
 * them need a sync first? Collapsed it is one line (how many Brains can be sent, how
 * many are out of date, one "Sync" action for those); the per-Brain rows (why one
 * cannot be sent, files the Hive changed, runs using it) are behind "Details".
 *
 * "Sync" only updates Brains the Hive already holds, plus the SwarmAI and General
 * Brains. A project Brain that was never sent stays on this machine until a task that
 * uses it is dispatched (the dispatch syncs it, core/swarm_out.py): a Brain leaves the
 * machine because a task needs it, never because a button counted it as stale.
 *
 * Data: GET /hive/instances/{id}/brains (hiveService.brainStatus). The host computes
 * every verdict and reason; from the Hive it only gets digests, times and counts.
 * The first time this session shows a Hive whose SwarmAI or General Brain is missing
 * or out of date, it syncs those two (design: "synced by default") and reloads.
 *
 * @exports HiveBrains, resetHiveBrainsAutoSync
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { hiveService, type HiveBrainRow } from '../../services/hive';
import { friendlyError } from './hiveComponents';

// Hives whose default Brains this app session already tried to sync (once each).
const autoSynced = new Set<string>();

/** Tests only: forget which Hives were auto-synced. */
export function resetHiveBrainsAutoSync(): void {
  autoSynced.clear();
}

/** On the Hive already (out of date or current), or one of the two default Brains. */
const kept = (b: HiveBrainRow) => b.isDefault || b.hiveDigest !== null;
const needsSync = (b: HiveBrainRow) => b.syncable && !b.synced && !b.localError && kept(b);
/** A valid project Brain not on this Hive: the first task that uses it brings it. */
const sentWithTask = (b: HiveBrainRow) => b.syncable && !b.synced && !b.localError && !kept(b);

function verdict(b: HiveBrainRow, t: TFunction): { text: string; tone: 'ok' | 'warn' | 'muted' } {
  if (b.dispatchable) return { text: t('ui.HiveBrains.canBeSent'), tone: 'ok' };
  if (sentWithTask(b)) return { text: t('ui.HiveBrains.sentWithTask'), tone: 'ok' };
  if (needsSync(b) && b.reasons.every((r) => r === 'not synced' || r === 'out of date')) {
    return { text: b.hiveDigest ? t('ui.HiveBrains.outOfDate') : t('ui.HiveBrains.notOnHiveYet'), tone: 'warn' };
  }
  return { text: b.localError ?? b.reasons.join(' · '), tone: 'muted' };
}

const TONE = {
  ok: 'text-[var(--color-status-success)]',
  warn: 'text-[var(--color-status-warning)]',
  muted: 'text-[var(--color-text-muted)]',
};

export function HiveBrains({ instanceId }: { instanceId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const key = ['hive', 'brains', instanceId];
  const q = useQuery({ queryKey: key, queryFn: () => hiveService.brainStatus(instanceId), staleTime: 30_000 });
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);     // brain being synced, or '*'
  const [error, setError] = useState<string | null>(null);
  const reload = () => qc.invalidateQueries({ queryKey: key });

  const data = q.data;
  useEffect(() => {
    if (!data?.supported || autoSynced.has(instanceId)) return;
    if (!data.brains.some((b) => b.isDefault && needsSync(b))) return;
    autoSynced.add(instanceId);
    setBusy('*');
    hiveService.syncDefaultBrains(instanceId)
      .catch((e) => setError(friendlyError(e)))
      .finally(() => { setBusy(null); void reload(); });
    // reload is stable enough for a once-per-Hive effect
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, instanceId]);

  const sync = async (brains: string[], label: string) => {
    setBusy(label);
    setError(null);
    try {
      for (const b of brains) await hiveService.syncBrain(instanceId, b);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
      void reload();
    }
  };

  let summary: ReactNode;
  let stale: HiveBrainRow[] = [];
  if (q.isPending) {
    summary = <span className="text-[var(--color-text-muted)]">{t('ui.HiveBrains.checking')}</span>;
  } else if (q.isError || !data) {
    summary = (
      <span className="text-[var(--color-text-muted)]" data-testid="hive-brains-error">
        {t('ui.HiveBrains.couldNotRead', { error: friendlyError(q.error) })}{' '}
        <button className="text-primary hover:underline" onClick={() => { void q.refetch(); }}>{t('common.button.retry')}</button>
      </span>
    );
  } else if (!data.supported) {
    summary = (
      <span className="text-[var(--color-text-muted)]" data-testid="hive-brains-unsupported">
        {t('ui.HiveBrains.unsupported')}
      </span>
    );
  } else {
    const ready = data.brains.filter((b) => b.dispatchable || sentWithTask(b)).length;
    stale = data.brains.filter(needsSync);
    summary = (
      <span data-testid="hive-brains-summary">
        <span className="text-[var(--color-text)] font-medium">
          {t('ui.HiveBrains.readyCount', { ready, total: data.brains.length })}
        </span>
        {stale.length > 0 && (
          <span className={TONE.warn}> · {t('ui.HiveBrains.needSync', { count: stale.length })}</span>
        )}
      </span>
    );
  }

  return (
    <div className="mt-2 text-xs" data-testid="hive-brains">
      <div className="flex items-center gap-2">
        <span className="text-[var(--color-text-muted)]">{t('ui.HiveBrains.brainsLabel')}</span>
        {summary}
        <span className="flex-1" />
        {busy === '*' && <span className="text-[var(--color-text-muted)]">{t('ui.HiveBrains.syncingDefaults')}</span>}
        {stale.length > 0 && (
          <button
            className="btn-sm btn-soft disabled:opacity-50"
            disabled={busy !== null}
            onClick={() => void sync(stale.map((b) => b.brain), 'all')}
            data-testid="hive-brains-sync-all"
          >
            {busy === 'all'
              ? t('ui.HiveBrains.syncing')
              : stale.length === 1
                ? t('ui.HiveBrains.syncOne', { brain: stale[0].brain })
                : t('ui.HiveBrains.syncMany', { count: stale.length })}
          </button>
        )}
        {data?.supported && data.brains.length > 0 && (
          <button
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            data-testid="hive-brains-toggle"
          >
            {open ? t('ui.HiveBrains.hide') : t('ui.HiveBrains.details')}
          </button>
        )}
      </div>
      {error && <p className="mt-1 text-red-400" data-testid="hive-brains-sync-error">{error}</p>}
      {open && data?.supported && (
        <ul className="mt-2 space-y-1.5 pl-1" data-testid="hive-brains-rows">
          {data.brains.map((b) => {
            const v = verdict(b, t);
            const notes = [
              b.hiveChangedFiles ? t('ui.HiveBrains.hiveChangedFiles', { count: b.hiveChangedFiles }) : null,
              b.running ? t('ui.HiveBrains.running', { count: b.running }) : null,
            ].filter(Boolean);
            return (
              <li key={b.brain} className="flex items-baseline gap-2" data-testid={`hive-brain-${b.brain}`}>
                <span
                  className="w-36 shrink-0 truncate text-[var(--color-text)]"
                  title={
                    t('ui.HiveBrains.digestTitle', {
                      local: b.localDigest?.slice(0, 8) ?? '—',
                      hive: b.hiveDigest?.slice(0, 8) ?? '—',
                    }) + (b.installedAt ? t('ui.HiveBrains.syncedAt', { time: b.installedAt }) : '')
                  }
                >
                  {b.brain}
                </span>
                <span className={TONE[v.tone]}>{v.text}</span>
                {notes.length > 0 && <span className="text-[var(--color-text-faint)]">· {notes.join(' · ')}</span>}
                <span className="flex-1" />
                {needsSync(b) && (
                  <button
                    className="text-primary hover:underline disabled:opacity-50"
                    disabled={busy !== null}
                    onClick={() => void sync([b.brain], b.brain)}
                    data-testid={`hive-brain-sync-${b.brain}`}
                  >
                    {busy === b.brain ? t('ui.HiveBrains.syncing') : t('common.button.sync')}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
