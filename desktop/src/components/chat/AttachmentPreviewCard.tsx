import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import type { UnifiedAttachment } from '../../types';

interface AttachmentPreviewCardProps {
  attachment: UnifiedAttachment;
  onRemove: (id: string) => void;
  /** Terminal chips: click shows / hides the full text below the row. */
  onToggleOpen?: (id: string) => void;
  open?: boolean;
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
const getFileIcon = (type: UnifiedAttachment['type']): string => {
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

export function AttachmentPreviewCard({ attachment, onRemove, onToggleOpen, open }: AttachmentPreviewCardProps) {
  const { t } = useTranslation();
  const { id, name, type, size, preview, isLoading, error } = attachment;

  if (type === 'terminal') {
    const term = attachment.terminal;
    return (
      <div
        data-testid={`attachment-terminal-${id}`}
        className={clsx(
          'relative group flex-shrink-0 w-44 h-24 rounded-lg overflow-hidden border transition-colors cursor-pointer',
          open ? 'border-[var(--color-primary)]' : 'border-[var(--color-border)] hover:border-primary/50',
          'bg-[var(--color-bg)]',
        )}
        onClick={() => onToggleOpen?.(id)}
        title={open ? t('ui.AttachmentPreviewCard.hideTerminal') : t('ui.AttachmentPreviewCard.showTerminal')}
      >
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute top-1 right-1 z-10 w-5 h-5 rounded-full bg-black/60 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-500"
          title={t('ui.AttachmentPreviewCard.removeTerminal')}
          aria-label={t('ui.AttachmentPreviewCard.removeTerminal')}
        >
          <span className="material-symbols-outlined icon-xs">close</span>
        </button>
        <div className="h-full w-full flex flex-col">
          <pre className="flex-1 overflow-hidden px-2 pt-1.5 font-mono text-[8.5px] leading-[1.25] text-[var(--color-text-muted)] whitespace-pre">
            {preview}
          </pre>
          <div className="flex items-center gap-1 px-2 pb-1.5 pt-0.5 bg-[var(--color-card)]">
            <span className="material-symbols-outlined icon-xs text-[var(--color-primary)]">terminal</span>
            <span className="text-xs text-[var(--color-text)] truncate" title={term?.cwd || 'SwarmWS'}>{name.replace(/^Terminal · /, '')}</span>
            <span className="ml-auto text-[10.5px] text-[var(--color-text-muted)] whitespace-nowrap">
              {term?.truncated
                ? t('ui.AttachmentPreviewCard.linesCut', { count: term?.lines ?? 0 })
                : t('ui.AttachmentPreviewCard.lines', { count: term?.lines ?? 0 })}
            </span>
          </div>
        </div>
      </div>
    );
  }

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
        title={t('ui.AttachmentPreviewCard.removeAttachment')}
      >
        <span className="material-symbols-outlined icon-sm">close</span>
      </button>

      {/* Content */}
      <div className="h-full w-full flex flex-col">
        {/* Preview area */}
        <div className="flex-1 flex items-center justify-center p-2 overflow-hidden">
          {isLoading ? (
            <div className="animate-spin">
              <span className="material-symbols-outlined icon-md text-[var(--color-text-muted)]">progress_activity</span>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center text-red-400">
              <span className="material-symbols-outlined icon-sm">error</span>
              <span className="text-xs mt-1">{t('common.status.error')}</span>
            </div>
          ) : type === 'image' && preview ? (
            <img
              src={preview}
              alt={name}
              className="max-h-full max-w-full object-contain rounded"
            />
          ) : type === 'pdf' ? (
            <div className="flex flex-col items-center text-red-400">
              <span className="material-symbols-outlined icon-md">picture_as_pdf</span>
            </div>
          ) : (type === 'text' || type === 'csv') && preview ? (
            <div className="w-full h-full overflow-hidden p-1">
              <pre className="text-[8px] text-[var(--color-text-muted)] leading-tight whitespace-pre-wrap break-all">
                {preview}
              </pre>
            </div>
          ) : (
            <span className="material-symbols-outlined icon-md text-[var(--color-text-muted)]">{getFileIcon(type)}</span>
          )}
        </div>

        {/* File info */}
        <div className="px-2 pb-1.5 bg-[var(--color-card)]/80">
          <p className="text-xs text-[var(--color-text)] truncate" title={name}>
            {name}
          </p>
          {/* Hide the size label when size is unknown (0) — e.g. a workspace file
              whose meta fetch failed. A wrong "0 B" is worse than no label. A real
              tiny file (size>0, e.g. 100 B) still shows. */}
          {size > 0 && (
            <p className="text-[11px] text-[var(--color-text-muted)]">{formatFileSize(size)}</p>
          )}
        </div>
      </div>
    </div>
  );
}
