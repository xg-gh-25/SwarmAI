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
import type { Message, UnifiedAttachment, ContentBlock, SystemPromptMetadata, CompactionGuardEvent } from '../types/index';
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
  /** Write ONLY via setIsStreaming() — direct mutation bypasses re-render
   *  and causes spinner-hang on background tabs. Marked readonly to enforce
   *  at compile time; setIsStreaming() uses a type cast internally. */
  readonly isStreaming: boolean;
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
  status: TabStatus;
  /** Per-tab context warning from backend context monitor (null = no warning). */
  contextWarning: ContextWarning | null;
  /** Per-tab compaction guard event from backend (null = no active guard event). */
  compactionGuard: CompactionGuardEvent | null;
  /** Per-tab system prompt metadata delivered via SSE (null = not yet received). */
  promptMetadata: SystemPromptMetadata | null;
  /** Per-tab expanded/compact mode for ChatInput (runtime-only, NOT serialized). */
  isExpanded?: boolean;
  /** Per-tab scroll position (runtime-only, NOT serialized).
   *  `undefined` = scroll to bottom (new tab default).
   *  A number = saved scrollTop value to restore on tab switch. */
  scrollPosition?: number;
  /** True while the SSE connection is being retried after a connection-phase failure. */
  isReconnecting?: boolean;
  /** True while PATH A cold-start resume is in progress (subprocess killed, re-spawning). */
  isResuming?: boolean;
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
  } | null;
  /** Queued message payload — stored when user sends while streaming.
   *  Drained automatically when the current stream completes or is stopped. */
  queuedMessage?: {
    text: string;
    attachments: UnifiedAttachment[];
    displayContent: ContentBlock[];
    messageId: string;
  };
  /** Timestamp when queuedMessage was set. Used by reconcile poll to detect
   *  stale queues (>60s = SSE event lost, drain deadlock). */
  _queuedAt?: number;
  /** Timestamp of last sessionStorage checkpoint write (throttle: max 1 per 10s). */
  _lastCheckpointTime?: number;
  /** Reconciliation race guard: set ONLY by setIsStreaming(true). Never touched by
   *  elapsed-timer effects or selectTab. Used by reconcile loop to skip fresh streams. */
  _reconcileStreamStart?: number;
  /** Timestamp of the most recent setIsStreaming(false) (any clear: user Stop,
   *  turn end, force-clear). Flap-guard for the reconcile loop's idle→streaming
   *  re-arm: the loop must NOT re-arm the spinner within the settle window after
   *  a clear, or the ~5s gap between a user Stop (frontend idle) and the backend
   *  transitioning STREAMING→IDLE would let a poll re-light a just-stopped tab. */
  _streamClearedAt?: number;
  /** Reconcile-OWNED backstop clock. Stamped by the 15s reconcile poll the FIRST
   *  time it observes the stuck condition (frontend=streaming + backend NOT
   *  streaming + NOT an active backend state). Reset to undefined by the same poll
   *  whenever that condition does not hold. Because ONLY the reconcile loop writes
   *  it — never setIsStreaming / reconnect / elapsed-timer — a daemon-restart
   *  reconnect loop cannot postpone it. This is the re-arm-immune hard deadline. */
  _idleStreamingSince?: number;
  /** Absolute hard-cap clock (OT01 route-A). Stamped SET-ONCE on the streaming
   *  false→true edge inside setIsStreaming() (guarded by `=== undefined`, so a
   *  churn re-entry that re-enters setIsStreaming(true) without an intervening
   *  false does NOT postpone the deadline), and cleared on the streaming→idle edge
   *  in setIsStreaming(false). It is NOT reset by the reconcile loop's
   *  reset-and-skip churn — distinct from _idleStreamingSince (which the poll
   *  resets to undefined every tick the stuck condition lapses): under abort+recycle
   *  churn the backend momentarily reports streaming between turns, so the settle
   *  clock keeps restarting and force-clear is never reached (the "stuck 10+ min"
   *  case, frontend.log forcing-clear ×204). This clock gives forceClearStreamVerdict
   *  an absolute upper bound (default 120s; effective clear latency is hardCapMs +
   *  one ~15s reconcile poll). Consulted ONLY after all four alive guards
   *  (backend_streaming/active_backend/flushing/resuming) AND exempted for
   *  drain/queue holds, so it can never clear a genuinely-live or queued-draining
   *  turn. NOTE: because every terminal handler routes through setIsStreaming(false),
   *  the clock is cleared on all turn ends — but a queued-drain result keeps
   *  isStreaming true (no false edge), so the clock spans a whole multi-turn drain;
   *  the drain/queue exemption in forceClearStreamVerdict covers that span. */
  _streamingSinceHardStart?: number;
  /** True between result-event (hasQueuedMessage) and drain completion.
   *  Signals reconcile poll: "backend is IDLE but drain is intentionally
   *  holding streaming state — do NOT force-clear." */
  drainPending?: boolean;
  /** True when backend returned SESSION_BUSY — polling for response completion.
   *  Send button is disabled, "Waiting..." indicator shown, polling active. */
  isWaitingForBusy?: boolean;
  /** Per-tab polling interval handle — cleared on tab close or recovery. */
  busyPollInterval?: ReturnType<typeof setInterval>;
  /** Per-tab polling timeout handle — cleared on tab close or recovery. */
  busyPollTimeout?: ReturnType<typeof setTimeout>;
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
  /** Root-1 SSOT Phase 3 (AC5): toolUseId of a re-surfaced question for which a
   *  discovery toast was raised because no assistant bubble existed to host the
   *  inline form (no-host path). Tracked so the toast can be RETIRED when the
   *  question later gets a host (inline form takes over) or is abandoned (backend
   *  leaves waiting_input) — otherwise the persistent toast leaks forever. */
  _pendingQuestionToastId?: string;
  /** Root-1 SSOT Phase 3 (AC4): the last_drained_seqs the tab observed on the
   *  previous reconcile poll — compared against the current poll to detect a
   *  server drain and retire the local optimistic queue mirror. */
  _lastDrainedSeqs?: number[];
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
  /** Patch excludes `id` (primary key) and `isStreaming` (write-only via setIsStreaming). */
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

      // Clear SESSION_BUSY polling if active (prevents orphaned intervals)
      if (tab.busyPollInterval) clearInterval(tab.busyPollInterval);
      if (tab.busyPollTimeout) clearTimeout(tab.busyPollTimeout);

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
