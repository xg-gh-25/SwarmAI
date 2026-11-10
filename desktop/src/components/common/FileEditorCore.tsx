/**
 * FileEditorCore — The shared editor surface used by both FileEditorModal
 * (fullscreen overlay) and FileEditorPanel (side panel).
 *
 * Extracted from FileEditorModal to enable dual-mount without duplicating
 * editor logic. Owns all editing state: content, syntax highlighting,
 * search, diff view, markdown preview, unsaved-changes guard.
 *
 * Key exports:
 * - `FileEditorCore`      — Main component (default export)
 * - `FileEditorCoreProps`  — Public prop interface
 *
 * Sub-components (module-private, carried over from FileEditorModal):
 * - `BreadcrumbBar`  — Breadcrumb path display
 * - `LineGutter`     — Synchronized line-number gutter
 * - `DiffView`       — Inline diff renderer
 * - `SearchBar`      — Floating Cmd+F search bar
 */

import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo, useDeferredValue } from 'react';
import { createPortal } from 'react-dom';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import hljs from 'highlight.js';
import Button from './Button';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { gitStatusBadge } from '../../utils/fileUtils';
import { FileTypeIcon } from './FileTypeIcon';
import { computeLineDiff } from '../../utils/lineDiff';
import type { DiffLine } from '../../utils/lineDiff';
import api from '../../services/api';
import { useDialogLayer, useEscape } from '../../hooks/useLayer';
import { CopyPathButton, copyPathNow } from './CopyPathButton';
import { previewKindOf, canDrawPreview, PreviewBody } from './filePreviews';
import { openExternal, openInSystemApp } from '../../utils/openExternal';
import { subscribeFileChanged } from '../../hooks/fileChangedBroker';
import { detectLanguage, isDirtyState, findAllMatches } from './FileEditorModal';
import type { SearchMatch } from './FileEditorModal';
import { useReviewMode } from '../../hooks/useReviewMode';
import type { DiffContext, ReviewComment } from '../../hooks/useReviewMode';
import ReviewModeGutter from './ReviewModeGutter';
import {
  computeGutterWindow,
  GUTTER_LINE_HEIGHT,
  GUTTER_VIRTUALIZE_MIN_LINES,
} from './gutterVirtualization';
import CommentPopover, {
  COMMENT_POPOVER_CLASS,
  COMMENT_TEXTAREA_CLASS,
  COMMENT_TEXTAREA_ROWS,
  clampToViewport,
} from './CommentPopover';
import { dispatchInjectChatInput } from '../../pages/chat/injectChatInput';

// HIGHLIGHT_MAX_CHARS / shouldProcessSync live in ./editorLimits (shared with the
// preview table, ./filePreviews, which FileEditorCore imports — no import cycle).
export { HIGHLIGHT_MAX_CHARS, shouldProcessSync } from './editorLimits';
import { shouldProcessSync } from './editorLimits';
import type { CanvasFileViewReport, CanvasFileView } from '../../utils/uiContext';

/**
 * Debounce (ms) for the syntax-highlight overlay. hljs.highlight runs over the
 * full file synchronously; without a debounce it re-ran on every keystroke,
 * causing typing lag on medium files (40-99K chars, under the freeze ceiling).
 * 150ms is below the "done typing" perception threshold — the colored overlay
 * settles a beat after you pause; the textarea text/caret is never delayed.
 */
export const HIGHLIGHT_DEBOUNCE_MS = 150;

/** Panel only: bottom padding of every content scroll layer, so the last lines
 *  scroll clear of the floating bar (a ~30px group + 10px inset + breathing room). */
export const PANEL_FLOAT_PAD = 56;

/** `swarm:file-changed` for the open file: at most one read per this many ms (C5). */
export const FILE_CHANGE_COALESCE_MS = 250;

/** The changed-lines overlay as segments: each changed line on its own, each run of
 *  unchanged lines joined into one, nothing after the last changed line. DOM nodes
 *  grow with the changes, not with the file (C5). */
export function changedLineSegments(content: string, changed: Set<number>): Array<{ changed: boolean; text: string }> {
  const lines = content.split('\n');
  const last = Math.min(lines.length, Math.max(0, ...changed));
  const out: Array<{ changed: boolean; text: string }> = [];
  let run: string[] = [];
  for (let n = 1; n <= last; n++) {
    if (changed.has(n)) {
      if (run.length) { out.push({ changed: false, text: run.join('\n') }); run = []; }
      out.push({ changed: true, text: lines[n - 1] });
    } else {
      run.push(lines[n - 1]);
    }
  }
  return out;
}

/** 1-based numbers of the lines that differ between two versions, by position. */
export function changedLineSet(before: string, after: string): Set<number> {
  const a = before.split('\n');
  const b = after.split('\n');
  const changed = new Set<number>();
  for (let i = 0; i < Math.max(a.length, b.length); i++) if (a[i] !== b[i]) changed.add(i + 1);
  return changed;
}


export interface FileEditorCoreProps {
  filePath: string;
  fileName: string;
  workspaceId: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
  gitStatus?: GitStatus;
  onAttachToChat?: (item: FileTreeItem) => void;
  isAttached?: boolean;
  readonly?: boolean;
  committedContent?: string;
  /** The committed baseline is still loading (FileViewer renders the content first
   *  and passes `committedContent` when it lands). Changes stays off until then. */
  baselinePending?: boolean;
  /**
   * When true, the editor opens with the diff view already shown (instead of
   * the default edit view). Used by the Radar ✍ Changes section so clicking a
   * changed file lands directly on its diff. Honored both on mount AND on the
   * file-switch reset effect (see below) — a prop-only useState init would be
   * clobbered by that effect, so the effect reads this too.
   */
  initialShowDiff?: boolean;
  /** 'panel' keeps editor open after save; 'modal' closes after save. */
  variant: 'panel' | 'modal';
  /** Toggle between panel ↔ modal mode. */
  onToggleMode?: () => void;
  /** Called after save when diff is non-empty (L2 auto-diff).
   *  Second arg is the fileName captured at save time to avoid stale closures. */
  onSaveWithDiff?: (diffSummary: string, fileName?: string) => void;
  /** Called on every content change so parent can track live edits.
   *  Used to preserve content across panel ↔ modal mode switches. */
  onContentChange?: (content: string) => void;
  /** Reports whether the editor holds unsaved edits: its content differs from its
   *  saved baseline. The editor is the one authority on this, because only it knows
   *  that baseline — a save or a reload from disk moves it, and a reload is not an
   *  edit (a parent comparing against its own copy from open time would call it one). */
  onDirtyChange?: (dirty: boolean) => void;
  /** Imperative close trigger from a parent-owned header (run_f49d3ff3 R2). When this
   *  counter INCREASES, FileEditorCore runs its OWN guarded close (handleCloseAttempt),
   *  so the unsaved-changes dialog is reused (not reimplemented). Used by FileViewer's
   *  unified file-chrome header in panel variant, where FileEditorCore's own filename+
   *  close chrome is suppressed. Undefined/0 = no external close (mount baseline). */
  closeSignal?: number;
  /** Called when the SHOWN view changes (preview / edit / changes, Review on/off) —
   *  not on typing or scrolling. A huge file whose preview falls back to the raw
   *  source reports `edit` (canvas-proprioception §3.2). */
  onViewChange?: (v: CanvasFileViewReport) => void;
  /** Not a file on this machine (a versions document: FileViewer's `versions`): the
   *  editor never reads `filePath` from disk (no refresh on a file change or on focus,
   *  no Reload, no Copy path) and draws no rendered preview of the text. Use with
   *  `readonly`. */
  detached?: boolean;
  /** The read-only banner's text, in place of the System Default one. */
  readonlyNote?: string;
}

/* ------------------------------------------------------------------ */
/*  BreadcrumbBar                                                       */
/* ------------------------------------------------------------------ */

function BreadcrumbBar({ filePath }: { filePath: string }) {
  const segments = filePath.split('/').filter(Boolean);
  return (
    <div
      className="flex items-center gap-1 text-xs min-w-0 overflow-hidden"
      style={{ direction: 'rtl' }}
      title={filePath}
    >
      <span style={{ direction: 'ltr', whiteSpace: 'nowrap' }} className="flex items-center gap-1">
        {segments.map((seg, i) => {
          const isLast = i === segments.length - 1;
          return (
            <span key={i} className="flex items-center gap-1 shrink-0">
              {i > 0 && (
                <span className="text-[var(--color-text-muted)]">&rsaquo;</span>
              )}
              <span
                className={isLast
                  ? 'font-semibold text-[var(--color-text)]'
                  : 'text-[var(--color-text-muted)]'
                }
              >
                {seg}
              </span>
            </span>
          );
        })}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  LineGutter                                                          */
/* ------------------------------------------------------------------ */

function LineGutter({ lineCount, scrollTop, activeLineNumber, viewportHeight = 0 }: {
  lineCount: number;
  scrollTop: number;
  activeLineNumber?: number;
  viewportHeight?: number;
}) {
  const gutterWidth = `${Math.max(3, String(lineCount).length) + 1}ch`;
  const virtualize = lineCount > GUTTER_VIRTUALIZE_MIN_LINES;

  // Small files: unchanged full render (zero regression).
  if (!virtualize) {
    return (
      <div
        className="shrink-0 select-none border-r border-[var(--color-border)] bg-[var(--color-background)] overflow-hidden"
        style={{ width: gutterWidth }}
      >
        <div
          data-testid="gutter-line-numbers"
          className="font-mono text-xs leading-6 text-right pr-2 pt-4"
          style={{ transform: `translateY(-${scrollTop}px)` }}
        >
          {Array.from({ length: lineCount }, (_, i) => {
            const lineNum = i + 1;
            const isActive = lineNum === activeLineNumber;
            return (
              <div
                key={lineNum}
                className={isActive
                  ? 'text-[var(--color-text)] bg-[var(--color-hover)]'
                  : 'text-[var(--color-text-muted)]'
                }
              >
                {lineNum}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // Large files: render only the visible window, sized by a full-height spacer
  // so the scrollbar reflects the whole file. The window is offset by its
  // start line, then shifted with the shared translateY(-scrollTop) so it
  // stays aligned with the textarea exactly like the un-virtualized path.
  const { start, end } = computeGutterWindow(lineCount, scrollTop, viewportHeight);
  return (
    <div
      className="shrink-0 select-none border-r border-[var(--color-border)] bg-[var(--color-background)] overflow-hidden"
      style={{ width: gutterWidth }}
    >
      <div
        className="relative"
        style={{ transform: `translateY(-${scrollTop}px)` }}
      >
        {/* Full-height sizer reserves the file's full scroll height so the
            translated (overflow-hidden) gutter box is tall enough that no line
            number is clipped at max scroll. lineCount*24 + 32 = content + p-4
            top+bottom padding, matching the textarea's scrollable height. Kept
            consistent with ReviewModeGutter's sizer. (The visible numbers are
            position:absolute, so this only matters if they ever become in-flow
            — but consistency prevents a latent clip on future refactor.) */}
        <div data-testid="gutter-sizer" style={{ height: `${lineCount * GUTTER_LINE_HEIGHT + 32}px` }} />
        <div
          data-testid="gutter-line-numbers"
          className="absolute left-0 right-0 font-mono text-xs leading-6 text-right pr-2"
          style={{ top: `${start * GUTTER_LINE_HEIGHT + 16 /* pt-4 */}px` }}
        >
          {Array.from({ length: end - start }, (_, i) => {
            const lineNum = start + i + 1;
            const isActive = lineNum === activeLineNumber;
            return (
              <div
                key={lineNum}
                style={{ height: `${GUTTER_LINE_HEIGHT}px` }}
                className={isActive
                  ? 'text-[var(--color-text)] bg-[var(--color-hover)]'
                  : 'text-[var(--color-text-muted)]'
                }
              >
                {lineNum}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Comment selection mark                                              */
/* ------------------------------------------------------------------ */

/** A selection under comment: source-text offsets, or a DOM range in the preview. */
type CommentMark =
  | { kind: 'source'; start: number; end: number }
  | { kind: 'preview'; range: Range };

/** CSS Custom Highlight name; styled by `::highlight(...)` in index.css. */
const COMMENT_HIGHLIGHT = 'swarm-comment-selection';

type HighlightRegistry = { set: (name: string, h: unknown) => void; delete: (name: string) => void };
function highlightRegistry(): { registry: HighlightRegistry; Highlight: new (...r: Range[]) => unknown } | null {
  const reg = (globalThis as { CSS?: { highlights?: HighlightRegistry } }).CSS?.highlights;
  const Ctor = (globalThis as { Highlight?: new (...r: Range[]) => unknown }).Highlight;
  return reg && Ctor ? { registry: reg, Highlight: Ctor } : null;
}

/* ------------------------------------------------------------------ */
/*  SelectionCommentPopover                                             */
/* ------------------------------------------------------------------ */

/**
 * Lightweight inline popover for selection-based comments.
 * Positioned directly at viewport coords (from selectionPopoverPos)
 * instead of using the anchorRef-based positioning in CommentPopover
 * which is designed for gutter-click review mode.
 */
function SelectionCommentPopover({
  position,
  onSubmit,
  onCancel,
}: {
  position: { top: number; left: number };
  onSubmit: (text: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  // A layer above the editor: Escape cancels the comment (not the editor), wherever
  // focus is inside it.
  useEscape(true, onCancel, popoverRef);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Keep the (large) popover on screen: measure after mount, clamp to the viewport.
  const anchorTop = position.top + 28;
  const [clamped, setClamped] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const el = popoverRef.current;
    if (!el) return;
    setClamped(clampToViewport(anchorTop, position.left, el.offsetWidth, el.offsetHeight));
  }, [anchorTop, position.left]);

  // Click-outside dismisses
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onCancel();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onCancel]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        const trimmed = text.trim();
        if (trimmed) onSubmit(trimmed);
      }
    },
    [text, onSubmit],
  );

  return createPortal(
    <div
      ref={popoverRef}
      className={COMMENT_POPOVER_CLASS}
      style={clamped ?? { top: anchorTop, left: position.left }}
      data-testid="selection-comment-popover"
    >
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-hover)] rounded-t-lg">
        <span className="text-xs text-[var(--color-text-muted)] font-medium">
          {t('ui.FileEditorCore.commentOnSelection')}
        </span>
        <button
          onClick={onCancel}
          className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
        >
          <span className="material-symbols-outlined icon-sm">close</span>
        </button>
      </div>
      <div className="p-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('ui.FileEditorCore.instructionPlaceholder')}
          rows={COMMENT_TEXTAREA_ROWS}
          className={COMMENT_TEXTAREA_CLASS}
          data-testid="selection-comment-input"
        />
      </div>
      <div className="flex items-center justify-end gap-1 px-2 pb-2">
        <button
          onClick={onCancel}
          className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
        >
          {t('common.button.cancel')}
        </button>
        <button
          onClick={() => { const trimmed = text.trim(); if (trimmed) onSubmit(trimmed); }}
          disabled={!text.trim()}
          className="btn-sm btn-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          data-testid="selection-comment-send"
        >
          <span className="material-symbols-outlined icon-xs">send</span>
          {t('common.button.send')}
        </button>
      </div>
      <div className="px-3 pb-1.5 text-[11px] text-[var(--color-text-dim)]">
        {t('ui.FileEditorCore.sendHint')}
      </div>
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------------ */
/*  DiffView                                                            */
/* ------------------------------------------------------------------ */

/** LINE_HEIGHT must match the editor textarea (leading-6 = 24px). */
const DIFF_LINE_HEIGHT = 24;

interface DiffViewProps {
  lines: DiffLine[];
  /** Currently active popover diff-line index (0-based). */
  activePopoverIndex?: number | null;
  /** Called when a diff line is clicked. Index is 0-based into `lines`. */
  onLineClick?: (index: number) => void;
  /** Called to add a new comment on a diff line. */
  onAddComment?: (lineStart: number, lineEnd: number, text: string, diffContext?: DiffContext) => void;
  /** Called to update an existing comment. */
  onUpdateComment?: (id: string, text: string) => void;
  /** Called to remove an existing comment. */
  onRemoveComment?: (id: string) => void;
  /** Called to close the popover. */
  onCancelPopover?: () => void;
  /** Get comment for a specific diff-line index (0-based). */
  getCommentForDiffIndex?: (index: number) => ReviewComment | undefined;
  /** Comment being edited (for existing comment editing). */
  editingComment?: ReviewComment | null;
  /** Extra scroll room at the end (the panel's floating bar covers the bottom). */
  bottomPad?: number;
}

function DiffView({
  lines,
  activePopoverIndex,
  onLineClick,
  onAddComment,
  onUpdateComment,
  onRemoveComment,
  onCancelPopover,
  getCommentForDiffIndex,
  editingComment,
  bottomPad,
}: DiffViewProps) {
  const maxOld = lines.reduce((m, l) => Math.max(m, l.oldLineNumber ?? 0), 0);
  const maxNew = lines.reduce((m, l) => Math.max(m, l.newLineNumber ?? 0), 0);
  const gutterW = `${Math.max(3, String(Math.max(maxOld, maxNew)).length)}ch`;
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLPreElement>(null);
  const isInteractive = !!onLineClick;

  // Track scroll position as state so CommentPopover re-renders on scroll
  const [diffScrollTop, setDiffScrollTop] = useState(0);
  const handleDiffScroll = useCallback(() => {
    if (scrollRef.current) {
      setDiffScrollTop(scrollRef.current.scrollTop);
    }
  }, []);

  // Active line for popover
  const activeLine = activePopoverIndex != null ? lines[activePopoverIndex] : null;

  return (
    <pre
      ref={scrollRef}
      onScroll={handleDiffScroll}
      className="absolute inset-0 m-0 overflow-auto font-mono text-sm leading-6 bg-[var(--color-background)]"
      style={bottomPad ? { paddingBottom: bottomPad } : undefined}
      data-testid="diff-view"
    >
      {lines.map((line, i) => {
        let bgClass = '';
        if (line.type === 'added') bgClass = 'bg-[var(--color-git-added)]/15';
        if (line.type === 'removed') bgClass = 'bg-[var(--color-git-deleted)]/15';
        const hasComment = getCommentForDiffIndex ? !!getCommentForDiffIndex(i) : false;
        const isPopoverTarget = activePopoverIndex === i;

        return (
          <div
            key={i}
            className={clsx(
              'flex',
              bgClass,
              isInteractive && 'cursor-pointer',
              isInteractive && !hasComment && !isPopoverTarget && 'hover:brightness-95 dark:hover:brightness-110',
              isPopoverTarget && 'ring-1 ring-[var(--color-primary)]/40',
              hasComment && !isPopoverTarget && 'ring-1 ring-amber-500/30',
            )}
            onClick={isInteractive ? () => onLineClick!(i) : undefined}
            role={isInteractive ? 'button' : undefined}
            aria-label={isInteractive ? t('ui.FileEditorCore.commentOnDiffLine', { line: i + 1 }) : undefined}
          >
            {/* Two distinct gutters — OLD (before) is faint, NEW (after) is
                normal, separated by a 2px divider, so the columns read as
                "before | after" instead of two duplicated line-number strips
                (#6 clarity fix). Gutter tint is gated on line.type so an added
                row doesn't carry a red old-gutter (and vice-versa). */}
            <span
              className={clsx(
                'shrink-0 text-right pr-1 opacity-70 select-none text-[var(--color-text-faint,var(--color-text-muted))]',
                line.type === 'removed' && 'bg-[var(--color-git-deleted)]/[0.08]',
              )}
              style={{ width: gutterW }}
              title={t('ui.FileEditorCore.lineBefore')}
            >
              {line.oldLineNumber ?? ''}
            </span>
            <span
              className={clsx(
                'shrink-0 text-right pr-1 pl-1 select-none border-r-2 border-[var(--color-border-strong,var(--color-border))] text-[var(--color-text-muted)]',
                line.type === 'added' && 'bg-[var(--color-git-added)]/[0.08]',
              )}
              style={{ width: gutterW }}
              title={t('ui.FileEditorCore.lineAfter')}
            >
              {line.newLineNumber ?? ''}
            </span>
            <span className="shrink-0 w-4 text-center select-none text-[var(--color-text-muted)]">
              {line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
            </span>
            <span className="flex-1 whitespace-pre-wrap break-words px-2">
              {line.content}
            </span>
            {hasComment && (
              <span className="shrink-0 pr-2 text-[11px] self-center" title={t('ui.FileEditorCore.hasComment')}>💬</span>
            )}
          </div>
        );
      })}

      {/* CommentPopover for diff lines */}
      {activePopoverIndex != null && activeLine && onAddComment && onCancelPopover && scrollRef.current && (
        <CommentPopover
          lineNumber={activePopoverIndex + 1}
          initialText={editingComment?.text ?? ''}
          onSubmit={(text) => {
            if (editingComment && onUpdateComment) {
              onUpdateComment(editingComment.id, text);
            } else {
              // Use the real line number from the diff line for the comment model
              const lineNum = activeLine.newLineNumber ?? activeLine.oldLineNumber ?? activePopoverIndex + 1;
              onAddComment(lineNum, lineNum, text, {
                type: activeLine.type,
                oldLineNumber: activeLine.oldLineNumber,
                newLineNumber: activeLine.newLineNumber,
                content: activeLine.content,
              });
            }
          }}
          onCancel={onCancelPopover}
          onDelete={
            editingComment && onRemoveComment
              ? () => onRemoveComment(editingComment.id)
              : undefined
          }
          topOffset={activePopoverIndex * DIFF_LINE_HEIGHT - diffScrollTop}
          anchorRef={scrollRef as unknown as React.RefObject<HTMLDivElement>}
        />
      )}
    </pre>
  );
}

/* ------------------------------------------------------------------ */
/*  SearchBar                                                           */
/* ------------------------------------------------------------------ */

function SearchBar({
  searchQuery,
  onSearchChange,
  currentMatch,
  totalMatches,
  onNext,
  onPrevious,
  onClose,
  disabled = false,
}: {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  currentMatch: number;
  totalMatches: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
  /** True when search is unavailable (file too large for sync scan). */
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    } else if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault();
      onPrevious();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      onNext();
    }
  };

  const matchDisplay = disabled
    ? t('ui.FileEditorCore.searchDisabled')
    : t('ui.FileEditorCore.matchCount', {
        current: totalMatches > 0 ? currentMatch + 1 : 0,
        total: totalMatches,
      });

  return (
    <div
      className="absolute top-0 right-0 z-10 flex items-center gap-1 px-2 py-1.5 m-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-lg"
      data-testid="search-bar"
    >
      <input
        ref={inputRef}
        type="text"
        value={searchQuery}
        onChange={(e) => onSearchChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={t('ui.FileEditorCore.findPlaceholder')}
        className="w-40 px-2 py-0.5 text-xs rounded bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)]"
        data-testid="search-input"
      />
      <span className="text-xs text-[var(--color-text-muted)] min-w-[4rem] text-center">
        {matchDisplay}
      </span>
      <button
        onClick={onPrevious}
        disabled={totalMatches === 0}
        className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] disabled:opacity-40"
        aria-label={t('ui.FileEditorCore.previousMatch')}
      >
        <span className="material-symbols-outlined icon-sm">keyboard_arrow_up</span>
      </button>
      <button
        onClick={onNext}
        disabled={totalMatches === 0}
        className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] disabled:opacity-40"
        aria-label={t('ui.FileEditorCore.nextMatch')}
      >
        <span className="material-symbols-outlined icon-sm">keyboard_arrow_down</span>
      </button>
      <button
        onClick={onClose}
        className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]"
        aria-label={t('ui.FileEditorCore.closeSearch')}
      >
        <span className="material-symbols-outlined icon-sm">close</span>
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  FileEditorCore                                                      */
/* ------------------------------------------------------------------ */

export default function FileEditorCore({
  filePath,
  fileName,
  workspaceId,
  initialContent = '',
  onSave,
  onClose,
  gitStatus,
  onAttachToChat,
  isAttached,
  readonly,
  committedContent,
  baselinePending,
  initialShowDiff,
  variant,
  onToggleMode,
  onSaveWithDiff,
  onContentChange,
  onDirtyChange,
  closeSignal,
  onViewChange,
  detached = false,
  readonlyNote,
}: FileEditorCoreProps) {
  const { t } = useTranslation();
  // The file's rendered preview, if it has one — the ONE table in ./filePreviews
  // (md / svg / ipynb / mermaid; canvas-file-types.md P3).
  // A detached document's text is not this machine's (a Hive's change): never drawn.
  const previewKind = detached ? null : previewKindOf(fileName);
  // Whether the initial content is small enough to draw the preview. A huge file
  // must NOT default to preview — it defaults to edit (Gate perf).
  const canPreviewInitial = !!previewKind && canDrawPreview(previewKind, initialContent);

  const [content, setContent] = useState(initialContent);
  const [originalContent, setOriginalContent] = useState(committedContent ?? initialContent);
  // Tracks the last content successfully saved to disk, so hasUnsavedEdits
  // resets after save without destroying the committed (HEAD) baseline in
  // originalContent.  Null means "nothing saved yet — use initialContent".
  const [savedContent, setSavedContent] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showUnsavedWarning, setShowUnsavedWarning] = useState(false);
  // True when the unsaved-warning modal was opened by the Reload button (vs Close).
  // Distinguishes the modal's forward action: reload-from-disk vs revert-and-close.
  const [reloadPending, setReloadPending] = useState(false);
  const [showDiff, setShowDiff] = useState(initialShowDiff ?? false);
  // md/svg default to PREVIEW (view), not the raw edit textarea — a user opening
  // a doc in Canvas sees rendered content first. Guards: (1) a file opened with
  // autoDiff shows the diff, so preview must NOT also be on (Gate-1 Risk 4: else
  // "Back to Edit" would land on preview, not edit) — hence `!(initialShowDiff)`;
  // (2) a huge .md defaults to edit, never preview (perf — canPreviewInitial).
  const [previewOn, setPreviewOn] = useState(canPreviewInitial && !initialShowDiff);
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [activeLineNumber, setActiveLineNumber] = useState<number | undefined>(undefined);
  const [selectionText, setSelectionText] = useState('');
  const [selectionPopoverPos, setSelectionPopoverPos] = useState<{ top: number; left: number } | null>(null);
  const [showSelectionComment, setShowSelectionComment] = useState(false);
  // Lines of the current selection (source view only; the preview has no line map).
  const [selectionLines, setSelectionLines] = useState<[number, number] | null>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  // The selection being commented on stays marked while the comment popover is open:
  // focusing the popover's input hides the textarea's own selection paint and
  // replaces the preview's window selection. The last non-empty selection of either
  // surface is kept here and frozen into `commentMark` when Comment is clicked.
  const lastSelectionRef = useRef<CommentMark | null>(null);
  const [commentMark, setCommentMark] = useState<CommentMark | null>(null);
  const commentMarkRef = useRef<HTMLPreElement>(null);
  const [attachFeedback, setAttachFeedback] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  // Viewport height of the scroll area — drives the virtualized gutter's window.
  // Seeded from window.innerHeight so the FIRST paint never under-fills a tall
  // panel (Gate-2 B: a 0→fallback-1200 seed truncated the window on >1200px
  // displays for one frame). Corrected to the textarea's real clientHeight on
  // mount + scroll + resize below.
  const [viewportHeight, setViewportHeight] = useState(
    () => (typeof window !== 'undefined' ? window.innerHeight : 0),
  );
  const [highlightedLines, setHighlightedLines] = useState<Set<number>>(new Set());
  const lastFetchRef = useRef(Date.now());
  const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Debounce timer for the syntax-highlight overlay (distinct from the changed-line
  // decoration timer above). hljs.highlight is O(content-length) and ran synchronously
  // on every keystroke → typing lag on medium files. We debounce so it runs on a
  // typing pause; the textarea (which owns the caret + shows real text) is never
  // debounced, so input stays responsive.
  const highlightDebounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Clear pending highlight timer on unmount (covers reload button's setTimeout)
  useEffect(() => () => {
    clearTimeout(highlightTimerRef.current);
    clearTimeout(highlightDebounceRef.current);
  }, []);
  const rootRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightRef = useRef<HTMLPreElement>(null);

  // Track the scroll-area viewport height for the virtualized gutter. Seed on
  // mount and follow window/panel resizes via ResizeObserver. handleScroll also
  // refreshes it, so the window stays correct during interaction.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    setViewportHeight(ta.clientHeight);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setViewportHeight(ta.clientHeight));
    ro.observe(ta);
    return () => ro.disconnect();
  }, []);

  // Resolve workspace root once on mount — avoids API call on every button click
  const wsRootRef = useRef<string>('');
  useEffect(() => {
    let cancelled = false;
    api.get<{ file_path?: string; filePath?: string }>('/workspace')
      .then((resp) => {
        if (!cancelled) {
          wsRootRef.current = resp.data.file_path ?? resp.data.filePath ?? '';
        }
      })
      .catch(() => { /* workspace config unavailable — absolutePath will use relative */ });
    return () => { cancelled = true; };
  }, []);

  /** Build absolute path from cached workspace root + relative filePath.
   *  If filePath is already absolute, return it as-is. */
  const getAbsolutePath = useCallback(() => {
    if (filePath.startsWith('/')) return filePath;
    const ws = wsRootRef.current;
    return ws ? `${ws}/${filePath}` : filePath;
  }, [filePath]);

  // Panel variant (Canvas) uses a compact icon-only header — buttons drop their
  // text spans and carry the label in `title` (hover tooltip) instead, grouped by
  // vertical dividers. Stateful toggles keep their active-tint so the state signal
  // survives without text. Modal variant keeps the roomy icon+text header. `iconOnly`
  // gates the text spans; label always lives in `title` so nothing is lost.
  const iconOnly = variant === 'panel';
  // Header toolbar buttons: an icon button (icon-btn + icon-sm, index.css scale) in
  // the panel, a labelled chip in the modal. One definition for every button below.
  const toolBtn = iconOnly ? 'icon-btn' : 'flex items-center gap-1 px-2 py-1 rounded-lg text-xs';

  // The markdown preview renders from a deferred copy (C5): parsing a large document
  // or one the agent is rewriting never blocks typing, scrolling or the panel; React
  // renders the newest content as soon as it is idle. Nothing is skipped.
  const previewContent = useDeferredValue(content);
  const isDirty = isDirtyState(content, originalContent);
  const hasUnsavedEdits = isDirtyState(content, savedContent ?? initialContent);
  const language = detectLanguage(fileName);

  // ── AC2: Auto-refresh — poll file content every 3s when editor is open ──
  // Uses refs to avoid stale closures without re-creating the interval on every render.
  const contentRef = useRef(content);
  contentRef.current = content;
  const hasUnsavedEditsRef = useRef(hasUnsavedEdits);
  hasUnsavedEditsRef.current = hasUnsavedEdits;
  const onContentChangeRef = useRef(onContentChange);
  onContentChangeRef.current = onContentChange;
  const onDirtyChangeRef = useRef(onDirtyChange);
  onDirtyChangeRef.current = onDirtyChange;
  useEffect(() => { onDirtyChangeRef.current?.(hasUnsavedEdits); }, [hasUnsavedEdits]);

  // ── Auto-refresh via SSE file_changed event + diff highlight ──
  // A file the agent is writing can change many times a second. Events for this
  // file are coalesced (super-canvas-fixing.md C5): the first event schedules a read
  // FILE_CHANGE_COALESCE_MS later and the events after it join that read, at most one
  // read in flight (an event during a read schedules one more
  // after it), and a sequence number so only the newest read is ever applied.
  // A layout effect, so the subscription exists before the editor can be seen: as a
  // passive effect it ran after the paint, and a change announced in that gap was lost.
  useLayoutEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let again = false;
    let seq = 0;
    let alive = true;
    const read = async () => {
      timer = undefined;
      if (inFlight) { again = true; return; }
      if (hasUnsavedEditsRef.current) return; // Don't overwrite user's unsaved edits
      inFlight = true;
      const mine = ++seq;
      try {
        const resp = await api.get<{ content: string }>('/workspace/file', {
          params: { path: filePath },
        });
        const freshContent = resp.data.content;
        // Re-check after the await: the user may have typed while the read was in
        // flight, and those edits must not be overwritten by the disk copy.
        if (!alive || mine !== seq || hasUnsavedEditsRef.current) return;
        if (freshContent !== contentRef.current) {
          setHighlightedLines(changedLineSet(contentRef.current || '', freshContent));
          setContent(freshContent);
          setSavedContent(freshContent);
          onContentChangeRef.current?.(freshContent);
          clearTimeout(highlightTimerRef.current);
          highlightTimerRef.current = setTimeout(() => setHighlightedLines(new Set()), 5000);
          lastFetchRef.current = Date.now();
        }
      } catch {
        // Silently ignore — file may have been deleted
      } finally {
        inFlight = false;
        if (alive && again) { again = false; timer = setTimeout(read, FILE_CHANGE_COALESCE_MS); }
      }
    };
    const handler = (e: Event) => {
      const changedPath = (e as CustomEvent<{ path: string }>).detail?.path;
      if (!changedPath || detached) return;
      // Match: exact path, or absolute path ends with workspace-relative filePath
      const isMatch = changedPath === filePath
        || changedPath.endsWith(`/${filePath}`);
      if (!isMatch) return;
      // Throttle, not debounce: a later event never pushes the read back, so a file
      // written continuously still refreshes every FILE_CHANGE_COALESCE_MS.
      if (timer === undefined) timer = setTimeout(read, FILE_CHANGE_COALESCE_MS);
    };
    // D1 (run_5d9178bf): subscribe via the single fileChangedBroker.
    const unsub = subscribeFileChanged(handler);
    return () => { alive = false; unsub(); clearTimeout(timer); clearTimeout(highlightTimerRef.current); };
  }, [filePath, detached]);

  // ── Visibility-based refetch: reload when app/tab becomes visible if >3s idle ──
  useEffect(() => {
    const handleVisibility = async () => {
      if (document.hidden || detached) return;
      if (Date.now() - lastFetchRef.current < 3000) return;
      if (hasUnsavedEditsRef.current) return;
      try {
        const resp = await api.get<{ content: string }>('/workspace/file', { params: { path: filePath } });
        const fresh = resp.data.content;
        if (hasUnsavedEditsRef.current) return;   // typed during the read: keep the edits
        if (fresh !== contentRef.current) {
          setHighlightedLines(changedLineSet(contentRef.current || '', fresh));
          setContent(fresh);
          setSavedContent(fresh);
          onContentChangeRef.current?.(fresh);
          clearTimeout(highlightTimerRef.current);
          highlightTimerRef.current = setTimeout(() => setHighlightedLines(new Set()), 5000);
          lastFetchRef.current = Date.now();
        }
      } catch { /* ignore */ }
    };
    // Also on window focus: clicking back from an editor beside SwarmAI never fires
    // visibilitychange (the window was not hidden). Same throttle + unsaved skip.
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleVisibility);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleVisibility);
      clearTimeout(highlightTimerRef.current);
    };
  }, [filePath, detached]);

  // L3: Review mode — inline comments (used for both normal review and diff review)
  // filePath key enables sessionStorage persistence across tab switches (U10).
  const review = useReviewMode(content, filePath);

  // Diff-line comment tracking
  const [activeDiffPopoverIndex, setActiveDiffPopoverIndex] = useState<number | null>(null);
  const [editingDiffComment, setEditingDiffComment] = useState<ReviewComment | null>(null);

  // When a line is clicked in review mode gutter
  const handleReviewLineClick = useCallback(
    (lineNumber: number) => {
      const existing = review.getCommentForLine(lineNumber);
      if (existing) {
        // Edit existing comment
        review.setEditingCommentId(existing.id);
        review.setActivePopoverLine(lineNumber);
      } else {
        // New comment
        review.setEditingCommentId(null);
        review.setActivePopoverLine(lineNumber);
      }
    },
    [review],
  );

  // Compute diff lines — declared early because callbacks and memos below depend on it.
  // Skip on large files: computeLineDiff is an O(n) LCS that blocks the main thread.
  const diffLines = useMemo(() => {
    if (!showDiff) return [];
    if (!shouldProcessSync(content.length) || !shouldProcessSync(originalContent.length)) return [];
    return computeLineDiff(originalContent, content);
  }, [showDiff, originalContent, content]);

  // Derived: maps diff-line index → comment id
  const diffCommentMap = useMemo(() => {
    if (!showDiff || diffLines.length === 0) return new Map<number, string>();
    const map = new Map<number, string>();
    for (const comment of review.comments) {
      if (!comment.diffContext) continue;
      const idx = diffLines.findIndex((dl) =>
        dl.type === comment.diffContext!.type &&
        dl.content === comment.diffContext!.content &&
        dl.oldLineNumber === comment.diffContext!.oldLineNumber &&
        dl.newLineNumber === comment.diffContext!.newLineNumber
      );
      if (idx !== -1) {
        map.set(idx, comment.id);
      }
    }
    return map;
  }, [review.comments, showDiff, diffLines]);

  // When a diff line is clicked
  const handleDiffLineClick = useCallback(
    (diffIndex: number) => {
      const existingCommentId = diffCommentMap.get(diffIndex);
      if (existingCommentId) {
        const comment = review.comments.find((c) => c.id === existingCommentId);
        setEditingDiffComment(comment ?? null);
      } else {
        setEditingDiffComment(null);
      }
      setActiveDiffPopoverIndex(diffIndex);
    },
    [diffCommentMap, review.comments],
  );

  // Get comment for a specific diff-line index
  const getCommentForDiffIndex = useCallback(
    (diffIndex: number): ReviewComment | undefined => {
      const commentId = diffCommentMap.get(diffIndex);
      if (!commentId) return undefined;
      return review.comments.find((c) => c.id === commentId);
    },
    [diffCommentMap, review.comments],
  );

  // Add comment on a diff line — wraps review.addComment with diff context
  const handleDiffAddComment = useCallback(
    (lineStart: number, lineEnd: number, text: string, diffContext?: DiffContext) => {
      review.addComment(lineStart, lineEnd, text, diffContext);
      setActiveDiffPopoverIndex(null);
    },
    [review],
  );

  const handleDiffCancelPopover = useCallback(() => {
    setActiveDiffPopoverIndex(null);
    setEditingDiffComment(null);
  }, []);

  // ── Send review comment with full context (filePath + selectedText + comment) ──
  const handleSendReviewComment = useCallback(
    (comment: string, selectedTextOverride?: string) => {
      const selected = selectedTextOverride ?? selectionText;
      let message: string;
      if (selected) {
        message = `[File Review: \`${filePath}\`]\n\nSelected text:\n\`\`\`\n${selected}\n\`\`\`\n\nInstruction: ${comment}`;
      } else {
        // Fallback: no selection, just file + comment
        message = `[File Review: \`${filePath}\`]\n\nInstruction: ${comment}`;
      }
      dispatchInjectChatInput({ text: message, focus: true, autoSend: true });
      // Clear selection state after send
      setSelectionText('');
      setSelectionPopoverPos(null);
      setShowSelectionComment(false);
    },
    [filePath, selectionText],
  );

  // Legacy single-line send (for gutter click — now sends line content, not line number)
  const handleSendSingleComment = useCallback(
    (text: string, lineNumber: number) => {
      const lineContent = content.split('\n')[lineNumber - 1] || '';
      handleSendReviewComment(text, lineContent);
    },
    [content, handleSendReviewComment],
  );

  // Selection in the markdown preview (round 2, G3): the window selection inside the
  // preview, placed under its rect. A collapsed selection clears the toolbar unless
  // the comment popover is open (same rule as the textarea).
  const handlePreviewSelect = useCallback(() => {
    const sel = window.getSelection();
    const box = previewRef.current;
    const text = sel && !sel.isCollapsed ? sel.toString() : '';
    if (sel && text.trim() && box && sel.anchorNode && box.contains(sel.anchorNode)) {
      const range = sel.getRangeAt(0);
      const r = range.getBoundingClientRect();
      lastSelectionRef.current = { kind: 'preview', range: range.cloneRange() };
      setSelectionText(text);
      setSelectionLines(null);
      setSelectionPopoverPos({ top: r.bottom + 4, left: Math.max(8, Math.min(r.left, window.innerWidth - 240)) });
    } else if (!showSelectionComment) {
      setSelectionText('');
      setSelectionPopoverPos(null);
    }
  }, [showSelectionComment]);

  // Attach the selection to the chat (round 2, G4): a quote appended to what the
  // user typed, focused, not sent.
  const handleAttachSelection = useCallback(() => {
    if (!selectionText.trim()) return;
    const where = selectionLines
      ? (selectionLines[0] === selectionLines[1] ? ` line ${selectionLines[0]}` : ` lines ${selectionLines[0]}-${selectionLines[1]}`)
      : '';
    const quote = selectionText.replace(/\s+$/, '').split('\n').map((l) => `> ${l}`).join('\n');
    dispatchInjectChatInput({ text: `From \`${filePath}\`${where}:\n${quote}\n`, focus: true, append: true });
    setSelectionText('');
    setSelectionPopoverPos(null);
    setSelectionLines(null);
    window.getSelection()?.removeAllRanges();
  }, [selectionText, selectionLines, filePath]);

  // --- Handlers ---

  // The mark lives exactly as long as the selection-comment popover (send, cancel,
  // click-outside and the Redo path all end by closing it).
  useEffect(() => {
    if (!showSelectionComment) setCommentMark(null);
  }, [showSelectionComment]);

  // Preview: paint the frozen range with the CSS Custom Highlight API (no DOM change,
  // so the rendered markdown is untouched). Without the API there is no mark.
  useEffect(() => {
    if (commentMark?.kind !== 'preview') return;
    const hl = highlightRegistry();
    if (!hl) return;
    hl.registry.set(COMMENT_HIGHLIGHT, new hl.Highlight(commentMark.range));
    return () => hl.registry.delete(COMMENT_HIGHLIGHT);
  }, [commentMark]);

  // Source: the mark overlay must start at the textarea's current scroll position.
  useLayoutEffect(() => {
    if (commentMark?.kind === 'source' && commentMarkRef.current && textareaRef.current) {
      commentMarkRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  }, [commentMark]);

  const handleScroll = useCallback(() => {
    if (commentMarkRef.current && textareaRef.current) {
      commentMarkRef.current.scrollTop = textareaRef.current.scrollTop;
    }
    if (textareaRef.current && highlightRef.current) {
      const top = textareaRef.current.scrollTop;
      highlightRef.current.scrollTop = top;
      highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
      setScrollTop(top);
      // Keep the virtualized gutter's window sized to the real viewport.
      setViewportHeight(textareaRef.current.clientHeight);
    }
  }, []);

  const handleCloseAttempt = useCallback(() => {
    if (hasUnsavedEdits) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [hasUnsavedEdits, onClose]);

  // R2 (run_f49d3ff3): a parent-owned unified header (FileViewer, panel variant)
  // triggers this editor's GUARDED close by increasing closeSignal. Reuse the existing
  // guard so the unsaved-changes dialog is preserved (no reimpl, no dialog-lift).
  // Amendment 1: skip the mount/0 baseline so a fresh mount (or a tab-switch remount —
  // FileViewer keys FileEditorCore on filePath) never auto-closes. Amendment 2: read the
  // dirty flag through hasUnsavedEditsRef (kept fresh above) rather than the
  // handleCloseAttempt closure, so a stale closure can't mis-judge dirty at fire time.
  const prevCloseSignalRef = useRef(closeSignal);
  useEffect(() => {
    if (closeSignal === undefined) return;
    if (prevCloseSignalRef.current === undefined) { prevCloseSignalRef.current = closeSignal; return; }
    if (closeSignal > prevCloseSignalRef.current) {
      prevCloseSignalRef.current = closeSignal;
      if (hasUnsavedEditsRef.current) setShowUnsavedWarning(true);
      else onClose();
    }
  }, [closeSignal, onClose]);

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      await onSave(content);
      // Track saved content separately so hasUnsavedEdits resets,
      // but originalContent (committed/HEAD baseline) stays intact for Show Changes.
      setSavedContent(content);

      // L2: Auto-diff feedback — fetch diff after save and notify parent
      if (onSaveWithDiff) {
        try {
          const diffResp = await api.get<{ summary: string; hunks: unknown[] }>(
            '/workspace/file/diff',
            { params: { path: filePath } },
          );
          if (diffResp.data.hunks && diffResp.data.hunks.length > 0) {
            onSaveWithDiff(diffResp.data.summary, fileName);
          }
        } catch {
          // Diff fetch failure is non-critical — save still succeeded
        }
      }

      // In modal mode: close after save (legacy behavior)
      // In panel mode: stay open so user can see Swarm's response
      if (variant === 'modal') {
        onClose();
      }
    } catch (error) {
      const status =
        (error as { statusCode?: number })?.statusCode ??
        (error as { response?: { status?: number } })?.response?.status;
      if (status === 409) {
        setReloadPending(true);
        setShowUnsavedWarning(true);
      }
      console.error('Failed to save file:', error);
    } finally {
      setIsSaving(false);
    }
  }, [content, onSave, onClose, variant, filePath, fileName, onSaveWithDiff]);

  const handleSearchClose = useCallback(() => {
    setShowSearch(false);
    setSearchQuery('');
    setCurrentMatchIndex(0);
    textareaRef.current?.focus();
  }, []);

  // --- Reset state when content changes (e.g. file switch in panel mode) ---
  // Only when the file the state was set up for changes. The state's initial values
  // already equal what this resets, and React runs this effect after the editor is
  // painted, so running it on mount overwrote an edit made in that gap (the tab then
  // never turned dirty). Comparing values also keeps StrictMode's second run a no-op.
  const resetForRef = useRef({ initialContent, filePath, fileName, initialShowDiff });
  useEffect(() => {
    const last = resetForRef.current;
    if (last.initialContent === initialContent && last.filePath === filePath
      && last.fileName === fileName && last.initialShowDiff === initialShowDiff) return;
    resetForRef.current = { initialContent, filePath, fileName, initialShowDiff };
    setContent(initialContent);
    setOriginalContent(committedContent ?? initialContent);
    setSavedContent(null);
    setShowUnsavedWarning(false);
    // Honor initialShowDiff on file-switch too — a bare `false` here would
    // clobber the auto-diff intent the moment the file's content loads
    // (this effect's deps include filePath/committedContent). Switching to a
    // file opened WITHOUT autoDiff still resets to edit view (prop is false).
    setShowDiff(initialShowDiff ?? false);
    // Re-apply the md/svg preview default on file-switch (same formula as the
    // initializers). `!initialShowDiff` keeps preview OFF when the file opened on
    // the diff, so toggling the diff off lands on EDIT, not a sticky preview
    // (Gate-1 Risk 4). A huge .md re-defaults to edit via canPreviewInitial.
    setPreviewOn(canPreviewInitial && !initialShowDiff);
    setShowSearch(false);
    setSearchQuery('');
    setCurrentMatchIndex(0);
    setActiveLineNumber(undefined);
    setAttachFeedback(false);
    // L3: Hard-reset review mode on file switch (no dependency on isReviewMode)
    review.resetReviewMode();
    // Clear diff comment state
    setActiveDiffPopoverIndex(null);
    setEditingDiffComment(null);
    // fileName is a dep because the reset formula reads previewKind (derived from
    // fileName); filePath already changes in lockstep with it, so
    // this adds no extra fires but removes a latent stale-closure (Gate-2 MED).
    // committedContent is NOT a dep: it can land after the content (C5) and must not
    // reset what the user is doing; the effect below takes it on its own.
  }, [initialContent, filePath, fileName, initialShowDiff, review.resetReviewMode]); // eslint-disable-line react-hooks/exhaustive-deps -- review object stable

  // The committed baseline landing after the content: it only changes what Changes
  // compares against, never the text, the view or the unsaved state.
  useEffect(() => {
    if (committedContent !== undefined) setOriginalContent(committedContent);
  }, [committedContent]);

  // Syntax highlighting
  useEffect(() => {
    if (!highlightRef.current || showDiff) return;

    // Large files: do NOT paint content here at all. This <pre> is the visible
    // text layer (the textarea is text-transparent), but a full-content text node
    // is exactly what froze WKWebView on a 17MB file — the old guard skipped hljs
    // *markup* yet still wrote the whole raw string into this node (the real
    // freeze, run_f71e5920). Leave it EMPTY; on large files the textarea below is
    // made visible (see `largeContent` / textarea className) and paints its own
    // text, which the browser virtualizes internally. No debounce needed.
    if (!shouldProcessSync(content.length)) {
      highlightRef.current.textContent = '';
      return;
    }

    // Debounce the expensive hljs pass so rapid typing doesn't re-tokenize the
    // whole file per keystroke. Cancel any pending run first.
    clearTimeout(highlightDebounceRef.current);
    highlightDebounceRef.current = setTimeout(() => {
      if (!highlightRef.current) return;
      const escaped = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      try {
        const highlighted = hljs.highlight(escaped, { language }).value;
        highlightRef.current.innerHTML = highlighted + '\n';
      } catch {
        highlightRef.current.textContent = content + '\n';
      }
    }, HIGHLIGHT_DEBOUNCE_MS);

    return () => clearTimeout(highlightDebounceRef.current);
  }, [content, language, showDiff, previewOn]);

  // Focus-gate helper: in panel mode, only handle keyboard events that
  // originate from within the editor surface. In modal mode, the overlay
  // blocks interaction with the rest of the page so global capture is safe.
  const isFocusWithinEditor = useCallback(
    (e: KeyboardEvent) => {
      if (variant === 'modal') return true;
      return !!rootRef.current?.contains(e.target as Node);
    },
    [variant],
  );

  // Escape — close search first, then the editor (through the unsaved-changes guard).
  // The search box, a comment box and the editor itself take Escape first and mark it
  // handled. In the modal the editor is a layer on the shared stack (hooks/useLayer),
  // so a dialog over it gets Escape before it does; in the panel it is not a layer and
  // answers only an Escape that reached its own root (handleRootKeyDown).
  const handleEscape = useCallback(() => {
    if (showSearch) handleSearchClose();
    else handleCloseAttempt();
  }, [showSearch, handleSearchClose, handleCloseAttempt]);
  useEscape(variant === 'modal', handleEscape, rootRef);
  const handleRootKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (variant === 'modal' || e.key !== 'Escape' || e.defaultPrevented || e.nativeEvent.isComposing) return;
    e.preventDefault();
    handleEscape();
  }, [variant, handleEscape]);
  // The unsaved-changes question is a layer of its own: Escape is "continue editing".
  const unsavedRef = useRef<HTMLDivElement>(null);

  // Ctrl+S / Cmd+S to save
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && !showDiff && !readonly && isFocusWithinEditor(e)) {
        e.preventDefault();
        handleSave();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [showDiff, readonly, handleSave, isFocusWithinEditor]);

  // Cmd+F / Ctrl+F — open search
  // Always intercept when editor is visible: in modal mode the overlay blocks
  // other interactions anyway; in panel mode, the editor is the primary editing
  // surface so CMD+F should target it regardless of current focus.
  // Skipped in diff view and markdown preview (search bar operates on raw content only).
  useEffect(() => {
    if (showDiff || previewOn) return; // no search in diff/preview modes
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        e.stopPropagation();
        setShowSearch(true);
      }
    };
    document.addEventListener('keydown', handleKeyDown, true); // capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [showDiff, previewOn]);

  const handleCancel = useCallback(() => {
    if (hasUnsavedEdits) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [hasUnsavedEdits, onClose]);

  const handleDiscardChanges = useCallback(() => {
    setShowUnsavedWarning(false);
    // Revert to the last-saved baseline (savedContent) if there was an in-session
    // save, else the mount content — matches the hasUnsavedEdits baseline so a
    // prior save isn't visually lost on discard.
    const baseline = savedContent ?? initialContent;
    setContent(baseline);
    onContentChange?.(baseline);
    // CLEAR the parent's dirty flag BEFORE onClose. The parent (FileViewer) tracks
    // per-tab isDirty from onDirtyChange, and useFileViewerTabs.closeTab NO-OPS on a
    // dirty tab ("caller must confirm first"). The confirmed discard IS that
    // confirmation, but the hasUnsavedEdits effect would only report clean after this
    // render — too late for the close below, which would leave the tab open. So report
    // clean synchronously here.
    onDirtyChangeRef.current?.(false);
    onClose();
  }, [savedContent, initialContent, onClose, onContentChange]);

  const handleContinueEditing = useCallback(() => {
    setShowUnsavedWarning(false);
    setReloadPending(false);
  }, []);
  useDialogLayer(showUnsavedWarning, unsavedRef, handleContinueEditing, { first: true });

  /** Refetch the file from disk and apply it (with changed-line highlight).
   *  Shared by the Reload button and its unsaved-edits confirm path. */
  const doReload = useCallback(async () => {
    if (detached) return;
    try {
      const resp = await api.get<{ content: string }>('/workspace/file', { params: { path: filePath } });
      const fresh = resp.data.content;
      if (fresh !== contentRef.current) {
        const oldLines = (contentRef.current || '').split('\n');
        const newLines = fresh.split('\n');
        const changed = new Set<number>();
        for (let i = 0; i < Math.max(oldLines.length, newLines.length); i++) {
          if (oldLines[i] !== newLines[i]) changed.add(i + 1);
        }
        setContent(fresh);
        setSavedContent(fresh);
        onContentChange?.(fresh);
        if (changed.size > 0) {
          setHighlightedLines(changed);
          clearTimeout(highlightTimerRef.current);
          highlightTimerRef.current = setTimeout(() => setHighlightedLines(new Set()), 5000);
        }
      }
    } catch { /* file gone — ignore */ }
  }, [filePath, onContentChange, detached]);

  /** Discard unsaved edits AND reload from disk (Reload button's confirm path).
   *  Distinct from handleDiscardChanges (Close's path), which reverts + closes. */
  const handleDiscardAndReload = useCallback(async () => {
    setShowUnsavedWarning(false);
    setReloadPending(false);
    await doReload();
  }, [doReload]);

  // --- Computed ---

  // Memoized: split of a large string is O(n) and this component re-renders on
  // every scroll/select/keystroke — an un-memoized split re-walked the full 17MB
  // string per frame (run_f71e5920, Gate-1). Recompute only when content changes.
  const lineCount = useMemo(() => content.split('\n').length, [content]);

  // True when the file is too large for synchronous highlight/diff/search — those
  // features degrade silently, so the UI surfaces this flag as an explicit notice
  // (a matching search otherwise shows a misleading "0 of 0").
  const syncDisabled = !shouldProcessSync(content.length);
  // On a large file the highlight <pre> is intentionally left EMPTY (it would
  // otherwise be a freeze-causing full-content node), so the textarea must paint
  // its own text — flip it from text-transparent to visible. Small files keep the
  // transparent textarea + highlight-<pre> overlay (syntax colors preserved).
  const largeContent = syncDisabled;

  const searchMatches = useMemo(() => {
    if (!searchQuery) return [];
    // Skip on large files: findAllMatches is an O(n) per-line scan on every keystroke.
    if (!shouldProcessSync(content.length)) return [];
    return findAllMatches(content, searchQuery);
  }, [content, searchQuery]);

  const handleSelect = useCallback(() => {
    if (!textareaRef.current) return;
    const ta = textareaRef.current;
    const pos = ta.selectionStart;
    const textBefore = content.slice(0, pos);
    const line = textBefore.split('\n').length;
    setActiveLineNumber(line);

    // Track text selection for review comment
    const selected = content.slice(ta.selectionStart, ta.selectionEnd);
    if (selected && selected.length > 0 && ta.selectionStart !== ta.selectionEnd) {
      lastSelectionRef.current = { kind: 'source', start: ta.selectionStart, end: ta.selectionEnd };
      setSelectionText(selected);
      setSelectionLines([
        content.slice(0, ta.selectionStart).split('\n').length,
        content.slice(0, ta.selectionEnd).split('\n').length,
      ]);
      // Position floating button near end of selection using textarea geometry
      const rect = ta.getBoundingClientRect();
      const computedStyle = window.getComputedStyle(ta);
      const lineHeight = parseFloat(computedStyle.lineHeight) || 24;
      const paddingTop = parseFloat(computedStyle.paddingTop) || 16;
      const endPos = ta.selectionEnd;
      const linesBeforeEnd = content.slice(0, endPos).split('\n').length;
      const top = rect.top + paddingTop + (linesBeforeEnd * lineHeight) - ta.scrollTop;
      const left = rect.left + Math.min(rect.width * 0.4, 200); // 40% of width, max 200px
      setSelectionPopoverPos({
        top: Math.max(rect.top + 8, Math.min(top, rect.bottom - 40)),
        left: Math.min(left, rect.right - 100),
      });
    } else if (!showSelectionComment) {
      // Only clear selection state when the comment popover is NOT active.
      // Clicking the Comment button causes WebKit to collapse the textarea selection,
      // which would clear selectionText and prevent the popover from ever mounting.
      setSelectionText('');
      setSelectionPopoverPos(null);
      setShowSelectionComment(false);
    }
  }, [content, showSelectionComment]);

  const handleAttachToChat = useCallback(() => {
    if (!onAttachToChat || isAttached || attachFeedback) return;
    const item: FileTreeItem = {
      id: filePath,
      name: fileName,
      type: 'file',
      path: filePath,
      workspaceId,
      workspaceName: '',
    };
    try {
      onAttachToChat(item);
      setAttachFeedback(true);
      setTimeout(() => setAttachFeedback(false), 2000);
    } catch (err) {
      console.error('Failed to attach file to chat:', err);
    }
  }, [onAttachToChat, isAttached, attachFeedback, filePath, fileName, workspaceId]);

  const handleToggleDiff = useCallback(() => {
    setShowDiff((prev) => !prev);
  }, []);

  // Scroll textarea so the current search match is visible.
  // Does NOT steal focus — the search input keeps focus so user can
  // press Enter repeatedly to navigate matches.
  const scrollToMatch = useCallback((matchIndex: number) => {
    if (!textareaRef.current || searchMatches.length === 0) return;
    const match = searchMatches[matchIndex];
    if (!match) return;
    const ta = textareaRef.current;
    // Scroll to approximately 1/3 from top of viewport
    const lineHeight = 24; // leading-6 = 1.5rem = 24px
    const targetScroll = match.lineIndex * lineHeight - ta.clientHeight / 3;
    ta.scrollTop = Math.max(0, targetScroll);
  }, [searchMatches]);

  // When currentMatchIndex changes, scroll the viewport to that match.
  // Separated from the state setter to avoid side effects inside updaters.
  useEffect(() => {
    if (showSearch && searchMatches.length > 0) {
      scrollToMatch(currentMatchIndex);
    }
  }, [currentMatchIndex, showSearch, searchMatches.length, scrollToMatch]);

  const handleSearchNext = useCallback(() => {
    if (searchMatches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev + 1) % searchMatches.length);
  }, [searchMatches.length]);

  const handleSearchPrevious = useCallback(() => {
    if (searchMatches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev - 1 + searchMatches.length) % searchMatches.length);
  }, [searchMatches.length]);

  // File actions shared by the modal header and the panel's floating bar.
  const reviewBtn = (
    <button
      onClick={() => {
        // The review gutter lives in the editable view (round 2, G2): turning Review
        // on from a preview switches there, so the click visibly does something.
        if (!review.isReviewMode) setPreviewOn(false);
        review.toggleReviewMode();
      }}
      className={clsx(
        toolBtn, 'transition-colors',
        review.isReviewMode
          ? 'bg-amber-500/20 text-amber-600 dark:text-amber-400 font-medium'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
      )}
      data-testid="review-mode-toggle"
      title={review.isReviewMode ? t('ui.FileEditorCore.exitReviewModeTitle') : t('ui.FileEditorCore.reviewModeTitle')}
    >
      <span className="material-symbols-outlined icon-sm">rate_review</span>
      {!iconOnly && (review.isReviewMode ? t('ui.FileEditorCore.exitReview') : t('ui.FileEditorCore.review'))}
      {review.comments.length > 0 && (
        <span className="ml-0.5 px-1 py-px rounded-full bg-amber-500/30 text-[11px] font-bold">
          {review.comments.length}
        </span>
      )}
    </button>
  );
  const openExternalBtn = (() => {
    const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
    const browserRenderable = ['html', 'htm', 'svg'];
    const externalRenderable = ['html', 'htm', 'pdf', 'svg', 'xml'];
    if (!externalRenderable.includes(ext)) return null;
    const useBrowser = browserRenderable.includes(ext);
    return (
      <button
        onClick={async () => {
          const absolutePath = getAbsolutePath();
          try {
            if (useBrowser) {
              await openExternal(`file://${absolutePath}`);
            } else {
              await openInSystemApp(absolutePath);
            }
          } catch {
            // Fallback: copy the path so the user can paste it (toast on failure)
            await copyPathNow(absolutePath);
          }
        }}
        className={clsx(toolBtn, 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors')}
        data-testid="open-in-browser-btn"
        title={useBrowser ? t('ui.FileEditorCore.openInBrowser') : t('ui.FileEditorCore.openWithSystemApp')}
      >
        <span className="material-symbols-outlined icon-sm">open_in_browser</span>
        {!iconOnly && t('ui.FileEditorCore.open')}
      </button>
    );
  })();
  const reloadBtn = detached ? null : (
    <button
      onClick={async () => {
        // Guard unsaved edits, mirroring the SSE + visibility auto-refresh paths:
        // the warning opens in RELOAD mode, so its confirm reloads from disk.
        if (hasUnsavedEditsRef.current) {
          setReloadPending(true);
          setShowUnsavedWarning(true);
          return;
        }
        await doReload();
      }}
      className={clsx(toolBtn, 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors')}
      title={t('ui.FileEditorCore.reloadFromDisk')}
      data-testid="file-editor-reload"
    >
      <span className="material-symbols-outlined icon-sm">refresh</span>
    </button>
  );
  const copyPathBtn = detached ? null : (
    <CopyPathButton
      path={getAbsolutePath()}
      label={iconOnly ? undefined : t('common.copy.path')}
      className={clsx(toolBtn, 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]')}
    />
  );
  const attachBtn = onAttachToChat ? (
    <button
      onClick={handleAttachToChat}
      disabled={isAttached || attachFeedback}
      className={clsx(
        toolBtn, 'transition-colors',
        isAttached || attachFeedback
          ? 'text-[var(--color-success)] cursor-default'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
      )}
      data-testid="attach-to-chat-btn"
      title={isAttached || attachFeedback ? t('ui.FileEditorCore.attachedToChat') : t('ui.FileEditorCore.attachToChat')}
    >
      <span className="material-symbols-outlined icon-sm">
        {isAttached || attachFeedback ? 'check_circle' : 'attach_file'}
      </span>
      {!iconOnly && (isAttached || attachFeedback ? t('ui.FileEditorCore.attached') : t('ui.FileEditorCore.attach'))}
    </button>
  ) : null;

  // ── Panel bar (Super Canvas, docs/design/super-canvas.md commit 3) ──
  // The panel has no header or footer: the controls FLOAT over the bottom of the
  // content — a raised group on the left (one Preview / Source / Changes control,
  // Review, Open, Attach) and Save on the right, shown only while there are unsaved
  // edits. Every scroll layer of the content gets the same `floatPad` bottom padding
  // (textarea, highlight and search overlays, previews, diff), so the last lines
  // scroll clear of the bar and the synced layers keep one scroll height. The
  // gutter needs none: it positions from scrollTop. Reload is in the group (the
  // editor also reloads on an agent write, on window focus and when the window is
  // shown again; an edit made elsewhere can still need it). Copy path is here too
  // (frequent action, canvas-file-types.md §2b). No Close (the list is the selector).
  const floatPad = iconOnly ? { paddingBottom: PANEL_FLOAT_PAD } : undefined;
  const hasPreview = !!previewKind;
  const view: 'preview' | 'source' | 'changes' = showDiff ? 'changes' : previewOn ? 'preview' : 'source';
  // What the user actually sees: a preview too large to draw shows the raw source.
  const shownView: CanvasFileView = showDiff ? 'changes'
    : previewOn && previewKind && canDrawPreview(previewKind, content) ? 'preview' : 'edit';
  const reviewOn = review.isReviewMode;
  useEffect(() => {
    onViewChange?.({ path: filePath, view: shownView, review: reviewOn });
  }, [onViewChange, filePath, shownView, reviewOn]);
  const changesOff = (!isDirty || !!baselinePending) && !showDiff;
  const pickView = (v: 'preview' | 'source' | 'changes') => {
    if (v === 'changes') { if (!changesOff) setShowDiff(true); return; }
    setShowDiff(false);
    if (hasPreview) setPreviewOn(v === 'preview');
  };
  const views = [
    ...(hasPreview ? [['preview', t('ui.FileEditorCore.preview')] as const] : []),
    ['source', t('common.button.edit')] as const,
    ['changes', t('ui.FileEditorCore.changes')] as const,
  ];
  const panelBar = (
    <div
      className="editor-float-ink absolute inset-x-0 bottom-0 z-[3] flex items-end gap-2 px-2.5 pb-2.5 pointer-events-none"
      data-testid="editor-panel-bar"
    >
      <div className="editor-float-group pointer-events-auto" role="toolbar" aria-label={t('ui.FileEditorCore.fileViewAndActions')}>
        <div role="radiogroup" aria-label={t('ui.FileEditorCore.view')} className="flex items-center gap-px">
          {views.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={view === id}
              disabled={id === 'changes' && changesOff}
              onClick={() => pickView(id)}
              data-testid={`editor-view-${id}`}
              title={id === 'changes'
                ? (baselinePending && !showDiff ? t('ui.FileEditorCore.loadingCommitted') : changesOff ? t('ui.FileEditorCore.noChanges') : t('ui.FileEditorCore.showChangesTitle'))
                : undefined}
              className={clsx(
                'h-6 px-2 rounded-md text-[11.5px] transition-colors',
                view === id
                  ? 'editor-view-on'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
                id === 'changes' && changesOff && 'opacity-40 cursor-not-allowed hover:text-[var(--color-text-muted)]',
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="w-px h-4 bg-[var(--color-border)] mx-0.5 shrink-0" aria-hidden="true" />
        {reloadBtn}
        {reviewBtn}
        {openExternalBtn}
        {copyPathBtn}
        {attachBtn}
        {syncDisabled && (
          <span
            className="px-1.5 text-[11px] text-[var(--color-warning,#d97706)] whitespace-nowrap"
          title={t('ui.FileEditorCore.largeFileTitle')}
            data-testid="large-file-notice"
          >
            {t('ui.FileEditorCore.largeFile')}
          </span>
        )}
        {showDiff && review.comments.length === 0 && (
          <span className="px-1.5 text-[11px] text-[var(--color-text-muted)] whitespace-nowrap" data-testid="diff-comment-hint">
            {t('ui.FileEditorCore.clickLineToComment')}
          </span>
        )}
      </div>
      <span className="flex-1" />
      {hasUnsavedEdits && !readonly && (
        <Button
          variant="primary"
          size="sm"
          onClick={handleSave}
          isLoading={isSaving}
          disabled={showDiff}
          title={showDiff ? t('ui.FileEditorCore.backToSave') : t('ui.FileEditorCore.saveShortcut')}
          className="pointer-events-auto shadow-[0_2px_8px_rgba(0,0,0,0.18)]"
          data-testid="file-editor-save"
        >
          {t('common.button.save')}
        </Button>
      )}
    </div>
  );

  return (
    <>
      <div
        ref={rootRef}
        className={iconOnly
          // Panel: the Canvas content card is the frame; no second card inside it.
          ? 'relative bg-[var(--color-card)] flex flex-col h-full w-full overflow-hidden'
          : 'bg-[var(--color-card)] border border-[var(--color-border)] shadow-2xl flex flex-col h-full w-full rounded-xl overflow-hidden'}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={handleRootKeyDown}
        data-testid="file-editor-core"
      >
        {/* Header — modal only. In the panel (Super Canvas) the Canvas list names the
            file and the editor's controls float at the bottom (PanelBar below). */}
        {!iconOnly && (
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {!iconOnly && (
              <>
                <FileTypeIcon name={fileName} />
                {gitStatus && (() => {
                  const badge = gitStatusBadge(gitStatus);
                  if (!badge) return null;
                  return (
                    <span
                      className="text-[11px] font-bold px-1 py-0.5 rounded shrink-0"
                      style={{ color: badge.color, backgroundColor: badge.bg }}
                    >
                      {badge.label}
                    </span>
                  );
                })()}
                <BreadcrumbBar filePath={filePath} />
                {hasUnsavedEdits && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)] bg-opacity-20 text-[var(--color-warning)] shrink-0">
                    {t('ui.FileEditorCore.modified')}
                  </span>
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0 ml-2">
            {/* Preview toggle (modal) — one for every preview kind */}
            {previewKind && (
              <button
                onClick={() => { setPreviewOn((p) => !p); if (showDiff) setShowDiff(false); }}
                title={previewOn ? t('ui.FileEditorCore.editSource') : t('ui.FileEditorCore.preview')}
                className={clsx(
                  toolBtn, 'transition-colors',
                  previewOn
                    ? 'bg-blue-500/20 text-[var(--color-primary)] font-medium'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                )}
                data-testid="preview-toggle"
              >
                <span className="material-symbols-outlined icon-sm">
                  {previewOn ? 'edit' : 'visibility'}
                </span>
                {!iconOnly && (previewOn ? t('common.button.edit') : t('ui.FileEditorCore.preview'))}
              </button>
            )}
            {reloadBtn}
            {reviewBtn}
            {/* Show Changes toggle */}
            <button
              onClick={handleToggleDiff}
              disabled={!isDirty && !showDiff}
              className={clsx(
                toolBtn, 'transition-colors',
                showDiff
                  ? 'bg-blue-500/20 text-[var(--color-primary)] font-medium'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]',
                !isDirty && !showDiff && 'opacity-40 cursor-not-allowed'
              )}
              data-testid="show-changes-toggle"
              title={showDiff ? t('ui.FileEditorCore.backToEditTitle') : t('ui.FileEditorCore.showChangesTitle')}
            >
              <span className="material-symbols-outlined icon-sm">
                {showDiff ? 'edit' : 'difference'}
              </span>
              {!iconOnly && (showDiff ? t('ui.FileEditorCore.backToEdit') : t('ui.FileEditorCore.showChanges'))}
            </button>
            {openExternalBtn}
            {copyPathBtn}
            {attachBtn}
            {/* Mode toggle: panel ↔ modal */}
            {onToggleMode && (
              <button
                onClick={onToggleMode}
                className="icon-btn text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                aria-label={t('ui.FileEditorCore.dockAsPanel')}
                title={t('ui.FileEditorCore.dockAsSidePanel')}
                data-testid="mode-toggle"
              >
                <span className="material-symbols-outlined icon-sm">close_fullscreen</span>
              </button>
            )}
            <button
              onClick={handleCloseAttempt}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              aria-label={t('common.button.close')}
            >
              <span className="material-symbols-outlined icon-md">close</span>
            </button>
          </div>
        </div>
        )}

        {/* Selection toolbar + comment popover (round 2, G3/G4) — one for the source
            view and the markdown preview. Portaled to <body>: the Canvas column has
            `contain: layout`, which makes it the containing block of a fixed
            descendant, so a fixed toolbar rendered in place landed off-card. */}
        {selectionText && selectionPopoverPos && !showSelectionComment && createPortal(
          <div
            className="fixed z-[999] flex items-center gap-1 p-1 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)] shadow-lg"
            style={{ top: selectionPopoverPos.top, left: selectionPopoverPos.left }}
            onMouseDown={(e) => e.preventDefault()}
            data-testid="selection-toolbar"
          >
            <button className="btn-sm btn-primary" onClick={() => { setCommentMark(lastSelectionRef.current); setShowSelectionComment(true); }} data-testid="selection-comment-btn">
              <span className="material-symbols-outlined icon-xs">comment</span>
              {t('ui.FileEditorCore.comment')}
            </button>
            <button className="btn-sm btn-ghost" onClick={handleAttachSelection} data-testid="selection-attach-btn">
              <span className="material-symbols-outlined icon-xs">attach_file</span>
              {t('ui.FileEditorCore.attachToChat')}
            </button>
          </div>,
          document.body,
        )}
        {showSelectionComment && selectionText && selectionPopoverPos && (
          <SelectionCommentPopover
            position={selectionPopoverPos}
            onSubmit={(text) => handleSendReviewComment(text)}
            onCancel={() => { setShowSelectionComment(false); setSelectionPopoverPos(null); }}
          />
        )}

        {/* Readonly banner */}
        {readonly && (
          <div
            className="flex items-center gap-2 px-4 py-2 text-xs border-b border-[var(--color-border)] bg-amber-500/10 text-amber-700 dark:text-amber-300 shrink-0"
            data-testid="readonly-banner"
          >
            <span>{readonlyNote ?? t('ui.FileEditorCore.readonlyBanner')}</span>
          </div>
        )}

        {/* Editor area */}
        <div className="flex-1 relative overflow-hidden flex">
          {showSearch && (
            <SearchBar
              searchQuery={searchQuery}
              onSearchChange={(q) => { setSearchQuery(q); setCurrentMatchIndex(0); }}
              currentMatch={currentMatchIndex}
              totalMatches={searchMatches.length}
              disabled={syncDisabled}
              onNext={handleSearchNext}
              onPrevious={handleSearchPrevious}
              onClose={handleSearchClose}
            />
          )}

          {showDiff ? (
            <div className="flex-1 relative overflow-hidden">
              <DiffView
                lines={diffLines}
                activePopoverIndex={activeDiffPopoverIndex}
                onLineClick={handleDiffLineClick}
                onAddComment={handleDiffAddComment}
                onUpdateComment={review.updateComment}
                onRemoveComment={review.removeComment}
                onCancelPopover={handleDiffCancelPopover}
                getCommentForDiffIndex={getCommentForDiffIndex}
                editingComment={editingDiffComment}
                bottomPad={floatPad?.paddingBottom}
              />
            </div>
          ) : previewOn && previewKind ? (
            canDrawPreview(previewKind, content) ? (
              <div
                ref={previewRef}
                className="flex-1 relative overflow-auto p-6 bg-[var(--color-background)]"
                style={floatPad}
                onMouseUp={handlePreviewSelect}
                onKeyUp={handlePreviewSelect}
                data-testid={`${previewKind}-preview-box`}
              >
                <PreviewBody
                  kind={previewKind}
                  content={previewContent}
                  basePath={filePath.includes('/') ? filePath.replace(/\/[^/]*$/, '') : ''}
                />
              </div>
            ) : (
              // Large-file guard (one for every kind): drawing a huge preview would
              // block the panel, so the raw source shows with a notice (a manual
              // Preview click on a huge file; the default never lands here, it
              // starts in Edit via canPreviewInitial).
              <div
                className="flex-1 relative overflow-auto bg-[var(--color-background)]"
                style={floatPad}
                data-testid="large-preview-fallback"
              >
                <div className="flex items-center gap-2 px-4 py-2 text-xs border-b border-[var(--color-border)] bg-amber-500/10 text-amber-700 dark:text-amber-300 shrink-0">
                  <span className="material-symbols-outlined icon-xs">info</span>
                  <span>{t('ui.FileEditorCore.largePreviewNotice')}</span>
                </div>
                <pre className="m-0 p-4 font-mono text-sm leading-6 whitespace-pre-wrap break-words [word-break:break-all]">
                  {content}
                </pre>
              </div>
            )
          ) : (
            <>
              {review.isReviewMode ? (
                <ReviewModeGutter
                  lineCount={lineCount}
                  scrollTop={scrollTop}
                  viewportHeight={viewportHeight}
                  comments={review.comments}
                  activePopoverLine={review.activePopoverLine}
                  editingCommentId={review.editingCommentId}
                  onLineClick={handleReviewLineClick}
                  onAddComment={review.addComment}
                  onUpdateComment={review.updateComment}
                  onRemoveComment={review.removeComment}
                  onCancelPopover={() => {
                    review.setActivePopoverLine(null);
                    review.setEditingCommentId(null);
                  }}
                  getCommentForLine={review.getCommentForLine}
                  onSendSingle={handleSendSingleComment}
                  isCommentApplied={review.isCommentApplied}
                />
              ) : (
                <LineGutter
                  lineCount={lineCount}
                  scrollTop={scrollTop}
                  activeLineNumber={activeLineNumber}
                  viewportHeight={viewportHeight}
                />
              )}
              <div className="flex-1 relative overflow-hidden">
                <pre
                  ref={highlightRef}
                  data-testid="editor-highlight-layer"
                  className={clsx(
                    'absolute inset-0 m-0 p-4 overflow-y-scroll overflow-x-hidden',
                    'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                    'pointer-events-none',
                    'bg-[var(--color-background)]',
                    '[word-break:break-all]'
                  )}
                  style={{ tabSize: 4, ...floatPad }}
                  aria-hidden="true"
                />
                {/* Diff highlight overlay — shows green border on changed lines after
                    reload. Gated on !syncDisabled: on a large file this maps
                    content.split('\n') into a full-content DOM node (the same freeze
                    class as the highlight <pre>), so it must not build for big files
                    (run_f71e5920). highlightedLines only ever populates post-reload
                    diff, which is itself sync-gated, so this loses nothing on large files. */}
                {!syncDisabled && highlightedLines.size > 0 && (
                  <>
                    <pre
                      className={clsx(
                        'absolute inset-0 m-0 p-4 overflow-hidden',
                        'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                        'pointer-events-none z-[1]',
                        '[word-break:break-all]'
                      )}
                      style={{ tabSize: 4, transform: `translateY(-${scrollTop}px)` }}
                      aria-hidden="true"
                    >
                      {changedLineSegments(content, highlightedLines).map((seg, i) => (
                        seg.changed ? (
                          <div key={i} className="border-l-3 border-green-500 bg-green-500/10 transition-opacity duration-1000" data-testid="changed-line">
                            <span className="invisible">{seg.text || ' '}</span>
                            {'\n'}
                          </div>
                        ) : (
                          // A run of unchanged lines: one text node, laid out (and
                          // wrapped) exactly like the textarea, so the marks below it
                          // land on the right rows without one node per line.
                          <span key={i} className="invisible">{seg.text}{'\n'}</span>
                        )
                      ))}
                    </pre>
                    {/* Confirm/Redo floating bar */}
                    <div className="absolute top-2 right-2 z-[2] flex items-center gap-1 px-2 py-1 rounded-lg bg-green-500/15 border border-green-500/30 backdrop-blur-sm">
                      <span className="text-xs text-green-600 dark:text-green-400 font-medium">
                        {t('ui.FileEditorCore.linesChanged', { count: highlightedLines.size })}
                      </span>
                      <button
                        onClick={() => setHighlightedLines(new Set())}
                        className="btn-sm btn-success dark:text-green-300 transition-colors"
                        title={t('ui.FileEditorCore.dismissHighlights')}
                      >
                        ✓
                      </button>
                      <button
                        onClick={() => {
                          // Redo: pre-select the changed text and open comment
                          const lines = content.split('\n');
                          const changedText = [...highlightedLines]
                            .sort((a, b) => a - b)
                            .map(n => lines[n - 1] ?? '')
                            .join('\n');
                          if (!changedText.trim()) return; // Guard: no empty selection → stuck UI
                          setSelectionText(changedText);
                          setShowSelectionComment(true);
                          setSelectionPopoverPos({
                            top: (rootRef.current?.getBoundingClientRect().top ?? 0) + 50,
                            left: (rootRef.current?.getBoundingClientRect().left ?? 0) + 100,
                          });
                          setHighlightedLines(new Set());
                        }}
                        className="btn-sm btn-warning dark:text-amber-300 transition-colors"
                        title={t('ui.FileEditorCore.requestChanges')}
                      >
                        ↩ {t('ui.FileEditorCore.redo')}
                      </button>
                    </div>
                  </>
                )}
                {/* Search highlight overlay */}
                {showSearch && searchMatches.length > 0 && (
                  <pre
                    className={clsx(
                      'absolute inset-0 m-0 p-4 overflow-y-scroll overflow-x-hidden',
                      'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                      'pointer-events-none z-[1]',
                      '[word-break:break-all]'
                    )}
                    style={{ scrollBehavior: 'auto', tabSize: 4, ...floatPad }}
                    aria-hidden="true"
                  >
                    {content.split('\n').map((line, lineIdx) => {
                      const lineMatches = searchMatches.filter((m: SearchMatch) => m.lineIndex === lineIdx);
                      if (lineMatches.length === 0) return <div key={lineIdx}>{'\n'}</div>;
                      const parts: React.ReactNode[] = [];
                      let cursor = 0;
                      lineMatches.forEach((m: SearchMatch, mi: number) => {
                        if (m.startOffset > cursor) {
                          parts.push(<span key={`t${mi}`} className="invisible">{line.slice(cursor, m.startOffset)}</span>);
                        }
                        const isCurrentMatch = searchMatches.indexOf(m) === currentMatchIndex;
                        parts.push(
                          <mark
                            key={`m${mi}`}
                            className={isCurrentMatch
                              ? 'bg-[var(--color-warning)] text-[var(--color-text)] rounded-sm'
                              : 'bg-[var(--color-warning)]/30 text-transparent rounded-sm'
                            }
                            data-testid={isCurrentMatch ? 'current-search-match' : undefined}
                          >
                            {line.slice(m.startOffset, m.startOffset + m.length)}
                          </mark>
                        );
                        cursor = m.startOffset + m.length;
                      });
                      if (cursor < line.length) {
                        parts.push(<span key="tail" className="invisible">{line.slice(cursor)}</span>);
                      }
                      return <div key={lineIdx}>{parts}{'\n'}</div>;
                    })}
                  </pre>
                )}
                {/* Selection-under-comment overlay: same box and typography as the
                    textarea, text invisible, only the selected span painted. Skipped
                    on large files, like the search overlay (no full-content node). */}
                {commentMark?.kind === 'source' && !largeContent && commentMark.end > commentMark.start && (
                  <pre
                    ref={commentMarkRef}
                    className={clsx(
                      'absolute inset-0 m-0 p-4 overflow-y-scroll overflow-x-hidden',
                      'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                      'pointer-events-none z-[1]',
                      '[word-break:break-all]'
                    )}
                    style={{ scrollBehavior: 'auto', tabSize: 4, ...floatPad }}
                    aria-hidden="true"
                    data-testid="comment-selection-mark"
                  >
                    <span className="invisible">{content.slice(0, commentMark.start)}</span>
                    <mark className="bg-[var(--color-primary)]/25 text-transparent rounded-sm">
                      {content.slice(commentMark.start, commentMark.end)}
                    </mark>
                  </pre>
                )}
                <textarea
                  ref={textareaRef}
                  value={content}
                  onChange={(e) => { if (!readonly && !review.isReviewMode) { setContent(e.target.value); onContentChange?.(e.target.value); } }}
                  onScroll={handleScroll}
                  onSelect={handleSelect}
                  onClick={handleSelect}
                  onKeyUp={handleSelect}
                  readOnly={readonly || review.isReviewMode}
                  className={clsx(
                    'absolute inset-0 m-0 p-4 resize-none appearance-none',
                    'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                    'bg-transparent caret-[var(--color-text)]',
                    // Small files: transparent (the highlight <pre> paints colored
                    // text underneath). Large files: the <pre> is empty (freeze
                    // guard), so the textarea itself must show the text.
                    largeContent ? 'text-[var(--color-text)]' : 'text-transparent',
                    'border-none outline-none',
                    'overflow-y-scroll overflow-x-hidden',
                    '[word-break:break-all]',
                    readonly && 'cursor-default'
                  )}
                  style={{ tabSize: 4, ...floatPad }}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  data-testid="file-editor-textarea"
                />

              </div>
            </>
          )}
        </div>

        {/* Diff review hint — shown when in diff mode with no comments yet */}
        {!iconOnly && showDiff && review.comments.length === 0 && (
          <div className="flex items-center gap-2 px-4 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50 shrink-0">
            <span className="material-symbols-outlined icon-xs text-[var(--color-text-muted)]">touch_app</span>
            <span className="text-xs text-[var(--color-text-muted)]">
              {t('ui.FileEditorCore.clickAnyLineToReview')}
            </span>
          </div>
        )}


        {iconOnly && panelBar}

        {/* Footer — modal only (the panel's Save floats in panelBar). */}
        {!iconOnly && (
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            {language !== 'plaintext' && (
              <span className="px-2 py-1 rounded bg-[var(--color-hover)]">
                {language}
              </span>
            )}
            {syncDisabled && (
              <span
                className="px-2 py-1 rounded bg-[var(--color-hover)] text-[var(--color-warning,#d97706)]"
                title={t('ui.FileEditorCore.largeFileTitle')}
                data-testid="large-file-notice"
              >
                {t('ui.FileEditorCore.largeFileFooter')}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {/* Footer Close/Cancel — suppressed in PANEL variant (run_f49d3ff3 R2):
                the unified FileViewer file-chrome header owns the ONE close there, so a
                footer "Close" would be a SECOND close affordance for editor types (the
                exact non-unification the refactor removes). Save STAYS (editor-specific).
                MODAL keeps "Cancel" (its header close + this are the modal's own chrome). */}
            {!iconOnly && (
              <Button
                variant="secondary"
                size="sm"
                onClick={handleCancel}
                disabled={isSaving}
                data-testid="file-editor-cancel"
              >
                {t('common.button.cancel')}
              </Button>
            )}
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isLoading={isSaving}
              disabled={!hasUnsavedEdits || showDiff || readonly}
              data-testid="file-editor-save"
            >
              {t('common.button.save')}
            </Button>
          </div>
        </div>
        )}
      </div>

      {/* Unsaved Changes Warning */}
      {showUnsavedWarning && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div ref={unsavedRef} role="alertdialog" aria-modal="true" aria-labelledby="unsaved-warning-title" tabIndex={-1}
            className="w-full max-w-md bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl p-6 outline-none">
            <div className="flex items-center gap-3 mb-4">
              <span className="material-symbols-outlined icon-md text-[var(--color-warning)]" aria-hidden="true">
                warning
              </span>
              <h3 id="unsaved-warning-title" className="text-lg font-semibold text-[var(--color-text)]">
                {t('ui.FileEditorCore.unsavedTitle')}
              </h3>
            </div>
            <p className="text-[var(--color-text-muted)] mb-6">
              {t('ui.FileEditorCore.unsavedBefore')}<strong>{fileName}</strong>{t('ui.FileEditorCore.unsavedAfter')}
              {reloadPending
                ? t('ui.FileEditorCore.unsavedReloadQuestion')
                : t('ui.FileEditorCore.unsavedDiscardQuestion')}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleContinueEditing}
                data-testid="unsaved-warning-continue"
              >
                {t('ui.FileEditorCore.continueEditing')}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={reloadPending ? handleDiscardAndReload : handleDiscardChanges}
                data-testid="unsaved-warning-discard"
              >
                {reloadPending ? t('ui.FileEditorCore.discardAndReload') : t('ui.FileEditorCore.discardChanges')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
