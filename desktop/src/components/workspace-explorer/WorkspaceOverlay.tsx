/**
 * WorkspaceOverlay — the Workspace nav overlay: browse your working directories by
 * hand (design docs/design/workspace-working-location.md v5).
 *
 * Locations are STANDALONE: SwarmWS (default, first) and each declared local repo
 * are switched by tab; each has its own header stats, tree, filter, open folders,
 * scroll and error. Only the location being looked at is fetched; a pane, once
 * shown, stays mounted (hidden) for the rest of this open so switching back is
 * instant and keeps its state. Nothing here polls; nothing reads runs or sessions.
 *
 * - SwarmWS pane: its own `ExplorerProvider` (fetch on first show, refresh, writes)
 *   around the existing `WorkspaceExplorer` (sections, write actions) in `embedded`
 *   form, under the shared `LocationHeader`.
 * - Repo pane: `RepoPane` (read-only).
 * Keys: `/` focuses the shown location's filter. (⌘1…⌘9 is not used: it switches
 * chat tabs, and the overlay must not change what that key does.)
 *
 * Exports: `WorkspaceOverlay` (default).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { ExplorerProvider, useTreeData } from '../../contexts/ExplorerContext';
import { overlayQuery, getLastWorkspaceLocation, setLastWorkspaceLocation } from '../layout/overlayQueries';
import { workspaceLocationsService, type WorkspaceLocation } from '../../services/workspace';
import WorkspaceExplorer from './WorkspaceExplorer';
import type { FileTreeItem } from './FileTreeNode';
import { RepoPane } from './RepoPane';
import { LocationHeader, PaneFooter, TreeSkeleton3 } from './workspaceParts';

const SWARMWS: WorkspaceLocation = { key: 'swarmws', label: 'SwarmWS', kind: 'default', path: '' };
export const SWARMWS_FOOT = 'Knowledge · Projects · Attachments';

function SwarmWsInner({ loc, close }: { loc: WorkspaceLocation; close: () => void }) {
  const qc = useQueryClient();
  const { treeData, isLoading, error, refreshTree } = useTreeData();
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const firstTree = useRef(true);

  // a new tree (first load, refresh, or a write's own refresh) → its stats are new too
  useEffect(() => {
    if (isLoading) return;
    setUpdatedAt(Date.now());
    if (firstTree.current) { firstTree.current = false; return; }
    void qc.invalidateQueries({ queryKey: overlayQuery.workspaceSummary(loc.key).queryKey });
  }, [treeData, isLoading, qc, loc.key]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refreshTree();        // fresh=true: rescans; the summary below reads that scan
      qc.setQueryData(overlayQuery.workspaceSummary(loc.key).queryKey,
        await workspaceLocationsService.summary(loc.key));
    } catch { /* the tree keeps its error state */ } finally {
      setRefreshing(false);
    }
  }, [refreshTree, qc, loc.key]);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="swarmws-pane">
      <LocationHeader loc={loc} onRefresh={() => void onRefresh()} refreshing={refreshing}
        refreshFailed={!!error && treeData.length > 0} />
      <div className="flex-1 min-h-0">
        <WorkspaceExplorer
          embedded
          onFileDoubleClick={(file: FileTreeItem) => {
            close();   // close first so the Canvas is not drawn under the overlay
            document.dispatchEvent(new CustomEvent('swarm:open-file', {
              detail: { path: file.path, gitStatus: file.gitStatus, workspaceId: file.workspaceId },
            }));
          }}
        />
      </div>
      <PaneFooter left={SWARMWS_FOOT} updatedAt={updatedAt} />
    </div>
  );
}

function TabGlyph({ kind, on }: { kind: WorkspaceLocation['kind']; on: boolean }) {
  return (
    <span aria-hidden="true"
      className={clsx('inline-flex items-center justify-center min-w-4 h-4 px-0.5 rounded font-mono text-meta font-semibold leading-none',
        kind === 'default' ? 'text-[var(--color-zone-work)] bg-[color-mix(in_srgb,var(--color-zone-work)_16%,transparent)]'
          : on ? 'text-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_14%,transparent)]'
            : 'text-[var(--color-text-muted)] bg-[var(--color-hover)]')}>
      {kind === 'default' ? 'WS' : '</>'}
    </span>
  );
}

export default function WorkspaceOverlay({ close }: { close: () => void }) {
  const locs = useQuery(overlayQuery.workspaceLocations());
  const list: WorkspaceLocation[] = locs.data?.length ? locs.data : [SWARMWS];
  const [wanted, setWanted] = useState(() => getLastWorkspaceLocation());
  const current = list.find((l) => l.key === wanted) ?? list[0];
  // reopening on a remembered repo: wait for the list instead of mounting (and
  // fetching) SwarmWS first; SwarmWS itself never waits
  const waiting = wanted !== SWARMWS.key && !locs.data && !locs.isError;
  const [visited, setVisited] = useState<Set<string>>(() => new Set());
  const rootRef = useRef<HTMLDivElement>(null);

  const select = useCallback((key: string) => {
    setWanted(key);
    setLastWorkspaceLocation(key);
    setVisited((v) => (v.has(key) ? v : new Set(v).add(key)));
  }, []);

  useEffect(() => {
    if (waiting) return;
    setVisited((v) => (v.has(current.key) ? v : new Set(v).add(current.key)));
  }, [current.key, waiting]);

  // a remembered repo that left the list falls back to SwarmWS (and is forgotten)
  useEffect(() => {
    if (locs.data && current.key !== wanted) select(current.key);
  }, [locs.data, current.key, wanted, select]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      if (e.key === '/' && !typing) {
        const input = rootRef.current?.querySelector<HTMLInputElement>(
          `[data-location-pane="${current.key}"] [data-workspace-filter]`);
        if (input) { e.preventDefault(); input.focus(); }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [current.key]);

  return (
    <div ref={rootRef} className="flex h-full min-h-0 flex-col" data-testid="workspace-overlay">
      <div role="tablist" aria-label="Workspace locations"
        className="flex items-end gap-1 px-3.5 pt-2.5 bg-[var(--color-bg-chrome)] border-b border-[var(--color-border)]">
        {list.map((l) => {
          const on = l.key === current.key;
          return (
            <button key={l.key} type="button" role="tab" aria-selected={on} data-testid={`workspace-tab-${l.key}`}
              title={l.label}
              onClick={() => select(l.key)}
              className={clsx('relative -mb-px flex items-center gap-1.5 h-8 px-3 rounded-t-lg text-body transition-colors',
                on ? 'border border-b-0 border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text)] font-semibold'
                  : 'border border-b-0 border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]')}>
              <TabGlyph kind={l.kind} on={on} />
              {l.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 bg-[var(--color-bg)]">
        {/* the shown location is always mounted (also when the list arrives after
            the first render and moves `current` to a remembered repo) */}
        {waiting && <TreeSkeleton3 />}
        {!waiting && list.filter((l) => visited.has(l.key) || l.key === current.key).map((l) => (
          <div key={l.key} data-location-pane={l.key} hidden={l.key !== current.key} className="h-full">
            {l.kind === 'default'
              ? <ExplorerProvider><SwarmWsInner loc={l} close={close} /></ExplorerProvider>
              : <RepoPane loc={l} active={l.key === current.key} close={close} />}
          </div>
        ))}
      </div>
    </div>
  );
}
