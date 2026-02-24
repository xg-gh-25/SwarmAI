import clsx from 'clsx';
import type { FileAttachment } from '../../types';

interface AttachmentPreviewCardProps {
  attachment: FileAttachment;
  onRemove: (id: string) => void;
}

/**
 * Format file size for display
 */
const formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * Get icon for file type
 */
const getFileIcon = (type: FileAttachment['type']): string => {
  switch (type) {
    case 'image':
      return 'image';
    case 'pdf':
      return 'picture_as_pdf';
    case 'text':
      return 'description';
    case 'csv':
      return 'table_chart';
    default:
      return 'insert_drive_file';
  }
};

export function AttachmentPreviewCard({ attachment, onRemove }: AttachmentPreviewCardProps) {
  const { id, name, type, size, preview, isLoading, error } = attachment;

  return (
    <div
      className={clsx(
        'relative group flex-shrink-0 w-32 h-24 rounded-lg overflow-hidden border transition-colors',
        error
          ? 'border-red-500/50 bg-red-500/10'
          : 'border-[var(--color-border)] bg-[var(--color-hover)] hover:border-primary/50'
      )}
    >
      {/* Remove button */}
      <button
        onClick={() => onRemove(id)}
        className="absolute top-1 right-1 z-10 w-5 h-5 rounded-full bg-black/60 text-[var(--color-text)] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
        title="Remove attachment"
      >
        <span className="material-symbols-outlined text-sm">close</span>
      </button>

      {/* Content */}
      <div className="h-full w-full flex flex-col">
        {/* Preview area */}
        <div className="flex-1 flex items-center justify-center p-2 overflow-hidden">
          {isLoading ? (
            <div className="animate-spin">
              <span className="material-symbols-outlined text-[var(--color-text-muted)]">progress_activity</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center text-red-400">
              <span className="material-symbols-outlined text-xl">error</span>
              <span className="text-xs mt-1">Error</span>
            </div>
          ) : type === 'image' && preview ? (
            <img
              src={preview}
              alt={name}
              className="max-h-full max-w-full object-contain rounded"
            />
          ) : type === 'pdf' ? (
            <div className="flex flex-col items-center text-red-400">
              <span className="material-symbols-outlined text-2xl">picture_as_pdf</span>
            </div>
          ) : (type === 'text' || type === 'csv') && preview ? (
            <div className="w-full h-full overflow-hidden p-1">
              <pre className="text-[8px] text-[var(--color-text-muted)] leading-tight whitespace-pre-wrap break-all">
                {preview}
              </pre>
            </div>
          ) : (
            <span className="material-symbols-outlined text-2xl text-[var(--color-text-muted)]">{getFileIcon(type)}</span>
          )}
        </div>

        {/* File info */}
        <div className="px-2 pb-1.5 bg-[var(--color-card)]/80">
          <p className="text-xs text-[var(--color-text)] truncate" title={name}>
            {name}
          </p>
          <p className="text-[10px] text-[var(--color-text-muted)]">{formatFileSize(size)}</p>
        </div>
      </div>
    </div>
  );
}
