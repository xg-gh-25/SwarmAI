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
 * - `useViewportClamp` — places a fixed popover at an anchor and keeps it on screen
 *   while it, the window or the app zoom changes size
 *
 * Coordinates: everything handed to `clampToViewport` / `useViewportClamp` is in
 * layout CSS px (what `style.top/left` are in). The app zooms with CSS `zoom` on
 * <html>, under which `getBoundingClientRect()` and `innerWidth/innerHeight` stay in
 * unzoomed client px, so a rect read for an anchor is multiplied by
 * `clientToCssScale()` first (utils/layoutViewport.ts). Mixing the two put the
 * popover past the window's right and bottom edges at any zoom above 100%.
 */

import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useEscape } from '../../hooks/useLayer';
import {
  APP_ZOOM_EVENT,
  clientToCssScale,
  layoutViewportHeight,
  layoutViewportWidth,
} from '../../utils/layoutViewport';

/** Popover shell: wide enough for a sentence or two of feedback per line. */
export const COMMENT_POPOVER_CLASS =
  'fixed z-[1000] w-[28rem] max-w-[calc(100vw-16px)] bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl';
/** Comment input: readable text size, drag to grow up to 60% of the viewport. */
export const COMMENT_TEXTAREA_CLASS =
  'w-full px-2.5 py-2 text-sm leading-5 rounded bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)] resize-y min-h-[7.5rem] max-h-[60vh]';
export const COMMENT_TEXTAREA_ROWS = 6;

const VIEWPORT_MARGIN = 8;

/**
 * Clamp a fixed popover's top-left so a `width`×`height` box stays on screen.
 * All values are layout CSS px (see the module header).
 */
export function clampToViewport(
  top: number,
  left: number,
  width: number,
  height: number,
): { top: number; left: number } {
  const maxTop = layoutViewportHeight() - height - VIEWPORT_MARGIN;
  const maxLeft = layoutViewportWidth() - width - VIEWPORT_MARGIN;
  return {
    top: Math.max(VIEWPORT_MARGIN, Math.min(top, maxTop)),
    left: Math.max(VIEWPORT_MARGIN, Math.min(left, maxLeft)),
  };
}

export interface ClampedPlacement {
  top: number;
  left: number;
  /** The popover never grows taller than the window; past this it scrolls inside. */
  maxHeight: number;
}

/**
 * Place the fixed popover in `ref` at `getAnchor()` (layout CSS px), clamped to the
 * window. Re-places it whenever the popover changes size (the comment box is
 * drag-resizable, so a box opened at the bottom edge used to push its buttons off
 * screen), on scroll (the anchor line moves), on window resize and on app zoom
 * (CSS zoom fires no resize event). Returns null until the first measurement, which
 * happens in a layout effect, before the first paint.
 */
export function useViewportClamp(
  ref: React.RefObject<HTMLElement | null>,
  getAnchor: () => { top: number; left: number } | null,
): ClampedPlacement | null {
  const [placement, setPlacement] = useState<ClampedPlacement | null>(null);

  const place = useCallback(() => {
    const el = ref.current;
    const anchor = getAnchor();
    if (!el || !anchor) return;
    const { top, left } = clampToViewport(anchor.top, anchor.left, el.offsetWidth, el.offsetHeight);
    const maxHeight = layoutViewportHeight() - 2 * VIEWPORT_MARGIN;
    setPlacement((prev) =>
      prev && prev.top === top && prev.left === left && prev.maxHeight === maxHeight
        ? prev
        : { top, left, maxHeight },
    );
  }, [ref, getAnchor]);

  useLayoutEffect(() => {
    place();
    const el = ref.current;
    const observer = el && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => place()) : null;
    if (el && observer) observer.observe(el);
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    window.addEventListener(APP_ZOOM_EVENT, place);
    return () => {
      observer?.disconnect();
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
      window.removeEventListener(APP_ZOOM_EVENT, place);
    };
  }, [place, ref]);

  return placement;
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
  /** Vertical offset of the line from the top of the anchor (layout CSS px). */
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
  const { t } = useTranslation();
  const [text, setText] = useState(initialText);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Anchor: just right of the gutter, at the clicked line. The gutter rect is client
  // px, `topOffset` is CSS px (line height × index), so the rect is scaled first.
  const getAnchor = useCallback(() => {
    if (!anchorRef?.current) return null;
    const rect = anchorRef.current.getBoundingClientRect();
    const s = clientToCssScale();
    return { top: rect.top * s + topOffset, left: rect.right * s + 4 };
  }, [anchorRef, topOffset]);
  const position = useViewportClamp(popoverRef, getAnchor);

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
      style={position
        ? { top: position.top, left: position.left, maxHeight: position.maxHeight, overflowY: 'auto' }
        : { top: topOffset, left: 0 }}
      data-testid="comment-popover"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-hover)] rounded-t-lg">
        <span className="text-xs text-[var(--color-text-muted)] font-medium">
          {t('ui.CommentPopover.commentOnLine', { line: lineNumber })}
        </span>
        <button
          onClick={onCancel}
          className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
          aria-label={t('common.button.close')}
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
          placeholder={t('ui.CommentPopover.placeholder')}
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
              {t('common.button.delete')}
            </button>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onCancel}
            className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          >
            {t('common.button.cancel')}
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
              title={t('ui.CommentPopover.sendNowTitle')}
            >
              <span className="material-symbols-outlined icon-xs">send</span>
              {t('common.button.send')}
            </button>
          )}
          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            className="btn-sm btn-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            data-testid="comment-submit"
          >
            {initialText ? t('ui.CommentPopover.update') : t('common.button.add')}
          </button>
        </div>
      </div>

      {/* Keyboard hint */}
      <div className="px-3 pb-1.5 text-[11px] text-[var(--color-text-dim)]">
        {t('ui.CommentPopover.keyHint')}
      </div>
    </div>
  );

  // Render via Portal to escape overflow-hidden ancestors
  return createPortal(popoverContent, document.body);
}
