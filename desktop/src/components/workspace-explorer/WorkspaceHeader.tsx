import { useState, useCallback } from 'react';
import type { SwarmWorkspace } from '../../types';
import type { ViewScope } from '../../hooks/useViewScope';

export type { ViewScope };

export interface WorkspaceHeaderProps {
  workspaces: SwarmWorkspace[];
  selectedWorkspaceId: string;
  viewScope: ViewScope;
  isLoading?: boolean;
  showArchived?: boolean;
  onWorkspaceChange: (workspaceId: string) => void;
  onViewScopeChange: (scope: ViewScope) => void;
  onShowArchivedChange?: (show: boolean) => void;
  onSearch?: (query: string) => void;
  onCollapseToggle?: () => void;
}

/**
 * WorkspaceHeader - Workspace selector, view/scope toggle, and search bar.
 * Requirements: 3.2, 9.1, 9.2, 9.3, 36.4, 36.11
 */
export default function WorkspaceHeader({
  workspaces,
  selectedWorkspaceId,
  viewScope,
  isLoading = false,
  showArchived = false,
  onWorkspaceChange,
  onViewScopeChange,
  onShowArchivedChange,
  onSearch,
  onCollapseToggle,
}: WorkspaceHeaderProps) {
  const [searchQuery, setSearchQuery] = useState('');

  const handleWorkspaceChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      onWorkspaceChange(e.target.value);
    },
    [onWorkspaceChange]
  );

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const q = e.target.value;
      setSearchQuery(q);
      onSearch?.(q);
    },
    [onSearch]
  );

  const selectedWorkspace = workspaces.find((w) => w.id === selectedWorkspaceId);
  const isSwarmWS = selectedWorkspace?.isDefault ?? false;

  return (
    <div className="border-b border-[var(--color-border)]" data-testid="workspace-header">
      {/* Title bar with collapse */}
      <div className="h-10 flex items-center justify-between px-3 border-b border-[var(--color-border)]">
        <span className="text-sm font-medium text-[var(--color-text)]">Explorer</span>
        {onCollapseToggle && (
          <button
            onClick={onCollapseToggle}
            className="p-1 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            title="Collapse workspace explorer"
            aria-label="Collapse workspace explorer"
            aria-expanded="true"
            data-testid="collapse-button"
          >
            <span className="material-symbols-outlined text-sm">chevron_left</span>
          </button>
        )}
      </div>

      {/* Workspace selector */}
      <div className="px-3 py-2">
        <div className="relative">
          <select
            value={selectedWorkspaceId}
            onChange={handleWorkspaceChange}
            disabled={isLoading}
            className="w-full px-3 py-1.5 pr-8 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] appearance-none cursor-pointer hover:border-[var(--color-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="workspace-selector"
            aria-label="Select workspace"
          >
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.isDefault ? `🏠 ${ws.name}` : ws.isArchived ? `📦 ${ws.name} (archived)` : `📁 ${ws.name}`}
              </option>
            ))}
          </select>
          <div className="absolute inset-y-0 right-0 flex items-center pr-2 pointer-events-none">
            <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
              expand_more
            </span>
          </div>
        </div>
        {/* Show Archived toggle */}
        {onShowArchivedChange && (
          <label
            className="flex items-center gap-1.5 mt-1.5 cursor-pointer text-xs text-[var(--color-text-muted)]"
            data-testid="show-archived-toggle"
          >
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => onShowArchivedChange(e.target.checked)}
              className="rounded border-[var(--color-border)] accent-[var(--color-primary)]"
            />
            <span>Show Archived</span>
          </label>
        )}
      </div>

      {/* View/Scope toggle */}
      <div className="px-3 pb-2">
        <div className="flex rounded border border-[var(--color-border)] overflow-hidden text-xs">
          <button
            className={`flex-1 px-2 py-1 transition-colors ${
              viewScope === 'global'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
            }`}
            onClick={() => onViewScopeChange('global')}
            data-testid="scope-toggle-global"
            aria-pressed={viewScope === 'global'}
          >
            {isSwarmWS ? 'Global' : 'All Workspaces'}
          </button>
          <button
            className={`flex-1 px-2 py-1 transition-colors ${
              viewScope === 'scoped'
                ? 'bg-[var(--color-primary)] text-white'
                : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
            }`}
            onClick={() => onViewScopeChange('scoped')}
            data-testid="scope-toggle-scoped"
            aria-pressed={viewScope === 'scoped'}
          >
            {isSwarmWS ? 'SwarmWS Only' : 'This Workspace'}
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="px-3 pb-2">
        <div className="relative">
          <span className="absolute left-2 top-1/2 -translate-y-1/2 material-symbols-outlined text-sm text-[var(--color-text-muted)]">
            search
          </span>
          <input
            type="text"
            value={searchQuery}
            onChange={handleSearchChange}
            placeholder="Search… (threads, tasks, signals, artifacts)"
            className="w-full pl-7 pr-3 py-1.5 text-xs rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
            data-testid="workspace-search"
            aria-label="Search workspace"
          />
        </div>
      </div>
    </div>
  );
}
