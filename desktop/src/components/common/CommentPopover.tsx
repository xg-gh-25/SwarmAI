/**
 * CommentPopover — Inline popover for adding/editing review comments.
 *
 * Positioned next to the clicked gutter line. Shows a small text input
 * with Save/Cancel actions. Supports both new comment creation and
 * editing existing comments.
 *
 * Key exports:
 * - `CommentPopover` (default) — Popover component
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';

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
    const measuredHeight = popoverRef.current?.offsetHeight ?? 180;
    let top = rect.top + topOffset;
    // Clamp: don't let popover go below viewport
    if (top + measuredHeight > window.innerHeight - 8) {
      top = window.innerHeight - measuredHeight - 8;
    }
    // Clamp: don't let popover go above viewport
    if (top < 8) top = 8;
    setPosition({ top, left: rect.right + 4 });
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
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
      }
    },
    [handleSubmit, onCancel],
  );

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
      className="fixed z-[1000] w-72 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl"
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
          <span className="material-symbols-outlined text-sm">close</span>
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
          rows={2}
          className="w-full px-2 py-1.5 text-xs rounded bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)] resize-none"
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
          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            className="px-3 py-1 text-xs rounded bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            data-testid="comment-submit"
          >
            {initialText ? 'Update' : 'Add'}
          </button>
        </div>
      </div>

      {/* Keyboard hint */}
      <div className="px-3 pb-1.5 text-[10px] text-[var(--color-text-dim)]">
        ⌘+Enter to save · Esc to cancel
      </div>
    </div>
  );

  // Render via Portal to escape overflow-hidden ancestors
  return createPortal(popoverContent, document.body);
}
