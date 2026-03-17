/**
 * ReviewModeGutter — Gutter overlay for review mode in FileEditorCore.
 *
 * Replaces the standard LineGutter when review mode is active. Renders
 * clickable line numbers that open the CommentPopover. Lines with existing
 * comments show a yellow badge with a comment icon.
 *
 * Key exports:
 * - `ReviewModeGutter` (default) — Gutter overlay component
 */

import { useCallback, useRef } from 'react';
import CommentPopover from './CommentPopover';
import type { ReviewComment } from '../../hooks/useReviewMode';

/** Line height must match the editor textarea (leading-6 = 24px). */
const LINE_HEIGHT = 24;
/** Top padding of the textarea (p-4 = 16px). */
const EDITOR_PADDING_TOP = 16;

interface ReviewModeGutterProps {
  lineCount: number;
  scrollTop: number;
  comments: ReviewComment[];
  activePopoverLine: number | null;
  editingCommentId: string | null;
  onLineClick: (lineNumber: number) => void;
  onAddComment: (lineStart: number, lineEnd: number, text: string) => void;
  onUpdateComment: (id: string, text: string) => void;
  onRemoveComment: (id: string) => void;
  onCancelPopover: () => void;
  getCommentForLine: (lineNumber: number) => ReviewComment | undefined;
}

export default function ReviewModeGutter({
  lineCount,
  scrollTop,
  comments,
  activePopoverLine,
  editingCommentId,
  onLineClick,
  onAddComment,
  onUpdateComment,
  onRemoveComment,
  onCancelPopover,
  getCommentForLine,
}: ReviewModeGutterProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const gutterWidth = `${Math.max(3, String(lineCount).length) + 1}ch`;

  const handleLineClick = useCallback(
    (lineNumber: number) => {
      onLineClick(lineNumber);
    },
    [onLineClick],
  );

  // Find the active comment (for editing)
  const activeComment = editingCommentId
    ? comments.find((c) => c.id === editingCommentId)
    : activePopoverLine
      ? getCommentForLine(activePopoverLine)
      : undefined;

  // Determine if popover is for new comment or editing existing
  const isEditingExisting = !!editingCommentId || (activePopoverLine != null && !!activeComment);

  // Compute popover top offset relative to container
  const popoverLine = activePopoverLine ?? activeComment?.lineStart;
  const popoverTopOffset = popoverLine != null
    ? (popoverLine - 1) * LINE_HEIGHT + EDITOR_PADDING_TOP - scrollTop
    : 0;

  return (
    <div ref={containerRef} className="relative shrink-0 select-none border-r border-[var(--color-border)] bg-[var(--color-background)] overflow-hidden" style={{ width: gutterWidth }}>
      {/* Line numbers */}
      <div
        className="font-mono text-xs leading-6 text-right pr-2 pt-4"
        style={{ transform: `translateY(-${scrollTop}px)` }}
      >
        {Array.from({ length: lineCount }, (_, i) => {
          const lineNum = i + 1;
          const comment = getCommentForLine(lineNum);
          const hasComment = !!comment;
          const isPopoverTarget = activePopoverLine === lineNum;

          return (
            <div
              key={lineNum}
              className={`relative cursor-pointer transition-colors ${
                isPopoverTarget
                  ? 'bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                  : hasComment
                    ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
              }`}
              onClick={() => handleLineClick(lineNum)}
              title={hasComment ? `Comment: ${comment.text}` : `Add comment on line ${lineNum}`}
              role="button"
              aria-label={hasComment ? `Edit comment on line ${lineNum}` : `Add comment on line ${lineNum}`}
            >
              {hasComment && (
                <span className="absolute left-0.5 top-1/2 -translate-y-1/2 text-[10px]">💬</span>
              )}
              <span className={hasComment ? 'pl-3' : ''}>{lineNum}</span>
            </div>
          );
        })}
      </div>

      {/* Comment Popover — rendered via Portal to avoid overflow clipping */}
      {activePopoverLine != null && (
        <CommentPopover
          lineNumber={activePopoverLine}
          initialText={isEditingExisting && activeComment ? activeComment.text : ''}
          onSubmit={(text) => {
            if (isEditingExisting && activeComment) {
              onUpdateComment(activeComment.id, text);
            } else {
              onAddComment(activePopoverLine, activePopoverLine, text);
            }
          }}
          onCancel={onCancelPopover}
          onDelete={
            isEditingExisting && activeComment
              ? () => onRemoveComment(activeComment.id)
              : undefined
          }
          topOffset={popoverTopOffset}
          anchorRef={containerRef}
        />
      )}
    </div>
  );
}
