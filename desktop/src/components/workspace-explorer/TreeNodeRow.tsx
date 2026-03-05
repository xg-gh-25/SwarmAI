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
import type { TreeNode, GitStatus } from '../../types';

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

/** Map git status to the CSS variable name for text/icon color. */
function gitStatusColor(status?: GitStatus): string | undefined {
  if (!status) return undefined;
  const map: Record<GitStatus, string> = {
    added: 'var(--color-git-added)',
    modified: 'var(--color-git-modified)',
    deleted: 'var(--color-git-deleted)',
    renamed: 'var(--color-git-renamed)',
    untracked: 'var(--color-git-untracked)',
    conflicting: 'var(--color-git-conflicting)',
    ignored: 'var(--color-git-ignored)',
  };
  return map[status];
}

/** Map git status to a short badge label (like VS Code / Kiro). */
function gitStatusBadge(status?: GitStatus): { label: string; color: string; bg: string } | null {
  if (!status) return null;
  const badges: Record<GitStatus, { label: string; color: string; bg: string }> = {
    added:       { label: 'A', color: 'var(--color-git-added)',       bg: 'var(--color-git-badge-added-bg)' },
    modified:    { label: 'M', color: 'var(--color-git-modified)',    bg: 'var(--color-git-badge-modified-bg)' },
    deleted:     { label: 'D', color: 'var(--color-git-deleted)',     bg: 'var(--color-git-badge-deleted-bg)' },
    renamed:     { label: 'R', color: 'var(--color-git-renamed)',     bg: 'var(--color-git-badge-renamed-bg)' },
    untracked:   { label: 'U', color: 'var(--color-git-untracked)',   bg: 'var(--color-git-badge-untracked-bg)' },
    conflicting: { label: 'C', color: 'var(--color-git-conflicting)', bg: 'var(--color-git-badge-conflicting-bg)' },
    ignored:     { label: 'I', color: 'var(--color-git-ignored)',     bg: 'var(--color-git-badge-ignored-bg, transparent)' },
  };
  return badges[status];
}

/** Return a Material Symbols icon name based on file extension. */
function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'md':
      return 'description';
    case 'json':
      return 'data_object';
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
      return 'javascript';
    case 'py':
      return 'code';
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
}

/** Return a CSS variable for the file-type icon color. */
function fileIconColor(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'ts':
    case 'tsx':
      return 'var(--color-icon-typescript)';
    case 'js':
    case 'jsx':
      return 'var(--color-icon-javascript)';
    case 'py':
      return 'var(--color-icon-python)';
    case 'css':
    case 'scss':
      return 'var(--color-icon-css)';
    case 'html':
      return 'var(--color-icon-html)';
    case 'json':
      return 'var(--color-icon-json)';
    case 'md':
      return 'var(--color-icon-markdown)';
    case 'svg':
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
      return 'var(--color-icon-image)';
    default:
      return 'var(--color-icon-default)';
  }
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

      {/* Node icon */}
      <span
        className="material-symbols-outlined"
        aria-hidden="true"
        style={{
          fontSize: '16px',
          flexShrink: 0,
          color: iconColor,
        }}
      >
        {isDirectory ? (isExpanded ? 'folder_open' : 'folder') : fileIcon(node.name)}
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
