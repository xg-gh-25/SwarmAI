import { useState, useCallback, ReactNode } from 'react';
import clsx from 'clsx';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

interface ChatDropZoneProps {
  /** Child content (the chat panel) */
  children: ReactNode;
  /** Add native File objects (from OS drop) to the unified attachment pipeline */
  addFiles: (files: File[]) => Promise<void>;
  /** Add workspace files by path (from Workspace Explorer drag) */
  addWorkspaceFiles: (files: FileTreeItem[]) => Promise<void>;
}

/**
 * ChatDropZone — Drop target wrapper for the chat panel.
 *
 * Accepts files from two drag sources and routes them through the
 * unified attachment pipeline via props supplied by ChatPage:
 *
 *   Path 1 – Workspace Explorer: JSON payload (`application/json`) containing
 *            a FileTreeItem.  Routed to `addWorkspaceFiles`.
 *   Path 2 – Native OS drop (Finder / Explorer / Nautilus): `DataTransfer.files`
 *            containing native File objects.  Routed to `addFiles`.
 *
 * A visual drop overlay is shown for both drag types.
 *
 * Requirements: 3.1, 4.1, 4.2, 4.3
 */
export function ChatDropZone({ children, addFiles, addWorkspaceFiles }: ChatDropZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);

  // Handle drag enter - show visual feedback for workspace JSON or native files
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Show overlay for workspace explorer JSON drags OR native OS file drags
    if (e.dataTransfer.types.includes('application/json') || e.dataTransfer.types.includes('Files')) {
      setIsDragOver(true);
    }
  }, []);

  // Handle drag over - required to allow drop for both drag types
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Set the drop effect to copy for workspace JSON or native file drags
    if (e.dataTransfer.types.includes('application/json') || e.dataTransfer.types.includes('Files')) {
      e.dataTransfer.dropEffect = 'copy';
    }
  }, []);

  // Handle drag leave - remove visual feedback
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Only set isDragOver to false if we're leaving the drop zone entirely
    // Check if the related target is outside the drop zone
    const relatedTarget = e.relatedTarget as HTMLElement | null;
    const currentTarget = e.currentTarget as HTMLElement;
    
    if (!relatedTarget || !currentTarget.contains(relatedTarget)) {
      setIsDragOver(false);
    }
  }, []);

  // Handle drop - route to unified attachment pipeline via props
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);

    // Path 1: Workspace explorer (JSON data from FileTreeNode drag)
    const jsonData = e.dataTransfer.getData('application/json');
    if (jsonData) {
      try {
        const fileData: FileTreeItem = JSON.parse(jsonData);
        if (fileData.type === 'file') {
          addWorkspaceFiles([fileData]);
        }
      } catch (err) {
        console.error('Failed to parse dropped file data:', err);
      }
      return;
    }

    // Path 2: Native OS file drop (Finder/Explorer/Nautilus)
    if (e.dataTransfer.files.length > 0) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  }, [addFiles, addWorkspaceFiles]);

  return (
    <div
      className="relative flex-1 flex flex-col overflow-hidden"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid="chat-drop-zone"
    >
      {/* Main content */}
      {children}

      {/* Drop overlay - shown when dragging over */}
      {isDragOver && (
        <div
          className={clsx(
            'absolute inset-0 z-50 flex items-center justify-center',
            'bg-[var(--color-primary)]/10 backdrop-blur-sm',
            'border-2 border-dashed border-[var(--color-primary)]',
            'pointer-events-none'
          )}
          data-testid="drop-overlay"
        >
          <div
            className={clsx(
              'flex flex-col items-center gap-3 p-6 rounded-xl',
              'bg-[var(--color-card)] shadow-lg',
              'border border-[var(--color-primary)]/30'
            )}
          >
            <div
              className={clsx(
                'w-16 h-16 rounded-full flex items-center justify-center',
                'bg-[var(--color-primary)]/20'
              )}
            >
              <span className="material-symbols-outlined text-3xl text-[var(--color-primary)]">upload_file</span>
            </div>
            <div className="text-center">
              <p className="text-lg font-medium text-[var(--color-text)]">
                Drop to attach
              </p>
              <p className="text-sm text-[var(--color-text-muted)]">
                File will be added to chat context
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ChatDropZone;
