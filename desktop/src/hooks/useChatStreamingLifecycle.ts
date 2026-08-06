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

import React, { useState, useReducer, useRef, useCallback, useMemo, useEffect } from 'react';
import { streamingReducer, INITIAL_STATE, type StreamingEvent } from './streaming-machine';
// Re-export state machine utilities for consumers
export { isActivelyStreaming, isInputBlocked, getStatusLabel, type StreamingMode, type StreamingState, type StreamingEvent } from './streaming-machine';
import type {
  Message,
  ContentBlock,
  TextContent,
  ThinkingContent,
  ToolUseContent,
  ToolResultContent,
  StreamEvent,
  SystemPromptMetadata,
  CompactionGuardEvent,
} from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import { queuedMessageFromRetryPayload, retryPayloadHasAttachments, shouldResurfaceQuestion, computeDrainRetirement, shouldArmSpinnerFromBackend, forceClearStreamVerdict, healGraceExpiryVerdict, desyncConvergeVerdict, nextReconcileDelay, type HealGraceVerdict } from './streaming-guards';
import { chatService } from '../services/chat';
import { messageStoreRegistry } from '../stores/MessageStore';
import { isOt01DiagEnabled } from '../utils/diagFlags';
import { dispatchUiCommand } from '../utils/uiCommands';
import type { UnifiedTab } from './useUnifiedTabState';
import { type TabStatus } from './useUnifiedTabState';
import { useToast } from '../contexts/ToastContext';

// ---------------------------------------------------------------------------
// Reconnection constants
// ---------------------------------------------------------------------------

/** Maximum number of automatic reconnection attempts for connection-phase failures.
 *  Sized (with the 10s delay cap below) to span ~65s so a typical daemon redeploy
 *  (~60s: crash-loop + boot + context injection) is ridden out SILENTLY — the
 *  stream just re-establishes and the user sees a "Reconnecting…" spinner, no
 *  error, no resend needed. Outages LONGER than this fall through to the
 *  auto-resend safety net (RESEND_MAX_ATTEMPTS, fired on swarm:backend-recovered). */
const RECONNECT_MAX_ATTEMPTS = 9;

/** Maximum number of auto-resends on backend-recovered for a single swallowed-question
 *  episode. The ~65s connection-phase reconnect budget covers a normal redeploy; this
 *  is the fallback for outages that outlast it. The exhausted send is re-sent when
 *  health flips back. Capped so a flapping backend (up/down/up) can't drive an
 *  unbounded resend loop. */
const RESEND_MAX_ATTEMPTS = 2;

/** Base delay in ms for exponential backoff (attempt 0 → 1000ms). */
const RECONNECT_BASE_DELAY_MS = 1000;

/** Maximum delay cap in ms for exponential backoff. Capped at 10s (not 30s) so the
 *  later retries stay responsive — once the backend is back, the next silent retry
 *  fires within ≤10s instead of leaving a long blind gap. With 9 attempts the
 *  schedule is 1,2,4,8,10,10,10,10,10 ≈ 65s total. */
const RECONNECT_MAX_DELAY_MS = 10000;

// ---------------------------------------------------------------------------
// Stall detection constants
// ---------------------------------------------------------------------------

/** Stall threshold during text generation — no real (non-heartbeat) event for this long. */
const STALL_THRESHOLD_TEXT_MS = 60_000;

/** Stall threshold during tool execution — tools like Bash/Read can take minutes. */
const STALL_THRESHOLD_TOOL_MS = 180_000;

/**
 * Tail size for turn-end reconcile fetches. The reconcile sites below only need
 * the NEWEST rows (the just-streamed assistant turn + any drained rows) — the
 * persist-lag guard and dedup both match by id on the tail, and `_applyMerge`
 * preserves any older local message NOT present in a partial DB fetch
 * (MessageStore "DB may be paginated" invariant). So reconcile fetches the last
 * RECONCILE_TAIL rows via `getSessionMessagesReconcileTail`, NOT the full history
 * — dropping per-turn pull+camelCase parse from O(N) (a heavy session is ~1000+
 * rows) to a constant. Backend caps limit at 200; 50 is well under.
 *
 * ⚠️ 50 counts RAW DB ROWS, not merged bubbles. The backend persists ONE row per
 * `assistant` SSE event (crash-safety), so a long agentic tool-loop turn can emit
 * 5–20+ raw rows that `_merge_consecutive_assistant_messages` later folds into one
 * bubble. Keep 50 comfortably ABOVE the max raw-rows-per-turn — do NOT lower it.
 * Even if a pathological >50-row turn's START falls outside the tail, correctness
 * still holds: every row carries the same `{client_id}-asst`, and the persist-lag
 * guard (_mergePreservingInteractive: more-complete-content-wins by id) keeps the
 * complete local content; a later cold initial-load (200-cap) restores the full
 * turn. The tail is a perf floor, not a correctness boundary.
 */
const RECONCILE_TAIL = 50;

// ---------------------------------------------------------------------------
// Self-healing grace period
// ---------------------------------------------------------------------------

/**
 * Grace period (ms) before showing disconnect error during backend self-heal.
 *
 * When the backend's HealingLoop refreshes a session (kill → respawn), the SSE
 * connection drops briefly (3-10s typical). During this window:
 * - Don't clear streaming state (isStreaming stays true)
 * - Don't show error toast
 * - Keep the spinner/thinking indicator (looks like "still working")
 * - Only show error if disconnect persists beyond this threshold
 *
 * User sees: brief pause in output, then work continues seamlessly.
 * User does NOT see: error messages, "Reconnecting...", or manual intervention.
 */
export const HEAL_GRACE_PERIOD_MS = 30_000;

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
  /**
   * Stable id of the current (last) tool_use block. Distinct invocations of the
   * SAME tool (two `Read`s, or `Read` → think → `Read`) get distinct ids, so the
   * elapsed-timer anchor can re-anchor per invocation rather than per tool-name —
   * a name-keyed anchor wrongly treats a recurring name as one continuous run.
   * ``null`` when there is no tool_use block (Thinking…).
   */
  toolId: string | null;
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

  // Stable per-invocation id — lets the elapsed timer re-anchor on each distinct
  // tool_use block even when the tool NAME repeats (Read → think → Read).
  // Coerce empty string → null so a degenerate id behaves like the Thinking
  // path (keep anchor) rather than a stable non-null sentinel.
  const rawToolId =
    lastToolUse && 'id' in lastToolUse
      ? (lastToolUse as { id?: string }).id
      : null;
  const toolId = rawToolId ? rawToolId : null;

  return { hasContent, toolName, toolContext, toolCount, toolId };
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
 * For text blocks, also checks substring containment: if any existing text
 * block's content ends with the incoming text, the incoming block is treated
 * as a duplicate. This handles multi-turn agentic responses where text_delta
 * accumulates ALL turns' text into one block, but AssistantMessage events
 * arrive per-turn with only that turn's text.
 *
 * Returns the same message reference when no new content is added
 * (referential stability for React memoization).
 */
/**
 * Reconcile authoritative content from an ``assistant`` SSE event into the
 * message's content array.
 *
 * Design principle: **replace, don't dedup.** The assistant event is the SDK's
 * authoritative truth for that turn. Streamed text/thinking blocks are
 * PROVISIONAL (user sees them in real-time for UX), and must be REPLACED by
 * the authoritative content when the assistant event arrives.
 *
 * Strategy:
 * 1. KEEP all existing blocks that are "confirmed" from prior turns
 *    (tool_use, tool_result, and text/thinking blocks that were already
 *    reconciled by a previous assistant event — marked with `_confirmed`).
 * 2. REMOVE unconfirmed text/thinking blocks (these are streaming provisional).
 * 3. APPEND all blocks from the assistant event, marking text/thinking as
 *    `_confirmed`. Tool_use/tool_result are deduped by ID (they may already
 *    exist from tool_result events that arrived during execution).
 *
 * This makes duplication impossible by construction — no string matching needed.
 */
export function updateMessages(
  currentMessages: Message[],
  assistantMessageId: string,
  newContent: ContentBlock[],
  model?: string,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;

    // Partition existing content into confirmed (prior turns) and unconfirmed (streaming).
    //
    // BUG FIX (2026-06-07): In agentic loops with many tools, the SDK emits
    // multiple AssistantMessage events per API roundtrip. Each event for the SAME
    // turn re-sends the same (or growing) text. The old code kept ALL _confirmed
    // text blocks → duplicates accumulated (4x, 8x...) → content array exploded
    // → React render hang → spinner stuck forever.
    //
    // Fix: When the new assistant event has text/thinking, REPLACE the LAST
    // confirmed text/thinking block (same-turn update). Earlier confirmed
    // text/thinking blocks (from genuinely different prior turns) are preserved.
    //
    // BUG FIX (2026-06-18): In agentic multi-turn flows, the SDK emits
    // intermediate AssistantMessage events that contain only thinking + tool_use
    // (no text block). The old code unconditionally dropped unconfirmed text
    // blocks — wiping streamed text the user was already seeing. Fix: only drop
    // unconfirmed text/thinking when the authoritative payload actually provides
    // a replacement of that type. Otherwise preserve the streaming provisional.
    const newHasText = newContent.some((b) => b.type === 'text');
    const newHasThinking = newContent.some((b) => b.type === 'thinking');

    const confirmed: ContentBlock[] = [];
    const hadUnconfirmed = { text: false, thinking: false };

    for (const block of msg.content) {
      if (block.type === 'text' || block.type === 'thinking') {
        if ((block as TextContent | ThinkingContent)._confirmed) {
          confirmed.push(block);
        } else {
          // Only drop unconfirmed blocks when the authoritative payload
          // provides a replacement. Otherwise preserve streaming provisional
          // content so it remains visible to the user.
          const shouldDrop =
            (block.type === 'text' && newHasText) ||
            (block.type === 'thinking' && newHasThinking);
          if (shouldDrop) {
            hadUnconfirmed[block.type] = true;
            // Drop — will be replaced by authoritative content
          } else {
            // Preserve — no replacement provided in this event
            confirmed.push(block);
          }
        }
      } else {
        // tool_use, tool_result, ask_user_question — always keep
        confirmed.push(block);
      }
    }

    // Build the authoritative content from the assistant event.
    // Dedup tool_use by id, tool_result by toolUseId — separately.
    const existingToolUseIds = new Set(
      confirmed
        .filter((b): b is ToolUseContent => b.type === 'tool_use')
        .map((b) => b.id)
    );
    const existingToolResultIds = new Set(
      confirmed
        .filter((b): b is ToolResultContent => b.type === 'tool_result')
        .map((b) => b.toolUseId)
    );

    const authoritativeBlocks: ContentBlock[] = [];
    for (const block of newContent) {
      if (block.type === 'tool_use') {
        if (!existingToolUseIds.has(block.id)) {
          authoritativeBlocks.push(block);
        }
      } else if (block.type === 'tool_result') {
        if (!existingToolResultIds.has(block.toolUseId)) {
          authoritativeBlocks.push(block);
        }
      } else if (block.type === 'text' || block.type === 'thinking') {
        // Mark as confirmed — next assistant event won't remove these
        authoritativeBlocks.push({ ...block, _confirmed: true });
      } else {
        authoritativeBlocks.push(block);
      }
    }

    // ── Same-turn text dedup (BUG FIX 2026-06-07) ──────────────────────
    // In agentic loops, the SDK re-emits the same text in multiple
    // AssistantMessage events within one turn (e.g., text + tool_use,
    // then same text + tool_use + tool_result + more tools). Without dedup,
    // each re-emission adds another confirmed text block → content explodes
    // → React render hang → spinner stuck forever.
    //
    // Strategy: If the new assistant event has a text block whose content
    // MATCHES (equals or extends) the LAST confirmed text block, replace it.
    // If the text is genuinely DIFFERENT (new turn), keep both.
    //
    // Guards against false positives:
    // - Minimum 20 chars: short strings have high collision probability
    // - Empty string skip: "".startsWith("") is always true
    // - Only checks the LAST confirmed text block (most recent turn)
    const MIN_DEDUP_LENGTH = 20;
    const newTextBlocks = authoritativeBlocks.filter((b) => b.type === 'text');
    const newThinkingBlocks = authoritativeBlocks.filter((b) => b.type === 'thinking');

    // Same-turn dedup: if new text equals or extends the last confirmed text,
    // replace it. This handles SDK re-emission where the same turn emits
    // multiple assistant events with growing text content.
    //
    // NOTE: Do NOT add a "hasToolAfter" guard here. In the P0 bug scenario,
    // tools ARE between text blocks (text → tool_use → tool_result → text re-emission).
    // The hasToolAfter check was attempted in commit 410200eb and BREAKS the fix.
    // Cross-turn safety is handled by the `startsWith` check: genuinely different
    // turns have different text, so startsWith returns false → no dedup.
    if (newTextBlocks.length > 0) {
      const newText = (newTextBlocks[0] as TextContent).text ?? '';
      if (newText) {  // Skip empty string (guard against "".startsWith("") === true)
        for (let i = confirmed.length - 1; i >= 0; i--) {
          if (confirmed[i].type === 'text' && (confirmed[i] as TextContent)._confirmed) {
            const existingText = (confirmed[i] as TextContent).text ?? '';
            if (!existingText) break;  // Empty confirmed text — don't dedup
            // Short text: exact match only (prevents false positive on common words)
            // Long text: exact match OR startsWith (handles SDK text growth within turn)
            const isMatch = existingText.length >= MIN_DEDUP_LENGTH
              ? (newText === existingText || newText.startsWith(existingText))
              : (newText === existingText);
            if (isMatch) {
              confirmed.splice(i, 1);
            }
            break;
          }
        }
      }
    }

    if (newThinkingBlocks.length > 0) {
      const newThinking = (newThinkingBlocks[0] as ThinkingContent).thinking ?? '';
      if (newThinking) {
        for (let i = confirmed.length - 1; i >= 0; i--) {
          if (confirmed[i].type === 'thinking' && (confirmed[i] as ThinkingContent)._confirmed) {
            const existingThinking = (confirmed[i] as ThinkingContent).thinking ?? '';
            if (!existingThinking) break;
            const isMatch = existingThinking.length >= MIN_DEDUP_LENGTH
              ? (newThinking === existingThinking || newThinking.startsWith(existingThinking))
              : (newThinking === existingThinking);
            if (isMatch) {
              confirmed.splice(i, 1);
            }
            break;
          }
        }
      }
    }

    // If nothing changed (no unconfirmed blocks existed AND no new blocks to add),
    // return same reference for React memoization.
    if (!hadUnconfirmed.text && !hadUnconfirmed.thinking && authoritativeBlocks.length === 0) {
      return msg;
    }

    return {
      ...msg,
      content: [...confirmed, ...authoritativeBlocks],
      ...(model ? { model } : {}),
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
    if (lastBlock && lastBlock.type === 'text' && !lastBlock._confirmed) {
      // Append to existing UNCONFIRMED text block (new reference).
      // Never append to a confirmed block — that belongs to a prior turn.
      content[content.length - 1] = {
        ...lastBlock,
        text: (lastBlock.text ?? '') + text,
      };
    } else {
      // First text token or last block is confirmed/non-text — create new provisional block
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
    if (lastBlock && lastBlock.type === 'thinking' && !lastBlock._confirmed) {
      // Append to existing UNCONFIRMED thinking block (new reference).
      // Never append to a confirmed block — that belongs to a prior turn.
      content[content.length - 1] = {
        ...lastBlock,
        thinking: ((lastBlock as { thinking?: string }).thinking ?? '') + thinking,
      } as ContentBlock;
    } else {
      // First thinking token or last block is confirmed — create new provisional block
      content.push({ type: 'thinking', thinking } as ContentBlock);
    }
    return { ...msg, content };
  });
}

// ---------------------------------------------------------------------------
// Per-message updaters — used by MessageStore.updateLast() for single-writer flow.
// These transform a single Message (not an array), matching the store's updater API.
// ---------------------------------------------------------------------------

/**
 * Append text token to a message's last unconfirmed text block.
 * If no unconfirmed text block exists, creates a new one.
 */
export function applyTextDelta(msg: Message, text: string): Message {
  const content = [...msg.content];
  const lastBlock = content[content.length - 1];
  if (lastBlock && lastBlock.type === 'text' && !lastBlock._confirmed) {
    content[content.length - 1] = {
      ...lastBlock,
      text: (lastBlock.text ?? '') + text,
    };
  } else {
    content.push({ type: 'text', text } as ContentBlock);
  }
  return { ...msg, content };
}

/**
 * Append thinking token to a message's last unconfirmed thinking block.
 * If no unconfirmed thinking block exists, creates a new one.
 */
export function applyThinkingDelta(msg: Message, thinking: string): Message {
  const content = [...msg.content];
  const lastBlock = content[content.length - 1];
  if (lastBlock && lastBlock.type === 'thinking' && !lastBlock._confirmed) {
    content[content.length - 1] = {
      ...lastBlock,
      thinking: ((lastBlock as { thinking?: string }).thinking ?? '') + thinking,
    } as ContentBlock;
  } else {
    content.push({ type: 'thinking', thinking } as ContentBlock);
  }
  return { ...msg, content };
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

  // SESSION_BUSY recovery — true when polling for backend completion
  /** True when backend returned SESSION_BUSY and we're polling for the response. */
  isWaitingForBusy: boolean;
  /** Manually cancel a tab's SESSION_BUSY recovery wait (local clear, offline-safe). */
  cancelBusyWait: (tabId: string) => void;
  // NOTE: Global streamState/dispatch intentionally NOT exposed here.
  // The global reducer is single-tab (tracks active tab only) — exposing it
  // would let consumers introduce cross-tab state bleed. Per-tab state lives
  // on tabState.streamState (accessible via tabMapRef) which is tab-safe.
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
  /** Callback to switch the active tab — used by the cross-tab AskUserQuestion
   *  toast so the user can click straight to the tab that is asking. */
  onSelectTab?: (tabId: string) => void;
  /** Callback to start a fresh session for a tab — used by the
   *  recovery_exhausted toast's "Start fresh session" action when automatic
   *  recovery has given up. Clears the tab to a new session (history preserved
   *  server-side). */
  onStartFresh?: (tabId: string) => void;
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
    onSelectTab,
    onStartFresh,
  } = deps;

  // --- Toast for reconnection notifications ---
  const { addToast, removeToast } = useToast();

  // --- Core chat state ---
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();

  // Per-tab pending state: tracks tabs between handleSendMessage and session_start.
  // Replaces the old single `_pendingStream` boolean — each tab's pending state
  // is independent, keyed by tabId.
  const [pendingStreamTabs, setPendingStreamTabs] = useState<Set<string>>(new Set());

  // Derive isStreaming from the active tab's per-tab state.
  // tabMapRef is the SINGLE source of truth once a tab is registered.
  // pendingStreamTabs serves TWO narrow purposes only:
  //   1. Re-render trigger — ref mutations don't re-render; this useState does.
  //   2. Pre-registration gap — before initTabState creates the tabState,
  //      there is no flag to read, so the Set covers that window.
  // CRITICAL: once activeTabState exists, the flag is authoritative and the
  // Set is NOT consulted. Previously this used `flag || set.has(id)`, which
  // let a stale Set entry (orphaned when a clear path skipped setIsStreaming)
  // pin isStreaming=true forever → spinner hang. Reading the Set only when
  // tabState is absent makes that orphan structurally unable to hang the UI.
  const activeTabIdCurrent = activeTabIdRef.current;
  const activeTabState = activeTabIdCurrent ? tabMapRef.current.get(activeTabIdCurrent) : undefined;
  // isStreaming derivation: boolean flags remain authoritative (per-tab state is
  // multi-tab aware; state machine is currently single-tab). The state machine
  // provides streamState.mode for consumers that want explicit mode checks.
  const isStreaming = activeTabState
    ? activeTabState.isStreaming
    : pendingStreamTabs.has(activeTabIdCurrent ?? '');


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

  // ── Streaming State Machine (shadow mode — coexists with boolean flags) ──
  // Phase 2 integration: reducer dispatches at lifecycle boundaries.
  // Consumers can opt into `streamState.mode` checks instead of boolean combos.
  // Boolean flags remain authoritative until P3/P4 completes full migration.
  const [streamState, dispatch] = useReducer(streamingReducer, INITIAL_STATE);
  const streamStateRef = useRef(streamState);
  streamStateRef.current = streamState;

  // ── Dev-mode divergence detector: state machine vs boolean flags ──
  // Logs when the two sources disagree. Once this never fires in production
  // use, we can safely remove the boolean flags (future P5).
  if (process.env.NODE_ENV === 'development') {
    const machineThinks = streamState.mode !== 'idle' && streamState.mode !== 'error'
      && streamState.mode !== 'waiting_input' && streamState.mode !== 'permission_needed';
    if (isStreaming !== machineThinks) {
      // Expected during: multi-tab (machine is single-tab), brief dispatch window.
      // Uncomment to debug: console.debug('[StreamMachine] divergence:', { isStreaming, mode: streamState.mode });
    }
  }

  // ── MessageStore subscription: store → React state bridge ──────────────
  // When the active tab has a store, subscribe to it. On notify (rAF-gated),
  // sync store.messages → setMessages (React render) + tabState.messages (cache).
  // This is the SINGLE path from store to UI during streaming — stream handlers
  // write to store only, never call setMessages() directly.
  useEffect(() => {
    const tabId = activeTabIdRef.current;
    if (!tabId) return;
    const store = messageStoreRegistry.get(tabId);
    if (!store) return;

    // Fix #3 (adversarial): Immediate sync on subscription — hydrate React state
    // with current store contents. Without this, there's a gap between tab switch
    // and first store notification where updates are invisible.
    setMessages(store.getSnapshot());

    const unsub = store.subscribe(() => {
      // Fix #2 (adversarial): Read ref INSIDE callback — not from closure.
      // Between effect setup and callback fire, active tab may have changed.
      // Guard BOTH writes to prevent flashing wrong-tab content during the
      // rAF window between effect cleanup scheduling and actual unsub.
      const currentActiveTabId = activeTabIdRef.current;

      // Always update tabState cache (for instant display on tab switch)
      const tabState = tabMapRef.current.get(tabId);
      if (tabState) {
        tabState.messages = store.messages;
      }

      // STRICT tab identity check for React state writes.
      // Never push a background tab's store into the displayed UI.
      // The app-restart stale-ref case is handled by the tab-switch effect
      // which calls setMessages(store.getSnapshot()) on subscribe (line above).
      if (currentActiveTabId !== tabId) return;

      // ── OT01 render-loss diagnostic (opt-in via isOt01DiagEnabled; run_3451bbd1) — STORE side ─
      // Gated on isOt01DiagEnabled() (DEV, or localStorage SWARM_OT01_DIAG=1 in
      // prod) — NOT import.meta.env.DEV, which is tree-shaken dead in the prod
      // .app where the bug actually happens (run_3451bbd1).
      // The STORE half of a store-vs-render pair. The render half lives in
      // AssistantMessageView ([OT01-diag] render). Compare the two in the live
      // console for the SAME msgId: if `storeTextBlocks` here ever exceeds the
      // `renderedTextBlocks` the view logs for that msgId, the loss is
      // DOWNSTREAM of this store→React push (React batch / throttle / memo at
      // the view) — the single-tab layer no headless test exposes. Aimed per
      // adversarial review (the prior active-tab-mismatch probe was unreachable
      // in the single-tab repro). getSnapshot() is a shallow copy so a
      // store-vs-snapshot content compare is vacuous (PIT37) — hence comparing
      // against the RENDER, not the snapshot. Removed once the layer is found.
      if (isOt01DiagEnabled()) {
        const sLast = store.messages[store.messages.length - 1];
        if (sLast?.role === 'assistant') {
          const sText = sLast.content.filter((b) => b.type === 'text').length;
          console.debug('[OT01-diag] store→React sync', {
            msgId: sLast.id, storeBlocks: sLast.content.length,
            storeTextBlocks: sText, isStreaming: store.phase === 'streaming',
          });
        }
      }

      // Sync store → React state (triggers render)
      setMessages(store.getSnapshot());
    });

    return unsub;
  }, [activeTabIdCurrent, tabMapRef, activeTabIdRef]); // re-subscribe on tab change

  // SESSION_BUSY recovery: polling state (intervals stored per-tab in UnifiedTab)
  const [isWaitingForBusy, setIsWaitingForBusy] = useState(false);

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

  // ── Streaming state reconciliation (all tabs) — ALWAYS-ON ──
  // Safety net: if ANY frontend tab thinks isStreaming=true but backend has
  // already transitioned to idle, force-clear that tab's streaming state.
  // Catches SSE events lost due to disconnect, tab switch races, or event
  // reader failures.
  //
  // ALWAYS-ON: runs unconditionally while the hook is mounted. The previous
  // design gated on `anyTabStreaming` (active-tab state + pendingStreamTabs).
  // That missed background tabs whose isStreaming flag lives purely in
  // tabMapRef (a ref, invisible to React state). When the active tab finishes
  // AND pendingStreamTabs is empty, the effect tore down — leaving stuck
  // background tabs (e.g. sub-agent streams) unrescued forever.
  //
  // Design: iterates ALL tabs in tabMapRef (not just active). Guards against
  // race with legitimately-restarted streams via _reconcileStreamStart
  // timestamp (skips tabs where stream started <10s ago).
  // Cost: one GET /streaming-state every 15s (~1KB). Acceptable.
  useEffect(() => {
    const RECONCILE_DELAY_MS = 5_000;
    // B-1 (OT01 north-star): cadence is now streaming-aware via
    // nextReconcileDelay() — 3s while any tab streams (so a lost SSE event
    // self-heals in ≤3s, AC5), 15s when idle (cheap safety net, AC2). The loop
    // self-reschedules with setTimeout instead of a fixed setInterval, because a
    // running setInterval's period cannot be changed. Authority is unchanged:
    // the per-tick arm/clear verdicts already drive the spinner from backend
    // state — only the POLL CADENCE moved (Gate-1 finding: arm/clear already shipped).
    let timer: ReturnType<typeof setTimeout> | null = null;
    let nextTick: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const reconcile = async () => {
      if (cancelled) return;
      try {
        const states = await chatService.getStreamingState();
        if (cancelled) return;

        let anyCleared = false;

        // Check ALL tabs, not just active — background tabs can hang too
        for (const [tabId, tabState] of tabMapRef.current.entries()) {
          // ── STORE↔REACT DESYNC GUARD (run_1a264fd1) ──────────────────────────
          // The frozen-tab bug: the MessageStore watchdog (or any silent
          // endStreaming) flips the store to phase='idle', but tabState.isStreaming
          // stayed true — the spinner is driven solely by isStreaming (TabView),
          // and nothing bridged the store phase back to it. Result: a tab frozen
          // mid-response with NO indicator until the 120min cap. endStreaming now
          // _notify()s, but the authoritative cross-check is here: if the store
          // is idle yet React still thinks streaming, the turn is OVER — converge
          // the flag. Convergent + cause-agnostic (heals ANY desync source), fires
          // ONLY on the actual divergence edge (no churn when in sync), and covers
          // background tabs. Turns a 120min freeze into ≤15s.
          // GRACE (Gate-2 HIGH): a send sets isStreaming=true and stamps
          // _reconcileStreamStart, THEN `await buildContentArray` (attachment
          // file/network I/O, ChatPage.tsx:1876) runs BEFORE store.startStreaming
          // sets phase='streaming'. In that window isStreaming=true && phase=idle
          // is LEGITIMATE (turn starting), not a desync. Reuse the same ≥10s grace
          // the force-clear path uses (line ~1158) so a just-started turn is never
          // cleared. A real watchdog-fire desync is always ≥45s old (the watchdog
          // timeout), so the grace excludes ONLY the start window, not the fix.
          if (tabState.isStreaming) {
            const dsStore = messageStoreRegistry.get(tabId);
            const dsStartAge = Date.now() - (tabState._reconcileStreamStart ?? 0);
            // OT01 sibling-path guard: the MessageStore watchdog flips phase→idle
            // at 90s, but a long turn whose SSE dropped is STILL flushing the
            // answer into the DB (backend alive, unit clean-idle). Converging the
            // spinner here would drop it mid-flush — the exact truncated-content
            // bug, just surfacing on THIS branch instead of the force-clear branch.
            // The desync decision is now backend-state-gated (same alive signal as
            // forceClearStreamVerdict): converge only when the store is idle AND
            // the backend is NOT alive (streaming/waiting_input/flushing), bounded
            // by the same 120-min cap so a stuck flag still converges.
            const dsBackend = tabState.sessionId ? states[tabState.sessionId] : undefined;
            const dsConverge = dsStore
              ? desyncConvergeVerdict({
                  storeIdle: dsStore.phase === 'idle',
                  streamStartAge: dsStartAge,
                  backendIsStreaming: dsBackend?.streaming ?? false,
                  backendWaitingInput: dsBackend?.state === 'waiting_input',
                  postDisconnectFlushing: dsBackend?.postDisconnectFlushing ?? false,
                })
              : false;
            if (dsConverge) {
              setIsStreaming(false, tabId);
              const dsSid = tabState.sessionId;
              if (dsSid) scheduleTurnEndReconcile(dsSid, tabId);
              anyCleared = true;
              continue; // desync resolved this tick; other branches re-eval next poll
            }
          }

          // POST-DISCONNECT RECOVERY: a tab whose SSE dropped on a long turn is
          // NOT streaming (heal-grace expiry cleared isStreaming) but is pinned
          // into queue-only mode via _postDisconnectUncertain. The backend
          // subprocess may still be finishing. Reconcile is the ONLY owner that
          // can clear this flag — the normal-send clear (ChatPage) is unreachable
          // because the flag itself forces the queue path. So: when the backend
          // confirms the session is genuinely idle, clear the flag and drain any
          // queued message. Without this the tab is bricked (queues forever).
          if (!tabState.isStreaming && tabState._postDisconnectUncertain && !tabState.isWaitingForBusy) {
            // Time cap (matches the streaming-path 120min cap): if stopSession
            // failed AND the backend never transitions out of streaming
            // (is_generating_after_disconnect stuck), the flag would never clear
            // and the tab would be bricked. After the cap, force-clear regardless
            // of reported backend state.
            const pdAge = Date.now() - (tabState._postDisconnectAt ?? 0);
            const pdCapExceeded = pdAge > 7_200_000; // 120 min

            const pdSid = tabState.sessionId;
            const pdBackend = pdSid ? states[pdSid] : undefined;
            const pdStreaming = pdBackend?.streaming ?? false;
            const pdActive = pdBackend?.state === 'waiting_input' || pdBackend?.state === 'streaming';
            // Honest-signal (OT01): the unit is CLEAN-IDLE (streaming=false) but its
            // subprocess is still finishing a long turn post-disconnect. Treat that
            // as "still alive" so we keep waiting (re-pulling DB each tick) instead
            // of resolving — resolving here is what surfaced the false "Connection
            // lost" error while the answer was still being produced. Fail-safe:
            // undefined (older backend) → false → behavior identical to before.
            const pdFlushing = pdBackend?.postDisconnectFlushing ?? false;

            if ((!pdStreaming && !pdActive && !pdFlushing) || pdCapExceeded) {
              // Backend idle (or evicted, or cap exceeded) — uncertainty resolved.
              // CRITICAL: only clear _postDisconnectUncertain when there is NO
              // queued message to drain. When a message IS queued, leave the flag
              // set and let drainQueuedMessage clear it on SUCCESS — so a failed
              // drain (which restores the queue) keeps the flag set and this block
              // retries on the next tick instead of orphaning the restored queue.
              if (pdSid) {
                chatService.invalidateMessageCache(pdSid);
                chatService.getSessionMessagesReconcileTail(pdSid, RECONCILE_TAIL).then((msgs) => {
                  if (cancelled) return;
                  const store = messageStoreRegistry.getOrCreate(tabId, { sessionId: pdSid });
                  store.reconcile(msgs);
                  if (store.phase === 'idle') {
                    if (tabId === activeTabIdRef.current) setMessages(() => store.messages);
                    else tabState.messages = store.messages;
                  }
                }).catch(() => { /* best-effort DB sync */ });
              }
              if (tabState.queuedMessage && deps.onDrainQueue) {
                // Flag stays set; drainQueuedMessage clears it on success.
                tabState.drainPending = true;
                setTimeout(() => { if (!cancelled) deps.onDrainQueue?.(tabId); }, 100);
              } else {
                // Nothing queued — pure idle recovery, safe to clear now.
                tabState._postDisconnectUncertain = false;
              }
              anyCleared = true;
            }
            continue;  // handled (or backend still busy — wait for next poll)
          }

          // ── Root-1 SSOT Phase 3: mirror the authoritative read API ──────────
          // Runs for EVERY tab (including non-streaming) BEFORE the streaming-only
          // guard below, because a lost AskUserQuestion leaves the tab NOT
          // streaming (the SSE event that would set waiting_input was dropped).
          const mirrorSid = tabState.sessionId;
          const mirrorState = mirrorSid ? states[mirrorSid] : undefined;
          if (mirrorState) {
            // AC5: re-surface a lost AskUserQuestion from authoritative state.
            // Gated + idempotent (shouldResurfaceQuestion) so the 15s poll can't
            // flap the question UI, and an answer-in-flight is suppressed.
            if (
              shouldResurfaceQuestion({
                backendWaitingInput: mirrorState.waitingInput,
                backendPendingQuestion: mirrorState.pendingQuestion,
                currentPendingToolUseId: tabState.pendingQuestion?.toolUseId ?? null,
                answeredToolUseId: tabState._answeredToolUseId ?? null,
              })
            ) {
              const pqPayload = mirrorState.pendingQuestion!;
              // The assistant message to attach the block to: the last assistant
              // bubble in this tab's store (or messages cache fallback).
              const store = messageStoreRegistry.get(tabId);
              const msgs = store?.messages ?? tabState.messages ?? [];
              // Use the file's findLast helper (line 174) — avoids the
              // [...arr].reverse().find() copy-per-call the helper exists to prevent.
              const lastAssistant = findLast(msgs, (m) => m.role === 'assistant');
              if (lastAssistant) {
                const isActive = tabId === activeTabIdRef.current;
                surfacePendingQuestion(
                  tabId,
                  lastAssistant.id,
                  { toolUseId: pqPayload.toolUseId, questions: pqPayload.questions },
                  { isActive, sessionId: mirrorSid },
                );
                // The inline form now hosts the question — retire any no-host
                // discovery toast we raised on an earlier poll (else it coexists
                // with the form). surfacePendingQuestion raises its OWN bg-tab
                // toast, so only clear the no-host one we tracked here.
                if (tabState._pendingQuestionToastId) {
                  removeToast(tabState._pendingQuestionToastId);
                  tabState._pendingQuestionToastId = undefined;
                }
                anyCleared = true;
              } else {
                // No assistant bubble to host the block (question's SSE was lost
                // before any assistant content). surfacePendingQuestion needs a
                // host message, so we can't render the form inline — but the user
                // must still be able to DISCOVER the pending question. Raise the
                // persistent "Swarm is asking" toast (idempotent via stable id)
                // so the tab is not a silent dead-end every 15s poll. Track its id
                // so it can be retired when the form takes over or the question is
                // abandoned (else the persistent toast leaks forever).
                const askTabTitle = tabMapRef.current.get(tabId)?.title ?? 'another tab';
                const toastId = `ask-uq-${pqPayload.toolUseId}`;
                addToast({
                  severity: 'info',
                  message: `Swarm is asking a question in "${askTabTitle}".`,
                  id: toastId,
                  autoDismiss: false,
                  action: onSelectTab ? { label: 'Go to tab', onClick: () => onSelectTab(tabId) } : undefined,
                });
                tabState._pendingQuestionToastId = toastId;
              }
            } else if (
              !mirrorState.waitingInput &&
              (tabState.pendingQuestion || tabState._pendingQuestionToastId)
            ) {
              // SYMMETRIC RETIRE (mirror is authoritative both ways): the backend
              // is no longer waiting on a question, but the tab still shows one —
              // it was answered elsewhere, abandoned (new chat), or its
              // tool_use resolved. The loop must CLEAR stale questions, not only
              // ADD them, or a dismissed/answered question lingers as a phantom
              // un-answerable prompt. Clear the ref (all tabs) + React state (active)
              // + any leaked no-host discovery toast.
              tabState.pendingQuestion = null;
              tabState._answeredToolUseId = undefined;
              if (tabState._pendingQuestionToastId) {
                removeToast(tabState._pendingQuestionToastId);
                tabState._pendingQuestionToastId = undefined;
              }
              if (tabId === activeTabIdRef.current) setPendingQuestion(null);
              anyCleared = true;
            } else if (
              // Clear the answer-in-flight guard once the backend has moved past
              // the answered question (still waiting_input but a DIFFERENT id).
              tabState._answeredToolUseId &&
              mirrorState.pendingQuestion?.toolUseId !== tabState._answeredToolUseId
            ) {
              tabState._answeredToolUseId = undefined;
            }

            // AC4: mirror the server's drain progress. Retire the local optimistic
            // queue mirror once the server confirms it drained a tracked seq.
            // NOTE: the session-level "N queued" badge DRIVEN BY pending_count is
            // deferred to the chat-tab-view-isolation track (it owns TabView's
            // indicator render — see PLAN boundaries.ask_first). We therefore do
            // NOT store pending_count on tabState here — a written-but-unread field
            // is dead code (GUI82). The functional half (drain retirement) uses
            // drain.serverPendingCount locally below; the display half lands with
            // the isolation track, which will add the field + write + reader atomically.
            const drain = computeDrainRetirement({
              priorDrainedSeqs: tabState._lastDrainedSeqs ?? [],
              currentDrainedSeqs: mirrorState.lastDrainedSeqs,
              serverPendingCount: mirrorState.pendingCount,
            });
            if (drain.retire) {
              // ── B1: server-side drain SURFACE (the missing wire) ───────────
              // A server-side drain delivered a coalesced turn to the DB. Root-1
              // DEFERRED live re-attach (design Scenario 7 / F8): drain_pending
              // consumes and DISCARDS the live SSE events, so the drained turn's
              // user+assistant content exists ONLY in the DB. The design's stated
              // recovery — "completed content loads from DB via the existing
              // message reconcile" — was never wired for this case: the only
              // DB-fetch path below sits behind `if (!isStreaming) continue`, which
              // a drained (idle) tab never reaches. Without this block the drained
              // response is invisible until reload/tab-switch — the "queue doesn't
              // auto-continue" dead-end. This block runs ABOVE the isStreaming gate
              // so it fires for idle drained tabs, and is the authoritative mirror
              // surface for server-owned drains (no local re-send → no double-send).
              const drainSid = tabState.sessionId;
              const observedSeqs = mirrorState.lastDrainedSeqs;
              // The synthetic 'queued-<uuid>' optimistic bubble (if the local
              // queue path created one) must be REMOVED, not merely un-badged:
              // the DB reconcile below brings the canonical coalesced user row, so
              // keeping the synthetic bubble would duplicate the user's text.
              const retireId = (tabState.queuedMessage && drain.serverPendingCount === 0)
                ? tabState.queuedMessage.messageId
                : null;
              if (drainSid) {
                chatService.invalidateMessageCache(drainSid);
                chatService.getSessionMessagesReconcileTail(drainSid, RECONCILE_TAIL).then((msgs) => {
                  if (cancelled) return;
                  const s = messageStoreRegistry.getOrCreate(tabId, { sessionId: drainSid });
                  // Remove the optimistic bubble only now (fetch succeeded) so a
                  // failed fetch leaves it intact for the next tick to retry.
                  if (retireId) s.remove((m) => m.id === retireId);
                  // reconcile is phase-gated + dedups by id: NO-OP if streaming
                  // restarted, preserves any other local-only messages.
                  s.reconcile(msgs);
                  if (s.phase === 'idle') {
                    if (tabId === activeTabIdRef.current) setMessages(() => s.messages);
                    else tabState.messages = s.messages;
                  }
                  // Mark surfaced ONLY on success — defer the prior-seqs update so a
                  // failed fetch re-fires `drain.retire` next tick (retry, P2: no
                  // drained response lost to a transient fetch error).
                  tabState._lastDrainedSeqs = observedSeqs;
                  if (retireId) {
                    tabState.queuedMessage = undefined;
                    tabState._queuedAt = undefined;
                  }
                }).catch(() => { /* best-effort; _lastDrainedSeqs NOT advanced → retry next tick */ });
              } else {
                // No session id to fetch from — nothing to surface; just advance.
                tabState._lastDrainedSeqs = observedSeqs;
              }
              anyCleared = true;
            } else {
              tabState._lastDrainedSeqs = mirrorState.lastDrainedSeqs;
            }
          }

          // ── Symmetric re-arm: backend streaming but tab shows idle ─────────
          // The mirror is authoritative BOTH ways. The force-clear below handles
          // frontend=streaming + backend=idle (the stuck-spinner case). This
          // handles the OPPOSITE: backend IS streaming but the tab shows no
          // spinner — which happens after a RESTART (restored tabs init
          // isStreaming=false while their backend session resume-streams) or a
          // lost session_start SSE. Without this the response renders (via the DB
          // reconcile above) with NO spinner. Self-healing: once the backend goes
          // idle the force-clear branch turns it back off, so a wrong re-arm can
          // never become a permanent hang.
          if (
            shouldArmSpinnerFromBackend({
              backendStreaming: mirrorState?.streaming === true,
              tabIsStreaming: tabState.isStreaming,
              hasSessionId: !!mirrorSid,
              postDisconnectUncertain: !!tabState._postDisconnectUncertain,
              isWaitingForBusy: !!tabState.isWaitingForBusy,
              hasQueuedMessage: !!tabState.queuedMessage,
              streamClearedAt: tabState._streamClearedAt,
              now: Date.now(),
            })
          ) {
            console.warn(
              '[StreamReconcile] Backend streaming but tab idle — arming spinner',
              { tabId, sessionId: mirrorSid },
            );
            // Atomic primitive (flag + Set + re-render + state machine).
            // NEVER mutate tabState.isStreaming directly.
            setIsStreaming(true, tabId);
            continue;  // re-armed — nothing else to reconcile for this tab this tick
          }

          if (!tabState.isStreaming) continue;  // only check streaming tabs

          // DRAIN/QUEUE IMMUNITY: never force-clear a tab that has a pending
          // drain or queued message. The "backend=IDLE + frontend=streaming"
          // state is INTENTIONAL during the drain gap (result arrived, drain
          // scheduled via setTimeout(0), new stream not yet started).
          // EXCEPTION: if queue has been waiting >60s, the "drain gap"
          // explanation no longer applies — SSE event was likely lost.
          // Without this override, queuedMessage creates a deadlock:
          // streaming(wrong) → queue msg → reconcile skips → never clears.
          const queueAge = tabState._queuedAt ? Date.now() - tabState._queuedAt : 0;
          const sid = tabState.sessionId;
          if (!sid) { tabState._idleStreamingSince = undefined; continue; }
          const backendState = states[sid];

          // Decision extracted to forceClearStreamVerdict (pure, test-locked in
          // streaming-guards.test.ts: "backend streaming → NEVER force-clear" is
          // the load-bearing invariant). The hook only APPLIES side effects per
          // verdict — the stuck-vs-live judgement lives in the tested function.
          const { verdict } = forceClearStreamVerdict({
            drainPending: !!tabState.drainPending,
            hasQueuedMessage: !!tabState.queuedMessage,
            queueAge,
            hasSessionId: true,
            backendIsStreaming: backendState?.streaming ?? false,
            reportedState: backendState?.state,
            resumeInProgress: !!tabState.isResuming,
            // OT01: backend clean-idle but still flushing a long turn post-disconnect
            // (heal-grace kept the spinner; do not force-clear mid-flush).
            postDisconnectFlushing: backendState?.postDisconnectFlushing ?? false,
            activeGuardAge: Date.now() - (tabState._reconcileStreamStart ?? 0),
            idleStreamingSince: tabState._idleStreamingSince,
            // OT01 AC1: churn-immune absolute bound. Cleared on the streaming→idle
            // edge (setIsStreaming false), so it is only non-undefined during a
            // live span; the verdict consults it ONLY after all four alive guards.
            streamingSinceHardStart: tabState._streamingSinceHardStart,
            now: Date.now(),
          });

          if (verdict === 'reset-and-skip') {
            // Condition does NOT hold (drain gap / fresh queue / backend
            // streaming / active state). Clear the reconcile-owned backstop
            // clock so a later genuine stall restarts the 30s window fresh.
            tabState._idleStreamingSince = undefined;
            continue;
          }
          if (verdict === 'wait-settle') {
            // Stuck but within the settle window — stamp on first observation,
            // keep aging the clock (touched ONLY here, so reconnect/heal churn
            // can't postpone the deadline forever).
            if (tabState._idleStreamingSince === undefined) {
              tabState._idleStreamingSince = Date.now();
            }
            continue;
          }

          // verdict === 'force-clear': frontend=streaming, backend idle, settled.
          console.warn(
            '[StreamReconcile] Backend idle but tab streaming — forcing clear',
            { tabId, sessionId: sid, backendState: backendState?.state ?? 'evicted',
              hasQueuedMessage: !!tabState.queuedMessage },
          );
          // Use atomic primitive — handles flag + Set + re-render.
          // Direct mutation here was the root cause of background-tab hang.
          setIsStreaming(false, tabId);
          tabState.isReconnecting = false;
          tabState.isResuming = false;
          tabState._idleStreamingSince = undefined;  // condition resolved
          if (tabState._resumeTimeoutId) { clearTimeout(tabState._resumeTimeoutId); tabState._resumeTimeoutId = undefined; }
          anyCleared = true;

          // Auto-drain: if a queued message was waiting (deadlock scenario),
          // trigger drain now that streaming is cleared. Without this, the
          // user is unstuck (can type) but the already-queued message stays unsent.
          if (tabState.queuedMessage && deps.onDrainQueue) {
            // Schedule drain after a tick to let React state settle
            setTimeout(() => { if (!cancelled) deps.onDrainQueue?.(tabId); }, 100);
          }

          // Sync messages from DB for this tab (content is there, event was lost).
          // Phase-gated via MessageStore.reconcile(): preserves local-only
          // messages (queued, synthetic) and respects streaming gate.
          chatService.invalidateMessageCache(sid);
          chatService.getSessionMessagesReconcileTail(sid, RECONCILE_TAIL).then((msgs) => {
            if (cancelled) return;
            // Route through store — reconcile handles dedup by ID, preserves
            // local-only queued messages, and phase-gates (NO-OP if streaming
            // restarted between fetch and resolve).
            const store = messageStoreRegistry.getOrCreate(tabId, { sessionId: sid });
            store.reconcile(msgs);
            // Only sync back if reconcile actually executed (not queued).
            if (store.phase === 'idle') {
              if (tabId === activeTabIdRef.current) {
                setMessages(() => store.messages);
              } else {
                tabState.messages = store.messages;
              }
              // Recovery succeeded — clear any prior failure flag.
              tabState._dbReconcileFailed = false;
            }
          }).catch((err) => {
            console.warn('[useChatStreamingLifecycle] Recovery sync failed:', err);
            // Backend unreachable — the force-clear left a frozen partial.
            // Flag for retry; the backend-recovered handler re-reconciles
            // from DB once the daemon is back.
            tabState._dbReconcileFailed = true;
          });
        }

        // Force re-render if any tab was cleared
        if (anyCleared) {
          setPendingStreamTabs((prev) => {
            const next = new Set(prev);
            // Remove tabs that are no longer streaming OR no longer exist
            for (const id of next) {
              const ts = tabMapRef.current.get(id);
              if (!ts || !ts.isStreaming) next.delete(id);
            }
            return next;
          });
        }
      } catch (err) {
        // API unavailable — log for observability but don't block
        console.warn('[StreamReconcile] poll failed:', err);
      }
    };

    // Self-rescheduling poll: after each reconcile, pick the next delay from
    // whether any tab is currently streaming. Fast (3s) under streaming so the
    // backend alive predicate drives the spinner promptly; slow (15s) when idle.
    const scheduleNext = () => {
      if (cancelled) return;
      let anyStreaming = false;
      for (const ts of tabMapRef.current.values()) {
        if (ts.isStreaming) { anyStreaming = true; break; }
      }
      nextTick = setTimeout(runReconcile, nextReconcileDelay(anyStreaming));
    };
    const runReconcile = async () => {
      if (cancelled) return;
      await reconcile();
      scheduleNext();
    };

    timer = setTimeout(() => {
      if (cancelled) return;
      void runReconcile();
    }, RECONCILE_DELAY_MS);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (nextTick) clearTimeout(nextTick);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally always-on, no deps trigger restart
  }, []);

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
          // ── Per-tab state machine dispatch (P5) ──
          // Dispatch to the tab's own streamState. Coarse events: SEND_MESSAGE
          // on true-from-idle, USER_STOP on false-from-active. More specific
          // events (SESSION_START, RESULT, etc.) dispatch at their semantic sites.
          const tabMode = tabState.streamState.mode;
          if (streaming && tabMode === 'idle') {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'SEND_MESSAGE' });
          } else if (!streaming && tabMode !== 'idle' && tabMode !== 'error'
            && tabMode !== 'waiting_input' && tabMode !== 'permission_needed') {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'USER_STOP' });
          }

          // AUTHORIZED WRITER: readonly bypass — this is the ONLY place
          // that may mutate isStreaming. All other paths get TS2540.
          (tabState as { isStreaming: boolean }).isStreaming = streaming;
          if (streaming) {
            tabState._reconcileStreamStart = Date.now();
            // OT01 hard-cap clock (AC1): stamp SET-ONCE per continuous streaming
            // span. Unlike _reconcileStreamStart (re-stamped on every arm, incl.
            // the reconcile re-arm + post-disconnect handoff — Gate-1 Q3 churn),
            // this is set only on the FIRST true-edge and left untouched until the
            // span ends, so abort+recycle churn that re-enters setIsStreaming(true)
            // cannot postpone the absolute deadline. Cleared on the false-edge below.
            if (tabState._streamingSinceHardStart === undefined) {
              tabState._streamingSinceHardStart = Date.now();
            }
            // Record stream start time for elapsed timer on tab switch
            if (!tabState.streamStartTime) {
              tabState.streamStartTime = Date.now();
            }
          } else {
            tabState.streamStartTime = undefined;
            // OT01 hard-cap clock: clear on the streaming→idle edge so the NEXT
            // genuine turn re-stamps fresh (no stale deadline carried across turns).
            tabState._streamingSinceHardStart = undefined;
            // Flap-guard stamp: the reconcile loop's idle→streaming re-arm skips
            // a tab cleared within the settle window, so a Stop (frontend idle)
            // is not re-lit during the ~5s before the backend transitions to IDLE.
            tabState._streamClearedAt = Date.now();
          }
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
    [], // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs
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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /**
   * Schedule a turn-end DB reconcile — the single authoritative backstop that
   * corrects a streamed buffer against the canonical DB rows at turn end.
   *
   * WHY this is shared: the streamed display buffer can drop a tail block (rAF
   * coalescing, a lost text_delta/assistant SSE event). The backend persists
   * every assistant block immediately (crash-safe), so the DB always holds the
   * complete content. Fetching + reconciling at turn-end repairs any truncation.
   *
   * THIS MUST FIRE ON EVERY TERMINAL PATH, not just `result`. A turn that ends
   * via ask_user_question (waiting_input) or cmd_permission_request
   * (permission_needed) emits NO `result` event — before this was extracted, those
   * paths had no backstop, so a truncated reply stayed truncated with an idle
   * Continue button (reconcile-gap, 2026-06-22).
   *
   * Properties:
   *  - 200ms-debounced per tab (timer stored on tabState._turnEndReconcileTimer)
   *  - phase-gated inside store.reconcile() → NO-OP if streaming restarted
   *  - fetches FRESH (invalidateMessageCache first), fire-and-forget (non-fatal)
   *  - syncs React state only if the tab is still active (cross-tab isolation)
   *  - preserves local-only interactive blocks via _applyMerge (the question /
   *    permission form is frontend-synthesized, never in the DB — see PART 1)
   */
  const scheduleTurnEndReconcile = useCallback(
    (sid: string | undefined, tabId: string | null | undefined) => {
      if (!sid || !tabId) return;
      const store = messageStoreRegistry.get(tabId);
      if (!store) return;
      const tabState = tabMapRef.current.get(tabId);
      if (tabState?._turnEndReconcileTimer) {
        clearTimeout(tabState._turnEndReconcileTimer);
      }
      const reconcileSid = sid;
      const reconcileTabId = tabId;
      const timer = setTimeout(() => {
        chatService.invalidateMessageCache(reconcileSid);
        chatService.getSessionMessagesReconcileTail(reconcileSid, RECONCILE_TAIL).then((msgs) => {
          const s = messageStoreRegistry.get(reconcileTabId);
          if (!s) return;
          s.reconcile(msgs);
          // Only sync React/cache if reconcile actually executed (phase=idle).
          // If a new stream restarted in the 200ms window, reconcile() queued a
          // thunk (NO-OP now) and the snapshot is mid-stream — pushing it would
          // be a stale render. Matches the phase-gate used at every other
          // reconcile sync site in this file.
          if (s.phase === 'idle') {
            if (reconcileTabId === activeTabIdRef.current) {
              setMessages(s.getSnapshot());
            }
            const ts = tabMapRef.current.get(reconcileTabId);
            if (ts) ts.messages = s.messages;
          }
        }).catch(() => { /* non-fatal: next turn re-reconciles */ });
      }, 200);
      if (tabState) tabState._turnEndReconcileTimer = timer;
    },
    [setMessages], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
  );

  /**
   * Surface an AskUserQuestion into a tab — the single authoritative path for
   * rendering a question, shared by the live SSE handler AND the reconcile-loop
   * re-surface (Root-1 SSOT Phase 3, AC5). Extracting this (Gate-1 4a) prevents
   * a partial re-implementation: a question is only answerable if ALL of these
   * happen together, so the lost-SSE re-surface must do exactly what the live
   * path does — not just set `pendingQuestion`.
   *
   * The 6 parts (each load-bearing):
   *  1. state-machine transition STREAMING→WAITING_INPUT (global + per-tab)
   *  2. append the `ask_user_question` content block to the assistant message
   *     (ContentBlockRenderer renders the form FROM this block — it is NOT in
   *     the DB, so a reconcile from getSessionMessages will not restore it)
   *  3. ref write `tabState.pendingQuestion` (per-tab source of truth)
   *  4. active-tab ONLY: `setPendingQuestion` (React render) — PIT76: never for
   *     a background tab, or its question leaks into the active tab's state
   *  5. end streaming phase + status → waiting_input
   *  6. background tab → "go answer" toast; persist pending state to sessionStorage
   */
  const surfacePendingQuestion = useCallback(
    (
      tabId: string,
      assistantMessageId: string,
      pq: PendingQuestion,
      opts: { isActive: boolean; sessionId?: string },
    ) => {
      const { isActive } = opts;
      const tabState = tabMapRef.current.get(tabId);

      // (1) State-machine transition (global only when active; per-tab always)
      if (isActive) dispatch({ type: 'ASK_USER_QUESTION' });
      if (tabState) {
        tabState.streamState = streamingReducer(tabState.streamState, { type: 'ASK_USER_QUESTION' });
      }

      // (2) Append the ask_user_question block to the assistant message via the
      // store (single-writer). The block is what ContentBlockRenderer renders.
      const auqBlock = {
        type: 'ask_user_question' as const,
        toolUseId: pq.toolUseId,
        questions: pq.questions,
      };
      const store = messageStoreRegistry.get(tabId);
      if (store) {
        // Idempotent: only append if this message doesn't already carry the block.
        const alreadyHasBlock = store.messages.some(
          (m) => m.id === assistantMessageId &&
            m.content.some((b) => (b as { type?: string; toolUseId?: string }).type === 'ask_user_question'
              && (b as { toolUseId?: string }).toolUseId === pq.toolUseId),
        );
        if (!alreadyHasBlock) {
          store.updateLast(
            (msg) => ({ ...msg, content: [...msg.content, auqBlock] }),
            (msg) => msg.id === assistantMessageId,
          );
          // Parallel-write: mirror the block into tabState.messages too. Post
          // reconcile-gap fix (run_9db9f987) the STORE is the single render
          // source and switch-back no longer clobbers a populated store, so this
          // mirror is NO LONGER needed to survive a reverse-flow replace. It is
          // retained because persistPendingState below serializes
          // tabState.messages — the mount-restore (and the cold-restore seed)
          // needs the block present in the tabState snapshot. Keeping store and
          // tabState in sync is belt-and-suspenders, not a clobber-guard.
          if (tabState) tabState.messages = store.messages;
        }
      } else if (tabState) {
        const already = tabState.messages.some(
          (m) => m.id === assistantMessageId &&
            m.content.some((b) => (b as { type?: string; toolUseId?: string }).type === 'ask_user_question'
              && (b as { toolUseId?: string }).toolUseId === pq.toolUseId),
        );
        if (!already) {
          tabState.messages = tabState.messages.map((msg) =>
            msg.id === assistantMessageId ? { ...msg, content: [...msg.content, auqBlock] } : msg,
          );
          if (isActive) setMessages(tabState.messages);
        }
      }

      // (3) Per-tab ref write (always — even background tabs, so a later switch shows it)
      if (tabState) {
        tabState.pendingQuestion = pq;
        if (opts.sessionId) tabState.sessionId = opts.sessionId;
      }

      // (4) Active-tab ONLY React render (PIT76: never setPendingQuestion for bg tab)
      if (isActive) {
        setPendingQuestion(pq);
        if (opts.sessionId) setSessionId(opts.sessionId);
      }

      // (5) End streaming phase + status → waiting_input
      if (store) store.endStreaming();
      setIsStreaming(false, tabId);
      incrementStreamGen();
      updateTabStatus(tabId, 'waiting_input');

      // (5b) Turn-end DB reconcile (reconcile-gap): this terminal path emits NO
      // `result` event, so without this the streamed buffer (possibly missing a
      // tail block) is never corrected against the complete DB rows. The
      // synthesized ask_user_question block appended in (2) survives reconcile —
      // _applyMerge carries forward local-only interactive blocks (PART 1).
      scheduleTurnEndReconcile(tabState?.sessionId ?? opts.sessionId, tabId);

      // (6) Background tab → "go answer" toast (action, not auto-dismiss); persist
      if (!isActive) {
        const bgTabTitle = tabMapRef.current.get(tabId)?.title ?? 'another tab';
        addToast({
          severity: 'info',
          message: `Swarm is asking a question in "${bgTabTitle}".`,
          id: `ask-uq-${pq.toolUseId}`,
          autoDismiss: false,
          action: onSelectTab ? { label: 'Go to tab', onClick: () => onSelectTab(tabId) } : undefined,
        });
      }
      const persistSessionId = tabState?.sessionId ?? opts.sessionId;
      if (persistSessionId && tabState) {
        persistPendingState(persistSessionId, tabState.messages, pq);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refs + stable setters
    [dispatch, setMessages, setPendingQuestion, setSessionId, setIsStreaming, incrementStreamGen, updateTabStatus, addToast, onSelectTab, scheduleTurnEndReconcile],
  );

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
  }, [isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

  // --- Fix 9: Tick elapsed counter every second while streaming ---
  // Ticks for the entire streaming duration (both "Thinking..." and tool execution).
  // This gives users a time reference when tools (especially sub-agents) run long.
  useEffect(() => {
    if (!isStreaming) {
      // Not streaming — clear elapsed (unconditional; React bails out if already 0)
      setElapsedSeconds(0);
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
  }, [isStreaming]);  

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
      // Restore via store (single-writer) or fallback
      const tabId = activeTabIdRef.current;
      const restoreStore = tabId ? messageStoreRegistry.get(tabId) : null;
      if (restoreStore) {
        restoreStore.replace(restored.messages);
      } else {
        setMessages(restored.messages);
      }
      setPendingQuestion(restored.pendingQuestion);
      if (tabId) {
        const tabState = tabMapRef.current.get(tabId);
        if (tabState) {
          tabState.messages = restoreStore ? restoreStore.messages : restored.messages;
          tabState.pendingQuestion = restored.pendingQuestion;
        }
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only

  // --- Stream checkpoint recovery ---
  // If a stream checkpoint exists for the current session (persisted during
  // long streaming runs), and current messages are empty or shorter than the
  // checkpoint, restore from checkpoint. This recovers from the P0 "18 min
  // content disappeared" bug where React state gets cleared but sessionStorage
  // retains the accumulated content.
  //
  // IMPORTANT: This runs AFTER the pendingState restore (declared above).
  // Both are mount-only useEffects that fire in declaration order.
  // We use a setTimeout(0) to defer to the NEXT microtask — ensuring
  // the first useEffect's setMessages has flushed to messagesRef before
  // we compare lengths. This prevents stale checkpoint from overwriting
  // fresher pendingState data.
  useEffect(() => {
    setTimeout(() => {
      const currentSessionId = sessionIdRef.current;
      if (!currentSessionId) return;
      const key = `swarm_stream_checkpoint_${currentSessionId}`;
      try {
        const raw = window.sessionStorage.getItem(key);
        if (!raw) return;
        const { messages: checkpointMessages, timestamp } = JSON.parse(raw);
        // Only restore if checkpoint is recent (< 30 min) and has more content
        const age = Date.now() - (timestamp || 0);
        if (age > 30 * 60 * 1000) {
          window.sessionStorage.removeItem(key);
          return;
        }
        // Compare against CURRENT state (after pendingState restore has flushed)
        const currentMessages = messagesRef.current;
        if (checkpointMessages && checkpointMessages.length > currentMessages.length) {
          console.warn('[StreamCheckpoint] Restoring from checkpoint:', {
            checkpointLen: checkpointMessages.length,
            currentLen: currentMessages.length,
            ageSeconds: Math.round(age / 1000),
          });
          // Restore via store (single-writer) or fallback
          const tabId = activeTabIdRef.current;
          const cpStore = tabId ? messageStoreRegistry.get(tabId) : null;
          if (cpStore) {
            cpStore.replace(checkpointMessages);
          } else {
            setMessages(checkpointMessages);
            if (tabId) {
              const tabState = tabMapRef.current.get(tabId);
              if (tabState) tabState.messages = checkpointMessages;
            }
          }
        }
        // Clean up after restore attempt (whether restored or not — stale data)
        window.sessionStorage.removeItem(key);
      } catch {
        // Non-fatal — checkpoint recovery is best-effort
      }
    }, 0);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only

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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- mount-only

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
      // Capture stream generation at creation time — events from a stale
      // generation (e.g. old response arriving after user sent a new message)
      // must be discarded to prevent cross-turn bleed.
      const capturedStreamGen = streamGenRef.current;

      // OT01 (run_f9adee1e): stamp the tab's LIVE stream generation EAGERLY here,
      // at handler creation (send time, in every send path — the stream handler
      // is always created synchronously). The generation guard below compares
      // against latestStreamGen (advanced ONLY by a genuinely new send that
      // creates a new stream handler) NOT streamGen (churned mid-turn by
      // result/reconnect/error via incrementStreamGen). Without this, the turn's
      // own result-following tail events (context_warning, system_prompt_metadata)
      // were discarded as stale after `result` bumped streamGen → turn-end refresh
      // lost → UI frozen until the next send. Mirrors createCompleteHandler's
      // latestCompleteGen fix (run_6adee7d5) for the stream-event path.
      //
      // SIBLING NOW CLOSED (run_26aa6caa): the background-resend/reconnect retry path
      // (ChatPage resendTabOnRecovery → retryStreamFn) used to build its stream
      // handler via wrappedCreateStreamHandler (which reads activeTabIdRef.current),
      // so a BACKGROUND tab's resend stamped this tab's events onto the ACTIVE tab.
      // Both retryStreamFn sites now call createStreamHandler(id, capturedTabIdForRetry)
      // directly — symmetric with their already-correct error/complete/disconnect
      // handlers. The half-migration (R27) this note tracked is closed.
      if (capturedTabId) {
        const stampTab = tabMapRef.current.get(capturedTabId);
        if (stampTab) stampTab.latestStreamGen = capturedStreamGen;
      }

      // ── Activate store phase gate ──
      // This makes reconcile()/replace() structurally impossible during streaming.
      // The single structural protection layer — no more relying on scattered
      // `if (isStreaming) return` guards at each call site.
      if (capturedTabId) {
        const streamStore = messageStoreRegistry.get(capturedTabId);
        if (streamStore) {
          streamStore.startStreaming(assistantMessageId);
        }
      }

      return (event: StreamEvent) => {
        // ╔══════════════════════════════════════════════════════════════╗
        // ║ CRITICAL — watchdog liveness reset. Keep this FIRST.           ║
        // ╚══════════════════════════════════════════════════════════════╝
        // ANY inbound SSE event = the connection is alive. Reset this tab's
        // 90s force-end watchdog up front — BEFORE any type-check, the
        // recovery_exhausted/still_working early-returns, AND the generation
        // guard. The backend emits a heartbeat every 15s (and thinking_progress
        // during a think), so a long-but-LIVE operation (heavy cold --resume
        // that replays the transcript for minutes before the first token, a
        // multi-minute silent tool) must NEVER be force-ended while bytes are
        // still flowing. The watchdog then fires ONLY on true silence (no event
        // at all for 90s = dead stream).
        //
        // Why up here and not type-gated: the old wiring touched the watchdog
        // for heartbeat/thinking_progress AFTER the generation guard, so a
        // heartbeat dropped by that guard left the watchdog un-reset → it
        // force-ended a live resume mid-replay ("resume 看起来根本起不来"). touch()
        // is a NO-OP unless the store is streaming, so idle/stale pings are
        // harmless. Do NOT move this below the guards.
        if (capturedTabId) messageStoreRegistry.get(capturedTabId)?.touch();

        // recovery_exhausted is a pure side-channel signal (toast only — no
        // store/streamState writes) and is yielded from the self-heal block
        // AFTER the turn's `result` event, which has already bumped streamGen.
        // It MUST be handled BEFORE the generation guard below, or the guard
        // discards it as a stale-generation event and the toast never shows
        // (the feature would be dead for the foreground tab — adversarial #1,
        // run_d8dce02a). Handle-before-guard mirrors how file_changed-class
        // side-channel events must bypass the cross-turn-bleed guard.
        if (event.type === 'recovery_exhausted') {
          // Only actionable for a real, still-open tab: handleNewChat clears
          // the ACTIVE tab, so a 'Start fresh' on a closed/unknown tab would
          // wipe the wrong live tab (adversarial #3/#4). Require capturedTabId
          // to still exist before offering the action.
          const rexTabId = capturedTabId;
          const tabStillOpen = !!rexTabId && tabMapRef.current.has(rexTabId);
          const rexToastId = `recovery-exhausted-${rexTabId ?? 'global'}`;
          const msg = event.message
            ?? 'Automatic recovery for this session has stopped. Start a fresh session to continue.';
          addToast({
            severity: 'warning',
            message: msg,
            id: rexToastId,
            autoDismiss: false,
            action: (tabStillOpen && onStartFresh)
              ? {
                  label: 'Start fresh session',
                  onClick: () => {
                    removeToast(rexToastId);
                    onStartFresh(rexTabId);
                  },
                }
              : undefined,
          });
          return; // side-channel handled; never falls through to stream logic
        }

        // Long-running-turn notice. The backend emits this every
        // LONG_TURN_HEARTBEAT_S during a silent step (it carries elapsed time,
        // the oldest open tool, and a "press Stop to recover" hint). Surface it
        // in-tab so a multi-minute turn reads as "still working", not a dead
        // spinner. Handled HERE — before the generation guard and before the
        // lastRealEventRef update — so it (a) is never discarded as stale and
        // (b) does NOT reset the stall timer (a heartbeat is not SDK progress,
        // same exclusion as 'heartbeat'). Active tab only: a background tab's
        // notice would surface against the chat the user is actually viewing.
        // Keyed + auto-dismiss so successive notices replace in place (no
        // stacking) and clear on their own once the turn produces or ends.
        if (event.type === 'still_working') {
          // The 90s force-end watchdog is already reset at the TOP of this
          // handler for every event, so no touch() is needed here. This block
          // just surfaces the long-turn "still working" notice in-tab.
          // isActiveTab is derived further down; this side-channel runs early,
          // so compare against the live active-tab ref directly.
          if (capturedTabId === activeTabIdRef.current && event.message) {
            addToast({
              severity: 'info',
              message: event.message,
              id: `still-working-${capturedTabId ?? 'global'}`,
              autoDismiss: true,
            });
          }
          return; // side-channel handled; never falls through to stream logic
        }

        // ── HITL exemption (P0 run_3e404199) ─────────────────────────────
        // A terminal human-in-the-loop prompt (cmd_permission_request /
        // ask_user_question) is emitted by the backend ONLY when a PreToolUse
        // hook is CURRENTLY BLOCKED awaiting the user's decision — the
        // orchestrator checks has_live_waiter right before yielding
        // (streaming_orchestrator.py:495 for cmd_permission_request via
        // _pm_live; :457 for ask_user_question via _aqm), so an emitted HITL
        // prompt always had a live blocked hook at emit time. A dead/superseded
        // waiter is dropped at EMIT and never reaches here — so this exemption
        // has no frontend backstop behind it and needs none. Such a prompt is
        // NEVER "stale cross-turn bleed": the hook cannot proceed until answered.
        //
        // The gen-guard below discarded it because latestStreamGen advanced
        // (queued sends bumping the gen while the hook sat blocked — see the
        // session_busy_pending seq in the daemon log), leaving capturedStreamGen
        // (the blocked handler's gen) behind the live gen. Discarding = the
        // approve/deny button never renders → the hook blocks until
        // MESSAGE_TIMEOUT (~10min) → wait_for_permission_decision CANCELLED →
        // session force-killed to COLD. That is the reported hang.
        //
        // Exempt these two event types from the gen-discard. Worst case (the
        // waiter died between emit and render): the rendered button's approve
        // routes to chat.py which returns a graceful "No pending permission
        // request" error — strictly better than a silent 10-min hang. This is
        // same-tab only (the guard is keyed on capturedTabId); cross-tab
        // isolation is enforced separately by the isActiveTab setMessages gate.
        const _isTerminalHITL = (
          event.type === 'cmd_permission_request'
          || event.type === 'ask_user_question'
        );

        // Generation guard: discard events from a previous stream.
        // This prevents cross-turn bleed where stale SSE events from an
        // interrupted response arrive after a new stream has started.
        if (capturedTabId) {
          const currentTabState = tabMapRef.current.get(capturedTabId);
          // OT01 (run_f9adee1e): compare latestStreamGen (advanced ONLY by a
          // genuinely new send that creates a new stream handler), NOT streamGen
          // (churned mid-turn by result/reconnect/error). This lets the turn's
          // OWN result-following tail events (context_warning / system_prompt_
          // metadata) survive the guard while still discarding events from a
          // superseded stream. Fail-safe: an UNSET latestStreamGen is treated as
          // NON-authoritative (discard), never as a wildcard match — mirrors the
          // createCompleteHandler liveness gate (:3986). latestStreamGen is
          // stamped eagerly at handler creation above, so on the live turn it
          // always equals capturedStreamGen.
          const liveStreamGen = currentTabState?.latestStreamGen;
          if (currentTabState && !_isTerminalHITL && (liveStreamGen === undefined || liveStreamGen !== capturedStreamGen)) {
            // OT01 diag (AC5): a discarded event is the smoking gun for a lost
            // terminal event (isStreaming pins true). console.warn (NOT debug) so
            // logForwarder persists it to frontend.log. Fields per Gate-1 Q4:
            // capturedTabId vs activeTabIdRef.current disambiguates cross-tab from
            // own-turn; tab vs global gen tells which guard layer fired.
            // FLOOD GUARD (Gate-2 operational MED): a stale stream keeps yielding
            // buffered per-token text_delta/thinking_delta — one warn per token
            // would emit thousands in a burst and EVICT the high-value
            // [OT01-Complete] no-op warn from logForwarder's 500-entry queue /
            // 200-per-request cap. Per-token deltas carry no diagnostic value
            // beyond the first; only the TERMINAL/control event types (result,
            // error, session_*, ask/permission) are the smoking gun for a lost
            // [DONE]. Skip the warn for high-frequency content deltas; still
            // discard the event itself.
            const _diagNoiseTypes = new Set(['text_delta', 'thinking_delta', 'content_block_delta']);
            if (!_diagNoiseTypes.has(event.type)) {
              console.warn('[OT01-GenGuard] discard stale stream event', {
                eventType: event.type, capturedTabId,
                activeTab: activeTabIdRef.current,
                tabStreamGen: currentTabState.streamGen, capturedStreamGen,
                globalStreamGen: streamGenRef.current,
                sessionId: currentTabState.sessionId,
              });
            }
            return; // stale event — discard silently
          }
        } else if (!_isTerminalHITL && streamGenRef.current !== capturedStreamGen) {
          console.warn('[OT01-GenGuard] discard stale stream event (null-tab global path)', {
            eventType: event.type, capturedTabId: null,
            activeTab: activeTabIdRef.current,
            globalStreamGen: streamGenRef.current, capturedStreamGen,
          });
          return; // stale event — discard silently
        }

        // Guard: if tab was closed while stream was running, no-op
        const tabState = capturedTabId
          ? tabMapRef.current.get(capturedTabId)
          : undefined;

        // When capturedTabId is null (initial tab before registration), treat as active.
        // The null case only occurs for the first tab before initTabState fires.
        const isActiveTab = capturedTabId === null || capturedTabId === activeTabIdRef.current;

        // Mark that we've received data — used by reconnection logic AND the
        // post-stop silent-retry to distinguish connection-phase vs mid-stream
        // failures. Control/lifecycle events (session_start, session_resuming),
        // error events, AND heartbeats do NOT count as "data": otherwise a
        // connection-phase error that arrives right AFTER session_start (e.g.
        // the backend pipe-flush from a Stop kills the freshly-spawned
        // subprocess on the next send) would flip hasReceivedData=true and
        // defeat the silent-retry, surfacing a spurious "Connection interrupted
        // — send again" and forcing a manual resend after every Stop. Heartbeat
        // is excluded for the same reason: it now reaches this handler (to reset
        // the watchdog) but it is liveness, not real stream content.
        const isDataEvent = event.type !== 'session_start'
          && event.type !== 'session_resuming'
          && event.type !== 'error'
          && event.type !== 'heartbeat';
        if (tabState && !tabState.hasReceivedData && isDataEvent) {
          tabState.hasReceivedData = true;

          // ── Self-heal grace: data arrived → heal succeeded silently ──
          // If we were in a heal grace window (backend killed+respawned),
          // receiving data proves the heal worked. Clear the flag so the
          // grace timeout becomes a no-op. User saw nothing — just a pause.
          if (tabState._healGraceActive) {
            tabState._healGraceActive = false;
            console.log(`[HealGrace] Tab ${capturedTabId}: data arrived during grace — heal succeeded silently`);
          }

          // If we were reconnecting, the stream has successfully resumed.
          // Clear reconnection state and fire a success toast.
          if (tabState.isReconnecting) {
            console.log(`[Reconnect] Tab ${capturedTabId}: reconnection succeeded`);
            tabState.isReconnecting = false;
            tabState.reconnectionAttempt = 0;
            // Clear disconnect timeout — recovery happened before it fired
            if (tabState._disconnectTimeoutId) {
              clearTimeout(tabState._disconnectTimeoutId);
              tabState._disconnectTimeoutId = undefined;
            }
            addToast({
              severity: 'info',
              message: 'Stream reconnected successfully.',
              id: `reconnect-success-${capturedTabId}`,
            });
          }
          // Clear "Resuming session..." indicator once data arrives
          if (tabState.isResuming) {
            tabState.isResuming = false;
            if (tabState._resumeTimeoutId) { clearTimeout(tabState._resumeTimeoutId); tabState._resumeTimeoutId = undefined; }
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
          if (tabState._resumeTimeoutId) { clearTimeout(tabState._resumeTimeoutId); tabState._resumeTimeoutId = undefined; }
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
        // progress. Only real events reset the stall timer. (thinking_progress
        // IS progress → it falls in here and correctly resets the stall timer.)
        if (event.type !== 'heartbeat') {
          lastRealEventRef.current = Date.now();
        }

        // Liveness: the per-tab force-end watchdog is already reset at the TOP
        // of this handler for EVERY event (heartbeat/thinking_progress included),
        // so no type-gated touch() is needed here. The stall detector
        // (lastRealEventRef, above) is a DIFFERENT signal — SDK progress — and
        // deliberately excludes heartbeat; do not conflate the two.

        // Track tool execution state for context-aware stall thresholds.
        // tool_use → tool is running (may take minutes), tool_result → done.
        if (event.type === 'tool_use') {
          pendingToolUseRef.current = true;
        } else if (event.type === 'tool_result') {
          pendingToolUseRef.current = false;
        }

        // ── Unified file-change notification (run_e626e121) — the SINGLE
        //    backend-authoritative Canvas trigger. Carries the enriched payload
        //    {path (ws-relative), absolutePath (physical, for copy-path),
        //    relevance (deliverable|incidental|bookkeeping), operation}. ALL three
        //    consumers read this one DOM event: FileEditorCore highlight,
        //    useReferencedFiles (rail), useCanvasAutoSurface (deliverable-only pop).
        //    Stamped with the active sessionId for tab-scoping (background-tab
        //    writes must not surface in the active tab). Replaces the old frontend
        //    summary-parse trigger (MergedToolBlock swarm:file-referenced). ──
        if (event.type === 'file_changed') {
          const e = event as unknown as Record<string, unknown>;
          const path = e.path as string;
          if (path) {
            // Tab-scope stamp: use capturedTabId DIRECTLY, not the tab's sessionId
            // (run_26aa6caa). sessionId is undefined during the "session-not-yet-
            // resolved" window — a new tab's FIRST turn, which is exactly when files
            // get written — so a sessionId stamp went out UNDEFINED and the consumers'
            // fail-open filter bled the write into EVERY mounted rail (empirically
            // reproduced in useReferencedFiles.test.ts "BLEED PROBE"). capturedTabId
            // is the SAME stable key useCanvasHost uses → one key across the Canvas.
            //
            // HONEST EDGE (Gate-2, run_26aa6caa): capturedTabId is `tabId ??
            // activeTabIdRef.current` (line ~2227) — it is NOT literally always
            // present; it can be null in ONE window — the very FIRST tab, before
            // initTabState registers it (see the null note at line ~2443). In that
            // window `_stampTab` is undefined and the event ships unstamped → the
            // consumers fail OPEN. That is SAFE here, not a bleed: pre-registration
            // only ONE tab's rail is mounted, so a fail-open write lands in that
            // single tab (correct) — there is no second rail to bleed into. So the
            // cross-tab bleed is closed for every MULTI-tab case (≥2 tabs always have
            // registered ids); the lone unstamped case is single-tab and harmless.
            const _stampTab = capturedTabId ?? undefined;
            window.dispatchEvent(new CustomEvent('swarm:file-changed', {
              detail: {
                path,
                absolutePath: (e.absolutePath as string) ?? path,
                // Fail CLOSED on an older backend that omits relevance: default to
                // 'incidental' (lists in the rail, never auto-pops) — directive #3
                // 别什么都 trigger 成噪音. The current backend always sends it.
                relevance: (e.relevance as string) ?? 'incidental',
                // Unified review verdict (run_dcce7023): content|knowledge → rail+pop;
                // source → aggregated into the pipeline-finish local PR (NOT the rail);
                // process → never (already dropped server-side). Omitted by an older
                // backend → undefined → consumers fall back to `relevance` (migration).
                kind: (e.kind as string) ?? undefined,
                // Diff baseline ref (run_030dc98e): a source-final finish-batch event
                // carries `<sha>^` so the row opens on this-run's diff, not empty.
                baseRef: (e.baseRef as string) ?? undefined,
                operation: (e.operation as string) ?? 'written',
                // Owning-tab stamp (run_26aa6caa; renamed sessionId→tabId) = THIS
                // handler's captured tab (capturedTabId, line ~2227), NOT whatever tab
                // is active at dispatch time. Consumers (useReferencedFiles,
                // useCanvasAutoSurface) keep the event only when this owning tabId
                // matches the tab their rail is mounted for → a background tab's write
                // never lands in the foreground rail. tabId is stable (no unresolved
                // window), so the stamp is reliable for every multi-tab turn.
                tabId: _stampTab,
              },
            }));
          }
        }

        // ── UI command (ACT / proprioception Run 2) — the agent navigates its
        // own UI. dispatchUiCommand keys ONLY on `cmd` against the frontend's own
        // allowlist table; it IGNORES any event/target on the wire (crux: a buggy
        // backend can't name an arbitrary swarm:* event). Fail-closed on unknown. ──
        if (event.type === 'ui_command') {
          // Pass cmd + (for the one path-carrying cmd, open-canvas-file) the DATA
          // path. dispatchUiCommand still derives event+target from its OWN table
          // (never the wire); the path is a data arg it forwards ONLY for a
          // path-carrying cmd, then the workspace-scoped /workspace/file/resolve
          // filters it. Pure-nav cmds stay payload-less (run_c0550cc2).
          const _uev = event as unknown as Record<string, unknown>;
          // Pass this stream's CAPTURED origin tab (capturedTabId — the same value
          // _stampTab aliases for the file_changed sibling above) so an agent
          // open-canvas-file lands on the INITIATING tab, not whatever tab is active
          // when this mid-stream event fires. A background tab's stream reaches here
          // with capturedTabId = its own tab while activeTabIdRef may be another tab —
          // without this the file bled onto the active tab (run_48a29fc2). Frontend-
          // captured, NOT wire-derived → no new untrusted-input surface.
          dispatchUiCommand(_uev.cmd, _uev.path, capturedTabId ?? undefined);
        }

        if (event.type === 'session_start' && event.sessionId) {
          // ── State machine dispatch: PENDING → STREAMING (global + per-tab) ──
          dispatch({ type: 'SESSION_START', sessionId: event.sessionId });
          if (tabState) {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'SESSION_START', sessionId: event.sessionId });
          }

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
            // GUI28 backstop: a session_start means this tab spawned a fresh
            // subprocess and is streaming again — any stale recovery_exhausted
            // toast for this tab is now obsolete. Clear it even if the user
            // recovered by some path other than the toast's own action.
            removeToast(`recovery-exhausted-${capturedTabId}`);
          }
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          // SDK compaction: session ID changed but conversation continues.
          // CRITICAL: Do NOT clear messages — only append placeholder if needed.
          if (tabState) {
            tabState.sessionId = event.newSessionId;
          }

          const clearStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (clearStore) {
            // Append placeholder only if not already present
            const hasPlaceholder = clearStore.messages.some(m => m.id === assistantMessageId);
            if (!hasPlaceholder) {
              clearStore.append({
                id: assistantMessageId,
                role: 'assistant' as const,
                content: [],
                timestamp: new Date().toISOString(),
              } as Message);
            }
          } else {
            // Fallback: no store
            if (tabState) {
              const hasPlaceholder = tabState.messages.some(m => m.id === assistantMessageId);
              if (!hasPlaceholder) {
                tabState.messages = [...tabState.messages, {
                  id: assistantMessageId, role: 'assistant' as const, content: [], timestamp: new Date().toISOString(),
                }];
              }
            }
            if (isActiveTab) {
              setMessages((prev) => {
                if (prev.some(m => m.id === assistantMessageId)) return prev;
                return [...prev, { id: assistantMessageId, role: 'assistant' as const, content: [], timestamp: new Date().toISOString() }];
              });
            }
          }
          if (isActiveTab) {
            setSessionId(event.newSessionId);
            queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
          }
        } else if (event.type === 'text_delta' && event.text) {
          // --- Streaming text delta: append token incrementally ---
          // This is the HOT PATH — called once per token for real-time rendering.
          // SINGLE-WRITER: writes to store only. Store subscription auto-syncs
          // to React state (setMessages) and tabState.messages cache.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }
          // Reset stall threshold to text-mode (60s) — thinking phase is over.
          pendingToolUseRef.current = false;

          // Write to store — subscription handles React sync + tabState cache
          const textStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (textStore) {
            textStore.updateLast(
              (msg) => applyTextDelta(msg, event.text!),
              (msg) => msg.id === assistantMessageId,
            );
          } else {
            // Fallback for initial tab before store exists (capturedTabId===null)
            if (tabState) {
              tabState.messages = appendTextDelta(tabState.messages, assistantMessageId, event.text);
            }
            if (isActiveTab) {
              setMessages((prev) => appendTextDelta(prev, assistantMessageId, event.text!));
            }
          }
        } else if (event.type === 'thinking_delta' && event.thinking) {
          // --- Streaming thinking delta: append thinking token incrementally ---
          // SINGLE-WRITER: same as text_delta — store is the sole writer.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          const thinkStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (thinkStore) {
            thinkStore.updateLast(
              (msg) => applyThinkingDelta(msg, event.thinking!),
              (msg) => msg.id === assistantMessageId,
            );
          } else {
            if (tabState) {
              tabState.messages = appendThinkingDelta(tabState.messages, assistantMessageId, event.thinking);
            }
            if (isActiveTab) {
              setMessages((prev) => appendThinkingDelta(prev, assistantMessageId, event.thinking!));
            }
          }
        } else if (event.type === 'thinking_start') {
          // Thinking block started — update tab status to streaming.
          // The actual content arrives via thinking_delta events.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }
          // Extended thinking can have 30-120s silent periods (deep reasoning
          // without producing tokens). Use the longer tool threshold to avoid
          // false "session stalled" warnings during thinking.
          pendingToolUseRef.current = true;
        } else if (event.type === 'assistant' && event.content) {
          // Full assistant message — the SDK's complete, authoritative content.
          // SINGLE-WRITER: store handles dedup + subscription syncs to React.
          // Fix 8: Update tab status to 'streaming' on first assistant event
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          const assistStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (assistStore) {
            // Use updateMessages on the single matching message — reuses the
            // complex dedup/confirm logic without rewriting it.
            assistStore.updateLast(
              (msg) => updateMessages([msg], assistantMessageId, event.content!, event.model)[0],
              (msg) => msg.id === assistantMessageId,
            );
          } else {
            // Fallback: initial tab before store exists
            if (tabState) {
              tabState.messages = updateMessages(tabState.messages, assistantMessageId, event.content, event.model);
            }
            if (isActiveTab) {
              setMessages((prev) => updateMessages(prev, assistantMessageId, event.content!, event.model));
            }
          }
        } else if (event.type === 'ask_user_question' && !(event.questions && event.toolUseId)) {
          // Root 3 / 3A #4: a malformed ask_user_question (missing questions or
          // toolUseId) used to fall through ALL branches silently → the backend
          // sits in WAITING_INPUT forever and the user sees nothing. Log it.
          console.warn('[StreamHandler] ask_user_question event dropped — missing questions/toolUseId', {
            toolUseId: event.toolUseId,
            hasQuestions: !!event.questions,
            capturedTabId,
          });
        } else if (
          event.type === 'ask_user_question' &&
          event.questions &&
          event.toolUseId
        ) {
          const pq: PendingQuestion = {
            toolUseId: event.toolUseId,
            questions: event.questions,
          };
          if (capturedTabId) {
            // ── Single authoritative surface path (shared with reconcile re-surface) ──
            surfacePendingQuestion(capturedTabId, assistantMessageId, pq, {
              isActive: isActiveTab,
              sessionId: event.sessionId,
            });
          } else {
            // Edge case: no tab id yet (very first message before a tab exists).
            // The helper requires a tabId; replicate the minimal no-store path.
            dispatch({ type: 'ASK_USER_QUESTION' });
            const auqBlock = {
              type: 'ask_user_question' as const,
              toolUseId: event.toolUseId,
              questions: event.questions,
            };
            const currentMsgs = messagesRef.current;
            const auqMessages = currentMsgs.map((msg) =>
              msg.id === assistantMessageId ? { ...msg, content: [...msg.content, auqBlock] } : msg,
            );
            setMessages(auqMessages);
            setPendingQuestion(pq);
            if (event.sessionId) setSessionId(event.sessionId);
            setIsStreaming(false, undefined);
            incrementStreamGen();
          }
        } else if (event.type === 'cmd_permission_request') {
          const raw = event as unknown as Record<string, unknown>;
          const sid = event.sessionId || (raw.session_id as string);
          const requestId = (event.requestId || raw.request_id) as string;
          const toolName = (event.toolName || raw.tool_name) as string;
          const toolInput = (event.toolInput || raw.tool_input) as Record<string, unknown>;

          // Append cmd_permission_request content block via store (single-writer)
          const permBlock = {
            type: 'cmd_permission_request' as const,
            requestId: requestId,
            toolName: toolName,
            toolInput: toolInput,
            reason: event.reason || '',
            options: event.options || ['approve', 'deny'],
          };
          const permStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (permStore) {
            permStore.updateLast(
              (msg) => ({ ...msg, content: [...msg.content, permBlock] }),
              (msg) => msg.id === assistantMessageId,
            );
          } else {
            const currentMsgs = tabState?.messages ?? messagesRef.current;
            const permMessages = currentMsgs.map((msg) =>
              msg.id === assistantMessageId ? { ...msg, content: [...msg.content, permBlock] } : msg,
            );
            if (tabState) tabState.messages = permMessages;
            if (isActiveTab) setMessages(permMessages);
          }

          if (tabState) {
            if (sid) tabState.sessionId = sid;
            tabState.pendingPermissionRequestId = requestId;
          }
          if (isActiveTab) {
            if (sid) setSessionId(sid);
            setPendingPermissionRequestId(requestId);
          }
          // End store streaming phase — unblocks reconcile/replace
          const permEndStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (permEndStore) permEndStore.endStreaming();
          setIsStreaming(false, capturedTabId ?? undefined);
          incrementStreamGen();

          // Turn-end DB reconcile (reconcile-gap): like ask_user_question, this
          // terminal path emits NO `result` event — without this the streamed
          // buffer is never repaired from the DB. The synthesized
          // cmd_permission_request block survives reconcile via _applyMerge
          // local-only interactive carry-forward (PART 1).
          scheduleTurnEndReconcile(sid, capturedTabId);

          // Fix 8: Update tab status to 'permission_needed'
          if (capturedTabId) {
            updateTabStatus(capturedTabId, 'permission_needed');
          }
        } else if (event.type === 'result') {
          // ── State machine dispatch: STREAMING → IDLE or DRAIN_PENDING (global + per-tab) ──
          const hasQueued = !!(tabState?.queuedMessage);
          dispatch({ type: 'RESULT', hasQueuedMessage: hasQueued });
          if (tabState) {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'RESULT', hasQueuedMessage: hasQueued });
          }

          const sid =
            event.sessionId ||
            ((event as unknown as Record<string, unknown>)
              .session_id as string);

          if (tabState && sid) {
            tabState.sessionId = sid;
          }

          // Result is the definitive signal that the conversation turn is
          // complete. Transition store to idle — unblocks reconcile/replace
          // and flushes any pending reconcile thunk.
          const resultStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (resultStore) {
            resultStore.endStreaming();
          }

          // ── H2: unconditional turn-end reconcile-from-DB (backstop) ──
          // Even when H1 correlation succeeds, fetch the canonical DB rows once
          // per turn so any turn whose placeholder could NOT be correlated
          // (continuation paths pass no client_id) still finalizes, AND any
          // turn whose streamed buffer dropped a tail block is repaired from
          // the DB. Shared with the ask_user_question / cmd_permission_request
          // terminal paths (reconcile-gap) — see scheduleTurnEndReconcile.
          if (resultStore && sid) {
            scheduleTurnEndReconcile(sid, capturedTabId);
          }
          // Sync final state to React — ONLY if this tab is active.
          // Cross-tab isolation: never push a background tab's messages into
          // the currently displayed React state. The tabState.messages cache
          // is updated for both active and background (for instant display on
          // tab switch), but setMessages must be gated.
          if (resultStore) {
            // Always cache in tabState (for tab-switch instant display)
            if (tabState) tabState.messages = resultStore.messages;
            // Only sync to React if THIS tab is currently displayed
            if (isActiveTab || capturedTabId === activeTabIdRef.current) {
              if (sid) setSessionId(sid);
              setMessages(resultStore.getSnapshot());
            }
          } else if (isActiveTab) {
            if (sid) setSessionId(sid);
            // Fallback: no store — sync from tabState
            if (tabState) {
              setMessages(() => [...tabState.messages]);
            }
          }
          queryClient.invalidateQueries({ queryKey: ['radar', 'wipTasks'] });
          queryClient.invalidateQueries({ queryKey: ['radar', 'completedTasks'] });

          // ── Streaming content checkpoint (P0 content loss protection) ──
          // Persist messages to sessionStorage so that even if React state is
          // lost (SSE disconnect, error handler race, or undiagnosed clearing),
          // the content survives and can be recovered on mount.
          // Throttled: write at most every 10 seconds to avoid main-thread jank
          // from JSON.stringify + synchronous sessionStorage.setItem on large
          // message arrays (100+ tool results = 50-100KB per write).
          const checkpointSid = sid ?? tabState?.sessionId;
          if (checkpointSid && tabState && tabState.messages.length > 2) {
            const now = Date.now();
            const lastCheckpoint = tabState._lastCheckpointTime ?? 0;
            if (now - lastCheckpoint > 10_000) {  // 10s throttle
              tabState._lastCheckpointTime = now;
              try {
                const key = `swarm_stream_checkpoint_${checkpointSid}`;
                const payload = JSON.stringify({
                  messages: prepareMessagesForStorage(tabState.messages),
                  timestamp: now,
                });
                window.sessionStorage.setItem(key, payload);
              } catch {
                // Quota exceeded — non-fatal
              }
            }
          }

          // Drain site A: if a queued message is waiting, keep streaming
          // state TRUE to avoid a false→true flicker that kills the
          // "Running…" / "Progressing…" indicator.  The drain will
          // seamlessly continue the stream with a new conversation turn.
          const hasQueuedMessage = !!(capturedTabId && tabState?.queuedMessage);

          // ── DIAGNOSTIC: spinner-hang root cause ──
          // Gated behind localStorage flag to avoid noise in production.
          // Enable: localStorage.setItem('swarm_diag_result', '1')
          if (typeof localStorage !== 'undefined' && localStorage.getItem('swarm_diag_result')) {
            console.warn('[DIAG:result]', {
              capturedTabId,
              activeTabId: activeTabIdRef.current,
              isActiveTab,
              hasQueuedMessage,
              queuedMessage: tabState?.queuedMessage ? String(tabState.queuedMessage).slice(0, 50) : null,
              tabStateIsStreaming: tabState?.isStreaming,
              tabStateIsReconnecting: tabState?.isReconnecting,
              pendingStreamTabs: [...pendingStreamTabs],
              sid,
            });
          }


          if (!hasQueuedMessage) {
            // Normal completion — clear streaming state so spinner stops
            // and input re-enables.
            setIsStreaming(false, capturedTabId ?? undefined);
          }
          // Turn boundary: clear the reconcile-owned backstop clock so a stale
          // stuck-since timestamp from THIS turn cannot leak into the NEXT turn
          // on the same tab (adversarial HIGH #4). This is deterministic at turn
          // end — it does NOT depend on the reconcile poll happening to run, and
          // it is NOT in setIsStreaming (which reconnect churns), so it cannot
          // re-introduce the re-arm-loop bug.
          if (capturedTabId) {
            const resultTab = tabMapRef.current.get(capturedTabId);
            if (resultTab) resultTab._idleStreamingSince = undefined;
          }
          // Always bump generation so the old completeHandler no-ops.
          incrementStreamGen();

          // Fix 5: Remove persisted pending state — session completed successfully
          const resultSessionId = sid ?? tabState?.sessionId;
          if (resultSessionId) {
            removePendingState(resultSessionId);
            // Also clean up streaming checkpoint (written during long runs).
            // Without this, sessionStorage fills with stale blobs over time.
            try { window.sessionStorage.removeItem(`swarm_stream_checkpoint_${resultSessionId}`); } catch { /* best-effort cleanup */ }
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
            // Mark drain-in-progress so reconcile poll skips this tab.
            // Without this, reconcile sees backend=IDLE + frontend=streaming
            // and force-clears — killing the drain before it starts.
            if (tabState) tabState.drainPending = true;
            // Schedule drain — isStreaming stays true, indicator persists.
            // setTimeout(0) lets React flush the result-event state updates
            // (messages sync, session ID) before starting the next turn.
            setTimeout(() => deps.onDrainQueue?.(capturedTabId!), 0);
            // Safety timeout: if drain callback no-ops (component unmounted,
            // stale closure), clear drainPending so reconcile isn't suppressed
            // forever. 5s is generous — drain fires in <16ms normally.
            setTimeout(() => {
              if (tabState?.drainPending) tabState.drainPending = false;
            }, 5000);
          }
        } else if (event.type === 'error') {
          // Suppress error events from a user-stopped stream — the abort
          // can race with backend error delivery, producing a spurious
          // "An unknown error occurred" that forces a redundant resend.
          if (tabState?.userStopped) {
            // End store streaming phase + clean up React streaming state.
            const stoppedStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            if (stoppedStore) stoppedStore.endStreaming();
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            return;
          }

          // POST-STOP SILENT RETRY: If we haven't received any data yet
          // (connection-phase) and we have a retry function, auto-retry once
          // instead of showing a scary error. This catches the race where
          // the backend's pipe flush kills our new stream before it produces
          // any output. The user never sees the error — just a brief delay.
          if (
            tabState &&
            !tabState.hasReceivedData &&
            tabState.retryStreamFn &&
            (tabState.reconnectionAttempt ?? 0) < 1
          ) {
            tabState.reconnectionAttempt = 1;
            const retryFn = tabState.retryStreamFn;
            setTimeout(() => {
              if (!capturedTabId || !tabMapRef.current.has(capturedTabId)) return;
              const currentTabState = tabMapRef.current.get(capturedTabId);
              if (!currentTabState) return;
              currentTabState.hasReceivedData = false;
              // Bump gen BEFORE retry so the retry's fresh handlers (created
              // inside retryFn) capture+stamp a NEWER latestCompleteGen than the
              // original turn's handlers — a late [DONE] from the original
              // connection then correctly no-ops instead of clearing the retry's
              // live stream (adversarial LOW, run_6adee7d5). Mirrors the send
              // path's bump-before-createCompleteHandler ordering.
              incrementStreamGen();
              const newAbort = retryFn();
              currentTabState.abortController = {
                abort: () => { newAbort(); },
                signal: { aborted: false },
              } as unknown as AbortController;
            }, 300); // 300ms — enough for pipe flush to complete
            return;
          }

          // CONTEXT_TOO_LARGE: Backend circuit breaker triggered — session context
          // too large for reliable inference within timeout. Stop streaming, show
          // persistent warning with "New Tab" guidance. Not a transient error.
          if (event.code === 'CONTEXT_TOO_LARGE') {
            console.log('[StreamHandler] CONTEXT_TOO_LARGE — circuit breaker activated', { capturedTabId });
            const ctlStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            if (ctlStore) ctlStore.endStreaming();
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            // Terminal idle path with no following `result` event — the 15s
            // reconcile loop is gated to streaming tabs, so an idle circuit-breaker
            // tab is skipped. Schedule a turn-end reconcile so any assistant content
            // the backend persisted before tripping the breaker is repaired from DB
            // (reconcile-gap; phase-gated + debounced, harmless if nothing to fix).
            {
              const ctlSid = event.sessionId || (event as unknown as Record<string, unknown>).session_id as string;
              scheduleTurnEndReconcile(ctlSid, capturedTabId);
            }
            // Surface as a persistent context warning (reuses existing banner infra)
            const warningForTooLarge: ContextWarning = {
              level: 'critical',
              pct: 100,
              tokensEst: 0,
              message: event.message ?? 'Session context too large. Start a new tab for fresh context.',
            };
            const tabState = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
            if (tabState) tabState.contextWarning = warningForTooLarge;
            if (!capturedTabId || capturedTabId === activeTabIdRef.current) {
              setContextWarning(warningForTooLarge);
            }
            return;
          }

          // SESSION_BUSY: Backend rejected our send because the session is
          // still actively streaming (SSE disconnect caused a race). The backend
          // deletes the orphaned user message from DB (cold-resume hygiene) but
          // hands the text back via event.retryPayload so we can RE-QUEUE it —
          // the message is never silently lost. (The frontend send-guard
          // shouldQueueSend should normally prevent the send from escaping in
          // the first place; this re-queue is the backend safety net for the
          // window where the guard didn't catch it.)
          // See: 2026-04-02 SSE disconnect kill chain diagnosis.
          //
          // Recovery (2026-05-14): poll the messages endpoint so the in-flight
          // backend response renders when it completes. On completion we drain
          // the re-queued message (single owner — the queue), so it is sent
          // exactly once.
          if (event.code === 'SESSION_BUSY') {
            console.log('[StreamHandler] SESSION_BUSY — starting poll recovery', { capturedTabId });
            const busyEndStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            if (busyEndStore) busyEndStore.endStreaming();
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) {
              updateTabStatus(capturedTabId, 'streaming');
              const tabState = tabMapRef.current.get(capturedTabId);
              if (tabState) {
                // Re-queue the rejected message from retryPayload so it is sent
                // when the current response completes — NEVER lose user input.
                // Skip if a message is already queued (don't clobber an existing
                // queue) — the existing one drains first, this is idempotent.
                if (!tabState.queuedMessage) {
                  const requeued = queuedMessageFromRetryPayload(
                    event.retryPayload,
                    `queued-${crypto.randomUUID()}`,
                  );
                  if (requeued) {
                    tabState.queuedMessage = requeued;
                    tabState._queuedAt = Date.now();
                    console.log('[StreamHandler] SESSION_BUSY — re-queued message from retryPayload', { capturedTabId });
                    // Text is recovered, but binary attachments are not (composer
                    // already cleared on the original send). Warn so the user can
                    // re-attach rather than silently losing the file.
                    if (retryPayloadHasAttachments(event.retryPayload)) {
                      addToast({
                        severity: 'warning',
                        message: 'Your message text was recovered, but an attachment could not be — please re-attach it.',
                        id: `requeue-attach-${capturedTabId ?? 'global'}`,
                        autoDismiss: true,
                      });
                    }
                  }
                }
                // Atomic clear (flag + Set + re-render) instead of direct
                // tabState.isStreaming = false. Direct mutation on a background
                // tab triggers no re-render → spinner frozen true. See the
                // disconnect-timeout fix for the same root cause.
                setIsStreaming(false, capturedTabId);
                tabState.isWaitingForBusy = true;
                // Remove the orphan DISPLAY messages from this failed send:
                // the assistant placeholder (known ID) + the user message
                // immediately before it. The backend deleted the user row from
                // DB (cold-resume hygiene) — but we already preserved the text
                // above via the retryPayload re-queue, so removing the stale
                // display bubbles here loses nothing. Without this, orphans
                // display for 6-9s until polling overwrites with DB truth.
                // Remove orphan messages via store or fallback
                const removeStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
                const storeOrTab = removeStore?.messages ?? tabState.messages;
                const assistantIdx = storeOrTab.findIndex(
                  (m) => m.id === assistantMessageId
                );
                if (assistantIdx >= 0) {
                  const removeIds = new Set([assistantMessageId]);
                  if (assistantIdx > 0 && storeOrTab[assistantIdx - 1].role === 'user') {
                    removeIds.add(storeOrTab[assistantIdx - 1].id);
                  }
                  if (removeStore) {
                    removeStore.remove((m) => removeIds.has(m.id));
                  } else {
                    tabState.messages = tabState.messages.filter(m => !removeIds.has(m.id));
                  }
                }
              }
            }
            // Mirror to React state if this is the active tab
            if (!capturedTabId || capturedTabId === activeTabIdRef.current) {
              setIsWaitingForBusy(true);
              // Force sync after remove — store subscription is rAF-deferred,
              // so force immediate sync for UI responsiveness on orphan cleanup
              const rmStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
              if (rmStore) {
                setMessages(rmStore.getSnapshot());
              } else {
                const tabState = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
                if (tabState) setMessages([...tabState.messages]);
              }
            }

            // Start polling messages endpoint (ETag-based, cheap 304s)
            const pollSessionId = capturedTabId
              ? tabMapRef.current.get(capturedTabId)?.sessionId
              : undefined;

            if (pollSessionId && capturedTabId) {
              const busyTab = tabMapRef.current.get(capturedTabId);
              if (!busyTab) return;

              // Clear any existing poll for this tab (safety)
              if (busyTab.busyPollInterval) clearInterval(busyTab.busyPollInterval);
              if (busyTab.busyPollTimeout) clearTimeout(busyTab.busyPollTimeout);

              // Helper: clear all busy-poll state for the tab
              const clearBusyPoll = (tab: typeof busyTab) => {
                if (tab.busyPollInterval) clearInterval(tab.busyPollInterval);
                if (tab.busyPollTimeout) clearTimeout(tab.busyPollTimeout);
                tab.busyPollInterval = undefined;
                tab.busyPollTimeout = undefined;
                tab.isWaitingForBusy = false;
              };

              // ── B1: drain-aware busy recovery (authoritative state) ────────
              // The backend is the SOLE owner of the pending drain (Root-1 SSOT):
              // the rejected send was persisted server-side (sent=0) and will be
              // coalesce-drained at the next clean IDLE. We therefore do NOT
              // re-send from here — a frontend re-send double-sends against the
              // server drain AND spawns fresh SESSION_BUSY rows (the root of the
              // "queue loops / never continues" symptom). Instead we POLL
              // authoritative state and RECONCILE the DB each tick so the in-flight
              // turn AND the drained turn surface progressively (Root-1 deferred
              // live token re-attach — design Scenario 7/F8). getSessionMessages
              // excludes sent=0 pending rows, so nothing flickers before the drain
              // flips them sent=1. Finish only when the backend is genuinely idle
              // with nothing pending. The always-on 15s reconcile tick is the
              // backstop if this fast poll is cleared early.
              busyTab.busyPollInterval = setInterval(async () => {
                // Guard: tab may have been closed during polling
                const currentTab = tabMapRef.current.get(capturedTabId);
                if (!currentTab || !currentTab.isWaitingForBusy) {
                  clearBusyPoll(busyTab);
                  return;
                }
                try {
                  const states = await chatService.getStreamingState();
                  const auth = states[pollSessionId];
                  // "Busy" = an in-flight turn (streaming/waiting) OR a coalesced
                  // drain still queued/running (pendingCount > 0). Missing entry =
                  // session GC'd/evicted = nothing running = not busy.
                  const backendBusy =
                    auth?.streaming === true ||
                    auth?.state === 'streaming' ||
                    auth?.state === 'waiting_input' ||
                    (auth?.pendingCount ?? 0) > 0;

                  // Surface current DB truth every tick (cheap; dedups by id;
                  // phase-gated NO-OP if a live stream restarted).
                  chatService.invalidateMessageCache(pollSessionId);
                  const msgs = await chatService.getSessionMessagesReconcileTail(pollSessionId, RECONCILE_TAIL);
                  const busyStore = messageStoreRegistry.getOrCreate(capturedTabId, { sessionId: pollSessionId });
                  busyStore.reconcile(msgs);
                  if (busyStore.phase === 'idle') {
                    currentTab.messages = busyStore.messages;
                    if (capturedTabId === activeTabIdRef.current) setMessages(busyStore.getSnapshot());
                  }

                  if (!backendBusy) {
                    // In-flight turn + coalesced drain are all done. Retire the
                    // optimistic queued bubble (the DB reconcile above already
                    // brought the canonical drained rows, so the synthetic would
                    // duplicate them) — then finish. NO re-send (single owner).
                    if (currentTab.queuedMessage) {
                      const qid = currentTab.queuedMessage.messageId;
                      busyStore.remove((m) => m.id === qid);
                      currentTab.queuedMessage = undefined;
                      currentTab._queuedAt = undefined;
                      if (busyStore.phase === 'idle') {
                        currentTab.messages = busyStore.messages;
                        if (capturedTabId === activeTabIdRef.current) setMessages(busyStore.getSnapshot());
                      }
                    }
                    clearBusyPoll(currentTab);
                    if (capturedTabId === activeTabIdRef.current) setIsWaitingForBusy(false);
                    updateTabStatus(
                      capturedTabId,
                      capturedTabId === activeTabIdRef.current ? 'idle' : 'complete_unread',
                    );
                  }
                } catch {
                  // Silently ignore poll errors — don't turn recovery into error
                }
              }, 3000);

              // Safety cap: stop the fast poll after 10 min. The always-on 15s
              // reconcile tick remains the backstop that surfaces any later drain.
              // NO re-send on timeout (single owner — the backend drains).
              busyTab.busyPollTimeout = setTimeout(() => {
                const currentTab = tabMapRef.current.get(capturedTabId);
                if (currentTab) clearBusyPoll(currentTab);
                if (capturedTabId === activeTabIdRef.current) setIsWaitingForBusy(false);
                updateTabStatus(capturedTabId, 'idle');
              }, 600_000);
            } else {
              // No session ID — can't poll, clear immediately
              if (capturedTabId) {
                const tabState = tabMapRef.current.get(capturedTabId);
                if (tabState) {
                  tabState.isWaitingForBusy = false;
                  // Drain any message re-queued above — without a poll there is
                  // no other owner to send it, so it would be stranded forever.
                  if (tabState.queuedMessage && deps.onDrainQueue) {
                    setTimeout(() => deps.onDrainQueue?.(capturedTabId), 100);
                  }
                }
              }
              setIsWaitingForBusy(false);
            }
            return;
          }

          const errorMsg =
            event.message ||
            event.error ||
            event.detail ||
            'Connection interrupted — send your message again to continue.';
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

          // Apply error via store (single-writer) or fallback.
          // CRITICAL (adversarial #1): endStreaming() MUST be called before replace()
          // because replace() is NO-OP during streaming phase. The stream is
          // definitively over at this point (error = terminal event).
          const errStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (errStore) {
            errStore.endStreaming(); // Transition to idle so replace() succeeds
            const errorResult = applyError(errStore.messages);
            errStore.replace(errorResult);
          } else {
            if (tabState) tabState.messages = applyError(tabState.messages);
            if (isActiveTab) setMessages((prev) => applyError(prev));
          }

          if (tabState) {
            // QUEUE_TIMEOUT: store retry payload so ChatPage can offer "Retry" button
            if (event.code === 'QUEUE_TIMEOUT' && event.retryPayload) {
              tabState.queueTimeoutRetry = event.retryPayload;
            }
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
            // Safety timeout: if no data arrives within 60s, clear the
            // resuming indicator so the user isn't stuck on a spinner forever.
            // When resume actually succeeds, the clearing at line 1592/1606
            // fires first and this timeout becomes a no-op.
            const resumeTimeoutId = setTimeout(() => {
              if (tabState.isResuming) {
                tabState.isResuming = false;
                const stillActive = capturedTabId === activeTabIdRef.current;
                if (stillActive) {
                  setIsStreaming(false, capturedTabId ?? undefined);
                  const timeoutStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
                  if (timeoutStore) timeoutStore.endStreaming();
                }
                console.warn('[StreamHandler] Resume timeout — cleared isResuming after 60s', { capturedTabId });
              }
            }, 60_000);
            // If isResuming is cleared normally (data arrives), cancel the timeout.
            // We piggyback on the existing clear paths by storing the timeout ID.
            tabState._resumeTimeoutId = resumeTimeoutId;
            // Inject synthetic resume boundary into local messages so the
            // divider renders immediately — before the stream completes and
            // the reconcile re-fetch picks up the DB-persisted marker.
            //
            // DEDUP: If the last message is already a system boundary, skip.
            // Self-healing can trigger multiple kill→resume cycles, each
            // emitting session_resuming — without this guard, multiple
            // "Session Resumed" dividers stack up.
            const boundaryStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            const currentMsgs = boundaryStore?.messages ?? tabState.messages ?? [];
            const lastMsg = currentMsgs[currentMsgs.length - 1];
            const alreadyHasBoundary = lastMsg?.role === 'system' &&
              lastMsg?.id?.startsWith('resume-boundary');

            if (!alreadyHasBoundary) {
              const boundaryMsg = {
                id: `resume-boundary-${Date.now()}`,
                role: 'system' as const,
                content: [{ type: 'text' as const, text: 'Session resumed' }],
                timestamp: new Date().toISOString(),
              };
              // Append boundary via store (single-writer)
              if (boundaryStore) {
                boundaryStore.append(boundaryMsg as Message);
              } else {
                tabState.messages = [...(tabState.messages || []), boundaryMsg];
              }
            }
            // Force re-render so ChatPage picks up isResuming from tabMapRef.
            const isActive = capturedTabId === activeTabIdRef.current;
            if (isActive) {
              setIsStreaming(true, capturedTabId ?? undefined);
            }
          }
        }
        // Turn limit reached — CLI hit maxTurns cap (default 100, we set 200).
        // This is NOT an error — the agent completed its work up to this point.
        // Preserve all streamed content. Show a gentle "paused" indicator so the
        // user knows to send a message (or click Resume) to continue.
        // BUG FIX (2026-06-01): Previously this arrived as an 'error' event which
        // triggered error UI path and could clear streamed content.
        else if (event.type === 'turn_limit_reached') {
          const tabState = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          // Append a gentle info message to the assistant's last response
          // (NOT as an error — no isError flag, no red styling).
          const infoBlock: ContentBlock = {
            type: 'text' as const,
            text: `\n\n⏸️ ${event.message || 'Turn limit reached — send a message to continue.'}`,
          };
          if (tabState) {
            const lastAssistant = findLast(
              tabState.messages, (m: Message) => m.role === 'assistant',
            );
            if (lastAssistant) {
              lastAssistant.content = [...lastAssistant.content, infoBlock];
            }
          }
          // Don't call setMessages here — the result event that immediately
          // follows will sync tabState.messages → React state (line 1388).
          // Calling setMessages here AND in result handler creates a fragile
          // double-write that depends on React batching order.
          // Don't clear streaming state yet — the result event that follows
          // will handle transition. This event is purely informational.
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
              // show "Running" forever after the guard fires. Use the atomic
              // setIsStreaming(false) primitive (flag + Set + re-render) — a
              // direct flag mutation on a background tab triggers no re-render
              // so the spinner would stay frozen true (same root cause as the
              // disconnect-timeout and SESSION_BUSY fixes).
              if (subtype === 'hard_warn' || subtype === 'kill') {
                setIsStreaming(false, capturedTabId ?? undefined);
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
        // MCP health warning — backend emits once per session if configured
        // MCPs failed to connect. Show a toast notification so the user knows.
        else if (event.type === 'mcp_health_warning') {
          const msg = event.message ?? 'Some MCP servers failed to load.';
          addToast({
            severity: 'warning',
            message: msg,
            id: `mcp-health-${capturedTabId ?? 'global'}`,
          });
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

          // Append evolution message via store (single-writer)
          const evoStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (evoStore) {
            evoStore.append(evolutionMessage);
          } else {
            if (tabState) tabState.messages = [...tabState.messages, evolutionMessage];
            if (isActiveTab) setMessages((prev) => [...prev, evolutionMessage]);
          }
        }
        // Telemetry events (agent_activity, tool_invocation, etc.) are no
        // longer processed — TSCC fetches metadata from the endpoint instead.
      };
    },
    [queryClient, setIsStreaming, incrementStreamGen, updateTabStatus, addToast], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
  );

  const createErrorHandler = useCallback(
    (assistantMessageId: string, tabId?: string) => {
      const capturedTabId = tabId ?? activeTabIdRef.current;
      // Capture stream generation — stale errors from a previous stream
      // must not kill the spinner or invalidate handlers for the current stream.
      const capturedStreamGen = streamGenRef.current;

      return (error: Error) => {
        // Generation guard: discard errors from a previous stream.
        if (capturedTabId) {
          const genCheckTab = tabMapRef.current.get(capturedTabId);
          if (genCheckTab && genCheckTab.streamGen !== capturedStreamGen) {
            console.log('[ErrorHandler] Discarding stale error from previous stream generation', { capturedTabId });
            return; // stale error — discard silently
          }
        } else if (streamGenRef.current !== capturedStreamGen) {
          return; // stale error — discard silently
        }

        console.error('Stream error:', error);
        // ── State machine dispatch: → ERROR or RECONNECTING (global + per-tab) ──
        const errorTabState = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
        const hasData = errorTabState?.hasReceivedData ?? false;
        const errorEvent: StreamingEvent = {
          type: 'ERROR',
          phase: hasData ? 'mid_stream' : 'connection',
          message: error.message,
        };
        dispatch(errorEvent);
        if (errorTabState) {
          errorTabState.streamState = streamingReducer(errorTabState.streamState, errorEvent);
        }

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

            // Bump gen BEFORE retry so the retry's fresh handlers supersede the
            // original turn's — a late [DONE] from the original connection then
            // no-ops instead of clearing the retry's live stream (adversarial
            // LOW, run_6adee7d5). Mirrors send-path bump-before-handler ordering.
            incrementStreamGen();

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

        // --- Self-heal grace period ---
        // If data WAS being received (mid-stream disconnect), this may be the
        // backend self-healing (kill → respawn). Don't show error immediately —
        // wait HEAL_GRACE_PERIOD_MS. If the backend reconnects within that window,
        // the user sees nothing (just a brief pause that looks like "thinking").
        const hadData = tabState?.hasReceivedData ?? false;
        if (hadData && tabState && !tabState._healGraceActive) {
          tabState._healGraceActive = true;
          console.log(
            `[HealGrace] Tab ${capturedTabId}: mid-stream disconnect — entering ${HEAL_GRACE_PERIOD_MS / 1000}s grace period`,
          );
          // Keep isStreaming = true (spinner stays, looks like thinking)
          // Don't show error toast yet
          // Set a timeout — if backend doesn't reconnect, consult authoritative
          // state and only THEN decide error-vs-still-working (OT01 honest signal).
          setTimeout(async () => {
            const currentTab = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
            if (!currentTab || !currentTab._healGraceActive) {
              // _healGraceActive was cleared (successful reconnection / data
              // arrived during grace) — the heal worked, user saw nothing.
              return;
            }
            // ── Honest-signal decision (OT01) ──────────────────────────────
            // The SSE connection is gone, but the backend subprocess may still be
            // producing the answer (a long agent turn can outlive the connection:
            // it is left ALIVE post-disconnect and finishes into the DB). Showing
            // a hard "Connection lost" error here while the turn is still alive was
            // the recurring false-error bug. Consult the authoritative backend
            // mirror ONCE; if alive (streaming / waiting_input / flushing) keep the
            // spinner and hand ongoing recovery to the 15s reconcile owner. Only
            // surface the error when the backend is genuinely done — or when the
            // liveness query fails (fail-safe: never strand on an eternal spinner).
            let verdict: HealGraceVerdict;
            try {
              const states = await chatService.getStreamingState();
              const sid = currentTab.sessionId;
              const auth = sid ? states[sid] : undefined;
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: auth?.streaming ?? false,
                backendWaitingInput: auth?.waitingInput ?? false,
                postDisconnectFlushing: auth?.postDisconnectFlushing ?? false,
                queryFailed: false,
              });
            } catch {
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: false, backendWaitingInput: false,
                postDisconnectFlushing: false, queryFailed: true,
              });
            }
            // Re-read AFTER the await — the tab may have closed / reconnected /
            // switched during the network round-trip (cross-tab capturedTabId
            // guard preserved, same pattern as the busy poll).
            const tab2 = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
            if (!tab2 || !tab2._healGraceActive) return;

            if (verdict === 'still-working') {
              // Backend is alive — DO NOT show an error, KEEP the spinner. Hand
              // ongoing recovery to the 15s reconcile loop (the SOLE owner of
              // _postDisconnectUncertain). The reconcile loop's existing 120-min
              // cap is the long-turn ceiling — we add no competing timer/cap.
              console.log(`[HealGrace] Tab ${capturedTabId}: grace expired but backend still working — keeping spinner, reconcile owns recovery`);
              tab2._postDisconnectUncertain = true;
              tab2._postDisconnectAt = Date.now();
              // Stamp _reconcileStreamStart so the desync (capMs/graceMs) and
              // force-clear (activeGuardAge) caps in the 15s reconcile loop
              // anchor to NOW — identical to the disconnect-handler still-working
              // branch (~:4083). WITHOUT it, this branch hands recovery to the
              // reconcile loop while _reconcileStreamStart stays stale (~0), so
              // the next tick computes a huge dsStartAge → the ≥10s start-grace
              // does NOT apply → the spinner is force-cleared in the backend
              // dead→cold gap while the answer is still flushing = the exact
              // OT01 truncated-render bug, just surfacing on the heal-grace path.
              tab2._reconcileStreamStart = Date.now();
              // Leave _healGraceActive + isStreaming true (spinner stays).
              return;
            }

            // verdict === 'show-error': backend genuinely done/dead (or query
            // failed). Run the original error path.
            tab2._healGraceActive = false;
            console.warn(`[HealGrace] Tab ${capturedTabId}: grace expired, backend genuinely done — showing error`);
            setIsStreaming(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            // Mark post-disconnect-uncertain so a follow-up send is QUEUED
            // (shouldQueueSend) instead of escaping → SESSION_BUSY → orphan delete.
            tab2._postDisconnectUncertain = true;
            tab2._postDisconnectAt = Date.now();
            // NOTE (2026-06-21, zombie-poison fix): do NOT POST /stop here.
            // /stop → interrupt_session → kill-on-timeout POISONS the subprocess
            // → next send zombie_via_error → manual-Continue loop. The backend's
            // _recover_streaming_on_disconnect already transitions STREAMING→IDLE
            // and leaves the subprocess alive; the 15s reconcile loop drains any
            // queued message.
            addToast({ severity: 'warning', message: 'Connection lost after self-heal attempt. Send your message again to continue.', autoDismiss: true });
          }, HEAL_GRACE_PERIOD_MS);
          return; // Don't show error — heal grace in progress
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

        // ── AUTO-RESEND ARMING (swallowed-question fix) ───────────────────
        // CONNECTION-PHASE failure means the question never produced any data —
        // very likely it never reached the backend (e.g. daemon redeploy ~60s
        // outage >> the ~7s reconnect budget). Arm a one-shot auto-resend that
        // the backend-recovered handler fires once health flips back, so the
        // user's question isn't silently swallowed. Gated to connection-phase
        // ONLY (never mid-stream — that may have persisted partial work, and
        // mergeTabFromDb recovers it; resending would double-answer). Bounded by
        // RESEND_MAX_ATTEMPTS so a flapping backend can't loop. Never for a
        // user-stopped stream.
        const canArmResend =
          !hadData &&
          !!tabState?.retryStreamFn &&
          !tabState?.userStopped &&
          (tabState?._pendingResendAttempts ?? 0) < RESEND_MAX_ATTEMPTS;
        if (tabState && canArmResend) {
          tabState._pendingResendOnRecovery = true;
          tabState._pendingResendAssistantId = assistantMessageId;
        }

        // NOTE (2026-06-21, zombie-poison fix): do NOT POST /stop here.
        // This was a stale "Gap 2 fix" that explicitly stopped the backend
        // session on mid-stream disconnect. But /stop → interrupt_session →
        // kill-on-timeout POISONS the subprocess turn-state, so the next
        // send() reused a poisoned subprocess → instant empty
        // error_during_execution → zombie_via_error → kill+--resume → the
        // "response stops half-way, must click Continue" loop.
        //
        // The backend already handles SSE disconnect gracefully via
        // _recover_streaming_on_disconnect: it transitions STREAMING → IDLE
        // (so the next send() takes the normal IDLE path, NOT force_unstick)
        // and soft-interrupts the subprocess, LEAVING IT ALIVE on timeout so
        // a long tool-loop finishes and its output persists to DB for
        // reconciliation recovery. The frontend stop was both redundant and
        // the actual source of the poison. Trust the backend recovery.

        // If this was a successful reconnection that then failed mid-stream,
        // fire the toast for the reconnection success (handled by stream handler).
        // For exhausted retries or mid-stream failures, show the error.

        // Include the real error text so the user knows what actually happened.
        // The original code suppressed error.message entirely — that made debugging
        // impossible and showed a blank-looking generic message.
        const realError = error.message || 'Unknown connection error';
        // If an auto-resend is armed, tell the user it will retry itself when the
        // backend returns (and leave the placeholder so it's visible meanwhile).
        // If the backend never comes back, this message stays as the fallback.
        const willAutoResend = tabState?._pendingResendOnRecovery === true;
        const errorContent: ContentBlock[] = [
          { type: 'text' as const, text: willAutoResend
              ? `⏳ Backend unreachable — your message will be re-sent automatically as soon as it's back. (${realError})`
              : `⚠️ Connection interrupted: ${realError}\n\n💡 Your conversation is saved — send your message again to continue.` },
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

        // Apply error via store (single-writer) or fallback.
        // CRITICAL (adversarial #1): endStreaming() before replace() — stream is
        // definitively over (SSE connection errored out).
        const errStore2 = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
        if (errStore2) {
          errStore2.endStreaming(); // Transition to idle so replace() succeeds
          const beforeCount = errStore2.messages.find((m) => m.id === assistantMessageId)?.content.length ?? 0;
          const errorResult = applyError(errStore2.messages);
          errStore2.replace(errorResult);
          const afterCount = errStore2.messages.find((m) => m.id === assistantMessageId)?.content.length ?? 0;
          if (beforeCount > 0 && afterCount <= beforeCount) {
            console.warn('[ErrorHandler] Possible content loss:', {
              assistantMessageId, beforeCount, afterCount,
              totalMessages: errStore2.messages.length, tabId: capturedTabId,
              error: error.message?.slice(0, 80),
            });
          }
        } else {
          if (tabState) {
            tabState.messages = applyError(tabState.messages);
          }
          if (isActiveTab) {
            setMessages((prev) => applyError(prev));
          }
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
    [setIsStreaming, incrementStreamGen, addToast, updateTabStatus], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
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

    // Mark THIS handler as the live completer for the tab. The send path bumps
    // incrementStreamGen() BEFORE calling createCompleteHandler (ChatPage send
    // sites), so a genuinely NEW send always advances latestCompleteGen past an
    // older handler's capturedGen — that older handler then correctly no-ops.
    // Mid-stream churn (reconnect/result/error) bumps streamGen but does NOT
    // create a new handler, so latestCompleteGen stays == capturedGen and this
    // handler remains authoritative. This is what makes [DONE] survive the
    // stale-gen guard (run_6adee7d5).
    if (capturedTabId) {
      const liveTab = tabMapRef.current.get(capturedTabId);
      if (liveTab) liveTab.latestCompleteGen = capturedGen;
    }

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

      // ── Staleness gate (single source of truth) ──
      // Validate freshness ONCE, then clear streaming via the atomic
      // setIsStreaming() primitive — which clears BOTH tabState.isStreaming
      // and the pendingStreamTabs entry together. We must never clear the
      // flag directly here: doing so before an early-return orphans the
      // pendingStreamTabs entry, and the `||` in the isStreaming derivation
      // then pins the spinner true forever (confirmed spinner-hang root cause).
      if (capturedTabId) {
        const tabState = tabMapRef.current.get(capturedTabId);
        // Live-completer check (run_6adee7d5): clear streaming iff THIS handler
        // is still the tab's live completer. We compare against latestCompleteGen
        // (advanced only by a NEW send), NOT streamGen (churned mid-stream by
        // reconnect/result/error). The old `streamGen !== capturedGen` check
        // self-invalidated a turn's own [DONE] after a single reconnect → the
        // setIsStreaming(false) below was skipped → spinner pinned true, rescued
        // only by the 30s reconcile force-clear (~109/114 idle force-clears).
        // Closed tab (no tabState) → no-op, as before.
        if (!tabState) return; // closed tab
        // Fail-SAFE liveness check (adversarial MED, run_6adee7d5): treat an
        // UNSET latestCompleteGen as NON-authoritative, not authoritative. The
        // stamp at handler creation only writes when the tab already exists in
        // the map; if it was never stamped (deferred tab registration, legacy
        // null-tab path), a `?? capturedGen` fallback would make the gate
        // `capturedGen === capturedGen` = always-true = always-clear, which
        // could wrongly clear a NEWER stream's spinner. An unset marker means
        // "I cannot prove I am the live completer" → no-op (the 30s reconcile
        // backstop still covers the genuinely-stuck case).
        const liveGen = tabState.latestCompleteGen;
        if (liveGen === undefined || capturedGen !== liveGen) {
          // Either liveness unknown, or a genuinely newer send superseded this
          // handler — correct no-op.
          // OT01 diag (AC5): a complete-handler no-op means the [DONE] that would
          // call setIsStreaming(false) was discarded → spinner can pin. ALWAYS
          // warn (not DEV-only — DEV is tree-shaken dead in the prod .app where
          // the bug happens, run_3451bbd1); logForwarder persists warn to
          // frontend.log. Fields per Gate-1 Q4 (own-turn vs cross-tab).
          console.warn('[OT01-Complete] handler no-op (not the live completer — [DONE] dropped)', {
            capturedTabId, activeTab: activeTabIdRef.current,
            capturedGen, liveGen, tabStreamGen: tabState.streamGen,
            globalStreamGen: streamGenRef.current, sessionId: tabState.sessionId,
          });
          return;
        }

        // Clear resume indicator + sessionStorage (not part of streaming flag).
        tabState.isResuming = false;
        if (tabState._resumeTimeoutId) { clearTimeout(tabState._resumeTimeoutId); tabState._resumeTimeoutId = undefined; }
        if (tabState.sessionId) {
          removePendingState(tabState.sessionId);
        }
      } else if (streamGenRef.current !== capturedGen) {
        // Legacy null-tab path: fall back to the global generation check.
        return; // stale — no-op
      }

      // End store streaming phase — unblocks reconcile/replace, flushes
      // pending reconcile thunk. Must happen before setIsStreaming(false)
      // so that the store is in idle state when any post-stream DB fetch fires.
      if (capturedTabId) {
        const completeStore = messageStoreRegistry.get(capturedTabId);
        if (completeStore) completeStore.endStreaming();
      }

      // Atomic clear — flag + pendingStreamTabs in one primitive. This is the
      // ONLY place streaming is cleared on the complete path, so the two
      // representations can never drift out of sync.
      setIsStreaming(false, capturedTabId ?? undefined);

      // Reset tab status to idle — without this, tabs that ended with an
      // error event (SSE disconnect, SDK error) would keep showing the red
      // "!" indicator forever since no subsequent result event arrives to
      // clear it.  The completeHandler is the definitive "stream is over"
      // signal — if we reach here the tab is no longer active.
      if (capturedTabId) {
        updateTabStatus(capturedTabId, 'idle');
      }

      // DEFINITIVE QUEUE DRAIN backstop (stranded-queue fix): the completeHandler
      // is the authoritative "stream is over" signal. The two existing drain
      // triggers each have a gap:
      //   • the `result` event branch schedules a drain, but it lives under the
      //     raw streamGen staleness guard — if streamGen was bumped mid-turn the
      //     final `result` is discarded as stale and the drain never schedules,
      //     while isStreaming was never cleared (stays true);
      //   • the pre-guard above only drains when isStreaming is ALREADY false,
      //     so it misses the case where isStreaming is still true at [DONE].
      // This block — AFTER the atomic setIsStreaming(false) — closes that gap.
      //
      // Guards:
      //   • !drainPending: the `result` path sets drainPending when it schedules
      //     a drain, so we don't double up on the normal path. (drainQueuedMessage
      //     is idempotent regardless — it clears queuedMessage synchronously
      //     before its first await — so a redundant schedule never double-sends.)
      //   • !pendingQuestion && !pendingPermissionRequestId: ask_user_question and
      //     cmd_permission_request are TERMINAL (they clear isStreaming) and the
      //     backend then sends [DONE], so the completeHandler runs while the agent
      //     is suspended awaiting the user. Draining here would fire the queued
      //     follow-up and abandon the open question/permission. Skip — the queue
      //     drains after the user answers and the turn truly completes.
      // We deliberately do NOT set drainPending here: a drainQueuedMessage that
      // early-returns (queue already gone) does not clear it, and a stale
      // drainPending=true would make this very check skip the drain on the next
      // turn — silently regressing the fix.
      if (capturedTabId) {
        const finalTab = tabMapRef.current.get(capturedTabId);
        if (
          finalTab?.queuedMessage &&
          !finalTab.drainPending &&
          !finalTab.pendingQuestion &&
          !finalTab.pendingPermissionRequestId &&
          deps.onDrainQueue
        ) {
          setTimeout(() => deps.onDrainQueue?.(capturedTabId), 0);
        }
      }
    };
  }, [setIsStreaming, updateTabStatus]); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

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
    const capturedStreamGen = streamGenRef.current;
    const DISCONNECT_TIMEOUT_MS = 30_000; // 30s before giving up

    return () => {
      // Generation guard: discard stale disconnect from a previous stream.
      if (capturedTabId) {
        const currentTabState = tabMapRef.current.get(capturedTabId);
        if (currentTabState && currentTabState.streamGen !== capturedStreamGen) {
          return; // stale disconnect — discard silently
        }
      } else if (streamGenRef.current !== capturedStreamGen) {
        return; // stale disconnect — discard silently
      }

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
          // state and recover content from DB.  The backend persists assistant
          // messages immediately during streaming (crash-safe), so any content
          // generated after disconnect is in DB — we just need to fetch it.
          const disconnectTimeoutId = setTimeout(async () => {
            const preTabState = tabMapRef.current.get(capturedTabId);
            if (!preTabState?.isReconnecting) return; // Already recovered or tab closed

            // Consult the authoritative backend mirror ONCE — SYMMETRIC with the
            // heal-grace expiry path. Before this fix the timeout UNCONDITIONALLY
            // cleared the spinner + did a ONE-SHOT DB pull; if the backend was
            // still flushing/streaming, that pull grabbed INCOMPLETE content and
            // nothing re-pulled the finished answer until the user's NEXT send
            // (the reported bug). Fail-safe try/catch: a failed query -> show-error,
            // never strand on an eternal spinner.
            const sid = preTabState.sessionId;
            let verdict: HealGraceVerdict;
            try {
              const states = await chatService.getStreamingState();
              const auth = sid ? states[sid] : undefined;
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: auth?.streaming ?? false,
                backendWaitingInput: auth?.waitingInput ?? false,
                postDisconnectFlushing: auth?.postDisconnectFlushing ?? false,
                queryFailed: false,
              });
            } catch {
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: false, backendWaitingInput: false,
                postDisconnectFlushing: false, queryFailed: true,
              });
            }

            // Re-read AFTER the await (MUST-FIX, Gate-1): the pre-await
            // isReconnecting check is now stale — the tab may have closed /
            // reconnected / a NEW stream may have started during the round-trip.
            // Guard on the same (present + streamGen unchanged + still
            // reconnecting) triad the stale-disconnect guard uses.
            const currentTabState = tabMapRef.current.get(capturedTabId);
            if (
              !currentTabState ||
              currentTabState.streamGen !== capturedStreamGen ||
              !currentTabState.isReconnecting
            ) {
              return; // tab gone / superseded / already recovered -> discard
            }

            if (verdict === 'still-working') {
              // Backend alive (streaming / waiting_input / flushing). KEEP the
              // spinner; hand ongoing recovery to the 15s reconcile loop. Do NOT
              // endStreaming() here (would flip the store idle immediately).
              //
              // The 2-stage handoff (verified Gate-2): (1) the MessageStore 90s
              // watchdog flips store phase -> idle; the next reconcile tick's
              // desyncConvergeVerdict block (the PRIMARY stage-1 owner — it
              // `continue`s before control reaches the force-clear block) sees
              // storeIdle + backend-terminal-idle and calls setIsStreaming(false).
              // (2) the FOLLOWING tick's _postDisconnectUncertain block (needs
              // !isStreaming) re-pulls DB + drains. So content surfaces with NO
              // user send. forceClearStreamVerdict is only the fallback for when
              // the store never goes idle.
              //
              // BLOCKER (Gate-1): stamp _reconcileStreamStart so BOTH the desync
              // (capMs/graceMs) and force-clear (activeGuardAge) caps anchor to
              // NOW — without it the age is ~Date.now() (every cap pre-blown) and
              // the spinner is force-cleared in the backend dead->cold gap.
              // KNOWN SOFT-LIMIT (Gate-2 HIGH-2, accepted): if the backend's
              // postDisconnectFlushing flag gets stuck true, stage-1 defers up to
              // the shared 120-min long-turn cap. A tighter post-disconnect cap
              // would thread a new signal into desyncConvergeVerdict (OT01 hot
              // zone) — deferred over expanding blast radius for a backend-leak
              // edge the _postDisconnectUncertain block's own 120-min cap (:1243)
              // already backstops.
              console.log('[DisconnectHandler] Timeout — backend still working; handing recovery to reconcile loop', { capturedTabId });
              currentTabState._postDisconnectUncertain = true;
              currentTabState._postDisconnectAt = Date.now();
              currentTabState._reconcileStreamStart = Date.now();
              currentTabState._disconnectTimeoutId = undefined;
              // Leave isReconnecting + isStreaming TRUE (spinner stays).
              return;
            }

            // verdict === 'show-error': backend genuinely done/dead (or query
            // failed). Original behavior — clear + one-shot DB pull. Also set
            // _postDisconnectUncertain so a follow-up send QUEUES (not escapes ->
            // SESSION_BUSY) and the reconcile loop does the final drain/pull.
            console.warn('[DisconnectHandler] Timeout — backend done; clearing reconnecting state and recovering from DB', { capturedTabId });
            currentTabState.isReconnecting = false;
            currentTabState._disconnectTimeoutId = undefined;
            currentTabState._postDisconnectUncertain = true;
            currentTabState._postDisconnectAt = Date.now();
            // End store streaming phase — unblocks reconcile/replace
            const dcStore = messageStoreRegistry.get(capturedTabId);
            if (!dcStore) return; // Tab was closed — don't re-create store
            dcStore.endStreaming();
            // Clear via the atomic setIsStreaming(false) primitive for ALL tabs.
            setIsStreaming(false, capturedTabId);

            // Recover content from DB: backend persists messages immediately
            // during streaming, so content generated before the disconnect is
            // in DB. Fetch and reconcile to display it.
            if (sid) {
              try {
                chatService.invalidateMessageCache(sid);
                const msgs = await chatService.getSessionMessagesReconcileTail(sid, RECONCILE_TAIL);
                // Guard: store may have been destroyed between await calls
                const recoveryStore = messageStoreRegistry.get(capturedTabId);
                if (recoveryStore) recoveryStore.reconcile(msgs);
              } catch (err) {
                console.warn('[DisconnectHandler] DB recovery failed:', err);
              }
            }
          }, DISCONNECT_TIMEOUT_MS);
          // Store timeout ID so it can be cleared on recovery or tab close
          tabState._disconnectTimeoutId = disconnectTimeoutId;
        }
      }
    };
  }, [setIsStreaming]); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

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
    if (tabIsStreaming && tabState?.streamStartTime) {
      // Streaming (any phase) — restore elapsed from stored start time
      streamStartTimeRef.current = tabState.streamStartTime;
      setElapsedSeconds(Math.floor((Date.now() - tabState.streamStartTime) / 1000));
    } else {
      // Not streaming — clear everything
      streamStartTimeRef.current = null;
      setElapsedSeconds(0);
    }

    // Sync isWaitingForBusy for the new active tab
    setIsWaitingForBusy(tabState?.isWaitingForBusy ?? false);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /**
   * Manually cancel a tab's SESSION_BUSY recovery wait ("stop waiting").
   *
   * The busy poll (createStreamHandler, SESSION_BUSY branch) calls
   * getStreamingState() every 3s to learn when the backend goes idle. When the
   * backend is UNREACHABLE that call throws and is swallowed, so the poll can
   * never self-resolve — the only exit is the 10-min safety cap, which is why
   * the "Waiting for response..." spinner felt unstoppable and open-ended.
   *
   * This clears the poll locally and returns the tab to idle. It is SAFE: the
   * in-flight backend turn (if any) keeps running and its result still surfaces
   * via the always-on 15s reconcile tick once the backend returns — nothing is
   * lost. No backend round-trip is attempted (it would only fail while offline).
   */
  const cancelBusyWait = useCallback((tabId: string) => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab) return;
    // Defense-in-depth: the indicator is gated on `!isStreaming`, but never let a
    // manual cancel touch a tab that is genuinely streaming. cancelBusyWait does
    // NOT set isStreaming=false or abort, so a live stream would survive — but its
    // status label would flicker to 'idle' until the next event. A no-op here
    // keeps cancel strictly a busy-wait concern and can never perturb a live turn.
    if (tab.isStreaming) return;
    if (tab.busyPollInterval) clearInterval(tab.busyPollInterval);
    if (tab.busyPollTimeout) clearTimeout(tab.busyPollTimeout);
    tab.busyPollInterval = undefined;
    tab.busyPollTimeout = undefined;
    tab.isWaitingForBusy = false;
    if (tabId === activeTabIdRef.current) setIsWaitingForBusy(false);
    updateTabStatus(tabId, 'idle');
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs + stable setters

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
    isWaitingForBusy,
    cancelBusyWait,
  };
}
