/**
 * ExplorerHeader — Static header for the SwarmWS Workspace Explorer.
 *
 * Replaces the old ``WorkspaceHeader`` component entirely. All previous
 * controls have been removed:
 * - Workspace dropdown selector
 * - Global/SwarmWS toggle switch
 * - "Show Archived Workspaces" checkbox
 * - "New Workspace" button
 * - Add-context area
 * - Inline search bar
 *
 * Key exports:
 * - ``ExplorerHeader``        — The header component
 * - ``ExplorerHeaderProps``   — Props interface
 *
 * Requirements: 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 12.4
 */

import { useSelection, useTreeData } from '../../contexts/ExplorerContext';

export interface ExplorerHeaderProps {
  onCollapseToggle?: () => void;
}

export default function ExplorerHeader({ onCollapseToggle }: ExplorerHeaderProps) {
  const { focusMode, toggleFocusMode, activeProjectId } = useSelection();
  const { refreshTree } = useTreeData();

  const isFocusDisabled = activeProjectId === null;

  return (
    <div
      data-testid="explorer-header"
    >
      <div className="flex items-center justify-between px-3.5 pt-2 pb-1.5">
        {/* Static title — Requirements: 9.1 — mockup: uppercase, dim, wide tracking */}
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[var(--color-text-muted)]">
          SwarmWS
        </span>

        <div className="flex items-center gap-1">
          {/* Focus Mode toggle — Requirements: 12.4 */}
          <button
            onClick={toggleFocusMode}
            disabled={isFocusDisabled}
            className={`p-1 rounded transition-colors ${
              isFocusDisabled
                ? 'text-[var(--color-text-muted)] opacity-40 cursor-not-allowed'
                : focusMode
                  ? 'text-[var(--color-explorer-focus-indicator)] bg-[var(--color-explorer-focus-indicator)]/10'
                  : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            }`}
            title={isFocusDisabled ? 'Select a project first' : 'Focus on Current Project'}
            aria-label={isFocusDisabled ? 'Select a project first' : 'Focus on Current Project'}
            aria-pressed={focusMode}
            data-testid="focus-mode-toggle"
          >
            <span className="material-symbols-outlined text-sm">center_focus_strong</span>
          </button>

          {/* Manual refresh button — fallback for external filesystem changes */}
          <button
            onClick={refreshTree}
            className="p-1 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            title="Refresh workspace tree"
            aria-label="Refresh workspace tree"
            data-testid="refresh-button"
          >
            <span className="material-symbols-outlined text-sm">refresh</span>
          </button>

          {/* Collapse toggle — same pattern as current explorer */}
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
      </div>
    </div>
  );
}
