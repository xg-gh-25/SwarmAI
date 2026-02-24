import type { FileAttachment } from '../../types';
import { AttachmentPreviewCard } from './AttachmentPreviewCard';

interface FileAttachmentPreviewProps {
  attachments: FileAttachment[];
  onRemove: (id: string) => void;
}

/**
 * Calculate total size of attachments
 */
const formatTotalSize = (attachments: FileAttachment[]): string => {
  const total = attachments.reduce((sum, a) => sum + a.size, 0);
  if (total < 1024) return `${total} B`;
  if (total < 1024 * 1024) return `${(total / 1024).toFixed(1)} KB`;
  return `${(total / (1024 * 1024)).toFixed(1)} MB`;
};

export function FileAttachmentPreview({ attachments, onRemove }: FileAttachmentPreviewProps) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className="mb-3">
      {/* Horizontal scrollable container */}
      <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-dark-border scrollbar-track-transparent">
        {attachments.map((attachment) => (
          <AttachmentPreviewCard
            key={attachment.id}
            attachment={attachment}
            onRemove={onRemove}
          />
        ))}
      </div>

      {/* Total size indicator */}
      <div className="flex items-center justify-between mt-1 px-1">
        <span className="text-xs text-[var(--color-text-muted)]">
          {attachments.length} attachment{attachments.length !== 1 ? 's' : ''}
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          Total: {formatTotalSize(attachments)}
        </span>
      </div>
    </div>
  );
}
