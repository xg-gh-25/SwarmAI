/**
 * Custom hook encapsulating the chat streaming lifecycle state machine.
 *
 * Extracted from ``ChatPage.tsx`` (Phase 0 refactor) to isolate streaming
 * concerns into a testable, self-contained unit. This hook owns:
 *
 * - **State**: ``messages``, ``sessionId``, ``pendingQuestion``, ``isStreaming``,
 *   ``pendingStreamTabs`` (per-tab pending tracking)
 * - **Refs**: ``sessionIdRef``, ``messagesRef`` (scrolling lives in each TabView,
 *   pages/chat/scrollFocus.ts)
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

import { collapseAdjacentDuplicateBlocks } from '../utils/duplicateBlocks';
import { planTranscriptChange, type TranscriptChange } from './transcriptFeed';
import React, { useState, useReducer, useRef, useCallback, useMemo, useEffect } from 'react';
import { streamingReducer, INITIAL_STATE, type StreamingEvent } from './streaming-machine';
// Re-export state machine utilities for consumers
export { isActivelyStreaming, isInputBlocked, type StreamingMode, type StreamingState, type StreamingEvent } from './streaming-machine';
import type {
  Message,
  SteerReadEvent,
  ContentBlock,
  TextContent,
  ThinkingContent,
  ToolUseContent,
  ToolResultContent,
  StreamEvent,
  SubAgentProgress,
  SystemPromptMetadata,
  CompactionGuardEvent,
  PendingPermission,
  StreamingStateEntry,
  SessionActivity,
  ChatRequest,
  PendingQueueEntry,
} from '../types';
import type { PendingQuestion } from '../pages/chat/types';
import { applyCompactionEvent } from '../pages/chat/compaction';
import { shouldResurfaceQuestion, shouldResurfacePermission, queuedBehindApprovalText, isServerQueuedErrorCode, hostForInjectedTurn, firstSteerOfTurn, computeDrainRetirement, healGraceExpiryVerdict, nextReconcileDelay, type HealGraceVerdict } from './streaming-guards';
import { errorFieldsFromEvent } from './errorFields';
import { chatService } from '../services/chat';
import { sessionEvents } from '../services/sessionEvents';
import { backgroundAgentsStore } from '../stores/backgroundAgentsStore';
import {
  createTabTurnState, onPostSend, routePostEvent, routeSubEvent, onPostComplete, onPostLost,
  onSubDisconnected, syncTurnStateSession,
  type TabTurnState, type TurnAction, type TurnEnvelope, type TurnSource,
} from './sessionTurnRouter';
import { promptMetadataToCamelCase } from '../services/tscc';
import { messageStoreRegistry } from '../stores/MessageStore';

import { isOt01DiagEnabled } from '../utils/diagFlags';
import { dispatchUiCommand } from '../utils/uiCommands';
import type { UnifiedTab } from './useUnifiedTabState';
import { type TabStatus } from './useUnifiedTabState';
import { tabNoticeStore } from '../stores/tabNoticeStore';
import { noteThinking } from '../stores/thinkingProgressStore';
import { toContextWarning, isNewerSnapshot, snapshotFromEvent, onContextUsageStale, type ContextUsageSnapshot } from './contextUsage';

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

/** In-tab notice id for the critical-context warning (one per tab). */
/** In-tab notice id for "automatic recovery stopped" (one per tab). */
export const RECOVERY_EXHAUSTED_NOTICE_ID = 'recovery-exhausted';
/** In-tab notice id for the long-turn "still working" heartbeat (one per tab). */
export const STILL_WORKING_NOTICE_ID = 'still-working';
/** The still-working notice clears itself after this long without a refresh. */
export const STILL_WORKING_NOTICE_MS = 8000;
/** In-tab notice id for the MCP health warning (one per tab). */
export const MCP_HEALTH_NOTICE_ID = 'mcp-health';
/** In-tab notice id for "the connection was lost after a self-heal attempt". */
export const CONNECTION_LOST_NOTICE_ID = 'connection-lost';

/** Maximum number of auto-resends on backend-recovered for a single swallowed-question
 *  episode. The ~65s connection-phase reconnect budget covers a normal redeploy; this
 *  is the fallback for outages that outlast it. The exhausted send is re-sent when
 *  health flips back. Capped so a flapping backend (up/down/up) can't drive an
 *  unbounded resend loop. */
const RESEND_MAX_ATTEMPTS = 2;

/** Tab-notice id of "your message waits for an approval" (one per tab). */
const QUEUED_BEHIND_APPROVAL_NOTICE = 'queued-behind-approval';

/** Base delay in ms for exponential backoff (attempt 0 → 1000ms). */
const RECONNECT_BASE_DELAY_MS = 1000;

/** Maximum delay cap in ms for exponential backoff. Capped at 10s (not 30s) so the
 *  later retries stay responsive — once the backend is back, the next silent retry
 *  fires within ≤10s instead of leaving a long blind gap. With 9 attempts the
 *  schedule is 1,2,4,8,10,10,10,10,10 ≈ 65s total. */
const RECONNECT_MAX_DELAY_MS = 10000;

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
 *
 * EXPORTED so the tests assert the SAME constant the reconcile sites pass, instead of a
 * hardcoded literal that would silently diverge if this bound is ever tuned. (This file
 * already paid for that class: 306a9ad0 switched every reconcile to the tail fetch and 3
 * tests kept spying on the OLD method name, sitting RED on main unnoticed.)
 */
export const RECONCILE_TAIL = 50;

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
 * Streamed text/thinking blocks are PROVISIONAL; the assistant event's blocks are
 * the SDK's authoritative version. Each authoritative text/thinking block replaces
 * exactly one block IN PLACE — a re-emitted confirmed block, or its own
 * provisional block — or is appended when new. Nothing else is removed, so a
 * finished response or thought can never disappear or move (XG 2026-10-18).
 * Tool_use / tool_result are deduped by id. Returns the same message reference
 * when nothing changed (referential stability for React memoization).
 */
export function updateMessages(
  currentMessages: Message[],
  assistantMessageId: string,
  newContent: ContentBlock[],
  model?: string,
  turnStart = 0,
): Message[] {
  return currentMessages.map((msg) => {
    if (msg.id !== assistantMessageId) return msg;

    // Reconcile the event's text / thinking blocks IN PLACE (XG 2026-10-18: a
    // finished response or thought is never lost, and never moves). For each
    // incoming text/thinking block, in order:
    //   a. it confirms ITS provisional (streamed) block — the unconfirmed block of
    //      that type whose text it starts with — replaced where it is;
    //   c. else it confirms the last unconfirmed block of that type (the confirmed
    //      text may differ from what streamed, e.g. a cleaned leak);
    //   d. else it is new and goes at the end.
    // Nothing is matched against CONFIRMED blocks by text: every envelope arrives
    // once (sessionTurnRouter, eventSeq), and the record keeps a block the model
    // wrote twice twice — so live shows it twice too (live == reload, R6). The
    // old re-emission rule (b) and the adjacent-duplicate collapse are gone with
    // the double deliveries they covered for; a streamed copy next to the block it
    // duplicates is still dropped (collapseAdjacentDuplicateBlocks).
    // Exactly ONE provisional block is consumed per incoming block. Every other
    // provisional block stays (BUG FIX 2026-06-18: an event that carries no
    // text/thinking never wipes streamed content; 2026-10-18: one that does no
    // longer drops OTHER provisional thoughts — an earlier thought whose own
    // confirmation was lost stays visible). Tool_use / tool_result are deduped by id.
    const content = [...msg.content];
    // `turnStart`: the index where this turn's blocks begin when the bubble also
    // holds earlier turns (a continued bubble). Those blocks are final and never
    // matched: a later turn repeating an earlier text keeps both (live == reload).
    const consumed = new Set<number>(Array.from({ length: Math.min(turnStart, content.length) }, (_, i) => i));
    const appended: ContentBlock[] = [];
    let changed = false;

    const body = (b: ContentBlock): string =>
      b.type === 'text' ? ((b as TextContent).text ?? '') : ((b as ThinkingContent).thinking ?? '');

    const existingToolUseIds = new Set(
      content.filter((b): b is ToolUseContent => b.type === 'tool_use').map((b) => b.id),
    );
    const existingToolResultIds = new Set(
      content.filter((b): b is ToolResultContent => b.type === 'tool_result').map((b) => b.toolUseId),
    );

    for (const block of newContent) {
      if (block.type === 'tool_use') {
        if (!existingToolUseIds.has(block.id)) { appended.push(block); existingToolUseIds.add(block.id); }
        continue;
      }
      if (block.type === 'tool_result') {
        if (!existingToolResultIds.has(block.toolUseId)) { appended.push(block); existingToolResultIds.add(block.toolUseId); }
        continue;
      }
      if (block.type !== 'text' && block.type !== 'thinking') {
        appended.push(block);
        continue;
      }
      const type = block.type;
      const incoming = body(block);
      const confirmedBlock = { ...block, _confirmed: true } as ContentBlock;

      // a. Its own provisional block: the streamed text this block confirms.
      let provisional = -1;
      let fallback = -1;
      for (let i = content.length - 1; i >= 0; i -= 1) {
        const b = content[i];
        if (b.type !== type || (b as TextContent | ThinkingContent)._confirmed || consumed.has(i)) continue;
        if (fallback < 0) fallback = i;
        if (incoming.startsWith(body(b))) { provisional = i; break; }
      }
      if (provisional >= 0) {
        content[provisional] = confirmedBlock;
        consumed.add(provisional);
        changed = true;
        continue;
      }

      // c. Its provisional block when the confirmed text differs from what streamed
      //    (e.g. a cleaned leak): the last unconfirmed block of that type.
      if (fallback >= 0) {
        content[fallback] = confirmedBlock;
        consumed.add(fallback);
        changed = true;
        continue;
      }

      // d. New.
      appended.push(confirmedBlock);
    }

    if (!changed && appended.length === 0) return msg;
    // Earlier turns' blocks (before turnStart) are never touched; the collapse only
    // looks at this turn's, and only ever drops a STREAMED copy (utils/duplicateBlocks).
    const merged = [...content, ...appended];
    const head = Math.min(turnStart, merged.length);
    return {
      ...msg,
      content: [...merged.slice(0, head),
        ...collapseAdjacentDuplicateBlocks(merged.slice(head), 'updateMessages')],
      ...(model ? { model } : {}),
      ...(msg.isError ? { isError: false } : {}),
    };
  });
}

/** Monotonic id for stream handlers (see UnifiedTab.latestStreamHandler). */
let streamHandlerSeq = 0;

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
  fresh = false,
): Message[] {
  return applyToMessage(currentMessages, assistantMessageId, (m) => applyTextDelta(m, text, fresh));
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
  fresh = false,
): Message[] {
  return applyToMessage(currentMessages, assistantMessageId, (m) => applyThinkingDelta(m, thinking, fresh));
}

// ---------------------------------------------------------------------------
// Per-message updaters — used by MessageStore.updateLast() for single-writer flow.
// These transform a single Message (not an array), matching the store's updater API.
// ---------------------------------------------------------------------------

/**
 * Append text token to a message's last unconfirmed text block.
 * If no unconfirmed text block exists, creates a new one.
 */
export function applyTextDelta(
  msg: Message, text: string, fresh = false, block?: string, onRestart?: () => void,
): Message {
  const content = [...msg.content];
  const lastBlock = content[content.length - 1];
  if (lastBlock && lastBlock.type === 'text' && !lastBlock._confirmed
      && (!fresh || sameStreamBlock(lastBlock, block))) {
    if (fresh) onRestart?.();
    content[content.length - 1] = {
      ...lastBlock,
      text: (lastBlock.text ?? '') + text,
    };
  } else {
    content.push({ type: 'text', text, ...(block ? { _streamBlock: block } : {}) } as ContentBlock);
  }
  return { ...msg, content };
}

/**
 * A fresh start (text_start / thinking_start) for the block the last provisional
 * block already streams (the backend's `block` key: model message + block index).
 * A repeated start of the SAME block continues it: split in two, its confirmation
 * replaced one half and left the other, so the tail of the answer showed twice
 * live only (2026-10-24, docs/design/2026-10-24-bg-agent-stop-livereload.md D4).
 * No key, or another key, is a new block as before.
 */
function sameStreamBlock(b: ContentBlock, block: string | undefined): boolean {
  return !!block && (b as TextContent | ThinkingContent)._streamBlock === block;
}

/**
 * Which transport delivered a block-start event (A0 evidence, docs/design/
 * 2026-10-24-chat-stream-structure.md). Set at the two entry points (this tab's
 * POST, the window's subscription) for text_start / thinking_start only; the
 * handler that renders it may be the other one (a POST turn_start begins the turn
 * as the subscription would), so the handler's own label does not say this.
 */
const startDeliveredBy = new WeakMap<object, 'post' | 'sub'>();
function tagStart(ev: unknown, via: 'post' | 'sub'): void {
  const t = (ev as { type?: unknown } | null)?.type;
  if (t === 'text_start' || t === 'thinking_start') startDeliveredBy.set(ev as object, via);
}

/**
 * Append thinking token to a message's last unconfirmed thinking block.
 * If no unconfirmed thinking block exists, creates a new one.
 */
export function applyThinkingDelta(
  msg: Message, thinking: string, fresh = false, block?: string, onRestart?: () => void,
): Message {
  const content = [...msg.content];
  const lastBlock = content[content.length - 1];
  if (lastBlock && lastBlock.type === 'thinking' && !lastBlock._confirmed
      && (!fresh || sameStreamBlock(lastBlock, block))) {
    if (fresh) onRestart?.();
    content[content.length - 1] = {
      ...lastBlock,
      thinking: ((lastBlock as { thinking?: string }).thinking ?? '') + thinking,
    } as ContentBlock;
  } else {
    content.push({ type: 'thinking', thinking, ...(block ? { _streamBlock: block } : {}) } as ContentBlock);
  }
  return { ...msg, content };
}

/**
 * Record a `subagent_progress` event on the streaming message: the latest
 * status per sub-agent, keyed by its Agent tool_use id. A side field, not a
 * content block, so it never splits the streaming text block and never shifts
 * a block index (turnView keys rows by index).
 */
export function applySubAgentProgress(msg: Message, event: StreamEvent): Message {
  const parent = event.parentToolUseId;
  if (!parent) return msg;
  const prev = msg.subAgentProgress?.[parent];
  const next: SubAgentProgress = {
    ...prev,
    parentToolUseId: parent,
    ...(event.taskId ? { taskId: event.taskId } : {}),
    status: event.status ?? prev?.status ?? 'running',
    ...(event.description ? { description: event.description } : {}),
    ...(event.lastToolName ? { lastToolName: event.lastToolName } : {}),
    ...(typeof event.toolUses === 'number' ? { toolUses: event.toolUses } : {}),
    ...(typeof event.durationMs === 'number' ? { durationMs: event.durationMs } : {}),
    ...(event.summary ? { summary: event.summary } : {}),
  };
  return { ...msg, subAgentProgress: { ...msg.subAgentProgress, [parent]: next } };
}

/** Per-event inputs of ``applyContentEvent`` that live in the handler, not the event. */
export interface ContentEventCtx {
  /** a text_start / thinking_start came: this delta may open a new block */
  fresh?: boolean;
  /** index where this turn's blocks begin in a continued bubble (updateMessages) */
  turnStart?: number;
}

/** What ``applyContentEvent`` did: the new message, and whether a fresh start
 *  continued the block the last provisional block streams (A0 `[stream-restart]`
 *  evidence, which the handler logs). */
export interface ContentEventResult {
  msg: Message;
  restarted: boolean;
}

/**
 * The content reducer (design 2026-10-24-chat-stream-structure.md A1): one stream
 * event applied to the assistant message it renders into, blocks only. Request
 * state, activity, prompts, notices, caches and reconcile scheduling stay in the
 * handler. Events that carry no content return ``msg`` unchanged. Hot path (one
 * call per token): no allocation beyond the updater's own.
 */
export function applyContentEvent(msg: Message, event: StreamEvent, ctx: ContentEventCtx = {}): ContentEventResult {
  let restarted = false;
  const mark = () => { restarted = true; };
  let next = msg;
  switch (event.type) {
    case 'text_delta':
      if (event.text) next = applyTextDelta(msg, event.text, !!ctx.fresh, event.block, mark);
      break;
    case 'thinking_delta':
      if (event.thinking) next = applyThinkingDelta(msg, event.thinking, !!ctx.fresh, event.block, mark);
      break;
    case 'tool_result':
      if (event.content) next = updateMessages([msg], msg.id, event.content, undefined, ctx.turnStart)[0];
      break;
    case 'assistant':
      if (event.content) next = updateMessages([msg], msg.id, event.content, event.model, ctx.turnStart)[0];
      break;
    case 'subagent_progress':
      next = applySubAgentProgress(msg, event);
      break;
    default:
      break;
  }
  return { msg: next, restarted };
}

/** Apply ``fn`` to the message ``id`` of a plain list (no store yet). */
function applyToMessage(list: Message[], id: string, fn: (m: Message) => Message): Message[] {
  return list.map((m) => (m.id === id ? fn(m) : m));
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
  /** A turn of the active tab's session is in progress (backend activity). */
  isStreaming: boolean;
  /** The active tab has its own POST open (Send / Stop reads both). */
  isSending: boolean;
  /** The tab's own request opened / ended (not the bee: docs/design/chat-turn-liveness.md S3).
   *  ``keepSend``: the POST dropped but its message may still go (a reconnect follows). */
  setLocalSending: (sending: boolean, tabId?: string, opts?: { keepSend?: boolean }) => void;
  /** The backend's activity for a tab's session (seq-ordered); the only writer of
   *  isStreaming. The subscription and the poll call it. */
  applyActivity: (tabId: string, activity: SessionActivity, seq: number | undefined) => void;
  /** The tab now shows another session: its activity starts over (idle, seq 0). */
  rebindActivity: (tabId: string) => void;
  /** The POST the tab opens sends this request (kept for Retry until `accepted`). */
  noteSend: (tabId: string, request: ChatRequest) => ChatRequest;
  /** The server's queue for a tab's session (session_state `pending`, read at ``at``). */
  applyServerQueue: (tabId: string, list: PendingQueueEntry[], at: number, complete?: boolean,
    steer?: Pick<StreamingStateEntry, 'steering' | 'segments' | 'queuedReason'>) => void;
  /** Re-derive the tab's "Queued" marks (after a local change to the queue). */
  syncQueuedBadges: (tabId: string) => void;
  /** reset the queue fields when the tab's session changed */
  rebindQueue: (tabId: string) => void;
  streamingActivity: StreamingActivity | null;
  /** Debounced activity — stable for at least MIN_ACTIVITY_DISPLAY_MS. */
  displayedActivity: StreamingActivity | null;
  /** Elapsed seconds since streaming started with no content yet (Fix 9). */
  elapsedSeconds: number;

  // Per-tab pending state for ChatPage guard
  pendingStreamTabs: Set<string>;
  /** Remove a specific tab from pendingStreamTabs (e.g. on tab close). */
  clearPendingStreamTab: (tabId: string) => void;
  /** Force re-derivation of isStreaming (e.g. after tab switch). */
  bumpStreamingDerivation: () => void;

  // Fix 1: Stream generation counter
  streamGenRef: React.MutableRefObject<number>;
  incrementStreamGen: () => void;

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
  /** Read the backend's context-usage snapshot for a tab (non-blocking). */
  refreshContextUsage: (tabId: string) => Promise<void>;

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

  // (Compaction notice has no hook-level state — it is appended as a persistent
  //  system message to the per-tab MessageStore at the event site; no React slot.)

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

/** Display value of the backend context-usage snapshot (``hooks/contextUsage``).
 *  The backend is the only authority; this is what the ring / banner / compact
 *  button render. */
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
  /** Callback to reset ONE tab (by id) to a fresh session — the
   *  recovery_exhausted notice's "Start fresh session" action when automatic
   *  recovery has given up (history preserved server-side). */
  onStartFresh?: (tabId: string) => void;
  /** Callback fired when a tab binds its own backend session (session_start),
   *  for ANY tab — active OR background. Lets the owner (ChatPage) backfill a
   *  pending ToDo-dispatch record even when the tab is not the active one (the
   *  global-sessionId effect only covers the active tab). Opaque to the hook —
   *  keeps ToDo logic out of the streaming hot path. */
  onTabSessionBound?: (tabId: string, sessionId: string) => void;
}

// ---------------------------------------------------------------------------
// Hook implementation
// ---------------------------------------------------------------------------

/** The reconcile loop's entry point for a pushed session state (set by its effect). */
const pushedStateRef: { current?: (sid: string, entry: StreamingStateEntry) => void } = {};

/** Rows each store has applied from the transcript feed, with their rev. */
const feedApplied = new WeakMap<object, Map<string, number>>();

export function useChatStreamingLifecycle(
  deps: ChatStreamingLifecycleDeps,
): ChatStreamingLifecycle {
  const {
    queryClient,
    getSession,
    updateTabStatus,
    tabMapRef,
    activeTabIdRef,
    onStartFresh,
    onTabSessionBound,
  } = deps;

  // Tab/session notices go to the per-tab notice store (stores/tabNoticeStore),
  // never to the app-wide toast stack: this hook only handles tab-scoped events.

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
  // let a stale Set entry (orphaned when a clear path skipped setLocalSending)
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
  // The active tab has its own POST open (setLocalSending). Re-rendered by renderTick.
  const [, setRenderTick] = useState(0);
  const isSending = activeTabState?.localSending ?? false;


  // --- Refs: streaming lifecycle ---
  // These refs are used by stream handlers, scroll detection, etc.
  // Tab state refs (tabMapRef, activeTabIdRef) are now injected via deps
  // from the unified hook — see ChatStreamingLifecycleDeps.
  const streamGenRef = useRef<number>(0);
  const sessionIdRef = useRef<string | undefined>(sessionId);
  const messagesRef = useRef<Message[]>(messages);
  const streamStartTimeRef = useRef<number | null>(null); // Fix 9: elapsed time counter


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
  // Design: iterates ALL tabs in tabMapRef (not just active).
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
    // The last full read; a pushed `session_state` (backend core/session_state,
    // the same producer) replaces its session's entry and runs the verdicts now.
    let lastStates: Record<string, StreamingStateEntry> | null = null;

    const reconcile = async (pushed?: Record<string, StreamingStateEntry>) => {
      if (cancelled) return;
      try {
        // when this read was sent: a session it does not list was gone at a time
        // after every activity a tab applied before then (applyActivity)
        const readAt = Date.now();
        const states = pushed ? { ...(lastStates ?? {}), ...pushed } : await chatService.getStreamingState();
        if (cancelled) return;
        lastStates = states;

        let anyCleared = false;
        // The subscription keeps cursors for the sessions shown in tabs; a newly
        // shown session is joined, so a turn still running after a reload is
        // replayed from its start.
        watchOpenTabs();

        // Check ALL tabs, not just active — background tabs can hang too
        for (const [tabId, tabState] of tabMapRef.current.entries()) {
          // The bee: the backend's activity for this tab's session
          // (docs/design/chat-turn-liveness.md S3) — the only thing that turns it
          // on or off. A pushed read speaks only for the sessions it carries.
          {
            rebindActivity(tabId);
            const aSid = tabState.sessionId;
            const aEntry = aSid ? states[aSid] : undefined;
            if (aSid && aEntry?.activity && (!pushed || aSid in pushed)) {
              applyActivity(tabId, aEntry.activity, aEntry.activitySeq);
            } else if (aSid && !aEntry && !pushed
                       && (tabState.activityAt === undefined || tabState.activityAt < readAt)) {
              applyActivity(tabId, 'idle', undefined);   // its unit is gone
            }
          }
          // The server's queue (docs/design/chat-turn-liveness.md S3): from a poll
          // only when it was sent after the tab's last pushed list.
          {
            rebindQueue(tabId);
            const qSid = tabState.sessionId;
            const qEntry = qSid ? states[qSid] : undefined;
            // no entry = no unit (nothing queued it holds); an entry without
            // `pending` comes from a backend that does not send the queue: unknown
            if (qSid && !pushed && (!qEntry || qEntry.pending)
                && (tabState._serverQueueAt === undefined || tabState._serverQueueAt < readAt)) {
              const qList = qEntry?.pending ?? [];
              applyServerQueue(tabId, qList, readAt, !qEntry || qEntry.pendingComplete === true, qEntry);
            }
          }
          // Post-disconnect mode ends once the backend says the session is idle
          // (whether or not this tab saw it go idle: the turn may have ended first).
          if (tabState._postDisconnectUncertain && tabState.activity === 'idle'
              && tabState.activitySeq > 0 && !tabState.isWaitingForBusy) {
            onActivityIdleRef.current(tabId);
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
                // The inline form now hosts the question — retire the no-host
                // in-tab notice we raised on an earlier poll (else it coexists
                // with the form).
                if (tabState._pendingQuestionToastId) {
                  tabNoticeStore.dismiss(tabId, tabState._pendingQuestionToastId);
                  tabState._pendingQuestionToastId = undefined;
                }
                anyCleared = true;
              } else {
                // No assistant bubble to host the block (question's SSE was lost
                // before any assistant content). surfacePendingQuestion needs a
                // host message, so we can't render the form inline — but the user
                // must still be able to DISCOVER the pending question. Raise a
                // persistent notice IN THAT TAB (idempotent via stable id; the
                // tab header also shows waiting_input). Track its id so it can be
                // retired when the form takes over or the question is abandoned.
                const noticeId = `ask-uq-${pqPayload.toolUseId}`;
                tabNoticeStore.post(tabState, mirrorSid, {
                  id: noticeId,
                  severity: 'info',
                  message: 'Swarm is asking a question in this session.',
                });
                tabState._pendingQuestionToastId = noticeId;
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
              // + any leaked no-host discovery notice.
              tabState.pendingQuestion = null;
              tabState._answeredToolUseId = undefined;
              if (tabState._pendingQuestionToastId) {
                tabNoticeStore.dismiss(tabId, tabState._pendingQuestionToastId);
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

            // Re-surface a lost command-permission prompt (core/permission_prompt,
            // 2026-10-22): the backend is blocked on it but this tab never got its
            // live event, so the session would sit waiting with nothing on screen.
            if (
              shouldResurfacePermission({
                backendWaitingInput: mirrorState.waitingInput,
                backendPendingPermission: mirrorState.pendingPermission ?? null,
                currentPendingRequestId: tabState.pendingPermissionRequestId ?? null,
                decidedRequestId: tabState._decidedPermissionId ?? null,
              })
            ) {
              const perm = mirrorState.pendingPermission!;
              const store = messageStoreRegistry.get(tabId);
              const msgs = store?.messages ?? tabState.messages ?? [];
              const host = findLast(msgs, (m) => m.role === 'assistant');
              const isActive = tabId === activeTabIdRef.current;
              if (host) {
                surfacePendingPermission(tabId, host.id, perm, { isActive, sessionId: mirrorSid });
                if (tabState._pendingPermissionNoticeId) {
                  tabNoticeStore.dismiss(tabId, tabState._pendingPermissionNoticeId);
                  tabState._pendingPermissionNoticeId = undefined;
                }
              } else {
                const noticeId = `perm-${perm.requestId}`;
                tabNoticeStore.post(tabState, mirrorSid, {
                  id: noticeId,
                  severity: 'info',
                  message: `Swarm is waiting for your approval: ${perm.toolName}.`,
                });
                tabState._pendingPermissionNoticeId = noticeId;
              }
              anyCleared = true;
            } else if (
              !mirrorState.waitingInput &&
              !tabState.isStreaming &&
              (tabState.pendingPermissionRequestId || tabState._pendingPermissionNoticeId)
            ) {
              // SYMMETRIC RETIRE: the backend no longer waits on a prompt this tab
              // still offers (decided elsewhere, stopped, timed out). Its card goes
              // non-actionable; the DB copy says how it ended on the next reconcile.
              tabState.pendingPermissionRequestId = null;
              tabState._decidedPermissionId = undefined;
              if (tabState._pendingPermissionNoticeId) {
                tabNoticeStore.dismiss(tabId, tabState._pendingPermissionNoticeId);
                tabState._pendingPermissionNoticeId = undefined;
              }
              if (tabId === activeTabIdRef.current) setPendingPermissionRequestId(null);
              anyCleared = true;
            } else if (
              tabState._decidedPermissionId &&
              mirrorState.pendingPermission?.requestId !== tabState._decidedPermissionId
            ) {
              tabState._decidedPermissionId = undefined;
            }

            // Say why sent messages are not moving when they wait on an approval.
            {
              const queuedText = queuedBehindApprovalText({
                backendWaitingInput: mirrorState.waitingInput,
                backendPendingPermission: mirrorState.pendingPermission ?? null,
                serverPendingCount: mirrorState.pendingCount ?? 0,
              });
              if (queuedText) {
                tabNoticeStore.post(tabState, mirrorSid, {
                  id: QUEUED_BEHIND_APPROVAL_NOTICE, severity: 'info', message: queuedText,
                });
                tabState._queuedBehindApprovalNotice = true;
              } else if (tabState._queuedBehindApprovalNotice) {
                tabNoticeStore.dismiss(tabId, QUEUED_BEHIND_APPROVAL_NOTICE);
                tabState._queuedBehindApprovalNotice = false;
              }
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
              // A server-side drain delivered a coalesced turn to the DB. When the
              // tab's subscription did not render it live (it was down, or the tab
              // reconnected after), the drained turn's user+assistant content
              // exists ONLY in the DB. The design's stated
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
              const routedTurn = tabState._turnRoute?.bound;
              if (routedTurn && !routedTurn.completed) {
                // A turn is rendering in this tab (the drained one, via the
                // subscription): it retires the queued mirror itself at its end.
                // Retry this surface on a later tick — _lastDrainedSeqs stays.
              } else if (drainSid) {
                chatService.invalidateMessageCache(drainSid);
                chatService.getSessionMessagesReconcileTail(drainSid, RECONCILE_TAIL).then((msgs) => {
                  if (cancelled) return;
                  const s = messageStoreRegistry.getOrCreate(tabId, { sessionId: drainSid });
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
                  // nothing pending on the server: the rejected send was delivered
                  if (drain.serverPendingCount === 0 && tabState.isWaitingForBusy) {
                    tabState.isWaitingForBusy = false;
                    if (tabId === activeTabIdRef.current) setIsWaitingForBusy(false);
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
    // A state change pushed on the subscription: the same verdicts, now (before
    // the first full read there is nothing to merge it into: that read is soon).
    pushedStateRef.current = (sid, entry) => {
      if (cancelled || !lastStates) return;
      void reconcile({ [sid]: entry });
    };

    return () => {
      cancelled = true;
      pushedStateRef.current = undefined;
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

  // Compaction notice is appended as a system message at the event site (see the
  // context_compacted branch) — no React-state slot, so none is declared here.

  // --- Context usage: ONE apply path for backend snapshots (SSE + GET) ---
  // The backend owns the value (core/context_usage.py); the frontend only
  // displays the newest snapshot it has seen for each tab.
  const applyContextUsage = useCallback((tabId: string | null, raw: ContextUsageSnapshot) => {
    const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
    // measuredAt ordering only compares snapshots of the SAME session: a tab that
    // switches to an older session must accept that session's (older) value.
    if (
      tabState
      && tabState.contextMeasuredFor === tabState.sessionId
      && !isNewerSnapshot(tabState.contextMeasuredAt, raw)
    ) return;
    const next = toContextWarning(raw);
    let unchanged = false;
    if (tabState) {
      const prev = tabState.contextWarning;
      unchanged = prev !== null && next !== null && prev.level === next.level
        && prev.pct === next.pct && prev.tokensEst === next.tokensEst
        && prev.message === next.message;
      if (!unchanged) tabState.contextWarning = next;
      if (raw.measuredAt !== undefined) {
        tabState.contextMeasuredAt = raw.measuredAt;
        tabState.contextMeasuredFor = tabState.sessionId;
      }
      // No in-tab notice for a full context: the CLI summarizes earlier messages
      // automatically at 75% and the turn continues (core/prompt_builder.py
      // AUTOCOMPACT_TRIGGER_FRACTION); the context ring shows the level, and a
      // failed compaction has its own notice (CompactNotice).
    }
    // One snapshot per API call: skip the re-render when nothing visible changed.
    if (!unchanged && (tabId === null || tabId === activeTabIdRef.current)) {
      setContextWarning(next);
    }
  }, [tabMapRef, activeTabIdRef]);

  /** Read the backend snapshot for a tab's session (tab activation, app
   *  restart, after compact/refresh). Fire-and-forget for callers: never
   *  throws, never blocks render. A result for a session the tab no longer
   *  holds is dropped. */
  const refreshContextUsage = useCallback(async (tabId: string): Promise<void> => {
    const sessionId = tabMapRef.current.get(tabId)?.sessionId;
    if (!sessionId) return;
    try {
      const raw = await chatService.getContextUsage(sessionId);
      if (tabMapRef.current.get(tabId)?.sessionId !== sessionId) return;
      applyContextUsage(tabId, raw);
    } catch (err) {
      console.debug('[contextUsage] refresh failed', err);
    }
  }, [tabMapRef, applyContextUsage]);

  // A manual compact / refresh changes the backend value outside any stream:
  // refetch every tab holding that session.
  useEffect(() => onContextUsageStale((sessionId) => {
    for (const [tabId, tab] of tabMapRef.current) {
      if (tab.sessionId === sessionId) void refreshContextUsage(tabId);
    }
  }), [tabMapRef, refreshContextUsage]);

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
   * This tab's own POST to /chat/stream opened (true) or ended (false). Only the
   * tab's request state: the Send / Stop button and the queue-or-send decision
   * read it together with isStreaming; the bee does not (docs/design/
   * chat-turn-liveness.md S3). When no ``tabId`` is given, the active tab.
   */
  const setLocalSending = useCallback(
    (sending: boolean, tabId?: string, opts?: { keepSend?: boolean }) => {
      const targetTabId = tabId ?? activeTabIdRef.current;
      const tabState = targetTabId ? tabMapRef.current.get(targetTabId) : undefined;
      if (!tabState) return;
      // ── Per-tab state machine dispatch (P5) ── follows the tab's request:
      // SEND_MESSAGE when one opens, USER_STOP when it ends early.
      const tabMode = tabState.streamState.mode;
      if (sending && tabMode === 'idle') {
        tabState.streamState = streamingReducer(tabState.streamState, { type: 'SEND_MESSAGE' });
      } else if (!sending && tabMode !== 'idle' && tabMode !== 'error'
        && tabMode !== 'waiting_input' && tabMode !== 'permission_needed') {
        tabState.streamState = streamingReducer(tabState.streamState, { type: 'USER_STOP' });
      }
      if (!sending && tabState._openSendCid && !opts?.keepSend) {
        // The request is over. Its message was never accepted (no `accepted`
        // receipt): its bubble says "Not sent" and offers Retry (S3, T7). A
        // stopped send is the user's own end: nothing to retry, forget it.
        const cid = tabState._openSendCid;
        tabState._openSendCid = undefined;
        if (tabState.userStopped) tabState._unsentRequests?.delete(cid);
        else if (tabState._unsentRequests?.has(cid)) markUnsent(targetTabId!, cid);
      }
      if (tabState.localSending === sending) return;
      // AUTHORIZED WRITER (readonly elsewhere)
      (tabState as { localSending: boolean }).localSending = sending;
      setRenderTick((n) => n + 1);
    },
    [], // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs
  );

  /**
   * A tab's activity is its session's: when the tab now shows another session (a
   * reset, a resumed history, its first session), what it held is no news about
   * this one. Start over — idle, seq 0 — without the turn-end work (that belongs
   * to the old session's turn). Called before every apply, from the poll for every
   * tab, and by ChatPage where it rebinds a tab.
   */
  const rebindActivity = useCallback((tabId: string) => {
    const tabState = tabMapRef.current.get(tabId);
    if (!tabState || tabState.activitySid === tabState.sessionId) return;
    tabState.activitySid = tabState.sessionId;
    tabState.activitySeq = 0;
    tabState.activityAt = undefined;
    if ((tabState.activity ?? 'idle') !== 'idle' || tabState.isStreaming) {
      tabState.activity = 'idle';
      (tabState as { isStreaming: boolean }).isStreaming = false;   // AUTHORIZED WRITER
      tabState.streamStartTime = undefined;
      setPendingStreamTabs((prev) => {
        if (!prev.has(tabId)) return prev;
        const next = new Set(prev);
        next.delete(tabId);
        return next;
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /**
   * The POST this tab opens sends ``request`` (its message, ``request.clientId``):
   * kept until the backend accepts it, and the request Retry sends again. Every
   * send path calls this right before chatService.streamChat. Returns the request.
   */
  const noteSend = useCallback((tabId: string, request: ChatRequest): ChatRequest => {
    const tabState = tabMapRef.current.get(tabId);
    const cid = request.clientId;
    if (tabState && cid) {
      if (!tabState._unsentRequests) tabState._unsentRequests = new Map();
      tabState._unsentRequests.set(cid, request);
      tabState._openSendCid = cid;
    }
    return request;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /** Mark the user bubble of an unaccepted message "Not sent", and keep only the
   *  requests whose bubble can still be retried (a reset / reconcile drops them). */
  const markUnsent = (tabId: string, cid: string) => {
    const tabState = tabMapRef.current.get(tabId);
    const store = messageStoreRegistry.get(tabId);
    if (!tabState || !store) return;
    const isBubble = (m: Message, c: string) => m.role === 'user' && (m.id === c || m.metadata?.client_id === c);
    const bubble = store.messages.find((m) => isBubble(m, cid));
    if (bubble && bubble.sendState !== 'failed') store.updateById(bubble.id, (m) => ({ ...m, sendState: 'failed' }));
    for (const kept of [...(tabState._unsentRequests?.keys() ?? [])]) {
      // still in flight: the open POST's, or a busy send awaiting its receipt
      if (kept === cid || tabState._openSendCid === kept || tabState._queuedLocal?.has(kept)) continue;
      if (!store.messages.some((m) => isBubble(m, kept) && m.sendState === 'failed')) tabState._unsentRequests!.delete(kept);
    }
    if (!bubble) tabState._unsentRequests?.delete(cid);
  };

  /** The queue fields belong to one session: when the tab's session changes they
   *  start over (eventSeq is per session; an old list says nothing of the new one). */
  const rebindQueue = useCallback((tabId: string) => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab || tab._serverQueueSid === tab.sessionId) return;
    if (tab._serverQueueSid !== undefined) {
      tab._serverQueue = undefined;
      tab._serverQueueAt = undefined;
      tab._serverQueueSeq = undefined;
      tab._serverQueueComplete = undefined;
      tab._queuedLocal = undefined;
      tab._steerLocal = undefined;
      tab._serverSteering = undefined;
      tab._serverQueuedReason = undefined;
      tab._turnSegments = undefined;
    }
    tab._serverQueueSid = tab.sessionId;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /** Set each user bubble's "Queued" and steer "Waiting" marks from the server's
   *  lists + the tab's just-sent ones (store write only where it changes).
   *  Waiting (design 2026-10-25-chat-steer-frontend.md §4): on the snapshot's
   *  ``steering`` or still decided locally (_steerLocal); it is sticky -- only a
   *  read (``metadata.steered``), the queue or the turn's end takes it away, so a
   *  snapshot that drops it before the read arrives does not blank the line. */
  const syncQueuedBadges = useCallback((tabId: string, onList?: Set<string>) => {
    const tab = tabMapRef.current.get(tabId);
    const store = messageStoreRegistry.get(tabId);
    if (!tab || !store) return;
    const list = tab._serverQueue ?? [];
    const queued = onList ?? new Set(list.filter((p) => !p.claimed && p.clientId).map((p) => p.clientId!));
    for (const cid of tab._queuedLocal?.keys() ?? []) queued.add(cid);
    // a capped list speaks only for the rows it carries
    const partial = tab._serverQueueComplete === false;
    const listed = partial ? new Set(list.map((p) => p.clientId).filter(Boolean) as string[]) : null;
    // History leaves queued (unsent) rows out: one the store does not show yet
    // (after a reload, in a restored tab) is shown from the list, so it can be
    // seen and cancelled. Its DB row replaces it by client_id once delivered.
    const shown = new Set<string>();
    for (const m of store.messages) {
      shown.add(m.id);
      const c = m.metadata?.client_id as string | undefined;
      if (c) shown.add(c);
    }
    for (const p of list) {
      if (p.claimed || !p.clientId || shown.has(p.clientId) || shown.has(p.id)) continue;
      shown.add(p.clientId);
      store.append({ id: p.clientId, role: 'user', content: [{ type: 'text', text: p.preview || '[Attachments]' }],
        timestamp: p.createdAt || new Date().toISOString(), metadata: { client_id: p.clientId }, isQueued: true });
    }
    // a steer the store does not show (another tab's, after a reload): its bubble
    // from the preview, provisional until the feed brings the row (§8)
    const steering = tab._serverSteering ?? [];
    for (const s of steering) {
      if (shown.has(s.clientId)) continue;
      shown.add(s.clientId);
      store.append({ id: s.clientId, role: 'user', content: [{ type: 'text', text: s.preview || '[Attachments]' }],
        timestamp: new Date().toISOString(), metadata: { client_id: s.clientId, provisional: true }, steerWaiting: true });
    }
    const waitingNow = new Set(steering.map((s) => s.clientId));
    for (const cid of tab._steerLocal?.keys() ?? []) waitingNow.add(cid);
    const reasonOf = (m: Message, cid: string) =>
      tab._queuedLocal?.has(cid) ? m.queuedReason : (tab._serverQueuedReason ?? undefined);
    for (const m of store.messages) {
      if (m.role !== 'user') continue;
      const cid = (m.metadata?.client_id as string | undefined) ?? m.id;
      const want = queued.has(cid) || queued.has(m.id)
        || (!!listed && !!m.isQueued && !listed.has(cid) && !listed.has(m.id));
      const read = !!m.metadata?.steered;
      const wait = !want && !read && (waitingNow.has(cid) || waitingNow.has(m.id) || !!m.steerWaiting);
      const reason = want ? reasonOf(m, cid) : undefined;
      if (!!m.isQueued !== want || !!m.steerWaiting !== wait || m.queuedReason !== reason) {
        store.updateById(m.id, (x) => ({ ...x, isQueued: want || undefined, steerWaiting: wait || undefined,
          queuedReason: reason }));
      }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /**
   * The session's queue as the server holds it (session_state `pending`) is the
   * tab's queue (docs/design/chat-turn-liveness.md S3): a user bubble shows
   * "Queued" (with Cancel) while its client_id is on it, unclaimed. A message this
   * tab just sent while busy counts as queued until a list read after its
   * `accepted` decides (tab._queuedLocal). ``at`` = when the list was read (a
   * poll's send time, a push's arrival).
   */
  const applyServerQueue = useCallback((tabId: string, list: PendingQueueEntry[], at: number, complete = true,
                                        steer?: Pick<StreamingStateEntry, 'steering' | 'segments' | 'queuedReason'>) => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab) return;
    rebindQueue(tabId);
    tab._serverQueue = list;
    tab._serverQueueAt = at;
    tab._serverQueueComplete = complete;
    // the steer fields come with the same snapshot (absent = none)
    tab._serverSteering = steer?.steering ?? [];
    tab._serverQueuedReason = steer?.queuedReason;
    tab._turnSegments = steer?.segments ?? [];
    const onList = new Set(list.filter((p) => !p.claimed && p.clientId).map((p) => p.clientId!));
    const listed = new Set(list.map((p) => p.clientId).filter(Boolean) as string[]);
    for (const [cid, acceptedAt] of [...(tab._queuedLocal ?? new Map()).entries()]) {
      // the server decides now: a list read after the receipt that covers it
      if (acceptedAt !== undefined && at > acceptedAt && (complete || listed.has(cid))) tab._queuedLocal!.delete(cid);
    }
    const steeringIds = new Set(tab._serverSteering.map((s) => s.clientId));
    for (const [cid, acceptedAt] of [...(tab._steerLocal ?? new Map<string, number>()).entries()]) {
      // the server decides a steer once a list read after its receipt carries it
      if (at > acceptedAt && (steeringIds.has(cid) || onList.has(cid))) tab._steerLocal!.delete(cid);
    }
    syncQueuedBadges(tabId, onList);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- reads from refs

  /** Turn-end work, run where the session's activity goes back to idle (the one
   *  place a turn is known to have ended, whichever way it ended): align the tab
   *  with the DB, leave post-disconnect mode. */
  const onActivityIdleRef = useRef<(tabId: string) => void>(() => {});

  /**
   * THE writer of ``tab.isStreaming`` (docs/design/chat-turn-liveness.md S3): the
   * backend's activity for the tab's session, from the push or the poll. A seq not
   * larger than the tab's is older news and is ignored. ``seq === undefined`` is a
   * poll that no longer lists the session (its unit is gone): idle, at the seq the
   * tab has, so any later activity still wins.
   */
  const applyActivity = useCallback(
    (tabId: string, activity: SessionActivity, seq: number | undefined) => {
      const tabState = tabMapRef.current.get(tabId);
      if (!tabState) return;
      rebindActivity(tabId);
      if (seq !== undefined) {
        if (seq <= (tabState.activitySeq ?? 0)) return;
        tabState.activitySeq = seq;
      }
      tabState.activityAt = Date.now();
      if (tabState.activity === activity) {
        // Fresh idle news on a tab already idle: when the tab still looks mid-turn
        // and no POST of it is open, the turn's busy activity and its end were both
        // lost (a disconnect) — do the turn-end work now.
        if (activity === 'idle' && seq !== undefined && !tabState.localSending
            && (messageStoreRegistry.get(tabId)?.phase === 'streaming' || tabState.status === 'streaming'
                || tabState.isReconnecting || tabState.isResuming || tabState._postDisconnectUncertain)) {
          onActivityIdleRef.current(tabId);
        }
        return;
      }
      const wasBusy = (tabState.activity ?? 'idle') !== 'idle';
      tabState.activity = activity;
      const busy = activity !== 'idle';
      if (busy !== wasBusy) {
        // AUTHORIZED WRITER: readonly bypass — the only place isStreaming changes.
        (tabState as { isStreaming: boolean }).isStreaming = busy;
        // elapsed timer of the bee (kept across tab switches)
        tabState.streamStartTime = busy ? (tabState.streamStartTime ?? Date.now()) : undefined;
      }
      setPendingStreamTabs((prev) => {
        const next = new Set(prev);   // a new Set: the phase text may have moved
        if (busy) next.add(tabId); else next.delete(tabId);
        return next;
      });
      if (wasBusy && !busy) onActivityIdleRef.current(tabId);
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

  // The turn-end work at the session's idle edge (applyActivity): the same for
  // every way a turn ends, so no path can skip it.
  onActivityIdleRef.current = (tabId: string) => {
    const tabState = tabMapRef.current.get(tabId);
    if (!tabState) return;
    tabState.isReconnecting = false;
    tabState.isResuming = false;
    if (tabState._resumeTimeoutId) { clearTimeout(tabState._resumeTimeoutId); tabState._resumeTimeoutId = undefined; }
    // The turn is over. A POST of this tab still open ends its own store phase when
    // its tail arrives; otherwise nothing else will (a lost turn_end, a turn this
    // tab only watched), and the DB read below would wait behind the phase gate.
    if (!tabState.localSending) messageStoreRegistry.get(tabId)?.endStreaming();
    if (tabState.status === 'streaming') updateTabStatus(tabId, 'idle');
    // the DB has the whole turn: align the tab with it (phase-gated, dedups by id)
    scheduleTurnEndReconcile(tabState.sessionId, tabId);
    // post-disconnect mode ends with the turn (queued messages are the server's)
    tabState._postDisconnectUncertain = false;
    // a steer still Waiting when its turn ends was not read in it: the queue or
    // the history has it now (design 2026-10-25-chat-steer-frontend.md §4)
    tabState._steerLocal = undefined;
    tabState._serverSteering = undefined;
    const store = messageStoreRegistry.get(tabId);
    for (const m of store?.messages ?? []) {
      if (m.steerWaiting) store!.updateById(m.id, (x) => ({ ...x, steerWaiting: undefined }));
    }
  };

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
      setLocalSending(false, tabId);
      incrementStreamGen();
      updateTabStatus(tabId, 'waiting_input');

      // (5b) Turn-end DB reconcile (reconcile-gap): this terminal path emits NO
      // `result` event, so without this the streamed buffer (possibly missing a
      // tail block) is never corrected against the complete DB rows. The
      // synthesized ask_user_question block appended in (2) survives reconcile —
      // _applyMerge carries forward local-only interactive blocks (PART 1).
      scheduleTurnEndReconcile(tabState?.sessionId ?? opts.sessionId, tabId);

      // (6) Background tab: the tab header's waiting_input indicator is the
      //     signal (no top-right toast — that would float over another tab).
      const persistSessionId = tabState?.sessionId ?? opts.sessionId;
      if (persistSessionId && tabState) {
        persistPendingState(persistSessionId, tabState.messages, pq);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refs + stable setters
    [dispatch, setMessages, setPendingQuestion, setSessionId, setLocalSending, incrementStreamGen, updateTabStatus, scheduleTurnEndReconcile],
  );

  /**
   * Surface a command-permission prompt in a tab — the ONE path for the live
   * `cmd_permission_request` event and for the mirror's re-surface of a prompt the
   * tab never got (core/permission_prompt, 2026-10-22):
   *  1. append the block to the host assistant message (idempotent by requestId —
   *     the stored copy may already be there from a reconcile)
   *  2. tabState.pendingPermissionRequestId (+ active-tab React state): the card is live
   *  3. end streaming, reconcile from the DB, status → permission_needed
   */
  const surfacePendingPermission = useCallback(
    (
      tabId: string | null,
      assistantMessageId: string,
      perm: PendingPermission,
      opts: { isActive: boolean; sessionId?: string },
    ) => {
      const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
      const permBlock = {
        type: 'cmd_permission_request' as const,
        requestId: perm.requestId,
        toolName: perm.toolName,
        toolInput: perm.toolInput,
        reason: perm.reason || '',
        options: perm.options?.length ? perm.options : ['approve', 'deny'],
      };
      const has = (m: Message) => m.id === assistantMessageId && m.content.some((b) =>
        b.type === 'cmd_permission_request' && (b as { requestId?: string }).requestId === perm.requestId);
      const store = tabId ? messageStoreRegistry.get(tabId) : undefined;
      if (store) {
        if (!store.messages.some(has)) {
          store.updateLast(
            (msg) => ({ ...msg, content: [...msg.content, permBlock] }),
            (msg) => msg.id === assistantMessageId,
          );
        }
        if (tabState) tabState.messages = store.messages;
      } else if (tabState && !tabState.messages.some(has)) {
        tabState.messages = tabState.messages.map((msg) =>
          msg.id === assistantMessageId ? { ...msg, content: [...msg.content, permBlock] } : msg,
        );
        if (opts.isActive) setMessages(tabState.messages);
      } else if (!tabState && opts.isActive) {
        setMessages((prev: Message[]) => (prev.some(has) ? prev : prev.map((msg) =>
          msg.id === assistantMessageId ? { ...msg, content: [...msg.content, permBlock] } : msg)));
      }

      if (tabState) {
        if (opts.sessionId) tabState.sessionId = opts.sessionId;
        tabState.pendingPermissionRequestId = perm.requestId;
      }
      if (opts.isActive) {
        if (opts.sessionId) setSessionId(opts.sessionId);
        setPendingPermissionRequestId(perm.requestId);
      }
      if (store) store.endStreaming();
      setLocalSending(false, tabId ?? undefined);
      incrementStreamGen();
      // Turn-end DB reconcile (reconcile-gap): this terminal path emits NO
      // `result`; the block survives reconcile (stored, and carried by _applyMerge).
      scheduleTurnEndReconcile(opts.sessionId ?? tabState?.sessionId, tabId);
      if (tabId) updateTabStatus(tabId, 'permission_needed');
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refs + stable setters
    [setMessages, setSessionId, setLocalSending, incrementStreamGen, updateTabStatus, scheduleTurnEndReconcile],
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

  // --- Stream handler factories (tab-aware — Fix 6) ---

  // ── Session turn routing ────────────────────────────────────────────
  // Every turn of a tab's session reaches it from the window's session
  // subscription (services/sessionEvents); its own sends also get a POST stream.
  // hooks/sessionTurnRouter decides which source renders each event (the
  // subscription while it is connected, the POST only as a fallback), renders
  // each eventSeq once, begins turns the tab did not send (drained messages,
  // CLI-initiated turns, channel turns), and turns a lost POST into a detach.
  // The POST factories below wrap their handlers through it; the subscription
  // effect feeds it. turnRunRef is assigned after the factories exist.
  const turnStateOf = (tab: UnifiedTab): TabTurnState => {
    if (!tab._turnRoute) tab._turnRoute = createTabTurnState(tab.sessionId);
    // eventSeq is per session: a tab that moved to another session starts over
    // (the POST handlers stay — a send for the new session may be in flight).
    if (syncTurnStateSession(tab._turnRoute, tab.sessionId) && tab._turnHandlers) {
      tab._turnHandlers.subRender = undefined;
      tab._turnHandlers.subComplete = undefined;
    }
    return tab._turnRoute;
  };
  const turnHandlersOf = (tab: UnifiedTab) => {
    if (!tab._turnHandlers) tab._turnHandlers = {};
    return tab._turnHandlers;
  };
  const turnRunRef = useRef<(
    tabId: string, actions: TurnAction[], postRender?: (event: StreamEvent) => void,
    postComplete?: () => void,
  ) => void>(() => {});

  /** Keep the window's subscription on the sessions shown in tabs. */
  const watchOpenTabs = () => {
    sessionEvents.watch(
      Array.from(tabMapRef.current.values(), (t) => t.sessionId).filter((s): s is string => !!s),
    );
  };

  /** The bubble a handler made for ``host`` writes into: ``host``, or the segment
   *  a steer opened after it in the running turn (design
   *  2026-10-25-chat-steer-frontend.md §6). */
  const segmentTargetOf = (tabId: string | null | undefined, host: string): string => {
    const seg = tabId ? tabMapRef.current.get(tabId)?._turnHandlers?.segmentTarget : undefined;
    return seg?.byHost[host] ?? host;
  };

  /**
   * The CLI read a steer (turn event ``user_steered``, design
   * 2026-10-25-chat-steer-frontend.md §5): its bubble is labelled in place with
   * the row's own field (``metadata.steered``: ``during`` / ``after``), and --
   * while the turn streams -- the next segment opens right after it: the bubble
   * ``rowKey`` (= ``{clientId}-asst``, the key its DB rows carry), which the
   * handler of ``host`` writes into from now on. A steer segment still empty
   * when the next steer is read is taken out (the DB has no row for it). A
   * steer bubble the tab does not have yet comes from the snapshot's preview.
   */
  const applySteerRead = (tabId: string, ev: SteerReadEvent, host: string) => {
    const tab = tabMapRef.current.get(tabId);
    const store = messageStoreRegistry.get(tabId);
    const cid = ev.clientId;
    if (!tab || !store || !cid) return;
    const label = ev.outcome === 'opened' ? 'after' : 'during';
    const isSteer = (m: Message) => m.role === 'user' && (m.id === cid || m.metadata?.client_id === cid);
    if (!store.messages.some(isSteer)) {
      const preview = tab._serverSteering?.find((s) => s.clientId === cid)?.preview ?? '';
      store.append({ id: cid, role: 'user', content: [{ type: 'text', text: preview }],
        timestamp: new Date().toISOString(), metadata: { client_id: cid, provisional: true } });
    }
    const bubble = store.messages.find(isSteer)!;
    store.updateById(bubble.id, (m) => ({ ...m, isQueued: undefined, steerWaiting: undefined, queuedReason: undefined,
      metadata: { ...(m.metadata ?? {}), steered: label } }));
    tab._queuedLocal?.delete(cid);
    tab._steerLocal?.delete(cid);
    if (tab._serverSteering) tab._serverSteering = tab._serverSteering.filter((s) => s.clientId !== cid);
    // the turn's segment keys as the backend has them: a replay clears them (§8)
    const segs = (ev as { segments?: unknown }).segments;
    if (Array.isArray(segs)) tab._turnSegments = segs.filter((k): k is string => typeof k === 'string');
    if (store.phase !== 'streaming') return;   // a Stop / the turn ended: the label only
    const handlers = turnHandlersOf(tab);
    const turnId = (ev as { turnId?: string }).turnId;
    const prev = handlers.segmentTarget && handlers.segmentTarget.turnId === turnId ? handlers.segmentTarget.byHost : {};
    const current = prev[host];
    if (current && current !== host) {
      const seg = store.messages.find((m) => m.id === current);
      if (seg && seg.role === 'assistant' && (seg.content?.length ?? 0) === 0) store.remove((m) => m.id === current);
    }
    const rowKey = ev.rowKey || `${cid}-asst`;
    if (!store.messages.some((m) => m.id === rowKey)) {
      const msgs = store.messages;
      const i = msgs.findIndex((m) => m.id === bubble.id);
      const placeholder: Message = { id: rowKey, role: 'assistant', content: [], timestamp: new Date().toISOString(),
        metadata: { client_id: rowKey } };
      if (i < 0 || i === msgs.length - 1) store.append(placeholder);
      else store.insertBefore(msgs[i + 1].id, placeholder);
    }
    handlers.segmentTarget = { turnId, byHost: { ...prev, [host]: rowKey } };
    if (tabId === activeTabIdRef.current) setMessages(store.getSnapshot());
  };

  const wrapPostHandler = (
    tabId: string | null | undefined,
    inner: (event: StreamEvent) => void,
    bubbleId: string,
  ): ((event: StreamEvent) => void) => {
    const tab = tabId ? tabMapRef.current.get(tabId) : undefined;
    if (!tabId || !tab) return inner;
    onPostSend(turnStateOf(tab));
    turnHandlersOf(tab).postRender = inner;
    turnHandlersOf(tab).postBubbleId = bubbleId;
    return (event: StreamEvent) => {
      const live = tabMapRef.current.get(tabId);
      tagStart(event, 'post');
      if (!live) { inner(event); return; }
      // this POST's own handler renders what the router gives the POST side
      turnRunRef.current(tabId, routePostEvent(
        turnStateOf(live), event as TurnEnvelope, sessionEvents.isConnected(),
      ), inner);
    };
  };

  const createStreamHandler = useCallback(
    (hostMessageId: string, tabId?: string, source: 'post' | 'sub' = 'post') => {
      // Capture the tab this handler belongs to. Falls back to active tab
      // for backward compatibility with single-tab usage.
      const capturedTabId = tabId ?? activeTabIdRef.current;
      // The bubble this handler writes into: the one it was made for, or the
      // segment a steer opened after it (segmentTargetOf, §6). Read per event.
      const currentTarget = (): string => segmentTargetOf(capturedTabId, hostMessageId);
      // Evidence (docs/design/2026-10-24-chat-stream-structure.md A0): a repeated
      // start of a block already streaming, absorbed by its block key. Which path
      // delivers it decides whether Track B is needed. Once per block, no content.
      const restartedBlocks = new Set<string>();
      // the transport of the start that opened the current text / thinking block
      let textStartVia: 'post' | 'sub' | null = null;
      let thinkingStartVia: 'post' | 'sub' | null = null;
      const logStreamRestart = (block: string, kind: 'text' | 'thinking', ev: StreamEvent): void => {
        const key = `${kind}:${block}`;
        if (restartedBlocks.has(key)) return;
        restartedBlocks.add(key);
        const seq = (ev as { eventSeq?: unknown }).eventSeq;
        console.warn('[stream-restart]', {
          block, kind, via: kind === 'text' ? textStartVia : thinkingStartVia, handler: source,
          eventSeq: typeof seq === 'number' ? seq : null,
        });
      };
      // Where this turn begins in its bubble: >0 when the bubble also holds the
      // turns before it (beginSubscriptionTurn hostedTurn); read per event, since
      // the begin sets it right before this handler gets its first event.
      const turnStartOf = (): number => {
        const h = capturedTabId ? tabMapRef.current.get(capturedTabId)?._turnHandlers?.hostedTurn : undefined;
        return h && h.hostId === currentTarget() ? h.baseLen : 0;
      };
      // Capture stream generation at creation time — events from a stale
      // generation (e.g. old response arriving after user sent a new message)
      // must be discarded to prevent cross-turn bleed.
      const capturedStreamGen = streamGenRef.current;
      const handlerToken = ++streamHandlerSeq;
      // A `thinking_start` / `text_start` opens a NEW block: its first delta must not
      // be appended to the previous provisional block of that type (two thoughts in
      // a row would merge, and the first one's confirmation would then replace both).
      let freshThinking = false;
      let freshText = false;

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
        if (stampTab) {
          stampTab.latestStreamGen = capturedStreamGen;
          stampTab.latestStreamHandler = handlerToken;
        }
      }

      // ── Activate store phase gate ──
      // This makes reconcile()/replace() structurally impossible during streaming.
      // The single structural protection layer — no more relying on scattered
      // `if (isStreaming) return` guards at each call site.
      if (capturedTabId) {
        const streamStore = messageStoreRegistry.get(capturedTabId);
        if (streamStore) {
          streamStore.startStreaming(hostMessageId);
        }
      }

      const inner = (event: StreamEvent) => {
        // the bubble this event writes into (after a steer: its segment)
        const assistantMessageId = currentTarget();
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

        // The send's acceptance receipt (backend core/send_requests): once it
        // arrived, the message is the server's — kept whatever follows (SESSION_BUSY,
        // QUEUE_TIMEOUT, a dropped stream), so this tab never removes or re-sends it.
        if (event.type === 'accepted') {
          // The message is the session's now: a tab on its first send learns its
          // session here, before the cold start, and follows that session's
          // activity from admission on (docs/design/chat-turn-liveness.md S3).
          if (capturedTabId && event.sessionId) {
            const tab = tabMapRef.current.get(capturedTabId);
            if (tab && !tab.sessionId) {
              tab.sessionId = event.sessionId;
              watchOpenTabs();
            }
          }
          // The send is the server's: no retry needed, and a bubble marked
          // "Not sent" (an earlier attempt of it) is sent after all.
          const acceptedCid = (event as { clientId?: string | null }).clientId;
          if (capturedTabId && acceptedCid) {
            const tab = tabMapRef.current.get(capturedTabId);
            if (tab) tab._unsentRequests?.delete(acceptedCid);
            const store = messageStoreRegistry.get(capturedTabId);
            const bubble = store?.messages.find((m) => m.role === 'user'
              && (m.id === acceptedCid || m.metadata?.client_id === acceptedCid));
            if (store && bubble?.sendState) store.updateById(bubble.id, (m) => ({ ...m, sendState: undefined }));
          }
          // a resend of a send the server already queued: wait for its delivery
          if (event.duplicate && event.queued && capturedTabId) {
            const tab = tabMapRef.current.get(capturedTabId);
            if (tab) tab.isWaitingForBusy = true;
            if (capturedTabId === activeTabIdRef.current) setIsWaitingForBusy(true);
          }
          return;
        }

        // thinking_progress writes no content: record it so the streaming wait
        // hint (useStreamingWait) can say "Thinking · 1m 05s", not "waiting".
        if (event.type === 'thinking_progress' && capturedTabId) {
          const sid = tabMapRef.current.get(capturedTabId)?.sessionId;
          if (sid) noteThinking(sid, (event as { elapsed_seconds?: unknown }).elapsed_seconds);
        }

        // recovery_exhausted is a pure side-channel signal (toast only — no
        // store/streamState writes) and is yielded from the self-heal block
        // AFTER the turn's `result` event, which has already bumped streamGen.
        // It MUST be handled BEFORE the generation guard below, or the guard
        // discards it as a stale-generation event and the toast never shows
        // (the feature would be dead for the foreground tab — adversarial #1,
        // run_d8dce02a). Handle-before-guard mirrors how file_changed-class
        // side-channel events must bypass the cross-turn-bleed guard.
        if (event.type === 'recovery_exhausted') {
          // In-tab notice on the tab this handler belongs to. Dropped when the
          // tab is closed, or when a newer handler (a new send / reconnect)
          // owns the tab — a stale stream's signal must not land on the new turn.
          const rexTab = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
          if (!rexTab || (rexTab.latestStreamHandler !== undefined && rexTab.latestStreamHandler !== handlerToken)) {
            return;
          }
          const msg = event.message
            ?? 'Automatic recovery for this session has stopped. Start a fresh session to continue.';
          tabNoticeStore.post(rexTab, rexTab.sessionId, {
            id: RECOVERY_EXHAUSTED_NOTICE_ID,
            severity: 'warning',
            message: msg,
            // The bar re-checks tab + session at click time, then hands us the
            // notice's own tabId (never the active tab).
            action: onStartFresh
              ? { label: 'Start fresh session', run: (tabId) => onStartFresh(tabId) }
              : undefined,
          });
          return; // side-channel handled; never falls through to stream logic
        }

        // Long-running-turn notice. The backend emits this every
        // LONG_TURN_HEARTBEAT_S during a silent step (it carries elapsed time,
        // the oldest open tool, and a "press Stop to recover" hint). Surface it
        // in-tab so a multi-minute turn reads as "still working", not a dead
        // spinner. Handled HERE — before the generation guard and before the
        // stall bookkeeping — so it (a) is never discarded as stale and
        // (b) does NOT reset the stall timer (a heartbeat is not SDK progress,
        // same exclusion as 'heartbeat'). Active tab only: a background tab's
        // notice would surface against the chat the user is actually viewing.
        // Keyed + auto-dismiss so successive notices replace in place (no
        // stacking) and clear on their own once the turn produces or ends.
        if (event.type === 'still_working') {
          // The 90s force-end watchdog is already reset at the TOP of this
          // handler for every event, so no touch() is needed here. This block
          // surfaces the long-turn "still working" notice in THIS tab (any
          // tab, active or not — the notice lives with the tab, so it can never
          // show over another chat). Same id → replaces in place; auto-dismiss;
          // retracted at turn end by the complete handler.
          const swTab = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
          if (swTab && event.message
            && (swTab.latestStreamHandler === undefined || swTab.latestStreamHandler === handlerToken)) {
            const token = tabNoticeStore.post(swTab, swTab.sessionId, {
              id: STILL_WORKING_NOTICE_ID,
              severity: 'info',
              message: event.message,
              autoDismissMs: STILL_WORKING_NOTICE_MS,
            });
            if (token !== null) swTab._stillWorkingNoticeToken = token;
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
            // the subscription took this POST's turn over (same turn): expected
            const _handedOver = source === 'post' && currentTabState._handedOverGen === capturedStreamGen;
            if (_handedOver) {
              console.debug('[OT01-GenGuard] discard POST event after the subscription took the turn over',
                { eventType: event.type, capturedTabId });
            } else if (!_diagNoiseTypes.has(event.type)) {
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

        // Superseded-handler guard: a reconnect/resend creates a NEW handler for
        // the same message with the SAME generation, so the guard above passes
        // both. If the old stream is still delivering, every delta lands twice
        // (the duplicated thought/paragraph). Only the newest handler of the tab
        // writes message content; control events still go through.
        if (capturedTabId && (
          event.type === 'text_delta' || event.type === 'thinking_delta'
          || event.type === 'text_start' || event.type === 'thinking_start'
          || event.type === 'assistant' || event.type === 'tool_result'
        )) {
          const latest = tabMapRef.current.get(capturedTabId)?.latestStreamHandler;
          if (latest !== undefined && latest !== handlerToken) {
            if (event.type !== 'text_delta' && event.type !== 'thinking_delta') {
              console.warn('[stream-dup] dropped content from a superseded stream handler', {
                eventType: event.type, capturedTabId, handlerToken, latest,
              });
            }
            return;
          }
        }

        // Guard: if tab was closed while stream was running, no-op
        const tabState = capturedTabId
          ? tabMapRef.current.get(capturedTabId)
          : undefined;

        // When capturedTabId is null (initial tab before registration), treat as active.
        // The null case only occurs for the first tab before initTabState fires.
        const isActiveTab = capturedTabId === null || capturedTabId === activeTabIdRef.current;

        // ── Pre-first-token phase (backend core/turn_phase.py) ──
        // turn_phase says which step the turn is in before the first token
        // (preparing → starting on a cold spawn → waiting for the model). It is
        // display-only: handled and returned HERE, before the hasReceivedData
        // block below, so it never counts as stream data (that flag gates the
        // connection-phase silent retry). Any later event other than liveness
        // pings clears it, so a phase cannot outlive the wait it describes.
        // The CLI read a steer here (design 2026-10-25-chat-steer-frontend.md §5):
        // label it, and what follows goes into the segment it opens.
        if (event.type === 'user_steered') {
          if (capturedTabId) applySteerRead(capturedTabId, event as unknown as SteerReadEvent, hostMessageId);
          return;
        }
        if (event.type === 'turn_phase') {
          if (tabState && capturedTabId && (event.phase === 'preparing' || event.phase === 'starting' || event.phase === 'waiting')) {
            tabState.turnPhase = { phase: event.phase, since: Date.now() };
            updateTabStatus(capturedTabId, 'streaming'); // re-render to show it
          }
          return;
        }
        if (tabState?.turnPhase && event.type !== 'heartbeat' && event.type !== 'session_start') {
          tabState.turnPhase = undefined;
          if (capturedTabId) updateTabStatus(capturedTabId, 'streaming'); // drop the line now
        }

        // Mark that we've received data — used by reconnection logic AND the
        // post-stop silent-retry to distinguish connection-phase vs mid-stream
        // failures. Control/lifecycle events (session_start, session_resuming,
        // reconnecting), error events, AND heartbeats do NOT count as "data":
        // otherwise a connection-phase error that arrives right AFTER
        // session_start (a connection-phase failure on the send right after a
        // Stop) would flip
        // hasReceivedData=true and defeat the silent-retry, surfacing a spurious
        // "Connection interrupted — send again" and forcing a manual resend
        // after every Stop. Heartbeat is excluded for the same reason: it now
        // reaches this handler (to reset the watchdog) but it is liveness, not
        // real stream content. `reconnecting` is excluded for a 2nd reason too:
        // it is the leak-recovery soft signal — if it counted as data it would
        // set hasReceivedData BEFORE the respawned stream's first real token,
        // defeating the isReconnecting clear below (the spinner would stick).
        const isDataEvent = event.type !== 'session_start'
          && event.type !== 'session_resuming'
          && event.type !== 'reconnecting'
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
          // Clear reconnection state. No notice: the tab's "Reconnecting…"
          // indicator disappearing IS the success signal (a top-right toast
          // here used to float over whichever tab was active).
          if (tabState.isReconnecting) {
            console.log(`[Reconnect] Tab ${capturedTabId}: reconnection succeeded`);
            tabState.isReconnecting = false;
            tabState.reconnectionAttempt = 0;
            // Clear disconnect timeout — recovery happened before it fired
            if (tabState._disconnectTimeoutId) {
              clearTimeout(tabState._disconnectTimeoutId);
              tabState._disconnectTimeoutId = undefined;
            }
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

        // Liveness: the per-tab force-end watchdog is already reset at the TOP
        // of this handler for EVERY event, so no type-gated touch() is needed.
        // (No frontend stall guess: the backend reports a real stall itself as
        // a still_working notice, and ends a silent wait on its own.)

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
                // Owning SESSION id (run_c014a4f3, distinct from tabId): the backend
                // emit gate attaches it ONLY for an EXTERNAL (outside-SwarmWS) surfaced
                // file so the render fetch can prove "this session surfaced this path"
                // to GET /workspace/file (which then renders it read-only). MUST be
                // carried through this whitelist — a prior run threaded sessionId
                // through every OTHER hop (event type → ReferencedFile → openDetail →
                // useCanvasHost → FileViewer GET) but this SSE→CustomEvent bridge
                // silently DROPPED it (whitelist omission, GUI53/PIT44 class), so the
                // render fetch never sent session_id → external files 400'd. Absent for
                // SwarmWS-internal files (they render via the home-only guard).
                sessionId: (e.sessionId as string) ?? undefined,
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
          // allowAbs: backend-authored flag (present+true ONLY when the backend's
          // build_ui_command_event admitted an absolute path for a local-desktop owner
          // session). It relaxes ONLY the frontend's leading-`/` reject; ~ and .. stay
          // rejected unconditionally. Same trust class as path — a data flag scoped to
          // path filtering, never used to derive event/target (run_cbaecb86).
          dispatchUiCommand(
            _uev.cmd,
            _uev.path,
            capturedTabId ?? undefined,
            _uev.allowAbs === true,
            _uev.brain,
          );
        }

        if (event.type === 'session_start' && event.sessionId) {
          // ── State machine dispatch: PENDING → STREAMING (global + per-tab) ──
          dispatch({ type: 'SESSION_START', sessionId: event.sessionId });
          if (tabState) {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'SESSION_START', sessionId: event.sessionId });
          }

          // The tab's session (usually already bound at `accepted`); the bee follows
          // that session's activity (docs/design/chat-turn-liveness.md S3).
          if (tabState) {
            tabState.sessionId = event.sessionId;
            watchOpenTabs();
          }
          // Notify the owner that THIS tab bound a session — fires for background
          // tabs too, so a dispatched ToDo record can backfill even when the tab
          // is not active (the active-only global-sessionId effect misses those).
          // Opaque callback: no ToDo logic enters the streaming hot path. Wrapped
          // so a throw in the owner's callback can never break session_start
          // handling (P0: never degrade the streaming path).
          if (capturedTabId) {
            try {
              onTabSessionBound?.(capturedTabId, event.sessionId);
            } catch (err) {
              console.error('[useChatStreamingLifecycle] onTabSessionBound threw (ignored):', err);
            }
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
            // recovered by some path other than the notice's own action.
            if (capturedTabId) tabNoticeStore.dismiss(capturedTabId, RECOVERY_EXHAUSTED_NOTICE_ID);
          }
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          // SDK compaction: session ID changed but conversation continues.
          // CRITICAL: Do NOT clear messages — only append placeholder if needed.
          if (tabState) {
            // Same conversation under a new id: carry this tab's notices over
            // (otherwise the session filter would drop them as another session's).
            if (capturedTabId) tabNoticeStore.renameSession(capturedTabId, tabState.sessionId, event.newSessionId);
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

          const textFresh = freshText;
          freshText = false;
          // Write to store — subscription handles React sync + tabState cache
          const textStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          const textApply = (msg: Message) => {
            const r = applyContentEvent(msg, event, { fresh: textFresh });
            if (r.restarted) logStreamRestart(event.block!, 'text', event);
            return r.msg;
          };
          if (textStore) {
            textStore.updateLast(textApply, (msg) => msg.id === assistantMessageId);
          } else {
            // Fallback for initial tab before store exists (capturedTabId===null)
            if (tabState) tabState.messages = applyToMessage(tabState.messages, assistantMessageId, textApply);
            if (isActiveTab) setMessages((prev) => applyToMessage(prev, assistantMessageId, textApply));
          }
        } else if (event.type === 'thinking_delta' && event.thinking) {
          // --- Streaming thinking delta: append thinking token incrementally ---
          // SINGLE-WRITER: same as text_delta — store is the sole writer.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          const thinkFresh = freshThinking;
          freshThinking = false;
          const thinkStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          const thinkApply = (msg: Message) => {
            const r = applyContentEvent(msg, event, { fresh: thinkFresh });
            if (r.restarted) logStreamRestart(event.block!, 'thinking', event);
            return r.msg;
          };
          if (thinkStore) {
            thinkStore.updateLast(thinkApply, (msg) => msg.id === assistantMessageId);
          } else {
            if (tabState) tabState.messages = applyToMessage(tabState.messages, assistantMessageId, thinkApply);
            if (isActiveTab) setMessages((prev) => applyToMessage(prev, assistantMessageId, thinkApply));
          }
        } else if (event.type === 'tool_result' && event.content) {
          // Live tool results (persisted only in compact form — status + outcome).
          // Same single-writer merge as an assistant event: updateMessages dedups
          // tool_result by toolUseId and keeps streaming text, so a step flips to
          // done and gets its outcome. A sub-agent's result carries
          // parentToolUseId and lands on its nested step.
          const resStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (resStore) {
            resStore.updateLast((msg) => applyContentEvent(msg, event, { turnStart: turnStartOf() }).msg,
              (msg) => msg.id === assistantMessageId);
          }
        } else if (event.type === 'subagent_progress') {
          // A sub-agent's lifecycle / progress (SDK Task* messages): the main
          // agent is silent while it runs, so this is what keeps the chat alive
          // and tells the user what runs and how far it got. Ephemeral side field.
          const saStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          if (saStore) {
            saStore.updateLast((msg) => applyContentEvent(msg, event).msg, (msg) => msg.id === assistantMessageId);
          }
        } else if (event.type === 'text_start') {
          freshText = true;
          textStartVia = startDeliveredBy.get(event) ?? null;
        } else if (event.type === 'thinking_start') {
          freshThinking = true;
          thinkingStartVia = startDeliveredBy.get(event) ?? null;
          // Thinking block started — update tab status to streaming.
          // The actual content arrives via thinking_delta events.
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }
        } else if (event.type === 'assistant' && event.content) {
          // Full assistant message — the SDK's complete, authoritative content.
          // SINGLE-WRITER: store handles dedup + subscription syncs to React.
          // Fix 8: Update tab status to 'streaming' on first assistant event
          if (capturedTabId && tabState && tabState.status !== 'streaming') {
            updateTabStatus(capturedTabId, 'streaming');
          }

          const assistStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
          // The confirmed blocks (updateMessages' dedup / confirm logic, via the reducer)
          const assistApply = (msg: Message) => applyContentEvent(msg, event, { turnStart: turnStartOf() }).msg;
          if (assistStore) {
            assistStore.updateLast(assistApply, (msg) => msg.id === assistantMessageId);
          } else {
            // Fallback: initial tab before store exists
            if (tabState) tabState.messages = applyToMessage(tabState.messages, assistantMessageId, assistApply);
            if (isActiveTab) setMessages((prev) => applyToMessage(prev, assistantMessageId, assistApply));
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
            setLocalSending(false, undefined);
            incrementStreamGen();
          }
        } else if (event.type === 'cmd_permission_request') {
          const raw = event as unknown as Record<string, unknown>;
          const sid = event.sessionId || (raw.session_id as string);
          // One path with the mirror's re-surface (surfacePendingPermission).
          surfacePendingPermission(capturedTabId ?? null, assistantMessageId, {
            requestId: (event.requestId || raw.request_id) as string,
            toolName: (event.toolName || raw.tool_name) as string,
            toolInput: (event.toolInput || raw.tool_input) as Record<string, unknown>,
            reason: event.reason || '',
            options: event.options || ['approve', 'deny'],
          }, { isActive: !!isActiveTab, sessionId: sid });
        } else if (event.type === 'result') {
          // ── State machine dispatch: STREAMING → IDLE (global + per-tab) ──
          dispatch({ type: 'RESULT' });
          if (tabState) {
            tabState.streamState = streamingReducer(tabState.streamState, { type: 'RESULT' });
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

          // ── DIAGNOSTIC: spinner-hang root cause ──
          // Gated behind localStorage flag to avoid noise in production.
          // Enable: localStorage.setItem('swarm_diag_result', '1')
          if (typeof localStorage !== 'undefined' && localStorage.getItem('swarm_diag_result')) {
            console.warn('[DIAG:result]', {
              capturedTabId,
              activeTabId: activeTabIdRef.current,
              isActiveTab,
              tabStateIsStreaming: tabState?.isStreaming,
              tabStateIsReconnecting: tabState?.isReconnecting,
              pendingStreamTabs: [...pendingStreamTabs],
              sid,
            });
          }


          // Normal completion — the tab's request is over.
          setLocalSending(false, capturedTabId ?? undefined);
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

          // Fix 8: Update tab status — background tabs get 'complete_unread', foreground gets 'idle'
          if (capturedTabId) {
            updateTabStatus(
              capturedTabId,
              isActiveTab ? 'idle' : 'complete_unread',
            );
          }

        } else if (event.type === 'error') {
          // Suppress error events from a user-stopped stream — the abort
          // can race with backend error delivery, producing a spurious
          // "An unknown error occurred" that forces a redundant resend.
          if (tabState?.userStopped) {
            // End store streaming phase + clean up React streaming state.
            const stoppedStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            if (stoppedStore) stoppedStore.endStreaming();
            setLocalSending(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            return;
          }

          // POST-STOP SILENT RETRY: If we haven't received any data yet
          // (connection-phase) and we have a retry function, auto-retry once
          // instead of showing a scary error. This catches a send that fails
          // before it produces any output (e.g. the first send after a Stop).
          // The user never sees the error — just a brief delay.
          //
          // Never for SESSION_BUSY / QUEUE_TIMEOUT: the backend already stored
          // the message as pending and delivers it itself. Retrying re-POSTed
          // the same clientId 300ms later and stored it a second time — the
          // model got every message twice (19 duplicate user rows since
          // 10-15; e4f3397f 2026-10-22 18:52:20, "你咋没返回啊" x2).
          if (
            tabState &&
            !tabState.hasReceivedData &&
            tabState.retryStreamFn &&
            !isServerQueuedErrorCode(event.code) &&
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
            }, 300); // a short pause before the single retry
            return;
          }

          // SESSION_BUSY: the session is running another turn. The server KEPT
          // the message (its row is pending, `pendingId`; the send's `accepted`
          // receipt came first) and delivers it when the session is idle: the
          // drained turn reaches this tab on the session subscription. So the
          // user bubble stays where it is — it is the server's row — and only
          // this send's assistant placeholder goes (its turn is the drained one).
          // Only when the server could not keep it (no row: `retryPayload`, the
          // persist-failed fallback) is the text re-queued locally and the bubble
          // removed, as before.
          if (event.code === 'SESSION_BUSY') {
            console.log('[StreamHandler] SESSION_BUSY — message pending on the server', { capturedTabId });
            const busyEndStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
            if (busyEndStore) busyEndStore.endStreaming();
            setLocalSending(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) {
              updateTabStatus(capturedTabId, 'streaming');
              const tabState = tabMapRef.current.get(capturedTabId);
              if (tabState) {
                // A message the server could not keep (no row: the persist-failed
                // fallback) is "Not sent · Retry" when this request ends (P2b).
                // Atomic clear (flag + Set + re-render) instead of direct
                // tabState.isStreaming = false. Direct mutation on a background
                // tab triggers no re-render → spinner frozen true. See the
                // disconnect-timeout fix for the same root cause.
                setLocalSending(false, capturedTabId);
                tabState.isWaitingForBusy = true;
                // Remove this send's assistant placeholder; the user bubble stays
                // (queued on the server, or marked "Not sent").
                const removeStore = capturedTabId ? messageStoreRegistry.get(capturedTabId) : null;
                const storeOrTab = removeStore?.messages ?? tabState.messages;
                const assistantIdx = storeOrTab.findIndex(
                  (m) => m.id === assistantMessageId
                );
                if (assistantIdx >= 0) {
                  const removeIds = new Set([assistantMessageId]);
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

            const pollSessionId = capturedTabId
              ? tabMapRef.current.get(capturedTabId)?.sessionId
              : undefined;

            if (pollSessionId && capturedTabId) {
              // No poll: the server owns the pending message. The session
              // subscription renders the running turn and then the drained one
              // live (the drained turn retires the queued mirror and clears the
              // waiting state at its end); while the subscription is down, the
              // always-on reconcile's drain surface shows it and clears the waiting
              // state once nothing is pending.
            } else {
              // No session ID — nothing is pending on the server: clear now
              if (capturedTabId) {
                const tabState = tabMapRef.current.get(capturedTabId);
                if (tabState) tabState.isWaitingForBusy = false;
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

          // Carry the backend error CODE (+ detail + suggestedAction) onto the
          // message so TabView → ChatErrorMessage can render the real coded label
          // instead of the generic amber "Interrupted" fallback. A code-less event
          // (genuine connection drop) yields errorCode=undefined → amber preserved
          // (FR-2). Without this, a coded terminal like TOOL_CALL_LEAK_UNRECOVERED
          // rendered identically to a connection drop (the whole bug).
          const errorFields = errorFieldsFromEvent(event);

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
                  ? { ...msg, isError: true, ...errorFields, content: [...msg.content, ...errorContent] }
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
                  ? { ...msg, isError: true, ...errorFields, content: [...msg.content, ...errorContent] }
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
                ...errorFields,
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
          setLocalSending(false, capturedTabId ?? undefined);
          // (The error bubble brings its tab's view to the bottom: TabView,
          // scrollFocus.tailErrorId.)
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
          setLocalSending(true, capturedTabId ?? undefined);
          // Set isReconnecting so TabView renders the soft "Reconnecting…"
          // spinner during the gap. Without this the handler sets only
          // isStreaming → the generic "Thinking…" shows, not "Reconnecting…"
          // (the spinner reads isReconnecting). Mirrors the disconnect path's
          // flag-set. Backend emits this on the tool_call_leak 1st-recovery
          // path to fill the ~8-9s kill→respawn information vacuum; the flag is
          // cleared automatically when the respawned stream's first data event
          // arrives (the data-arrival clear in the message-processing path).
          const reconnTab = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
          if (reconnTab) {
            reconnTab.isReconnecting = true;
          }
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
                  setLocalSending(false, capturedTabId ?? undefined);
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
              setLocalSending(true, capturedTabId ?? undefined);
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
        // (either auto or manual trigger). It does NOT touch the context ring: the
        // backend follows it with a context_warning snapshot of the post-compaction
        // size (backend/core/context_usage.py), which replaces the old value. It:
        //   Appends a PERSISTENT in-stream system message to the originating tab's
        //      MessageStore (CLI-native autocompact — approach A). This is NOT a toast:
        //      compaction is a state change ON THE CURRENT SESSION, so it must leave a
        //      reviewable trace in the conversation (XG directive 2026-10-14: "不能用
        //      toast ... 针对当前session 做的"). A 5s auto-dismiss toast disappeared before
        //      the user could see it ("做没做、做完没有 都没有信息").
        //      The backend is the SOLE judge (it owns the PreCompact flag); this is a pure
        //      projection of event.message — no second source of truth, no front-end inference.
        //      Mirrors the resume-boundary store-append path above (session-scoped: no DB
        //      row — the message survives in-session reconcile via MessageStore._applyMerge
        //      Pass 2, which preserves local-only synthetic system messages).
        //
        // context_compacting (2026-10-17) — emitted LIVE the moment the CLI starts
        // compacting (phase 'started') or if it fails ('failed'), so a 1-2 minute
        // pause reads as "compacting", not a hang. Same store-append path and the
        // same 'compact-' id prefix (TabView renders it as the compact divider).
        else if (event.type === 'context_compacted' || event.type === 'context_compacting') {
          // The ring is NOT cleared here: the backend follows this event with a
          // context_warning snapshot of the post-compaction size (or source=none
          // when the CLI reported none), applied by the context_warning branch.
          // Write to the ORIGINATING tab's store — same for active AND background
          // tab (the single-writer store IS the per-tab authority). ONE divider per
          // compaction: "compacting" is replaced in place by the stored record
          // (pages/chat/compaction.ts applyCompactionEvent).
          if (capturedTabId) {
            const compactStore = messageStoreRegistry.get(capturedTabId);
            if (compactStore) applyCompactionEvent(compactStore, event);
          }
        }
        // Context usage — the backend's snapshot, pushed after every main-agent
        // request, on compaction and at turn end. Same apply path as the GET
        // (refreshContextUsage): newest measuredAt wins, source=none clears.
        else if (event.type === 'context_warning' && event.level) {
          applyContextUsage(capturedTabId, snapshotFromEvent(event));
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
              // setLocalSending(false) primitive (flag + Set + re-render) — a
              // direct flag mutation on a background tab triggers no re-render
              // so the spinner would stay frozen true (same root cause as the
              // disconnect-timeout and SESSION_BUSY fixes).
              if (subtype === 'hard_warn' || subtype === 'kill') {
                setLocalSending(false, capturedTabId ?? undefined);
              }
            }
            if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
              setCompactionGuard(guardEvent);
              if (subtype === 'hard_warn' || subtype === 'kill') {
                setLocalSending(false, capturedTabId ?? undefined);
              }
            }
          }
        }
        // System prompt metadata — backend emits after each turn alongside
        // context_warning.  Same display mirror pattern: write to tabMapRef,
        // mirror to React state only for the active tab.
        else if (event.type === 'system_prompt_metadata') {
          const { type: _type, ...raw } = event;
          // The event spreads the backend metadata dict, so it is snake_case.
          // Casting it to the camelCase type (as this did) left total_tokens and
          // effective_token_budget reading as undefined — the panel showed 0
          // tokens and no budget whenever the prop came from SSE rather than the
          // HTTP fetch. Use the same mapper the endpoint uses.
          const metadata = promptMetadataToCamelCase(
            raw as Record<string, unknown>,
          );
          const spmTab = capturedTabId
            ? tabMapRef.current.get(capturedTabId)
            : undefined;
          if (spmTab) {
            spmTab.promptMetadata = metadata;
          }
          if (capturedTabId === null || capturedTabId === activeTabIdRef.current) {
            setPromptMetadata(metadata);
          }
        }
        // MCP health warning — backend emits once per session if configured
        // MCPs failed to connect. Shown in THIS tab: it describes the tools
        // this session can use, not the app.
        else if (event.type === 'mcp_health_warning') {
          const msg = event.message ?? 'Some MCP servers failed to load.';
          if (tabState) {
            tabNoticeStore.post(tabState, tabState.sessionId, {
              id: MCP_HEALTH_NOTICE_ID,
              severity: 'warning',
              message: msg,
            });
          }
        }
        // Telemetry events (agent_activity, tool_invocation, etc.) are no
        // longer processed — TSCC fetches metadata from the endpoint instead.
      };
      // A POST send's handler goes through the turn router; a subscription
      // turn's handler is called by the router directly.
      return source === 'post' ? wrapPostHandler(capturedTabId, inner, hostMessageId) : inner;
    },
    [queryClient, setLocalSending, incrementStreamGen, updateTabStatus, applyContextUsage], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
  );

  const createErrorHandlerInner = useCallback(
    (hostMessageId: string, tabId?: string) => {
      const capturedTabId = tabId ?? activeTabIdRef.current;
      // Capture stream generation — stale errors from a previous stream
      // must not kill the spinner or invalidate handlers for the current stream.
      const capturedStreamGen = streamGenRef.current;

      return (error: Error) => {
        // the bubble the turn writes into now (after a steer: its segment, §6)
        const assistantMessageId = segmentTargetOf(capturedTabId, hostMessageId);
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
          setLocalSending(false, capturedTabId ?? undefined);
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
            setLocalSending(true, capturedTabId ?? undefined);
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
          // The session this grace window is for: the expiry notice is dropped
          // if the tab has moved to another session by then.
          const graceSessionId = tabState.sessionId;
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
            // a disconnect does not interrupt it, and it persists to the DB). Showing
            // a hard "Connection lost" error here while the turn is still alive was
            // the recurring false-error bug. Consult the authoritative backend
            // mirror ONCE; if alive (streaming / waiting_input) keep the
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
                queryFailed: false,
              });
            } catch {
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: false, backendWaitingInput: false, queryFailed: true,
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
              // Leave _healGraceActive set; the bee follows the backend's activity.
              return;
            }

            // verdict === 'show-error': backend genuinely done/dead (or query
            // failed). Run the original error path.
            tab2._healGraceActive = false;
            console.warn(`[HealGrace] Tab ${capturedTabId}: grace expired, backend genuinely done — showing error`);
            setLocalSending(false, capturedTabId ?? undefined);
            incrementStreamGen();
            if (capturedTabId) updateTabStatus(capturedTabId, 'idle');
            // Mark post-disconnect-uncertain so a follow-up send is QUEUED
            // (shouldQueueSend) instead of escaping → SESSION_BUSY → orphan delete.
            tab2._postDisconnectUncertain = true;
            tab2._postDisconnectAt = Date.now();
            // NOTE (2026-06-21, zombie-poison fix): do NOT POST /stop here.
            // /stop → interrupt_session → kill-on-timeout POISONS the subprocess
            // → next send zombie_via_error → manual-Continue loop. A client
            // disconnect no longer touches the backend turn at all (it runs to
            // its own end, core/session_events); the reconcile loop drains any
            // queued message.
            // In-tab error on the tab whose stream was lost (this timer can fire
            // while another tab is active — it must never land there).
            tabNoticeStore.post(tab2, graceSessionId, {
              id: CONNECTION_LOST_NOTICE_ID,
              severity: 'warning',
              // Not "send it again": once the backend is idle the reconcile loop
              // reloads this session's saved messages, so an answer that finished
              // meanwhile appears by itself; resending would duplicate it.
              message: 'The connection to this reply dropped. If it finished, it appears here within about 15s; if not, send your message again.',
            });
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
        // A client disconnect does not stop the backend turn: the turn belongs
        // to its session and runs to its own end (core/session_events), its
        // output persisted to DB and published to the session subscription.
        // The frontend stop was both redundant and
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
        setLocalSending(false, capturedTabId ?? undefined);
        incrementStreamGen();

        // Clean up sessionStorage pending state on stream error
        if (tabState?.sessionId) {
          removePendingState(tabState.sessionId);
        }

        // Fix 8: Update tab status to 'error'
        if (capturedTabId) {
          updateTabStatus(capturedTabId, 'error');
        }

      };
    },
    [setLocalSending, incrementStreamGen, updateTabStatus], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
  );

  // A transport error of a POST whose turn the session subscription carries is
  // a detach, not an error: the turn is still running and being rendered.
  const createErrorHandler = useCallback(
    (assistantMessageId: string, tabId?: string) => {
      const innerError = createErrorHandlerInner(assistantMessageId, tabId);
      const capturedTabId = tabId ?? activeTabIdRef.current;
      return (error: Error) => {
        const tab = capturedTabId ? tabMapRef.current.get(capturedTabId) : undefined;
        if (capturedTabId && tab) {
          const lost = onPostLost(turnStateOf(tab), sessionEvents.isConnected());
          turnRunRef.current(capturedTabId, lost.actions);
          if (lost.verdict === 'detach') {
            console.info('[TurnRouter] POST stream error — the session subscription carries the turn', { capturedTabId, error: error?.message });
            return;
          }
        }
        innerError(error);
      };
    },
    [createErrorHandlerInner], // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable
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
  const createCompleteHandler = useCallback((tabId?: string, source: 'post' | 'sub' = 'post') => {
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

    const inner = () => {
      // ── Staleness gate (single source of truth) ──
      // Validate freshness ONCE, then end the tab's request via setLocalSending
      // (the bee follows the session's activity, not this handler:
      // docs/design/chat-turn-liveness.md S3).
      if (capturedTabId) {
        const tabState = tabMapRef.current.get(capturedTabId);
        // Live-completer check (run_6adee7d5): clear streaming iff THIS handler
        // is still the tab's live completer. We compare against latestCompleteGen
        // (advanced only by a NEW send), NOT streamGen (churned mid-stream by
        // reconnect/result/error). The old `streamGen !== capturedGen` check
        // self-invalidated a turn's own [DONE] after a single reconnect → the
        // setLocalSending(false) below was skipped (run_6adee7d5).
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
          // call setLocalSending(false) was discarded → spinner can pin. ALWAYS
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
        // The turn is over: retract ITS "still working" notice. Token-checked,
        // and only reached by the live completer (gen guard above), so a late
        // [DONE] of an older turn can never remove a newer turn's notice.
        if (capturedTabId && tabState._stillWorkingNoticeToken !== undefined) {
          tabNoticeStore.dismiss(capturedTabId, STILL_WORKING_NOTICE_ID, tabState._stillWorkingNoticeToken);
          tabState._stillWorkingNoticeToken = undefined;
        }
        if (tabState.sessionId) {
          removePendingState(tabState.sessionId);
        }
      } else if (streamGenRef.current !== capturedGen) {
        // Legacy null-tab path: fall back to the global generation check.
        return; // stale — no-op
      }

      // End store streaming phase — unblocks reconcile/replace, flushes
      // pending reconcile thunk. Must happen before setLocalSending(false)
      // so that the store is in idle state when any post-stream DB fetch fires.
      if (capturedTabId) {
        const completeStore = messageStoreRegistry.get(capturedTabId);
        if (completeStore) completeStore.endStreaming();
      }

      // Atomic clear — flag + pendingStreamTabs in one primitive. This is the
      // ONLY place streaming is cleared on the complete path, so the two
      // representations can never drift out of sync.
      setLocalSending(false, capturedTabId ?? undefined);

      // Reset tab status to idle — without this, tabs that ended with an
      // error event (SSE disconnect, SDK error) would keep showing the red
      // "!" indicator forever since no subsequent result event arrives to
      // clear it.  The completeHandler is the definitive "stream is over"
      // signal — if we reach here the tab is no longer active.
      if (capturedTabId) {
        updateTabStatus(capturedTabId, 'idle');
      }

    };
    if (source === 'sub' || !capturedTabId) return inner;
    const ownTab = tabMapRef.current.get(capturedTabId);
    if (!ownTab) return inner;
    turnHandlersOf(ownTab).postComplete = inner;
    // The POST's [DONE]: the router decides whether it completes the turn now
    // (POST rendered it, or a pre-turn reply) or the subscription's turn_end does.
    return () => {
      const live = tabMapRef.current.get(capturedTabId);
      if (!live) { inner(); return; }
      turnRunRef.current(capturedTabId, onPostComplete(turnStateOf(live), sessionEvents.isConnected()), undefined, inner);
    };
  }, [setLocalSending, updateTabStatus]); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

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
      // A POST lost while the session subscription is connected is not a
      // disconnect: the turn keeps running and the subscription renders it.
      if (capturedTabId) {
        const routedTab = tabMapRef.current.get(capturedTabId);
        if (routedTab) {
          const lost = onPostLost(turnStateOf(routedTab), sessionEvents.isConnected());
          turnRunRef.current(capturedTabId, lost.actions);
          if (lost.verdict === 'detach') {
            console.info('[TurnRouter] POST stream closed early — the session subscription carries the turn', { capturedTabId });
            return;
          }
        }
      }
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
          // The POST is gone; the turn may still run (its activity says so), and
          // its message may still go (a reconnect re-opens it): not its end
          tabState.isReconnecting = true;
          setLocalSending(false, capturedTabId, { keepSend: true });

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
            // still streaming, that pull grabbed INCOMPLETE content and
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
                queryFailed: false,
              });
            } catch {
              verdict = healGraceExpiryVerdict({
                backendIsStreaming: false, backendWaitingInput: false, queryFailed: true,
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
              // Backend alive (streaming / waiting_input): no error, and the bee
              // follows the backend's activity (docs/design/chat-turn-liveness.md
              // S3); its idle edge ends post-disconnect mode and pulls the DB.
              console.log('[DisconnectHandler] Timeout — backend still working; the session activity takes over', { capturedTabId });
              currentTabState._postDisconnectUncertain = true;
              currentTabState._postDisconnectAt = Date.now();
              currentTabState._disconnectTimeoutId = undefined;
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
            // Clear via the atomic setLocalSending(false) primitive for ALL tabs.
            setLocalSending(false, capturedTabId);

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
  }, [setLocalSending]); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

  /** Begin rendering a turn this tab did not send (drained / CLI-initiated /
   *  channel turn, or a running turn the tab joined after a reload). The bubble
   *  id is the persisted assistant rows' client_id when it is a `local-` id,
   *  else a `local-sub-` id carrying that client_id, so the turn-end DB reconcile
   *  correlates it. A bubble already shown for this turn is replaced: the
   *  subscription replays the turn from its turn_start. */
  const beginSubscriptionTurn = (tabId: string, ev: TurnEnvelope) => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab) return;
    const cid = ev.turnClientId;
    const source = (ev as { source?: TurnSource }).source;
    // A continuation shares its client_id with the turn that asked the question.
    const continuation = source === 'answer' || source === 'permission';
    const store = messageStoreRegistry.getOrCreate(tabId, { sessionId: tab.sessionId });
    if (store.phase === 'streaming') store.endStreaming();
    const handlers = turnHandlersOf(tab);
    const route = turnStateOf(tab);
    const postBubble = route.postTurnId === ev.turnId ? handlers.postBubbleId : undefined;
    // rendering starts at the turn's start: a replay re-delivers its user_steered
    // events, which rebuild the split (§8); another turn never inherits it
    handlers.segmentTarget = undefined;
    // ONE bubble per run of assistant turns, as the reload shows it (the history
    // endpoint merges consecutive assistant rows into one message; live == reload,
    // liveReload.property.test.ts). A turn that does not follow a user message — a
    // CLI-initiated one (a background agent finished), or the continuation after a
    // question / permission — renders INTO the bubble before it (ChatPage gives a
    // continuation's POST that same bubble). An empty bubble of this turn from an
    // earlier start (a replay) goes first.
    const hosts = source === 'injected' || continuation;
    // The turn's segments (§8), as the snapshot or its user_steered events last
    // gave them: only this turn's (the backend clears them at the next turn's
    // start, so a list from an earlier turn starts with another key).
    const known = tab._turnSegments ?? [];
    const segments = cid && known[0] === cid ? known : [];
    if (hosts) {
      store.remove((m) => m.role === 'assistant' && (m.content?.length ?? 0) === 0
        && (m.id === postBubble || m.id === `local-sub-${ev.turnId}`));
    }
    const host = hosts ? hostForInjectedTurn(store.getSnapshot(), ev.turnId, handlers.hostedTurn) : null;
    if (host) {
      const replay = handlers.hostedTurn?.turnId === ev.turnId;
      const baseLen = replay ? handlers.hostedTurn!.baseLen : host.content.length;
      // What the host shows so far belongs to turns that have ended: final. Marked
      // confirmed, and the turn's merge starts at baseLen (updateMessages turnStart),
      // so this turn never replaces or de-duplicates against it.
      store.updateById(host.id, (m) => ({
        ...m,
        content: m.content.slice(0, baseLen).map((b) =>
          (b.type === 'text' || b.type === 'thinking') ? { ...b, _confirmed: true } as typeof b : b),
      }));
      handlers.hostedTurn = { turnId: ev.turnId!, hostId: host.id, baseLen };
      // the host keeps what it had (baseLen); the segments after it are rebuilt.
      // A continuation's host can itself be a segment (the one the question
      // was in): only the keys after it go.
      const at = segments.indexOf(host.id);
      const after = new Set(at >= 0 ? segments.slice(at + 1) : segments.filter((k) => k !== host.id));
      if (after.size) store.remove((m) => m.role === 'assistant' && (after.has(m.id)
        || (typeof m.metadata?.client_id === 'string' && after.has(m.metadata.client_id))));
    } else {
      handlers.hostedTurn = undefined;
    }
    const id = host ? host.id
      : !continuation && cid && cid.startsWith('local-') ? cid : `local-sub-${ev.turnId}`;
    if (!host) {
      // The replay rebuilds this turn from its start: drop what was shown of it —
      // its placeholder, the POST bubble when it is this tab's own send, and (for a
      // turn that is not a continuation) its persisted rows.
      const segSet = new Set(continuation ? [] : segments);
      store.remove((m) => m.role === 'assistant' && (
        m.id === id || (!!postBubble && m.id === postBubble)
        || (!continuation && !!cid && m.metadata?.client_id === cid)
        || segSet.has(m.id)
        || (typeof m.metadata?.client_id === 'string' && segSet.has(m.metadata.client_id))));
      const placeholder = {
        id, role: 'assistant', content: [], timestamp: new Date().toISOString(),
        metadata: {
          ...(cid ? { client_id: cid } : {}),
          ...(source ? { turn_source: source } : {}),
        },
      } as Message;
      // It goes above this turn's first steer -- read, Waiting, or a queued
      // message (§8): with the steer still Waiting, appending put the replay
      // under it.
      const above = firstSteerOfTurn(store.getSnapshot(), cid);
      if (above) store.insertBefore(above, placeholder);
      else store.appendMany([placeholder]);
    }
    if (tab._disconnectTimeoutId) { clearTimeout(tab._disconnectTimeoutId); tab._disconnectTimeoutId = undefined; }
    tab.isReconnecting = false;
    // (the bee comes from the session's activity, not from this event)
    updateTabStatus(tabId, 'streaming');
    // this tab's own POST of this same turn is taken over: its later events are
    // expected discards, not a lost stream
    tab._handedOverGen = postBubble ? tab.latestStreamGen : undefined;
    incrementStreamGen();
    handlers.subRender = createStreamHandler(id, tabId, 'sub');
    handlers.subComplete = createCompleteHandler(tabId, 'sub');
    if (tabId === activeTabIdRef.current) setMessages(store.getSnapshot());
  };

  const resyncTabFromDb = (tabId: string) => {
    const tab = tabMapRef.current.get(tabId);
    const sid = tab?.sessionId;
    if (!tab || !sid) return;
    // the reload's snapshot rev becomes the tab's: the next resync asks only
    // for what changed after it
    const reload = () => {
      chatService.invalidateMessageCache(sid);
      return chatService.getReconcileTailWithRev(sid, RECONCILE_TAIL).then(({ messages, rev }) => {
        if (rev > (tab.transcriptRev ?? 0)) tab.transcriptRev = rev;
        return messages;
      });
    };
    // A tab that knows its transcript rev first asks what changed after it: an
    // unchanged record needs no reload (the replay that follows the resync
    // carries the running turn). Any change, or a removal, reloads the tail.
    const known = tab.transcriptRev ?? 0;
    const check = known > 0
      ? chatService.getTranscriptSince(sid, known).then((d) => {
          if (d.rev > known) tab.transcriptRev = d.rev;
          return d.reset || d.rows.length > 0 ? reload() : null;
        }).catch(() => reload())
      : reload();
    check.then((msgs) => {
      if (!msgs) return;
      const s = messageStoreRegistry.getOrCreate(tabId, { sessionId: sid });
      s.reconcile(msgs);
      if (s.phase === 'idle') {
        if (tabId === activeTabIdRef.current) setMessages(() => s.messages);
        else tab.messages = s.messages;
      }
    }).catch(() => { /* best-effort: the reconcile tick retries */ });
  };

  // `postRender` / `postComplete`: the calling POST's own handlers, so a stale
  // POST's callbacks act on that POST's turn, never on a newer send's.
  turnRunRef.current = (
    tabId: string, actions: TurnAction[], postRender?: (event: StreamEvent) => void,
    postComplete?: () => void,
  ) => {
    for (const a of actions) {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      const h = turnHandlersOf(tab);
      if (a.type === 'render') {
        (a.origin === 'post' ? (postRender ?? h.postRender) : h.subRender)?.(a.event);
      } else if (a.type === 'begin') {
        beginSubscriptionTurn(tabId, a.event);
        turnHandlersOf(tab).subRender?.(a.event);
      } else if (a.type === 'complete') {
        if (a.origin === 'sub') {
          if (tab.isWaitingForBusy) {
            tab.isWaitingForBusy = false;
            if (tabId === activeTabIdRef.current) setIsWaitingForBusy(false);
          }
          const done = h.subComplete;
          h.subRender = undefined;
          h.subComplete = undefined;
          done?.();
        } else {
          (postComplete ?? h.postComplete)?.();
        }
      } else if (a.type === 'postComplete') {
        (postComplete ?? h.postComplete)?.();
      } else if (a.type === 'resync') {
        resyncTabFromDb(tabId);
      } else if (a.type === 'reset') {
        // the turn is replayed from its turn_start next: its bubble is rebuilt by
        // the begin that follows; nothing completes now (the completion handler
        // stays, in case the turn ends before its replay arrives)
        if (a.origin === 'sub') h.subRender = undefined;
      }
    }
  };

  // One session subscription per window: route each event to the tab(s)
  // showing its session. The client is a module singleton, never stopped here
  // (a StrictMode re-mount must not drop the window's stream).
  useEffect(() => {
    watchOpenTabs(); // restored tabs are in the first connect's URL
    sessionEvents.start();
    const offEvents = sessionEvents.subscribe((ev) => {
      const sid = ev.sessionId;
      if (!sid) return;
      if (ev.type === 'background_agents') {
        // a background agent changed status: not a turn event (stores/backgroundAgentsStore)
        backgroundAgentsStore.apply(sid, (ev as { agents?: unknown }).agents);
        return;
      }
      if (ev.type === 'session_activity') {
        // whether a turn of the session is in progress (backend core/session_state)
        const a = ev as { activity?: unknown; activity_seq?: unknown };
        if (typeof a.activity === 'string' && typeof a.activity_seq === 'number') {
          for (const [tabId, tab] of tabMapRef.current.entries()) {
            if (tab.sessionId === sid) applyActivity(tabId, a.activity as SessionActivity, a.activity_seq);
          }
        }
        return;
      }
      if (ev.type === 'session_state') {
        const raw = (ev as { state?: unknown }).state;
        if (raw && typeof raw === 'object') {
          const entry = chatService.toStreamingStateEntry(raw as never);
          const seq = (ev as { eventSeq?: unknown }).eventSeq;
          for (const [tabId, tab] of tabMapRef.current.entries()) {
            if (tab.sessionId !== sid) continue;
            if (entry.activity) applyActivity(tabId, entry.activity, entry.activitySeq);
            // the server's queue: pushes in their own order (eventSeq, per session)
            rebindQueue(tabId);
            if (entry.pending && (typeof seq !== 'number' || seq > (tab._serverQueueSeq ?? -1))) {
              if (typeof seq === 'number') tab._serverQueueSeq = seq;
              applyServerQueue(tabId, entry.pending, Date.now(), entry.pendingComplete === true, entry);
            }
          }
          pushedStateRef.current?.(sid, entry);
        }
        return;
      }
      if (ev.type === 'transcript_row' || ev.type === 'transcript_delete') {
        // A persisted change outside any turn (backend core/transcript_feed): every
        // tab showing the session applies it, the one that caused it too.
        for (const [tabId, tab] of tabMapRef.current.entries()) {
          if (tab.sessionId !== sid) continue;
          const store = messageStoreRegistry.getOrCreate(tabId, { sessionId: sid });
          let applied = feedApplied.get(store);
          if (!applied) { applied = new Map(); feedApplied.set(store, applied); }
          // The change's rev does NOT become the tab's transcriptRev: a turn's own
          // rows are written before their events publish and carry no rev, so a
          // feed change can arrive ahead of a lower-rev turn row this tab has not
          // seen yet. Only a read of the record (resync) sets the rev the next
          // resync asks from, so `since_rev` never skips that row.
          for (const op of planTranscriptChange(store.messages, ev as unknown as TranscriptChange, applied)) {
            if (op.kind === 'append') store.append(op.msg);
            else if (op.kind === 'update') store.updateById(op.id, op.update);
            else if (op.kind === 'remove') store.remove((m) => m.id === op.id);
            else applyCompactionEvent(store, op.event);
          }
        }
        return;
      }
      // Every tab sees every event; the router's ownership rule decides what is
      // the tab's own (its session, its bound turn, or a candidate while its
      // first send has no session yet) — one rule, in sessionTurnRouter.
      tagStart(ev, 'sub');
      for (const [tabId, tab] of tabMapRef.current.entries()) {
        turnRunRef.current(tabId, routeSubEvent(turnStateOf(tab), ev as TurnEnvelope));
      }
    });
    const offConn = sessionEvents.onConnectionChange((connected) => {
      if (connected) return;
      for (const [tabId, tab] of tabMapRef.current.entries()) {
        turnRunRef.current(tabId, onSubDisconnected(turnStateOf(tab)));
      }
    });
    return () => { offEvents(); offConn(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- refs are stable

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
    // a tab whose session changed (reset, resumed history) starts its activity over
    for (const id of tabMapRef.current.keys()) rebindActivity(id);
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
    isSending,
    setLocalSending,
    applyActivity,
    rebindActivity,
    noteSend,
    applyServerQueue,
    syncQueuedBadges,
    rebindQueue,
    streamingActivity,
    displayedActivity,
    elapsedSeconds,
    pendingStreamTabs,
    clearPendingStreamTab,
    bumpStreamingDerivation,
    streamGenRef,
    incrementStreamGen,
    createStreamHandler,
    createCompleteHandler,
    createDisconnectHandler,
    createErrorHandler,
    removePendingStateForSession: removePendingState,
    contextWarning,
    setContextWarning,
    clearContextWarning,
    refreshContextUsage,
    promptMetadata,
    setPromptMetadata,
    compactionGuard,
    setCompactionGuard,
    isWaitingForBusy,
    cancelBusyWait,
  };
}
