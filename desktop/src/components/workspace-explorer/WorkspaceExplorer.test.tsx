/**
 * Unit tests for the redesigned WorkspaceExplorer component.
 *
 * Tests the new single-workspace, semantically-zoned explorer that replaced
 * the old multi-workspace file browser. Verifies loading, error, empty, and
 * normal render states, collapsed mode, and confirms old components
 * (SectionNavigation, WorkspaceHeader, OverviewContextCard) are absent.
 *
 * Validates: Requirements 9.3, 9.7
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import type { TreeNode } from '../../types';

// ---------- Mocks ----------

vi.mock('../../contexts/LayoutContext', () => ({
  useLayout: vi.fn(),
  LAYOUT_CONSTANTS: {
    MIN_WORKSPACE_EXPLORER_WIDTH: 200,
    MAX_WORKSPACE_EXPLORER_WIDTH: 500,
  },
}));

vi.mock('../../contexts/ExplorerContext', () => ({
  useTreeData: vi.fn(),
}));

// Mock AutoSizer to render children immediately
vi.mock('react-virtualized-auto-sizer', () => ({
  AutoSizer: ({ renderProp }: { renderProp: (size: { height: number; width: number }) => React.ReactNode }) => (
    <div data-testid="auto-sizer">{renderProp({ height: 500, width: 280 })}</div>
  ),
  default: ({ renderProp }: { renderProp: (size: { height: number; width: number }) => React.ReactNode }) => (
    <div data-testid="auto-sizer">{renderProp({ height: 500, width: 280 })}</div>
  ),
}));

// Mock VirtualizedTree
vi.mock('./VirtualizedTree', () => ({
  default: () => <div data-testid="virtualized-tree" />,
}));

// Mock ExplorerHeader
vi.mock('./ExplorerHeader', () => ({
  default: ({ onCollapseToggle }: { onCollapseToggle?: () => void }) => (
    <div data-testid="explorer-header" onClick={onCollapseToggle}>ExplorerHeader</div>
  ),
}));

// Mock ResizeHandle
vi.mock('./ResizeHandle', () => ({
  default: () => <div data-testid="resize-handle" />,
}));

// ---------- Imports (after mocks) ----------

import WorkspaceExplorer from './WorkspaceExplorer';
import { useLayout } from '../../contexts/LayoutContext';
import { useTreeData } from '../../contexts/ExplorerContext';

// ---------- Helpers ----------

const mockUseLayout = useLayout as ReturnType<typeof vi.fn>;
const mockUseTreeData = useTreeData as ReturnType<typeof vi.fn>;

const SAMPLE_TREE: TreeNode[] = [
  { name: 'system-prompts.md', path: 'system-prompts.md', type: 'file' },
  {
    name: 'Knowledge',
    path: 'Knowledge',
    type: 'directory',
    children: [
      { name: 'Notes', path: 'Knowledge/Notes', type: 'directory' },
    ],
  },
];

function setupMocks(overrides: {
  collapsed?: boolean;
  treeData?: TreeNode[];
  isLoading?: boolean;
  error?: string | null;
} = {}) {
  mockUseLayout.mockReturnValue({
    workspaceExplorerCollapsed: overrides.collapsed ?? false,
    workspaceExplorerWidth: 280,
    setWorkspaceExplorerWidth: vi.fn(),
    setWorkspaceExplorerCollapsed: vi.fn(),
    isNarrowViewport: false,
  });

  mockUseTreeData.mockReturnValue({
    treeData: overrides.treeData ?? SAMPLE_TREE,
    isLoading: overrides.isLoading ?? false,
    error: overrides.error ?? null,
    refreshTree: vi.fn(),
  });
}

// ---------- Tests ----------

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkspaceExplorer', () => {
  beforeEach(() => {
    setupMocks();
  });

  it('renders TreeSkeleton when isLoading is true', () => {
    setupMocks({ isLoading: true, treeData: [] });
    render(<WorkspaceExplorer />);
    expect(screen.getByTestId('tree-skeleton')).toBeInTheDocument();
  });

  it('renders TreeErrorState with retry button when error is set', () => {
    setupMocks({ error: 'Network error' });
    render(<WorkspaceExplorer />);
    expect(screen.getByTestId('tree-error-state')).toBeInTheDocument();
    expect(screen.getByText('Failed to load file tree')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('renders empty state when treeData is empty and not loading', () => {
    setupMocks({ treeData: [] });
    render(<WorkspaceExplorer />);
    expect(screen.getByText(/SwarmWS is empty/)).toBeInTheDocument();
  });

  it('renders ExplorerHeader when not loading and has data', () => {
    render(<WorkspaceExplorer />);
    expect(screen.getByTestId('explorer-header')).toBeInTheDocument();
  });

  it('renders VirtualizedTree via AutoSizer when data is present', () => {
    render(<WorkspaceExplorer />);
    expect(screen.getByTestId('virtualized-tree')).toBeInTheDocument();
  });

  it('renders collapsed state with 24px expand button', () => {
    setupMocks({ collapsed: true });
    render(<WorkspaceExplorer />);
    expect(screen.getByTestId('workspace-explorer-collapsed')).toBeInTheDocument();
    expect(screen.getByTestId('expand-button')).toBeInTheDocument();
  });

  it('does NOT render old components (SectionNavigation, WorkspaceHeader, OverviewContextCard)', () => {
    render(<WorkspaceExplorer />);
    // Old components should be completely absent from the DOM
    expect(screen.queryByTestId('section-header-signals')).not.toBeInTheDocument();
    expect(screen.queryByTestId('workspace-selector')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scope-toggle-global')).not.toBeInTheDocument();
    expect(screen.queryByText('No workspace context set')).not.toBeInTheDocument();
    expect(screen.queryByText('+ Add Context')).not.toBeInTheDocument();
    expect(screen.queryByTestId('new-workspace-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('show-archived-toggle')).not.toBeInTheDocument();
  });

  // ── embedded mode (rendered inside the SwarmWS fullscreen overlay) ──
  describe('embedded mode', () => {
    it('ignores the collapsed state and renders the tree even when collapsed=true', () => {
      // In an overlay there is no column to collapse; embedded must bypass the rail.
      setupMocks({ collapsed: true });
      render(<WorkspaceExplorer embedded />);
      expect(screen.queryByTestId('workspace-explorer-collapsed')).not.toBeInTheDocument();
      expect(screen.getByTestId('workspace-explorer')).toBeInTheDocument();
      expect(screen.getByTestId('virtualized-tree')).toBeInTheDocument();
    });

    it('does NOT render the ResizeHandle in embedded mode (overlay owns the width)', () => {
      render(<WorkspaceExplorer embedded />);
      expect(screen.queryByTestId('resize-handle')).not.toBeInTheDocument();
    });

    it('fills its parent (no fixed column width / border-r rail) in embedded mode', () => {
      render(<WorkspaceExplorer embedded />);
      const root = screen.getByTestId('workspace-explorer');
      // embedded root fills parent instead of the fixed-width bordered column
      expect(root.className).toContain('h-full');
      expect(root.className).not.toContain('border-r');
      expect(root.className).not.toContain('flex-shrink-0');
      // no inline fixed width applied
      expect(root.style.width).toBe('');
    });

    it('still renders the ExplorerHeader in embedded mode', () => {
      render(<WorkspaceExplorer embedded />);
      expect(screen.getByTestId('explorer-header')).toBeInTheDocument();
    });
  });
});
