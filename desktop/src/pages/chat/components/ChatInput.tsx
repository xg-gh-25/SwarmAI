import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { UnifiedAttachment, SystemPromptMetadata } from '../../../types';
import { FileAttachmentButton, FileAttachmentPreview, ScreenshotButton } from '../../../components/chat';
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
import { INJECT_CHAT_INPUT, type InjectChatInputDetail } from '../injectChatInput';

/** The (value, width, expanded) triple that the textarea's wrapped height is a
 *  pure function of. applyHeight skips the forced-reflow measure when the current
 *  triple equals the last measured one. Exported so the reflow-skip guard is tested
 *  as a pure function (its width-key correctness — Gate-1 FLAW4 — is otherwise
 *  hard to isolate in a component test without accidentally passing on a value-only
 *  guard). run_1cb87e1a. */
export interface HeightMeasureSig {
  value: string;
  width: number;
  expanded: boolean;
}

/** True iff a height re-measure is unnecessary: same value AND same width AND same
 *  expanded mode. WIDTH is load-bearing — a rewrap from a width change (Canvas
 *  open/close, drag-resize) with the value unchanged MUST re-measure, so a
 *  value-only comparison would wrongly return true and freeze the height. */
export function heightMeasureUnchanged(
  prev: HeightMeasureSig | null,
  next: HeightMeasureSig,
): boolean {
  return (
    prev !== null &&
    prev.value === next.value &&
    prev.width === next.width &&
    prev.expanded === next.expanded
  );
}

/** What to store as the last-measured signature after a measure. Returns the sig
 *  ONLY when it was measured at a real (>0) width; at width 0 (a keep-mounted
 *  BACKGROUND tab's textarea) returns null so the NEXT measure — once the element is
 *  actually laid out — is never skipped by a stale 0-width signature match (Gate-2
 *  correctness MED, run_1cb87e1a). */
export function cacheableMeasureSig(sig: HeightMeasureSig): HeightMeasureSig | null {
  return sig.width > 0 ? sig : null;
}

/** The collapsed (empty-input) minimum height in px for `rows` rows of text.
 *
 *  ROOT FIX (run_17d708f4) for the "3-row default height disappeared" regression:
 *  under CSS `field-sizing: content` the browser sizes the textarea to its CONTENT
 *  height and the `rows={DEFAULT_ROWS}` HTML attribute is NOT honored as a minimum, so
 *  an empty input collapsed to ~1 row on WebKit 26+ (where field-sizing IS supported).
 *  The JS-autogrow fallback path still gets its minimum from `rows`, so this minHeight
 *  is applied ONLY in the field-sizing style branch.
 *
 *  The textarea is border-box (Tailwind preflight) and carries `py-2` (vertical
 *  padding), so a `min-height` covering `rows` CONTENT rows must ADD that vertical
 *  padding — otherwise it would clamp to `rows*lineHeight` of BORDER-box, showing
 *  fewer than `rows` content rows. Pure (line-height + padding in, px out) so the
 *  border-box math is unit-tested deterministically. */
export function computeCollapsedMinHeight(
  lineHeight: number,
  paddingTop: number,
  paddingBottom: number,
  rows: number = DEFAULT_ROWS,
): number {
  return Math.round(rows * lineHeight) + paddingTop + paddingBottom;
}

/** True iff the WebKit/browser natively auto-sizes a textarea via CSS
 *  `field-sizing: content` — in which case the JS autogrow (a per-keystroke
 *  `height='auto'` write → `scrollHeight` read → forced synchronous document
 *  reflow) is unnecessary and MUST be disabled so the two mechanisms don't fight.
 *
 *  ROOT FIX (run_26172836) for the recurring "Canvas 开着时 chat input 输入卡死": the
 *  chat textarea is a flex sibling of the Canvas in one shared row (ChatPage.tsx),
 *  so the per-keystroke forced reflow flushes that row and re-lays-out the large
 *  un-virtualized Canvas surface. `field-sizing:content` sizes the control natively
 *  with ZERO scriptable measurement — eliminating the reflow at its source rather
 *  than trying (as 3 prior fixes did, unsuccessfully) to make the Canvas cheaper to
 *  re-lay-out. Supported in WebKit 26.0+ (macOS 26 Safari engine) / Chromium 123+.
 *  On older WebKit (Tauri uses the system WKWebView), this returns false and the
 *  JS autogrow fallback runs unchanged. Guarded for non-DOM/test envs where
 *  `CSS`/`CSS.supports` may be absent (jsdom → false → the JS path is exercised by
 *  the existing autogrow/reflow-skip suites). */
export function supportsFieldSizing(): boolean {
  try {
    return typeof CSS !== 'undefined' && typeof CSS.supports === 'function'
      && CSS.supports('field-sizing', 'content');
  } catch {
    return false;
  }
}

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
  /** Callback to trigger context refresh (same-tab restart with resume) */
  onRefreshContext?: () => void;
  /** Available skills for slash command picker */
  skills?: Skill[];
  /** Voice conversation mode state (off = normal text mode) */
  voiceConversationState?: VoiceConversationState;
  /** Toggle voice conversation mode on/off */
  onVoiceConversationToggle?: () => void;
  /** Interrupt TTS playback and return to listening */
  onVoiceConversationInterrupt?: () => void;
  /** Called when one-tap screenshot capture fails (e.g. permission not granted) — surfaces a toast. */
  onCaptureError?: (message: string) => void;
}

const MAX_ROWS = 20;
/** Default (empty-input) visible rows. The single source of truth for both the
 *  `rows={DEFAULT_ROWS}` attribute (JS-autogrow path minimum) and the field-sizing
 *  branch's computed `minHeight` (run_17d708f4). */
const DEFAULT_ROWS = 3;
/** Fallback collapsed minHeight (px) before the mount effect measures the real
 *  computed line-height + padding: DEFAULT_ROWS*20 + py-2 (8+8). Safe because Tailwind
 *  preflight loads before React paints; the mount effect overwrites it with the
 *  real computed value. */
const DEFAULT_MIN_HEIGHT_PX = DEFAULT_ROWS * 20 + 16;

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
  onRefreshContext,
  skills = [],
  voiceConversationState = 'off',
  onVoiceConversationToggle,
  onVoiceConversationInterrupt,
  onCaptureError,
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
  // rAF handle for the deferred height recalc — batches/coalesces the layout
  // read-write so keystrokes never force a synchronous reflow (input-lag fix).
  const heightFrameRef = useRef<number | null>(null);
  // Signature of the last height measure: the (value, width, expanded) triple that
  // wrapped height is a pure function of. applyHeight early-returns (skips the
  // forced `height='auto'`→read-`scrollHeight` reflow) when this triple is unchanged.
  // Width is part of the key so a width-driven rewrap (Canvas open/close, drag-resize)
  // still re-measures even when the value is unchanged (run_1cb87e1a Gate-1 FLAW4).
  // Reset to null on send-reset (height cleared to '') so the next measure recomputes.
  const lastMeasureRef = useRef<{ value: string; width: number; expanded: boolean } | null>(null);
  // Native CSS auto-sizing available? If so, the JS autogrow path (which forces a
  // per-keystroke synchronous document reflow — the ROOT of the Canvas-open input
  // lag) is disabled entirely; CSS `field-sizing:content` + `max-height` do the
  // sizing. Computed ONCE (support is process-stable). run_26172836.
  const fieldSizingRef = useRef<boolean>(supportsFieldSizing());
  // Reactive mirror of maxHeightRef for the field-sizing CSS `max-height` (which is
  // read at RENDER time in JSX — a ref write wouldn't refresh it). The JS autogrow
  // path reads maxHeightRef.current live so it doesn't need this; only the CSS path
  // does. Seeds at 400 (=MAX_ROWS default) and updates to the real computed value on
  // mount. (REVIEW F1, run_26172836.)
  const [maxHeightPx, setMaxHeightPx] = useState(400);
  // Reactive collapsed minHeight for the field-sizing CSS branch (run_17d708f4). Under
  // `field-sizing:content` the rows={DEFAULT_ROWS} attribute is NOT honored as a min, so
  // an empty input collapses to 1 row without this. Seeds at the DEFAULT_ROWS fallback
  // and updates to the real computed (line-height + padding) value at mount. The
  // JS-autogrow path gets its min from rows={DEFAULT_ROWS}, so this is applied ONLY in
  // the field-sizing style branch.
  const [minHeightPx, setMinHeightPx] = useState(DEFAULT_MIN_HEIGHT_PX);

  // Compute max/min height once from actual computed line-height + padding at mount
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const cs = getComputedStyle(el);
    const lineHeight = parseFloat(cs.lineHeight) || 20;
    const px = MAX_ROWS * lineHeight;
    maxHeightRef.current = px;
    setMaxHeightPx(px);
    // border-box + py-2: the DEFAULT_ROWS-row minimum must include vertical padding.
    // Fallback to 8 (py-2 = 0.5rem) when unreadable, mirroring the lineHeight `|| 20`
    // fallback — so the computed value matches DEFAULT_MIN_HEIGHT_PX's assumption and
    // never diverges DOWNWARD from the seed (adversarial MED, run_17d708f4).
    const padTop = parseFloat(cs.paddingTop) || 8;
    const padBottom = parseFloat(cs.paddingBottom) || 8;
    setMinHeightPx(computeCollapsedMinHeight(lineHeight, padTop, padBottom));
  }, []);

  // L2: Listen for auto-diff injection from FileEditorPanel save
  // and L3 review feedback. Updates both the visible textarea AND the
  // per-tab draft storage so the text survives tab switches.
  // Supports `autoSend: true` to immediately dispatch the message to the agent.
  // Auto-send uses a two-phase approach: set pendingAutoSend flag, then fire
  // onSend in a separate effect AFTER React has flushed the input state update.
  const [pendingAutoSend, setPendingAutoSend] = useState(false);

  useEffect(() => {
    const handler = (e: Event) => {
      const { text, focus, autoSend } = (e as CustomEvent<InjectChatInputDetail>).detail ?? { text: '' };
      if (text) {
        onInputChange(text);
        // Sync to per-tab draft storage so the injected text survives tab switches
        const tabId = activeTabIdRef?.current;
        if (tabId && inputValueMapRef) {
          inputValueMapRef.current.set(tabId, text);
        }
        if (autoSend) {
          setPendingAutoSend(true);
        } else if (focus) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
      }
    };
    window.addEventListener(INJECT_CHAT_INPUT, handler);
    return () => window.removeEventListener(INJECT_CHAT_INPUT, handler);
  }, [onInputChange, activeTabIdRef, inputValueMapRef]);

  // Phase 2: fire onSend after React has committed the input state update
  useEffect(() => {
    if (pendingAutoSend) {
      setPendingAutoSend(false);
      onSend();
    }
  }, [pendingAutoSend, onSend]);

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

  // Cleanup transition timer, inline style, and any pending height frame on unmount
  useEffect(() => {
    const el = textareaRef.current;
    return () => {
      if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
      if (heightFrameRef.current !== null) {
        cancelAnimationFrame(heightFrameRef.current);
        heightFrameRef.current = null;
      }
      if (el) el.style.transition = '';
    };
  }, []);

  // Synchronous core: the actual DOM read-write. Reads scrollHeight ONCE and
  // reuses it (the second read the old code did — after writing style.height —
  // forced a needless second reflow).
  const applyHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    // Line count drives the expand-toggle button (`lineCount > 3`) — it is a pure
    // string op (no reflow), so compute it on BOTH paths (before the field-sizing
    // early-return) or the toggle would never appear under native sizing.
    const lines = el.value.split('\n').length;
    setLineCount(prev => prev !== lines ? lines : prev);
    // ROOT FIX (run_26172836): when the browser auto-sizes via `field-sizing:content`
    // (see the textarea's inline style), do NOT run the JS measure — the whole point
    // is to eliminate the `height='auto'` write → `scrollHeight` read that forces a
    // synchronous document reflow every keystroke (which, with Canvas open, re-lays-out
    // the large Canvas surface that shares the flex row). CSS `max-height` + `overflow-y`
    // handle the clamp/scroll natively. Falls through to the JS autogrow on older WebKit.
    if (fieldSizingRef.current) return;
    const maxHeight = isExpanded ? window.innerHeight * 0.6 : maxHeightRef.current;
    // Skip the forced-reflow measure (`height='auto'` write → `scrollHeight` read)
    // when NOTHING that can change the wrapped height changed since the last apply:
    // same value AND same client width AND same expanded mode. Wrapped height is a
    // pure function of (text, width, maxHeight-mode) — if all three are unchanged the
    // measure would recompute the identical height, so the whole read-after-write
    // reflow is pure waste. Guarding on WIDTH (not value alone) is REQUIRED: a
    // width-driven rewrap (Canvas open/close, drag-resize, window resize) changes the
    // height with the value unchanged, so a value-only skip would freeze the textarea
    // at a stale height (run_1cb87e1a Gate-1 FLAW4). This removes redundant measures
    // on non-value re-fires; the contain:layout on the Canvas column bounds the cost
    // of the measures that DO run. (During active typing the value changes each key,
    // so this never wrongly skips a real growth.)
    const width = el.clientWidth;
    const sig: HeightMeasureSig = { value: el.value, width, expanded: isExpanded };
    if (heightMeasureUnchanged(lastMeasureRef.current, sig)) {
      return;
    }
    el.style.height = 'auto';
    const scrollH = el.scrollHeight;
    el.style.height = `${Math.min(scrollH, maxHeight)}px`;
    el.style.overflowY = scrollH > maxHeight ? 'auto' : 'hidden';
    // Do NOT cache a signature measured at width 0 (keep-mounted background tab) —
    // else a later width-only recovery with the value unchanged would match-and-skip,
    // freezing the height (Gate-2 correctness MED, run_1cb87e1a). See cacheableMeasureSig.
    lastMeasureRef.current = cacheableMeasureSig(sig);
  }, [isExpanded]);

  // Deferred variant: coalesce into a single animation frame so a burst of
  // keystrokes triggers at most one layout op, and never on the synchronous
  // input/render path (which is what caused per-keystroke reflow lag).
  const scheduleAdjustHeight = useCallback(() => {
    if (heightFrameRef.current !== null) {
      cancelAnimationFrame(heightFrameRef.current);
    }
    heightFrameRef.current = requestAnimationFrame(() => {
      heightFrameRef.current = null;
      applyHeight();
    });
  }, [applyHeight]);

  // Keystroke path: defer height recalc to rAF (handles programmatic clears
  // after send too). This is the hot path — never do a synchronous reflow here.
  useEffect(() => {
    scheduleAdjustHeight();
  }, [inputValue, scheduleAdjustHeight]);

  // Expand/collapse path: run height recalc SYNCHRONOUSLY. toggleExpanded's
  // cursor-restore rAF reads clientHeight/scrollTop and must see the settled
  // height — deferring this by a frame would race that measurement.
  useEffect(() => {
    applyHeight();
  }, [isExpanded, applyHeight]);

  // Re-clamp textarea height on window resize when expanded (60vh is viewport-relative)
  useEffect(() => {
    if (!isExpanded) return;
    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const handleResize = () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => scheduleAdjustHeight(), 100);
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (resizeTimer) clearTimeout(resizeTimer);
    };
  }, [isExpanded, scheduleAdjustHeight]);

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
    // Under field-sizing (native auto-sizing) NO inline height/overflow is ever
    // written, so there is nothing to reset — and the CSS `overflow-y:auto` must not
    // be clobbered to 'hidden' (would momentarily clip a >maxHeight draft until React
    // re-commits). Only the JS-autogrow path needs the imperative reset. (REVIEW F2/F3.)
    if (!fieldSizingRef.current) {
      const el = textareaRef.current;
      if (el) {
        el.style.height = '';       // clear inline style, rows={DEFAULT_ROWS} reasserts minimum
        el.style.overflowY = 'hidden';
      }
      // Invalidate the measure cache: height was just reset to '' (native rows min),
      // so the next applyHeight MUST re-measure rather than skip on a stale signature.
      lastMeasureRef.current = null;
    }
  }, [onSend, isExpanded, onExpandedChange, applyTransition]);

  const hasAttachments = attachments.some((a) => !a.error && !a.isLoading);
  const canSend = (inputValue.trim() || hasAttachments) && selectedAgentId;

  return (
    <div className="pl-2 pr-4 pb-4 pt-2 flex-shrink-0">
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
              rows={DEFAULT_ROWS}
              disabled={disabled}
              // Native auto-sizing (run_26172836): when `field-sizing:content` is
              // supported, CSS grows the textarea with content — no JS scrollHeight
              // read, so no per-keystroke forced document reflow (the Canvas-open lag
              // root cause). max-height clamps growth (60vh expanded / MAX_ROWS px
              // collapsed — mirrors the JS maxHeight, via the reactive maxHeightPx so it
              // refreshes after the mount line-height measure) and overflow-y:auto
              // scrolls past it. min-height enforces the DEFAULT_ROWS-row default that
              // field-sizing would otherwise ignore (rows={} is not a min under
              // field-sizing — run_17d708f4). Set only when supported, so on older WebKit
              // the property is absent and the JS autogrow (which writes inline height)
              // stays in charge.
              style={fieldSizingRef.current ? {
                // `fieldSizing` is not yet in React's CSSProperties typings — set via
                // an index cast so tsc doesn't reject the (valid, supported) property.
                ['fieldSizing' as string]: 'content',
                // DEFAULT_ROWS-row floor: field-sizing does NOT honor rows={} as a min,
                // so without this an empty input collapses to 1 row (run_17d708f4).
                minHeight: `${minHeightPx}px`,
                maxHeight: isExpanded ? '60vh' : `${maxHeightPx}px`,
                overflowY: 'auto',
              } as React.CSSProperties : undefined}
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
          <div className="flex items-center justify-between mt-1">
            {/* Left: Attachment + Voice buttons */}
            <div className="flex items-center gap-2">
              <FileAttachmentButton onFilesSelected={onAddFiles} disabled={isProcessingFiles || disabled} canAddMore={canAddMore} />
              <ScreenshotButton
                onCaptured={onAddFiles}
                onError={(msg) => onCaptureError?.(msg)}
                disabled={isProcessingFiles || disabled}
                canAddMore={canAddMore}
              />
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
            {/* Right: Context ring + Refresh + TSCC */}
            <div className="flex items-center gap-2">
              <ContextUsageRing pct={contextPct ?? null} size={18} showLabel />
              {onRefreshContext && (
                <button
                  type="button"
                  onClick={onRefreshContext}
                  disabled={isStreaming || disabled}
                  className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  title="Refresh context — restart AI with conversation summary"
                  aria-label="Refresh context"
                >
                  <span className="material-symbols-outlined text-[18px]">
                    refresh
                  </span>
                </button>
              )}
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
