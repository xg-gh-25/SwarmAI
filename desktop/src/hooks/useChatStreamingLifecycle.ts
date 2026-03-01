/**
 * Custom hook encapsulating the chat streaming lifecycle state machine.
 *
 * Extracted from ``ChatPage.tsx`` (Phase 0 refactor) to isolate streaming
 * concerns into a testable, self-contained unit. This hook owns:
 *
 * - **State**: ``messages``, ``sessionId``, ``pendingQuestion``, ``isStreaming``,
 *   ``_pendingStream``, ``streamingSessions``
 * - **Refs**: ``abortRef``, ``messagesEndRef``, ``sessionIdRef``, ``messagesRef``,
 *   ``pendingQuestionRef``, ``tabStateRef``, ``activeTabIdRef``
 * - **Factories**: ``createStreamHandler``, ``createCompleteHandler``,
 *   ``createErrorHandler``
 * - **Pure function**: ``deriveStreamingActivity`` (exported standalone for
 *   testability)
 * - **Pure function**: ``updateMessages`` (exported for testability)
 * - **Derived**: ``isStreaming`` derivation, ``streamingActivity`` memo
 * - **Tab management**: ``saveTabState``, ``restoreTabState``, ``initTabState``,
 *   ``cleanupTabState`` — per-tab state isolation (Fix 6)
 *
 * ``ChatPage`` consumes this hook and focuses on rendering + user interactions.
 *
 * **Fix 1**: Stream generation counter prevents stale complete handlers.
 * **Fix 6**: Per-tab state map isolates messages, sessionId, pendingQuestion,
 *   abortController, and pendingStream across tabs. The ``tabStateRef`` map is
 *   the authoritative source of truth; ``useState`` mirrors the active tab.
 *   Background streaming updates the per-tab map but NOT foreground useState.
 *
 * @module useChatStreamingLifecycle
 */

import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import type {
  Message,
  ContentBlock,
  StreamEvent,
} from '../types';
import type { PendingQuestion } from '../pages/chat/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Tab lifecycle status for header indicators.
 *
 * State machine transitions:
 *   idle → streaming → {waiting_input | permission_needed | error | complete_unread} → idle
 */
export type TabStatus =
  | 'idle'
  | 'streaming'
  | 'waiting_input'
  | 'permission_needed'
  | 'error'
  | 'complete_unread';

/**
 * Per-tab state stored in the backing ``tabStateRef`` map.
 *
 * Each open chat tab has its own isolated copy of messages, sessionId,
 * pendingQuestion, abortController, and streaming flags. The active
 * (foreground) tab's state is mirrored into React ``useState`` for rendering;
 * background tabs live only in this map until switched to.
 */
export interface TabState {
  messages: Message[];
  sessionId: string | undefined;
  pendingQuestion: PendingQuestion | null;
  abortController: AbortController | null;
  pendingStream: boolean;
  streamGen: number;
  status: TabStatus;
}

/** Maximum number of concurrent open tabs. Bounds the per-tab state map. */
export const MAX_OPEN_TABS = 6;

/**
 * Threshold in milliseconds before the elapsed time counter is shown.
 * Below this, the spinner just shows "Thinking…" with no elapsed time.
 */
export const ELAPSED_DISPLAY_THRESHOLD_MS = 10000;

/**
 * Format an elapsed duration in seconds into a human-readable string.
 *
 * - Under 60 s → ``"15s"``
 * - 60 s and above → ``"1m 5s"``, ``"2m 0s"``
 *
 * Exported for testability.
 */
export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}m ${secs}s`;
}

/**
 * Minimum display duration (ms) for each activity label before transitioning.
 * Prevents flickering during rapid tool calls (< 2 s intervals).
 */
export const MIN_ACTIVITY_DISPLAY_MS = 1500;

/** Maximum character length for the operational context string. */
const MAX_CONTEXT_LENGTH = 60;

/** Shape returned by ``deriveStreamingActivity``. */
export interface StreamingActivity {
  hasContent: boolean;
  toolName: string | null;
  /** Brief operational context extracted from the last tool_use input. */
  toolContext: string | null;
  /** Count of all tool_use blocks in the last assistant message. */
  toolCount: number;
}

// ---------------------------------------------------------------------------
// Pure helpers — exported for unit / property-based test access
// ---------------------------------------------------------------------------

/**
 * Patterns that indicate sensitive content in a command string.
 * Anything after these tokens (up to the next flag or end-of-string) is stripped.
 */
const SENSITIVE_FLAG_RE = /--(password|token|key)\b/i;

/**
 * Sanitize a command string by stripping potential secrets.
 *
 * - Strips everything after ``--password``, ``--token``, ``--key``
 * - Strips environment variable ``KEY=value`` assignments
 * - Returns ``[command]`` placeholder if the entire command is sensitive
 * - Truncates to ``MAX_CONTEXT_LENGTH`` characters
 *
 * Exported for testability.
 */
export function sanitizeCommand(command: string): string {
  let sanitized = command;

  // Strip everything after sensitive flags
  const flagMatch = SENSITIVE_FLAG_RE.exec(sanitized);
  if (flagMatch) {
    sanitized = sanitized.slice(0, flagMatch.index).trim();
  }

  // Strip env var assignments (UPPER_CASE=value only — avoids stripping lowercase path=/usr/bin)
  sanitized = sanitized.replace(/(?:^|\s)[A-Z_][A-Z0-9_]*=\S*/g, '').trim();

  if (!sanitized) return '[command]';

  return sanitized.slice(0, MAX_CONTEXT_LENGTH);
}

/**
 * Extract operational context from a tool_use block's input object.
 *
 * Priority order:
 *   1. ``input.command`` → sanitized first 60 chars
 *   2. ``input.path`` or ``input.file_path`` → file path directly
 *   3. ``input.query``, ``input.search``, or ``input.pattern`` → first 60 chars
 *   4. Otherwise → ``null``
 *
 * Exported for testability.
 */
export function extractToolContext(input: Record<string, unknown> | null | undefined): string | null {
  if (!input || typeof input !== 'object') return null;

  // 1. Command
  if (typeof input.command === 'string' && input.command.trim()) {
    return sanitizeCommand(input.command.trim());
  }

  // 2. File path
  if (typeof input.path === 'string' && input.path.trim()) {
    return input.path.trim().slice(0, MAX_CONTEXT_LENGTH);
  }
  if (typeof input.file_path === 'string' && input.file_path.trim()) {
    return input.file_path.trim().slice(0, MAX_CONTEXT_LENGTH);
  }

  // 3. Query / search / pattern
  if (typeof input.query === 'string' && input.query.trim()) {
    return input.query.trim().slice(0, MAX_CONTEXT_LENGTH);
  }
  if (typeof input.search === 'string' && input.search.trim()) {
    return input.search.trim().slice(0, MAX_CONTEXT_LENGTH);
  }
  if (typeof input.pattern === 'string' && input.pattern.trim()) {
    return input.pattern.trim().slice(0, MAX_CONTEXT_LENGTH);
  }

  return null;
}

/**
 * Find the last element in an array matching a predicate.
 * Avoids ``[...arr].reverse().find()`` which allocates a copy on every call.
 */
function findLast<T>(arr: readonly T[], predicate: (item: T) => boolean): T | undefined {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (predicate(arr[i])) return arr[i];
  }
  return undefined;
}

/**
 * Derive the current streaming activity state from messages.
 *
 * Returns ``null`` when not streaming or no content blocks exist yet
 * (preserving the original "Thinking…" behavior). Otherwise returns the
 * activity state with an optional tool name, operational context, and
 * cumulative tool count for the most recent assistant message.
 */
export function deriveStreamingActivity(
  isStreaming: boolean,
  messages: Message[],
): StreamingActivity | null {
  if (!isStreaming) return null;

  const lastAssistant = findLast(messages, (m: Message) => m.role === 'assistant');
  if (!lastAssistant || lastAssistant.content.length === 0) return null;

  const hasContent = lastAssistant.content.some(
    (b: ContentBlock) =>
      b.type === 'text' || b.type === 'tool_use' || b.type === 'tool_result',
  );
  if (!hasContent) return null;

  // Count all tool_use blocks in the last assistant message
  const toolUseBlocks = lastAssistant.content.filter(
    (b) => b.type === 'tool_use',
  );
  const toolCount = toolUseBlocks.length;

  // Find the last tool_use block for name and context
  const lastToolUse = findLast(lastAssistant.content, (b) => b.type === 'tool_use');
  const toolName =
    lastToolUse && 'name' in lastToolUse
      ? (lastToolUse as { name?: string }).name?.trim() || null
      : null;

  // Extract operational context from the last tool_use's input
  const toolInput =
    lastToolUse && 'input' in lastToolUse
      ? (lastToolUse as { input?: Record<string, unknown> }).input
      : null;
  const toolContext = extractToolContext(toolInput ?? null);

  return { hasContent, toolName, toolContext, toolCount };
}

// ---------------------------------------------------------------------------
// Pure function — updateMessages (exported for testability)
// ---------------------------------------------------------------------------

/**
 * Compute updated messages array after an ``assistant`` stream event.
 *
 * Called once per event — the result is stored in both the per-tab map and
 * (if active) the ``useState``. Extracted as a pure function so
 * ``createStreamHandler`` doesn't duplicate the merge logic.
 */
/**
 * Derives a unique string key for a content block, used for Set-based dedup.
 *
 * Key format by block type:
 * - `tool_use:<id>`
 * - `tool_result:<toolUseId>`
 * - `text:<text>`
 * - Fallback: `<type>:JSON` or `<type>:String`
 */
export function blockKey(block: ContentBlock): string {
  switch (block.type) {
    case 'tool_use':
      return `tool_use:${block.id}`;
    case 'tool_result':
      return `tool_result:${block.toolUseId}`;
    case 'text':
      return `text:${block.text}`;
    default: {
      // Safe fallback — avoid JSON.stringify on potentially circular objects
      try {
        return `${block.type}:${JSON.stringify(block)}`;
      } catch {
        return `${block.type}:${String(block)}`;
      }
    }
  }
}

/**
 * Merges new content blocks into the matching assistant message using
 * Set-based O(n+m) deduplication instead of O(n×m) nested iteration.
 *
 * Returns the same message reference when no new content is added
 * (referential stability for React memoization).
 */
export function updateMessages(
  currentMessages: Message[],
  assistantMessageId: string,
  newContent: ContentBlock[],
  model?: string,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;
    const existingKeys = new Set(msg.content.map(blockKey));
    const filteredContent = newContent.filter((b) => !existingKeys.has(blockKey(b)));
    if (filteredContent.length === 0) return msg; // No new content — return same reference
    return {
      ...msg,
      content: [...msg.content, ...filteredContent],
      ...(model ? { model } : {}),
    };
  });
}

// ---------------------------------------------------------------------------
// Fix 5: sessionStorage persistence helpers (exported for testability)
// ---------------------------------------------------------------------------

/** Storage key prefix for pending chat state. */
export const STORAGE_KEY_PREFIX = 'swarm_chat_pending_';

/** Maximum number of stale entries to clean per mount cycle. */
const MAX_STALE_CLEANUP = 5;

/** Delay (ms) before stale entry cleanup runs after mount. */
const STALE_CLEANUP_DELAY_MS = 2000;

/** Tool count threshold above which tool_result content is truncated before serializing. */
const LARGE_SESSION_TOOL_THRESHOLD = 80;

/** Max chars for truncated tool_result content blocks. */
const TRUNCATED_CONTENT_LENGTH = 200;

/** Current schema version for PersistedPendingState. Bump on breaking changes. */
export const PERSISTED_STATE_VERSION = 1;

/** Shape of the persisted pending state in sessionStorage. */
export interface PersistedPendingState {
  version: number;
  messages: Message[];
  pendingQuestion: PendingQuestion;
  sessionId: string;
}

/**
 * Check whether sessionStorage is available in the current environment.
 *
 * Guards against SSR, private browsing restrictions, and Tauri webview
 * edge cases where ``sessionStorage`` may be undefined.
 */
export function isSessionStorageAvailable(): boolean {
  return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
}

/**
 * Truncate ``tool_result`` content blocks for large sessions before
 * serializing to sessionStorage. Only applies when the message array
 * contains 80+ tool_use blocks (indicating a large session).
 *
 * Returns a shallow copy with truncated tool_result text — the original
 * messages array is NOT mutated.
 */
export function prepareMessagesForStorage(messages: Message[]): Message[] {
  // Count total tool_use blocks across all messages
  let toolUseCount = 0;
  for (const msg of messages) {
    for (const block of msg.content) {
      if (block.type === 'tool_use') toolUseCount++;
    }
  }

  if (toolUseCount < LARGE_SESSION_TOOL_THRESHOLD) return messages;

  // Truncate tool_result content blocks
  return messages.map((msg) => ({
    ...msg,
    content: msg.content.map((block) => {
      if (block.type !== 'tool_result') return block;
      // tool_result blocks may have a nested content array or a text field
      const raw = block as unknown as Record<string, unknown>;
      if (typeof raw.content === 'string' && raw.content.length > TRUNCATED_CONTENT_LENGTH) {
        return { ...block, content: (raw.content as string).slice(0, TRUNCATED_CONTENT_LENGTH) + '…' } as typeof block;
      }
      return block;
    }),
  }));
}

/**
 * Persist pending chat state to sessionStorage.
 *
 * Called when ``ask_user_question`` arrives. Writes from the per-tab map
 * (authoritative source) rather than useState. Gracefully degrades on
 * quota exceeded — logs a warning and continues.
 */
export function persistPendingState(
  sessionId: string,
  messages: Message[],
  pendingQuestion: PendingQuestion,
): void {
  if (!isSessionStorageAvailable()) return;

  const key = `${STORAGE_KEY_PREFIX}${sessionId}`;
  const payload: PersistedPendingState = {
    version: PERSISTED_STATE_VERSION,
    messages: prepareMessagesForStorage(messages),
    pendingQuestion,
    sessionId,
  };

  try {
    window.sessionStorage.setItem(key, JSON.stringify(payload));
  } catch (err) {
    // Quota exceeded or other storage error — graceful degradation
    console.warn('[useChatStreamingLifecycle] Failed to persist pending state:', err);
  }
}

/**
 * Restore pending chat state from sessionStorage for a given sessionId.
 *
 * Returns ``null`` if no entry exists, the entry is corrupted, or the
 * schema doesn't match. Discards invalid entries automatically.
 */
export function restorePendingState(sessionId: string): PersistedPendingState | null {
  if (!isSessionStorageAvailable()) return null;

  const key = `${STORAGE_KEY_PREFIX}${sessionId}`;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;

    const parsed = JSON.parse(raw);

    // Schema validation — must have messages array, pendingQuestion with toolUseId, and sessionId
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      !Array.isArray(parsed.messages) ||
      !parsed.pendingQuestion ||
      typeof parsed.pendingQuestion.toolUseId !== 'string' ||
      typeof parsed.sessionId !== 'string'
    ) {
      // Schema mismatch — discard
      window.sessionStorage.removeItem(key);
      return null;
    }

    // Version mismatch — discard stale entry
    if (parsed.version !== PERSISTED_STATE_VERSION) {
      window.sessionStorage.removeItem(key);
      return null;
    }

    return parsed as PersistedPendingState;
  } catch {
    // Corrupted JSON or other parse error — discard entry
    try {
      window.sessionStorage.removeItem(`${STORAGE_KEY_PREFIX}${sessionId}`);
    } catch { /* ignore cleanup failure */ }
    return null;
  }
}

/**
 * Remove the persisted pending state for a session.
 *
 * Called on ``result`` event or successful answer submission.
 */
export function removePendingState(sessionId: string): void {
  if (!isSessionStorageAvailable()) return;

  try {
    window.sessionStorage.removeItem(`${STORAGE_KEY_PREFIX}${sessionId}`);
  } catch {
    // Ignore removal failure
  }
}

/**
 * Detect whether an error represents a 404 Not Found response using
 * structured error properties only.
 *
 * Checks Axios-style ``err.response.status`` first, then a top-level
 * ``err.status`` property (custom API errors). Returns ``false`` for
 * errors without a structured numeric status — these are treated as
 * indeterminate and should not trigger cleanup.
 *
 * Exported for testability.
 */
export function isNotFoundError(err: unknown): boolean {
  // Axios-style error with response.status
  if (typeof err === 'object' && err !== null && 'response' in err) {
    const resp = (err as { response?: { status?: number } }).response;
    if (resp && typeof resp.status === 'number') {
      return resp.status === 404;
    }
  }
  // Error with a status property (e.g., custom API errors)
  if (typeof err === 'object' && err !== null && 'status' in err) {
    const status = (err as { status: unknown }).status;
    return typeof status === 'number' && status === 404;
  }
  // No structured status — treat as indeterminate, skip cleanup
  return false;
}

/**
 * Clean up stale ``swarm_chat_pending_*`` entries from sessionStorage.
 *
 * Scans at most ``MAX_STALE_CLEANUP`` entries per invocation. For each,
 * checks session status via the provided ``getSession`` callback. Removes
 * entries for completed or 404 sessions.
 *
 * Designed to be called via ``setTimeout`` on mount so it doesn't block
 * initial render.
 */
export async function cleanupStalePendingEntries(
  getSession: (sessionId: string) => Promise<{ id: string } | null>,
): Promise<void> {
  if (!isSessionStorageAvailable()) return;

  const keysToCheck: string[] = [];
  try {
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const key = window.sessionStorage.key(i);
      if (key?.startsWith(STORAGE_KEY_PREFIX)) {
        keysToCheck.push(key);
      }
      if (keysToCheck.length >= MAX_STALE_CLEANUP) break;
    }
  } catch {
    return; // sessionStorage iteration failed
  }

  for (const key of keysToCheck) {
    const sessionId = key.slice(STORAGE_KEY_PREFIX.length);
    if (!sessionId) continue;

    try {
      await getSession(sessionId);
      // Session exists and is not completed — keep the entry
    } catch (err: unknown) {
      // Only remove if the error is a structured 404 (session not found).
      // Network errors and errors without a status property are treated
      // as indeterminate — keep the entry for the next cleanup cycle.
      if (isNotFoundError(err)) {
        try {
          window.sessionStorage.removeItem(key);
        } catch { /* ignore */ }
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Return type interface
// ---------------------------------------------------------------------------

/**
 * Everything the hook exposes to ``ChatPage``.
 *
 * State setters are included so ChatPage can still drive user-interaction
 * flows (send message, answer question, permission decision) that mutate
 * streaming state.
 */
export interface ChatStreamingLifecycle {
  // State for rendering
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  sessionId: string | undefined;
  setSessionId: React.Dispatch<React.SetStateAction<string | undefined>>;
  pendingQuestion: PendingQuestion | null;
  setPendingQuestion: React.Dispatch<React.SetStateAction<PendingQuestion | null>>;
  isStreaming: boolean;
  setIsStreaming: (streaming: boolean) => void;
  streamingActivity: StreamingActivity | null;
  /** Debounced activity — stable for at least MIN_ACTIVITY_DISPLAY_MS. */
  displayedActivity: StreamingActivity | null;
  /** Elapsed seconds since streaming started with no content yet (Fix 9). */
  elapsedSeconds: number;

  // Refs for external access
  abortRef: React.MutableRefObject<(() => void) | null>;
  messagesEndRef: React.RefObject<HTMLDivElement | null>;

  // Fix 1: Stream generation counter
  streamGenRef: React.MutableRefObject<number>;
  incrementStreamGen: () => void;

  // Fix 2: Auto-scroll with user scroll detection
  userScrolledUpRef: React.MutableRefObject<boolean>;
  /** Reset user-scrolled-up flag so auto-scroll resumes (e.g. on new user message). */
  resetUserScroll: () => void;

  // Fix 6: Per-tab state isolation
  tabStateRef: React.MutableRefObject<Map<string, TabState>>;
  activeTabIdRef: React.MutableRefObject<string | null>;
  /** Save current foreground tab state into the per-tab map. */
  saveTabState: () => void;
  /** Restore a tab's state from the per-tab map into useState. Returns false if tab not found. */
  restoreTabState: (tabId: string) => boolean;
  /** Initialize a new tab entry in the per-tab map with defaults. */
  initTabState: (tabId: string, initialMessages?: Message[]) => void;
  /** Clean up a tab: remove from map, abort controller if active. */
  cleanupTabState: (tabId: string) => void;

  // Fix 8: Tab status indicators
  /** Per-tab status for header indicators. Keyed by tabId. */
  tabStatuses: Record<string, TabStatus>;
  /** Update a tab's status in both the per-tab map and the tabStatuses useState. */
  updateTabStatus: (tabId: string, status: TabStatus) => void;

  // Factories — tab-aware (Fix 6)
  createStreamHandler: (assistantMessageId: string, tabId?: string) => (event: StreamEvent) => void;
  createCompleteHandler: (tabId?: string) => () => void;
  createErrorHandler: (assistantMessageId: string, tabId?: string) => (error: Error) => void;

  // Fix 5: sessionStorage persistence
  /** Remove persisted pending state for a session (call on successful answer submission). */
  removePendingStateForSession: (sessionId: string) => void;
}

// ---------------------------------------------------------------------------
// Hook dependencies — injected by ChatPage so the hook stays decoupled
// ---------------------------------------------------------------------------

export interface ChatStreamingLifecycleDeps {
  /** react-query client for cache invalidation on result/session_cleared */
  queryClient: {
    invalidateQueries: (opts: { queryKey: string[] }) => void;
  };
  /** TSCC telemetry integration */
  applyTelemetryEvent: (event: unknown) => void;
  tsccTriggerAutoExpand: (reason: string) => void;
  /** Session lookup for stale entry cleanup (Fix 5). Returns null/throws on 404. */
  getSession?: (sessionId: string) => Promise<{ id: string } | null>;
}

// ---------------------------------------------------------------------------
// Hook implementation
// ---------------------------------------------------------------------------

export function useChatStreamingLifecycle(
  deps: ChatStreamingLifecycleDeps,
): ChatStreamingLifecycle {
  const { queryClient, applyTelemetryEvent, tsccTriggerAutoExpand, getSession } = deps;

  // --- Core chat state ---
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();

  // Track streaming state per session so tabs don't interfere with each other.
  // _pendingStream covers the gap before session_start assigns a sessionId.
  const [streamingSessions, setStreamingSessions] = useState<Set<string>>(
    new Set(),
  );
  const [_pendingStream, _setPendingStream] = useState(false);

  const isStreaming = sessionId
    ? streamingSessions.has(sessionId) || _pendingStream
    : _pendingStream;

  // --- Refs: streaming lifecycle & per-tab state isolation ---
  // These refs are used by stream handlers, tab switch logic, and scroll detection.
  // They are intentionally refs (not state) to avoid stale closures and unnecessary re-renders.
  const tabStateRef = useRef<Map<string, TabState>>(new Map());
  const activeTabIdRef = useRef<string | null>(null);
  const streamGenRef = useRef<number>(0);
  const sessionIdRef = useRef<string | undefined>(sessionId);
  const messagesRef = useRef<Message[]>(messages);
  const pendingQuestionRef = useRef<PendingQuestion | null>(null);
  const userScrolledUpRef = useRef<boolean>(false); // Fix 2: auto-scroll detection
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const streamStartTimeRef = useRef<number | null>(null); // Fix 9: elapsed time counter

  // Pending states
  const [pendingQuestion, setPendingQuestion] =
    useState<PendingQuestion | null>(null);

  // --- Fix 9: Elapsed time counter during initial wait ---
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);

  // --- Fix 8: Tab status indicators ---
  // Mirror of TabState.status for each tab, stored as useState to trigger
  // re-renders in the tab header when status changes. Bounded by MAX_OPEN_TABS.
  const [tabStatuses, setTabStatuses] = useState<Record<string, TabStatus>>({});

  /**
   * Update a tab's lifecycle status in both the per-tab map (authoritative)
   * and the ``tabStatuses`` useState (for rendering). Guarded to avoid
   * unnecessary re-renders when the status hasn't actually changed.
   */
  const updateTabStatus = useCallback((tabId: string, newStatus: TabStatus) => {
    const tabState = tabStateRef.current.get(tabId);
    if (tabState && tabState.status === newStatus) return; // no change — skip re-render
    if (tabState) {
      tabState.status = newStatus;
    }
    setTabStatuses(prev => ({ ...prev, [tabId]: newStatus }));
  }, []);

  // --- Consolidated ref sync (single useEffect for performance) ---
  useEffect(() => {
    messagesRef.current = messages;
    sessionIdRef.current = sessionId;
    pendingQuestionRef.current = pendingQuestion;
  }, [messages, sessionId, pendingQuestion]);

  /**
   * Transition isStreaming state using ``sessionIdRef.current`` (not a
   * stale closure over ``sessionId``). When starting a stream, sets
   * ``_pendingStream`` so the spinner shows immediately. When stopping,
   * removes the current sessionId from ``streamingSessions``.
   */
  const setIsStreaming = useCallback(
    (streaming: boolean) => {
      const currentSessionId = sessionIdRef.current;
      _setPendingStream(streaming);
      setStreamingSessions((prev) => {
        const next = new Set(prev);
        if (streaming && currentSessionId) {
          next.add(currentSessionId);
        } else if (!streaming) {
          if (currentSessionId) next.delete(currentSessionId);
        }
        return next;
      });
    },
    [], // no sessionId dependency — reads from ref
  );

  /**
   * Increment the stream generation counter. Called when starting a new
   * stream or when an event-driven pause (ask_user_question,
   * cmd_permission_request, error) should invalidate any pending
   * createCompleteHandler.
   */
  const incrementStreamGen = useCallback(() => {
    streamGenRef.current += 1;
    // Also update per-tab map if active tab exists
    const tabId = activeTabIdRef.current;
    if (tabId) {
      const tabState = tabStateRef.current.get(tabId);
      if (tabState) {
        tabState.streamGen = streamGenRef.current;
      }
    }
  }, []);

  // Derive streaming activity for spinner label
  const streamingActivity = useMemo(
    () => deriveStreamingActivity(isStreaming, messages),
    [isStreaming, messages],
  );

  // --- Fix 4: Debounced activity label — minimum display duration ---
  const [displayedActivity, setDisplayedActivity] = useState<StreamingActivity | null>(null);
  const lastActivityChangeTimeRef = useRef<number>(0);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // If streaming stopped, show final activity immediately
    if (!isStreaming) {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      setDisplayedActivity(streamingActivity);
      lastActivityChangeTimeRef.current = 0;
      return;
    }

    // If activity is null (no content yet / "Thinking..."), show immediately
    if (streamingActivity === null) {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      setDisplayedActivity(null);
      return;
    }

    const now = Date.now();
    const elapsed = now - lastActivityChangeTimeRef.current;

    if (elapsed >= MIN_ACTIVITY_DISPLAY_MS || lastActivityChangeTimeRef.current === 0) {
      // Enough time has passed (or first activity) — update immediately
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      setDisplayedActivity(streamingActivity);
      lastActivityChangeTimeRef.current = now;
    } else {
      // Too soon — schedule update after remaining time
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      const remaining = MIN_ACTIVITY_DISPLAY_MS - elapsed;
      debounceTimerRef.current = setTimeout(() => {
        debounceTimerRef.current = null;
        setDisplayedActivity(streamingActivity);
        lastActivityChangeTimeRef.current = Date.now();
      }, remaining);
    }

    // Cleanup on unmount
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
    };
  }, [streamingActivity, isStreaming]);

  // --- Fix 9: Elapsed time — record start time when streaming begins ---
  useEffect(() => {
    if (isStreaming) {
      streamStartTimeRef.current = Date.now();
      setElapsedSeconds(0);
    } else {
      streamStartTimeRef.current = null;
      setElapsedSeconds(0);
    }
  }, [isStreaming]);

  // --- Fix 9: Tick elapsed counter every second while waiting for first content ---
  useEffect(() => {
    if (!isStreaming || streamingActivity !== null) {
      // Content arrived or not streaming — clear elapsed
      if (elapsedSeconds !== 0) setElapsedSeconds(0);
      return;
    }

    const intervalId = setInterval(() => {
      if (streamStartTimeRef.current === null) return;
      const elapsed = Math.floor(
        (Date.now() - streamStartTimeRef.current) / 1000,
      );
      setElapsedSeconds(elapsed);
    }, 1000);

    return () => clearInterval(intervalId);
  }, [isStreaming, streamingActivity]); // eslint-disable-line react-hooks/exhaustive-deps — elapsedSeconds intentionally omitted to avoid restarting the interval on every tick

  // --- Fix 5: Mount-time restore from sessionStorage ---
  // On mount, check if there's a persisted pending state for the current
  // sessionId. If found, restore messages and pendingQuestion so the user
  // sees the conversation + question form instead of a blank welcome screen.
  useEffect(() => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;

    const restored = restorePendingState(currentSessionId);
    if (restored) {
      setMessages(restored.messages);
      setPendingQuestion(restored.pendingQuestion);
      // Also update the per-tab map if an active tab exists
      const tabId = activeTabIdRef.current;
      if (tabId) {
        const tabState = tabStateRef.current.get(tabId);
        if (tabState) {
          tabState.messages = restored.messages;
          tabState.pendingQuestion = restored.pendingQuestion;
        }
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — mount-only

  // --- Fix 5: Deferred stale entry cleanup ---
  // Scan sessionStorage for stale swarm_chat_pending_* entries on mount.
  // Deferred via setTimeout so it doesn't block initial render.
  useEffect(() => {
    if (!getSession) return;

    const getSessionFn = getSession;
    const timerId = setTimeout(() => {
      cleanupStalePendingEntries(getSessionFn).catch(() => {
        // Cleanup is best-effort — swallow errors
      });
    }, STALE_CLEANUP_DELAY_MS);

    return () => clearTimeout(timerId);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — mount-only

  // --- Fix 6: Per-tab state management functions ---

  /**
   * Save current foreground tab state into the per-tab map.
   * Reads from the per-tab map (authoritative) for streaming fields,
   * and syncs non-streaming fields from refs.
   */
  const saveTabState = useCallback(() => {
    const currentTabId = activeTabIdRef.current;
    if (!currentTabId) return;

    const existing = tabStateRef.current.get(currentTabId);
    if (existing) {
      // Per-tab map is authoritative for messages (stream handlers update it directly).
      // Sync non-streaming fields that only change via useState:
      existing.pendingQuestion = pendingQuestionRef.current;
      existing.pendingStream = _pendingStream;
    } else {
      // First save for this tab — initialize from current useState
      tabStateRef.current.set(currentTabId, {
        messages: messagesRef.current,
        sessionId: sessionIdRef.current,
        pendingQuestion: pendingQuestionRef.current,
        abortController: null, // abort controller is set by stream start, not by save
        pendingStream: _pendingStream,
        streamGen: streamGenRef.current,
        status: 'idle',
      });
    }
  }, [_pendingStream]);

  /**
   * Restore a tab's state from the per-tab map into useState.
   * Returns false if tab not found in the map.
   */
  const restoreTabState = useCallback((tabId: string): boolean => {
    activeTabIdRef.current = tabId;
    const targetState = tabStateRef.current.get(tabId);
    if (!targetState) return false;

    setMessages(targetState.messages);
    setSessionId(targetState.sessionId);
    setPendingQuestion(targetState.pendingQuestion);
    abortRef.current = targetState.abortController
      ? () => targetState.abortController?.abort()
      : null;
    _setPendingStream(targetState.pendingStream);
    streamGenRef.current = targetState.streamGen;
    return true;
  }, []);

  /**
   * Initialize a new tab entry in the per-tab map with defaults.
   * Sets the new tab as the active tab.
   */
  const initTabState = useCallback((tabId: string, initialMessages?: Message[]) => {
    tabStateRef.current.set(tabId, {
      messages: initialMessages ?? [],
      sessionId: undefined,
      pendingQuestion: null,
      abortController: null,
      pendingStream: false,
      streamGen: streamGenRef.current, // Inherit current generation to avoid stale-guard mismatch
      status: 'idle',
    });
    activeTabIdRef.current = tabId;
    // Fix 8: Initialize tab status in the useState mirror
    setTabStatuses(prev => ({ ...prev, [tabId]: 'idle' }));
  }, []);

  /**
   * Reset the user-scrolled-up flag so auto-scroll resumes.
   * Called when the user sends a new message — ensures the new
   * conversation flow is visible from the start.
   */
  const resetUserScroll = useCallback(() => {
    userScrolledUpRef.current = false;
  }, []);

  /**
   * Clean up a tab: remove from map, abort controller if active.
   */
  const cleanupTabState = useCallback((tabId: string) => {
    const tabState = tabStateRef.current.get(tabId);
    if (tabState?.abortController) {
      tabState.abortController.abort();
    }
    tabStateRef.current.delete(tabId);
    // Fix 8: Remove tab status entry
    setTabStatuses(prev =>
      Object.fromEntries(Object.entries(prev).filter(([k]) => k !== tabId)),
    );
  }, []);

  // --- Stream handler factories (tab-aware — Fix 6) ---

  const createStreamHandler = useCallback(
    (assistantMessageId: string, tabId?: string) => {
      // Capture the tab this handler belongs to. Falls back to active tab
      // for backward compatibility with single-tab usage.
      const capturedTabId = tabId ?? activeTabIdRef.current;

      return (event: StreamEvent) => {
        // Guard: if tab was closed while stream was running, no-op
        const tabState = capturedTabId
          ? tabStateRef.current.get(capturedTabId)
          : undefined;

        // When capturedTabId is null (initial tab before registration), treat as active.
        // The null case only occurs for the first tab before initTabState fires.
        const isActiveTab = capturedTabId === null || capturedTabId === activeTabIdRef.current;

        // DEBUG: trace every SSE event through the handler
        if (import.meta.env.DEV) {
          console.log('[StreamHandler]', event.type, {
            capturedTabId,
            activeTabId: activeTabIdRef.current,
            isActiveTab,
            hasTabState: !!tabState,
            tabMapSize: tabStateRef.current.size,
            msgCount: messagesRef.current.length,
            assistantMessageId,
          });
        }

        if (event.type === 'session_start' && event.sessionId) {
          // Update per-tab map
          if (tabState) {
            tabState.sessionId = event.sessionId;
            tabState.pendingStream = false;
          }
          // Only update useState if this is the active foreground tab
          if (isActiveTab) {
            setSessionId(event.sessionId);
            setStreamingSessions((prev) => {
              const next = new Set(prev);
              next.add(event.sessionId!);
              return next;
            });
            _setPendingStream(false);
          }
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          if (tabState) {
            tabState.sessionId = event.newSessionId;
            tabState.messages = [];
          }
          if (isActiveTab) {
            setSessionId(event.newSessionId);
            setMessages([]);
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
          }
        } else if (event.type === 'assistant' && event.content) {
          // Fix 8: Update tab status to 'streaming' on first assistant event
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          // Always update the per-tab map (even for background tabs)
          if (tabState) {
            tabState.messages = updateMessages(
              tabState.messages,
              assistantMessageId,
              event.content,
              event.model,
            );
          }

          // Update useState with functional updater to avoid stale messagesRef
          if (isActiveTab) {
            setMessages((prev) => updateMessages(
              prev,
              assistantMessageId,
              event.content!,
              event.model,
            ));
          }
        } else if (
          event.type === 'ask_user_question' &&
          event.questions &&
          event.toolUseId
        ) {
          const pq: PendingQuestion = {
            toolUseId: event.toolUseId,
            questions: event.questions,
          };

          // Compute updated messages once — append ask_user_question block
          const auqBlock = {
            type: 'ask_user_question' as const,
            toolUseId: event.toolUseId!,
            questions: event.questions!,
          };
          const currentMsgs = tabState?.messages ?? messagesRef.current;
          const auqMessages = currentMsgs.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: [...msg.content, auqBlock] }
              : msg,
          );

          // Update per-tab map
          if (tabState) {
            tabState.pendingQuestion = pq;
            if (event.sessionId) tabState.sessionId = event.sessionId;
            tabState.messages = auqMessages;
          }

          if (isActiveTab) {
            setPendingQuestion(pq);
            if (event.sessionId) setSessionId(event.sessionId);
            setMessages(auqMessages);
            setIsStreaming(false);
          }
          // Fix 1: Increment stream generation so the pending
          // createCompleteHandler from the SSE reader becomes a no-op.
          incrementStreamGen();

          // Fix 8: Update tab status to 'waiting_input'
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'waiting_input');
          }

          // Fix 5: Persist pending state to sessionStorage from per-tab map
          // (authoritative source) so it survives component re-mounts.
          const persistSessionId = tabState?.sessionId ?? event.sessionId;
          if (persistSessionId && tabState) {
            persistPendingState(persistSessionId, tabState.messages, pq);
          }
          // Auto-expand TSCC for blocking issue requiring user input
          tsccTriggerAutoExpand('blocking_issue');
        } else if (event.type === 'cmd_permission_request') {
          const raw = event as unknown as Record<string, unknown>;
          const sid = event.sessionId || (raw.session_id as string);

          if (tabState && sid) {
            tabState.sessionId = sid;
          }

          if (isActiveTab) {
            if (sid) setSessionId(sid);
            setIsStreaming(false);
          }
          incrementStreamGen();

          // Fix 8: Update tab status to 'permission_needed'
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'permission_needed');
          }
        } else if (event.type === 'result') {
          const sid =
            event.sessionId ||
            ((event as unknown as Record<string, unknown>)
              .session_id as string);

          if (tabState && sid) {
            tabState.sessionId = sid;
          }

          if (isActiveTab) {
            if (sid) setSessionId(sid);
            queryClient.invalidateQueries({ queryKey: ['radar', 'wipTasks'] });
            queryClient.invalidateQueries({ queryKey: ['radar', 'completedTasks'] });
          }

          // Fix 5: Remove persisted pending state — session completed successfully
          const resultSessionId = sid ?? tabState?.sessionId;
          if (resultSessionId) {
            removePendingState(resultSessionId);
          }

          // Fix 8: Update tab status — background tabs get 'complete_unread', foreground gets 'idle'
          if (capturedTabId) {
            updateTabStatus(
              capturedTabId,
              isActiveTab ? 'idle' : 'complete_unread',
            );
          }
        } else if (event.type === 'error') {
          const errorMsg =
            event.message ||
            event.error ||
            event.detail ||
            'An unknown error occurred';
          const suggestedAction =
            event.suggestedAction ||
            ((event as unknown as Record<string, unknown>)
              .suggested_action as string | undefined);
          const fullError = suggestedAction
            ? `${errorMsg}\n\n💡 ${suggestedAction}`
            : errorMsg;

          // Build the error content block
          const errorContent: ContentBlock[] = [
            { type: 'text' as const, text: `Error: ${fullError}` },
          ];

          // Helper: replace assistant message content with error, or append
          // a standalone error message if the assistant message isn't found
          // (handles the case where error arrives before React syncs the
          // assistant message added by handleSendMessage).
          const applyError = (prev: Message[]): Message[] => {
            const found = prev.some((m) => m.id === assistantMessageId);
            if (found) {
              return prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, isError: true, content: errorContent }
                  : msg,
              );
            }
            // Assistant message not yet in array — append a standalone error
            return [
              ...prev,
              {
                id: assistantMessageId,
                role: 'assistant' as const,
                content: errorContent,
                isError: true,
                timestamp: new Date().toISOString(),
              },
            ];
          };

          if (tabState) {
            tabState.messages = applyError(tabState.messages);
          }

          if (isActiveTab) {
            // Use functional updater to get latest state (avoids stale ref)
            setMessages((prev) => applyError(prev));
            // Fix 3: Stop streaming on error so spinner doesn't persist
            setIsStreaming(false);
            // Fix 3: Force scroll to error — reset user-scrolled-up and scroll to bottom
            userScrolledUpRef.current = false;
            messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
          }
          incrementStreamGen();

          // Fix 8: Update tab status to 'error'
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'error');
          }
        } else if (
          event.type === 'agent_activity' ||
          event.type === 'tool_invocation' ||
          event.type === 'capability_activated' ||
          event.type === 'sources_updated' ||
          event.type === 'summary_updated'
        ) {
          applyTelemetryEvent(event as unknown);
          if (
            event.type === 'summary_updated' &&
            event.description?.toLowerCase().includes('plan')
          ) {
            tsccTriggerAutoExpand('first_plan');
          }
        }
      };
    },
    [queryClient, applyTelemetryEvent, tsccTriggerAutoExpand, setIsStreaming, incrementStreamGen, updateTabStatus],
  );

  const createErrorHandler = useCallback(
    (assistantMessageId: string, tabId?: string) => {
      const capturedTabId = tabId ?? activeTabIdRef.current;

      return (error: Error) => {
        console.error('Stream error:', error);
        const tabState = capturedTabId
          ? tabStateRef.current.get(capturedTabId)
          : undefined;
        const isActiveTab = capturedTabId === null || capturedTabId === activeTabIdRef.current;

        const errorContent: ContentBlock[] = [
          { type: 'text' as const, text: `Connection error: ${error.message}` },
        ];

        // Same pattern as createStreamHandler error path: if the assistant
        // message isn't in the array yet (React batching), append standalone.
        const applyError = (prev: Message[]): Message[] => {
          const found = prev.some((m) => m.id === assistantMessageId);
          if (found) {
            return prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: errorContent }
                : msg,
            );
          }
          return [
            ...prev,
            {
              id: assistantMessageId,
              role: 'assistant' as const,
              content: errorContent,
              isError: true,
              timestamp: new Date().toISOString(),
            },
          ];
        };

        if (tabState) {
          tabState.messages = applyError(tabState.messages);
        }

        if (isActiveTab) {
          setMessages((prev) => applyError(prev));
          setIsStreaming(false);
        }
        incrementStreamGen();
      };
    },
    [setIsStreaming, incrementStreamGen],
  );

  /**
   * Create a complete handler that is generation-guarded and tab-aware.
   *
   * Captures ``streamGenRef.current`` and ``tabId`` at creation time.
   * When the SSE reader fires the handler, it checks both the captured
   * generation and tab validity. If they differ (a new stream started,
   * or an event-driven pause already handled the transition), the handler
   * is a no-op.
   */
  const createCompleteHandler = useCallback((tabId?: string) => {
    const capturedGen = streamGenRef.current;
    const capturedTabId = tabId ?? activeTabIdRef.current;

    return () => {
      // Check per-tab generation if available
      if (capturedTabId) {
        const tabState = tabStateRef.current.get(capturedTabId);
        if (!tabState || tabState.streamGen !== capturedGen) return; // stale or closed tab
        tabState.pendingStream = false;
      }

      if (streamGenRef.current !== capturedGen) return; // stale — no-op

      // Only update useState if this is still the active foreground tab
      if (capturedTabId === activeTabIdRef.current) {
        setIsStreaming(false);
      }
    };
  }, [setIsStreaming]);

  // --- Return lifecycle interface ---
  return {
    messages,
    setMessages,
    sessionId,
    setSessionId,
    pendingQuestion,
    setPendingQuestion,
    isStreaming,
    setIsStreaming,
    streamingActivity,
    displayedActivity,
    elapsedSeconds,
    abortRef,
    messagesEndRef,
    streamGenRef,
    incrementStreamGen,
    userScrolledUpRef,
    resetUserScroll,
    tabStateRef,
    activeTabIdRef,
    saveTabState,
    restoreTabState,
    initTabState,
    cleanupTabState,
    tabStatuses,
    updateTabStatus,
    createStreamHandler,
    createCompleteHandler,
    createErrorHandler,
    removePendingStateForSession: removePendingState,
  };
}
