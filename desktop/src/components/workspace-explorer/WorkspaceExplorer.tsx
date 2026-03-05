/**
 * WorkspaceExplorer — middle column of the three-column layout.
 *
 * Redesigned for the single-workspace (SwarmWS) semantic explorer model.
 * Replaces the old multi-workspace file browser with a semantically-zoned,
 * virtualized tree view powered by ``ExplorerContext`` state management.
 *
 * Key exports:
 * - ``WorkspaceExplorer``       — The main explorer component (default export)
 * - ``WorkspaceExplorerProps``  — Props interface
 *
 * Component structure:
 * - ``ExplorerHeader``   — Static "SwarmWS" title, focus mode toggle, refresh, collapse
 * - ``AutoSizer``        — Dynamic sizing wrapper from react-virtualized-auto-sizer
 * - ``VirtualizedTree``  — react-window based virtualized tree rendering
 * - ``ResizeHandle``     — Drag-to-resize right edge
 *
 * Removed elements (from old explorer):
 * - WorkspaceHeader, SectionNavigation, FileTree, FileTreeNode
 * - OverviewContextCard, WorkspaceFooter, ArtifactsFileTree, RecommendedGroup
 * - Multi-workspace listing, archive/unarchive/delete logic
 * - showArchived toggle, workspace dropdown, @tanstack/react-query usage
 *
 * Data fetching is handled by ``ExplorerProvider`` (wraps this component in
 * ThreeColumnLayout). This component reads tree state from ``useTreeData()``
 * and renders accordingly.
 *
 * Requirements: 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.4, 11.1, 15.1
 */

import { useCallback } from 'react';
import { AutoSizer } from 'react-virtualized-auto-sizer';
import { useLayout, LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { useTreeData } from '../../contexts/ExplorerContext';
import ExplorerHeader from './ExplorerHeader';
import VirtualizedTree from './VirtualizedTree';
import ResizeHandle from './ResizeHandle';
import type { FileTreeItem } from './FileTreeNode';

export interface WorkspaceExplorerProps {
  /** Callback when a file node is double-clicked (e.g., to open in editor). */
  onFileDoubleClick?: (node: FileTreeItem) => void;
}

export default function WorkspaceExplorer({ onFileDoubleClick }: WorkspaceExplorerProps) {
  const {
    workspaceExplorerCollapsed,
    workspaceExplorerWidth,
    setWorkspaceExplorerWidth,
    setWorkspaceExplorerCollapsed,
    isNarrowViewport,
  } = useLayout();

  const { treeData, isLoading, error, refreshTree } = useTreeData();

  const handleWidthChange = useCallback(
    (newWidth: number) => {
      setWorkspaceExplorerWidth(newWidth);
    },
    [setWorkspaceExplorerWidth],
  );

  const handleCollapseToggle = useCallback(() => {
    setWorkspaceExplorerCollapsed(!workspaceExplorerCollapsed);
  }, [workspaceExplorerCollapsed, setWorkspaceExplorerCollapsed]);

  // Collapsed state — 24px wide expand button
  if (workspaceExplorerCollapsed) {
    return (
      <div
        className="flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] transition-all duration-200 ease-in-out"
        style={{ width: 24 }}
        data-testid="workspace-explorer-collapsed"
      >
        <button
          onClick={handleCollapseToggle}
          className={`w-6 h-full flex items-center justify-center transition-all duration-200 ease-in-out ${
            isNarrowViewport
              ? 'text-[var(--color-text-muted)] cursor-not-allowed opacity-50'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          }`}
          title={isNarrowViewport ? 'Expand disabled (window too narrow)' : 'Expand workspace explorer'}
          disabled={isNarrowViewport}
          aria-label="Expand workspace explorer"
          aria-expanded="false"
          data-testid="expand-button"
        >
          <span className="material-symbols-outlined text-sm">chevron_right</span>
        </button>
      </div>
    );
  }

  // Expanded state
  return (
    <div
      className="relative flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col transition-all duration-200 ease-in-out"
      style={{
        width: workspaceExplorerWidth,
        minWidth: LAYOUT_CONSTANTS.MIN_WORKSPACE_EXPLORER_WIDTH,
        maxWidth: LAYOUT_CONSTANTS.MAX_WORKSPACE_EXPLORER_WIDTH,
      }}
      data-testid="workspace-explorer"
    >
      <ResizeHandle currentWidth={workspaceExplorerWidth} onWidthChange={handleWidthChange} />

      <ExplorerHeader onCollapseToggle={handleCollapseToggle} />

      {/* Tree content area — fills remaining vertical space */}
      <div className="flex-1 overflow-hidden">
        {isLoading && (
          <div
            className="flex items-center justify-center h-full text-sm text-[var(--color-text-muted)]"
            data-testid="explorer-loading"
          >
            Loading...
          </div>
        )}

        {!isLoading && error && (
          <div
            className="flex flex-col items-center justify-center h-full gap-2 px-4 text-center"
            data-testid="explorer-error"
          >
            <span className="text-sm text-[var(--color-text-muted)]">
              Failed to load workspace tree.
            </span>
            <button
              onClick={refreshTree}
              className="text-sm text-[var(--color-primary)] hover:underline"
              data-testid="retry-button"
            >
              Retry
            </button>
          </div>
        )}

        {!isLoading && !error && treeData.length === 0 && (
          <div
            className="flex items-center justify-center h-full px-4 text-center text-sm text-[var(--color-text-muted)]"
            data-testid="explorer-empty"
          >
            SwarmWS is empty. Initialize your workspace to get started.
          </div>
        )}

        {!isLoading && !error && treeData.length > 0 && (
          <AutoSizer
            renderProp={({ height, width }) => {
              if (height === undefined || width === undefined) return null;
              return <VirtualizedTree height={height} width={width} onFileDoubleClick={onFileDoubleClick} />;
            }}
          />
        )}
      </div>
    </div>
  );
}
