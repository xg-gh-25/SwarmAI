/**
 * TreeNodeRow — renders a single tree node row inside the virtualized explorer list.
 *
 * This component is the leaf renderer for `VirtualizedTree`. It receives its
 * position and dimensions from `react-window` via the `style` prop and all
 * semantic data via explicit props (no context subscriptions — the parent
 * `VirtualizedTree` reads context and passes values down).
 *
 * Key exports:
 * - `TreeNodeRow`          — The memoised row component
 * - `TreeNodeRowProps`     — Prop interface consumed by `VirtualizedTree`
 *
 * Visual behaviour:
 * - Depth-based indentation (depth × 16 px) with optional indent guides
 * - Font-weight 500 at depth 0, 400 at depth 1+
 * - System-managed items show a lock badge and muted text; no CRUD actions
 * - User-managed items show accent colour on hover and CRUD icons on hover
 * - Search-matched rows get a highlight background
 * - Selected rows get a primary-colour background at 20 % opacity
 * - Chevron rotates with a 150 ms CSS transition
 * - Full ARIA tree-item attributes for accessibility
 *
 * Requirements: 11.2, 11.3, 14.1, 14.2, 14.4, 14.5, 14.6
 */

import React, { useCallback } from 'react';
import type { TreeNode } from '../../types';
import type { FileTreeItem } from './FileTreeNode';
import { fileIcon, fileIconColor, gitStatusColor, gitStatusBadge } from '../../utils/fileUtils';

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface TreeNodeRowProps {
  node: TreeNode;
  depth: number;
  isExpanded: boolean;
  isSelected: boolean;
  isMatched: boolean;
  onToggle: () => void;
  onSelect: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onDoubleClick: () => void;
  /** Positioning style injected by react-window (top, height, position). */
  style: React.CSSProperties;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const INDENT_PX = 16;
const CHEVRON_TRANSITION = 'transform 150ms ease';

/** Common hidden / generated file patterns that should appear dimmed. */
const HIDDEN_PATTERNS = [
  /^\./, // dotfiles: .gitignore, .env, .DS_Store, .eslintrc, etc.
  /^__/, // __pycache__, __tests__ (convention-based)
  /\.lock$/, // package-lock.json, yarn.lock, etc.
  /\.map$/, // source maps
  /\.d\.ts$/, // type declaration files
];

/** Returns true if the file/folder name matches a hidden/generated pattern. */
function isHiddenNode(name: string): boolean {
  return HIDDEN_PATTERNS.some((re) => re.test(name));
}



/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const TreeNodeRow: React.FC<TreeNodeRowProps> = React.memo(function TreeNodeRow({
  node,
  depth,
  isExpanded,
  isSelected,
  isMatched,
  onToggle,
  onSelect,
  onContextMenu,
  onDoubleClick,
  style,
}) {
  const isDirectory = node.type === 'directory';

  /* ---- drag handler ---- */

  const handleDragStart = useCallback((e: React.DragEvent) => {
    if (isDirectory) {
      e.preventDefault();
      return;
    }
    const payload: FileTreeItem = {
      id: node.path,
      name: node.name,
      type: node.type as 'file',
      path: node.path,
      workspaceId: '',
      workspaceName: '',
      gitStatus: node.gitStatus,
    };
    e.dataTransfer.setData('application/json', JSON.stringify(payload));
    e.dataTransfer.effectAllowed = 'copy';

    // Custom drag ghost using textContent (not innerHTML) to prevent XSS
    const ghost = document.createElement('div');
    ghost.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 10px;' +
      'background:var(--color-card);border:1px solid var(--color-border);' +
      'border-radius:6px;font-size:13px;color:var(--color-text);' +
      'position:absolute;top:-1000px;';
    const iconSpan = document.createElement('span');
    iconSpan.className = 'material-symbols-outlined';
    iconSpan.style.fontSize = '16px';
    iconSpan.textContent = fileIcon(node.name);
    ghost.appendChild(iconSpan);
    ghost.appendChild(document.createTextNode(node.name));
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 0, 0);
    requestAnimationFrame(() => document.body.removeChild(ghost));
  }, [isDirectory, node]);

  /* ---- event handlers ---- */

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (isDirectory) onToggle();
      onSelect();
    },
    [isDirectory, onToggle, onSelect],
  );

  const handleDoubleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onDoubleClick();
    },
    [onDoubleClick],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      onContextMenu(e);
    },
    [onContextMenu],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        if (isDirectory) {
          onToggle();
        } else {
          onDoubleClick();
        }
      } else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
        e.preventDefault();
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        onContextMenu({
          clientX: rect.left + rect.width / 2,
          clientY: rect.top + rect.height / 2,
          preventDefault: () => {},
          stopPropagation: () => {},
        } as unknown as React.MouseEvent);
      }
    },
    [isDirectory, onToggle, onDoubleClick, onContextMenu],
  );

  const handleChevronClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onToggle();
    },
    [onToggle],
  );

  /* ---- derived styles ---- */

  const paddingLeft = depth * INDENT_PX + 8; // 8 px base padding
  const fontWeight = depth === 0 ? 500 : 400;

  // Build the background colour — priority: selected > matched > default
  let backgroundColor: string | undefined;
  if (isSelected) {
    // --color-sidebar-icon-active is the primary accent; 20 % opacity
    backgroundColor = 'color-mix(in srgb, var(--color-sidebar-icon-active) 20%, transparent)';
  } else if (isMatched) {
    backgroundColor = 'var(--color-explorer-search-highlight)';
  }

  // Git status drives text color; fall back to default
  const statusColor = gitStatusColor(node.gitStatus);
  const isHidden = !statusColor && isHiddenNode(node.name);
  const textColor = statusColor
    ?? (isHidden ? 'var(--color-hidden-text)' : 'var(--color-text)');
  const badge = gitStatusBadge(node.gitStatus);
  const rowOpacity = (node.gitStatus === 'ignored' || isHidden) ? 0.7 : 1;

  // Icon color: git status > hidden dimming > file-type color
  const iconColor = statusColor
    ?? (isHidden
      ? 'var(--color-hidden-icon)'
      : (isDirectory
        ? (isExpanded ? 'var(--color-icon-folder-open)' : 'var(--color-icon-folder)')
        : fileIconColor(node.name)));

  /* ---- render ---- */

  return (
    <div
      data-testid="tree-row"
      role="treeitem"
      aria-level={depth + 1}
      aria-expanded={isDirectory ? isExpanded : undefined}
      aria-selected={isSelected}
      tabIndex={isSelected ? 0 : -1}
      className="tree-node-row"
      draggable={!isDirectory}
      onDragStart={handleDragStart}
      style={{
        ...style,
        paddingLeft,
        fontWeight,
        backgroundColor,
        color: textColor,
        opacity: rowOpacity,
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        paddingRight: '8px',
        cursor: 'pointer',
        userSelect: 'none',
        fontSize: '13px',
        lineHeight: '32px',
        position: style.position,
        boxSizing: 'border-box',
      }}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      onKeyDown={handleKeyDown}
    >
      {/* Indent guides — one vertical line per ancestor depth */}
      {Array.from({ length: depth }, (_, i) => (
        <span
          key={i}
          aria-hidden="true"
          style={{
            position: 'absolute',
            left: i * INDENT_PX + 8 + INDENT_PX / 2,
            top: 0,
            bottom: 0,
            width: '1px',
            backgroundColor: 'var(--color-explorer-indent-guide)',
            pointerEvents: 'none',
          }}
        />
      ))}

      {/* Expand / collapse chevron (directories only) */}
      {isDirectory ? (
        <span
          className="material-symbols-outlined"
          data-testid="tree-chevron"
          onClick={handleChevronClick}
          aria-hidden="true"
          style={{
            fontSize: '16px',
            width: '16px',
            flexShrink: 0,
            transition: CHEVRON_TRANSITION,
            transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          chevron_right
        </span>
      ) : (
        /* Spacer to keep files aligned with folder names */
        <span style={{ width: '16px', flexShrink: 0 }} aria-hidden="true" />
      )}

      {/* Node icon — with symlink overlay for linked directories */}
      <span
        style={{ position: 'relative', display: 'inline-flex', flexShrink: 0 }}
        aria-hidden="true"
      >
        <span
          className="material-symbols-outlined"
          style={{
            fontSize: '16px',
            color: iconColor,
          }}
        >
          {isDirectory ? (isExpanded ? 'folder_open' : 'folder') : fileIcon(node.name)}
        </span>
        {node.isSymlink && (
          <span
            className="material-symbols-outlined"
            title="Linked folder"
            style={{
              fontSize: '10px',
              position: 'absolute',
              bottom: '-2px',
              right: '-4px',
              color: 'var(--color-text-muted)',
            }}
          >
            link
          </span>
        )}
      </span>

      {/* Node name */}
      <span
        style={{
          flex: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={node.name}
      >
        {node.name}
      </span>

      {/* Git status badge (A/M/D/U/R/C) — always visible when status is set */}
      {badge && (
        <span
          data-testid="git-status-badge"
          title={node.gitStatus}
          style={{
            fontSize: '10px',
            fontWeight: 600,
            lineHeight: '16px',
            padding: '0 4px',
            borderRadius: '3px',
            color: badge.color,
            backgroundColor: badge.bg,
            flexShrink: 0,
            letterSpacing: '0.02em',
            fontFamily: 'monospace',
          }}
        >
          {badge.label}
        </span>
      )}

      {/* CRUD action icons — visible on hover only */}
      <span
          className="tree-node-actions"
          data-testid="crud-actions"
          aria-label="Actions"
          style={{
            display: 'none', /* shown via CSS :hover on parent */
            gap: '2px',
            flexShrink: 0,
            alignItems: 'center',
          }}
        >
          {isDirectory && (
            <span
              className="material-symbols-outlined"
              style={{
                fontSize: '14px',
                color: 'var(--color-explorer-accent)',
                cursor: 'pointer',
              }}
              title="New item"
            >
              add
            </span>
          )}
          <span
            className="material-symbols-outlined"
            style={{
              fontSize: '14px',
              color: 'var(--color-explorer-accent)',
              cursor: 'pointer',
            }}
            title="More actions"
          >
            more_horiz
          </span>
        </span>
    </div>
  );
});

export default TreeNodeRow;
