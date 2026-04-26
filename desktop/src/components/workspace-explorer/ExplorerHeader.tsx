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

import { useTreeData } from '../../contexts/ExplorerContext';

export interface ExplorerHeaderProps {
  onCollapseToggle?: () => void;
}

export default function ExplorerHeader({ onCollapseToggle }: ExplorerHeaderProps) {
  const { refreshTree } = useTreeData();

  return (
    <div
      data-testid="explorer-header"
    >
      <div className="flex items-center justify-between px-3.5 pt-2 pb-1.5">
        {/* Section title — SVG Layers icon (AC6: no emoji icons) */}
        <span className="flex items-center gap-1.5">
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--color-text-secondary)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            data-testid="layers-icon"
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
