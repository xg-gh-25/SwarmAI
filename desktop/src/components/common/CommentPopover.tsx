/**
 * CommentPopover — Inline popover for adding/editing review comments.
 *
 * Positioned next to the clicked gutter line and kept inside the viewport.
 * Shows a multi-line text input (user-resizable vertically) with Save/Cancel
 * actions. Supports both new comment creation and editing existing comments.
 *
 * Key exports:
 * - `CommentPopover` (default) — Popover component
 * - `COMMENT_POPOVER_CLASS` / `COMMENT_TEXTAREA_CLASS` / `COMMENT_TEXTAREA_ROWS`
 *   — shared sizing, also used by FileEditorCore's selection-comment popover
 * - `clampToViewport` — keeps a fixed-position popover fully on screen
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useEscape } from '../../hooks/useLayer';

/** Popover shell: wide enough for a sentence or two of feedback per line. */
export const COMMENT_POPOVER_CLASS =
  'fixed z-[1000] w-[28rem] max-w-[calc(100vw-16px)] bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl';
/** Comment input: readable text size, drag to grow up to 60% of the viewport. */
export const COMMENT_TEXTAREA_CLASS =
  'w-full px-2.5 py-2 text-sm leading-5 rounded bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)] resize-y min-h-[7.5rem] max-h-[60vh]';
export const COMMENT_TEXTAREA_ROWS = 6;

const VIEWPORT_MARGIN = 8;

/** Clamp a fixed popover's top-left so a `width`×`height` box stays on screen. */
export function clampToViewport(
  top: number,
  left: number,
  width: number,
  height: number,
): { top: number; left: number } {
  const maxTop = window.innerHeight - height - VIEWPORT_MARGIN;
  const maxLeft = window.innerWidth - width - VIEWPORT_MARGIN;
  return {
    top: Math.max(VIEWPORT_MARGIN, Math.min(top, maxTop)),
    left: Math.max(VIEWPORT_MARGIN, Math.min(left, maxLeft)),
  };
}

interface CommentPopoverProps {
  /** 1-based line number this popover is anchored to. */
  lineNumber: number;
  /** Pre-filled text when editing an existing comment. */
  initialText?: string;
  /** Called when user submits a new/edited comment. */
  onSubmit: (text: string) => void;
  /** Called when user cancels or clicks away. */
  onCancel: () => void;
  /** Called when user wants to delete this comment (only shown for existing). */
  onDelete?: () => void;
  /** Called to immediately send this single comment to the agent. */
  onSendSingle?: (text: string) => void;
  /** Vertical offset from top of the editor area (px). */
  topOffset: number;
  /** Ref to the gutter container — used to calculate portal position. */
  anchorRef?: React.RefObject<HTMLDivElement | null>;
}

export default function CommentPopover({
  lineNumber,
  initialText = '',
  onSubmit,
  onCancel,
  onDelete,
  onSendSingle,
  topOffset,
  anchorRef,
}: CommentPopoverProps) {
  const [text, setText] = useState(initialText);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Calculate screen-absolute position from the gutter anchor.
  // Re-runs on scroll/resize so the popover tracks the anchor.
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  const recalcPosition = useCallback(() => {
    if (!anchorRef?.current) return;
    const rect = anchorRef.current.getBoundingClientRect();
    const measuredHeight = popoverRef.current?.offsetHeight ?? 260;
    const measuredWidth = popoverRef.current?.offsetWidth ?? 448;
    setPosition(clampToViewport(rect.top + topOffset, rect.right + 4, measuredWidth, measuredHeight));
  }, [anchorRef, topOffset]);

  // Initial position + reposition on scroll/resize
  useEffect(() => {
    recalcPosition();
    window.addEventListener('scroll', recalcPosition, true);
    window.addEventListener('resize', recalcPosition);
    return () => {
      window.removeEventListener('scroll', recalcPosition, true);
      window.removeEventListener('resize', recalcPosition);
    };
  }, [recalcPosition]);

  useEffect(() => {
    // Auto-focus and select text on mount
    const el = inputRef.current;
    if (el) {
      el.focus();
      if (initialText) {
        el.select();
      }
    }
  }, [initialText]);

  const handleSubmit = useCallback(() => {
    const trimmed = text.trim();
    if (trimmed) {
      onSubmit(trimmed);
    }
  }, [text, onSubmit]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );
  // A layer above the editor: Escape cancels the comment, not the editor, wherever
  // focus is inside the popover.
  useEscape(true, onCancel, popoverRef);

  // Click-outside handler: close popover when clicking outside it
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onCancel();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [onCancel]);

  const popoverContent = (
    <div
      ref={popoverRef}
      className={COMMENT_POPOVER_CLASS}
      style={position ? { top: position.top, left: position.left } : { top: topOffset, left: 0 }}
      data-testid="comment-popover"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-hover)] rounded-t-lg">
        <span className="text-xs text-[var(--color-text-muted)] font-medium">
          Comment on line {lineNumber}
        </span>
        <button
          onClick={onCancel}
          className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
          aria-label="Close"
        >
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      </div>

      {/* Input */}
      <div className="p-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your feedback..."
          rows={COMMENT_TEXTAREA_ROWS}
          className={COMMENT_TEXTAREA_CLASS}
          data-testid="comment-input"
        />
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between px-2 pb-2">
        <div>
          {onDelete && (
            <button
              onClick={onDelete}
              className="px-2 py-1 text-xs rounded text-red-400 hover:text-red-300 hover:bg-red-500/10 transition-colors"
              data-testid="comment-delete"
            >
              Delete
            </button>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onCancel}
            className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          >
            Cancel
          </button>
          {onSendSingle && (
            <button
              onClick={() => {
                const trimmed = text.trim();
                if (trimmed) onSendSingle(trimmed);
              }}
              disabled={!text.trim()}
              className="btn-sm btn-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              data-testid="comment-send-single"
              title="Send this comment to agent immediately"
            >
              <span className="material-symbols-outlined icon-xs">send</span>
              Send
            </button>
          )}
          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            className="btn-sm btn-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            data-testid="comment-submit"
          >
            {initialText ? 'Update' : 'Add'}
          </button>
        </div>
      </div>

      {/* Keyboard hint */}
      <div className="px-3 pb-1.5 text-[11px] text-[var(--color-text-dim)]">
        ⌘+Enter to save · Esc to cancel
      </div>
    </div>
  );

  // Render via Portal to escape overflow-hidden ancestors
  return createPortal(popoverContent, document.body);
}
