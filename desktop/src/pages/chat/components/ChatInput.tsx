import { useState, useCallback, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { UnifiedAttachment, SystemPromptMetadata } from '../../../types';
import { FileAttachmentButton, FileAttachmentPreview } from '../../../components/chat';
import { TSCCPopoverButton } from './TSCCPopoverButton';
import { ContextUsageRing } from './ContextUsageRing';
import { SLASH_COMMANDS } from '../constants';
import type { DropPayload } from './RightSidebar/types';

interface ChatInputProps {
  inputValue: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
  selectedAgentId: string | null;
  attachments: UnifiedAttachment[];
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (id: string) => void;
  isProcessingFiles: boolean;
  fileError: string | null;
  canAddMore: boolean;
  /** TSCC session ID for the popover button */
  sessionId?: string | null;
  /** System prompt metadata for the popover button */
  promptMetadata?: SystemPromptMetadata | null;
  /** Context usage percentage for the ring indicator (null = no data) */
  contextPct?: number | null;
  /** Whether the textarea is in expanded mode (60vh max-height) */
  isExpanded: boolean;
  /** Callback to toggle expanded/compact mode */
  onExpandedChange: (expanded: boolean) => void;
  /** External disabled flag (e.g. backend disconnected). Disables input and action buttons. */
  disabled?: boolean;
  /** Ref to the currently active tab ID — read synchronously at drop time for tab-scoped isolation. */
  activeTabIdRef?: React.RefObject<string | null>;
  /** Per-tab draft text storage — drop operations write to the entry keyed by active tab ID. */
  inputValueMapRef?: React.MutableRefObject<Map<string, string>>;
  /** Callback to propagate draft text changes to the per-tab storage layer. */
  onInputValueChange?: (tabId: string, value: string) => void;
}

const MAX_ROWS = 20;

/**
 * Chat Input Component with file attachments and slash commands
 */
export function ChatInput({
  inputValue,
  onInputChange,
  onSend,
  onStop,
  isStreaming,
  selectedAgentId,
  attachments,
  onAddFiles,
  onRemoveFile,
  isProcessingFiles,
  fileError,
  canAddMore,
  sessionId,
  promptMetadata,
  contextPct,
  isExpanded,
  onExpandedChange,
  disabled = false,
  activeTabIdRef,
  inputValueMapRef,
  onInputValueChange,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [lineCount, setLineCount] = useState(1);
  const [modeAnnouncement, setModeAnnouncement] = useState('');

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const maxHeightRef = useRef<number>(400); // fallback: 20 * 20px
  const transitionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Compute maxHeight once from actual computed line-height at mount
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
    maxHeightRef.current = MAX_ROWS * lineHeight;
  }, []);

  // Apply a brief CSS transition for mode toggle animations only (not during typing)
  const applyTransition = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.transition = 'height 150ms ease-out';
    if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
    transitionTimerRef.current = setTimeout(() => {
      if (el) el.style.transition = '';
      transitionTimerRef.current = null;
    }, 160); // slightly longer than transition duration
  }, []);

  // Cleanup transition timer and inline style on unmount
  useEffect(() => {
    return () => {
      if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
      const el = textareaRef.current;
      if (el) el.style.transition = '';
    };
  }, []);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    const maxHeight = isExpanded ? window.innerHeight * 0.6 : maxHeightRef.current;
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, maxHeight);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
    // Update line count — only trigger re-render when the value actually changes
    const lines = el.value.split('\n').length;
    setLineCount(prev => prev !== lines ? lines : prev);
  }, [isExpanded]);

  // Call adjustHeight whenever inputValue changes (handles programmatic clears after send)
  useEffect(() => {
    adjustHeight();
  }, [inputValue, isExpanded, adjustHeight]);

  // Re-clamp textarea height on window resize when expanded (60vh is viewport-relative)
  useEffect(() => {
    if (!isExpanded) return;
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const handleResize = () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => adjustHeight(), 100);
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (resizeTimer) clearTimeout(resizeTimer);
    };
  }, [isExpanded, adjustHeight]);

  // Toggle between compact and expanded modes, preserving cursor position
  const toggleExpanded = useCallback(() => {
    const el = textareaRef.current;
    const selStart = el?.selectionStart ?? 0;
    const selEnd = el?.selectionEnd ?? 0;
    applyTransition();
    onExpandedChange(!isExpanded);
    setModeAnnouncement(isExpanded ? 'Input collapsed' : 'Input expanded');
    // Clear announcement after 2s to prevent stale re-announcements on focus changes
    setTimeout(() => setModeAnnouncement(''), 2000);
    requestAnimationFrame(() => {
      if (el) {
        el.selectionStart = selStart;
        el.selectionEnd = selEnd;
        const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
        const cursorLine = el.value.substring(0, selStart).split('\n').length;
        const cursorTop = (cursorLine - 1) * lineHeight;
        if (cursorTop < el.scrollTop) {
          el.scrollTop = cursorTop;
        } else if (cursorTop + lineHeight > el.scrollTop + el.clientHeight) {
          el.scrollTop = cursorTop + lineHeight - el.clientHeight;
        }
      }
    });
  }, [isExpanded, onExpandedChange, applyTransition]);

  // Filter commands based on input
  const filteredCommands = SLASH_COMMANDS.filter((cmd) =>
    cmd.name.toLowerCase().startsWith(inputValue.toLowerCase())
  );

  // Handle input change with slash command detection
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    onInputChange(value);

    if (value.startsWith('/') && !value.includes(' ')) {
      setShowCommandSuggestions(true);
      setSelectedCommandIndex(0);
    } else {
      setShowCommandSuggestions(false);
    }
  };

  // Handle command selection
  const handleSelectCommand = (command: string) => {
    onInputChange(command + ' ');
    setShowCommandSuggestions(false);
  };

  // Handle paste event for images
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            imageFiles.push(file);
          }
        }
      }
      if (imageFiles.length > 0) {
        e.preventDefault();
        onAddFiles(imageFiles);
      }
    },
    [onAddFiles]
  );

  // Drag handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files') || e.dataTransfer.types.includes('application/json')) {
      e.preventDefault();
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files') || e.dataTransfer.types.includes('application/json')) {
      e.preventDefault();
      setIsDragging(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      // 1. Existing file-drop behavior — unchanged
      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) {
        onAddFiles(files);
        return;
      }

      // 2. Radar DropPayload (application/json) processing
      const jsonData = e.dataTransfer.getData('application/json');
      if (!jsonData) return;

      let payload: DropPayload;
      try {
        const parsed = JSON.parse(jsonData);
        // Validate discriminator and required fields before casting
        if (!parsed || typeof parsed !== 'object' || typeof parsed.type !== 'string') {
          console.warn('[ChatInput] Drop payload missing type discriminator');
          return;
        }
        payload = parsed as DropPayload;
      } catch {
        console.warn('[ChatInput] Invalid JSON in drop payload');
        return;
      }

      if (payload.type !== 'radar-todo' && payload.type !== 'radar-artifact') return;

      // Build the text to insert based on payload type
      let text: string;
      if (payload.type === 'radar-todo') {
        text = payload.context
          ? `[ToDo] ${payload.title}\n${payload.context}`
          : `[ToDo] ${payload.title}`;
      } else {
        text = `[Artifact] ${payload.title} (${payload.path})`;
      }

      // --- SYNCHRONOUS read-and-write: no await, no setTimeout, no setState callback ---
      // Read active tab ID from ref at drop time (Principle 2 & 13: never from React state)
      const activeTabId = activeTabIdRef?.current ?? null;

      if (activeTabId && inputValueMapRef && onInputValueChange) {
        // Read existing draft for this tab, initialize if missing
        const existing = inputValueMapRef.current.get(activeTabId) ?? '';
        const newValue = existing ? `${existing}\n${text}` : text;
        // Write to per-tab draft storage keyed by active tab ID
        inputValueMapRef.current.set(activeTabId, newValue);
        // Notify parent of the change for this specific tab
        onInputValueChange(activeTabId, newValue);
      }

      // Only update the visible textarea if the drop-time tab matches the currently rendered tab
      // (inputValue is the display mirror for the active tab, so we update it directly)
      if (activeTabId && activeTabIdRef?.current === activeTabId) {
        const existing = inputValue;
        const newValue = existing ? `${existing}\n${text}` : text;
        onInputChange(newValue);
      }

      // Focus the input cursor after population
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
        const el = textareaRef.current;
        if (el) {
          el.selectionStart = el.value.length;
          el.selectionEnd = el.value.length;
        }
      });
    },
    [onAddFiles, activeTabIdRef, inputValueMapRef, onInputValueChange, inputValue, onInputChange]
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Expand/collapse shortcut
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'E') {
      e.preventDefault();
      toggleExpanded();
      return;
    }

    // Handle slash command navigation
    if (showCommandSuggestions && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev + 1) % filteredCommands.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        handleSelectCommand(filteredCommands[selectedCommandIndex].name);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowCommandSuggestions(false);
        return;
      }
    }

    // Normal enter to send
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Wrap onSend to reset textarea height after sending
  const handleSend = useCallback(() => {
    if (isExpanded) {
      applyTransition();
      onExpandedChange(false);
    }
    onSend();
    const el = textareaRef.current;
    if (el) {
      el.style.height = '';       // clear inline style, rows={2} reasserts minimum
      el.style.overflowY = 'hidden';
    }
  }, [onSend, isExpanded, onExpandedChange, applyTransition]);

  const hasAttachments = attachments.some((a) => !a.error && !a.isLoading);
  const canSend = (inputValue.trim() || hasAttachments) && selectedAgentId;

  return (
    <div className="px-4 pb-4 pt-2">
        {/* Input Container with drag-and-drop */}
        <div
          className={clsx(
            'bg-[var(--color-card)] border rounded-2xl p-3 relative transition-all',
            isDragging
              ? 'border-primary bg-primary/5'
              : 'border-[var(--color-border)]'
          )}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Drag Overlay */}
          {isDragging && (
            <div className="absolute inset-0 bg-primary/10 flex items-center justify-center rounded-2xl z-10 pointer-events-none">
              <div className="flex flex-col items-center gap-2">
                <span className="material-symbols-outlined text-primary text-3xl">upload_file</span>
                <span className="text-primary font-medium">Drop files here</span>
              </div>
            </div>
          )}

          {/* File Attachment Preview */}
          {attachments.length > 0 && <FileAttachmentPreview attachments={attachments} onRemove={onRemoveFile} />}

          {/* File Error */}
          {fileError && (
            <div className="mb-3 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {fileError}
            </div>
          )}


          {/* Input Row */}
          <div className="relative flex items-center gap-3">

            {/* Slash Command Suggestions */}
            {showCommandSuggestions && filteredCommands.length > 0 && (
              <div className="absolute bottom-full left-0 mb-2 w-64 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden z-10">
                <div className="px-3 py-2 border-b border-[var(--color-border)]">
                  <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
                    Commands
                  </span>
                </div>
                {filteredCommands.map((cmd, index) => (
                  <button
                    key={cmd.name}
                    onClick={() => handleSelectCommand(cmd.name)}
                    className={clsx(
                      'w-full px-3 py-2.5 flex items-start gap-3 text-left transition-colors',
                      index === selectedCommandIndex
                        ? 'bg-primary text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                    )}
                  >
                    <span className="material-symbols-outlined text-lg mt-0.5">terminal</span>
                    <div>
                      <p className="font-medium">{cmd.name}</p>
                      <p
                        className={clsx(
                          'text-xs',
                          index === selectedCommandIndex ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                        )}
                      >
                        {cmd.description}
                      </p>
                    </div>
                  </button>
                ))}
                <div className="px-3 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50">
                  <span className="text-xs text-[var(--color-text-muted)]">
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">↑↓</kbd> navigate
                    <span className="mx-2">·</span>
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Tab</kbd> select
                    <span className="mx-2">·</span>
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Esc</kbd> close
                  </span>
                </div>
              </div>
            )}

            {/* Text Input — disabled during streaming or when backend is disconnected */}
            <textarea
              ref={textareaRef}
              data-testid="chat-input"
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={disabled ? t('chat.disconnectedPlaceholder', 'Backend offline...') : isStreaming ? t('chat.streamingPlaceholder', 'Waiting for response...') : 'Ask anything'}
              rows={2}
              disabled={isStreaming || disabled}
              className={clsx(
                'flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none py-2',
                (isStreaming || disabled) && 'opacity-50 cursor-not-allowed'
              )}
            />

            {/* Expand/Collapse Toggle Button */}
            {(lineCount > 3 || isExpanded) && (
              <button
                onClick={toggleExpanded}
                aria-label={isExpanded ? 'Collapse input' : 'Expand input'}
                aria-expanded={isExpanded}
                title={`${isExpanded ? 'Collapse' : 'Expand'} input (${/Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl'}+Shift+E)`}
                className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              >
                <span className="material-symbols-outlined text-lg">
                  {isExpanded ? 'collapse_content' : 'expand_content'}
                </span>
              </button>
            )}

            {/* Send Button */}
            <button
              onClick={isStreaming ? onStop : handleSend}
              disabled={(!isStreaming && !canSend) || disabled}
              className={clsx(
                'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 transition-colors',
                isStreaming
                  ? 'bg-red-500 hover:bg-red-600'
                  : 'bg-primary hover:bg-primary-hover',
                ((!isStreaming && !canSend) || disabled) && 'opacity-50 cursor-not-allowed'
              )}
              title={
                isStreaming
                  ? 'Stop generation'
                  : attachments.length > 0
                      ? 'Send with attachments'
                      : 'Send message'
              }
            >
              {isStreaming ? (
                <span className="material-symbols-outlined text-white text-xl">stop</span>
              ) : (
                <span className="material-symbols-outlined text-white text-xl">arrow_upward</span>
              )}
            </button>
          </div>

          {/* Bottom Row - Attachment button + TSCC button + Commands hint */}
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--color-border)]/50">
            <div className="flex items-center gap-2">
              <FileAttachmentButton onFilesSelected={onAddFiles} disabled={isProcessingFiles || isStreaming || disabled} canAddMore={canAddMore} />
              <TSCCPopoverButton sessionId={sessionId ?? null} metadata={promptMetadata ?? null} />
              <ContextUsageRing pct={contextPct ?? null} />
            </div>
            <div className="flex items-center gap-3">
              {lineCount > 5 && (
                <span className="text-xs text-[var(--color-text-muted)]">
                  {lineCount} lines
                </span>
              )}
              <span className="text-xs text-[var(--color-text-muted)]">
                Type <kbd className="px-1.5 py-0.5 bg-[var(--color-hover)] rounded text-xs mx-1">/</kbd> for commands
              </span>
            </div>
          </div>

          {/* Accessibility: announce mode changes to screen readers */}
          <div aria-live="polite" className="sr-only">
            {modeAnnouncement}
          </div>
        </div>
    </div>
  );
}
