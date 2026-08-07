/**
 * ExplorerHeader — Static header for the SwarmWS Workspace Explorer.
 *
 * Layout:
 * - Title row: Layers icon + "SwarmWS" label, plus quick actions (collapse-all,
 *   expand-all, sort toggle, open-file, refresh).
 * - Search row: an inline filter box wired to the ExplorerContext search engine
 *   (setSearchQuery → findMatches → matchedPaths, consumed by VirtualizedTree).
 *
 * The search engine + collapseAll/expandAll/sortMode all live in ExplorerContext;
 * this header is the UI that drives them (run_36f2823c wired the previously-headless
 * engine — the old "no search bar" state, noted in this file's history, is reversed).
 *
 * Sort: cycles default → name-asc → name-desc → git-first → default. 'default'
 * keeps the built-in ordering (dirs-first + date-desc for Knowledge/Attachments).
 * There is no mtime/size sort — TreeNode carries no timestamp field.
 */

import { useTreeData, useSearch, useSelection, type SortMode } from '../../contexts/ExplorerContext';
import { OpenFileButton } from './OpenFileButton';

/** Sort cycle order + the icon/label shown for each mode. */
const SORT_CYCLE: SortMode[] = ['default', 'name-asc', 'name-desc', 'git-first'];
const SORT_META: Record<SortMode, { icon: string; label: string }> = {
  'default': { icon: 'sort', label: 'Sort: default (newest-first in Knowledge)' },
  'name-asc': { icon: 'sort_by_alpha', label: 'Sort: name A→Z' },
  'name-desc': { icon: 'sort_by_alpha', label: 'Sort: name Z→A' },
  'git-first': { icon: 'commit', label: 'Sort: changed files first' },
};

/** Small icon button matching the existing header action style. */
function HeaderIconButton({
  icon, title, onClick, testId, active = false,
}: {
  icon: string;
  title: string;
  onClick: () => void;
  testId: string;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      data-testid={testId}
      className={`p-1 rounded transition-colors ${
        active
          ? 'text-[var(--color-text)] bg-[var(--color-hover)]'
          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      }`}
    >
      <span className="material-symbols-outlined text-sm">{icon}</span>
    </button>
  );
}

export default function ExplorerHeader() {
  const { refreshTree } = useTreeData();
  const { searchQuery, setSearchQuery } = useSearch();
  const { collapseAll, expandAll, sortMode, setSortMode } = useSelection();

  const cycleSort = () => {
    const idx = SORT_CYCLE.indexOf(sortMode);
    const next = SORT_CYCLE[(idx + 1) % SORT_CYCLE.length];
    setSortMode(next);
  };

  return (
    <div data-testid="explorer-header">
      {/* Title row + quick actions */}
      <div className="flex items-center justify-between px-3.5 pt-2 pb-1.5">
        <span className="flex items-center gap-1.5">
          <svg
            width="13" height="13" viewBox="0 0 24 24" fill="none"
            stroke="var(--color-text-secondary)" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round"
            aria-hidden="true" data-testid="layers-icon"
          >
            <polygon points="12 2 2 7 12 12 22 7 12 2" />
            <polyline points="2 17 12 22 22 17" />
            <polyline points="2 12 12 17 22 12" />
          </svg>
          <span className="text-[11px] font-bold uppercase tracking-[0.6px] text-[var(--color-text-secondary)]">
            SwarmWS
          </span>
        </span>

        <div className="flex items-center gap-1">
          <HeaderIconButton
            icon={SORT_META[sortMode].icon}
            title={SORT_META[sortMode].label}
            onClick={cycleSort}
            testId="explorer-sort-toggle"
            active={sortMode !== 'default'}
          />
          <HeaderIconButton
            icon="unfold_less"
            title="Collapse all folders"
            onClick={collapseAll}
            testId="explorer-collapse-all"
          />
          <HeaderIconButton
            icon="unfold_more"
            title="Expand all folders"
            onClick={expandAll}
            testId="explorer-expand-all"
          />
          <OpenFileButton />
          <HeaderIconButton
            icon="refresh"
            title="Refresh workspace tree"
            onClick={refreshTree}
            testId="refresh-button"
          />
        </div>
      </div>

      {/* Search row */}
      <div className="px-3 pb-1.5">
        <div className="relative flex items-center">
          <span
            className="material-symbols-outlined absolute left-2 text-[14px] text-[var(--color-text-dim)] pointer-events-none"
            aria-hidden="true"
          >
            search
          </span>
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search files…"
            aria-label="Search workspace files"
            data-testid="explorer-search-input"
            className="w-full pl-7 pr-7 py-1 text-[12px] rounded-md bg-[var(--color-bg-chrome)] border border-[var(--color-border)] text-[var(--color-text)] placeholder-[var(--color-text-dim)] focus:outline-none focus:border-[var(--color-primary,var(--color-text-secondary))] transition-colors [&::-webkit-search-cancel-button]:hidden"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              title="Clear search"
              aria-label="Clear search"
              data-testid="explorer-search-clear"
              className="absolute right-1.5 p-0.5 rounded text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            >
              <span className="material-symbols-outlined text-[14px]">close</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
