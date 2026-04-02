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
 *   ``createErrorHandler`` (with SSE reconnection logic)
 * - **Pure function**: ``deriveStreamingActivity`` (exported standalone for
 *   testability)
 * - **Pure function**: ``updateMessages`` (exported for testability)
 * - **Pure function**: ``computeReconnectDelay`` (exported for testability)
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
 * **SSE Resilience**: Connection-phase failures trigger automatic reconnection
 *   with exponential backoff (up to 3 attempts). Mid-stream failures preserve
 *   partial content and show an error with a manual Retry button.
 *
 * @module useChatStreamingLifecycle
 */

import React, { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import type {
  Message,
  ContentBlock,
  StreamEvent,
  SystemPromptMetadata,
  CompactionGuardEvent,
} from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import { chatService } from '../services/chat';
import type { UnifiedTab } from './useUnifiedTabState';
import { type TabStatus } from './useUnifiedTabState';
import { useToast } from '../contexts/ToastContext';

// ---------------------------------------------------------------------------
// Reconnection constants
// ---------------------------------------------------------------------------

/** Maximum number of automatic reconnection attempts for connection-phase failures. */
const RECONNECT_MAX_ATTEMPTS = 3;

/** Base delay in ms for exponential backoff (attempt 0 → 1000ms). */
const RECONNECT_BASE_DELAY_MS = 1000;

/** Maximum delay cap in ms for exponential backoff. */
const RECONNECT_MAX_DELAY_MS = 30000;

// ---------------------------------------------------------------------------
// Stall detection constants
// ---------------------------------------------------------------------------

/** Stall threshold during text generation — no real (non-heartbeat) event for this long. */
const STALL_THRESHOLD_TEXT_MS = 60_000;

/** Stall threshold during tool execution — tools like Bash/Read can take minutes. */
const STALL_THRESHOLD_TOOL_MS = 180_000;

/**
 * Compute the reconnection delay for a given attempt using exponential backoff.
 *
 * Formula: ``min(baseDelay * 2^attempt, maxDelay)``
 *
 * Exported for testability (Property 3).
 */
export function computeReconnectDelay(
  attempt: number,
  baseDelayMs: number = RECONNECT_BASE_DELAY_MS,
  maxDelayMs: number = RECONNECT_MAX_DELAY_MS,
): number {
  return Math.min(baseDelayMs * Math.pow(2, attempt), maxDelayMs);
}

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

  // Extract operational context from the last tool_use's summary field
  const toolContext =
    lastToolUse && 'summary' in lastToolUse
      ? (lastToolUse as { summary?: string }).summary ?? null
      : null;

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
    case 'thinking':
      // Thinking blocks are accumulated via thinking_delta and then reconciled
      // by the assistant event. Use a type-only key so the streamed thinking
      // block deduplicates against the SDK's final thinking block.
      return `thinking:0`;
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
    if (filteredContent.length === 0) {
      return msg; // No changes — return same reference
    }
    return {
      ...msg,
      content: [...msg.content, ...filteredContent],
      ...(model ? { model } : {}),
      // Clear isError when new non-error content arrives — handles the
      // auto-retry case where backend recovers after emitting an error event.
      ...(msg.isError ? { isError: false } : {}),
    };
  });
}

/**
 * Append a text delta (streaming token) to the last text block in an assistant message.
 *
 * If the assistant message has no text block yet, creates one.  If the last
 * content block is already a text block, appends to it in-place (new object
 * reference for React).  This is the hot path during streaming — called once
 * per token, so it must be allocation-light.
 */
export function appendTextDelta(
  currentMessages: Message[],
  assistantMessageId: string,
  text: string,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;
    const content = [...msg.content];
    const lastBlock = content[content.length - 1];
    if (lastBlock && lastBlock.type === 'text') {
      // Append to existing text block (new reference)
      content[content.length - 1] = {
        ...lastBlock,
        text: (lastBlock.text ?? '') + text,
      };
    } else {
      // First text token — create a new text block
      content.push({ type: 'text', text } as ContentBlock);
    }
    return { ...msg, content };
  });
}

/**
 * Append a thinking delta (streaming token) to the last thinking block in an assistant message.
 *
 * If the assistant message has no thinking block yet, creates one.  If the last
 * content block is already a thinking block, appends to it in-place (new object
 * reference for React).  Same pattern as ``appendTextDelta`` but for thinking content.
 */
export function appendThinkingDelta(
  currentMessages: Message[],
  assistantMessageId: string,
  thinking: string,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;
    const content = [...msg.content];
    const lastBlock = content[content.length - 1];
    if (lastBlock && lastBlock.type === 'thinking') {
      // Append to existing thinking block (new reference)
      content[content.length - 1] = {
        ...lastBlock,
        thinking: ((lastBlock as { thinking?: string }).thinking ?? '') + thinking,
      } as ContentBlock;
    } else {
      // First thinking token — create a new thinking block
      content.push({ type: 'thinking', thinking } as ContentBlock);
    }
    return { ...msg, content };
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
  /** Active pending permission request ID (null = no pending permission). */
  pendingPermissionRequestId: string | null;
  setPendingPermissionRequestId: React.Dispatch<React.SetStateAction<string | null>>;
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
  createDisconnectHandler: (tabId?: string) => () => void;
  createErrorHandler: (assistantMessageId: string, tabId?: string) => (error: Error) => void;

  // Fix 5: sessionStorage persistence
  /** Remove persisted pending state for a session (call on successful answer submission). */
  removePendingStateForSession: (sessionId: string) => void;

  // Context window monitoring
  /** Non-null when the backend emits a context_warning SSE event (level: warn | critical). */
  contextWarning: ContextWarning | null;
  /** Set the context warning display mirror (used by tab switch restore). */
  setContextWarning: React.Dispatch<React.SetStateAction<ContextWarning | null>>;
  /** Dismiss the context warning banner/toast. */
  clearContextWarning: () => void;

  // System prompt metadata (delivered via SSE alongside context_warning)
  /** Non-null when the backend emits a system_prompt_metadata SSE event after a turn. */
  promptMetadata: SystemPromptMetadata | null;
  /** Set the prompt metadata display mirror (used by tab switch restore). */
  setPromptMetadata: React.Dispatch<React.SetStateAction<SystemPromptMetadata | null>>;

  // Compaction guard (delivered via SSE compaction_guard event)
  /** Non-null when the backend emits a compaction_guard SSE event (soft_warn, hard_warn, kill). */
  compactionGuard: CompactionGuardEvent | null;
  /** Set the compaction guard display mirror (used by tab switch restore). */
  setCompactionGuard: React.Dispatch<React.SetStateAction<CompactionGuardEvent | null>>;

  // Hang detection — true when streaming but no real (non-heartbeat) SDK events for >60s
  /** True when the active stream has received only heartbeats for >60s. */
  isLikelyStalled: boolean;
}

/** Context warning payload from the backend context monitor. */
export interface ContextWarning {
  level: 'ok' | 'warn' | 'critical';
  pct: number;
  tokensEst: number;
  message: string;
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
  /** Callback to drain a queued message after a stream completes or is stopped. */
  onDrainQueue?: (tabId: string) => void;
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

  // --- Toast for reconnection notifications ---
  const { addToast } = useToast();

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

  // --- Hang detection: track last real (non-heartbeat) SSE event ---
  // Context-aware: tool execution can legitimately take minutes (npm test,
  // large file reads), so we use a longer threshold when a tool is in flight.
  const lastRealEventRef = useRef<number>(Date.now());
  const pendingToolUseRef = useRef<boolean>(false);
  const [isLikelyStalled, setIsLikelyStalled] = useState(false);

  // Poll for stall state while streaming (10s interval).
  // Heartbeats keep the SSE connection alive but mask SDK hangs from the
  // frontend. This timer checks whether we've received any real SDK event
  // (text, tool_use, tool_result, result, etc.) within the threshold.
  useEffect(() => {
    if (!isStreaming) {
      setIsLikelyStalled(false);
      pendingToolUseRef.current = false;
      return;
    }
    const interval = setInterval(() => {
      const threshold = pendingToolUseRef.current
        ? STALL_THRESHOLD_TOOL_MS
        : STALL_THRESHOLD_TEXT_MS;
      const stalled = Date.now() - lastRealEventRef.current > threshold;
      setIsLikelyStalled(stalled);
    }, 10_000);
    return () => clearInterval(interval);
  }, [isStreaming]);

  // Pending states
  const [pendingQuestion, setPendingQuestion] =
    useState<PendingQuestion | null>(null);
  const [pendingPermissionRequestId, setPendingPermissionRequestId] =
    useState<string | null>(null);

  // --- Fix 9: Elapsed time counter during initial wait ---
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);

  // --- Context window monitoring ---
  const [contextWarning, setContextWarning] = useState<ContextWarning | null>(null);

  // --- System prompt metadata (delivered via SSE, same pipeline as contextWarning) ---
  const [promptMetadata, setPromptMetadata] = useState<SystemPromptMetadata | null>(null);

  // --- Compaction guard (delivered via SSE compaction_guard event) ---
  const [compactionGuard, setCompactionGuard] = useState<CompactionGuardEvent | null>(null);

  const clearContextWarning = useCallback(() => {
    const tabId = activeTabIdRef.current;
    if (tabId) {
      const tabState = tabMapRef.current.get(tabId);
      if (tabState) tabState.contextWarning = null;
    }
    setContextWarning(null);
  }, [activeTabIdRef, tabMapRef]);

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
  // On tab switch, isStreaming flips false→true but the stream was already
  // running in the background. Check the per-tab state to see if the tab
  // was already streaming — if so, restore the existing start time instead
  // of resetting. This prevents "Thinking..." from restarting on switch back.
  useEffect(() => {
    if (isStreaming) {
      const tabId = activeTabIdRef.current;
      const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
      // If the tab already has a stream start time (was streaming in bg),
      // restore it instead of resetting to now.
      if (tabState?.streamStartTime) {
        streamStartTimeRef.current = tabState.streamStartTime;
        // Re-derive elapsed from the stored start time
        setElapsedSeconds(Math.floor((Date.now() - tabState.streamStartTime) / 1000));
      } else {
        // New stream — record start time and store in tab state
        const now = Date.now();
        streamStartTimeRef.current = now;
        setElapsedSeconds(0);
        if (tabState) tabState.streamStartTime = now;
      }
    } else {
      // Clear per-tab start time when streaming stops
      const tabId = activeTabIdRef.current;
      const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
      if (tabState) tabState.streamStartTime = undefined;
      streamStartTimeRef.current = null;
      setElapsedSeconds(0);
    }
  }, [isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps — refs are stable

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

  // Long-stream timeout warning removed — the elapsed timer (Fix 9) already
  // shows "Thinking… Xs" when the agent hasn't produced content yet. A blanket
  // 120s toast false-alarmed on normal multi-tool workflows (deep-research,
  // code analysis, etc.) and trained users to ignore it.

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

        // Mark that we've received data — used by reconnection logic to
        // distinguish connection-phase vs mid-stream failures.
        if (tabState && !tabState.hasReceivedData) {
          tabState.hasReceivedData = true;

          // If we were reconnecting, the stream has successfully resumed.
          // Clear reconnection state and fire a success toast.
          if (tabState.isReconnecting) {
            console.log(`[Reconnect] Tab ${capturedTabId}: reconnection succeeded`);
            tabState.isReconnecting = false;
            tabState.reconnectionAttempt = 0;
            addToast({
              severity: 'info',
              message: 'Stream reconnected successfully.',
              id: `reconnect-success-${capturedTabId}`,
            });
          }
          // Clear "Resuming session..." indicator once data arrives
          if (tabState.isResuming) {
            tabState.isResuming = false;
          }
        }

        // Also clear isResuming on any real data event, outside the
        // hasReceivedData guard.  The session_resuming event itself can
        // consume the one-shot !hasReceivedData check before isResuming
        // is set to true (ordering: hasReceivedData=true runs at line 984,
        // then isResuming=true at line 1364).  Subsequent data events
        // skip the guard and isResuming stays stuck.  This catches it.
        if (tabState?.isResuming && (
          event.type === 'assistant' || event.type === 'tool_use' ||
          event.type === 'tool_result' || event.type === 'result'
        )) {
          tabState.isResuming = false;
        }

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

        // Track last real (non-heartbeat) event for stall detection.
        // Heartbeats keep the SSE connection alive but don't indicate SDK
        // progress. Only real events reset the stall timer.
        if (event.type !== 'heartbeat') {
          lastRealEventRef.current = Date.now();
        }

        // Track tool execution state for context-aware stall thresholds.
        // tool_use → tool is running (may take minutes), tool_result → done.
        if (event.type === 'tool_use') {
          pendingToolUseRef.current = true;
        } else if (event.type === 'tool_result') {
          pendingToolUseRef.current = false;
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
            // Re-inject the assistant placeholder after clearing so subsequent
            // 'assistant' events can find assistantMessageId via updateMessages().
            // Without this, session_cleared wipes the placeholder synced by
            // handleSendMessage, and all post-clear streaming content is dropped.
            tabState.messages = [{
              id: assistantMessageId,
              role: 'assistant' as const,
              content: [],
              timestamp: new Date().toISOString(),
            }];
          }
          if (isActiveTab) {
            setSessionId(event.newSessionId);
            setMessages([{
              id: assistantMessageId,
              role: 'assistant' as const,
              content: [],
              timestamp: new Date().toISOString(),
            }]);
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
          }
        } else if (event.type === 'text_delta' && event.text) {
          // --- Streaming text delta: append token incrementally ---
          // This is the HOT PATH — called once per token for real-time rendering.
          // Update tab status on first delta if not already streaming.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          if (tabState) {
            tabState.messages = appendTextDelta(
              tabState.messages,
              assistantMessageId,
              event.text,
            );
          }

          if (isActiveTab) {
            setMessages((prev) => appendTextDelta(
              prev,
              assistantMessageId,
              event.text!,
            ));
          }
        } else if (event.type === 'thinking_delta' && event.thinking) {
          // --- Streaming thinking delta: append thinking token incrementally ---
          // Same pattern as text_delta but for extended thinking content.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          if (tabState) {
            tabState.messages = appendThinkingDelta(
              tabState.messages,
              assistantMessageId,
              event.thinking,
            );
          }

          if (isActiveTab) {
            setMessages((prev) => appendThinkingDelta(
              prev,
              assistantMessageId,
              event.thinking!,
            ));
          }
        } else if (event.type === 'thinking_start') {
          // Thinking block started — update tab status to streaming.
          // The actual content arrives via thinking_delta events.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }
        } else if (event.type === 'assistant' && event.content) {
          // Full assistant message — the SDK's complete, authoritative content.
          // When streaming is on, text was already rendered incrementally via
          // text_delta events. This event reconciles with the final truth:
          // tool_use/tool_result blocks are appended (they weren't streamed),
          // and existing text blocks are left alone (deduped by blockKey).
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
          const requestId = (event.requestId || raw.request_id) as string;
          const toolName = (event.toolName || raw.tool_name) as string;
          const toolInput = (event.toolInput || raw.tool_input) as Record<string, unknown>;

          // Append cmd_permission_request content block to assistant message
          // (same pattern as ask_user_question — inline in chat stream)
          const permBlock = {
            type: 'cmd_permission_request' as const,
            requestId: requestId,
            toolName: toolName,
            toolInput: toolInput,
            reason: event.reason || '',
            options: event.options || ['approve', 'deny'],
          };
          const currentMsgs = tabState?.messages ?? messagesRef.current;
          const permMessages = currentMsgs.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: [...msg.content, permBlock] }
              : msg,
          );

          // Update per-tab map
          if (tabState) {
            tabState.messages = permMessages;
            if (sid) tabState.sessionId = sid;
            tabState.pendingPermissionRequestId = requestId;
          }

          if (isActiveTab) {
            setMessages(permMessages);
            if (sid) setSessionId(sid);
            setPendingPermissionRequestId(requestId);
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

          // Result is the definitive signal that the conversation turn is
          // complete. Sync tabState.messages → React state as a safety net.
          //
          // CRITICAL: Use functional updater (not direct value) to avoid
          // breaking React 18's batched update chain. When all SSE events
          // arrive in a single reader.read() chunk (common for fast <200ms
          // responses), text_delta functional updaters and this result sync
          // execute in the same synchronous batch. A direct value like
          // setMessages(tabState.messages) can cause React to skip the
          // render if it considers the reference unchanged — leaving the
          // UI stuck showing partial content until the next interaction
          // forces a re-render. Using a functional updater that returns a
          // NEW array guarantees React sees a state change.
          if (isActiveTab && tabState) {
            if (sid) setSessionId(sid);
            // Spread creates a new reference — React always re-renders.
            const authoritativeMessages = [...tabState.messages];
            setMessages(() => authoritativeMessages);
          } else if (isActiveTab) {
            if (sid) setSessionId(sid);
          }
          // Deferred re-sync: if isActiveTab was transiently false due to
          // React batching or ref update lag, this microtask fires after the
          // batch flushes and re-checks the live ref. Only syncs if this tab
          // is ACTUALLY active — safe for multi-tab isolation.
          if (tabState && !isActiveTab) {
            queueMicrotask(() => {
              if (capturedTabId === activeTabIdRef.current) {
                const deferred = [...tabState.messages];
                setMessages(() => deferred);
                if (sid) setSessionId(sid);
              }
            });
          }
          queryClient.invalidateQueries({ queryKey: ['radar', 'wipTasks'] });
          queryClient.invalidateQueries({ queryKey: ['radar', 'completedTasks'] });

          // Drain site A: if a queued message is waiting, keep streaming
          // state TRUE to avoid a false→true flicker that kills the
          // "Running…" / "Progressing…" indicator.  The drain will
          // seamlessly continue the stream with a new conversation turn.
          const hasQueuedMessage = !!(capturedTabId && tabState?.queuedMessage);

          if (!hasQueuedMessage) {
            // Normal completion — clear streaming state so spinner stops
            // and input re-enables.
            setIsStreaming(false, capturedTabId ?? undefined);
          }
          // Always bump generation so the old completeHandler no-ops.
          incrementStreamGen();

          // Fix 5: Remove persisted pending state — session completed successfully
          const resultSessionId = sid ?? tabState?.sessionId;
          if (resultSessionId) {
            removePendingState(resultSessionId);
          }

          if (!hasQueuedMessage) {
            // Fix 8: Update tab status — background tabs get 'complete_unread', foreground gets 'idle'
            if (capturedTabId) {
              updateTabStatus(
                capturedTabId,
                isActiveTab ? 'idle' : 'complete_unread',
              );
            }
          }

          if (hasQueuedMessage) {
            // Schedule drain — isStreaming stays true, indicator persists.
            // setTimeout(0) lets React flush the result-event state updates
            // (messages sync, session ID) before starting the next turn.
            setTimeout(() => deps.onDrainQueue?.(capturedTabId!), 0);
          }
        } else if (event.type === 'error') {
          // Suppress error events from a user-stopped stream — the abort
          // can race with backend error delivery, producing a spurious
          // "An unknown error occurred" that forces a redundant resend.
          if (tabState?.userStopped) {
            console.log('[StreamHandler] Suppressing error from user-stopped stream', { capturedTabId });
            // Clean up streaming state — same as createErrorHandler suppression.
            // Without this, a leaked error event could leave the tab in a
            // half-streaming state (isStreaming=true but no UI indicators).
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            return;
          }

          // SESSION_BUSY: Backend rejected our send because the session is
          // still actively streaming (SSE disconnect caused a race).
          // Don't show error — silently inform user the message is queued.
          // See: 2026-04-02 SSE disconnect kill chain diagnosis.
          if (event.code === 'SESSION_BUSY') {
            console.log('[StreamHandler] SESSION_BUSY — session still streaming', { capturedTabId });
            // Clean up streaming state from this failed send attempt
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'streaming');
            // Show a lightweight toast instead of error in chat
            addToast({
              severity: 'info',
              message: 'Session is still processing. Your message will be sent when ready.',
              id: `session-busy-${capturedTabId}`,
            });
            return;
          }

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

          // Build the error content block — use friendly tone, not scary "Error:" prefix.
          // Backend already sanitizes SDK errors into user-friendly messages.
          const errorContent: ContentBlock[] = [
            { type: 'text' as const, text: `⚠️ ${fullError}` },
          ];

          // Helper: APPEND error to assistant message content (preserving
          // any tool_use / tool_result / text blocks already streamed).
          // If the exact assistantMessageId isn't found (race condition where
          // error arrives before React syncs the optimistic placeholder),
          // fall back to the LAST assistant message — that's where the
          // streamed content lives. Only create a standalone error as last resort.
          const applyError = (prev: Message[]): Message[] => {
            const found = prev.some((m) => m.id === assistantMessageId);
            if (found) {
              return prev.map((msg) =>
                msg.id === assistantMessageId
                  ? { ...msg, isError: true, content: [...msg.content, ...errorContent] }
                  : msg,
              );
            }
            // Fallback: find the last assistant message (may have different ID
            // due to race between tabState ref writes and React state updates)
            const lastAssistant = findLast(prev, (m) => m.role === 'assistant');
            if (lastAssistant) {
              console.warn('[StreamHandler] assistantMessageId not found, appending error to last assistant message', {
                expected: assistantMessageId,
                actual: lastAssistant.id,
              });
              return prev.map((msg) =>
                msg === lastAssistant
                  ? { ...msg, isError: true, content: [...msg.content, ...errorContent] }
                  : msg,
              );
            }
            // No assistant messages at all — create standalone error
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

            // QUEUE_TIMEOUT: store retry payload so ChatPage can offer "Retry" button
            if (event.code === 'QUEUE_TIMEOUT' && event.retryPayload) {
              tabState.queueTimeoutRetry = event.retryPayload;
            }
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
        // Backend auto-retry: the prior `error` event set isStreaming=false and
        // tabStatus='error'. The backend is now retrying with a fresh client on
        // the SAME SSE connection, so we need to re-enter streaming state.
        else if (event.type === 'reconnecting') {
          setIsStreaming(true, capturedTabId ?? undefined);
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'streaming');
          }
          // Reset stream start time for the elapsed counter
          if (streamStartTimeRef.current === null) {
            streamStartTimeRef.current = Date.now();
          }
        }
        // Backend cold-start resume: subprocess was killed (idle >2h, app restart),
        // PATH A is spawning a fresh subprocess with context injection.
        // Show "Resuming session..." instead of ambiguous "Thinking...".
        else if (event.type === 'session_resuming') {
          const tabState = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          if (tabState) {
            tabState.isResuming = true;
            // Force re-render so ChatPage picks up isResuming from tabMapRef.
            // Same pattern as isReconnecting — ref mutations alone don't
            // trigger React re-renders.
            const isActive = capturedTabId === activeTabIdRef.current;
            if (isActive) {
              setIsStreaming(true, capturedTabId ?? undefined);
            }
          }
        }
        // Context compacted — backend emits when the SDK compacts the context window
        // (either auto or manual trigger). Clear the originating tab's warning.
        else if (event.type === 'context_compacted') {
          const tabState = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          if (tabState) {
            tabState.contextWarning = null;
          }
          if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
            setContextWarning(null);
          }
        }
        // Context window warning — backend emits context usage at all levels
        // (ok, warn, critical). Write to the originating tab's UnifiedTab,
        // mirror to React state only if this is the active tab (display mirror pattern).
        else if (event.type === 'context_warning' && event.level && event.pct != null) {
          const warning: ContextWarning = {
            level: event.level as 'ok' | 'warn' | 'critical',
            pct: event.pct,
            tokensEst: event.tokensEst ?? 0,
            message: event.message ?? `Context ${event.pct}% full`,
          };
          const tabState = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          if (tabState) {
            tabState.contextWarning = warning;
          }
          if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
            setContextWarning(warning);
          }
        }
        // Compaction guard — backend emits when the guard escalates
        // (soft_warn, hard_warn, kill). Same display mirror pattern as context_warning:
        // write to tabMapRef, mirror to React state only for the active tab.
        else if (event.type === 'compaction_guard') {
          const subtype = event.subtype as 'soft_warn' | 'hard_warn' | 'kill' | undefined;
          // Ignore unknown subtypes gracefully (don't crash the stream handler)
          if (subtype === 'soft_warn' || subtype === 'hard_warn' || subtype === 'kill') {
            // SSE event uses snake_case (context_pct, pattern_description)
            // but CompactionGuardEvent uses camelCase — convert inline.
            const raw = event as unknown as Record<string, unknown>;
            const guardEvent: CompactionGuardEvent = {
              subtype,
              contextPct: (raw.context_pct as number) ?? (event.contextPct as number) ?? 0,
              message: event.message ?? 'Guard event',
              patternDescription: (raw.pattern_description as string) ?? event.patternDescription,
            };
            const cgTab = capturedTabId
              ? tabMapRef.current.get(capturedTabId)
              : undefined;
            if (cgTab) {
              cgTab.compactionGuard = guardEvent;
              // HARD_WARN and KILL trigger backend interrupt() which ends the
              // stream.  Clear streaming state immediately so the tab doesn't
              // show "Running" forever after the guard fires.
              if (subtype === 'hard_warn' || subtype === 'kill') {
                cgTab.isStreaming = false;
              }
            }
            if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
              setCompactionGuard(guardEvent);
              if (subtype === 'hard_warn' || subtype === 'kill') {
                setIsStreaming(false, capturedTabId ?? undefined);
              }
            }
          }
        }
        // System prompt metadata — backend emits after each turn alongside
        // context_warning.  Same display mirror pattern: write to tabMapRef,
        // mirror to React state only for the active tab.
        else if (event.type === 'system_prompt_metadata') {
          const { type: _type, ...metadata } = event;
          const spmTab = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          if (spmTab) {
            spmTab.promptMetadata = metadata as SystemPromptMetadata;
          }
          if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
            setPromptMetadata(metadata as SystemPromptMetadata);
          }
        }
        // Evolution SSE events — inject as standalone messages in the stream
        else if (event.type?.startsWith('evolution_')) {
          const evolutionMessage: Message = {
            id: `evo-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            role: 'assistant',
            content: [],
            timestamp: new Date().toISOString(),
            evolutionEvent: {
              eventType: event.type,
              data: (event as unknown as Record<string, unknown>).data as Record<string, unknown>
                ?? (event as unknown as Record<string, unknown>),
            },
          };

          if (tabState) {
            tabState.messages = [...tabState.messages, evolutionMessage];
          }
          if (isActiveTab) {
            setMessages((prev) => [...prev, evolutionMessage]);
          }
        }
        // Telemetry events (agent_activity, tool_invocation, etc.) are no
        // longer processed — TSCC fetches metadata from the endpoint instead.
      };
    },
    [queryClient, setIsStreaming, incrementStreamGen, updateTabStatus, addToast],
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

        // Suppress connection errors from a user-stopped stream — the abort
        // races with the SSE reader's catch block and fires onError with a
        // generic error that would show a confusing message on the next send.
        if (tabState?.userStopped) {
          console.log('[ErrorHandler] Suppressing connection error from user-stopped stream', { capturedTabId });
          // Still clean up streaming state
          setIsStreaming(false, capturedTabId ?? undefined);
          incrementStreamGen();
          if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
          return;
        }

        // --- Connection-phase reconnection logic ---
        // If no data has been received yet (connection-phase failure) and
        // the tab is still open, attempt automatic reconnection with
        // exponential backoff. Mid-stream failures cannot be resumed
        // because the backend turn is stateful.
        const isConnectionPhase = tabState ? !tabState.hasReceivedData : false;
        const currentAttempt = tabState?.reconnectionAttempt ?? 0;

        if (
          isConnectionPhase &&
          currentAttempt < RECONNECT_MAX_ATTEMPTS &&
          tabState &&
          tabState.retryStreamFn
        ) {
          // Tab still exists — schedule a retry
          const nextAttempt = currentAttempt + 1;
          tabState.reconnectionAttempt = nextAttempt;
          tabState.isReconnecting = true;

          // Mirror to React state if active tab
          if (isActiveTab) {
            // Force re-render so UI can show "Reconnecting..." indicator
            setIsStreaming(true, capturedTabId ?? undefined);
          }

          const delay = computeReconnectDelay(currentAttempt);
          console.log(
            `[Reconnect] Tab ${capturedTabId}: attempt ${nextAttempt}/${RECONNECT_MAX_ATTEMPTS}, delay ${delay}ms`,
          );

          const retryFn = tabState.retryStreamFn;

          setTimeout(() => {
            // Guard: tab may have been closed during the delay
            if (!capturedTabId || !tabMapRef.current.has(capturedTabId)) {
              console.log(`[Reconnect] Tab ${capturedTabId} closed during backoff — aborting`);
              return;
            }

            const currentTabState = tabMapRef.current.get(capturedTabId);
            if (!currentTabState) return;

            // Reset hasReceivedData for the new attempt so the next
            // error handler can distinguish connection-phase again
            currentTabState.hasReceivedData = false;

            // Re-initiate the stream via the stored retry function
            const newAbort = retryFn();

            // Update the abort controller for the new stream
            currentTabState.abortController = {
              abort: () => { newAbort(); },
              signal: { aborted: false },
            } as unknown as AbortController;
          }, delay);

          return; // Don't show error — reconnection in progress
        }

        // --- Reconnection exhausted or mid-stream failure ---
        // Clear reconnection state
        if (tabState) {
          const wasReconnecting = tabState.isReconnecting;
          tabState.isReconnecting = false;
          tabState.reconnectionAttempt = 0;
          tabState.hasReceivedData = false;

          // If we exhausted all reconnection attempts, log it
          if (wasReconnecting && currentAttempt >= RECONNECT_MAX_ATTEMPTS) {
            console.warn(
              `[Reconnect] Tab ${capturedTabId}: all ${RECONNECT_MAX_ATTEMPTS} attempts exhausted`,
            );
          }
        }

        // --- Gap 2 fix: explicitly stop backend session ---
        // The backend's disconnect recovery (_recover_streaming_on_disconnect)
        // may not have fired yet — the frontend stall timer (45s) can detect
        // the problem before the backend's heartbeat loop (15s interval)
        // notices the dead TCP connection.  Without this, the user sends a
        // new message while the backend is still in STREAMING → force_unstick
        // → kill → --resume → replays old output.
        //
        // Fire-and-forget: if stop fails, the backend disconnect recovery
        // or the force_unstick fallback in send() still handles it.
        const stopSessionId = tabState?.sessionId;
        if (stopSessionId) {
          chatService.stopSession(stopSessionId).catch(() => {
            // Best-effort — backend disconnect recovery is the fallback
          });
        }

        // If this was a successful reconnection that then failed mid-stream,
        // fire the toast for the reconnection success (handled by stream handler).
        // For exhausted retries or mid-stream failures, show the error.

        // Include the real error text so the user knows what actually happened.
        // The original code suppressed error.message entirely — that made debugging
        // impossible and showed a blank-looking generic message.
        const realError = error.message || 'Unknown connection error';
        const errorContent: ContentBlock[] = [
          { type: 'text' as const, text: `⚠️ Connection interrupted: ${realError}\n\n💡 Your conversation is saved — send your message again to continue.` },
        ];

        // Same pattern as createStreamHandler error path: APPEND error
        // to preserve any partial tool_use / text content already streamed.
        // Uses the same fallback-to-last-assistant strategy to prevent content loss.
        const applyError = (prev: Message[]): Message[] => {
          const found = prev.some((m) => m.id === assistantMessageId);
          if (found) {
            const updated = prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [...msg.content, ...errorContent], isError: true }
                : msg,
            );
            // Defensive: verify content was preserved, not replaced
            const updatedMsg = updated.find((m) => m.id === assistantMessageId);
            if (updatedMsg && updatedMsg.content.length < 2) {
              console.warn('[ErrorHandler] BUG: assistant message content may have been lost during error merge', {
                originalBlockCount: prev.find((m) => m.id === assistantMessageId)?.content.length,
                updatedBlockCount: updatedMsg.content.length,
              });
            }
            return updated;
          }
          // Fallback: find the last assistant message (race condition guard)
          const lastAssistant = findLast(prev, (m) => m.role === 'assistant');
          if (lastAssistant) {
            console.warn('[ErrorHandler] assistantMessageId not found, appending error to last assistant message', {
              expected: assistantMessageId,
              actual: lastAssistant.id,
            });
            return prev.map((msg) =>
              msg === lastAssistant
                ? { ...msg, content: [...msg.content, ...errorContent], isError: true }
                : msg,
            );
          }
          console.warn('[ErrorHandler] No assistant messages at all — creating standalone error', {
            assistantMessageId,
            messageCount: prev.length,
          });
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
          const beforeCount = tabState.messages.find((m) => m.id === assistantMessageId)?.content.length ?? 0;
          tabState.messages = applyError(tabState.messages);
          if (import.meta.env.DEV) {
            const afterCount = tabState.messages.find((m) => m.id === assistantMessageId)?.content.length ?? 0;
            console.log('[ErrorHandler] tabState message content:', { beforeCount, afterCount });
          }
        }

        if (isActiveTab) {
          setMessages((prev) => applyError(prev));
        }
        // Tab-aware: clear only this tab's streaming state
        setIsStreaming(false, capturedTabId ?? undefined);
        incrementStreamGen();

        // Clean up sessionStorage pending state on stream error
        if (tabState?.sessionId) {
          removePendingState(tabState.sessionId);
        }

        // Fix 8: Update tab status to 'error'
        if (capturedTabId) {
          updateTabStatus(capturedTabId, 'error');
        }

        // Drain queued message after terminal error — the user's queued
        // message shouldn't be silently orphaned because the previous
        // stream hit a connection error.  Same pattern as result-event
        // drain (Site A) and handleStop drain (Site B).
        if (capturedTabId && tabState?.queuedMessage) {
          setTimeout(() => deps.onDrainQueue?.(capturedTabId), 0);
        }
      };
    },
    [setIsStreaming, incrementStreamGen, addToast, updateTabStatus],
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
      // --- Pre-guard drain: rescue orphaned queued messages ---
      // When an SSE-level error event fires without a subsequent
      // `reconnecting` (backend decided not to retry), the error
      // handler bumps streamGen, making this complete handler stale.
      // The queued message would be silently orphaned.  Check BEFORE
      // the gen guard so stale handlers can still rescue the queue.
      // Guard: only drain if tab is NOT already streaming (prevents
      // double-drain when the result-event drain already started).
      if (capturedTabId) {
        const preGuardTab = tabMapRef.current.get(capturedTabId);
        if (preGuardTab?.queuedMessage && !preGuardTab.isStreaming) {
          setTimeout(() => deps.onDrainQueue?.(capturedTabId), 0);
        }
      }

      // Check per-tab generation if available
      if (capturedTabId) {
        const tabState = tabMapRef.current.get(capturedTabId);
        if (!tabState || tabState.streamGen !== capturedGen) return; // stale or closed tab
        tabState.isStreaming = false;
        // Always clear resume indicator on stream completion — safety net
        // for the case where session_resuming consumed hasReceivedData
        // before isResuming was set (ordering race in the event handler).
        tabState.isResuming = false;

        // Clean up sessionStorage pending state on stream completion
        if (tabState.sessionId) {
          removePendingState(tabState.sessionId);
        }
      }

      if (streamGenRef.current !== capturedGen) return; // stale — no-op

      // Tab-aware: clear only this tab's streaming state
      setIsStreaming(false, capturedTabId ?? undefined);
    };
  }, [setIsStreaming]);

  /**
   * Create a handler for premature SSE disconnects (HTTP stream closed
   * without [DONE] sentinel). Unlike ``createCompleteHandler``, this
   * keeps ``isStreaming=true`` and sets ``isReconnecting=true`` so the
   * user sees a "Reconnecting..." indicator instead of the stream
   * silently stopping.
   *
   * See: 2026-04-02 SSE disconnect kill chain diagnosis.
   */
  const createDisconnectHandler = useCallback((tabId?: string) => {
    const capturedTabId = tabId ?? activeTabIdRef.current;
    const DISCONNECT_TIMEOUT_MS = 30_000; // 30s before giving up

    return () => {
      console.warn('[DisconnectHandler] Premature SSE disconnect', { capturedTabId });

      if (capturedTabId) {
        const tabState = tabMapRef.current.get(capturedTabId);
        if (tabState) {
          // Keep isStreaming=true — backend may still be processing
          tabState.isReconnecting = true;
          // Force re-render so ChatPage shows "Reconnecting..." indicator
          const isActive = capturedTabId === activeTabIdRef.current;
          if (isActive) {
            setIsStreaming(true, capturedTabId);
          }

          // Safety timeout: if no recovery within 30s, clear reconnecting
          // state.  Without this, the user sees "Reconnecting..." forever
          // when the backend finished processing but the SSE dropped.
          setTimeout(() => {
            const currentTabState = tabMapRef.current.get(capturedTabId);
            if (currentTabState?.isReconnecting) {
              console.warn('[DisconnectHandler] Timeout — clearing reconnecting state', { capturedTabId });
              currentTabState.isReconnecting = false;
              currentTabState.isStreaming = false;
              const stillActive = capturedTabId === activeTabIdRef.current;
              if (stillActive) {
                setIsStreaming(false, capturedTabId);
              }
            }
          }, DISCONNECT_TIMEOUT_MS);
        }
      }
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
   *
   * Also **immediately** derives ``displayedActivity``, ``elapsedSeconds``,
   * and ``streamStartTimeRef`` from the new active tab's authoritative state
   * in ``tabMapRef``. Without this, those React states carry the *previous*
   * tab's values for one render frame (useEffect runs AFTER render), causing
   * the "Thinking…" indicator and elapsed timer to flash stale values on
   * every tab switch.
   */
  const bumpStreamingDerivation = useCallback(() => {
    setPendingStreamTabs((prev) => new Set(prev));

    // --- Immediate tab-switch state sync (eliminates useEffect lag) ---
    const tabId = activeTabIdRef.current;
    const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
    const tabIsStreaming = tabState?.isStreaming ?? false;
    const tabMessages = tabState?.messages ?? [];

    // Derive streamingActivity for the new tab directly from tabMapRef
    const activity = deriveStreamingActivity(tabIsStreaming, tabMessages);

    // Clear any pending debounce timer to prevent it from overwriting
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
    setDisplayedActivity(activity);
    lastActivityChangeTimeRef.current = Date.now();

    // Derive elapsedSeconds and streamStartTimeRef for the new tab
    if (tabIsStreaming && activity === null && tabState?.streamStartTime) {
      // Still in "Thinking…" phase — restore elapsed from stored start time
      streamStartTimeRef.current = tabState.streamStartTime;
      setElapsedSeconds(Math.floor((Date.now() - tabState.streamStartTime) / 1000));
    } else if (tabIsStreaming) {
      // Content already arrived — elapsed not shown, but keep start time
      streamStartTimeRef.current = tabState?.streamStartTime ?? null;
      setElapsedSeconds(0);
    } else {
      // Not streaming — clear everything
      streamStartTimeRef.current = null;
      setElapsedSeconds(0);
    }
  }, []);

  // --- Return lifecycle interface ---
  return {
    messages,
    setMessages,
    sessionId,
    setSessionId,
    pendingQuestion,
    setPendingQuestion,
    pendingPermissionRequestId,
    setPendingPermissionRequestId,
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
    createDisconnectHandler,
    createErrorHandler,
    removePendingStateForSession: removePendingState,
    contextWarning,
    setContextWarning,
    clearContextWarning,
    promptMetadata,
    setPromptMetadata,
    compactionGuard,
    setCompactionGuard,
    isLikelyStalled,
  };
}
