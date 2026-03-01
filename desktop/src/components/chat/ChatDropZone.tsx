import { useState, useCallback, ReactNode } from 'react';
import clsx from 'clsx';
import { useLayout } from '../../contexts/LayoutContext';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

interface ChatDropZoneProps {
  /** Child content (the chat panel) */
  children: ReactNode;
}

/**
 * ChatDropZone - Wrapper component that handles drag-drop file attachment
 * 
 * Requirements:
 * - 3.12: Drag files from file tree to chat to attach as context
 * - 6.2: Drag-drop files from Workspace Explorer to attach to chat context
 * 
 * Property 11: Drag-Drop File Attachment
 * For any file dragged from Workspace_Explorer and dropped on Main_Chat_Panel,
 * that file SHALL be added to the Chat_Context attachments list.
 */
export function ChatDropZone({ children }: ChatDropZoneProps) {
  const { attachFile } = useLayout();
  const [isDragOver, setIsDragOver] = useState(false);

  // Handle drag enter - show visual feedback
  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Check if the drag contains our file data
    if (e.dataTransfer.types.includes('application/json')) {
      setIsDragOver(true);
    }
  }, []);

  // Handle drag over - required to allow drop
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Set the drop effect to copy
    if (e.dataTransfer.types.includes('application/json')) {
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

  // Handle drop - attach the file to chat context
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);

    try {
      // Get the file data from the drag event
      const jsonData = e.dataTransfer.getData('application/json');
      if (!jsonData) {
        return;
      }

      const fileData: FileTreeItem = JSON.parse(jsonData);
      
      // Only attach files, not directories
      if (fileData.type === 'file') {
        attachFile(fileData);
      }
    } catch (error) {
      console.error('Failed to parse dropped file data:', error);
    }
  }, [attachFile]);

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
