import { useState, useCallback, useRef, useEffect } from 'react';

export interface WorkspaceFooterProps {
  isDefaultWorkspace: boolean;
  isArchived?: boolean;
  onNewWorkspace?: () => void;
  onSettings?: () => void;
  onArchive?: () => void;
  onUnarchive?: () => void;
  onDelete?: () => void;
}

/**
 * WorkspaceFooter - New Workspace, Settings, and Archive/Delete actions.
 * Requirements: 3.14, 9.12, 9.13, 36.1, 36.10
 */
export default function WorkspaceFooter({
  isDefaultWorkspace,
  isArchived = false,
  onNewWorkspace,
  onSettings,
  onArchive,
  onUnarchive,
  onDelete,
}: WorkspaceFooterProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuOpen]);

  const handleArchive = useCallback(() => {
    setMenuOpen(false);
    onArchive?.();
  }, [onArchive]);

  const handleUnarchive = useCallback(() => {
    setMenuOpen(false);
    onUnarchive?.();
  }, [onUnarchive]);

  const handleDelete = useCallback(() => {
    setMenuOpen(false);
    onDelete?.();
  }, [onDelete]);

  return (
    <div
      className="flex items-center gap-1 px-3 py-2 border-t border-[var(--color-border)] bg-[var(--color-bg)]"
      data-testid="workspace-footer"
    >
      {/* + New Workspace */}
      <button
        onClick={onNewWorkspace}
        className="flex items-center gap-1 px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
        title="New Workspace"
        data-testid="new-workspace-button"
      >
        <span className="text-sm">+</span>
        <span>New Workspace</span>
      </button>

      {/* Settings */}
      <button
        onClick={onSettings}
        className="flex items-center gap-1 px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
        title="Workspace Settings"
        data-testid="workspace-settings-button"
      >
        <span>⚙️</span>
      </button>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Context menu for custom workspaces */}
      {!isDefaultWorkspace && (
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="px-1.5 py-1 text-xs rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            title="More actions"
            data-testid="workspace-more-button"
            aria-haspopup="true"
            aria-expanded={menuOpen}
          >
            ⋯
          </button>
          {menuOpen && (
            <div
              className="absolute bottom-full right-0 mb-1 w-44 rounded border border-[var(--color-border)] bg-[var(--color-bg)] shadow-lg z-50"
              data-testid="workspace-context-menu"
              role="menu"
            >
              {isArchived ? (
                <button
                  onClick={handleUnarchive}
                  className="w-full text-left px-3 py-2 text-xs text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                  role="menuitem"
                  data-testid="unarchive-workspace-option"
                >
                  📂 Unarchive Workspace
                </button>
              ) : (
                <button
                  onClick={handleArchive}
                  className="w-full text-left px-3 py-2 text-xs text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                  role="menuitem"
                  data-testid="archive-workspace-option"
                >
                  📦 Archive Workspace
                </button>
              )}
              <button
                onClick={handleDelete}
                className="w-full text-left px-3 py-2 text-xs text-[var(--color-error)] hover:bg-[var(--color-hover)] transition-colors"
                role="menuitem"
                data-testid="delete-workspace-option"
              >
                🗑️ Delete Workspace
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
