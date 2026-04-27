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
 * 4. Remaining top-level directories not assigned to any zone
 * 5. Zone separator "System Settings" → .context/, .claude/, config.json, proactive_state.json
 *
 * Requirements: 10.1, 10.2, 10.3, 11.1, 11.4, 15.1, 15.2, 15.3
 */

import React, { useContext, useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { List } from 'react-window';
import type { TreeNode } from '../../types';
import { DEFAULT_WORKSPACE_ID } from '../../types/workspace-config';
import type { FileTreeItem } from './FileTreeNode';
import { useTreeData, useSelection } from '../../contexts/ExplorerContext';
import { toFileTreeItem } from './toFileTreeItem';
import { folderService } from '../../services/workspace';
import { EXPLORER_ATTACH_FILE, EXPLORER_ASK_ABOUT_FILE } from '../../constants/explorerEvents';
import { ToastContext } from '../../contexts/ToastContext';
import TreeNodeRow, { InlineRenameInput } from './TreeNodeRow';
import ZoneSeparator, { SectionHeader } from './ZoneSeparator';
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

/** System files/directories pinned at explorer bottom — dimmed but fully interactive. */
export const SYSTEM_NAMES = new Set(['.context', '.claude', 'config.json', 'proactive_state.json']);

const ROW_HEIGHT = 32;

// ─────────────────────────────────────────────────────────────────────────────
// 3-Tier Section Configuration
// ─────────────────────────────────────────────────────────────────────────────

/** Configuration for a primary or system section in the explorer. */
export interface SectionConfig {
  label: string;
  paths: string[];
  accentBg?: string;
  accentBorder?: string;
  dimmed?: boolean;
  defaultCollapsed?: boolean;
}

/** Primary sections with accent colours — Knowledge and Projects. */
export const EXPLORER_SECTIONS: SectionConfig[] = [
  {
    label: 'Knowledge',
    paths: ['Knowledge'],
    accentBg: 'rgba(234,179,8,0.04)',
    accentBorder: 'rgba(234,179,8,0.15)',
  },
  {
    label: 'Projects',
    paths: ['Projects'],
    accentBg: 'rgba(59,130,246,0.04)',
    accentBorder: 'rgba(59,130,246,0.15)',
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type FlattenedRow =
  | { kind: 'zone-separator'; zoneLabel: string }
  | { kind: 'section-header'; label: string; childCount: number; isCollapsed: boolean; dimmed?: boolean; defaultCollapsed?: boolean; config: SectionConfig }
  | { kind: 'secondary-label'; label: string }
  | { kind: 'node'; node: TreeNode; depth: number; isMatched: boolean; isExpanded: boolean; sectionConfig?: SectionConfig }
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

/** Date prefix pattern: YYYY-MM-DD at the start of a name. */
const DATE_PREFIX_RE = /^\d{4}-\d{2}-\d{2}/;

/** Folders whose children with date-prefixed names should be sorted newest-first. */
const DATE_DESC_ROOTS = new Set(['Knowledge', 'Attachments']);

/** Check if a node's ancestry is under a date-desc root. */
function isUnderDateDescRoot(path: string): boolean {
  const first = path.split('/')[0];
  return DATE_DESC_ROOTS.has(first);
}

/**
 * Recursively flatten a directory node's children into the row list.
 * Only includes children of directories whose path is in `expandedPaths`.
 *
 * When inside Knowledge/ or Attachments/, date-prefixed items are sorted
 * descending (newest first) while preserving dirs-before-files ordering.
 */
function flattenChildren(
  children: TreeNode[],
  depth: number,
  expandedPaths: Set<string>,
  matchedPaths: Set<string>,
  rows: FlattenedRow[],
  sectionConfig?: SectionConfig,
): void {
  // Apply date-descending sort for Knowledge/Attachments subdirectories
  let sorted = children;
  if (children.length > 0 && isUnderDateDescRoot(children[0].path)) {
    const hasDateItems = children.some((c) => DATE_PREFIX_RE.test(c.name));
    if (hasDateItems) {
      sorted = [...children].sort((a, b) => {
        // Dirs before files (preserve backend grouping)
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
        // Both have date prefixes → descending
        const aDate = DATE_PREFIX_RE.test(a.name);
        const bDate = DATE_PREFIX_RE.test(b.name);
        if (aDate && bDate) return b.name.localeCompare(a.name);
        // Date-prefixed items before non-date items
        if (aDate !== bDate) return aDate ? -1 : 1;
        // Fallback: alphabetical ascending
        return a.name.localeCompare(b.name);
      });
    }
  }

  for (const child of sorted) {
    const isExpanded = child.type === 'directory' && expandedPaths.has(child.path);
    rows.push({
      kind: 'node',
      node: child,
      depth,
      isMatched: matchedPaths.has(child.path),
      isExpanded,
      sectionConfig,
    });
    if (isExpanded && child.children) {
      flattenChildren(child.children, depth + 1, expandedPaths, matchedPaths, rows, sectionConfig);
    }
  }
}

/**
 * Flatten a workspace tree into a list of rows with 3-tier visual hierarchy.
 *
 * Ordering:
 * 1. Root-level files matching ROOT_FILES (in ROOT_FILES order)
 * 2. Any other root-level files not in ROOT_FILES and not section folders
 * 3. Dot-directories (non-system) before primary sections
 * 4. For each PRIMARY section (Knowledge, Projects):
 *    a. section-header row (label, childCount, isCollapsed, config)
 *    b. If not collapsed: root dir's CHILDREN at depth 0 (skip root dir row itself)
 *    c. Expanded children recursively at depth+1
 * 5. secondary-label "Other" (only if there are secondary dirs)
 * 6. Remaining dirs as normal tree items (Attachments, Services, etc.)
 * 7. section-header for System { dimmed: true, defaultCollapsed: true }
 * 8. System items (.context, .claude, config.json, proactive_state.json)
 *
 * @param treeData          — Top-level nodes from the workspace tree API
 * @param expandedPaths     — Set of directory paths currently expanded
 * @param matchedPaths      — Set of paths matching the current search query
 * @param sectionCollapsed  — Map of section label → collapsed state
 * @returns Ordered array of FlattenedRow entries
 */
export function flattenTree(
  treeData: TreeNode[],
  expandedPaths: Set<string>,
  matchedPaths: Set<string> = new Set(),
  sectionCollapsed: Record<string, boolean> = {},
): FlattenedRow[] {
  const rows: FlattenedRow[] = [];

  // Build a lookup map for top-level nodes by name for O(1) access
  const topLevelByName = new Map<string, TreeNode>();
  for (const node of treeData) {
    topLevelByName.set(node.name, node);
  }

  // Collect section folder names for exclusion checks
  const sectionFolderNames = new Set<string>();
  for (const section of EXPLORER_SECTIONS) {
    for (const p of section.paths) {
      sectionFolderNames.add(p);
    }
  }
  // Also keep the old zone names set for backward compat
  const zoneFolderNames = sectionFolderNames;
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

  // 2. Other root-level files not in ROOT_FILES, not section folders, not system
  for (const node of treeData) {
    if (
      node.type === 'file' &&
      !rootFileNames.has(node.name) &&
      !zoneFolderNames.has(node.name) &&
      !SYSTEM_NAMES.has(node.name)
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

  // 2b. Dot-directories (non-system) — shown before sections
  for (const node of treeData) {
    if (
      node.type === 'directory' &&
      node.name.startsWith('.') &&
      !zoneFolderNames.has(node.name) &&
      !SYSTEM_NAMES.has(node.name)
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

  // 3. Primary sections (Knowledge, Projects)
  for (const section of EXPLORER_SECTIONS) {
    // Find the root directory node for this section
    const rootNode = section.paths.map((p) => topLevelByName.get(p)).find(
      (n) => n && n.type === 'directory',
    );

    const childCount = rootNode?.children?.length ?? 0;
    const isCollapsed = sectionCollapsed[section.label] ?? false;

    rows.push({
      kind: 'section-header',
      label: section.label,
      childCount,
      isCollapsed,
      dimmed: section.dimmed,
      defaultCollapsed: section.defaultCollapsed,
      config: section,
    });

    // If not collapsed and root node exists, emit its CHILDREN at depth 1
    // (indented under the section header to show hierarchy)
    // Pass sectionConfig so child rows can inherit accent background
    if (!isCollapsed && rootNode && rootNode.children) {
      flattenChildren(rootNode.children, 1, expandedPaths, matchedPaths, rows, section);
    }
  }

  // 4. Secondary dirs (Attachments, Services, any other non-zone, non-system, non-dot dirs)
  const secondaryDirs: TreeNode[] = [];
  for (const node of treeData) {
    if (
      node.type === 'directory' &&
      !zoneFolderNames.has(node.name) &&
      !node.name.startsWith('.') &&
      !SYSTEM_NAMES.has(node.name)
    ) {
      secondaryDirs.push(node);
    }
  }

  if (secondaryDirs.length > 0) {
    rows.push({ kind: 'secondary-label', label: 'Other' });

    for (const node of secondaryDirs) {
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

  // 5. System section — pinned at the bottom (dimmed, collapsed by default)
  const systemConfig: SectionConfig = {
    label: 'System',
    paths: [],
    dimmed: true,
    defaultCollapsed: true,
  };
  const hasSystemItems = treeData.some((n) => SYSTEM_NAMES.has(n.name));
  if (hasSystemItems) {
    const systemNodes = treeData.filter((n) => SYSTEM_NAMES.has(n.name));
    const isSystemCollapsed = sectionCollapsed['System'] ?? systemConfig.defaultCollapsed ?? true;

    rows.push({
      kind: 'section-header',
      label: 'System',
      childCount: systemNodes.length,
      isCollapsed: isSystemCollapsed,
      dimmed: true,
      defaultCollapsed: true,
      config: systemConfig,
    });

    if (!isSystemCollapsed) {
      for (const node of systemNodes) {
        const isExpanded = node.type === 'directory' && expandedPaths.has(node.path);
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
  onSectionToggle: (label: string) => void;
  onSectionContextMenu: (e: React.MouseEvent, sectionLabel: string) => void;
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
    onSectionToggle, onSectionContextMenu,
    onRenameSubmit, onRenameCancel, onDragOverPath, onDropOnFolder,
    onCreateSubmit, onCreateCancel,
  } = props;
  const row = rows[index];

  if (!row) return null;

  if (row.kind === 'zone-separator') {
    return <ZoneSeparator label={row.zoneLabel} style={style} />;
  }

  // ── Section header row (3-tier primary/system) ─────────────────────
  if (row.kind === 'section-header') {
    return (
      <SectionHeader
        label={row.label}
        count={row.childCount}
        isCollapsed={row.isCollapsed}
        dimmed={row.dimmed}
        accentBg={row.config.accentBg}
        accentBorder={row.config.accentBorder}
        onToggle={() => onSectionToggle(row.label)}
        onContextMenu={(e) => onSectionContextMenu(e, row.label)}
        style={style}
      />
    );
  }

  // ── Secondary label row (Other) ────────────────────────────────────
  if (row.kind === 'secondary-label') {
    return (
      <div
        data-testid="secondary-label"
        style={{
          ...style,
          display: 'flex',
          alignItems: 'center',
          padding: '0 12px',
          height: '32px',
          boxSizing: 'border-box',
          userSelect: 'none',
          borderTop: '2px solid var(--color-section-divider, #222236)',
        }}
      >
        <span
          style={{
            fontSize: '10px',
            fontWeight: 600,
            letterSpacing: '0.05em',
            textTransform: 'uppercase',
            color: 'var(--color-text-dim, var(--color-explorer-zone-label))',
          }}
        >
          {row.label}
        </span>
      </div>
    );
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

  // Section accent background for child rows within primary sections
  const sectionBg = row.sectionConfig?.accentBg;

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
      sectionAccentBg={sectionBg}
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

  // ── Section collapse state (persisted in localStorage) ────────────────
  const [sectionCollapsed, setSectionCollapsed] = useState<Record<string, boolean>>(() => {
    const stored: Record<string, boolean> = {};
    try {
      // Primary sections default to expanded, System defaults to collapsed
      for (const section of EXPLORER_SECTIONS) {
        const key = `explorer-section-${section.label}`;
        const val = localStorage.getItem(key);
        stored[section.label] = val !== null ? val === 'true' : (section.defaultCollapsed ?? false);
      }
      // System section
      const sysKey = 'explorer-section-System';
      const sysVal = localStorage.getItem(sysKey);
      stored['System'] = sysVal !== null ? sysVal === 'true' : true;
    } catch {
      // localStorage unavailable (test env) — use defaults
    }
    return stored;
  });

  /** Toggle section collapse and persist to localStorage. */
  const toggleSectionCollapse = useCallback((label: string) => {
    setSectionCollapsed((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      try { localStorage.setItem(`explorer-section-${label}`, String(next[label])); } catch {}
      return next;
    });
  }, []);

  /** Right-click on a section header → open context menu for the root directory. */
  const handleSectionContextMenu = useCallback(
    (e: React.MouseEvent, sectionLabel: string) => {
      e.preventDefault();
      e.stopPropagation();
      // Find the root dir path for this section
      const section = EXPLORER_SECTIONS.find((s) => s.label === sectionLabel);
      const rootDirName = section?.paths[0] ?? sectionLabel;
      returnFocusRef.current = e.currentTarget as HTMLElement;
      setContextMenu({
        isOpen: true,
        x: e.clientX,
        y: e.clientY,
        item: {
          id: rootDirName,
          name: rootDirName,
          type: 'directory',
          path: rootDirName,
          workspaceId: DEFAULT_WORKSPACE_ID,
          workspaceName: 'SwarmWS',
        },
      });
    },
    [],
  );

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

  // Flatten tree into rows — recomputed when tree data, expand, or section collapse state changes.
  // If we're creating a new item, inject a phantom row after the parent directory.
  const rows = useMemo(() => {
    const base = flattenTree(treeData, expandedPaths, matchedPaths, sectionCollapsed);
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
  }, [treeData, expandedPaths, matchedPaths, creatingItem, sectionCollapsed]);

  // Stable rowProps object for the row renderer
  const rowProps = useMemo<RowCustomProps>(
    () => ({
      rows, selectedPath, renamingPath, dragOverPath, toggleExpand, setSelectedPath,
      onFileDoubleClick, onContextMenu: handleContextMenu, onAttachToChat: handleAttachToChat,
      onSectionToggle: toggleSectionCollapse, onSectionContextMenu: handleSectionContextMenu,
      onRenameSubmit: handleRenameSubmit, onRenameCancel: handleRenameCancel,
      onDragOverPath: setDragOverPath, onDropOnFolder: handleDropOnFolder,
      onCreateSubmit: handleCreateSubmit, onCreateCancel: handleCreateCancel,
    }),
    [rows, selectedPath, renamingPath, dragOverPath, toggleExpand, setSelectedPath, onFileDoubleClick, handleContextMenu, handleAttachToChat, toggleSectionCollapse, handleSectionContextMenu, handleRenameSubmit, handleRenameCancel, handleDropOnFolder, handleCreateSubmit, handleCreateCancel],
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
