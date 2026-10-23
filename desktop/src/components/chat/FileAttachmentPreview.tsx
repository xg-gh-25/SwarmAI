/**
 * FileAttachmentPreview — the chat input's attachment row: one card per attachment
 * (AttachmentPreviewCard) plus the count / total size. A terminal chip
 * (utils/terminalAttachment) opens its full text in a panel below the row.
 */
import { useState } from 'react';
import type { UnifiedAttachment } from '../../types';
import { AttachmentPreviewCard } from './AttachmentPreviewCard';

interface FileAttachmentPreviewProps {
  attachments: UnifiedAttachment[];
  onRemove: (id: string) => void;
}

/**
 * Calculate total size of attachments
 */
const formatTotalSize = (attachments: UnifiedAttachment[]): string => {
  const total = attachments.reduce((sum, a) => sum + a.size, 0);
  if (total < 1024) return `${total} B`;
  if (total < 1024 * 1024) return `${(total / 1024).toFixed(1)} KB`;
  return `${(total / (1024 * 1024)).toFixed(1)} MB`;
};

export function FileAttachmentPreview({ attachments, onRemove }: FileAttachmentPreviewProps) {
  const [openId, setOpenId] = useState<string | null>(null);
  if (attachments.length === 0) {
    return null;
  }
  const opened = attachments.find((a) => a.id === openId && a.type === 'terminal') ?? null;

  return (
    <div className="mb-3">
      {/* Horizontal scrollable container */}
      <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin scrollbar-thumb-dark-border scrollbar-track-transparent">
        {attachments.map((attachment) => (
          <AttachmentPreviewCard
            key={attachment.id}
            attachment={attachment}
            onRemove={(id) => { if (id === openId) setOpenId(null); onRemove(id); }}
            onToggleOpen={(id) => setOpenId((cur) => (cur === id ? null : id))}
            open={attachment.id === openId}
          />
        ))}
      </div>

      {opened && (
        <div
          data-testid="attachment-terminal-full"
          className="mb-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]"
        >
          <div className="flex items-center gap-2 px-2.5 h-7 border-b border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)]">
            <span className="truncate" title={opened.terminal?.cwd || 'SwarmWS'}>{opened.terminal?.cwd || 'SwarmWS'}</span>
            {opened.terminal?.truncated && <span className="ml-auto whitespace-nowrap">older output cut (16,000-character limit)</span>}
          </div>
          <pre className="max-h-48 overflow-auto px-2.5 py-1.5 font-mono text-[11px] leading-[1.35] text-[var(--color-text)] whitespace-pre">
            {opened.textContent}
          </pre>
        </div>
      )}

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
