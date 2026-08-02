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

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import clsx from 'clsx';
import hljs from 'highlight.js';
import Button from './Button';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { fileIcon, fileIconColor, gitStatusBadge } from '../../utils/fileUtils';
import { computeLineDiff } from '../../utils/lineDiff';
import type { DiffLine } from '../../utils/lineDiff';
import api from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';
import { openExternal, openInSystemApp } from '../../utils/openExternal';
import MarkdownRenderer from './MarkdownRenderer';
import { detectLanguage, isDirtyState, findAllMatches } from './FileEditorModal';
import type { SearchMatch } from './FileEditorModal';
import { useReviewMode } from '../../hooks/useReviewMode';
import type { DiffContext, ReviewComment } from '../../hooks/useReviewMode';
import ReviewModeGutter from './ReviewModeGutter';
import CommentPopover from './CommentPopover';

/**
 * Character-count ceiling above which O(n) main-thread text work is skipped to
 * avoid freezing the UI. `hljs.highlight`, `computeLineDiff`, and
 * `findAllMatches` all run synchronously over the full content on the render
 * path; on a large file (e.g. a 379K-char doc) each blocks the main thread for
 * hundreds of ms — on every keystroke while editing. Above this threshold the
 * editor degrades gracefully (plaintext, diff/search disabled) rather than hang.
 *
 * 100K chars: a typical source file is well under (fast to highlight); the files
 * that froze the UII in practice were far above. `content.length` (not line
 * count) is the right metric — hljs/diff/search cost scales with total
 * characters, and a single-line minified/base64 blob freezes just as hard as a
 * many-line file of the same size.
 */
export const HIGHLIGHT_MAX_CHARS = 100_000;

/**
 * Debounce (ms) for the syntax-highlight overlay. hljs.highlight runs over the
 * full file synchronously; without a debounce it re-ran on every keystroke,
 * causing typing lag on medium files (40-99K chars, under the freeze ceiling).
 * 150ms is below the "done typing" perception threshold — the colored overlay
 * settles a beat after you pause; the textarea text/caret is never delayed.
 */
export const HIGHLIGHT_DEBOUNCE_MS = 150;

/** Whether synchronous main-thread text processing (highlight/diff/search) is
 *  safe for content of this length. Pure — exported for testing. */
export function shouldProcessSync(contentLength: number): boolean {
  return contentLength <= HIGHLIGHT_MAX_CHARS;
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

function LineGutter({ lineCount, scrollTop, activeLineNumber }: {
  lineCount: number;
  scrollTop: number;
  activeLineNumber?: number;
}) {
  const gutterWidth = `${Math.max(3, String(lineCount).length) + 1}ch`;
  return (
    <div
      className="shrink-0 select-none border-r border-[var(--color-border)] bg-[var(--color-background)] overflow-hidden"
      style={{ width: gutterWidth }}
    >
      <div
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
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

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
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
      }
    },
    [text, onSubmit, onCancel],
  );

  return createPortal(
    <div
      ref={popoverRef}
      className="fixed z-[1000] w-72 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl"
      style={{ top: position.top + 28, left: position.left }}
      data-testid="selection-comment-popover"
    >
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-hover)] rounded-t-lg">
        <span className="text-xs text-[var(--color-text-muted)] font-medium">
          Comment on selection
        </span>
        <button
          onClick={onCancel}
          className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
        >
          <span className="material-symbols-outlined text-sm">close</span>
        </button>
      </div>
      <div className="p-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your instruction..."
          rows={2}
          className="w-full px-2 py-1.5 text-xs rounded bg-[var(--color-background)] text-[var(--color-text)] border border-[var(--color-border)] outline-none focus:border-[var(--color-primary)] resize-none"
          data-testid="selection-comment-input"
        />
      </div>
      <div className="flex items-center justify-end gap-1 px-2 pb-2">
        <button
          onClick={onCancel}
          className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={() => { const trimmed = text.trim(); if (trimmed) onSubmit(trimmed); }}
          disabled={!text.trim()}
          className="px-3 py-1 text-xs rounded bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-hover)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-1"
          data-testid="selection-comment-send"
        >
          <span className="material-symbols-outlined text-xs">send</span>
          Send
        </button>
      </div>
      <div className="px-3 pb-1.5 text-[10px] text-[var(--color-text-dim)]">
        ⌘+Enter to send · Esc to cancel
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
}: DiffViewProps) {
  const maxOld = lines.reduce((m, l) => Math.max(m, l.oldLineNumber ?? 0), 0);
  const maxNew = lines.reduce((m, l) => Math.max(m, l.newLineNumber ?? 0), 0);
  const gutterW = `${Math.max(3, String(Math.max(maxOld, maxNew)).length)}ch`;
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
            aria-label={isInteractive ? `Comment on diff line ${i + 1}` : undefined}
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
              title="line before"
            >
              {line.oldLineNumber ?? ''}
            </span>
            <span
              className={clsx(
                'shrink-0 text-right pr-1 pl-1 select-none border-r-2 border-[var(--color-border-strong,var(--color-border))] text-[var(--color-text-muted)]',
                line.type === 'added' && 'bg-[var(--color-git-added)]/[0.08]',
              )}
              style={{ width: gutterW }}
              title="line after"
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
              <span className="shrink-0 pr-2 text-[10px] self-center" title="Has comment">💬</span>
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
    ? 'search disabled (large file)'
    : totalMatches > 0
      ? `${currentMatch + 1} of ${totalMatches}`
      : '0 of 0';

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
        placeholder="Find..."
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
        aria-label="Previous match"
      >
        <span className="material-symbols-outlined text-base">keyboard_arrow_up</span>
      </button>
      <button
        onClick={onNext}
        disabled={totalMatches === 0}
        className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] disabled:opacity-40"
        aria-label="Next match"
      >
        <span className="material-symbols-outlined text-base">keyboard_arrow_down</span>
      </button>
      <button
        onClick={onClose}
        className="p-0.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]"
        aria-label="Close search"
      >
        <span className="material-symbols-outlined text-base">close</span>
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
  initialShowDiff,
  variant,
  onToggleMode,
  onSaveWithDiff,
  onContentChange,
}: FileEditorCoreProps) {
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
  const [showMarkdownPreview, setShowMarkdownPreview] = useState(false);
  const [showSvgPreview, setShowSvgPreview] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [activeLineNumber, setActiveLineNumber] = useState<number | undefined>(undefined);
  const [selectionText, setSelectionText] = useState('');
  const [selectionPopoverPos, setSelectionPopoverPos] = useState<{ top: number; left: number } | null>(null);
  const [showSelectionComment, setShowSelectionComment] = useState(false);
  const [attachFeedback, setAttachFeedback] = useState(false);
  const [copyPathFeedback, setCopyPathFeedback] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
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

  const isDirty = isDirtyState(content, originalContent);
  const hasUnsavedEdits = isDirtyState(content, savedContent ?? initialContent);
  const language = detectLanguage(fileName);
  const isMarkdown = /\.md$/i.test(fileName);
  const isSvg = /\.svg$/i.test(fileName);

  // ── AC2: Auto-refresh — poll file content every 3s when editor is open ──
  // Uses refs to avoid stale closures without re-creating the interval on every render.
  const contentRef = useRef(content);
  contentRef.current = content;
  const hasUnsavedEditsRef = useRef(hasUnsavedEdits);
  hasUnsavedEditsRef.current = hasUnsavedEdits;
  const onContentChangeRef = useRef(onContentChange);
  onContentChangeRef.current = onContentChange;

  // ── Auto-refresh via SSE file_changed event + diff highlight ──
  useEffect(() => {
    const handler = async (e: Event) => {
      const changedPath = (e as CustomEvent<{ path: string }>).detail?.path;
      if (!changedPath) return;
      // Match: exact path, or absolute path ends with workspace-relative filePath
      const isMatch = changedPath === filePath
        || changedPath.endsWith(`/${filePath}`);
      if (!isMatch) return;
      if (hasUnsavedEditsRef.current) return; // Don't overwrite user's unsaved edits
      try {
        const resp = await api.get<{ content: string }>('/workspace/file', {
          params: { path: filePath },
        });
        const freshContent = resp.data.content;
        if (freshContent !== contentRef.current) {
          // Compute changed lines for highlight
          const oldLines = (contentRef.current || '').split('\n');
          const newLines = freshContent.split('\n');
          const changed = new Set<number>();
          const maxLen = Math.max(oldLines.length, newLines.length);
          for (let i = 0; i < maxLen; i++) {
            if (oldLines[i] !== newLines[i]) changed.add(i + 1); // 1-based
          }
          setContent(freshContent);
          setSavedContent(freshContent);
          onContentChangeRef.current?.(freshContent);
          // Highlight changed lines for 5s
          if (changed.size > 0) {
            setHighlightedLines(changed);
            clearTimeout(highlightTimerRef.current); highlightTimerRef.current = setTimeout(() => setHighlightedLines(new Set()), 5000);
          }
          lastFetchRef.current = Date.now();
        }
      } catch {
        // Silently ignore — file may have been deleted
      }
    };
    window.addEventListener('swarm:file-changed', handler);
    return () => { window.removeEventListener('swarm:file-changed', handler); clearTimeout(highlightTimerRef.current); };
  }, [filePath]);

  // ── Visibility-based refetch: reload when app/tab becomes visible if >3s idle ──
  useEffect(() => {
    const handleVisibility = async () => {
      if (document.hidden) return;
      if (Date.now() - lastFetchRef.current < 3000) return;
      if (hasUnsavedEditsRef.current) return;
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
          onContentChangeRef.current?.(fresh);
          if (changed.size > 0) {
            setHighlightedLines(changed);
            clearTimeout(highlightTimerRef.current); highlightTimerRef.current = setTimeout(() => setHighlightedLines(new Set()), 5000);
          }
          lastFetchRef.current = Date.now();
        }
      } catch { /* ignore */ }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => { document.removeEventListener('visibilitychange', handleVisibility); clearTimeout(highlightTimerRef.current); };
  }, [filePath]);

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
      window.dispatchEvent(
        new CustomEvent('swarm:inject-chat-input', {
          detail: { text: message, focus: true, autoSend: true },
        }),
      );
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

  // --- Handlers ---

  const handleScroll = useCallback(() => {
    if (textareaRef.current && highlightRef.current) {
      const top = textareaRef.current.scrollTop;
      highlightRef.current.scrollTop = top;
      highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
      setScrollTop(top);
    }
  }, []);

  const handleCloseAttempt = useCallback(() => {
    if (hasUnsavedEdits) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [hasUnsavedEdits, onClose]);

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
  useEffect(() => {
    setContent(initialContent);
    setOriginalContent(committedContent ?? initialContent);
    setSavedContent(null);
    setShowUnsavedWarning(false);
    // Honor initialShowDiff on file-switch too — a bare `false` here would
    // clobber the auto-diff intent the moment the file's content loads
    // (this effect's deps include filePath/committedContent). Switching to a
    // file opened WITHOUT autoDiff still resets to edit view (prop is false).
    setShowDiff(initialShowDiff ?? false);
    setShowMarkdownPreview(false);
    setShowSvgPreview(false);
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
  }, [initialContent, committedContent, filePath, initialShowDiff, review.resetReviewMode]); // eslint-disable-line react-hooks/exhaustive-deps -- review object stable

  // Syntax highlighting
  useEffect(() => {
    if (!highlightRef.current || showDiff) return;

    // Large files: skip synchronous hljs.highlight (O(n), blocks the main
    // thread on every content change) and render plaintext instead — immediate,
    // no debounce needed (it's cheap).
    if (!shouldProcessSync(content.length)) {
      highlightRef.current.textContent = content + '\n';
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
  }, [content, language, showDiff, showMarkdownPreview]);

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

  // Escape key — close search first, then editor
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFocusWithinEditor(e)) {
        if (showSearch) {
          handleSearchClose();
          return;
        }
        handleCloseAttempt();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [showSearch, handleCloseAttempt, handleSearchClose, isFocusWithinEditor]);

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
    if (showDiff || showMarkdownPreview || showSvgPreview) return; // no search in diff/preview modes
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        e.stopPropagation();
        setShowSearch(true);
      }
    };
    document.addEventListener('keydown', handleKeyDown, true); // capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [showDiff, showMarkdownPreview, showSvgPreview]);

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
    setContent(savedContent ?? initialContent);
    onClose();
  }, [savedContent, initialContent, onClose]);

  const handleContinueEditing = useCallback(() => {
    setShowUnsavedWarning(false);
    setReloadPending(false);
  }, []);

  /** Refetch the file from disk and apply it (with changed-line highlight).
   *  Shared by the Reload button and its unsaved-edits confirm path. */
  const doReload = useCallback(async () => {
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
  }, [filePath, onContentChange]);

  /** Discard unsaved edits AND reload from disk (Reload button's confirm path).
   *  Distinct from handleDiscardChanges (Close's path), which reverts + closes. */
  const handleDiscardAndReload = useCallback(async () => {
    setShowUnsavedWarning(false);
    setReloadPending(false);
    await doReload();
  }, [doReload]);

  // --- Computed ---

  const lineCount = content.split('\n').length;

  // True when the file is too large for synchronous highlight/diff/search — those
  // features degrade silently, so the UI surfaces this flag as an explicit notice
  // (a matching search otherwise shows a misleading "0 of 0").
  const syncDisabled = !shouldProcessSync(content.length);

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
      setSelectionText(selected);
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

  return (
    <>
      <div
        ref={rootRef}
        className="bg-[var(--color-card)] border border-[var(--color-border)] shadow-2xl flex flex-col h-full w-full rounded-xl overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
        data-testid="file-editor-core"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <span
              className="material-symbols-outlined text-lg shrink-0"
              style={{ color: fileIconColor(fileName) }}
            >
              {fileIcon(fileName)}
            </span>
            {gitStatus && (() => {
              const badge = gitStatusBadge(gitStatus);
              if (!badge) return null;
              return (
                <span
                  className="text-[10px] font-bold px-1 py-0.5 rounded shrink-0"
                  style={{ color: badge.color, backgroundColor: badge.bg }}
                >
                  {badge.label}
                </span>
              );
            })()}
            <BreadcrumbBar filePath={filePath} />
            {hasUnsavedEdits && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)] bg-opacity-20 text-[var(--color-warning)] shrink-0">
                Modified
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0 ml-2">
            {/* Markdown Preview toggle */}
            {isMarkdown && (
              <button
                onClick={() => { setShowMarkdownPreview((p) => !p); if (showDiff) setShowDiff(false); }}
                className={clsx(
                  'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                  showMarkdownPreview
                    ? 'bg-blue-500/20 text-[var(--color-primary)] font-medium'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                )}
                data-testid="markdown-preview-toggle"
              >
                <span className="material-symbols-outlined text-sm">
                  {showMarkdownPreview ? 'edit' : 'visibility'}
                </span>
                {showMarkdownPreview ? 'Edit' : 'Preview'}
              </button>
            )}
            {/* SVG Preview toggle */}
            {isSvg && (
              <button
                onClick={() => { setShowSvgPreview((p) => !p); if (showDiff) setShowDiff(false); }}
                className={clsx(
                  'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                  showSvgPreview
                    ? 'bg-blue-500/20 text-[var(--color-primary)] font-medium'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                )}
                data-testid="svg-preview-toggle"
              >
                <span className="material-symbols-outlined text-sm">
                  {showSvgPreview ? 'edit' : 'image'}
                </span>
                {showSvgPreview ? 'Edit' : 'Preview'}
              </button>
            )}
            {/* Reload button — refetch file from disk */}
            <button
              onClick={async () => {
                // Guard unsaved edits, mirroring the SSE + visibility auto-refresh
                // paths. Without this, an explicit Reload silently overwrites the
                // user's pending edits with disk content. Open the warning in
                // RELOAD mode so its confirm action reloads-from-disk (not close).
                if (hasUnsavedEditsRef.current) {
                  setReloadPending(true);
                  setShowUnsavedWarning(true);
                  return;
                }
                await doReload();
              }}
              className="flex items-center px-2 py-1 rounded-lg text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              title="Reload file from disk"
              data-testid="file-editor-reload"
            >
              <span className="material-symbols-outlined text-sm">refresh</span>
            </button>
            {/* Review Mode toggle (L3) */}
            <button
              onClick={review.toggleReviewMode}
              className={clsx(
                'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                review.isReviewMode
                  ? 'bg-amber-500/20 text-amber-600 dark:text-amber-400 font-medium'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
              )}
              data-testid="review-mode-toggle"
              title="Toggle review mode — click line numbers to add comments"
            >
              <span className="material-symbols-outlined text-sm">rate_review</span>
              {review.isReviewMode ? 'Exit Review' : 'Review'}
              {review.comments.length > 0 && (
                <span className="ml-0.5 px-1 py-px rounded-full bg-amber-500/30 text-[10px] font-bold">
                  {review.comments.length}
                </span>
              )}
            </button>
            {/* Show Changes toggle */}
            <button
              onClick={handleToggleDiff}
              disabled={!isDirty && !showDiff}
              className={clsx(
                'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                showDiff
                  ? 'bg-blue-500/20 text-[var(--color-primary)] font-medium'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]',
                !isDirty && !showDiff && 'opacity-40 cursor-not-allowed'
              )}
              data-testid="show-changes-toggle"
            >
              <span className="material-symbols-outlined text-sm">
                {showDiff ? 'edit' : 'difference'}
              </span>
              {showDiff ? 'Back to Edit' : 'Show Changes'}
            </button>
            {/* Open externally — browser for HTML/SVG, system app for PDF/XML */}
            {(() => {
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
                      // Fallback: copy path to clipboard so user can paste in browser
                      await copyToClipboard(absolutePath);
                    }
                  }}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                  data-testid="open-in-browser-btn"
                  title={useBrowser ? 'Open in browser' : 'Open with system app'}
                >
                  <span className="material-symbols-outlined text-sm">open_in_browser</span>
                  Open
                </button>
              );
            })()}
            {/* Copy absolute file path */}
            <button
              onClick={async () => {
                if (copyPathFeedback) return;
                const absolutePath = getAbsolutePath();
                const ok = await copyToClipboard(absolutePath);
                if (ok) {
                  setCopyPathFeedback(true);
                  setTimeout(() => setCopyPathFeedback(false), 2000);
                } else {
                  console.warn('[FileEditor] copyToClipboard failed for:', absolutePath);
                }
              }}
              className={clsx(
                'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                copyPathFeedback
                  ? 'text-[var(--color-success)] cursor-default'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
              )}
              title="Copy absolute file path"
              data-testid="copy-path-btn"
            >
              <span className="material-symbols-outlined text-sm">
                {copyPathFeedback ? 'check' : 'content_copy'}
              </span>
              {copyPathFeedback ? 'Copied' : 'Copy Path'}
            </button>
            {/* Attach to Chat */}
            {onAttachToChat && (
              <button
                onClick={handleAttachToChat}
                disabled={isAttached || attachFeedback}
                className={clsx(
                  'flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors',
                  isAttached || attachFeedback
                    ? 'text-[var(--color-success)] cursor-default'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                )}
                data-testid="attach-to-chat-btn"
              >
                <span className="material-symbols-outlined text-sm">
                  {isAttached || attachFeedback ? 'check_circle' : 'attach_file'}
                </span>
                {isAttached || attachFeedback ? 'Attached' : 'Attach'}
              </button>
            )}
            {/* Mode toggle: panel ↔ modal */}
            {onToggleMode && (
              <button
                onClick={onToggleMode}
                className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                aria-label={variant === 'panel' ? 'Pop out to modal' : 'Dock as panel'}
                title={variant === 'panel' ? 'Pop out to fullscreen' : 'Dock as side panel'}
                data-testid="mode-toggle"
              >
                <span className="material-symbols-outlined text-lg">
                  {variant === 'panel' ? 'open_in_full' : 'close_fullscreen'}
                </span>
              </button>
            )}
            {/* Close */}
            <button
              onClick={handleCloseAttempt}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              aria-label="Close"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>

        {/* Readonly banner */}
        {readonly && (
          <div
            className="flex items-center gap-2 px-4 py-2 text-xs border-b border-[var(--color-border)] bg-amber-500/10 text-amber-700 dark:text-amber-300 shrink-0"
            data-testid="readonly-banner"
          >
            <span>System Default — This file is managed by SwarmAI and refreshed on every startup.</span>
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
              />
            </div>
          ) : showMarkdownPreview ? (
            <div className="flex-1 relative overflow-auto p-6 bg-[var(--color-background)]">
              <MarkdownRenderer
                content={content}
                className="max-w-4xl mx-auto"
                basePath={filePath.includes('/') ? filePath.replace(/\/[^/]*$/, '') : ''}
              />
            </div>
          ) : showSvgPreview ? (
            <div className="flex-1 relative overflow-auto p-6 bg-[var(--color-background)] flex items-center justify-center">
              <img
                className="max-w-full max-h-full w-auto h-auto"
                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`}
                alt="SVG preview"
                data-testid="svg-preview"
              />
            </div>
          ) : (
            <>
              {review.isReviewMode ? (
                <ReviewModeGutter
                  lineCount={lineCount}
                  scrollTop={scrollTop}
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
                />
              )}
              <div className="flex-1 relative overflow-hidden">
                <pre
                  ref={highlightRef}
                  className={clsx(
                    'absolute inset-0 m-0 p-4 overflow-y-scroll overflow-x-hidden',
                    'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                    'pointer-events-none',
                    'bg-[var(--color-background)]',
                    '[word-break:break-all]'
                  )}
                  style={{ tabSize: 4 }}
                  aria-hidden="true"
                />
                {/* Diff highlight overlay — shows green border on changed lines after reload */}
                {highlightedLines.size > 0 && (
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
                      {content.split('\n').map((line, lineIdx) => {
                        const lineNum = lineIdx + 1;
                        const isHighlighted = highlightedLines.has(lineNum);
                        return (
                          <div
                            key={lineIdx}
                            className={isHighlighted
                              ? 'border-l-3 border-green-500 bg-green-500/10 transition-opacity duration-1000'
                              : ''
                            }
                          >
                            <span className="invisible">{line || ' '}</span>
                            {'\n'}
                          </div>
                        );
                      })}
                    </pre>
                    {/* Confirm/Redo floating bar */}
                    <div className="absolute top-2 right-2 z-[2] flex items-center gap-1 px-2 py-1 rounded-lg bg-green-500/15 border border-green-500/30 backdrop-blur-sm">
                      <span className="text-xs text-green-600 dark:text-green-400 font-medium">
                        {highlightedLines.size} line{highlightedLines.size > 1 ? 's' : ''} changed
                      </span>
                      <button
                        onClick={() => setHighlightedLines(new Set())}
                        className="px-1.5 py-0.5 text-xs rounded bg-green-500/20 text-green-700 dark:text-green-300 hover:bg-green-500/30 transition-colors"
                        title="Dismiss highlights"
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
                        className="px-1.5 py-0.5 text-xs rounded bg-amber-500/20 text-amber-700 dark:text-amber-300 hover:bg-amber-500/30 transition-colors"
                        title="Request changes to these lines"
                      >
                        ↩ Redo
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
                    style={{ scrollBehavior: 'auto', tabSize: 4 }}
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
                    'bg-transparent text-transparent caret-[var(--color-text)]',
                    'border-none outline-none',
                    'overflow-y-scroll overflow-x-hidden',
                    '[word-break:break-all]',
                    readonly && 'cursor-default'
                  )}
                  style={{ tabSize: 4 }}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  data-testid="file-editor-textarea"
                />

                {/* Floating "Comment" button — appears on text selection */}
                {selectionText && selectionPopoverPos && !showSelectionComment && (
                  <button
                    className="fixed z-[999] px-2 py-1 text-xs font-medium rounded-lg bg-[var(--color-primary)] text-white shadow-lg hover:bg-[var(--color-primary-hover)] transition-colors flex items-center gap-1"
                    style={{ top: selectionPopoverPos.top, left: selectionPopoverPos.left }}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => setShowSelectionComment(true)}
                    data-testid="selection-comment-btn"
                  >
                    <span className="material-symbols-outlined text-sm">comment</span>
                    Comment
                  </button>
                )}

                {/* Selection comment popover — positioned directly at selection coords */}
                {showSelectionComment && selectionText && selectionPopoverPos && (
                  <SelectionCommentPopover
                    position={selectionPopoverPos}
                    onSubmit={(text) => handleSendReviewComment(text)}
                    onCancel={() => { setShowSelectionComment(false); setSelectionPopoverPos(null); }}
                  />
                )}
              </div>
            </>
          )}
        </div>

        {/* Diff review hint — shown when in diff mode with no comments yet */}
        {showDiff && review.comments.length === 0 && (
          <div className="flex items-center gap-2 px-4 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50 shrink-0">
            <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">touch_app</span>
            <span className="text-xs text-[var(--color-text-muted)]">
              Click any line to add review comments
            </span>
          </div>
        )}


        {/* Footer */}
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
                title="Syntax highlighting, search, and diff are disabled on large files to keep the editor responsive."
                data-testid="large-file-notice"
              >
                Large file — highlighting & search disabled
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleCancel}
              disabled={isSaving}
              data-testid="file-editor-cancel"
            >
              {variant === 'panel' ? 'Close' : 'Cancel'}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isLoading={isSaving}
              disabled={!hasUnsavedEdits || showDiff || readonly}
              data-testid="file-editor-save"
            >
              Save
            </Button>
          </div>
        </div>
      </div>

      {/* Unsaved Changes Warning */}
      {showUnsavedWarning && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/50"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="w-full max-w-md bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl p-6">
            <div className="flex items-center gap-3 mb-4">
              <span className="material-symbols-outlined text-2xl text-[var(--color-warning)]">
                warning
              </span>
              <h3 className="text-lg font-semibold text-[var(--color-text)]">
                Unsaved Changes
              </h3>
            </div>
            <p className="text-[var(--color-text-muted)] mb-6">
              You have unsaved changes in <strong>{fileName}</strong>.{' '}
              {reloadPending
                ? 'Discard them and reload the file from disk?'
                : 'Do you want to discard them?'}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleContinueEditing}
                data-testid="unsaved-warning-continue"
              >
                Continue Editing
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={reloadPending ? handleDiscardAndReload : handleDiscardChanges}
                data-testid="unsaved-warning-discard"
              >
                {reloadPending ? 'Discard & Reload' : 'Discard Changes'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
