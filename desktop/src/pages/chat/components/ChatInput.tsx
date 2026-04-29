import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { UnifiedAttachment, SystemPromptMetadata } from '../../../types';
import { FileAttachmentButton, FileAttachmentPreview } from '../../../components/chat';
import { TSCCPopoverButton } from './TSCCPopoverButton';
import { ContextUsageRing } from './ContextUsageRing';
import { SYSTEM_COMMANDS } from '../constants';
import type { SlashCommand } from '../constants';
import type { Skill } from '../../../types';
import type { DropPayload } from './RightSidebar/types';
import { todosService } from '../../../services/todos';
import { useVoiceRecorder } from '../../../hooks/useVoiceRecorder';
import { VoiceConversationIndicator } from '../../../components/chat/VoiceConversationIndicator';
import type { VoiceConversationState } from '../../../hooks/useVoiceConversation';

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
  /** True when streaming but no real SDK events received for >60s (session likely stalled). */
  isLikelyStalled?: boolean;
  /** Available skills for slash command picker */
  skills?: Skill[];
  /** Voice conversation mode state (off = normal text mode) */
  voiceConversationState?: VoiceConversationState;
  /** Toggle voice conversation mode on/off */
  onVoiceConversationToggle?: () => void;
  /** Interrupt TTS playback and return to listening */
  onVoiceConversationInterrupt?: () => void;
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
  isLikelyStalled = false,
  skills = [],
  voiceConversationState = 'off',
  onVoiceConversationToggle,
  onVoiceConversationInterrupt,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [lineCount, setLineCount] = useState(1);
  const [modeAnnouncement, setModeAnnouncement] = useState('');
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const voiceErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Ref to always have the latest inputValue inside callbacks (avoids stale closure)
  const inputValueRef = useRef(inputValue);
  inputValueRef.current = inputValue;

  // Cleanup voice error timer on unmount
  useEffect(() => {
    return () => {
      if (voiceErrorTimerRef.current) clearTimeout(voiceErrorTimerRef.current);
    };
  }, []);

  // Stable callbacks for voice recorder (avoids unnecessary hook re-creation)
  const handleVoiceTranscript = useCallback((text: string) => {
    const current = inputValueRef.current;
    const separator = current && !current.endsWith(' ') ? ' ' : '';
    onInputChange(current + separator + text);
    setVoiceError(null);
  }, [onInputChange]);

  const handleVoiceError = useCallback((err: string) => {
    setVoiceError(err);
    if (voiceErrorTimerRef.current) clearTimeout(voiceErrorTimerRef.current);
    voiceErrorTimerRef.current = setTimeout(() => setVoiceError(null), 4000);
  }, []);

  // Voice recording — append transcribed text to current input
  const { voiceState, toggleRecording, isSupported: voiceSupported } = useVoiceRecorder({
    onTranscript: handleVoiceTranscript,
    onError: handleVoiceError,
  });

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

  // L2: Listen for auto-diff injection from FileEditorPanel save
  // and L3 review feedback. Updates both the visible textarea AND the
  // per-tab draft storage so the text survives tab switches.
  useEffect(() => {
    const handler = (e: Event) => {
      const { text, focus } = (e as CustomEvent<{ text: string; focus?: boolean }>).detail ?? {};
      if (text) {
        onInputChange(text);
        // Sync to per-tab draft storage so the injected text survives tab switches
        const tabId = activeTabIdRef?.current;
        if (tabId && inputValueMapRef) {
          inputValueMapRef.current.set(tabId, text);
        }
        if (focus) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      }
    };
    window.addEventListener('swarm:inject-chat-input', handler);
    return () => window.removeEventListener('swarm:inject-chat-input', handler);
  }, [onInputChange, activeTabIdRef, inputValueMapRef]);

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

  // Build merged command list: system commands + skills
  const allCommands: SlashCommand[] = useMemo(() => {
    const skillCommands: SlashCommand[] = skills.map((s) => {
      // Strip s_ prefix from folder names for cleaner slash commands
      const cleanName = s.folderName.replace(/^s_/, '');
      return {
        name: `/${cleanName}`,
        description: s.description || s.name,
        category: 'skill' as const,
      };
    });
    return [...SYSTEM_COMMANDS, ...skillCommands];
  }, [skills]);

  // Filter commands based on input
  const filteredCommands = useMemo(() => {
    if (!inputValue.startsWith('/')) return [];
    const query = inputValue.toLowerCase();
    return allCommands.filter((cmd) =>
      cmd.name.toLowerCase().startsWith(query)
    );
  }, [inputValue, allCommands]);

  // F4 fix: clamp selectedCommandIndex when filtered list shrinks
  // F7 fix: auto-close dropdown when filter produces 0 results
  useEffect(() => {
    if (filteredCommands.length === 0 && showCommandSuggestions) {
      setShowCommandSuggestions(false);
    } else if (selectedCommandIndex >= filteredCommands.length && filteredCommands.length > 0) {
      setSelectedCommandIndex(filteredCommands.length - 1);
    }
  }, [filteredCommands.length, selectedCommandIndex, showCommandSuggestions]);

  // Group filtered commands by category for section headers
  const systemCmds = filteredCommands.filter((c) => c.category === 'system');
  const skillCmds = filteredCommands.filter((c) => c.category === 'skill');

  // F1+F2 fix: click-outside and global Escape to dismiss dropdown
  const dropdownRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!showCommandSuggestions) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node) &&
          textareaRef.current && !textareaRef.current.contains(e.target as Node)) {
        setShowCommandSuggestions(false);
      }
    };
    // F2: Global Escape works even when focus is on dropdown buttons.
    // Only fires when focus is NOT on the textarea (textarea has its own
    // Escape handler in handleKeyDown to avoid double-fire — F9 fix).
    const handleGlobalEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && document.activeElement !== textareaRef.current) {
        setShowCommandSuggestions(false);
        textareaRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleGlobalEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleGlobalEscape);
    };
  }, [showCommandSuggestions]);

  // F5 fix: auto-scroll to selected item. F11 fix: clear stale refs on list change.
  const itemRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  useEffect(() => { itemRefs.current.clear(); }, [filteredCommands.length]);
  useEffect(() => {
    const el = itemRefs.current.get(selectedCommandIndex);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [selectedCommandIndex]);

  // Handle input change with slash command detection
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    onInputChange(value);

    // Show suggestions when input starts with / — allow spaces for multi-word
    // commands like "/plugin install". Hide when user is typing args after a
    // complete command (e.g., "/plugin install my-plugin@market").
    if (value.startsWith('/')) {
      const isTypingArgs = allCommands.some(
        (cmd) => value.toLowerCase().startsWith(cmd.name.toLowerCase() + ' ') && value.length > cmd.name.length + 1
      );
      if (isTypingArgs) {
        setShowCommandSuggestions(false);
      } else {
        setShowCommandSuggestions(true);
        setSelectedCommandIndex(0);
      }
    } else {
      setShowCommandSuggestions(false);
    }
  };

  // Handle command selection
  const handleSelectCommand = (command: string) => {
    onInputChange(command + ' ');
    setShowCommandSuggestions(false);
    // Refocus textarea after selecting a command
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  // Handle paste event for files (images, PDFs, Office docs, audio, etc.)
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const pastedFiles: File[] = [];
      for (const item of items) {
        // Accept all file items — classification/validation in addFiles handles the rest
        if (item.kind === 'file') {
          const file = item.getAsFile();
          if (file) {
            pastedFiles.push(file);
          }
        }
      }
      if (pastedFiles.length > 0) {
        e.preventDefault();
        onAddFiles(pastedFiles);
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
        // Include todo ID so agent can retrieve full work packet via todo_db.py get <id>
        const idPrefix = payload.id.slice(0, 8);
        text = `[ToDo:${idPrefix}] ${payload.title}`;
        if (payload.context) {
          text += `\n${payload.context}`;
        }

        // Bind todo to session for lifecycle auto-completion
        const tabId = activeTabIdRef?.current;
        if (tabId && payload.id) {
          todosService.bindToSession(tabId, payload.id).catch((err: unknown) =>
            console.warn('[ChatInput] Failed to bind todo to session:', err)
          );
        }
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

    // Escape to stop generation (when streaming, and slash commands not open)
    if (e.key === 'Escape' && isStreaming) {
      e.preventDefault();
      onStop();
      return;
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
    <div className="pl-2 pr-4 pb-4 pt-2">
        {/* Input Container with drag-and-drop */}
        <div
          className={clsx(
            'bg-[var(--color-card)] border rounded-xl p-3 relative transition-all',
            isDragging
              ? 'border-primary bg-primary/5'
              : 'border-[var(--color-border)] focus-within:border-[rgba(43,108,238,0.5)] focus-within:shadow-[0_0_0_2px_rgba(43,108,238,0.1)]'
          )}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Drag Overlay */}
          {isDragging && (
            <div className="absolute inset-0 bg-primary/10 flex items-center justify-center rounded-xl z-10 pointer-events-none">
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

            {/* Slash Command Suggestions — system commands + skills */}
            {showCommandSuggestions && filteredCommands.length > 0 && (
              <div
                ref={dropdownRef}
                className="absolute bottom-full left-0 mb-2 w-80 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden z-10 flex flex-col max-h-80"
              >
                {/* Scrollable content area */}
                <div className="overflow-y-auto flex-1">
                  {/* System Commands Section */}
                  {systemCmds.length > 0 && (
                    <>
                      <div className="px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-card)] sticky top-0 z-10">
                        <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
                          Commands
                        </span>
                      </div>
                      {systemCmds.map((cmd) => {
                        const globalIndex = filteredCommands.indexOf(cmd);
                        return (
                          <button
                            key={cmd.name}
                            ref={(el) => { if (el) itemRefs.current.set(globalIndex, el); }}
                            onClick={() => handleSelectCommand(cmd.name)}
                            className={clsx(
                              'w-full px-3 py-2 flex items-start gap-3 text-left transition-colors',
                              globalIndex === selectedCommandIndex
                                ? 'bg-primary text-white'
                                : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                            )}
                          >
                            <span className="material-symbols-outlined text-base mt-0.5 opacity-60">terminal</span>
                            <div className="min-w-0 flex-1">
                              <p className="font-medium text-sm">{cmd.name}</p>
                              <p
                                className={clsx(
                                  'text-xs truncate',
                                  globalIndex === selectedCommandIndex ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                                )}
                              >
                                {cmd.description}
                              </p>
                            </div>
                          </button>
                        );
                      })}
                    </>
                  )}
                  {/* Skills Section */}
                  {skillCmds.length > 0 && (
                    <>
                      <div className="px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-card)] sticky top-0 z-10">
                        <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
                          Skills ({skillCmds.length})
                        </span>
                      </div>
                      {skillCmds.map((cmd) => {
                        const globalIndex = filteredCommands.indexOf(cmd);
                        return (
                          <button
                            key={cmd.name}
                            ref={(el) => { if (el) itemRefs.current.set(globalIndex, el); }}
                            onClick={() => handleSelectCommand(cmd.name)}
                            className={clsx(
                              'w-full px-3 py-2 flex items-start gap-3 text-left transition-colors',
                              globalIndex === selectedCommandIndex
                                ? 'bg-primary text-white'
                                : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                            )}
                          >
                            <span className="material-symbols-outlined text-base mt-0.5 opacity-60">magic_button</span>
                            <div className="min-w-0 flex-1">
                              <p className="font-medium text-sm">{cmd.name}</p>
                              <p
                                className={clsx(
                                  'text-xs truncate',
                                  globalIndex === selectedCommandIndex ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                                )}
                              >
                                {cmd.description}
                              </p>
                            </div>
                          </button>
                        );
                      })}
                    </>
                  )}
                </div>
                {/* F3 fix: Footer with keyboard hints + close button — always visible */}
                <div className="px-3 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50 flex items-center justify-between shrink-0">
                  <span className="text-xs text-[var(--color-text-muted)]">
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">↑↓</kbd> navigate
                    <span className="mx-2">·</span>
                    <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Tab</kbd> select
                  </span>
                  <button
                    onClick={() => { setShowCommandSuggestions(false); textareaRef.current?.focus(); }}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors p-0.5 rounded hover:bg-[var(--color-hover)]"
                    title="Close (Esc)"
                  >
                    <span className="material-symbols-outlined text-sm">close</span>
                  </button>
                </div>
              </div>
            )}

            {/* Text Input — always enabled during streaming so users can queue follow-ups.
                Only disabled when backend is disconnected. */}
            <textarea
              ref={textareaRef}
              data-testid="chat-input"
              data-chat-input
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={
                disabled
                  ? t('chat.disconnectedPlaceholder', 'Backend offline...')
                  : isLikelyStalled
                    ? 'Session may be stalled \u2014 send a message to recover'
                    : isStreaming
                      ? 'Type to queue a follow-up...'
                      : 'Ask Swarm anything...'
              }
              rows={2}
              disabled={disabled}
              className={clsx(
                'flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-dim)] resize-none focus:outline-none py-2',
                disabled && 'opacity-50 cursor-not-allowed'
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

            {/* Stop button — same size as send for consistent hit target,
                muted color for visual hierarchy. Always rendered to avoid layout
                shift; invisible when not streaming. */}
            <button
              onClick={onStop}
              className={clsx(
                'w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors',
                isStreaming
                  ? 'text-[var(--color-text-muted)] hover:text-red-500 hover:bg-red-500/10'
                  : 'invisible'
              )}
              title="Stop generation (Esc)"
              tabIndex={isStreaming ? 0 : -1}
              aria-hidden={!isStreaming}
            >
              <span className="material-symbols-outlined text-[16px]">stop</span>
            </button>

            {/* Send button — always primary, queues during streaming */}
            <button
              onClick={handleSend}
              disabled={!canSend || disabled}
              className={clsx(
                'w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors shadow-[0_1px_2px_rgba(0,0,0,0.2)]',
                'bg-gradient-to-b from-[#3d7ef0] to-[#2b6cee] hover:from-[#5a94f5] hover:to-[#3d7ef0]',
                (!canSend || disabled) && 'opacity-50 cursor-not-allowed'
              )}
              title={
                isStreaming
                  ? 'Queue message'
                  : attachments.length > 0
                      ? 'Send with attachments'
                      : 'Send message'
              }
            >
              <span className="material-symbols-outlined text-white text-[16px]">arrow_upward</span>
            </button>
          </div>

          {/* Bottom Row - attachment left, context/TSCC right */}
          <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--color-border)]/50">
            {/* Left: Attachment + Voice buttons */}
            <div className="flex items-center gap-2">
              <FileAttachmentButton onFilesSelected={onAddFiles} disabled={isProcessingFiles || disabled} canAddMore={canAddMore} />
              {/* Voice mode toggle: conversation mode (if handler provided) or fallback to single-shot mic */}
              {voiceSupported && onVoiceConversationToggle && (
                <button
                  onClick={onVoiceConversationToggle}
                  disabled={disabled}
                  aria-pressed={voiceConversationState !== 'off'}
                  aria-label={voiceConversationState !== 'off' ? 'Exit voice conversation' : 'Start voice conversation'}
                  className={clsx(
                    'w-6 h-6 rounded-md flex items-center justify-center transition-all',
                    voiceConversationState !== 'off'
                      ? 'text-green-500 bg-green-500/10'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]',
                    disabled && 'opacity-50 cursor-not-allowed',
                  )}
                  title={voiceConversationState !== 'off' ? 'Exit voice conversation' : 'Start voice conversation'}
                >
                  <span className="material-symbols-outlined text-[16px]">
                    {voiceConversationState !== 'off' ? 'hearing' : 'headset_mic'}
                  </span>
                </button>
              )}
              {/* Fallback: single-shot mic when no conversation handler */}
              {voiceSupported && !onVoiceConversationToggle && (
                <button
                  onClick={toggleRecording}
                  disabled={voiceState === 'processing' || disabled}
                  aria-pressed={voiceState === 'recording'}
                  aria-label={voiceState === 'recording' ? 'Stop recording' : 'Start voice input'}
                  className={clsx(
                    'w-6 h-6 rounded-md flex items-center justify-center transition-all',
                    voiceState === 'recording'
                      ? 'text-red-500 bg-red-500/10 animate-pulse'
                      : voiceState === 'processing'
                        ? 'text-[var(--color-text-muted)] opacity-60 cursor-wait'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg-hover)]',
                    disabled && 'opacity-50 cursor-not-allowed',
                  )}
                  title={
                    voiceState === 'recording'
                      ? 'Stop recording'
                      : voiceState === 'processing'
                        ? 'Transcribing...'
                        : 'Start voice input'
                  }
                >
                  <span className="material-symbols-outlined text-[16px]">
                    {voiceState === 'processing' ? 'hourglass_top' : 'mic'}
                  </span>
                </button>
              )}
              {voiceError && (
                <span className="text-xs text-red-400 max-w-[200px] truncate" title={voiceError}>
                  {voiceError}
                </span>
              )}
              {/* Voice conversation indicator */}
              {voiceConversationState !== 'off' && (
                <VoiceConversationIndicator
                  state={voiceConversationState}
                  onInterrupt={onVoiceConversationInterrupt}
                />
              )}
              {lineCount > 5 && (
                <span className="text-xs text-[var(--color-text-muted)]">
                  {lineCount} lines
                </span>
              )}
            </div>
            {/* Right: Context ring + TSCC */}
            <div className="flex items-center gap-2">
              <ContextUsageRing pct={contextPct ?? null} size={20} showLabel />
              <TSCCPopoverButton sessionId={sessionId ?? null} metadata={promptMetadata ?? null} />
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
