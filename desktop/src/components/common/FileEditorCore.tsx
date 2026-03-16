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
import clsx from 'clsx';
import hljs from 'highlight.js';
import Button from './Button';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { fileIcon, fileIconColor, gitStatusBadge } from '../../utils/fileUtils';
import { computeLineDiff } from '../../utils/lineDiff';
import type { DiffLine } from '../../utils/lineDiff';
import api from '../../services/api';
import MarkdownRenderer from './MarkdownRenderer';
import { detectLanguage, isDirtyState, findAllMatches } from './FileEditorModal';
import type { SearchMatch } from './FileEditorModal';
import { useReviewMode } from '../../hooks/useReviewMode';
import ReviewModeGutter from './ReviewModeGutter';
import ReviewFeedbackBar from './ReviewFeedbackBar';

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
/*  DiffView                                                            */
/* ------------------------------------------------------------------ */

function DiffView({ lines }: { lines: DiffLine[] }) {
  const maxOld = lines.reduce((m, l) => Math.max(m, l.oldLineNumber ?? 0), 0);
  const maxNew = lines.reduce((m, l) => Math.max(m, l.newLineNumber ?? 0), 0);
  const gutterW = `${Math.max(3, String(Math.max(maxOld, maxNew)).length)}ch`;

  return (
    <pre
      className="absolute inset-0 m-0 overflow-auto font-mono text-sm leading-6 bg-[var(--color-background)]"
      data-testid="diff-view"
    >
      {lines.map((line, i) => {
        let bgClass = '';
        if (line.type === 'added') bgClass = 'bg-[var(--color-git-added)]/15';
        if (line.type === 'removed') bgClass = 'bg-[var(--color-git-deleted)]/15';
        return (
          <div key={i} className={`flex ${bgClass}`}>
            <span
              className="shrink-0 text-right pr-1 text-[var(--color-text-muted)] select-none border-r border-[var(--color-border)]"
              style={{ width: gutterW }}
            >
              {line.oldLineNumber ?? ''}
            </span>
            <span
              className="shrink-0 text-right pr-1 pl-1 text-[var(--color-text-muted)] select-none border-r border-[var(--color-border)]"
              style={{ width: gutterW }}
            >
              {line.newLineNumber ?? ''}
            </span>
            <span className="shrink-0 w-4 text-center select-none text-[var(--color-text-muted)]">
              {line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
            </span>
            <span className="flex-1 whitespace-pre-wrap break-words px-2">
              {line.content}
            </span>
          </div>
        );
      })}
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
}: {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  currentMatch: number;
  totalMatches: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
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

  const matchDisplay = totalMatches > 0
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
  variant,
  onToggleMode,
  onSaveWithDiff,
}: FileEditorCoreProps) {
  const [content, setContent] = useState(initialContent);
  const [originalContent, setOriginalContent] = useState(committedContent ?? initialContent);
  const [isSaving, setIsSaving] = useState(false);
  const [showUnsavedWarning, setShowUnsavedWarning] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showMarkdownPreview, setShowMarkdownPreview] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [activeLineNumber, setActiveLineNumber] = useState<number | undefined>(undefined);
  const [attachFeedback, setAttachFeedback] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightRef = useRef<HTMLPreElement>(null);

  const isDirty = isDirtyState(content, originalContent);
  const hasUnsavedEdits = isDirtyState(content, initialContent);
  const language = detectLanguage(fileName);
  const isMarkdown = /\.md$/i.test(fileName);

  // L3: Review mode — inline comments
  const review = useReviewMode(content);
  const feedbackText = review.formatFeedback(fileName);

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

  const handleReviewFeedbackSent = useCallback(() => {
    review.clearComments();
    review.toggleReviewMode();
  }, [review]);

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
      setOriginalContent(content);

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
  }, [content, onSave, onClose, variant, filePath, onSaveWithDiff]);

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
    setShowUnsavedWarning(false);
    setShowDiff(false);
    setShowMarkdownPreview(false);
    setShowSearch(false);
    setSearchQuery('');
    setCurrentMatchIndex(0);
    setActiveLineNumber(undefined);
    setAttachFeedback(false);
    // L3: Hard-reset review mode on file switch (no dependency on isReviewMode)
    review.resetReviewMode();
  }, [initialContent, committedContent, filePath, review.resetReviewMode]);

  // Syntax highlighting
  useEffect(() => {
    if (highlightRef.current && !showDiff) {
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
    }
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
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f' && isFocusWithinEditor(e)) {
        e.preventDefault();
        setShowSearch(true);
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isFocusWithinEditor]);

  const handleCancel = useCallback(() => {
    if (hasUnsavedEdits) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [hasUnsavedEdits, onClose]);

  const handleDiscardChanges = useCallback(() => {
    setShowUnsavedWarning(false);
    setContent(initialContent);
    onClose();
  }, [initialContent, onClose]);

  const handleContinueEditing = useCallback(() => {
    setShowUnsavedWarning(false);
  }, []);

  // --- Computed ---

  const lineCount = content.split('\n').length;

  const diffLines = useMemo(() => {
    if (!showDiff) return [];
    return computeLineDiff(originalContent, content);
  }, [showDiff, originalContent, content]);

  const searchMatches = useMemo(() => {
    if (!searchQuery) return [];
    return findAllMatches(content, searchQuery);
  }, [content, searchQuery]);

  const handleSelect = useCallback(() => {
    if (!textareaRef.current) return;
    const pos = textareaRef.current.selectionStart;
    const textBefore = content.slice(0, pos);
    const line = textBefore.split('\n').length;
    setActiveLineNumber(line);
  }, [content]);

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
            {/* Open in Browser — for HTML, PDF, SVG, XML */}
            {(() => {
              const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
              const browserRenderable = ['html', 'htm', 'pdf', 'svg', 'xml'];
              if (!browserRenderable.includes(ext)) return null;
              return (
                <button
                  onClick={async () => {
                    try {
                      const configResp = await api.get<{ file_path?: string; filePath?: string }>('/workspace');
                      const wsRoot = configResp.data.file_path ?? configResp.data.filePath ?? '';
                      const absolutePath = wsRoot ? `${wsRoot}/${filePath}` : filePath;
                      const { openPath } = await import('@tauri-apps/plugin-opener');
                      await openPath(absolutePath);
                    } catch {
                      window.open(filePath, '_blank');
                    }
                  }}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                  data-testid="open-in-browser-btn"
                >
                  <span className="material-symbols-outlined text-sm">open_in_browser</span>
                  Open
                </button>
              );
            })()}
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
              onNext={handleSearchNext}
              onPrevious={handleSearchPrevious}
              onClose={handleSearchClose}
            />
          )}

          {showDiff ? (
            <div className="flex-1 relative overflow-hidden">
              <DiffView lines={diffLines} />
            </div>
          ) : showMarkdownPreview ? (
            <div className="flex-1 relative overflow-auto p-6 bg-[var(--color-background)]">
              <MarkdownRenderer
                content={content}
                className="max-w-4xl mx-auto"
                basePath={filePath.includes('/') ? filePath.replace(/\/[^/]*$/, '') : ''}
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
                {/* Search highlight overlay */}
                {showSearch && searchMatches.length > 0 && (
                  <pre
                    className={clsx(
                      'absolute inset-0 m-0 p-4 overflow-y-scroll overflow-x-hidden',
                      'font-mono text-sm leading-6 whitespace-pre-wrap',
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
                  onChange={(e) => { if (!readonly && !review.isReviewMode) setContent(e.target.value); }}
                  onScroll={handleScroll}
                  onSelect={handleSelect}
                  onClick={handleSelect}
                  onKeyUp={handleSelect}
                  readOnly={readonly || review.isReviewMode}
                  className={clsx(
                    'absolute inset-0 w-full h-full m-0 p-4 resize-none appearance-none',
                    'font-mono text-sm leading-6 whitespace-pre-wrap',
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
              </div>
            </>
          )}
        </div>

        {/* Review Feedback Bar (L3) — shown above footer when review mode active */}
        {review.isReviewMode && (
          <ReviewFeedbackBar
            commentCount={review.comments.length}
            feedbackText={feedbackText}
            onFeedbackSent={handleReviewFeedbackSent}
            onClearComments={review.clearComments}
          />
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            {language !== 'plaintext' && (
              <span className="px-2 py-1 rounded bg-[var(--color-hover)]">
                {language}
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
              You have unsaved changes in <strong>{fileName}</strong>. Do you want to discard them?
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
                onClick={handleDiscardChanges}
                data-testid="unsaved-warning-discard"
              >
                Discard Changes
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
