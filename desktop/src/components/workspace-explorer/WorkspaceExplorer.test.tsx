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
  default: () => <div data-testid="explorer-header">ExplorerHeader</div>,
}));

// ---------- Imports (after mocks) ----------

import WorkspaceExplorer from './WorkspaceExplorer';
import { useTreeData } from '../../contexts/ExplorerContext';

// ---------- Helpers ----------

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
  treeData?: TreeNode[];
  isLoading?: boolean;
  error?: string | null;
} = {}) {
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

  // ── overlay-only render (A10, run_1aab916c): the explorer always fills its
  //    parent — no collapse rail, no ResizeHandle, no fixed column width. ──
  it('fills its parent (no fixed column width / border-r rail / collapse)', () => {
    render(<WorkspaceExplorer />);
    const root = screen.getByTestId('workspace-explorer');
    expect(root.className).toContain('h-full');
    expect(root.className).not.toContain('border-r');
    expect(root.className).not.toContain('flex-shrink-0');
    // no inline fixed width applied (overlay owns the width)
    expect(root.style.width).toBe('');
    // the deleted column-mode collapse rail must never appear
    expect(screen.queryByTestId('workspace-explorer-collapsed')).not.toBeInTheDocument();
    expect(screen.queryByTestId('resize-handle')).not.toBeInTheDocument();
  });
});
