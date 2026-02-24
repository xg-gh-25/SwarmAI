import { useRef, useCallback } from 'react';
import clsx from 'clsx';

interface FileAttachmentButtonProps {
  onFilesSelected: (files: File[]) => void;
  disabled?: boolean;
  canAddMore?: boolean;
  className?: string;
}

// Accepted file types for the file input
const ACCEPT_TYPES = [
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'application/pdf',
  'text/plain',
  'text/csv',
].join(',');

export function FileAttachmentButton({
  onFilesSelected,
  disabled = false,
  canAddMore = true,
  className,
}: FileAttachmentButtonProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleClick = useCallback(() => {
    if (!disabled && canAddMore) {
      fileInputRef.current?.click();
    }
  }, [disabled, canAddMore]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      if (files.length > 0) {
        onFilesSelected(files);
      }
      // Reset input to allow selecting the same file again
      e.target.value = '';
    },
    [onFilesSelected]
  );

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || !canAddMore}
        className={clsx(
          'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 transition-colors',
          disabled || !canAddMore
            ? 'bg-[var(--color-hover)]/50 text-[var(--color-text-muted)]/50 cursor-not-allowed'
            : 'bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)]',
          className
        )}
        title={
          !canAddMore
            ? 'Maximum attachments reached'
            : 'Attach files (images, PDF, TXT, CSV)'
        }
      >
        <span className="material-symbols-outlined">add</span>
      </button>

      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT_TYPES}
        multiple
        onChange={handleChange}
        className="hidden"
        aria-hidden="true"
      />
    </>
  );
}
