/**
 * FileEditorModal — Modal overlay for editing files with syntax highlighting.
 *
 * This component provides the primary file-editing surface in SwarmAI's desktop
 * app. It is rendered as a modal overlay from ThreeColumnLayout and supports:
 *
 * - `FileEditorModal`    — Main modal component (default export)
 * - `FileEditorModalProps` / `FileEditorState` — Public interfaces
 * - `detectLanguage()`   — Maps file extensions to highlight.js language names
 * - `isDirtyState()`     — Checks whether content differs from original
 * - `findAllMatches()`   — Case-insensitive search match computation
 * - `SearchMatch`        — Interface for a single search hit
 *
 * Sub-components (module-private):
 * - `BreadcrumbBar`  — Breadcrumb path display in the header
 * - `LineGutter`     — Synchronized line-number gutter
 * - `DiffView`       — Inline diff renderer (read-only)
 * - `SearchBar`      — Floating Cmd+F search bar with match navigation
 *
 * All styling uses CSS variables for light/dark theme compatibility.
 * No Monaco or heavy editor dependencies — uses highlight.js + textarea overlay.
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

export interface FileEditorModalProps {
  isOpen: boolean;
  filePath: string;
  fileName: string;
  workspaceId: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
  /** Git status of the file, used to render a status badge in the header. */
  gitStatus?: GitStatus;
  /** Callback to attach the current file to the chat context. */
  onAttachToChat?: (item: FileTreeItem) => void;
  /** Whether the file is already attached to the chat context. */
  isAttached?: boolean;
}

export interface FileEditorState {
  isOpen: boolean;
  filePath: string | null;
  fileName: string | null;
  workspaceId: string | null;
  content: string;
  originalContent: string;
  isDirty: boolean;
  language: string;
  /** Git status of the file, captured at open time from the TreeNode. */
  gitStatus?: GitStatus;
}

/**
 * Detect language from file extension for syntax highlighting
 */
export function detectLanguage(fileName: string): string {
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  const languageMap: Record<string, string> = {
    // JavaScript/TypeScript
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    mjs: 'javascript',
    cjs: 'javascript',
    // Python
    py: 'python',
    pyw: 'python',
    // Web
    html: 'html',
    htm: 'html',
    css: 'css',
    scss: 'scss',
    sass: 'scss',
    less: 'less',
    // Data formats
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    xml: 'xml',
    toml: 'ini',
    ini: 'ini',
    // Shell
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    fish: 'bash',
    // Other languages
    go: 'go',
    rs: 'rust',
    java: 'java',
    kt: 'kotlin',
    c: 'c',
    cpp: 'cpp',
    h: 'c',
    hpp: 'cpp',
    rb: 'ruby',
    php: 'php',
    sql: 'sql',
    md: 'markdown',
    markdown: 'markdown',
    // Config files
    dockerfile: 'dockerfile',
    makefile: 'makefile',
    gitignore: 'plaintext',
    env: 'plaintext',
  };

  // Handle special filenames
  const lowerName = fileName.toLowerCase();
  if (lowerName === 'dockerfile') return 'dockerfile';
  if (lowerName === 'makefile') return 'makefile';
  if (lowerName.startsWith('.env')) return 'plaintext';

  // Use Object.prototype.hasOwnProperty to safely check for property existence
  // This avoids issues with __proto__ and other special properties
  if (Object.prototype.hasOwnProperty.call(languageMap, ext)) {
    return languageMap[ext];
  }
  
  return 'plaintext';
}

/**
 * Check if content has changed from original
 */
export function isDirtyState(content: string, originalContent: string): boolean {
  return content !== originalContent;
}

/* ------------------------------------------------------------------ */
/*  Search match utility (Task 9.1)                                    */
/* ------------------------------------------------------------------ */

/** A single search match within the file content. */
export interface SearchMatch {
  /** 0-based line index. */
  lineIndex: number;
  /** Character offset within the line where the match starts. */
  startOffset: number;
  /** Length of the matched text. */
  length: number;
}

/**
 * Find all case-insensitive, non-overlapping occurrences of `query` in `content`.
 * Returns an empty array when `query` is empty.
 */
export function findAllMatches(content: string, query: string): SearchMatch[] {
  if (!query) return [];
  const matches: SearchMatch[] = [];
  const lines = content.split('\n');
  const lowerQuery = query.toLowerCase();
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const lowerLine = lines[lineIndex].toLowerCase();
    let offset = 0;
    while (offset <= lowerLine.length - lowerQuery.length) {
      const idx = lowerLine.indexOf(lowerQuery, offset);
      if (idx === -1) break;
      matches.push({ lineIndex, startOffset: idx, length: lowerQuery.length });
      offset = idx + lowerQuery.length; // non-overlapping
    }
  }
  return matches;
}

/* ------------------------------------------------------------------ */
/*  BreadcrumbBar sub-component (Task 5.2)                             */
/* ------------------------------------------------------------------ */

interface BreadcrumbBarProps {
  filePath: string;
}

/** Renders a file path as breadcrumb segments separated by › chevrons. */
function BreadcrumbBar({ filePath }: BreadcrumbBarProps) {
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
                <span className="text-[var(--color-text-muted)]">›</span>
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
/*  LineGutter sub-component (Task 6.1)                                */
/* ------------------------------------------------------------------ */

interface LineGutterProps {
  lineCount: number;
  scrollTop: number;
  activeLineNumber?: number;
}

/** Renders sequential line numbers synced with the editor scroll position. */
function LineGutter({ lineCount, scrollTop, activeLineNumber }: LineGutterProps) {
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
/*  DiffView sub-component (Task 8.1)                                  */
/* ------------------------------------------------------------------ */

interface DiffViewProps {
  lines: DiffLine[];
}

/** Renders an inline diff with line-by-line coloring and dual line-number gutters. */
function DiffView({ lines }: DiffViewProps) {
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
/*  SearchBar sub-component (Task 9.2)                                 */
/* ------------------------------------------------------------------ */

interface SearchBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  currentMatch: number;
  totalMatches: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
}

/** Floating search bar at the top of the editor area. */
function SearchBar({
  searchQuery,
  onSearchChange,
  currentMatch,
  totalMatches,
  onNext,
  onPrevious,
  onClose,
}: SearchBarProps) {
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
    // Let Cmd+S / Ctrl+S pass through — don't intercept
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
        placeholder="Find…"
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
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function FileEditorModal({
  isOpen,
  filePath,
  fileName,
  workspaceId,
  initialContent = '',
  onSave,
  onClose,
  gitStatus,
  onAttachToChat,
  isAttached,
}: FileEditorModalProps) {
  const [content, setContent] = useState(initialContent);
  const [originalContent, setOriginalContent] = useState(initialContent);
  const [isSaving, setIsSaving] = useState(false);
  const [showUnsavedWarning, setShowUnsavedWarning] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [activeLineNumber, setActiveLineNumber] = useState<number | undefined>(undefined);
  const [attachFeedback, setAttachFeedback] = useState(false);
  const [scrollTop, setScrollTop] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightRef = useRef<HTMLPreElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  const isDirty = isDirtyState(content, originalContent);
  const language = detectLanguage(fileName);

  // Reset state when modal opens with new content
  useEffect(() => {
    if (isOpen) {
      setContent(initialContent);
      setOriginalContent(initialContent);
      setShowUnsavedWarning(false);
      setShowDiff(false);
      setShowSearch(false);
      setSearchQuery('');
      setCurrentMatchIndex(0);
      setActiveLineNumber(undefined);
      setAttachFeedback(false);
    }
  }, [isOpen, initialContent]);

  // Update syntax highlighting when content changes
  useEffect(() => {
    if (highlightRef.current && isOpen) {
      // Escape HTML entities for display
      const escaped = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      
      try {
        const highlighted = hljs.highlight(escaped, { language }).value;
        highlightRef.current.innerHTML = highlighted + '\n'; // Add newline for proper scrolling
      } catch {
        // Fallback to plain text if highlighting fails
        highlightRef.current.textContent = content + '\n';
      }
    }
  }, [content, language, isOpen]);

  // Handle escape key — close search first, then modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        if (showSearch) {
          handleSearchClose();
          return;
        }
        handleCloseAttempt();
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, isDirty, showSearch]);

  // Handle Ctrl+S / Cmd+S to save (disabled in diff mode)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && isOpen && !showDiff) {
        e.preventDefault();
        handleSave();
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
    }

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, content, showDiff]);

  // Sync scroll between textarea, highlight, and gutter
  const handleScroll = useCallback(() => {
    if (textareaRef.current && highlightRef.current) {
      const top = textareaRef.current.scrollTop;
      highlightRef.current.scrollTop = top;
      highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
      setScrollTop(top);
    }
  }, []);

  const handleCloseAttempt = useCallback(() => {
    if (isDirty) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      await onSave(content);
      setOriginalContent(content);
      onClose();
    } catch (error) {
      console.error('Failed to save file:', error);
      // Keep modal open on error
    } finally {
      setIsSaving(false);
    }
  }, [content, onSave, onClose]);

  const handleCancel = useCallback(() => {
    if (isDirty) {
      setShowUnsavedWarning(true);
    } else {
      onClose();
    }
  }, [isDirty, onClose]);

  const handleDiscardChanges = useCallback(() => {
    setShowUnsavedWarning(false);
    setContent(originalContent);
    onClose();
  }, [originalContent, onClose]);

  const handleContinueEditing = useCallback(() => {
    setShowUnsavedWarning(false);
  }, []);

  // --- Computed values ---

  const lineCount = content.split('\n').length;

  /** Diff lines computed on-demand when diff mode is active (Task 8.2). */
  const diffLines = useMemo(() => {
    if (!showDiff) return [];
    return computeLineDiff(originalContent, content);
  }, [showDiff, originalContent, content]);

  /** Search matches computed reactively (Task 9.3). */
  const searchMatches = useMemo(() => {
    if (!searchQuery) return [];
    return findAllMatches(content, searchQuery);
  }, [content, searchQuery]);

  // --- Active line tracking from cursor position ---

  const handleSelect = useCallback(() => {
    if (!textareaRef.current) return;
    const pos = textareaRef.current.selectionStart;
    const textBefore = content.slice(0, pos);
    const line = textBefore.split('\n').length;
    setActiveLineNumber(line);
  }, [content]);

  // --- Attach to Chat handler (Task 5.4) ---

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

  // --- Diff toggle handler (Task 8.2) ---

  const handleToggleDiff = useCallback(() => {
    setShowDiff((prev) => !prev);
  }, []);

  // --- Search navigation handlers (Task 9.3) ---

  const handleSearchNext = useCallback(() => {
    if (searchMatches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev + 1) % searchMatches.length);
  }, [searchMatches.length]);

  const handleSearchPrevious = useCallback(() => {
    if (searchMatches.length === 0) return;
    setCurrentMatchIndex((prev) => (prev - 1 + searchMatches.length) % searchMatches.length);
  }, [searchMatches.length]);

  const handleSearchClose = useCallback(() => {
    setShowSearch(false);
    setSearchQuery('');
    setCurrentMatchIndex(0);
    textareaRef.current?.focus();
  }, []);

  // --- Cmd+F / Ctrl+F keyboard shortcut (Task 9.3) ---

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f' && isOpen) {
        e.preventDefault();
        setShowSearch(true);
      }
    };
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === overlayRef.current) {
          handleCloseAttempt();
        }
      }}
      data-testid="file-editor-modal"
    >
      <div
        className={clsx(
          'w-full h-[80vh] bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl flex flex-col',
          showDiff ? 'max-w-6xl' : 'max-w-4xl'
        )}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header — file icon, git badge, breadcrumb, attach, close */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {/* File-type icon (Task 5.1) */}
            <span
              className="material-symbols-outlined text-lg shrink-0"
              style={{ color: fileIconColor(fileName) }}
            >
              {fileIcon(fileName)}
            </span>
            {/* Git status badge (Task 5.1) */}
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
            {/* Breadcrumb path (Task 5.2) */}
            <BreadcrumbBar filePath={filePath} />
            {isDirty && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)] bg-opacity-20 text-[var(--color-warning)] shrink-0">
                Modified
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0 ml-2">
            {/* Attach to Chat button (Task 5.4) */}
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
                {isAttached || attachFeedback ? 'Attached ✓' : 'Attach to Chat'}
              </button>
            )}
            <button
              onClick={handleCloseAttempt}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              aria-label="Close"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>

        {/* Editor area — gutter + textarea/pre overlay OR diff view */}
        <div className="flex-1 relative overflow-hidden flex">
          {/* SearchBar (Task 9.2/9.3) */}
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
            /* DiffView replaces editor when active (Task 8.1/8.2) */
            <div className="flex-1 relative overflow-hidden">
              <DiffView lines={diffLines} />
            </div>
          ) : (
            <>
              {/* Line gutter (Task 6.1) */}
              <LineGutter
                lineCount={lineCount}
                scrollTop={scrollTop}
                activeLineNumber={activeLineNumber}
              />
              {/* Editor content area */}
              <div className="flex-1 relative overflow-hidden">
                {/* Syntax highlighted background */}
                <pre
                  ref={highlightRef}
                  className={clsx(
                    'absolute inset-0 m-0 p-4 overflow-auto',
                    'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                    'pointer-events-none',
                    'bg-[var(--color-background)]'
                  )}
                  aria-hidden="true"
                />
                {/* Search highlight overlay (Task 9.3) */}
                {showSearch && searchMatches.length > 0 && (
                  <pre
                    className={clsx(
                      'absolute inset-0 m-0 p-4 overflow-auto',
                      'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                      'pointer-events-none z-[1]'
                    )}
                    style={{ scrollBehavior: 'auto' }}
                    aria-hidden="true"
                  >
                    {content.split('\n').map((line, lineIdx) => {
                      const lineMatches = searchMatches.filter((m) => m.lineIndex === lineIdx);
                      if (lineMatches.length === 0) return <div key={lineIdx}>{'\n'}</div>;
                      const parts: React.ReactNode[] = [];
                      let cursor = 0;
                      lineMatches.forEach((m, mi) => {
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
                {/* Editable textarea overlay */}
                <textarea
                  ref={textareaRef}
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  onScroll={handleScroll}
                  onSelect={handleSelect}
                  onClick={handleSelect}
                  onKeyUp={handleSelect}
                  className={clsx(
                    'absolute inset-0 w-full h-full m-0 p-4 resize-none',
                    'font-mono text-sm leading-6 whitespace-pre-wrap break-words',
                    'bg-transparent text-transparent caret-[var(--color-text)]',
                    'border-none outline-none',
                    'overflow-auto'
                  )}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                  data-testid="file-editor-textarea"
                />
              </div>
            </>
          )}
        </div>

        {/* Footer — language badge, show changes toggle, cancel, save */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            {language !== 'plaintext' && (
              <span className="px-2 py-1 rounded bg-[var(--color-hover)]">
                {language}
              </span>
            )}
            {/* Show Changes toggle (Task 8.2) */}
            <button
              onClick={handleToggleDiff}
              disabled={!isDirty && !showDiff}
              className={clsx(
                'flex items-center gap-1 px-2 py-1 rounded transition-colors',
                showDiff
                  ? 'bg-[var(--color-primary)] bg-opacity-20 text-[var(--color-primary)]'
                  : 'hover:bg-[var(--color-hover)] text-[var(--color-text-muted)]',
                !isDirty && !showDiff && 'opacity-40 cursor-not-allowed'
              )}
              data-testid="show-changes-toggle"
            >
              <span className="material-symbols-outlined text-sm">difference</span>
              {showDiff ? 'Hide Changes' : 'Show Changes'}
            </button>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleCancel}
              disabled={isSaving}
              data-testid="file-editor-cancel"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isLoading={isSaving}
              disabled={!isDirty || showDiff}
              data-testid="file-editor-save"
            >
              Save
            </Button>
          </div>
        </div>
      </div>

      {/* Unsaved Changes Warning Dialog - Requirement 9.8 */}
      {showUnsavedWarning && (
        <div
          className="fixed inset-0 z-60 flex items-center justify-center p-4 bg-black/50"
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
    </div>
  );
}
