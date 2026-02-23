import { useState, useCallback, useRef, useEffect } from 'react';
import clsx from 'clsx';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

/**
 * AttachedFileChips Props
 * 
 * From design.md:
 * - files: FileTreeItem[] (files attached to chat context from Workspace Explorer)
 * - onRemoveFile: callback to remove a file from context
 */
interface AttachedFileChipsProps {
  /** List of files attached to the chat context */
  files: FileTreeItem[];
  /** Callback when a file is removed */
  onRemoveFile: (file: FileTreeItem) => void;
}

/**
 * FileChip Props
 */
interface FileChipProps {
  file: FileTreeItem;
  onRemove: () => void;
  isFocused: boolean;
  onFocus: () => void;
  tabIndex: number;
}

/**
 * Get file icon based on file extension
 */
function getFileIcon(fileName: string): string {
  const ext = fileName.split('.').pop()?.toLowerCase();
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
}

/**
 * FileChip - Individual file chip with remove button
 * Supports keyboard interaction (Delete/Backspace to remove)
 * 
 * Requirements:
 * - 2.3: Display file name, truncated with ellipsis when exceeds available width
 * - 2.4: Display close (X) button to remove file from context
 * - 3.1: Use pill/rounded shape with compact padding
 * - 3.2: Display file icon before file name
 * - 3.3: Use theme-consistent colors
 * - 3.4: Close button visible and interactive
 * - 3.5: Display tooltip showing full file path on hover
 * - 4.2: Remove file by pressing Delete or Backspace when focused
 * - 4.4: Close button focusable and activatable via Enter or Space
 * - 4.5: ARIA attributes for screen reader compatibility
 */
function FileChip({ file, onRemove, isFocused, onFocus, tabIndex }: FileChipProps) {
  const chipRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  // Focus the chip when isFocused changes
  useEffect(() => {
    if (isFocused && chipRef.current) {
      chipRef.current.focus();
    }
  }, [isFocused]);

  // Handle keyboard events on the chip
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      onRemove();
    }
  }, [onRemove]);

  // Handle keyboard events on the close button
  const handleCloseButtonKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      e.stopPropagation();
      onRemove();
    }
  }, [onRemove]);

  // Handle click on close button
  const handleCloseClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    onRemove();
  }, [onRemove]);

  const displayName = file.name || 'Unknown file';
  const displayPath = file.path || 'Path unavailable';

  return (
    <div
      ref={chipRef}
      role="listitem"
      tabIndex={tabIndex}
      onFocus={onFocus}
      onKeyDown={handleKeyDown}
      title={displayPath}
      className={clsx(
        'group flex items-center gap-1 px-2 py-0.5 rounded-full text-xs',
        'bg-[var(--color-primary)]/10 text-[var(--color-primary)] border border-[var(--color-primary)]/20',
        'hover:bg-[var(--color-primary)]/20 transition-colors',
        'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]/50',
        'flex-shrink-0 cursor-default'
      )}
      data-testid={`file-chip-${file.id}`}
    >
      {/* File icon */}
      <span className="material-symbols-outlined text-xs flex-shrink-0">
        {getFileIcon(displayName)}
      </span>
      
      {/* File name - truncated */}
      <span className="truncate max-w-[100px]">{displayName}</span>
      
      {/* Close button */}
      <button
        ref={closeButtonRef}
        onClick={handleCloseClick}
        onKeyDown={handleCloseButtonKeyDown}
        className={clsx(
          'flex items-center justify-center w-4 h-4 rounded-full',
          'hover:bg-[var(--color-primary)]/30 transition-colors',
          'opacity-60 group-hover:opacity-100 focus:opacity-100',
          'focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/50'
        )}
        title={`Remove ${displayName}`}
        aria-label={`Remove ${displayName} from context`}
        tabIndex={-1}
      >
        <span className="material-symbols-outlined text-xs">close</span>
      </button>
    </div>
  );
}

/**
 * AttachedFileChips - Displays attached files as compact, removable chips
 * Supports keyboard navigation and accessibility
 * 
 * Requirements:
 * - 2.1: Display File_Chips above text input when files are attached
 * - 2.2: Display in horizontal row with horizontal scrolling when overflow
 * - 2.6: Do not display when no files are attached
 * - 4.1: Focusable via Tab key navigation
 * - 4.3: Navigate between chips using Arrow keys
 * - 4.5: ARIA attributes for screen reader compatibility
 */
export function AttachedFileChips({ files, onRemoveFile }: AttachedFileChipsProps): React.ReactElement | null {
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const containerRef = useRef<HTMLDivElement>(null);

  // Return null when no files attached - Requirement 2.6
  if (!files || files.length === 0) {
    return null;
  }

  // Handle keyboard navigation on the container
  const handleContainerKeyDown = (e: React.KeyboardEvent) => {
    if (files.length === 0) return;

    switch (e.key) {
      case 'ArrowLeft':
        e.preventDefault();
        setFocusedIndex((prev) => {
          if (prev <= 0) return files.length - 1;
          return prev - 1;
        });
        break;
      case 'ArrowRight':
        e.preventDefault();
        setFocusedIndex((prev) => {
          if (prev < 0 || prev >= files.length - 1) return 0;
          return prev + 1;
        });
        break;
      case 'Delete':
      case 'Backspace':
        e.preventDefault();
        if (focusedIndex >= 0 && focusedIndex < files.length) {
          const fileToRemove = files[focusedIndex];
          onRemoveFile(fileToRemove);
          // Adjust focus after removal
          if (files.length > 1) {
            setFocusedIndex(Math.min(focusedIndex, files.length - 2));
          } else {
            setFocusedIndex(-1);
          }
        }
        break;
    }
  };

  // Handle focus entering the container
  const handleContainerFocus = () => {
    if (focusedIndex < 0 && files.length > 0) {
      setFocusedIndex(0);
    }
  };

  // Handle focus leaving the container
  const handleContainerBlur = (e: React.FocusEvent) => {
    // Only reset if focus is leaving the container entirely
    if (!containerRef.current?.contains(e.relatedTarget as Node)) {
      setFocusedIndex(-1);
    }
  };

  return (
    <div
      ref={containerRef}
      role="list"
      aria-label="Attached files"
      className={clsx(
        'flex items-center gap-1.5 mb-3 px-3 py-2',
        'bg-[var(--color-hover)]/30 rounded-lg',
        'overflow-x-auto',
        'scrollbar-thin scrollbar-thumb-[var(--color-border)] scrollbar-track-transparent'
      )}
      onKeyDown={handleContainerKeyDown}
      onFocus={handleContainerFocus}
      onBlur={handleContainerBlur}
      data-testid="attached-file-chips"
    >
      {/* Attachment indicator icon */}
      <span className="material-symbols-outlined text-sm text-[var(--color-primary)] flex-shrink-0">
        attach_file
      </span>
      
      {/* File chips */}
      {files.map((file, index) => (
        <FileChip
          key={file.id}
          file={file}
          onRemove={() => onRemoveFile(file)}
          isFocused={focusedIndex === index}
          onFocus={() => setFocusedIndex(index)}
          tabIndex={index === 0 ? 0 : -1}
        />
      ))}
    </div>
  );
}

export default AttachedFileChips;
