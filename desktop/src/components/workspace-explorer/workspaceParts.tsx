/**
 * Shared parts of the Workspace overlay (design workspace-working-location.md v5
 * §2.1): each location's header, footer and first-load skeleton.
 *
 * - `agoText(ms)`        "just now" / "3m ago" / "3h ago" / "5d ago"
 * - `LocationHeader`     name, full path, the location's OWN stats (local main,
 *                        N changed, last commit), copy-path and refresh
 * - `PaneFooter`         left text + "updated <ago>"
 * - `TreeSkeleton3`      three grey lines (no spinner)
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { overlayQuery } from '../layout/overlayQueries';
import { CopyPathButton } from '../common/CopyPathButton';
import type { WorkspaceLocation } from '../../services/workspace';

export function agoText(ms: number | null | undefined, now = Date.now()): string {
  if (!ms) return '';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Re-render once a minute so "x ago" stays true while the overlay is open. */
function useMinuteTick(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function Chip({ children, dot, testid }: { children: ReactNode; dot?: string; testid?: string }) {
  return (
    <span data-testid={testid}
      className="inline-flex items-center gap-1.5 h-[18px] px-2 rounded-full border border-[var(--color-border)] text-meta text-[var(--color-text-muted)]">
      {dot && <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full" style={{ background: dot }} />}
      {children}
    </span>
  );
}

export function LocationHeader({ loc, onRefresh, refreshing, refreshFailed }: {
  loc: WorkspaceLocation;
  onRefresh: () => void;
  refreshing: boolean;
  refreshFailed: boolean;
}) {
  const { t } = useTranslation();
  const summary = useQuery(overlayQuery.workspaceSummary(loc.key));
  const now = useMinuteTick();
  const s = summary.data;
  const lastCommit = s?.lastCommitAt ? agoText(s.lastCommitAt * 1000, now) : '';
  return (
    <div className="flex items-start gap-2.5 px-3.5 pt-3 pb-2.5" data-testid="workspace-location-header">
      <div className="min-w-0 flex-1">
        <div className="text-heading font-semibold text-[var(--color-text)]">{loc.label}</div>
        <div title={loc.path} data-testid="workspace-location-path"
          className="mt-px truncate font-mono text-meta text-[var(--color-text-dim)]">{loc.path}</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Chip>{t('ui.workspaceParts.localMain')}</Chip>
          {s && s.changed > 0 && (
            <Chip dot="var(--color-git-modified)" testid="workspace-changed">{t('ui.workspaceParts.changed', { count: s.changed })}</Chip>
          )}
          {lastCommit && <Chip testid="workspace-last-commit">{t('ui.workspaceParts.lastCommit', { ago: lastCommit })}</Chip>}
        </div>
      </div>
      <div className="flex items-center gap-1">
        {refreshFailed && (
          <span title={t('ui.workspaceParts.refreshFailed')} data-testid="workspace-refresh-failed"
            className="material-symbols-outlined icon-sm text-[var(--color-warning)]">warning</span>
        )}
        {loc.path && <CopyPathButton path={loc.path} testId="workspace-copy-root" />}
        <button type="button" onClick={onRefresh} title={t('ui.workspaceParts.refreshLabel', { label: loc.label })} aria-label={t('ui.workspaceParts.refreshLabel', { label: loc.label })}
          data-testid="workspace-refresh"
          className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]">
          <span className={`material-symbols-outlined icon-sm ${refreshing ? 'animate-spin motion-reduce:animate-none' : ''}`}>refresh</span>
        </button>
      </div>
    </div>
  );
}

export function PaneFooter({ left, updatedAt }: { left: string; updatedAt: number | null }) {
  const { t } = useTranslation();
  const now = useMinuteTick();
  return (
    <div className="flex items-center justify-between px-3.5 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-bg-chrome)] text-meta text-[var(--color-text-muted)]">
      <span className="truncate">{left}</span>
      {updatedAt ? <span>{t('ui.workspaceParts.updated', { ago: agoText(updatedAt, now) })}</span> : null}
    </div>
  );
}

export function TreeSkeleton3() {
  return (
    <div className="px-3.5 py-3 space-y-3" data-testid="workspace-skeleton">
      {['60%', '45%', '52%'].map((w) => (
        <div key={w} className="h-2.5 rounded bg-[var(--color-hover)]" style={{ width: w }} />
      ))}
    </div>
  );
}
