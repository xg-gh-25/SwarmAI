/**
 * FileEditorModal - Modal for editing files with syntax highlighting
 *
 * Requirements:
 * - 9.1: Open on double-click in Workspace Explorer
 * - 9.2: Display as modal overlay, preserving chat underneath
 * - 9.3: Provide syntax highlighting for common programming languages
 * - 9.4: Display file path in header
 * - 9.5: Provide Save and Cancel buttons
 * - 9.6: Save changes to file on Save click
 * - 9.7: Discard changes on Cancel click
 * - 9.8: Show confirmation dialog on close with unsaved changes
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import clsx from 'clsx';
import hljs from 'highlight.js';
import Button from './Button';

export interface FileEditorModalProps {
  isOpen: boolean;
  filePath: string;
  fileName: string;
  workspaceId: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
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

export default function FileEditorModal({
  isOpen,
  filePath,
  fileName,
  initialContent = '',
  onSave,
  onClose,
}: FileEditorModalProps) {
  const [content, setContent] = useState(initialContent);
  const [originalContent, setOriginalContent] = useState(initialContent);
  const [isSaving, setIsSaving] = useState(false);
  const [showUnsavedWarning, setShowUnsavedWarning] = useState(false);
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

  // Handle escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
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
  }, [isOpen, isDirty]);

  // Handle Ctrl+S / Cmd+S to save
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && isOpen) {
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
  }, [isOpen, content]);

  // Sync scroll between textarea and highlight
  const handleScroll = useCallback(() => {
    if (textareaRef.current && highlightRef.current) {
      highlightRef.current.scrollTop = textareaRef.current.scrollTop;
      highlightRef.current.scrollLeft = textareaRef.current.scrollLeft;
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
        className="w-full max-w-4xl h-[80vh] bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl flex flex-col"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header - Requirement 9.4 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="material-symbols-outlined text-[var(--color-text-muted)]">
              draft
            </span>
            <span className="text-sm text-[var(--color-text)] truncate" title={filePath}>
              {filePath}
            </span>
            {isDirty && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-warning)] bg-opacity-20 text-[var(--color-warning)]">
                Modified
              </span>
            )}
          </div>
          <button
            onClick={handleCloseAttempt}
            className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            aria-label="Close"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* Editor - Requirement 9.3 */}
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
          {/* Editable textarea overlay */}
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onScroll={handleScroll}
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

        {/* Footer - Requirements 9.5, 9.6, 9.7 */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--color-border)] shrink-0">
          <div className="text-xs text-[var(--color-text-muted)]">
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
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isLoading={isSaving}
              disabled={!isDirty}
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
