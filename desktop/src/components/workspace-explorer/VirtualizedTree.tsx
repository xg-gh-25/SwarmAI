/**
 * VirtualizedTree — renders the workspace explorer as a virtualized flat list.
 *
 * This is the core rendering component for the SwarmWS Workspace Explorer.
 * It flattens the hierarchical `TreeNode[]` data into a list of
 * `FlattenedRow` entries, injecting semantic zone separators at the correct
 * positions, and delegates rendering to `react-window` v2 `List` for
 * efficient viewport-only DOM rendering.
 *
 * Key exports:
 * - `VirtualizedTree`   — The main component (default export)
 * - `flattenTree`       — Pure flattening algorithm (exported for property tests)
 * - `SEMANTIC_ZONES`    — Zone configuration constant
 * - `ROOT_FILES`        — Root-level file names displayed above zones
 * - `FlattenedRow`      — Discriminated union type for row data
 *
 * Flattening algorithm (semantic zone ordering):
 * 1. Root-level files (system-prompts.md, context-L0.md, context-L1.md) first
 * 2. Zone separator "Shared Knowledge" → Knowledge/ folder and expanded children
 * 3. Zone separator "Active Work" → Projects/ folder and expanded children
 *
 * Requirements: 10.1, 10.2, 10.3, 11.1, 11.4, 15.1, 15.2, 15.3
 */

import React, { useContext, useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { List } from 'react-window';
import type { TreeNode } from '../../types';
import type { FileTreeItem } from './FileTreeNode';
import { useTreeData, useSelection } from '../../contexts/ExplorerContext';
import { toFileTreeItem } from './toFileTreeItem';
import { folderService } from '../../services/workspace';
import { EXPLORER_ATTACH_FILE, EXPLORER_ASK_ABOUT_FILE } from '../../constants/explorerEvents';
import { ToastContext } from '../../contexts/ToastContext';
import TreeNodeRow, { InlineRenameInput } from './TreeNodeRow';
import ZoneSeparator from './ZoneSeparator';
import FileContextMenu from './FileContextMenu';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/** Zone configuration — maps filesystem paths to semantic zones. */
export const SEMANTIC_ZONES = [
  { label: 'Shared Knowledge', paths: ['Knowledge'] },
  { label: 'Active Work', paths: ['Projects'] },
] as const;

/** Root-level files displayed above the first zone separator. */
export const ROOT_FILES: string[] = [];

const ROW_HEIGHT = 32;

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type FlattenedRow =
  | { kind: 'zone-separator'; zoneLabel: string }
  | { kind: 'node'; node: TreeNode; depth: number; isMatched: boolean; isExpanded: boolean }
  | { kind: 'creating'; parentPath: string; itemType: 'file' | 'directory'; depth: number };

export interface VirtualizedTreeProps {
  /** Height of the tree container (from parent layout / AutoSizer). */
  height: number;
  /** Width of the tree container. */
  width: number;
  /** Callback when a file node is double-clicked (e.g., to open in editor). */
  onFileDoubleClick?: (node: FileTreeItem) => void;
  /** Callback when "Attach to Chat" is selected from the context menu. */
  onAttachToChat?: (item: FileTreeItem) => void;
}

// ─────────────────────────────────────────────────────────────────────────────
// Context menu state
// ─────────────────────────────────────────────────────────────────────────────

/** State for the right-click context menu rendered via portal. */
export interface ContextMenuState {
  isOpen: boolean;
  x: number;
  y: number;
  item: FileTreeItem | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Flattening algorithm
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Recursively flatten a directory node's children into the row list.
 * Only includes children of directories whose path is in `expandedPaths`.
 */
function flattenChildren(
  children: TreeNode[],
  depth: number,
  expandedPaths: Set<string>,
  matchedPaths: Set<string>,
  rows: FlattenedRow[],
): void {
  for (const child of children) {
    const isExpanded = child.type === 'directory' && expandedPaths.has(child.path);
    rows.push({
      kind: 'node',
      node: child,
      depth,
      isMatched: matchedPaths.has(child.path),
      isExpanded,
    });
    if (isExpanded && child.children) {
      flattenChildren(child.children, depth + 1, expandedPaths, matchedPaths, rows);
    }
  }
}

/**
 * Flatten a workspace tree into a list of rows with semantic zone ordering.
 *
 * Ordering:
 * 1. Root-level files matching ROOT_FILES (in ROOT_FILES order)
 * 2. Any other root-level files not in ROOT_FILES and not zone folders
 * 3. For each SEMANTIC_ZONE:
 *    a. Zone separator row
 *    b. Top-level directories matching the zone's paths
 *    c. Recursively expanded children (if directory is in expandedPaths)
 * 4. Any remaining top-level directories not assigned to a zone
 *
 * @param treeData       — Top-level nodes from the workspace tree API
 * @param expandedPaths  — Set of directory paths currently expanded
 * @param matchedPaths   — Set of paths matching the current search query
 * @returns Ordered array of FlattenedRow entries
 */
export function flattenTree(
  treeData: TreeNode[],
  expandedPaths: Set<string>,
  matchedPaths: Set<string> = new Set(),
): FlattenedRow[] {
  const rows: FlattenedRow[] = [];

  // Build a lookup map for top-level nodes by name for O(1) access
  const topLevelByName = new Map<string, TreeNode>();
  for (const node of treeData) {
    topLevelByName.set(node.name, node);
  }

  // Collect zone folder names for exclusion checks
  const zoneFolderNames = new Set<string>();
  for (const zone of SEMANTIC_ZONES) {
    for (const p of zone.paths) {
      zoneFolderNames.add(p);
    }
  }
  const rootFileNames = new Set(ROOT_FILES);

  // 1. Root-level files in ROOT_FILES order
  for (const fileName of ROOT_FILES) {
    const node = topLevelByName.get(fileName);
    if (node && node.type === 'file') {
      rows.push({
        kind: 'node',
        node,
        depth: 0,
        isMatched: matchedPaths.has(node.path),
        isExpanded: false,
      });
    }
  }

  // 2. Other root-level files not in ROOT_FILES and not zone folders
  for (const node of treeData) {
    if (
      node.type === 'file' &&
      !rootFileNames.has(node.name) &&
      !zoneFolderNames.has(node.name)
    ) {
      rows.push({
        kind: 'node',
        node,
        depth: 0,
        isMatched: matchedPaths.has(node.path),
        isExpanded: false,
      });
    }
  }

  // 2b. Dot-directories (e.g. .claude, .context) — shown before zones
  for (const node of treeData) {
    if (
      node.type === 'directory' &&
      node.name.startsWith('.') &&
      !zoneFolderNames.has(node.name)
    ) {
      const isExpanded = expandedPaths.has(node.path);
      rows.push({
        kind: 'node',
        node,
        depth: 0,
        isMatched: matchedPaths.has(node.path),
        isExpanded,
      });
      if (isExpanded && node.children) {
        flattenChildren(node.children, 1, expandedPaths, matchedPaths, rows);
      }
    }
  }

  // 3. Semantic zones
  for (const zone of SEMANTIC_ZONES) {
    rows.push({ kind: 'zone-separator', zoneLabel: zone.label });

    for (const folderName of zone.paths) {
      const node = topLevelByName.get(folderName);
      if (node && node.type === 'directory') {
        const isExpanded = expandedPaths.has(node.path);
        rows.push({
          kind: 'node',
          node,
          depth: 0,
          isMatched: matchedPaths.has(node.path),
          isExpanded,
        });
        if (isExpanded && node.children) {
          flattenChildren(node.children, 1, expandedPaths, matchedPaths, rows);
        }
      }
    }
  }

  // 4. Remaining top-level directories not assigned to a zone or dot-dirs
  for (const node of treeData) {
    if (
      node.type === 'directory' &&
      !zoneFolderNames.has(node.name) &&
      !node.name.startsWith('.')
    ) {
      const isExpanded = expandedPaths.has(node.path);
      rows.push({
        kind: 'node',
        node,
        depth: 0,
        isMatched: matchedPaths.has(node.path),
        isExpanded,
      });
      if (isExpanded && node.children) {
        flattenChildren(node.children, 1, expandedPaths, matchedPaths, rows);
      }
    }
  }

  return rows;
}

// ─────────────────────────────────────────────────────────────────────────────
// Row renderer (react-window v2 rowComponent)
// ─────────────────────────────────────────────────────────────────────────────

/** Custom props passed to the row component via List's rowProps. */
interface RowCustomProps {
  rows: FlattenedRow[];
  selectedPath: string | null;
  renamingPath: string | null;
  dragOverPath: string | null;
  toggleExpand: (path: string) => void;
  setSelectedPath: (path: string | null) => void;
  onFileDoubleClick?: (node: FileTreeItem) => void;
  onContextMenu: (e: React.MouseEvent, node: TreeNode) => void;
  onAttachToChat?: (item: FileTreeItem) => void;
  onRenameSubmit: (oldPath: string, newName: string) => void;
  onRenameCancel: () => void;
  onDragOverPath: (path: string | null) => void;
  onDropOnFolder: (targetDirPath: string, e: React.DragEvent) => void;
  onCreateSubmit: (name: string) => void;
  onCreateCancel: () => void;
}

/**
 * Row component for react-window v2 List.
 *
 * Receives `index`, `style`, and `ariaAttributes` from react-window,
 * plus our custom `RowCustomProps` via `rowProps`.
 */
function RowRenderer(props: {
  ariaAttributes: { 'aria-posinset': number; 'aria-setsize': number; role: 'listitem' };
  index: number;
  style: React.CSSProperties;
} & RowCustomProps) {
  const {
    index, style, rows, selectedPath, renamingPath, dragOverPath,
    toggleExpand, setSelectedPath, onFileDoubleClick, onContextMenu,
    onRenameSubmit, onRenameCancel, onDragOverPath, onDropOnFolder,
    onCreateSubmit, onCreateCancel,
  } = props;
  const row = rows[index];

  if (!row) return null;

  if (row.kind === 'zone-separator') {
    return <ZoneSeparator label={row.zoneLabel} style={style} />;
  }

  // ── Creating phantom row ────────────────────────────────────────────
  if (row.kind === 'creating') {
    const iconName = row.itemType === 'directory' ? 'folder' : 'note_add';
    const iconColor = row.itemType === 'directory'
      ? 'var(--color-icon-folder)'
      : 'var(--color-explorer-accent)';
    return (
      <div
        style={{
          ...style,
          paddingLeft: row.depth * 16 + 8,
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          paddingRight: '8px',
          fontSize: '13px',
          lineHeight: '32px',
          boxSizing: 'border-box',
        }}
      >
        {/* Spacer to align with chevron */}
        <span style={{ width: '16px', flexShrink: 0 }} />
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '16px', color: iconColor, flexShrink: 0 }}
        >
          {iconName}
        </span>
        <InlineRenameInput name="" onSubmit={onCreateSubmit} onCancel={onCreateCancel} />
      </div>
    );
  }

  // ── Normal node row ─────────────────────────────────────────────────
  const { node, depth, isMatched, isExpanded } = row;

  /** Bridge TreeNode → FileTreeItem for the file editor modal. */
  const handleDoubleClick = useCallback(() => {
    if (node.type === 'file' && onFileDoubleClick) {
      onFileDoubleClick(toFileTreeItem(node));
    }
  }, [node, onFileDoubleClick]);

  const handleToggle = useCallback(() => toggleExpand(node.path), [toggleExpand, node.path]);
  const handleSelect = useCallback(() => setSelectedPath(node.path), [setSelectedPath, node.path]);
  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => onContextMenu(e, node),
    [onContextMenu, node],
  );

  const handleRenameSubmit = useCallback(
    (newName: string) => onRenameSubmit(node.path, newName),
    [onRenameSubmit, node.path],
  );

  // Drag-over handlers for directory drop targets
  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      onDragOverPath(node.path);
    },
    [onDragOverPath, node.path],
  );

  const handleDragLeave = useCallback(
    (e: React.DragEvent) => {
      // Only clear if leaving the actual row element (not entering a child)
      if (e.currentTarget.contains(e.relatedTarget as Node)) return;
      onDragOverPath(null);
    },
    [onDragOverPath],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      onDragOverPath(null);
      onDropOnFolder(node.path, e);
    },
    [onDragOverPath, onDropOnFolder, node.path],
  );

  return (
    <TreeNodeRow
      node={node}
      depth={depth}
      isExpanded={isExpanded}
      isSelected={selectedPath === node.path}
      isMatched={isMatched}
      isRenaming={renamingPath === node.path}
      isDragOver={dragOverPath === node.path}
      onToggle={handleToggle}
      onSelect={handleSelect}
      onContextMenu={handleContextMenu}
      onDoubleClick={handleDoubleClick}
      onRenameSubmit={handleRenameSubmit}
      onRenameCancel={onRenameCancel}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      style={style}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

const VirtualizedTree: React.FC<VirtualizedTreeProps> = ({ height, width, onFileDoubleClick, onAttachToChat }) => {
  const { treeData, refreshTree } = useTreeData();
  const { expandedPaths, matchedPaths, selectedPath, toggleExpand, setSelectedPath } =
    useSelection();
  // Safe: useContext returns undefined when ToastProvider is missing (no throw in tests)
  const toastCtx = useContext(ToastContext);

  // ── Context menu state ──────────────────────────────────────────────────
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({
    isOpen: false,
    x: 0,
    y: 0,
    item: null,
  });

  // ── Rename state ────────────────────────────────────────────────────────
  const [renamingPath, setRenamingPath] = useState<string | null>(null);

  // ── Drag-and-drop state ────────────────────────────────────────────────
  const [dragOverPath, setDragOverPath] = useState<string | null>(null);

  // ── Creating (new file/folder) state ───────────────────────────────────
  const [creatingItem, setCreatingItem] = useState<{
    parentPath: string;
    itemType: 'file' | 'directory';
  } | null>(null);

  /** Ref to the element that had focus when the context menu opened. */
  const returnFocusRef = useRef<HTMLElement | null>(null);

  /** Open the context menu at the cursor position for the given node. */
  const handleContextMenu = useCallback(
    (e: React.MouseEvent, node: TreeNode) => {
      e.preventDefault();
      e.stopPropagation();
      returnFocusRef.current = e.currentTarget as HTMLElement;
      setContextMenu({
        isOpen: true,
        x: e.clientX,
        y: e.clientY,
        item: toFileTreeItem(node),
      });
    },
    [],
  );

  /** Close the context menu and reset state. */
  const closeContextMenu = useCallback(() => {
    setContextMenu({ isOpen: false, x: 0, y: 0, item: null });
  }, []);

  // Close context menu on any scroll event (capture phase catches nested scrolls)
  useEffect(() => {
    if (!contextMenu.isOpen) return;
    const handleScroll = () => closeContextMenu();
    window.addEventListener('scroll', handleScroll, true);
    return () => window.removeEventListener('scroll', handleScroll, true);
  }, [contextMenu.isOpen, closeContextMenu]);

  // ── Rename handlers ─────────────────────────────────────────────────────

  /** Context menu "Rename" → activate inline rename on the tree node. */
  const handleRenameRequest = useCallback((item: FileTreeItem) => {
    setRenamingPath(item.path);
  }, []);

  /** Inline input submitted → call backend rename → refresh tree.
   *  On failure, closes the input — the unchanged name in the tree signals
   *  the rename didn't take. User can right-click → Rename to retry. */
  const handleRenameSubmit = useCallback(
    async (oldPath: string, newName: string) => {
      const oldName = oldPath.split('/').pop() ?? '';
      if (newName === oldName || !newName.trim()) {
        setRenamingPath(null);
        return;
      }
      const parentDir = oldPath.includes('/') ? oldPath.substring(0, oldPath.lastIndexOf('/')) : '';
      const newPath = parentDir ? `${parentDir}/${newName}` : newName;
      try {
        await folderService.renameItem(oldPath, newPath);
        refreshTree();
      } catch (err) {
        console.error('Rename failed:', err);
        toastCtx?.addToast({
          severity: 'error',
          message: `Rename failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
        });
      }
      setRenamingPath(null);
    },
    [refreshTree, toastCtx],
  );

  /** Inline input cancelled → clear rename state. */
  const handleRenameCancel = useCallback(() => {
    setRenamingPath(null);
  }, []);

  // ── Delete handler ──────────────────────────────────────────────────────

  /** Delete confirmed in context menu → trash via backend → refresh tree.
   *  Re-throws on failure so FileContextMenu can display the error. */
  const handleDelete = useCallback(
    async (item: FileTreeItem) => {
      await folderService.trashItem(item.path);
      refreshTree();
    },
    [refreshTree],
  );

  // ── Ask Swarm handler ───────────────────────────────────────────────────

  /** "Ask Swarm about this file" → dispatch custom event for ChatPage. */
  const handleAskAbout = useCallback((item: FileTreeItem) => {
    window.dispatchEvent(new CustomEvent(EXPLORER_ASK_ABOUT_FILE, { detail: item }));
  }, []);

  /** "Attach to Chat" → dispatch custom event for ChatPage. */
  const handleAttachToChat = useCallback((item: FileTreeItem) => {
    if (onAttachToChat) {
      onAttachToChat(item);
    } else {
      // Fallback: dispatch event for ChatPage to handle
      window.dispatchEvent(new CustomEvent(EXPLORER_ATTACH_FILE, { detail: item }));
    }
  }, [onAttachToChat]);

  // ── Drag-and-drop handlers ──────────────────────────────────────────────

  /** Clear drag-over state on global dragend (e.g. user drops outside tree). */
  useEffect(() => {
    const clear = () => setDragOverPath(null);
    window.addEventListener('dragend', clear);
    return () => window.removeEventListener('dragend', clear);
  }, []);

  /** Handle a drop onto a folder — move the dragged item into it. */
  const handleDropOnFolder = useCallback(
    async (targetDirPath: string, e: React.DragEvent) => {
      const sourcePath = e.dataTransfer.getData('text/x-swarm-tree-path');
      if (!sourcePath) return;

      // Guard: no self-drop, no drop on own parent, no circular move
      const sourceParent = sourcePath.includes('/') ? sourcePath.substring(0, sourcePath.lastIndexOf('/')) : '';
      if (sourcePath === targetDirPath) return;
      if (sourceParent === targetDirPath) return;
      if (targetDirPath.startsWith(sourcePath + '/')) return;

      try {
        await folderService.moveItem(sourcePath, targetDirPath);
        refreshTree();
      } catch (err) {
        console.error('Move failed:', err);
        toastCtx?.addToast({
          severity: 'error',
          message: `Move failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
        });
      }
    },
    [refreshTree, toastCtx],
  );

  // ── New file/folder handlers ───────────────────────────────────────────

  /** Context menu "New File" or "New Folder" → show phantom row. */
  const handleNewFile = useCallback((item: FileTreeItem) => {
    // Auto-expand the parent directory
    if (!expandedPaths.has(item.path)) {
      toggleExpand(item.path);
    }
    setCreatingItem({ parentPath: item.path, itemType: 'file' });
  }, [expandedPaths, toggleExpand]);

  const handleNewFolder = useCallback((item: FileTreeItem) => {
    if (!expandedPaths.has(item.path)) {
      toggleExpand(item.path);
    }
    setCreatingItem({ parentPath: item.path, itemType: 'directory' });
  }, [expandedPaths, toggleExpand]);

  /** Phantom row inline input submitted → create file or folder. */
  const handleCreateSubmit = useCallback(
    async (name: string) => {
      if (!creatingItem || !name.trim()) {
        setCreatingItem(null);
        return;
      }
      const fullPath = creatingItem.parentPath
        ? `${creatingItem.parentPath}/${name}`
        : name;
      try {
        if (creatingItem.itemType === 'directory') {
          await folderService.createFolder(fullPath);
        } else {
          await folderService.createFile(fullPath);
        }
        refreshTree();
      } catch (err) {
        console.error('Create failed:', err);
        toastCtx?.addToast({
          severity: 'error',
          message: `Create failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
        });
      }
      setCreatingItem(null);
    },
    [creatingItem, refreshTree, toastCtx],
  );

  const handleCreateCancel = useCallback(() => {
    setCreatingItem(null);
  }, []);

  // Flatten tree into rows — recomputed when tree data or expand state changes.
  // If we're creating a new item, inject a phantom row after the parent directory.
  const rows = useMemo(() => {
    const base = flattenTree(treeData, expandedPaths, matchedPaths);
    if (!creatingItem) return base;

    // Find the parent directory row and inject phantom after it
    const parentIdx = base.findIndex(
      (r) => r.kind === 'node' && r.node.path === creatingItem.parentPath,
    );
    if (parentIdx === -1) return base;

    const parentRow = base[parentIdx];
    const parentDepth = parentRow.kind === 'node' ? parentRow.depth : 0;
    const phantomRow: FlattenedRow = {
      kind: 'creating',
      parentPath: creatingItem.parentPath,
      itemType: creatingItem.itemType,
      depth: parentDepth + 1,
    };

    // Insert right after the parent (before its children)
    const result = [...base];
    result.splice(parentIdx + 1, 0, phantomRow);
    return result;
  }, [treeData, expandedPaths, matchedPaths, creatingItem]);

  // Stable rowProps object for the row renderer
  const rowProps = useMemo<RowCustomProps>(
    () => ({
      rows, selectedPath, renamingPath, dragOverPath, toggleExpand, setSelectedPath,
      onFileDoubleClick, onContextMenu: handleContextMenu, onAttachToChat: handleAttachToChat,
      onRenameSubmit: handleRenameSubmit, onRenameCancel: handleRenameCancel,
      onDragOverPath: setDragOverPath, onDropOnFolder: handleDropOnFolder,
      onCreateSubmit: handleCreateSubmit, onCreateCancel: handleCreateCancel,
    }),
    [rows, selectedPath, renamingPath, dragOverPath, toggleExpand, setSelectedPath, onFileDoubleClick, handleContextMenu, handleAttachToChat, handleRenameSubmit, handleRenameCancel, handleDropOnFolder, handleCreateSubmit, handleCreateCancel],
  );

  return (
    <>
      <List
        style={{ height, width, overflow: 'auto' }}
        rowCount={rows.length}
        rowHeight={ROW_HEIGHT}
        rowComponent={RowRenderer}
        rowProps={rowProps}
        role="tree"
        aria-label="Workspace Explorer"
      />
      {contextMenu.isOpen && contextMenu.item && createPortal(
        <FileContextMenu
          item={contextMenu.item}
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={closeContextMenu}
          onOpenFile={onFileDoubleClick}
          onAttachToChat={handleAttachToChat}
          onRename={handleRenameRequest}
          onDelete={handleDelete}
          onFileSystemChange={refreshTree}
          onAskAbout={handleAskAbout}
          onNewFile={handleNewFile}
          onNewFolder={handleNewFolder}
          returnFocusRef={returnFocusRef}
        />,
        document.body,
      )}
    </>
  );
};

export default VirtualizedTree;
