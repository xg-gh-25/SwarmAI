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
import { computeGutterWindow, GUTTER_VIRTUALIZE_MIN_LINES } from './FileEditorCore';
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
  /** Send a single comment immediately to the agent. */
  onSendSingle?: (text: string, lineNumber: number) => void;
  /** Check if a comment has been applied (target lines changed). */
  isCommentApplied?: (comment: ReviewComment) => boolean;
  /** Scroll-area viewport height (px) — drives the virtualized line window. */
  viewportHeight?: number;
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
  onSendSingle,
  isCommentApplied,
  viewportHeight = 0,
}: ReviewModeGutterProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const gutterWidth = `${Math.max(3, String(lineCount).length) + 1}ch`;
  const virtualize = lineCount > GUTTER_VIRTUALIZE_MIN_LINES;
  const { start: winStart, end: winEnd } = virtualize
    ? computeGutterWindow(lineCount, scrollTop, viewportHeight)
    : { start: 0, end: lineCount };

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
      {/* Line numbers — virtualized above GUTTER_VIRTUALIZE_MIN_LINES so a
          large file in review mode doesn't mount lineCount clickable divs.
          A full-height sizer preserves scroll height; the window is absolutely
          positioned at its start line, then shifted with translateY(-scrollTop)
          exactly like the small-file path. */}
      <div
        className="relative"
        style={{ transform: `translateY(-${scrollTop}px)` }}
      >
        {/* Sizer consistent with LineGutter: lineCount*24 + 32 (p-4 top+bottom).
            See LineGutter's sizer comment — inert while numbers are absolute,
            kept consistent to avoid a latent clip on refactor. */}
        {virtualize && (
          <div data-testid="review-gutter-sizer" style={{ height: `${lineCount * LINE_HEIGHT + 32}px` }} />
        )}
        <div
          data-testid="review-gutter-line-numbers"
          className={
            virtualize
              ? 'absolute left-0 right-0 font-mono text-xs leading-6 text-right pr-2'
              : 'font-mono text-xs leading-6 text-right pr-2 pt-4'
          }
          style={virtualize ? { top: `${winStart * LINE_HEIGHT + EDITOR_PADDING_TOP}px` } : undefined}
        >
        {Array.from({ length: winEnd - winStart }, (_, i) => {
          const lineNum = winStart + i + 1;
          const comment = getCommentForLine(lineNum);
          const hasComment = !!comment;
          const isPopoverTarget = activePopoverLine === lineNum;
          const applied = hasComment && isCommentApplied ? isCommentApplied(comment) : false;

          return (
            <div
              key={lineNum}
              style={virtualize ? { height: `${LINE_HEIGHT}px` } : undefined}
              className={`relative cursor-pointer transition-colors ${
                isPopoverTarget
                  ? 'bg-[var(--color-primary)]/15 text-[var(--color-primary)]'
                  : applied
                    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                    : hasComment
                      ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                      : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
              }`}
              onClick={() => handleLineClick(lineNum)}
              title={applied && comment ? `Applied: ${comment.text}` : hasComment && comment ? `Comment: ${comment.text}` : `Add comment on line ${lineNum}`}
              role="button"
              aria-label={hasComment ? `Edit comment on line ${lineNum}` : `Add comment on line ${lineNum}`}
            >
              {applied ? (
                <span className="absolute left-0.5 top-1/2 -translate-y-1/2 text-[10px]">✅</span>
              ) : hasComment ? (
                <span className="absolute left-0.5 top-1/2 -translate-y-1/2 text-[10px]">💬</span>
              ) : null}
              <span className={hasComment || applied ? 'pl-3' : ''}>{lineNum}</span>
            </div>
          );
        })}
        </div>
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
          onSendSingle={onSendSingle ? (text) => {
            // Add comment first (if new), then send to agent
            const existingComment = getCommentForLine(activePopoverLine);
            if (!existingComment) {
              onAddComment(activePopoverLine, activePopoverLine, text);
            }
            onSendSingle(text, activePopoverLine);
          } : undefined}
          topOffset={popoverTopOffset}
          anchorRef={containerRef}
        />
      )}
    </div>
  );
}
