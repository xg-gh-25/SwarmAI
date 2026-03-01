import { useCallback, useMemo, useEffect, useState } from 'react';
import FileTreeNode, { type FileTreeItem } from './FileTreeNode';
import { workspaceService } from '../../services/workspace';
import type { SwarmWorkspace } from '../../types';

interface FileTreeProps {
  /** List of workspaces to display */
  workspaces: SwarmWorkspace[];
  /** Currently selected workspace scope ('all' or workspace ID) */
  selectedScope: string;
  /** Callback when a file is selected */
  onFileSelect?: (item: FileTreeItem) => void;
  /** Callback when a file is double-clicked (for editing) */
  onFileDoubleClick?: (item: FileTreeItem) => void;
  /** Callback for context menu */
  onContextMenu?: (event: React.MouseEvent, item: FileTreeItem) => void;
  /** Callback for drag start (file attachment) */
  onDragStart?: (event: React.DragEvent, item: FileTreeItem) => void;
  /** Whether the tree is loading */
  isLoading?: boolean;
  /** Callback when user wants to add a workspace - Requirement 10.5 */
  onAddWorkspace?: () => void;
}

/**
 * FileTree component - displays hierarchical file structure
 * 
 * Requirements:
 * - 3.5: Display files and folders in hierarchical tree structure
 * - 3.6: Expand or collapse folders on click
 * - 3.4: Filter based on selected workspace scope
 * - 10.5: Prompt user to add workspace if only Swarm Workspace exists
 */
export default function FileTree({
  workspaces,
  selectedScope,
  onFileSelect,
  onFileDoubleClick,
  onContextMenu,
  onDragStart,
  isLoading: externalLoading,
  onAddWorkspace,
}: FileTreeProps) {
  // Track expanded folder paths
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set());
  // Track selected path
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  // Track loaded directory contents
  const [directoryContents, setDirectoryContents] = useState<Map<string, FileTreeItem[]>>(new Map());
  // Track loading directories
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set());

  // Filter workspaces based on scope
  const filteredWorkspaces = useMemo(() => {
    if (selectedScope === 'all') {
      return workspaces;
    }
    return workspaces.filter(w => w.id === selectedScope);
  }, [workspaces, selectedScope]);

  // Convert workspaces to tree items (root level)
  const rootItems: FileTreeItem[] = useMemo(() => {
    return filteredWorkspaces.map(workspace => ({
      id: `workspace-${workspace.id}`,
      name: workspace.name,
      type: 'directory' as const,
      path: workspace.filePath,
      workspaceId: workspace.id,
      workspaceName: workspace.name,
      isSwarmWorkspace: workspace.isDefault,
      children: directoryContents.get(workspace.filePath) || [],
    }));
  }, [filteredWorkspaces, directoryContents]);

  // Load directory contents
  const loadDirectoryContents = useCallback(async (
    dirPath: string, 
    workspaceId: string, 
    workspaceName: string,
    isSwarmWorkspace: boolean
  ) => {
    if (loadingDirs.has(dirPath)) return;
    
    setLoadingDirs(prev => new Set(prev).add(dirPath));
    
    try {
      // Use browseFilesystem to list directory contents
      const response = await workspaceService.browseFilesystem(dirPath);
      
      // Convert WorkspaceFile[] to FileTreeItem[]
      const items: FileTreeItem[] = response.files
        .filter(file => file.name !== '.' && file.name !== '..')
        .sort((a, b) => {
          // Directories first, then alphabetically
          if (a.type !== b.type) {
            return a.type === 'directory' ? -1 : 1;
          }
          return a.name.localeCompare(b.name);
        })
        .map(file => ({
          id: `${workspaceId}-${dirPath}/${file.name}`,
          name: file.name,
          type: file.type,
          path: `${dirPath}/${file.name}`,
          workspaceId,
          workspaceName,
          isSwarmWorkspace,
          children: file.type === 'directory' ? [] : undefined,
        }));
      
      setDirectoryContents(prev => {
        const next = new Map(prev);
        next.set(dirPath, items);
        return next;
      });
    } catch (error) {
      console.error(`Failed to load directory contents for ${dirPath}:`, error);
    } finally {
      setLoadingDirs(prev => {
        const next = new Set(prev);
        next.delete(dirPath);
        return next;
      });
    }
  }, [loadingDirs]);

  // Handle folder toggle - Requirement 3.6
  const handleToggle = useCallback((path: string) => {
    setExpandedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
        
        // Find the workspace for this path to load contents
        const workspace = filteredWorkspaces.find(w => 
          path === w.filePath || path.startsWith(w.filePath + '/')
        );
        
        if (workspace && !directoryContents.has(path)) {
          loadDirectoryContents(path, workspace.id, workspace.name, workspace.isDefault);
        }
      }
      return next;
    });
  }, [filteredWorkspaces, directoryContents, loadDirectoryContents]);

  // Handle node selection
  const handleSelect = useCallback((node: FileTreeItem) => {
    setSelectedPath(node.path);
    onFileSelect?.(node);
  }, [onFileSelect]);

  // Load root workspace contents when workspaces change or scope changes
  useEffect(() => {
    filteredWorkspaces.forEach(workspace => {
      if (!directoryContents.has(workspace.filePath)) {
        loadDirectoryContents(
          workspace.filePath, 
          workspace.id, 
          workspace.name, 
          workspace.isDefault
        );
      }
    });
  }, [filteredWorkspaces, directoryContents, loadDirectoryContents]);

  // Update children in tree when directory contents change
  const treeWithChildren = useMemo(() => {
    const updateChildren = (items: FileTreeItem[]): FileTreeItem[] => {
      return items.map(item => {
        if (item.type === 'directory') {
          const children = directoryContents.get(item.path);
          return {
            ...item,
            children: children ? updateChildren(children) : [],
          };
        }
        return item;
      });
    };
    
    return updateChildren(rootItems);
  }, [rootItems, directoryContents]);

  const isLoading = externalLoading || loadingDirs.size > 0;

  // Check if only Swarm Workspace exists (no user workspaces) - Requirement 10.5
  const hasOnlySwarmWorkspace = useMemo(() => {
    // All workspaces are default (Swarm Workspace)
    return workspaces.length > 0 && workspaces.every(w => w.isDefault);
  }, [workspaces]);

  // Empty state - no workspaces at all
  if (!isLoading && filteredWorkspaces.length === 0) {
    return (
      <div 
        className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)] text-sm p-4"
        data-testid="file-tree-empty"
      >
        <span className="material-symbols-outlined text-3xl mb-2">folder_off</span>
        <p>No workspaces found</p>
      </div>
    );
  }

  // Empty workspace state - only Swarm Workspace exists - Requirement 10.5
  if (!isLoading && hasOnlySwarmWorkspace && selectedScope === 'all') {
    return (
      <div 
        className="flex flex-col h-full"
        data-testid="file-tree"
      >
        {/* Still show the Swarm Workspace tree */}
        <div 
          className="flex-1 overflow-auto"
          role="tree"
          aria-label="File explorer"
        >
          <div className="py-1">
            {treeWithChildren.map(item => (
              <FileTreeNode
                key={item.id}
                node={item}
                depth={0}
                expandedPaths={expandedPaths}
                onToggle={handleToggle}
                onSelect={handleSelect}
                selectedPath={selectedPath}
                onContextMenu={onContextMenu}
                onDragStart={onDragStart}
                onDoubleClick={onFileDoubleClick}
              />
            ))}
          </div>
        </div>
        
        {/* Add Workspace prompt - Requirement 10.5 */}
        <div 
          className="flex flex-col items-center justify-center p-4 border-t border-[var(--color-border)] bg-[var(--color-bg-secondary)]"
          data-testid="add-workspace-prompt"
        >
          <span className="material-symbols-outlined text-2xl mb-2 text-[var(--color-primary)]">
            add_circle
          </span>
          <p className="text-sm text-[var(--color-text-muted)] text-center mb-3">
            Add a workspace to get started
          </p>
          <button
            onClick={onAddWorkspace}
            className="px-4 py-2 text-sm rounded bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity flex items-center gap-2"
            data-testid="add-workspace-button"
          >
            <span className="material-symbols-outlined text-sm">add</span>
            Add Workspace
          </button>
        </div>
      </div>
    );
  }

  return (
    <div 
      className="flex-1 overflow-auto"
      role="tree"
      aria-label="File explorer"
      data-testid="file-tree"
    >
      {/* Loading indicator */}
      {isLoading && (
        <div className="flex items-center gap-2 px-3 py-2 text-sm text-[var(--color-text-muted)]">
          <div className="w-4 h-4 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
          <span>Loading...</span>
        </div>
      )}
      
      {/* Tree nodes */}
      <div className="py-1">
        {treeWithChildren.map(item => (
          <FileTreeNode
            key={item.id}
            node={item}
            depth={0}
            expandedPaths={expandedPaths}
            onToggle={handleToggle}
            onSelect={handleSelect}
            selectedPath={selectedPath}
            onContextMenu={onContextMenu}
            onDragStart={onDragStart}
            onDoubleClick={onFileDoubleClick}
          />
        ))}
      </div>
    </div>
  );
}

// Export types for use in other components
export type { FileTreeItem };
