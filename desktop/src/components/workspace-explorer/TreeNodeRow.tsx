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

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface TreeNodeRowProps {
  node: TreeNode;
  depth: number;
  isExpanded: boolean;
  isSelected: boolean;
  isMatched: boolean;
  isSystemManaged: boolean;
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

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const TreeNodeRow: React.FC<TreeNodeRowProps> = React.memo(function TreeNodeRow({
  node,
  depth,
  isExpanded,
  isSelected,
  isMatched,
  isSystemManaged,
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

  const textColor = isSystemManaged
    ? 'var(--color-text-muted)'
    : 'var(--color-text)';

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
          color: isSystemManaged ? 'var(--color-text-muted)' : undefined,
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

      {/* System-managed lock badge */}
      {isSystemManaged && (
        <span
          className="material-symbols-outlined"
          data-testid="lock-badge"
          aria-label="System managed"
          style={{
            fontSize: '14px',
            flexShrink: 0,
            color: 'var(--color-explorer-system-badge)',
          }}
        >
          lock
        </span>
      )}

      {/* User-managed CRUD action icons — visible on hover only */}
      {!isSystemManaged && (
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
      )}
    </div>
  );
});

export default TreeNodeRow;
