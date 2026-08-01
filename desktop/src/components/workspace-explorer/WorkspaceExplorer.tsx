/**
 * WorkspaceExplorer — the SwarmWS semantic file tree.
 *
 * Rendered ONLY inside the SwarmWS fullscreen overlay (A10, run_1aab916c) — the
 * former always-on middle column was removed, so this always fills its parent.
 * A semantically-zoned, virtualized tree view powered by ``ExplorerContext``.
 *
 * Key exports:
 * - ``WorkspaceExplorer``       — The main explorer component (default export)
 * - ``WorkspaceExplorerProps``  — Props interface
 *
 * Component structure:
 * - ``ExplorerHeader``   — Static "SwarmWS" title, focus mode toggle, refresh
 * - ``AutoSizer``        — Dynamic sizing wrapper from react-virtualized-auto-sizer
 * - ``VirtualizedTree``  — react-window based virtualized tree rendering
 *
 * Removed elements (from old explorer):
 * - WorkspaceHeader, SectionNavigation, FileTree, FileTreeNode
 * - OverviewContextCard, WorkspaceFooter, ArtifactsFileTree, RecommendedGroup
 * - Multi-workspace listing, archive/unarchive/delete logic
 * - showArchived toggle, workspace dropdown, @tanstack/react-query usage
 *
 * Data fetching is handled by ``ExplorerProvider`` (mounted in ThreeColumnLayout,
 * so the 30s tree poll runs whether or not the SwarmWS overlay is open). This
 * component reads tree state from ``useTreeData()`` and renders accordingly.
 *
 * Requirements: 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.4, 11.1, 15.1
 */

import { useTreeData } from '../../contexts/ExplorerContext';
import ExplorerHeader from './ExplorerHeader';
import SectionedExplorer from './SectionedExplorer';
import type { FileTreeItem } from './FileTreeNode';

/**
 * Skeleton placeholder for the file tree while loading.
 * Renders 8 pulsing lines with indentation to suggest a tree structure.
 * Widths are deterministic to avoid layout shifts on re-render.
 */
function TreeSkeleton() {
  const lines = [
    { indent: 0, width: '75%' },
    { indent: 0, width: '65%' },
    { indent: 16, width: '80%' },
    { indent: 16, width: '70%' },
    { indent: 32, width: '60%' },
    { indent: 0, width: '85%' },
    { indent: 16, width: '72%' },
    { indent: 0, width: '68%' },
  ];
  return (
    <div className="p-3 space-y-2" data-testid="tree-skeleton">
      {lines.map((line, i) => (
        <div
          key={i}
          className="h-4 bg-[var(--color-hover)] rounded animate-pulse"
          style={{ marginLeft: line.indent, width: line.width }}
        />
      ))}
    </div>
  );
}

/**
 * Inline error state with retry button, shown when the tree fetch fails.
 * ChatPage remains fully interactive regardless of this error.
 */
function TreeErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="p-4 text-center" data-testid="tree-error-state">
      <p className="text-sm text-[var(--color-text-muted)] mb-2">Failed to load file tree</p>
      <button
        onClick={onRetry}
        className="text-xs text-primary hover:underline"
      >
        Retry
      </button>
    </div>
  );
}

export interface WorkspaceExplorerProps {
  /** Callback when a file node is double-clicked (e.g., to open in editor). */
  onFileDoubleClick?: (node: FileTreeItem) => void;
  /** Callback when "Attach to Chat" is selected from the context menu. */
  onAttachToChat?: (item: FileTreeItem) => void;
}

// The explorer is rendered ONLY inside the SwarmWS fullscreen overlay (A10,
// run_1aab916c) — the always-on sibling column was removed. So it always fills
// its parent; there is no collapse rail, no ResizeHandle, no fixed column width
// (the overlay owns the width). The former column-mode machinery
// (workspaceExplorerCollapsed / width / narrow-viewport auto-collapse) was dead
// and has been deleted (Gate-2 E-3).
export default function WorkspaceExplorer({ onFileDoubleClick, onAttachToChat }: WorkspaceExplorerProps) {
  const { treeData, isLoading, error, refreshTree } = useTreeData();

  return (
    <div
      className="relative h-full w-full bg-[var(--color-bg-chrome)] flex flex-col"
      data-testid="workspace-explorer"
    >
      <ExplorerHeader />

      {/* Tree content area — fills remaining vertical space.
          min-h-0 is CRITICAL: without it, flex items default to min-height:auto
          which means the content won't shrink below its intrinsic size, causing
          overflow instead of scroll within react-window. */}
      <div className="flex-1 overflow-hidden min-h-0">
        {isLoading && <TreeSkeleton />}

        {!isLoading && error && <TreeErrorState onRetry={refreshTree} />}

        {!isLoading && !error && treeData.length === 0 && (
          <div
            className="flex items-center justify-center h-full px-4 text-center text-sm text-[var(--color-text-muted)]"
            data-testid="explorer-empty"
          >
            SwarmWS is empty. Initialize your workspace to get started.
          </div>
        )}

        {!isLoading && !error && treeData.length > 0 && (
          <SectionedExplorer onFileDoubleClick={onFileDoubleClick} onAttachToChat={onAttachToChat} />
        )}
      </div>
    </div>
  );
}
