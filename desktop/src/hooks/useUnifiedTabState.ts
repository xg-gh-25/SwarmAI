/**
 * Unified tab state hook — single source of truth for all tab state.
 *
 * Uses the dual-state pattern: ``useRef<Map>`` for synchronous reads/writes
 * in stream handlers (zero re-render overhead during SSE token deltas) +
 * ``useState`` render counter for React re-derivation of display views.
 *
 * This is the correct architecture for streaming-heavy state. Mutable refs
 * give O(1) synchronous access during high-frequency SSE events (~100/sec
 * during token streaming). An immutable store would add copy overhead on
 * every delta with no benefit — all consumers share tabMapRef via ChatPage.
 *
 * Replaces the three separate stores (`useTabState`, `tabStateRef`, `tabStatuses`)
 * with a single `useRef<Map<string, UnifiedTab>>` backed by a `useState` re-render
 * counter. Derived views (`openTabs`, `tabStatuses`, `activeTab`) are computed via
 * `useMemo` keyed on the counter.
 *
 * Key exports:
 * - `TabStatus`                 — Tab lifecycle status union type
 * - `UnifiedTab`                — Combined metadata + runtime state for a single tab
 * - `SerializableTab`           — Fields persisted to open_tabs.json
 * - `UseUnifiedTabStateReturn`  — Hook return interface
 * - `useUnifiedTabState`        — The hook itself
 */

import { useState, useRef, useMemo, useCallback, useEffect } from 'react';
import type { Message, UnifiedAttachment, SystemPromptMetadata, CompactionGuardEvent, TurnPhase, SessionActivity } from '../types/index';
import type { PendingQuestion, OpenTab } from '../pages/chat/types';
import type { ContextWarning } from './useChatStreamingLifecycle';
import { INITIAL_STATE, type StreamingState } from './streaming-machine';
import {
  tabPersistenceService,
  type OpenTabsFileData,
  type PersistedTab,
} from '../services/tabPersistence';
import api from '../services/api';
import { messageStoreRegistry } from '../stores/MessageStore';
import { tabNoticeStore } from '../stores/tabNoticeStore';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Hard ceiling for tab restore — all saved tabs up to this count are restored
 * from open_tabs.json regardless of current system resources.
 */
export const MAX_TABS_HARD_CEILING = 4;

/**
 * Fallback max tabs value used when the `GET /api/system/max-tabs` call fails.
 * Conservative default to prevent unbounded tab creation when the backend is
 * unreachable.
 */
export const MAX_OPEN_TABS_FALLBACK = 2;

/**
 * @deprecated Use `MAX_TABS_HARD_CEILING` for restore limits or the dynamic
 * value from `fetchMaxTabs()` for new tab creation. Kept as an alias for
 * backward compatibility with existing test imports.
 */
export const MAX_OPEN_TABS = MAX_TABS_HARD_CEILING;

// ---------------------------------------------------------------------------
// Dynamic tab limit types
// ---------------------------------------------------------------------------

/** Response shape from `GET /api/system/max-tabs`. */
export interface MaxTabsInfo {
  maxTabs: number;
  /** Max chat tabs allowed (maxTabs - 1, reserving 1 slot for channels). */
  chatMax: number;
  memoryPressure: 'ok' | 'warning' | 'critical';
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Tab lifecycle status for header indicators. */
export type TabStatus =
  | 'idle'
  | 'streaming'
  | 'waiting_input'
  | 'permission_needed'
  | 'error'
  | 'complete_unread';

/** Combined metadata + runtime state for a single tab. */
export interface UnifiedTab {
  // --- Metadata (persisted to open_tabs.json) ---
  id: string;
  title: string;
  agentId: string;
  isNew: boolean;
  sessionId?: string;

  // --- Runtime state (not persisted) ---
  messages: Message[];
  pendingQuestion: PendingQuestion | null;
  /** A turn of this tab's session is in progress: `activity !== 'idle'`
   *  (docs/design/chat-turn-liveness.md S3). The bee reads this. Written ONLY by
   *  the lifecycle hook's applyActivity — readonly to enforce it at compile time. */
  readonly isStreaming: boolean;
  /** The session's activity as the backend last reported it (session_activity /
   *  session_state push, or the poll), and its seq: only a larger seq replaces it. */
  activity: SessionActivity;
  activitySeq: number;
  /** The session `activity` / `activitySeq` belong to: when tab.sessionId is no
   *  longer it, they are another session's news (rebindActivity starts over). */
  activitySid?: string;
  /** Local time the current activity was applied (orders a poll that no longer
   *  lists the session against the last push: see applyActivity). */
  activityAt?: number;
  /** This tab has a POST to /chat/stream open (its own request; the Send / Stop
   *  button and the queue-or-send decision read it with isStreaming). Written only
   *  by the lifecycle hook's setLocalSending. */
  readonly localSending: boolean;
  /** The send this tab is preparing (handleSendMessage): set when it starts, cleared
   *  by Stop or replaced by a newer send. A send whose token is no longer current
   *  after its await stops there, before writing anything or opening its POST. */
  _sendToken?: number;
  /** The request of each send of this tab the backend has not accepted yet, by
   *  client_id (normally 0-1 entries): removed at `accepted`; after a failure it
   *  is what Retry sends again (docs/design/chat-turn-liveness.md S3). */
  _unsentRequests?: Map<string, import('../types').ChatRequest>;
  /** client_id of the message this tab's open POST sends (noteSend); when the
   *  request ends while it is still unaccepted, its bubble is marked "Not sent". */
  _openSendCid?: string;
  abortController: AbortController | null;
  streamGen: number;
  /** Generation of the LIVE complete-handler for this tab's current turn.
   *  Set by createCompleteHandler at creation (i.e. at send time, after the
   *  send path's incrementStreamGen). The [DONE] handler clears streaming iff
   *  its capturedGen === latestCompleteGen — making it immune to mid-stream
   *  streamGen churn (reconnect/result/error) while still letting a genuinely
   *  NEW send supersede the old handler. Fixes the stale-gen early-return that
   *  left isStreaming pinned true → spinner-hang (run_6adee7d5). */
  latestCompleteGen?: number;
  /** Generation of the LIVE stream-event handler for this tab's current turn.
   *  Stamped EAGERLY by createStreamHandler at creation (i.e. at send time, in
   *  ALL send paths — main/queued/permission-continue — since the stream handler
   *  is always created synchronously, unlike the complete handler which is lazy
   *  in the permission path). The stream-event generation guard discards an event
   *  iff its capturedStreamGen !== latestStreamGen — making the turn's OWN tail
   *  events (context_warning / system_prompt_metadata, delivered AFTER `result`
   *  bumps streamGen) immune to mid-stream streamGen churn, while a genuinely NEW
   *  send still advances latestStreamGen and supersedes the old handler. Fixes the
   *  OT01 render-freeze where result-following tail events were discarded as stale
   *  → turn-end refresh lost → UI frozen until next send (run_f9adee1e). Sibling of
   *  latestCompleteGen (run_6adee7d5) for the stream-event path. */
  latestStreamGen?: number;
  /** The POST stream generation the subscription took over for this same turn
   *  (beginSubscriptionTurn): that POST's later events are discarded by design, so
   *  their discard is not logged as a warning (docs/design/chat-turn-liveness.md S3). */
  _handedOverGen?: number;
  /** Token of the newest stream handler created for this tab. A reconnect/resend
   *  builds a new handler with the SAME stream generation, so the gen guard cannot
   *  tell it from the handler it replaces; this token can. Only the newest handler
   *  may write message content (deltas, assistant, tool_result). */
  latestStreamHandler?: number;
  status: TabStatus;
  /** Per-tab display of the backend context-usage snapshot (null = empty ring).
   *  Backend snapshots (SSE + GET) land via the lifecycle hook's
   *  applyContextUsage; the only other writer is the prompt-too-large error
   *  banner, which reports a rejected request rather than a measurement. */
  contextWarning: ContextWarning | null;
  /** measuredAt of the newest backend snapshot applied to this tab — kept even
   *  when that snapshot cleared the ring, so an older one cannot win later. */
  contextMeasuredAt?: number;
  /** The sessionId contextMeasuredAt was recorded for: ordering only applies
   *  within one session (a tab that loads an older session accepts its value). */
  contextMeasuredFor?: string;
  /** The session (`''` = none yet) whose current critical-context episode
   *  already raised this tab's in-tab notice; cleared when the tab leaves
   *  critical. A different session starts a new episode. */
  /** Per-tab compaction guard event from backend (null = no active guard event). */
  compactionGuard: CompactionGuardEvent | null;
  /** Per-tab system prompt metadata delivered via SSE (null = not yet received). */
  promptMetadata: SystemPromptMetadata | null;
  /** Per-tab expanded/compact mode for ChatInput (runtime-only, NOT serialized). */
  isExpanded?: boolean;
  /** True while the SSE connection is being retried after a connection-phase failure. */
  isReconnecting?: boolean;
  /** True while PATH A cold-start resume is in progress (subprocess killed, re-spawning). */
  isResuming?: boolean;
  /** Pre-first-token step from the backend `turn_phase` event and when it began
   *  (runtime-only, never serialized). Cleared by the first other stream event. */
  turnPhase?: { phase: TurnPhase; since: number };
  /** Current reconnection attempt number (0 = not reconnecting). */
  reconnectionAttempt?: number;
  /** Set to true on the first non-heartbeat SSE event — used to distinguish connection-phase vs mid-stream failures. */
  hasReceivedData?: boolean;
  /** Retry function stored by ChatPage — called by reconnection logic to re-initiate the stream. */
  retryStreamFn?: () => (() => void);
  /** Per-tab stream start timestamp — persists across tab switches so elapsed timer resumes correctly. */
  streamStartTime?: number;
  /** Active pending permission request ID for inline approval (null = no pending). */
  pendingPermissionRequestId?: string | null;
  /** Per-tab attachment list managed by useUnifiedAttachments (runtime-only, NOT serialized). */
  attachments: UnifiedAttachment[];
  /** Set true by handleStop — signals stream/error handlers to suppress
   *  error display for the interrupted stream. Cleared on next send. */
  userStopped?: boolean;
  /** Retry payload from QUEUE_TIMEOUT — stored so ChatPage can offer a "Retry" button.
   *  Set when backend returns QUEUE_TIMEOUT error with retryPayload. */
  queueTimeoutRetry?: {
    sessionId: string;
    agentId: string;
    userMessage: string | null;
    content: unknown[] | null;
    clientId?: string | null;
    /** the handed-back send's terminal output: the retry carries it again */
    terminalContext?: { bufferTail: string; cwd: string } | null;
  } | null;
  /** The session's queue as last read (session_state `pending`, oldest first),
   *  when it was read (local time) and the push seq it came with: the tab's
   *  "Queued" marks follow it (docs/design/chat-turn-liveness.md S3). */
  _serverQueue?: import('../types').PendingQueueEntry[];
  _serverQueueAt?: number;
  _serverQueueSeq?: number;
  /** false when the last list was capped (pending_count > its length): marks of
   *  messages missing from it are kept, not cleared */
  _serverQueueComplete?: boolean;
  /** the session the queue fields above belong to (a new session resets them) */
  _serverQueueSid?: string;
  /** a send of this tab is reading its attachments: another Send waits for it
   *  (the draft is still in the composer, a second click would send it twice) */
  _preparingSend?: number;
  /** Messages this tab sent while busy, shown queued until a list read after
   *  their `accepted` (value: its time; undefined until then) decides. */
  _queuedLocal?: Map<string, number | undefined>;
  /** The newest transcript rev this tab has seen (feed envelopes, since_rev reads);
   *  a resync asks the server what changed after it (hooks/transcriptFeed). */
  transcriptRev?: number;
  /** Timestamp of last sessionStorage checkpoint write (throttle: max 1 per 10s). */
  _lastCheckpointTime?: number;
  /** True when backend returned SESSION_BUSY — the message is pending on the
   *  server. Send button is disabled, "Waiting..." indicator shown. Cleared when
   *  the drained turn completes (session subscription) or the reconcile's drain
   *  surface sees nothing pending. */
  isWaitingForBusy?: boolean;
  /** Self-healing grace period active — backend may be refreshing session.
   *  While true, don't show error on SSE disconnect (looks like "thinking").
   *  Cleared by: (a) grace timeout expiry → show error, or
   *  (b) new stream data arrives → heal succeeded, continue silently. */
  _healGraceActive?: boolean;
  /** Set on heal-grace expiry: the SSE connection is gone but the backend
   *  subprocess may still be STREAMING (a long agent turn can outlive the
   *  connection). While true, a follow-up send must be QUEUED (shouldQueueSend)
   *  — never sent directly, or it escapes to SESSION_BUSY → orphan delete →
   *  silent message loss. Cleared when a new stream starts (handleSendMessage)
   *  or the reconcile loop confirms the backend is genuinely idle. */
  _postDisconnectUncertain?: boolean;
  /** Timestamp when _postDisconnectUncertain was set. Used by the reconcile loop
   *  to time-cap the post-disconnect window (force-clear after 120min) so a
   *  failed stopSession + stuck backend can't brick the tab forever. */
  _postDisconnectAt?: number;
  /** Root-1 SSOT Phase 3 (AC5): the toolUseId of a question the user just
   *  answered. Suppresses the reconcile-loop re-surface of that SAME question
   *  during the window where the backend mirror may still report waiting_input
   *  before transitioning. Set in handleAnswerQuestion; cleared by the reconcile
   *  loop once the backend transitions past the answered question (no longer
   *  waiting_input, or a different pending toolUseId). */
  _answeredToolUseId?: string;
  /** The permission prompt the user just decided in this tab (decision in flight):
   *  the mirror must not re-surface it while the backend still reports
   *  waiting_input on it (core/permission_prompt re-surface, 2026-10-22). */
  _decidedPermissionId?: string;
  /** Root-1 SSOT Phase 3 (AC5): id of the in-tab notice raised for a re-surfaced
   *  question that no assistant bubble could host (no-host path). Tracked so the
   *  notice can be RETIRED when the question later gets a host (inline form
   *  takes over) or is abandoned (backend leaves waiting_input). (Field name
   *  kept from when this was a toast.) */
  _pendingQuestionToastId?: string;
  /** Id of the in-tab notice for a re-surfaced permission prompt no assistant
   *  bubble could host; retired when the card takes over or the prompt ends. */
  _pendingPermissionNoticeId?: string;
  /** The "your message waits for an approval" notice is up (mirror-owned). */
  _queuedBehindApprovalNotice?: boolean;
  /** Write token of this tab's current "still working" notice. The complete
   *  handler retracts exactly that notice at turn end (token-checked, so a late
   *  [DONE] from an older turn cannot remove a newer turn's notice). */
  _stillWorkingNoticeToken?: number;
  /** Root-1 SSOT Phase 3 (AC4): the last_drained_seqs the tab observed on the
   *  previous reconcile poll — compared against the current poll to detect a
   *  server drain and retire the local optimistic queue mirror. */
  _lastDrainedSeqs?: number[];
  /** Session turn routing (hooks/sessionTurnRouter): which source renders this
   *  tab's turn events, the highest eventSeq rendered, held events. Runtime-only. */
  _turnRoute?: import('./sessionTurnRouter').TabTurnState;
  /** The render / completion handlers of the tab's current POST turn and
   *  subscription turn, used by the turn router. Runtime-only. */
  _turnHandlers?: {
    postRender?: (event: import('../types').StreamEvent) => void;
    postComplete?: () => void;
    subRender?: (event: import('../types').StreamEvent) => void;
    subComplete?: () => void;
    /** id of the synthetic queued bubble a drained subscription turn delivers */
    drainSyntheticId?: string;
    /** A CLI-initiated turn rendered INTO the previous assistant bubble: which
     *  turn, which bubble, and how many blocks it had before (a replay of the
     *  same turn truncates back to that before re-rendering). Runtime-only. */
    hostedTurn?: { turnId: string; hostId: string; baseLen: number };
    /** the bubble the tab's latest POST send streams into */
    postBubbleId?: string;
  };
  /** Resume timeout handle — auto-clears isResuming after 60s if no data arrives.
   *  Prevents permanent "Resuming session..." spinner when --resume hangs. */
  _resumeTimeoutId?: ReturnType<typeof setTimeout>;
  /** Disconnect recovery timeout handle — clears reconnecting state after 30s
   *  and triggers DB message recovery. Cleared on stream recovery or tab close. */
  _disconnectTimeoutId?: ReturnType<typeof setTimeout>;
  /** H2 turn-end reconcile debounce handle — fires an unconditional
   *  reconcile-from-DB 200ms after a `result` event so any turn whose
   *  placeholder could not be correlated still finalizes (no "Thinking forever").
   *  Cleared/re-armed on each result; NEVER set on the delta path. */
  _turnEndReconcileTimer?: ReturnType<typeof setTimeout>;
  /** Set true when the reconcile loop force-cleared this tab but the DB-recovery
   *  fetch failed (backend unreachable) — leaving a frozen partial response.
   *  The backend-recovered handler retries the DB reconcile for flagged tabs. */
  _dbReconcileFailed?: boolean;

  /** Set when a CONNECTION-PHASE send exhausted all reconnect attempts while the
   *  backend was unreachable (e.g. a daemon redeploy ~60s outage >> the ~7s
   *  connection-phase reconnect budget). The question never reached the backend,
   *  so mergeTabFromDb has nothing to recover. The backend-recovered handler
   *  auto-resends via retryStreamFn so the user's question isn't silently
   *  swallowed. Connection-phase ONLY — never armed for mid-stream failures
   *  (those may have persisted partial work; resending would double-answer). */
  _pendingResendOnRecovery?: boolean;
  /** The assistant placeholder id of the swallowed turn. On auto-resend the
   *  placeholder's error content is stripped so the fresh response lands in a
   *  clean bubble (retryStreamFn reuses this same id). */
  _pendingResendAssistantId?: string;
  /** Number of auto-resends performed for the current swallowed-question episode.
   *  Capped (RESEND_MAX_ATTEMPTS) so a flapping backend can't drive a resend loop.
   *  Reset on the next manual send. */
  _pendingResendAttempts?: number;

  // ── Per-tab Streaming State Machine (P5) ──────────────────────────
  /** Explicit state machine tracking this tab's streaming mode.
   *  Authoritative for mode queries (streamState.mode === 'streaming').
   *  Boolean flags (isStreaming, isReconnecting, etc.) coexist for backward
   *  compat — future P6 will remove them once all consumers migrate. */
  streamState: StreamingState;
}

/** Fields persisted to ~/.swarm-ai/open_tabs.json (re-exported from tabPersistence service). */
export type SerializableTab = PersistedTab;

/** Hook return interface. */
export interface UseUnifiedTabStateReturn {
  // --- Derived views (stable between mutations) ---
  openTabs: OpenTab[];
  activeTabId: string | null;
  activeTab: UnifiedTab | undefined;
  tabStatuses: Record<string, TabStatus>;

  // --- Tab CRUD ---
  addTab: (agentId: string) => OpenTab | undefined;
  closeTab: (tabId: string) => void;
  selectTab: (tabId: string) => void;

  // --- Metadata updates ---
  updateTabTitle: (tabId: string, title: string) => void;
  updateTabSessionId: (tabId: string, sessionId: string) => void;
  setTabIsNew: (tabId: string, isNew: boolean) => void;

  // --- Runtime state ---
  getTabState: (tabId: string) => UnifiedTab | undefined;
  /** Patch excludes `id` (primary key) and `isStreaming` (written only by the session's activity, applyActivity). */
  updateTabState: (
    tabId: string,
    patch: Partial<Omit<UnifiedTab, 'id' | 'isStreaming'>>,
  ) => void;
  updateTabStatus: (tabId: string, status: TabStatus) => void;

  // --- Lifecycle ---
  restoreTab: (tabId: string) => boolean;
  initTabState: (tabId: string, initialMessages?: Message[]) => void;
  cleanupTabState: (tabId: string) => void;

  // --- Cleanup ---
  removeInvalidTabs: (validSessionIds: Set<string>) => void;

  // --- File-based tab restore ---
  /** Loads tab state from ~/.swarm-ai/open_tabs.json. Returns true if tabs were restored. */
  restoreFromFile: () => Promise<boolean>;

  // --- Dynamic tab limit ---
  /** Fetches the current max tabs value from the backend API and updates the cached ref.
   *  Returns the fetched MaxTabsInfo, or a fallback on failure. */
  fetchMaxTabs: () => Promise<MaxTabsInfo>;
  /** Last known max tabs info from the most recent fetchMaxTabs() call. */
  maxTabsInfo: MaxTabsInfo;

  // --- Direct ref access (for synchronous reads in stream handlers) ---
  tabMapRef: React.RefObject<Map<string, UnifiedTab>>;
  activeTabIdRef: React.RefObject<string | null>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Creates a new UnifiedTab with default runtime state. */
function createDefaultTab(agentId: string): UnifiedTab {
  return {
    id: crypto.randomUUID(),
    title: 'New Session',
    agentId,
    isNew: true,
    sessionId: undefined,
    messages: [],
    pendingQuestion: null,
    isStreaming: false,
    activity: 'idle',
    activitySeq: 0,
    localSending: false,
    abortController: null,
    streamGen: 0,
    status: 'idle',
    contextWarning: null,
    compactionGuard: null,
    promptMetadata: null,
    isReconnecting: false,
    isResuming: false,
    reconnectionAttempt: 0,
    attachments: [],
    streamState: { ...INITIAL_STATE },
  };
}

/** Extracts the serializable subset from a UnifiedTab. */
function toSerializable(tab: UnifiedTab): PersistedTab {
  return {
    id: tab.id,
    title: tab.title,
    agentId: tab.agentId,
    isNew: tab.isNew,
    sessionId: tab.sessionId,
  };
}

/** Hydrates a PersistedTab into a full UnifiedTab with default runtime state. */
function hydrateTab(s: PersistedTab): UnifiedTab {
  return {
    ...s,
    messages: [],
    pendingQuestion: null,
    isStreaming: false,
    activity: 'idle',
    activitySeq: 0,
    localSending: false,
    abortController: null,
    streamGen: 0,
    status: 'idle',
    contextWarning: null,
    compactionGuard: null,
    promptMetadata: null,
    isReconnecting: false,
    isResuming: false,
    reconnectionAttempt: 0,
    attachments: [],
    streamState: { ...INITIAL_STATE },
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useUnifiedTabState(
  defaultAgentId: string,
): UseUnifiedTabStateReturn {
  // ---- Tab_Map: authoritative store (useRef so mutations don't re-render) --
  const tabMapRef = useRef<Map<string, UnifiedTab>>(new Map());

  // ---- Render_Counter: increment to trigger useMemo re-derivation ----------
  const [renderCounter, setRenderCounter] = useState<number>(0);
  const bump = useCallback(() => setRenderCounter((c) => c + 1), []);

  // ---- activeTabId with useRef mirror for synchronous reads ----------------
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const activeTabIdRef = useRef<string | null>(null);

  // Keep ref in sync with state
  const setActiveTabIdBoth = useCallback((id: string | null) => {
    activeTabIdRef.current = id;
    setActiveTabId(id);
  }, []);

  // ---- Initialization (always starts with a default tab) ------------------
  // The actual tab state is loaded asynchronously from open_tabs.json via
  // restoreFromFile(), called by ChatPage on mount after the backend is ready.
  const initialized = useRef(false);
  // fileRestoreDone serves two purposes:
  // 1. Gates restoreFromFile() to run only once (idempotency guard)
  // 2. Gates the save effect — prevents overwriting open_tabs.json with
  //    the temporary default tab before the real tabs are restored
  const fileRestoreDone = useRef(false);
  if (!initialized.current) {
    initialized.current = true;
    const map = tabMapRef.current;
    const defaultTab = createDefaultTab(defaultAgentId);
    map.set(defaultTab.id, defaultTab);
    activeTabIdRef.current = defaultTab.id;
    setActiveTabId(defaultTab.id);
    console.log('[useUnifiedTabState] Init with default tab, awaiting file restore');
  }

  // ---- Dynamic tab limit (cache-based, Option B) --------------------------
  // Cached max tabs value from the last successful API fetch. Initialized to
  // the fallback so addTab() works before the first fetch completes.
  const maxTabsRef = useRef<MaxTabsInfo>({
    maxTabs: MAX_OPEN_TABS_FALLBACK,
    chatMax: Math.max(1, MAX_OPEN_TABS_FALLBACK - 1),
    memoryPressure: 'ok',
  });
  const [maxTabsInfo, setMaxTabsInfo] = useState<MaxTabsInfo>({
    maxTabs: MAX_OPEN_TABS_FALLBACK,
    chatMax: Math.max(1, MAX_OPEN_TABS_FALLBACK - 1),
    memoryPressure: 'ok',
  });

  /**
   * Fetches the current max tabs value from `GET /api/system/max-tabs` and
   * updates the cached ref. Called by ChatPage on mount and periodically.
   * On failure, falls back to `MAX_OPEN_TABS_FALLBACK`.
   */
  const fetchMaxTabs = useCallback(async (): Promise<MaxTabsInfo> => {
    try {
      const response = await api.get('/system/max-tabs');
      const data = response.data;
      const maxTabs = typeof data.max_tabs === 'number' ? data.max_tabs : MAX_OPEN_TABS_FALLBACK;
      const chatMax = typeof data.chat_max === 'number' ? data.chat_max : Math.max(1, maxTabs - 1);
      const info: MaxTabsInfo = {
        maxTabs,
        chatMax,
        memoryPressure: (['ok', 'warning', 'critical'].includes(data.memory_pressure)
          ? data.memory_pressure
          : 'ok') as MaxTabsInfo['memoryPressure'],
      };
      maxTabsRef.current = info;
      setMaxTabsInfo(info);
      return info;
    } catch {
      console.warn('[useUnifiedTabState] Failed to fetch max-tabs, keeping last known value');
      // Keep the previous maxTabsRef value on transient errors — don't
      // downgrade to fallback if we had a successful fetch before.
      // Only use fallback if no prior fetch succeeded (initial state).
      return maxTabsRef.current;
    }
  }, []);

  // ---- Derived views via useMemo (keyed on renderCounter) -----------------

  const openTabs: OpenTab[] = useMemo(() => {
    const tabs: OpenTab[] = [];
    for (const t of tabMapRef.current.values()) {
      tabs.push({
        id: t.id,
        title: t.title,
        agentId: t.agentId,
        isNew: t.isNew,
        sessionId: t.sessionId,
      });
    }
    return tabs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter]);

  const tabStatuses: Record<string, TabStatus> = useMemo(() => {
    const result: Record<string, TabStatus> = {};
    for (const [id, t] of tabMapRef.current.entries()) {
      result[id] = t.status;
    }
    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter]);

  const activeTab: UnifiedTab | undefined = useMemo(() => {
    if (!activeTabId) return undefined;
    return tabMapRef.current.get(activeTabId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderCounter, activeTabId]);

  // ---- Tab CRUD -----------------------------------------------------------

  const addTab = useCallback(
    (agentId: string): OpenTab | undefined => {
      const map = tabMapRef.current;
      const chatMax = maxTabsRef.current.chatMax;
      if (map.size >= chatMax) return undefined;

      const newTab = createDefaultTab(agentId);
      map.set(newTab.id, newTab);
      setActiveTabIdBoth(newTab.id);
      bump();

      return {
        id: newTab.id,
        title: newTab.title,
        agentId: newTab.agentId,
        isNew: newTab.isNew,
        sessionId: newTab.sessionId,
      };
    },
    [bump, setActiveTabIdBoth],
  );

  const closeTab = useCallback(
    (tabId: string) => {
      const map = tabMapRef.current;
      const tab = map.get(tabId);
      if (!tab) return;

      // Abort streaming if active
      if (tab.abortController) {
        try {
          tab.abortController.abort();
        } catch {
          // already aborted — safe to ignore
        }
      }

      // closeTab stays pure (no backend call). R6b: the backend slot release is
      // fired from ChatPage.handleTabClose (which has access to streaming state
      // and the confirm dialog), not here. Best-effort by design — on a hard
      // crash/network failure the backend's 12h idle TTL is the backstop. NOTE:
      // the 10-min "orphan reaper" only kills unowned OS processes, NOT tab-less
      // SessionUnits — only the TTL reaps those, which is why R6b adds the
      // explicit on-close release.

      // Capture ordered keys before removal for reselection
      const keys = [...map.keys()];
      const closedIndex = keys.indexOf(tabId);
      map.delete(tabId);
      // In-tab notices and their timers die with the tab.
      tabNoticeStore.clearTab(tabId);

      if (map.size === 0) {
        // Auto-create a new tab when closing the last one
        const newTab = createDefaultTab(defaultAgentId);
        map.set(newTab.id, newTab);
        setActiveTabIdBoth(newTab.id);
      } else if (activeTabIdRef.current === tabId) {
        // Reselect adjacent tab (clamped to bounds)
        const remaining = [...map.keys()];
        const newIdx = Math.min(closedIndex, remaining.length - 1);
        setActiveTabIdBoth(remaining[newIdx]);
      }

      bump();
    },
    [bump, defaultAgentId, setActiveTabIdBoth],
  );

  const selectTab = useCallback(
    (tabId: string) => {
      if (tabMapRef.current.has(tabId)) {
        setActiveTabIdBoth(tabId);
        bump();
      }
    },
    [bump, setActiveTabIdBoth],
  );

  // ---- Metadata updates ---------------------------------------------------

  const updateTabTitle = useCallback(
    (tabId: string, title: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.title = title;
      bump();
    },
    [bump],
  );

  const updateTabSessionId = useCallback(
    (tabId: string, sessionId: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.sessionId = sessionId;
      bump();
    },
    [bump],
  );

  const setTabIsNew = useCallback(
    (tabId: string, isNew: boolean) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.isNew = isNew;
      bump();
    },
    [bump],
  );

  // ---- Runtime state ------------------------------------------------------

  const getTabState = useCallback(
    (tabId: string): UnifiedTab | undefined => tabMapRef.current.get(tabId),
    [],
  );

  const updateTabState = useCallback(
    (tabId: string, patch: Partial<Omit<UnifiedTab, 'id' | 'isStreaming'>>) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      Object.assign(tab, patch);
      bump();
    },
    [bump],
  );

  const updateTabStatus = useCallback(
    (tabId: string, status: TabStatus) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;
      tab.status = status;
      bump();
    },
    [bump],
  );

  // ---- Lifecycle ----------------------------------------------------------

  const restoreTab = useCallback((tabId: string): boolean => {
    return tabMapRef.current.has(tabId);
  }, []);

  const initTabState = useCallback(
    (tabId: string, initialMessages?: Message[]) => {
      const existing = tabMapRef.current.get(tabId);
      if (existing) {
        // Tab already exists — just update messages if provided
        if (initialMessages) {
          existing.messages = initialMessages;
          // Also seed the store so streaming handlers always find it
          const store = messageStoreRegistry.getOrCreate(tabId);
          store.replace(initialMessages);
          bump();
        }
        return;
      }
      // Eagerly create MessageStore for this tab — guarantees all streaming
      // handlers will find a store (eliminates fallback dual-write paths).
      const store = messageStoreRegistry.getOrCreate(tabId);
      if (initialMessages && initialMessages.length > 0) {
        store.replace(initialMessages);
      }
      const tab: UnifiedTab = {
        id: tabId,
        title: 'New Session',
        agentId: defaultAgentId,
        isNew: true,
        sessionId: undefined,
        messages: initialMessages ?? [],
        pendingQuestion: null,
        isStreaming: false,
    activity: 'idle',
    activitySeq: 0,
    localSending: false,
        abortController: null,
        streamGen: 0,
        status: 'idle',
        contextWarning: null,
        compactionGuard: null,
        promptMetadata: null,
        attachments: [],
        streamState: { ...INITIAL_STATE },
      };
      tabMapRef.current.set(tabId, tab);
      bump();
    },
    [bump, defaultAgentId],
  );

  const cleanupTabState = useCallback(
    (tabId: string) => {
      const tab = tabMapRef.current.get(tabId);
      if (!tab) return;

      // Abort streaming if active
      if (tab.abortController) {
        try {
          tab.abortController.abort();
        } catch {
          // already aborted — safe to ignore
        }
      }

      // Clear the H2 turn-end reconcile debounce so it can't fire after close.
      // (Defensive — the timer body also no-ops when the store is destroyed.)
      if (tab._turnEndReconcileTimer) {
        clearTimeout(tab._turnEndReconcileTimer);
        tab._turnEndReconcileTimer = undefined;
      }

      // Destroy the MessageStore for this tab (cleanup timers, listeners)
      messageStoreRegistry.destroy(tabId);

      tabMapRef.current.delete(tabId);
      bump();
    },
    [bump],
  );

  // ---- File-based tab restore -----------------------------------------------

  /**
   * Loads tab state from ``~/.swarm-ai/open_tabs.json`` via the backend API.
   * Called once by ChatPage after the backend is ready.
   *
   * If the file exists and contains valid tabs, replaces the default tab
   * with the persisted tabs. If the file is missing or empty, keeps the
   * default tab (fresh start).
   */
  const restoreFromFile = useCallback(
    async (): Promise<boolean> => {
      if (fileRestoreDone.current) return false;

      const data = await tabPersistenceService.load();
      if (!data || !data.tabs || data.tabs.length === 0) {
        console.log('[useUnifiedTabState] No open_tabs.json found, keeping default tab');
        fileRestoreDone.current = true; // Genuine fresh install — allow save effect
        return false;
      }

      const map = tabMapRef.current;

      // Race condition guard: if user already started a conversation
      const tabs = [...map.values()];
      if (tabs.length === 1 && tabs[0].sessionId !== undefined) {
        console.log('[useUnifiedTabState] File restore skipped: user already started a conversation');
        return false;
      }

      // Clear default tab and hydrate from file
      map.clear();
      for (const saved of data.tabs.slice(0, MAX_TABS_HARD_CEILING)) {
        map.set(saved.id, hydrateTab(saved));
      }

      // Restore activeTabId — validate it exists in the map
      const firstTabId = map.keys().next().value as string;
      if (data.activeTabId && map.has(data.activeTabId)) {
        setActiveTabIdBoth(data.activeTabId);
      } else {
        setActiveTabIdBoth(firstTabId);
      }

      bump();
      fileRestoreDone.current = true; // Mark done AFTER successful hydration
      console.log(`[useUnifiedTabState] Restored ${data.tabs.length} tabs from open_tabs.json`);

      return true;
    },
    [bump, setActiveTabIdBoth],
  );

  // ---- Cleanup ------------------------------------------------------------

  const removeInvalidTabs = useCallback(
    (validSessionIds: Set<string>) => {
      const map = tabMapRef.current;
      let changed = false;

      for (const tab of map.values()) {
        if (tab.sessionId && !validSessionIds.has(tab.sessionId)) {
          tab.sessionId = undefined;
          tab.isNew = true;
          tab.title = 'New Session';
          changed = true;
        }
      }

      if (changed) bump();
    },
    [bump],
  );

  // ---- Filesystem persistence effect (debounced) ---------------------------
  // Persists the serializable tab subset to ~/.swarm-ai/open_tabs.json
  // via the backend API. Debounced to avoid excessive writes during rapid
  // tab operations (streaming bumps the counter frequently).
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Skip saving until file restore is complete (avoid overwriting
    // the persisted state with the temporary default tab)
    if (!fileRestoreDone.current) return;

    // Debounce: wait 500ms after last change before writing
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const map = tabMapRef.current;
      const tabs: PersistedTab[] = [];
      for (const tab of map.values()) {
        tabs.push(toSerializable(tab));
      }
      const data: OpenTabsFileData = {
        tabs,
        activeTabId: activeTabIdRef.current,
      };
      tabPersistenceService.save(data);
    }, 500);

    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
     
  }, [renderCounter, activeTabId]);

  // ---- Return object ------------------------------------------------------

  return {
    // Derived views
    openTabs,
    activeTabId,
    activeTab,
    tabStatuses,

    // Tab CRUD
    addTab,
    closeTab,
    selectTab,

    // Metadata updates
    updateTabTitle,
    updateTabSessionId,
    setTabIsNew,

    // Runtime state
    getTabState,
    updateTabState,
    updateTabStatus,

    // Lifecycle
    restoreTab,
    initTabState,
    cleanupTabState,

    // Cleanup
    removeInvalidTabs,

    // File-based tab restore
    restoreFromFile,

    // Dynamic tab limit
    fetchMaxTabs,
    maxTabsInfo,

    // Direct ref access
    tabMapRef,
    activeTabIdRef,
  };
}
