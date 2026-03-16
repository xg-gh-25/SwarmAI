/**
 * FileEditorModal — Modal overlay wrapper for FileEditorCore.
 *
 * Thin shell: provides the fixed overlay backdrop and body-overflow lock.
 * All editor logic lives in FileEditorCore (shared with FileEditorPanel).
 *
 * Preserved exports for backward compatibility:
 * - `FileEditorModal`    — Main modal component (default export)
 * - `FileEditorModalProps` / `FileEditorState` — Public interfaces
 * - `detectLanguage()`   — Maps file extensions to highlight.js language names
 * - `isDirtyState()`     — Checks whether content differs from original
 * - `findAllMatches()`   — Case-insensitive search match computation
 * - `SearchMatch`        — Interface for a single search hit
 */

import { useEffect, useRef, useCallback } from 'react';
import FileEditorCore from './FileEditorCore';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

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
  /** When true, the file is a system-default context file and editing is disabled. */
  readonly?: boolean;
  /** The last committed (HEAD) version of the file, used as the diff baseline. */
  committedContent?: string;
  /** Callback to switch back to panel mode. */
  onToggleMode?: () => void;
  /** Called after save when diff is non-empty (L2 auto-diff).
   *  Second arg is the fileName captured at save time to avoid stale closures. */
  onSaveWithDiff?: (diffSummary: string, fileName?: string) => void;
  /** Called on every content change so parent can track live edits.
   *  Used to preserve content across panel ↔ modal mode switches. */
  onContentChange?: (content: string) => void;
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
      offset = idx + lowerQuery.length;
    }
  }
  return matches;
}

/* ------------------------------------------------------------------ */
/*  Modal wrapper — thin shell over FileEditorCore                     */
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
  readonly,
  committedContent,
  onToggleMode,
  onSaveWithDiff,
  onContentChange,
}: FileEditorModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  // Lock body scroll while modal is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  const handleOverlayClick = useCallback((e: React.MouseEvent) => {
    if (e.target === overlayRef.current) {
      onClose();
    }
  }, [onClose]);

  if (!isOpen) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      onMouseDown={handleOverlayClick}
      data-testid="file-editor-modal"
    >
      <div className="w-[95vw] h-[90vh] max-w-[1600px]">
        <FileEditorCore
          filePath={filePath}
          fileName={fileName}
          workspaceId={workspaceId}
          initialContent={initialContent}
          onSave={onSave}
          onClose={onClose}
          gitStatus={gitStatus}
          onAttachToChat={onAttachToChat}
          isAttached={isAttached}
          readonly={readonly}
          committedContent={committedContent}
          variant="modal"
          onToggleMode={onToggleMode}
          onSaveWithDiff={onSaveWithDiff}
          onContentChange={onContentChange}
        />
      </div>
    </div>
  );
}
