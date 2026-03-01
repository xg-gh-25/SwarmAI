import { useState, useCallback, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLayout } from '../../contexts/LayoutContext';
import { swarmWorkspacesService } from '../../services/swarmWorkspaces';
import FileTree, { type FileTreeItem } from './FileTree';
import ExplorerToolbar from './ExplorerToolbar';
import FileContextMenu from './FileContextMenu';
import ResizeHandle from './ResizeHandle';
import AddWorkspaceDialog from './AddWorkspaceDialog';
import { Toast } from '../common';
import type { SwarmWorkspace } from '../../types';

/**
 * WorkspaceExplorer component - middle column of the three-column layout
 * 
 * Displays a scope dropdown for filtering workspaces and a file tree
 * for browsing workspace contents.
 * 
 * Requirements:
 * - 3.1: Display scope dropdown showing current Workspace_Scope
 * - 3.2: Offer "All Workspaces" as default option
 * - 3.3: List all available workspaces as selectable options
 * - 3.5: Display files and folders in hierarchical tree structure
 * - 3.6: Expand or collapse folders on click
 * - 3.12: Drag files to chat to attach as context
 */

interface WorkspaceExplorerProps {
  /** Whether the explorer is collapsed */
  collapsed?: boolean;
  /** Width of the explorer panel */
  width?: number;
  /** Callback when collapse state changes */
  onCollapsedChange?: (collapsed: boolean) => void;
  /** Callback when width changes (from resize handle) */
  onWidthChange?: (width: number) => void;
  /** Callback when a file is selected */
  onFileSelect?: (file: FileTreeItem) => void;
  /** Callback when a file is double-clicked (for editing) */
  onFileDoubleClick?: (file: FileTreeItem) => void;
  /** Callback when a file should be attached to chat */
  onFileAttach?: (file: FileTreeItem) => void;
}

export default function WorkspaceExplorer({ 
  collapsed: controlledCollapsed,
  width: controlledWidth,
  onCollapsedChange,
  onWidthChange,
  onFileSelect,
  onFileDoubleClick,
  onFileAttach,
}: WorkspaceExplorerProps) {
  const queryClient = useQueryClient();
  const { 
    workspaceExplorerCollapsed, 
    workspaceExplorerWidth,
    setWorkspaceExplorerWidth,
    setWorkspaceExplorerCollapsed,
    selectedWorkspaceScope,
    setSelectedWorkspaceScope,
    validateWorkspaceScope,
    isNarrowViewport,
    attachFile,
    attachedFiles,
    clearAttachedFiles,
  } = useLayout();

  // Track selected file/folder for toolbar operations
  const [selectedItem, setSelectedItem] = useState<FileTreeItem | null>(null);
  
  // Toast notification state for scope change - Requirement 6.5
  const [scopeChangeToast, setScopeChangeToast] = useState<string | null>(null);
  
  // Context menu state
  const [contextMenu, setContextMenu] = useState<{
    item: FileTreeItem;
    x: number;
    y: number;
  } | null>(null);

  // Add Workspace dialog state - Requirement 10.5
  const [isAddWorkspaceDialogOpen, setIsAddWorkspaceDialogOpen] = useState(false);

  // Use controlled props if provided, otherwise use context
  const isCollapsed = controlledCollapsed ?? workspaceExplorerCollapsed;
  const explorerWidth = controlledWidth ?? workspaceExplorerWidth;

  // Fetch workspaces from the service
  const { data: workspaces = [], isLoading } = useQuery<SwarmWorkspace[]>({
    queryKey: ['swarmWorkspaces'],
    queryFn: swarmWorkspacesService.list,
  });

  // Validate workspace scope when workspaces are loaded - Requirement 10.2
  // This ensures that if a stored workspace ID is invalid, we reset to 'all'
  useEffect(() => {
    if (!isLoading && workspaces.length > 0) {
      const workspaceIds = workspaces.map(w => w.id);
      validateWorkspaceScope(workspaceIds);
    }
  }, [workspaces, isLoading, validateWorkspaceScope]);

  // Handle width change from resize handle
  const handleWidthChange = useCallback((newWidth: number) => {
    if (onWidthChange) {
      onWidthChange(newWidth);
    } else {
      setWorkspaceExplorerWidth(newWidth);
    }
  }, [onWidthChange, setWorkspaceExplorerWidth]);

  // Handle collapse toggle
  const handleCollapseToggle = (newCollapsed: boolean) => {
    if (onCollapsedChange) {
      onCollapsedChange(newCollapsed);
    } else {
      setWorkspaceExplorerCollapsed(newCollapsed);
    }
  };

  // Handle scope selection change - Requirement 6.5
  const handleScopeChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const newScope = event.target.value;
    const previousScope = selectedWorkspaceScope;
    
    // Only process if scope actually changed
    if (newScope === previousScope) {
      return;
    }
    
    // Clear attached files when scope changes - Requirement 6.5
    const hadAttachedFiles = attachedFiles.length > 0;
    if (hadAttachedFiles) {
      clearAttachedFiles();
      // Show notification that context was cleared
      setScopeChangeToast('Chat context cleared for new workspace scope');
    }
    
    // Update the scope
    setSelectedWorkspaceScope(newScope);
    // Clear selection when scope changes
    setSelectedItem(null);
  };

  // Handle file selection - track for toolbar operations
  const handleFileSelect = useCallback((item: FileTreeItem) => {
    setSelectedItem(item);
    onFileSelect?.(item);
  }, [onFileSelect]);

  // Handle file system changes (refresh file tree)
  const handleFileSystemChange = useCallback(() => {
    // Invalidate workspace queries to refresh file tree
    queryClient.invalidateQueries({ queryKey: ['swarmWorkspaces'] });
  }, [queryClient]);

  // Handle context menu open
  const handleContextMenu = useCallback((event: React.MouseEvent, item: FileTreeItem) => {
    event.preventDefault();
    setContextMenu({
      item,
      x: event.clientX,
      y: event.clientY,
    });
  }, []);

  // Handle context menu close
  const handleContextMenuClose = useCallback(() => {
    setContextMenu(null);
  }, []);

  // Handle attach to chat from context menu - Requirements 3.12, 6.1, 6.2
  const handleAttachToChat = useCallback((item: FileTreeItem) => {
    // Use context's attachFile function for centralized state management
    attachFile(item);
    // Also call the prop callback if provided
    onFileAttach?.(item);
  }, [attachFile, onFileAttach]);

  // Handle rename from context menu (placeholder - will be implemented in future task)
  const handleRename = useCallback((item: FileTreeItem) => {
    // TODO: Implement rename dialog in a future task
    console.log('Rename requested for:', item.name);
  }, []);

  // Handle opening Add Workspace dialog - Requirement 10.5
  const handleOpenAddWorkspaceDialog = useCallback(() => {
    setIsAddWorkspaceDialogOpen(true);
  }, []);

  // Handle workspace added - refresh the workspace list
  const handleWorkspaceAdded = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['swarmWorkspaces'] });
  }, [queryClient]);

  // Get the selected directory path for toolbar operations
  const getSelectedDirectoryPath = (): string | null => {
    if (!selectedItem) {
      // If no item selected, use the workspace root when a specific workspace is selected
      if (selectedWorkspaceScope !== 'all') {
        const workspace = workspaces.find(w => w.id === selectedWorkspaceScope);
        return workspace?.filePath ?? null;
      }
      return null;
    }
    // If a directory is selected, use it; otherwise use its parent
    if (selectedItem.type === 'directory') {
      return selectedItem.path;
    }
    // Get parent directory from file path
    const lastSlash = selectedItem.path.lastIndexOf('/');
    return lastSlash > 0 ? selectedItem.path.substring(0, lastSlash) : null;
  };

  // Get the workspace ID for the selected item
  const getSelectedWorkspaceId = (): string | null => {
    if (selectedItem) {
      return selectedItem.workspaceId;
    }
    if (selectedWorkspaceScope !== 'all') {
      return selectedWorkspaceScope;
    }
    return null;
  };

  // Collapsed state - show expand button
  // Requirements: 1.6, 11.2 - Collapsible explorer with visible toggle button when collapsed
  if (isCollapsed) {
    return (
      <>
        <div 
          className="flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] transition-all duration-200 ease-in-out"
          style={{ width: 24 }}
          data-testid="workspace-explorer-collapsed"
        >
          <button
            onClick={() => handleCollapseToggle(false)}
            className={`w-6 h-full flex items-center justify-center transition-all duration-200 ease-in-out ${
              isNarrowViewport 
                ? 'text-[var(--color-text-muted)] cursor-not-allowed opacity-50' 
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            }`}
            title={isNarrowViewport ? "Expand disabled (window too narrow)" : "Expand workspace explorer"}
            disabled={isNarrowViewport}
            aria-label="Expand workspace explorer"
            aria-expanded="false"
            data-testid="expand-button"
          >
            <span className="material-symbols-outlined text-sm transition-transform duration-200 ease-in-out">chevron_right</span>
          </button>
        </div>
        {/* Scope Change Toast Notification - Requirement 6.5 */}
        {scopeChangeToast && (
          <Toast
            message={scopeChangeToast}
            type="info"
            duration={3000}
            onDismiss={() => setScopeChangeToast(null)}
          />
        )}
      </>
    );
  }

  // Expanded state
  // Requirements: 1.6, 1.7, 11.2, 11.5 - Resizable and collapsible explorer with smooth transitions
  return (
    <div
      className="relative flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col transition-all duration-200 ease-in-out"
      style={{ 
        width: explorerWidth,
        minWidth: 200,
        maxWidth: 500
      }}
      data-testid="workspace-explorer"
    >
      {/* Resize Handle - Requirements 1.7, 11.5 */}
      <ResizeHandle
        currentWidth={explorerWidth}
        onWidthChange={handleWidthChange}
      />
      {/* Header with collapse button - Requirements 1.6, 11.2 */}
      <div className="h-10 flex items-center justify-between px-3 border-b border-[var(--color-border)]">
        <span className="text-sm font-medium text-[var(--color-text)]">Explorer</span>
        <button
          onClick={() => handleCollapseToggle(true)}
          className="p-1 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-all duration-200 ease-in-out"
          title="Collapse workspace explorer"
          aria-label="Collapse workspace explorer"
          aria-expanded="true"
          data-testid="collapse-button"
        >
          <span className="material-symbols-outlined text-sm transition-transform duration-200 ease-in-out">chevron_left</span>
        </button>
      </div>

      {/* Scope Dropdown - Requirements 3.1, 3.2, 3.3 */}
      <div className="px-3 py-2 border-b border-[var(--color-border)]">
        <ScopeDropdown
          selectedScope={selectedWorkspaceScope}
          workspaces={workspaces}
          isLoading={isLoading}
          onChange={handleScopeChange}
        />
      </div>

      {/* Toolbar - Requirements 3.7, 3.8, 3.9, 3.10 */}
      <ExplorerToolbar
        selectedPath={getSelectedDirectoryPath()}
        selectedWorkspaceId={getSelectedWorkspaceId()}
        disabled={isLoading}
        onFileSystemChange={handleFileSystemChange}
      />

      {/* File tree - Requirements 3.5, 3.6, 10.5 */}
      <FileTree
        workspaces={workspaces}
        selectedScope={selectedWorkspaceScope}
        onFileSelect={handleFileSelect}
        onFileDoubleClick={onFileDoubleClick}
        onDragStart={(event, item) => {
          // Set drag data for file attachment
          event.dataTransfer.setData('application/json', JSON.stringify(item));
          event.dataTransfer.effectAllowed = 'copy';
        }}
        onContextMenu={handleContextMenu}
        isLoading={isLoading}
        onAddWorkspace={handleOpenAddWorkspaceDialog}
      />

      {/* Context Menu - Requirements 3.11, 6.1 */}
      {contextMenu && (
        <FileContextMenu
          item={contextMenu.item}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={handleContextMenuClose}
          onAttachToChat={handleAttachToChat}
          onRename={handleRename}
          onFileSystemChange={handleFileSystemChange}
        />
      )}

      {/* Scope Change Toast Notification - Requirement 6.5 */}
      {scopeChangeToast && (
        <Toast
          message={scopeChangeToast}
          type="info"
          duration={3000}
          onDismiss={() => setScopeChangeToast(null)}
        />
      )}

      {/* Add Workspace Dialog - Requirement 10.5 */}
      <AddWorkspaceDialog
        isOpen={isAddWorkspaceDialogOpen}
        onClose={() => setIsAddWorkspaceDialogOpen(false)}
        onWorkspaceAdded={handleWorkspaceAdded}
      />
    </div>
  );
}

/**
 * ScopeDropdown component - dropdown for selecting workspace scope
 * 
 * Requirements:
 * - 3.1: Display current Workspace_Scope
 * - 3.2: "All Workspaces" as default option
 * - 3.3: List all available workspaces
 */
interface ScopeDropdownProps {
  selectedScope: string;
  workspaces: SwarmWorkspace[];
  isLoading: boolean;
  onChange: (event: React.ChangeEvent<HTMLSelectElement>) => void;
}

function ScopeDropdown({ selectedScope, workspaces, isLoading, onChange }: ScopeDropdownProps) {
  return (
    <div className="relative">
      <select
        value={selectedScope}
        onChange={onChange}
        disabled={isLoading}
        className="w-full px-3 py-1.5 pr-8 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] appearance-none cursor-pointer hover:border-[var(--color-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
        data-testid="scope-dropdown"
        aria-label="Select workspace scope"
      >
        {/* Default "All Workspaces" option - Requirement 3.2 */}
        <option value="all">All Workspaces</option>
        
        {/* Separator */}
        {workspaces.length > 0 && (
          <option disabled>──────────</option>
        )}
        
        {/* Individual workspace options - Requirement 3.3 */}
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.isDefault ? `🔒 ${workspace.name}` : workspace.name}
          </option>
        ))}
      </select>
      
      {/* Custom dropdown arrow */}
      <div className="absolute inset-y-0 right-0 flex items-center pr-2 pointer-events-none">
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">
          expand_more
        </span>
      </div>
      
      {/* Loading indicator */}
      {isLoading && (
        <div className="absolute inset-y-0 right-6 flex items-center">
          <div className="w-3 h-3 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
        </div>
      )}
    </div>
  );
}

// Export sub-components for testing
export { ScopeDropdown };
