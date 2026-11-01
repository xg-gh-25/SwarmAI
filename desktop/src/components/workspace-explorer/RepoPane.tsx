/**
 * RepoPane — one repo location of the Workspace overlay, browsed read-only
 * (design workspace-working-location.md v5 §2–§4).
 *
 * Shows the repo's LOCAL MAIN checkout: first level from
 * `overlayQuery.workspaceRepoTree(key)`, each folder's children fetched the first
 * time it is opened (`workspaceLocationsService.expand`). It reads nothing of any
 * other location, run or session. State (open folders, filter, show-hidden, scroll,
 * selection) lives in this pane, which stays mounted while the overlay is open, so
 * switching away and back restores it.
 *
 * Rows: chevron, folder icon or `FileTypeIcon`, name, then `N changed` (folder) or
 * the M / U / D mark (file); hovering a file swaps those for Open and Copy path.
 * Keys: ↑ ↓ select, ← → collapse / expand, ↵ open (folder: toggle). Right-click:
 * Open in Canvas / Copy path. No write action exists here.
 *
 * Exports: `RepoPane`, `flattenRows` (pure, tested), `REPO_FOOT` (the footer text i18n key).
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { overlayQuery } from '../layout/overlayQueries';
import { OverlayEmpty, OverlayError } from '../layout/OverlayState';
import { FileTypeIcon } from '../common/FileTypeIcon';
import { copyPathNow } from '../common/CopyPathButton';
import { workspaceLocationsService, type WorkspaceLocation } from '../../services/workspace';
import type { TreeNode, GitStatus } from '../../types';
import { LocationHeader, PaneFooter, TreeSkeleton3 } from './workspaceParts';

/** i18n key of the pane footer text ("local main · read-only"). */
export const REPO_FOOT = 'ui.RepoPane.footer';

const MARK: Partial<Record<GitStatus, [string, string]>> = {
  modified: ['M', 'var(--color-git-modified)'],
  added: ['A', 'var(--color-git-added)'],
  untracked: ['U', 'var(--color-git-untracked)'],
  deleted: ['D', 'var(--color-git-deleted)'],
  renamed: ['R', 'var(--color-git-renamed)'],
  conflicting: ['C', 'var(--color-git-conflicting)'],
};

export interface Row { node: TreeNode; depth: number; open: boolean; loading: boolean }

/** The visible rows: open folders expanded with their loaded children; hidden
 *  entries dropped unless shown; with a filter, only loaded names that match and
 *  their ancestors (opened so the match is visible). */
export function flattenRows(
  top: TreeNode[], kids: Map<string, TreeNode[]>, open: Set<string>, loading: Set<string>,
  showHidden: boolean, query: string,
): Row[] {
  const q = query.trim().toLowerCase();
  const childrenOf = (n: TreeNode) => kids.get(n.path) ?? (Array.isArray(n.children) ? n.children : []);
  const matches = (n: TreeNode): boolean =>
    n.name.toLowerCase().includes(q) || (n.type === 'directory' && childrenOf(n).some(matches));
  const out: Row[] = [];
  const walk = (nodes: TreeNode[], depth: number) => {
    for (const n of nodes) {
      if (n.hidden && !showHidden) continue;
      if (q && !matches(n)) continue;
      const isOpen = n.type === 'directory' && (open.has(n.path) || (!!q && childrenOf(n).some(matches)));
      out.push({ node: n, depth, open: isOpen, loading: loading.has(n.path) });
      if (isOpen) walk(childrenOf(n), depth + 1);
    }
  };
  walk(top, 0);
  return out;
}

function openInCanvas(abs: string, gitStatus: GitStatus | undefined, close: () => void) {
  close();   // the overlay closes first so the Canvas is not drawn under it
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: abs, gitStatus } }));
}

export function RepoPane({ loc, active, close }: { loc: WorkspaceLocation; active: boolean; close: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const treeQ = useQuery(overlayQuery.workspaceRepoTree(loc.key));
  const [kids, setKids] = useState<Map<string, TreeNode[]>>(() => new Map());
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState<Set<string>>(() => new Set());
  const [showHidden, setShowHidden] = useState(false);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; node: TreeNode } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollTop = useRef(0);

  const abs = useCallback((rel: string) => `${loc.path}/${rel}`, [loc.path]);

  const load = useCallback(async (path: string) => {
    setLoading((s) => new Set(s).add(path));
    try {
      const children = await workspaceLocationsService.expand(loc.key, path);
      setKids((m) => new Map(m).set(path, children));
    } catch {
      setOpen((s) => { const n = new Set(s); n.delete(path); return n; });
    } finally {
      setLoading((s) => { const n = new Set(s); n.delete(path); return n; });
    }
  }, [loc.key]);

  const toggle = useCallback((n: TreeNode) => {
    if (n.type !== 'directory') return;
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(n.path)) next.delete(n.path); else next.add(n.path);
      return next;
    });
    if (!open.has(n.path) && !kids.has(n.path) && !Array.isArray(n.children) && !n.isSymlink) void load(n.path);
  }, [open, kids, load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setRefreshFailed(false);
    try {
      // the tree's fresh=true rescans; the summary then reads that same new scan
      const tree = await workspaceLocationsService.tree(loc.key, 1, true);
      const summary = await workspaceLocationsService.summary(loc.key);
      qc.setQueryData(overlayQuery.workspaceRepoTree(loc.key).queryKey, tree);
      qc.setQueryData(overlayQuery.workspaceSummary(loc.key).queryKey, summary);
      const reopen = [...open].filter((p) => kids.has(p));
      setKids(new Map());
      await Promise.all(reopen.map(load));
    } catch {
      setRefreshFailed(true);
    } finally {
      setRefreshing(false);
    }
  }, [loc.key, qc, open, kids, load]);

  const rows = useMemo(
    () => flattenRows(treeQ.data ?? [], kids, open, loading, showHidden, query),
    [treeQ.data, kids, open, loading, showHidden, query],
  );

  // keep the scroll position across a switch away and back (a hidden pane may lose it)
  useLayoutEffect(() => {
    if (active && scrollRef.current) scrollRef.current.scrollTop = scrollTop.current;
  }, [active]);
  useEffect(() => {
    if (!menu) return;
    const off = () => setMenu(null);
    window.addEventListener('mousedown', off);
    return () => window.removeEventListener('mousedown', off);
  }, [menu]);

  const onKey = (e: React.KeyboardEvent) => {
    if (!rows.length) return;
    const i = Math.max(0, rows.findIndex((r) => r.node.path === selected));
    const row = rows[i];
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const j = Math.min(rows.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)));
      setSelected(rows[j].node.path);
      scrollRef.current?.querySelector(`[data-row-index="${j}"]`)?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowRight' && row.node.type === 'directory' && !row.open) {
      e.preventDefault(); toggle(row.node);
    } else if (e.key === 'ArrowLeft' && row.open) {
      e.preventDefault(); toggle(row.node);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (row.node.type === 'directory') toggle(row.node);
      else openInCanvas(abs(row.node.path), row.node.gitStatus, close);
    }
  };

  const header = (
    <LocationHeader loc={loc} onRefresh={() => void refresh()} refreshing={refreshing} refreshFailed={refreshFailed} />
  );
  const toolbar = (
    <div className="flex items-center gap-1 px-3.5 pb-2.5">
      <div className="relative flex flex-1 items-center">
        <span aria-hidden="true" className="material-symbols-outlined icon-xs absolute left-2 text-[var(--color-text-dim)] pointer-events-none">search</span>
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder={t('ui.RepoPane.filterLoaded')} aria-label={t('ui.RepoPane.filterLabel', { label: loc.label })}
          data-workspace-filter="" data-testid="repo-filter"
          className="w-full h-7 pl-7 pr-2 text-small rounded-md bg-[var(--color-bg-chrome)] border border-[var(--color-border)] text-[var(--color-text)] placeholder-[var(--color-text-dim)] focus:outline-none focus:border-[var(--color-primary)] [&::-webkit-search-cancel-button]:hidden" />
      </div>
      <button type="button" onClick={() => setShowHidden((v) => !v)} data-testid="repo-show-hidden"
        title={showHidden ? t('ui.RepoPane.hideHidden') : t('ui.RepoPane.showHidden')} aria-pressed={showHidden}
        className={clsx('icon-btn hover:bg-[var(--color-hover)]', showHidden ? 'text-[var(--color-primary)]' : 'text-[var(--color-text-muted)]')}>
        <span className="material-symbols-outlined icon-sm">{showHidden ? 'visibility' : 'visibility_off'}</span>
      </button>
      <button type="button" onClick={() => setOpen(new Set())} title={t('ui.RepoPane.collapseAll')} data-testid="repo-collapse-all"
        className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]">
        <span className="material-symbols-outlined icon-sm">unfold_less</span>
      </button>
    </div>
  );

  let body;
  if (!treeQ.data && treeQ.isError) {
    body = <OverlayError message={t('ui.RepoPane.loadError', { label: loc.label })} onRetry={() => void treeQ.refetch()}
      testid="repo-load-error" retryTestid="repo-retry" />;
  } else if (!treeQ.data) {
    body = <TreeSkeleton3 />;
  } else if (rows.length === 0) {
    body = query
      ? <OverlayEmpty testid="repo-no-match">{t('ui.RepoPane.noMatch', { query })}</OverlayEmpty>
      : <OverlayEmpty testid="repo-empty">{t('ui.RepoPane.emptyFolder')}</OverlayEmpty>;
  } else {
    body = (
      <div role="tree" aria-label={loc.label} tabIndex={0} onKeyDown={onKey} className="py-1 outline-none">
        {rows.map((r, i) => {
          const n = r.node;
          const dir = n.type === 'directory';
          const mark = n.gitStatus ? MARK[n.gitStatus] : undefined;
          return (
            <div key={n.path} role="treeitem" aria-expanded={dir ? r.open : undefined} aria-selected={selected === n.path}
              data-row-index={i} data-testid={`repo-row-${n.path}`}
              onClick={() => { setSelected(n.path); if (dir) toggle(n); }}
              onDoubleClick={() => { if (!dir) openInCanvas(abs(n.path), n.gitStatus, close); }}
              onContextMenu={(e) => { e.preventDefault(); setSelected(n.path); setMenu({ x: e.clientX, y: e.clientY, node: n }); }}
              style={{ paddingLeft: 10 + r.depth * 16 }}
              className={clsx('group relative flex items-center gap-1.5 h-7 pr-2.5 border-l-[3px] cursor-default text-body',
                selected === n.path
                  ? 'border-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_9%,transparent)]'
                  : 'border-transparent hover:bg-[var(--color-hover)]')}>
              <span aria-hidden="true" className={clsx('material-symbols-outlined icon-xs w-3.5 text-[var(--color-text-dim)] transition-transform duration-[120ms] motion-reduce:transition-none',
                r.open && 'rotate-90', !dir && 'invisible')}>chevron_right</span>
              <span className="file-icon-slot">
                {dir
                  ? <span className="material-symbols-outlined icon-sm text-[var(--color-icon-folder)]">{n.isSymlink ? 'folder_special' : r.open ? 'folder_open' : 'folder'}</span>
                  : <FileTypeIcon name={n.name} />}
              </span>
              <span className={clsx('flex-1 truncate', n.hidden ? 'text-[var(--color-text-dim)]' : 'text-[var(--color-text)]')}>{n.name}</span>
              {r.loading && <span className="material-symbols-outlined icon-xs animate-spin motion-reduce:animate-none text-[var(--color-text-dim)]">progress_activity</span>}
              <span className={clsx('flex items-center', !dir && 'group-hover:hidden')}>
                {dir && n.changedCount ? <span className="text-meta tabular-nums text-[var(--color-text-muted)]" data-testid="repo-changed-count">{t('ui.RepoPane.changedCount', { count: n.changedCount })}</span> : null}
                {!dir && mark ? <span className="font-mono text-meta font-semibold w-3.5 text-center" style={{ color: mark[1] }}>{mark[0]}</span> : null}
              </span>
              {!dir && (
                <span className="hidden group-hover:flex items-center gap-1">
                  <button type="button" className="btn-sm btn-secondary" data-testid="repo-row-open"
                    onClick={(e) => { e.stopPropagation(); openInCanvas(abs(n.path), n.gitStatus, close); }}>{t('ui.RepoPane.open')}</button>
                  <button type="button" className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]" title={t('ui.RepoPane.copyPath')}
                    onClick={(e) => { e.stopPropagation(); void copyPathNow(abs(n.path)); }}>
                    <span className="material-symbols-outlined icon-sm">content_copy</span>
                  </button>
                </span>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid={`repo-pane-${loc.key}`}>
      {header}
      {toolbar}
      <div ref={scrollRef} onScroll={(e) => { scrollTop.current = e.currentTarget.scrollTop; }}
        className="flex-1 min-h-0 overflow-auto border-t border-[var(--color-border)]">{body}</div>
      <PaneFooter left={t(REPO_FOOT)} updatedAt={treeQ.dataUpdatedAt || null} />
      {menu && (
        <div role="menu" style={{ left: menu.x, top: menu.y }} onMouseDown={(e) => e.stopPropagation()}
          className="fixed z-50 min-w-[160px] py-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-lg text-small">
          {menu.node.type === 'file' && (
            <button type="button" role="menuitem" className="w-full text-left px-3 py-1.5 hover:bg-[var(--color-hover)]"
              onClick={() => { const n = menu.node; setMenu(null); openInCanvas(abs(n.path), n.gitStatus, close); }}>{t('ui.RepoPane.openInCanvas')}</button>
          )}
          <button type="button" role="menuitem" className="w-full text-left px-3 py-1.5 hover:bg-[var(--color-hover)]"
            onClick={() => { const n = menu.node; setMenu(null); void copyPathNow(abs(n.path)); }}>{t('ui.RepoPane.copyPath')}</button>
        </div>
      )}
    </div>
  );
}
