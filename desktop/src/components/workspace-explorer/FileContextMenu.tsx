import { useEffect, useRef, useCallback, useState } from 'react';
import type { FileTreeItem } from './FileTreeNode';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';

/**
 * FileContextMenu component - right-click context menu for file operations
 * 
 * Requirements:
 * - 3.11: Support right-click context menu for file operations (rename, delete, copy path)
 * - 6.1: Show "Attach to Chat" option when right-clicking a file
 * - 4.1: Swarm Workspace cannot be deleted by user
 * - 4.4: Display error message when user attempts to delete Swarm Workspace
 * - 10.3: Swarm Workspace always exists and cannot be removed
 */

interface FileContextMenuProps {
  /** The file/folder item that was right-clicked */
  item: FileTreeItem;
  /** X position for the menu */
  x: number;
  /** Y position for the menu */
  y: number;
  /** Callback when menu should close */
  onClose: () => void;
  /** Callback when "Open File" is selected (file nodes only) */
  onOpenFile?: (item: FileTreeItem) => void;
  /** Callback when "Attach to Chat" is selected */
  onAttachToChat?: (item: FileTreeItem) => void;
  /** Callback when rename is requested */
  onRename?: (item: FileTreeItem) => void;
  /** Callback when delete is confirmed — should handle the actual deletion */
  onDelete?: (item: FileTreeItem) => void | Promise<void>;
  /** Callback when file system changes (for refresh) */
  onFileSystemChange?: () => void;
  /** Callback when "Ask Swarm about this" is selected */
  onAskAbout?: (item: FileTreeItem) => void;
  /** Ref to the element that should receive focus when the menu closes via Escape (Requirement 10.4) */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}

interface MenuItem {
  id: string;
  label: string;
  icon: string;
  action: () => void;
  disabled?: boolean;
  danger?: boolean;
  dividerAfter?: boolean;
}

export default function FileContextMenu({
  item,
  x,
  y,
  onClose,
  onOpenFile,
  onAttachToChat,
  onRename,
  onDelete,
  onFileSystemChange,
  onAskAbout,
  returnFocusRef,
}: FileContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showSwarmWorkspaceWarning, setShowSwarmWorkspaceWarning] = useState(false);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        returnFocusRef?.current?.focus();
        onClose();
        return;
      }

      const items = menuRef.current?.querySelectorAll('button[role="menuitem"]:not(:disabled)');
      if (!items?.length) return;
      const itemsArray = Array.from(items) as HTMLElement[];
      const currentIndex = itemsArray.indexOf(document.activeElement as HTMLElement);

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        const next = currentIndex < itemsArray.length - 1 ? currentIndex + 1 : 0;
        itemsArray[next].focus();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        const prev = currentIndex > 0 ? currentIndex - 1 : itemsArray.length - 1;
        itemsArray[prev].focus();
      } else if (event.key === 'Enter' && currentIndex >= 0) {
        event.preventDefault();
        itemsArray[currentIndex].click();
      }
    };

    // Add listeners with a small delay to prevent immediate close
    const timeoutId = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEscape);
    }, 0);

    return () => {
      clearTimeout(timeoutId);
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose, returnFocusRef]);

  // Adjust menu position to stay within viewport
  useEffect(() => {
    if (menuRef.current) {
      const menu = menuRef.current;
      const rect = menu.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;

      // Adjust horizontal position if menu would overflow right edge
      if (rect.right > viewportWidth) {
        menu.style.left = `${Math.max(0, viewportWidth - rect.width - 8)}px`;
      }

      // Adjust vertical position if menu would overflow bottom edge
      if (rect.bottom > viewportHeight) {
        menu.style.top = `${Math.max(0, viewportHeight - rect.height - 8)}px`;
      }
    }
  }, [x, y]);

  // Auto-focus the first menu item when the menu opens (Requirement 10.3)
  useEffect(() => {
    const firstItem = menuRef.current?.querySelector('button[role="menuitem"]') as HTMLElement | null;
    firstItem?.focus();
  }, []);

  // Copy path to clipboard
  const handleCopyPath = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(item.path);
      onClose();
    } catch (error) {
      console.error('Failed to copy path:', error);
    }
  }, [item.path, onClose]);

  // Handle open file
  const handleOpenFile = useCallback(() => {
    onOpenFile?.(item);
    onClose();
  }, [item, onOpenFile, onClose]);

  // Handle attach to chat
  const handleAttachToChat = useCallback(() => {
    onAttachToChat?.(item);
    onClose();
  }, [item, onAttachToChat, onClose]);

  // Handle rename
  const handleRename = useCallback(() => {
    onRename?.(item);
    onClose();
  }, [item, onRename, onClose]);

  // Handle "Ask Swarm about this"
  const handleAskAbout = useCallback(() => {
    onAskAbout?.(item);
    onClose();
  }, [item, onAskAbout, onClose]);

  // Handle delete with confirmation
  // For Swarm Workspace items, show warning dialog instead (Requirements 4.1, 4.4, 10.3)
  const handleDeleteClick = useCallback(() => {
    if (item.isSwarmWorkspace) {
      setShowSwarmWorkspaceWarning(true);
    } else {
      setShowDeleteConfirm(true);
    }
  }, [item.isSwarmWorkspace]);

  // Confirm delete — delegates to parent handler (trash via backend)
  const handleDeleteConfirm = useCallback(async () => {
    setIsDeleting(true);
    try {
      await onDelete?.(item);
      onFileSystemChange?.();
      onClose();
    } catch (error) {
      console.error('Failed to delete:', error);
      setIsDeleting(false);
      setShowDeleteConfirm(false);
    }
  }, [item, onDelete, onFileSystemChange, onClose]);

  // Cancel delete
  const handleDeleteCancel = useCallback(() => {
    setShowDeleteConfirm(false);
  }, []);

  // Close Swarm Workspace warning dialog (Requirements 4.1, 4.4)
  const handleSwarmWorkspaceWarningClose = useCallback(() => {
    setShowSwarmWorkspaceWarning(false);
    onClose();
  }, [onClose]);

  // Build menu items based on item type
  const menuItems: MenuItem[] = [];

  // Open File — only for files (Requirements 2.1, 2.3)
  if (item.type === 'file') {
    menuItems.push({
      id: 'open',
      label: 'Open File',
      icon: 'open_in_new',
      action: handleOpenFile,
    });
  }

  // Attach to Chat - only for files (Requirement 6.1)
  if (item.type === 'file') {
    menuItems.push({
      id: 'attach',
      label: 'Attach to Chat',
      icon: 'attach_file',
      action: handleAttachToChat,
    });
  }

  // Ask Swarm about this file
  if (item.type === 'file') {
    menuItems.push({
      id: 'ask-swarm',
      label: 'Ask Swarm',
      icon: 'smart_toy',
      action: handleAskAbout,
      dividerAfter: true,
    });
  }

  // Rename
  menuItems.push({
    id: 'rename',
    label: 'Rename',
    icon: 'edit',
    action: handleRename,
    // Disable rename for Swarm Workspace root
    disabled: item.isSwarmWorkspace && item.path === item.workspaceId,
  });

  // Delete - enabled for all items, but Swarm Workspace items show warning dialog
  menuItems.push({
    id: 'delete',
    label: 'Delete',
    icon: 'delete',
    action: handleDeleteClick,
    danger: true,
    dividerAfter: true,
  });

  // Copy Path
  menuItems.push({
    id: 'copy-path',
    label: 'Copy Path',
    icon: 'content_copy',
    action: handleCopyPath,
  });

  // Delete confirmation dialog
  if (showDeleteConfirm) {
    return (
      <div
        ref={menuRef}
        className="fixed z-50 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg p-4 w-72"
        style={{ left: x, top: y }}
        data-testid="delete-confirm-dialog"
      >
        <div className="flex items-start gap-3 mb-4">
          <span className="material-symbols-outlined text-[var(--color-error)]">warning</span>
          <div>
            <h4 className="text-sm font-medium text-[var(--color-text)] mb-1">
              Delete {item.type === 'directory' ? 'Folder' : 'File'}?
            </h4>
            <p className="text-xs text-[var(--color-text-muted)]">
              Are you sure you want to delete "{item.name}"?
              {item.type === 'directory' && ' This will delete all contents.'}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={handleDeleteCancel}
            disabled={isDeleting}
            className="px-3 py-1.5 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors disabled:opacity-50"
            data-testid="delete-cancel-button"
          >
            Cancel
          </button>
          <button
            onClick={handleDeleteConfirm}
            disabled={isDeleting}
            className="px-3 py-1.5 text-sm rounded bg-[var(--color-error)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center gap-1"
            data-testid="delete-confirm-button"
          >
            {isDeleting && (
              <div className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
            )}
            Delete
          </button>
        </div>
      </div>
    );
  }

  // Swarm Workspace deletion prevention dialog (Requirements 4.1, 4.4, 10.3)
  if (showSwarmWorkspaceWarning) {
    return (
      <SwarmWorkspaceWarningDialog
        isOpen={true}
        action="delete"
        fileName={item.name}
        onConfirm={handleSwarmWorkspaceWarningClose}
        onCancel={handleSwarmWorkspaceWarningClose}
      />
    );
  }

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-lg py-1 min-w-[160px]"
      style={{ left: x, top: y }}
      role="menu"
      aria-label="File context menu"
      data-testid="file-context-menu"
    >
      {menuItems.map((menuItem, index) => (
        <div key={menuItem.id}>
          <button
            onClick={menuItem.action}
            disabled={menuItem.disabled}
            className={`w-full px-3 py-2 text-sm text-left flex items-center gap-2 transition-colors ${
              menuItem.disabled
                ? 'text-[var(--color-text-muted)] opacity-50 cursor-not-allowed'
                : menuItem.danger
                  ? 'text-[var(--color-error)] hover:bg-[var(--color-error)] hover:bg-opacity-10'
                  : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
            }`}
            role="menuitem"
            data-testid={`context-menu-${menuItem.id}`}
          >
            <span className="material-symbols-outlined text-base">{menuItem.icon}</span>
            {menuItem.label}
          </button>
          {menuItem.dividerAfter && index < menuItems.length - 1 && (
            <div className="my-1 border-t border-[var(--color-border)]" />
          )}
        </div>
      ))}
    </div>
  );
}

// Export types for use in other components
export type { FileContextMenuProps };
