/**
 * ReviewCommentsTray — the Canvas file's pending review comments, sent as one batch.
 *
 * Every way of commenting on a file (a gutter line, a diff line, a selection in the
 * source view or the markdown preview) adds to one list (`useReviewMode`). This tray
 * shows it while it is not empty: a count that opens the list (each comment whole,
 * with the text it was made on, editable and removable) and one Send that hands all
 * of them to the chat as a single message, so the agent revises once.
 *
 * The list is a layer: Escape and a click outside close it. Clear all asks once
 * (a second click clears).
 *
 * Key exports:
 * - `ReviewCommentsTray` (default) — count + list + Send
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useEscape } from '../../hooks/useLayer';
import { hasLines, type ReviewComment } from '../../hooks/useReviewMode';
import { clientToCssScale } from '../../utils/layoutViewport';
import { COMMENT_POPOVER_CLASS, COMMENT_TEXTAREA_CLASS, placedStyle, usePopoverPlacement, type PopoverTarget } from './CommentPopover';

interface ReviewCommentsTrayProps {
  comments: ReviewComment[];
  onUpdate: (id: string, text: string) => void;
  onRemove: (id: string) => void;
  onClearAll: () => void;
  onSendAll: () => void;
}

function useLocationLabel() {
  const { t } = useTranslation();
  return (c: ReviewComment): string => {
    const where = !hasLines(c)
      ? t('ui.ReviewCommentsTray.previewSelection')
      : c.lineStart === c.lineEnd
        ? t('ui.ReviewCommentsTray.line', { line: c.lineStart })
        : t('ui.ReviewCommentsTray.lines', { start: c.lineStart, end: c.lineEnd });
    return c.diffContext ? `${where} · ${t(`ui.ReviewCommentsTray.diff_${c.diffContext.type}`)}` : where;
  };
}

function CommentItem({
  comment,
  onUpdate,
  onRemove,
}: {
  comment: ReviewComment;
  onUpdate: (id: string, text: string) => void;
  onRemove: (id: string) => void;
}) {
  const { t } = useTranslation();
  const label = useLocationLabel();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.text);
  const quote = comment.quote ?? comment.diffContext?.content ?? comment.originalLineContent;

  const save = () => {
    const trimmed = draft.trim();
    if (trimmed) onUpdate(comment.id, trimmed);
    setEditing(false);
  };

  return (
    <li className="px-3 py-2 border-b border-[var(--color-border)] last:border-b-0" data-testid="review-tray-item">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-[var(--color-text-muted)]">{label(comment)}</span>
        {!editing && (
          <span className="flex items-center gap-0.5">
            <button
              className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]"
              onClick={() => { setDraft(comment.text); setEditing(true); }}
              aria-label={t('common.button.edit')}
              title={t('common.button.edit')}
              data-testid="review-tray-edit"
            >
              <span className="material-symbols-outlined icon-xs">edit</span>
            </button>
            <button
              className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-500/10"
              onClick={() => onRemove(comment.id)}
              aria-label={t('common.button.delete')}
              title={t('common.button.delete')}
              data-testid="review-tray-remove"
            >
              <span className="material-symbols-outlined icon-xs">delete</span>
            </button>
          </span>
        )}
      </div>
      {quote?.trim() && (
        <div
          className="mt-1 pl-2 border-l-2 border-[var(--color-border)] text-xs text-[var(--color-text-muted)] whitespace-pre-wrap line-clamp-3"
          title={quote}
        >
          {quote}
        </div>
      )}
      {editing ? (
        <div className="mt-1.5">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
            }}
            rows={3}
            className={COMMENT_TEXTAREA_CLASS.replace('min-h-[7.5rem]', 'min-h-[4.5rem]')}
            autoFocus
            data-testid="review-tray-edit-input"
          />
          <div className="flex justify-end gap-1 mt-1">
            <button className="btn-sm btn-ghost" onClick={() => setEditing(false)}>{t('common.button.cancel')}</button>
            <button className="btn-sm btn-primary" onClick={save} disabled={!draft.trim()} data-testid="review-tray-edit-save">
              {t('common.button.save')}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-1 text-sm text-[var(--color-text)] whitespace-pre-wrap">{comment.text}</div>
      )}
    </li>
  );
}

/** The open list: a layer above the count chip, kept on screen while it changes size. */
function TrayList({
  chipRef,
  onClose,
  comments,
  onUpdate,
  onRemove,
  onClearAll,
}: {
  chipRef: React.RefObject<HTMLButtonElement | null>;
  onClose: () => void;
} & Omit<ReviewCommentsTrayProps, 'onSendAll'>) {
  const { t } = useTranslation();
  const listRef = useRef<HTMLDivElement>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  useEscape(true, onClose, listRef);

  // Above the count chip (the tray sits at the bottom of the panel).
  const getTarget = useCallback((): PopoverTarget | null => {
    const chip = chipRef.current;
    if (!chip) return null;
    const r = chip.getBoundingClientRect();
    const s = clientToCssScale();
    return { top: r.top * s, bottom: r.bottom * s, left: r.left * s, prefer: 'above' };
  }, [chipRef]);
  const placement = usePopoverPlacement(listRef, getTarget);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (listRef.current?.contains(target) || chipRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [chipRef, onClose]);

  return createPortal(
    <div
      ref={listRef}
      className={COMMENT_POPOVER_CLASS}
      style={placedStyle(placement)}
      role="dialog"
      aria-label={t('ui.ReviewCommentsTray.listTitle')}
      data-testid="review-tray-list"
    >
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-hover)] rounded-t-lg">
        <span className="text-xs font-medium text-[var(--color-text-muted)]">
          {t('ui.ReviewCommentsTray.count', { count: comments.length })}
        </span>
        <button
          className={confirmClear
            ? 'px-1.5 py-0.5 text-[11px] rounded text-red-400 bg-red-500/10 font-medium'
            : 'px-1.5 py-0.5 text-[11px] rounded text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-500/10'}
          // Two clicks: the first asks, the second clears (typed comments are not undoable).
          onClick={() => (confirmClear ? onClearAll() : setConfirmClear(true))}
          onBlur={() => setConfirmClear(false)}
          data-testid="review-tray-clear"
        >
          {confirmClear
            ? t('ui.ReviewCommentsTray.confirmClear', { count: comments.length })
            : t('ui.ReviewCommentsTray.clearAll')}
        </button>
      </div>
      <ul>
        {comments.map((c) => (
          <CommentItem key={c.id} comment={c} onUpdate={onUpdate} onRemove={onRemove} />
        ))}
      </ul>
    </div>,
    document.body,
  );
}

export default function ReviewCommentsTray({ comments, onUpdate, onRemove, onClearAll, onSendAll }: ReviewCommentsTrayProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const chipRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);

  // Nothing left to show → nothing to keep open.
  useEffect(() => { if (comments.length === 0) setOpen(false); }, [comments.length]);

  if (comments.length === 0) return null;

  return (
    <div className="flex items-center gap-1" data-testid="review-tray">
      <button
        ref={chipRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={clsx(
          'btn-sm btn-ghost text-amber-600 dark:text-amber-400',
          open && 'bg-amber-500/15',
        )}
        data-testid="review-tray-count"
        title={t('ui.ReviewCommentsTray.showList')}
      >
        <span className="material-symbols-outlined icon-xs">comment</span>
        {t('ui.ReviewCommentsTray.count', { count: comments.length })}
      </button>
      <button
        type="button"
        className="btn-sm btn-primary"
        onClick={() => { setOpen(false); onSendAll(); }}
        data-testid="review-tray-send"
        title={t('ui.ReviewCommentsTray.sendTitle', { count: comments.length })}
      >
        <span className="material-symbols-outlined icon-xs">send</span>
        {t('ui.ReviewCommentsTray.send')}
      </button>
      {open && (
        <TrayList
          chipRef={chipRef}
          onClose={close}
          comments={comments}
          onUpdate={onUpdate}
          onRemove={onRemove}
          onClearAll={onClearAll}
        />
      )}
    </div>
  );
}
