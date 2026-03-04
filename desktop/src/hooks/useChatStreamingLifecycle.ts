/**
 * Custom hook encapsulating the chat streaming lifecycle state machine.
 *
 * Extracted from ``ChatPage.tsx`` (Phase 0 refactor) to isolate streaming
 * concerns into a testable, self-contained unit. This hook owns:
 *
 * - **State**: ``messages``, ``sessionId``, ``pendingQuestion``, ``isStreaming``,
 *   ``pendingStreamTabs`` (per-tab pending tracking)
 * - **Refs**: ``messagesEndRef``, ``sessionIdRef``, ``messagesRef``
 * - **Factories**: ``createStreamHandler``, ``createCompleteHandler``,
 *   ``createErrorHandler``
 * - **Pure function**: ``deriveStreamingActivity`` (exported standalone for
 *   testability)
 * - **Pure function**: ``updateMessages`` (exported for testability)
 * - **Derived**: ``isStreaming`` derivation, ``streamingActivity`` memo
 *
 * Tab state management (per-tab map, activeTabIdRef, tab statuses, lifecycle
 * methods) has been migrated to ``useUnifiedTabState``. This hook now receives
 * unified tab state methods via ``ChatStreamingLifecycleDeps`` and uses them
 * in stream handlers for tab-aware updates.
 *
 * ``ChatPage`` consumes this hook and focuses on rendering + user interactions.
 *
 * **Fix 1**: Stream generation counter prevents stale complete handlers.
 * **Fix 6**: Per-tab state isolation — stream handlers read/write the unified
 *   Tab_Map via injected deps (``tabMapRef``, ``activeTabIdRef``).
 *
 * @module useChatStreamingLifecycle
 */

import React, { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import type {
  Message,
  ContentBlock,
  StreamEvent,
} from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import type { UnifiedTab } from './useUnifiedTabState';
import { type TabStatus } from './useUnifiedTabState';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// TabStatus is now imported from useUnifiedTabState and re-exported for
// backward compatibility with existing consumers.
export type { TabStatus } from './useUnifiedTabState';

// TabState has been replaced by UnifiedTab from useUnifiedTabState.
// The unified Tab_Map (injected via deps.tabMapRef) now holds UnifiedTab entries.

/** Maximum number of concurrent open tabs — re-exported from useUnifiedTabState for backward compat. */
export { MAX_OPEN_TABS } from './useUnifiedTabState';

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
  setIsStreaming: (streaming: boolean, tabId?: string) => void;
  streamingActivity: StreamingActivity | null;
  /** Debounced activity — stable for at least MIN_ACTIVITY_DISPLAY_MS. */
  displayedActivity: StreamingActivity | null;
  /** Elapsed seconds since streaming started with no content yet (Fix 9). */
  elapsedSeconds: number;

  // Refs for external access
  messagesEndRef: React.RefObject<HTMLDivElement | null>;

  // Per-tab pending state for ChatPage guard
  pendingStreamTabs: Set<string>;
  /** Remove a specific tab from pendingStreamTabs (e.g. on tab close). */
  clearPendingStreamTab: (tabId: string) => void;
  /** Force re-derivation of isStreaming (e.g. after tab switch). */
  bumpStreamingDerivation: () => void;

  // Fix 1: Stream generation counter
  streamGenRef: React.MutableRefObject<number>;
  incrementStreamGen: () => void;

  // Fix 2: Auto-scroll with user scroll detection
  userScrolledUpRef: React.MutableRefObject<boolean>;
  /** Reset user-scrolled-up flag so auto-scroll resumes (e.g. on new user message). */
  resetUserScroll: () => void;

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
  /** Session lookup for stale entry cleanup (Fix 5). Returns null/throws on 404. */
  getSession?: (sessionId: string) => Promise<{ id: string } | null>;

  // --- Unified tab state methods (injected from useUnifiedTabState) ---
  /** Read a tab's full state from the unified Tab_Map. */
  getTabState: (tabId: string) => UnifiedTab | undefined;
  /** Patch a tab's state in the unified Tab_Map. */
  updateTabState: (tabId: string, patch: Partial<Omit<UnifiedTab, 'id'>>) => void;
  /** Update a tab's lifecycle status in the unified Tab_Map. */
  updateTabStatus: (tabId: string, status: TabStatus) => void;
  /** Direct ref to the unified Tab_Map for synchronous reads in stream handlers. */
  tabMapRef: React.RefObject<Map<string, UnifiedTab>>;
  /** Direct ref to the active tab ID for synchronous reads in stream handlers. */
  activeTabIdRef: React.RefObject<string | null>;
}

// ---------------------------------------------------------------------------
// Hook implementation
// ---------------------------------------------------------------------------

export function useChatStreamingLifecycle(
  deps: ChatStreamingLifecycleDeps,
): ChatStreamingLifecycle {
  const {
    queryClient,
    getSession,
    updateTabStatus,
    tabMapRef,
    activeTabIdRef,
  } = deps;

  // --- Core chat state ---
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();

  // Per-tab pending state: tracks tabs between handleSendMessage and session_start.
  // Replaces the old single `_pendingStream` boolean — each tab's pending state
  // is independent, keyed by tabId.
  const [pendingStreamTabs, setPendingStreamTabs] = useState<Set<string>>(new Set());

  // Derive isStreaming from the active tab's per-tab state + pending set.
  // tabMapRef is authoritative for isStreaming; pendingStreamTabs covers the
  // gap before session_start. This useState triggers re-renders when pending changes.
  const activeTabIdCurrent = activeTabIdRef.current;
  const activeTabState = activeTabIdCurrent ? tabMapRef.current.get(activeTabIdCurrent) : undefined;
  const isStreaming = (activeTabState?.isStreaming ?? false) || pendingStreamTabs.has(activeTabIdCurrent ?? '');

  // --- Refs: streaming lifecycle ---
  // These refs are used by stream handlers, scroll detection, etc.
  // Tab state refs (tabMapRef, activeTabIdRef) are now injected via deps
  // from the unified hook — see ChatStreamingLifecycleDeps.
  const streamGenRef = useRef<number>(0);
  const sessionIdRef = useRef<string | undefined>(sessionId);
  const messagesRef = useRef<Message[]>(messages);
  const userScrolledUpRef = useRef<boolean>(false); // Fix 2: auto-scroll detection
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const streamStartTimeRef = useRef<number | null>(null); // Fix 9: elapsed time counter

  // Pending states
  const [pendingQuestion, setPendingQuestion] =
    useState<PendingQuestion | null>(null);

  // --- Fix 9: Elapsed time counter during initial wait ---
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);

  // --- Fix 8: Tab status indicators ---
  // Tab statuses are now managed by the unified hook (useUnifiedTabState)
  // and injected via deps.updateTabStatus. No local state needed.

  // --- Consolidated ref sync (single useEffect for performance) ---
  useEffect(() => {
    messagesRef.current = messages;
    sessionIdRef.current = sessionId;
  }, [messages, sessionId, pendingQuestion]);

  /**
   * Transition isStreaming state for a specific tab. Updates the per-tab map
   * entry and the ``pendingStreamTabs`` Set (which triggers re-render for
   * isStreaming derivation). When no ``tabId`` is provided, defaults to
   * ``activeTabIdRef.current`` for backward compatibility.
   */
  const setIsStreaming = useCallback(
    (streaming: boolean, tabId?: string) => {
      const targetTabId = tabId ?? activeTabIdRef.current;

      // Always update per-tab map
      if (targetTabId) {
        const tabState = tabMapRef.current.get(targetTabId);
        if (tabState) {
          tabState.isStreaming = streaming;
        }
      }

      // Update pendingStreamTabs (triggers re-render for isStreaming derivation)
      setPendingStreamTabs((prev) => {
        const next = new Set(prev);
        if (streaming && targetTabId) {
          next.add(targetTabId);
        } else if (!streaming && targetTabId) {
          next.delete(targetTabId);
        }
        return next;
      });
    },
    [], // no dependencies — reads from refs
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
      const tabState = tabMapRef.current.get(tabId);
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
        const tabState = tabMapRef.current.get(tabId);
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

  // --- Fix 6: Per-tab state management ---
  // Tab lifecycle methods (saveTabState, restoreTabState, initTabState,
  // cleanupTabState) are now owned by the unified hook (useUnifiedTabState).
  // Stream handlers access tab state via deps.tabMapRef and deps.activeTabIdRef.

  /**
   * Reset the user-scrolled-up flag so auto-scroll resumes.
   * Called when the user sends a new message — ensures the new
   * conversation flow is visible from the start.
   */
  const resetUserScroll = useCallback(() => {
    userScrolledUpRef.current = false;
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
          ? tabMapRef.current.get(capturedTabId)
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
            tabMapSize: tabMapRef.current.size,
            msgCount: messagesRef.current.length,
            assistantMessageId,
          });
        }

        if (event.type === 'session_start' && event.sessionId) {
          // Update per-tab map. Keep isStreaming true — the tab is still
          // actively streaming after session_start. The pending phase ends
          // (pendingStreamTabs removal below) but the tab remains streaming
          // until result/error/ask_user_question arrives.
          if (tabState) {
            tabState.sessionId = event.sessionId;
            // Keep tabState.isStreaming = true (set by setIsStreaming(true) in handleSendMessage)
          }
          // Only update useState if this is the active foreground tab
          if (isActiveTab) {
            setSessionId(event.sessionId);
          }
          // Clear pending for this specific tab — the tab is now tracked
          // by tabState.isStreaming (true) rather than pendingStreamTabs.
          if (capturedTabId) {
            setPendingStreamTabs((prev) => {
              const next = new Set(prev);
              next.delete(capturedTabId);
              return next;
            });
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
          }
          setIsStreaming(false, capturedTabId ?? undefined);
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
        } else if (event.type === 'cmd_permission_request') {
          const raw = event as unknown as Record<string, unknown>;
          const sid = event.sessionId || (raw.session_id as string);

          if (tabState && sid) {
            tabState.sessionId = sid;
          }

          if (isActiveTab) {
            if (sid) setSessionId(sid);
          }
          setIsStreaming(false, capturedTabId ?? undefined);
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

          // Result is the definitive signal that the conversation turn is
          // complete. Clear streaming state for this tab so the spinner
          // stops and the input re-enables. Without this, the tab stays
          // in "processing..." state until the SSE [DONE] signal fires
          // the createCompleteHandler — which may be stale or delayed.
          setIsStreaming(false, capturedTabId ?? undefined);
          incrementStreamGen();

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
          }
          // Tab-aware: clear only this tab's streaming state
          setIsStreaming(false, capturedTabId ?? undefined);
          if (isActiveTab) {
            // Fix 3: Force scroll to error — reset user-scrolled-up and scroll to bottom
            userScrolledUpRef.current = false;
            messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
          }
          incrementStreamGen();

          // Fix 8: Update tab status to 'error'
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'error');
          }
        }
        // Telemetry events (agent_activity, tool_invocation, etc.) are no
        // longer processed — TSCC fetches metadata from the endpoint instead.
      };
    },
    [queryClient, setIsStreaming, incrementStreamGen, updateTabStatus],
  );

  const createErrorHandler = useCallback(
    (assistantMessageId: string, tabId?: string) => {
      const capturedTabId = tabId ?? activeTabIdRef.current;

      return (error: Error) => {
        console.error('Stream error:', error);
        const tabState = capturedTabId
          ? tabMapRef.current.get(capturedTabId)
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
        }
        // Tab-aware: clear only this tab's streaming state
        setIsStreaming(false, capturedTabId ?? undefined);
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
        const tabState = tabMapRef.current.get(capturedTabId);
        if (!tabState || tabState.streamGen !== capturedGen) return; // stale or closed tab
        tabState.isStreaming = false;
      }

      if (streamGenRef.current !== capturedGen) return; // stale — no-op

      // Tab-aware: clear only this tab's streaming state
      setIsStreaming(false, capturedTabId ?? undefined);
    };
  }, [setIsStreaming]);

  /**
   * Remove a specific tab from ``pendingStreamTabs``. Called by ChatPage
   * when closing a tab to prevent stale entries from lingering in the Set
   * after the tab's map entry has been deleted.
   */
  const clearPendingStreamTab = useCallback((tabId: string) => {
    setPendingStreamTabs((prev) => {
      if (!prev.has(tabId)) return prev; // no-op — avoid unnecessary re-render
      const next = new Set(prev);
      next.delete(tabId);
      return next;
    });
  }, []);

  /**
   * Force re-derivation of ``isStreaming`` by triggering a re-render via
   * ``setPendingStreamTabs``. Used by ChatPage on tab switch so the
   * derivation picks up the new active tab's state from ``tabMapRef``.
   */
  const bumpStreamingDerivation = useCallback(() => {
    setPendingStreamTabs((prev) => new Set(prev));
  }, []);

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
    pendingStreamTabs,
    clearPendingStreamTab,
    bumpStreamingDerivation,
    messagesEndRef,
    streamGenRef,
    incrementStreamGen,
    userScrolledUpRef,
    resetUserScroll,
    createStreamHandler,
    createCompleteHandler,
    createErrorHandler,
    removePendingStateForSession: removePendingState,
  };
}
