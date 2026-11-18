/**
 * CommentPopover — the review comment box of the Canvas file view, and its placement.
 *
 * One box (`CommentBox`) serves every way of commenting on a file: a gutter line, a
 * diff line (both through `CommentPopover`) and a text selection (FileEditorCore's
 * SelectionCommentPopover). It shows what is being commented on (the line or the
 * selected text, quoted), a roomy input that grows with what is typed, and the
 * actions: the primary one (Add / Update), optionally Send now and Delete.
 *
 * Placement (`usePopoverPlacement`): the box is placed NEXT TO the commented text,
 * never on it — below it when it fits there, else above it, else on the side with
 * more room (scrolling inside). It always stays in the window: 8 px from the edges
 * and below the macOS title bar, which overlays the top 28 px of the window
 * (TitleBarStyle::Overlay). It re-places when it changes size (the input grows), on
 * scroll, on window resize and on app zoom.
 *
 * Coordinates: every target handed to the placement is in layout CSS px (what
 * `style.top/left` are in). The app zooms with CSS `zoom` on <html>, under which
 * `getBoundingClientRect()` stays in unzoomed client px, so a rect read for a target
 * is multiplied by `clientToCssScale()` first (utils/layoutViewport.ts).
 *
 * Key exports:
 * - `CommentPopover` (default) — the box for a gutter or diff line
 * - `CommentBox` — the box itself, placed next to any target
 * - `usePopoverPlacement` / `placeNextTo` — placement next to a target, on screen
 * - `COMMENT_POPOVER_CLASS` / `COMMENT_TEXTAREA_CLASS` — the shared shell and input look
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

/** Popover shell: a calm card, wide enough for a few sentences of feedback. */
export const COMMENT_POPOVER_CLASS =
  'fixed z-[1000] w-[34rem] max-w-[calc(100vw-24px)] bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl';
/** Comment input: readable text, room for several lines, grows with the text. */
export const COMMENT_TEXTAREA_CLASS =
  'block w-full px-3 py-2.5 text-[14px] leading-6 rounded-lg bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)] focus:ring-2 focus:ring-[var(--color-primary)]/15 resize-none overflow-y-auto min-h-[7.5rem] max-h-[45vh] placeholder:text-[var(--color-text-dim)]';

const VIEWPORT_MARGIN = 8;
/** The macOS title bar overlays the top 28 px of the window (TitleBarStyle::Overlay). */
const TOP_INSET = 28 + VIEWPORT_MARGIN;
/** Space between the commented text and the box. */
const GAP = 6;
/** Less room than this next to the target (about the box's own minimum: label,
 *  quote, input, actions) and the box is simply kept on screen, so its actions never
 *  scroll out of reach inside it. */
const MIN_USEFUL_ROOM = 280;

/** What a popover is placed next to (the commented line, selection or button), layout CSS px. */
export interface PopoverTarget {
  top: number;
  bottom: number;
  left: number;
  /** The side to try first (default below). */
  prefer?: 'below' | 'above';
}

export interface ClampedPlacement {
  top: number;
  left: number;
  /** The box never grows taller than the room it was given; past this it scrolls inside. */
  maxHeight: number;
}

/**
 * Place a `width`×`height` box next to `target` without covering it, inside the
 * window: on the preferred side when it fits, else the other side when that fits,
 * else the side with more room (with that room as its max height). When neither
 * side has useful room (the target scrolled out of view, a tiny window) the box is
 * just kept on screen.
 */
export function placeNextTo(target: PopoverTarget, width: number, height: number): ClampedPlacement {
  const minTop = TOP_INSET;
  const maxBottom = layoutViewportHeight() - VIEWPORT_MARGIN;
  const roomBelow = maxBottom - (target.bottom + GAP);
  const roomAbove = target.top - GAP - minTop;
  const fitsBelow = height <= roomBelow;
  const fitsAbove = height <= roomAbove;
  const side: 'below' | 'above' = target.prefer === 'above'
    ? (fitsAbove || (!fitsBelow && roomAbove >= roomBelow) ? 'above' : 'below')
    : (fitsBelow || (!fitsAbove && roomBelow >= roomAbove) ? 'below' : 'above');
  const room = side === 'below' ? roomBelow : roomAbove;

  const maxLeft = layoutViewportWidth() - width - VIEWPORT_MARGIN;
  const left = Math.max(VIEWPORT_MARGIN, Math.min(target.left, maxLeft));

  if (room < Math.min(height, MIN_USEFUL_ROOM)) {
    const maxHeight = Math.max(120, maxBottom - minTop);
    const shown = Math.min(height, maxHeight);
    const top = Math.max(minTop, Math.min(side === 'below' ? target.bottom + GAP : target.top - GAP - shown, maxBottom - shown));
    return { top, left, maxHeight };
  }
  const shown = Math.min(height, room);
  return { top: side === 'below' ? target.bottom + GAP : target.top - GAP - shown, left, maxHeight: room };
}

/**
 * Place the fixed popover in `ref` next to `getTarget()` and keep it there, on
 * screen, while it, the window or the app zoom changes size, and on scroll.
 * Returns null until the first measurement, which happens in a layout effect,
 * before the first paint.
 */
export function usePopoverPlacement(
  ref: React.RefObject<HTMLElement | null>,
  getTarget: () => PopoverTarget | null,
): ClampedPlacement | null {
  const [placement, setPlacement] = useState<ClampedPlacement | null>(null);

  const place = useCallback(() => {
    const el = ref.current;
    const target = getTarget();
    if (!el || !target) return;
    // The box's own height, not the one its max-height clips it to.
    const height = Math.max(el.offsetHeight, el.scrollHeight);
    const next = placeNextTo(target, el.offsetWidth, height);
    setPlacement((prev) =>
      prev && prev.top === next.top && prev.left === next.left && prev.maxHeight === next.maxHeight
        ? prev
        : next,
    );
  }, [ref, getTarget]);

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

/** Style for a placed box: hidden until measured, so it never flashes at 0,0. */
export function placedStyle(placement: ClampedPlacement | null): React.CSSProperties {
  return placement
    ? { top: placement.top, left: placement.left, maxHeight: placement.maxHeight, overflowY: 'auto' }
    : { top: 0, left: 0, visibility: 'hidden' };
}

export interface CommentBoxProps {
  /** Where the box goes: next to this (layout CSS px). */
  getTarget: () => PopoverTarget | null;
  /** What is being commented on, e.g. "Comment on line 3". */
  label: string;
  /** The text being commented on, quoted above the input. */
  quote?: string;
  initialText?: string;
  placeholder: string;
  primaryLabel: string;
  onPrimary: (text: string) => void;
  /** Send only this comment to the chat now. */
  onSendNow?: (text: string) => void;
  /** Delete the comment being edited. */
  onDelete?: () => void;
  onCancel: () => void;
  /** Key hint under the actions. */
  hint: string;
  testIds: { root: string; input: string; primary: string; sendNow?: string; delete?: string };
}

/** The comment box: what is commented on, the input, the actions. Portaled to <body>. */
export function CommentBox({
  getTarget,
  label,
  quote,
  initialText = '',
  placeholder,
  primaryLabel,
  onPrimary,
  onSendNow,
  onDelete,
  onCancel,
  hint,
  testIds,
}: CommentBoxProps) {
  const { t } = useTranslation();
  const [text, setText] = useState(initialText);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const placement = usePopoverPlacement(boxRef, getTarget);

  // A layer above the editor: Escape cancels the comment, wherever focus is inside it.
  useEscape(true, onCancel, boxRef);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    if (initialText) el.select();
  }, [initialText]);

  // The input grows with what is typed (up to its max height, then it scrolls).
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    if (el.scrollHeight) el.style.height = `${el.scrollHeight + 2}px`;
  }, [text]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) onCancel();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onCancel]);

  const trimmed = text.trim();
  const submit = () => { if (trimmed) onPrimary(trimmed); };

  return createPortal(
    <div
      ref={boxRef}
      className={`${COMMENT_POPOVER_CLASS} flex flex-col gap-2.5 p-3`}
      style={placedStyle(placement)}
      role="dialog"
      aria-label={label}
      data-testid={testIds.root}
    >
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-[11.5px] font-medium text-[var(--color-text-muted)]">{label}</div>
          {quote?.trim() && (
            <div
              className="mt-1.5 pl-2.5 border-l-[3px] border-amber-500/60 text-[12.5px] leading-5 text-[var(--color-text-muted)] whitespace-pre-wrap break-words line-clamp-3"
              title={quote}
              data-testid="comment-box-quote"
            >
              {quote}
            </div>
          )}
        </div>
        <button
          onClick={onCancel}
          className="shrink-0 -mr-1 -mt-0.5 p-1 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          aria-label={t('common.button.close')}
        >
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      </div>

      <textarea
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); }
        }}
        placeholder={placeholder}
        rows={5}
        className={COMMENT_TEXTAREA_CLASS}
        data-testid={testIds.input}
      />

      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--color-text-dim)] truncate">{hint}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          {onDelete && (
            <button
              onClick={onDelete}
              className="btn-sm btn-ghost text-red-400 hover:text-red-300 hover:bg-red-500/10"
              data-testid={testIds.delete}
            >
              {t('common.button.delete')}
            </button>
          )}
          <button onClick={onCancel} className="btn-sm btn-ghost">
            {t('common.button.cancel')}
          </button>
          {onSendNow && (
            <button
              onClick={() => { if (trimmed) onSendNow(trimmed); }}
              disabled={!trimmed}
              className="btn-sm btn-soft disabled:opacity-40 disabled:cursor-not-allowed"
              data-testid={testIds.sendNow}
              title={t('ui.CommentPopover.sendNowTitle')}
            >
              <span className="material-symbols-outlined icon-xs">send</span>
              {t('ui.CommentPopover.sendNow')}
            </button>
          )}
          <button
            onClick={submit}
            disabled={!trimmed}
            className="btn-sm btn-primary disabled:opacity-40 disabled:cursor-not-allowed"
            data-testid={testIds.primary}
          >
            {primaryLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

interface CommentPopoverProps {
  /** 1-based line number this popover is anchored to. */
  lineNumber: number;
  /** The text of that line, quoted in the box. */
  lineText?: string;
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
  /** Vertical offset of the line's top from the top of the anchor (layout CSS px). */
  topOffset: number;
  /** Height of the line (layout CSS px). */
  lineHeight?: number;
  /** The element the line is in (the gutter, or the diff view). */
  anchorRef?: React.RefObject<HTMLDivElement | null>;
  /** The box's left edge: the anchor's right edge (beside a gutter) or its left edge. */
  anchorEdge?: 'right' | 'left';
}

/** The comment box for a gutter line or a diff line. */
export default function CommentPopover({
  lineNumber,
  lineText,
  initialText = '',
  onSubmit,
  onCancel,
  onDelete,
  onSendSingle,
  topOffset,
  lineHeight = 24,
  anchorRef,
  anchorEdge = 'right',
}: CommentPopoverProps) {
  const { t } = useTranslation();
  // The line: its top is the anchor's top + `topOffset`. The anchor rect is client
  // px, `topOffset` is CSS px (line height × index), so the rect is scaled first.
  const getTarget = useCallback((): PopoverTarget | null => {
    if (!anchorRef?.current) return null;
    const rect = anchorRef.current.getBoundingClientRect();
    const s = clientToCssScale();
    const top = rect.top * s + topOffset;
    const left = anchorEdge === 'right' ? rect.right * s + 4 : rect.left * s + 8;
    return { top, bottom: top + lineHeight, left };
  }, [anchorRef, topOffset, lineHeight, anchorEdge]);

  return (
    <CommentBox
      getTarget={getTarget}
      label={t('ui.CommentPopover.commentOnLine', { line: lineNumber })}
      quote={lineText}
      initialText={initialText}
      placeholder={t('ui.CommentPopover.placeholder')}
      primaryLabel={initialText ? t('ui.CommentPopover.update') : t('common.button.add')}
      onPrimary={onSubmit}
      onSendNow={onSendSingle}
      onDelete={onDelete}
      onCancel={onCancel}
      hint={t('ui.CommentPopover.keyHint')}
      testIds={{ root: 'comment-popover', input: 'comment-input', primary: 'comment-submit', sendNow: 'comment-send-single', delete: 'comment-delete' }}
    />
  );
}
