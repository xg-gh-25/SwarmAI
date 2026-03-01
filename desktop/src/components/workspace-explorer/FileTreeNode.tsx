import { useCallback } from 'react';

/**
 * FileTreeItem interface - represents a file or directory in the tree
 * 
 * From design.md:
 * - id: unique identifier
 * - name: display name
 * - type: 'file' | 'directory'
 * - path: full path
 * - workspaceId: parent workspace ID
 * - workspaceName: parent workspace name
 * - children: nested items for directories
 * - isSwarmWorkspace: whether this is the protected system workspace
 */
export interface FileTreeItem {
  id: string;
  name: string;
  type: 'file' | 'directory';
  path: string;
  workspaceId: string;
  workspaceName: string;
  children?: FileTreeItem[];
  isSwarmWorkspace?: boolean;
}

interface FileTreeNodeProps {
  /** The file/directory node to render */
  node: FileTreeItem;
  /** Depth level for indentation */
  depth: number;
  /** Set of expanded folder paths */
  expandedPaths: Set<string>;
  /** Callback when a folder is toggled */
  onToggle: (path: string) => void;
  /** Callback when a file/folder is selected */
  onSelect: (node: FileTreeItem) => void;
  /** Currently selected path */
  selectedPath: string | null;
  /** Callback for context menu */
  onContextMenu?: (event: React.MouseEvent, node: FileTreeItem) => void;
  /** Callback for drag start */
  onDragStart?: (event: React.DragEvent, node: FileTreeItem) => void;
  /** Callback for double-click (file editing) - Requirement 9.1 */
  onDoubleClick?: (node: FileTreeItem) => void;
}

/**
 * FileTreeNode component - renders a single node in the file tree
 * 
 * Requirements:
 * - 3.5: Display files and folders in hierarchical tree structure
 * - 3.6: Expand or collapse folders on click
 */
export default function FileTreeNode({
  node,
  depth,
  expandedPaths,
  onToggle,
  onSelect,
  selectedPath,
  onContextMenu,
  onDragStart,
  onDoubleClick,
}: FileTreeNodeProps) {
  const isExpanded = expandedPaths.has(node.path);
  const isSelected = selectedPath === node.path;
  const isDirectory = node.type === 'directory';
  const hasChildren = isDirectory && node.children && node.children.length > 0;

  // Handle click on the node
  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    
    if (isDirectory) {
      // Toggle expand/collapse for directories - Requirement 3.6
      onToggle(node.path);
    }
    
    // Always select the node
    onSelect(node);
  }, [isDirectory, node, onToggle, onSelect]);

  // Handle double-click on the node - Requirement 9.1
  const handleDoubleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    
    // Only trigger for files, not directories
    if (!isDirectory && onDoubleClick) {
      onDoubleClick(node);
    }
  }, [isDirectory, node, onDoubleClick]);

  // Handle context menu
  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onContextMenu?.(e, node);
  }, [node, onContextMenu]);

  // Handle drag start for file attachment
  const handleDragStart = useCallback((e: React.DragEvent) => {
    onDragStart?.(e, node);
  }, [node, onDragStart]);

  // Get the appropriate icon for the node
  const getIcon = (): string => {
    if (isDirectory) {
      // Use folder icons for all directories (including Swarm Workspace)
      // Lock icon is shown separately for Swarm Workspace - Requirement 4.2
      return isExpanded ? 'folder_open' : 'folder';
    }
    
    // File icons based on extension
    const ext = node.name.split('.').pop()?.toLowerCase();
    switch (ext) {
      case 'ts':
      case 'tsx':
      case 'js':
      case 'jsx':
        return 'javascript';
      case 'py':
        return 'code';
      case 'json':
        return 'data_object';
      case 'md':
        return 'description';
      case 'css':
      case 'scss':
        return 'style';
      case 'html':
        return 'html';
      case 'svg':
      case 'png':
      case 'jpg':
      case 'jpeg':
      case 'gif':
        return 'image';
      default:
        return 'draft';
    }
  };

  return (
    <div 
      data-testid={`file-tree-node-${node.id}`}
      data-workspace-id={depth === 0 ? node.workspaceId : undefined}
    >
      {/* Node row */}
      <div
        className={`
          flex items-center gap-1 px-2 py-1 cursor-pointer rounded text-sm
          transition-colors select-none
          ${isSelected 
            ? 'bg-[var(--color-primary)] bg-opacity-20 text-[var(--color-text)]' 
            : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          }
          ${node.isSwarmWorkspace && depth === 0 
            ? 'bg-[var(--color-warning)] bg-opacity-5 border-l-2 border-[var(--color-warning)]' 
            : ''
          }
        `}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onContextMenu={handleContextMenu}
        draggable={node.type === 'file'}
        onDragStart={handleDragStart}
        role="treeitem"
        aria-expanded={isDirectory ? isExpanded : undefined}
        aria-selected={isSelected}
        data-testid={`file-tree-row-${node.id}`}
        data-swarm-workspace={node.isSwarmWorkspace ? 'true' : undefined}
      >
        {/* Expand/collapse chevron for directories */}
        {isDirectory && (
          <span 
            className="material-symbols-outlined text-xs w-4 flex-shrink-0"
            data-testid={`chevron-${node.id}`}
          >
            {hasChildren ? (isExpanded ? 'expand_more' : 'chevron_right') : ''}
          </span>
        )}
        
        {/* Spacer for files to align with folders */}
        {!isDirectory && <span className="w-4 flex-shrink-0" />}
        
        {/* File/folder icon */}
        <span 
          className={`material-symbols-outlined text-base flex-shrink-0 ${
            node.isSwarmWorkspace && isDirectory ? 'text-[var(--color-warning)]' : ''
          }`}
        >
          {getIcon()}
        </span>
        
        {/* Name */}
        <span 
          className={`truncate flex-1 ${node.isSwarmWorkspace ? 'italic opacity-80' : ''}`} 
          title={node.name}
        >
          {node.name}
        </span>
        
        {/* Lock icon for Swarm Workspace - Requirement 4.2 */}
        {node.isSwarmWorkspace && (
          <span 
            className="material-symbols-outlined text-sm text-[var(--color-warning)] flex-shrink-0"
            title="Protected system workspace"
            data-testid={`lock-icon-${node.id}`}
          >
            lock
          </span>
        )}
        
        {/* Swarm workspace badge - only at root level */}
        {node.isSwarmWorkspace && depth === 0 && (
          <span 
            className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)] bg-opacity-20 text-[var(--color-warning)]"
            title="Protected system workspace"
          >
            System
          </span>
        )}
      </div>

      {/* Children (recursive) - only render if expanded */}
      {isDirectory && isExpanded && node.children && (
        <div role="group" data-testid={`children-${node.id}`}>
          {node.children.map((child) => (
            <FileTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              onToggle={onToggle}
              onSelect={onSelect}
              selectedPath={selectedPath}
              onContextMenu={onContextMenu}
              onDragStart={onDragStart}
              onDoubleClick={onDoubleClick}
            />
          ))}
        </div>
      )}
    </div>
  );
}
