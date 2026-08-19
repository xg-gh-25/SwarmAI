/* eslint-disable react-refresh/only-export-components, react-hooks/exhaustive-deps */
/**
 * Main chat page component for SwarmAI.
 *
 * Renders the chat interface including message history, streaming indicators,
 * input area, TSCC panel, and right sidebars (Radar, History, File Browser).
 *
 * Streaming lifecycle state (messages, sessionId, pendingQuestion, isStreaming,
 * refs, and handler factories) is delegated to ``useChatStreamingLifecycle``.
 * This component focuses on:
 *
 * - JSX rendering and layout
 * - User interaction handlers (send, stop, answer, permission)
 * - Query hooks (agents, sessions, skills, plugins)
 * - Tab management
 * - TSCC panel integration
 * - Plugin command routing
 *
 * ``deriveStreamingActivity`` is re-exported for backward compatibility with
 * existing test imports.
 *
 * @module ChatPage
 */
import { useState, useRef, useEffect, useCallback, useMemo, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import type { Message, ContentBlock, Agent, AgentCreateRequest, ChatSession } from '../types';
import { MAX_ATTACHMENTS } from '../types';
import { chatService } from '../services/chat';
import { messageStoreRegistry } from '../stores/MessageStore';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { pluginsService } from '../services/plugins';
import { workspaceService } from '../services/workspace';
import { tasksService } from '../services/tasks';
import { Spinner, ConfirmDialog, AgentFormModal, ErrorBoundary } from '../components/common';
import { useToast } from '../contexts/ToastContext';
import { useHealth } from '../contexts/HealthContext';
import { deriveBackendLiveness } from '../hooks/useHealthMonitor';
import { useSessionMeta } from '../contexts/LayoutContext';
import { BACK_TO_CHAT_EVENT } from '../components/layout/useExclusiveOverlay';
import { useOverlay } from '../contexts/OverlayContext';
import type { UiContextSnapshot, CanvasSnapshot } from '../utils/uiContext';
import { ChatDropZone } from '../components/chat/ChatDropZone';
import { FilePreviewModal } from '../components/workspace/FilePreviewModal';
import FileViewerPanel from '../components/file-viewer/FileViewerPanel';
import { useCanvasHost } from '../hooks/useCanvasHost';
import { useRateLimiter, useRateLimitCountdown } from '../hooks';
import { useUnifiedAttachments } from '../hooks/useUnifiedAttachments';
import { useTSCCState } from '../hooks/useTSCCState';
import { useUnifiedTabState } from '../hooks/useUnifiedTabState';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { shouldQueueSend } from '../hooks/streaming-guards';
import { useVoiceConversation } from '../hooks/useVoiceConversation';
import { ChatHeader, ChatInput, TabView, AlertsPill } from './chat/components';
// HistoryOverlay retired (M3): migrated to OverlayHost registry (overlaySurfaces.tsx).
// parseWorkPacket still used by ChatPage's dispatch (linked_context → work packet);
// ToDoOverlay itself retired (M4): migrated to the OverlayHost registry.
import { parseWorkPacket } from '../components/layout/ToDoOverlay';
import { setOverlayCtxBridge } from '../components/layout/overlayRegistry';
import { OverlayHost } from '../components/layout/OverlayHost';
// ToDo / Jobs & Runs / Pipeline / Pollinate + NewBrain overlays retired (M3+M4):
// all migrated to the OverlayHost registry (overlaySurfaces.tsx); dispatch reaches
// them via the ctx bridge (setOverlayCtxBridge below).
import { type ResumeTabInfo } from './chat/resumeTarget';
import { classifyLanding, type LandingVerdict } from './chat/landPromptInTab';
import { dispatchInjectChatInput } from './chat/injectChatInput';
import { canBindSessionToActiveTab } from './chat/sessionBinding';
import { planDispatchBackfill } from './chat/dispatchBackfill';
import { todosService } from '../services/todos';
import type { ToDo } from '../types/todo';
import { useAttentionQueue } from '../hooks/useAttentionQueue';
import RefreshContextModal from '../components/modals/RefreshContextModal';

import { mergeOlderMessages, toDisplayMessage } from './chat/utils';
import { EXPLORER_ATTACH_FILE, EXPLORER_ASK_ABOUT_FILE } from '../constants/explorerEvents';
import { CLAUDE_NATIVE_IMAGE_MIMES } from '../utils/fileClassification';

/**
 * Re-export ``deriveStreamingActivity`` and tab constants from the
 * extracted hooks so existing test imports (``from '../pages/ChatPage'``)
 * continue to resolve.
 */
export { deriveStreamingActivity, formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS, MIN_ACTIVITY_DISPLAY_MS } from '../hooks/useChatStreamingLifecycle';
export { MAX_OPEN_TABS, MAX_TABS_HARD_CEILING, MAX_OPEN_TABS_FALLBACK } from '../hooks/useUnifiedTabState';

/**
 * Build the full injected prompt for a dispatched ToDo (B6 fix, run_937f5cc9).
 * A dispatched todo is a self-contained work packet — the injected chat prompt
 * must carry ALL of it (description + files + design docs + next step + acceptance
 * + blockers + notes), not just the title. Sections are omitted when empty, so a
 * bare todo (no linked context) degrades to just its title (old behavior).
 */
export function buildDispatchPrompt(todo: ToDo): string {
  const wp = parseWorkPacket(todo.linkedContext);
  const lines: string[] = [todo.title];
  if (todo.description) lines.push('', todo.description);
  const section = (label: string, items?: string[]) => {
    // Array.isArray guard: linkedContext is parsed JSON, so a hand-edited/malformed
    // DB row could put a scalar where an array is typed — never call .filter on it.
    const list = (Array.isArray(items) ? items : []).filter((s) => typeof s === 'string' && s.trim());
    if (list.length) lines.push('', `${label}:`, ...list.map((s) => `- ${s}`));
  };
  const line = (label: string, val?: string) => {
    if (val && val.trim()) lines.push('', `${label}: ${val.trim()}`);
  };
  if (wp) {
    section('Files to read/modify', wp.files);
    section('Design docs', wp.design_docs);
    section('Related commits', wp.commits);
    section('Related sessions', wp.sessions);
    section('Memory refs', wp.memory_refs);
    section('Blockers', wp.blockers);
    line('Next step', wp.next_step);
    line('Acceptance', wp.acceptance);
    line('Notes', wp.notes);
  }
  return lines.join('\n');
}

/** Max messages to load on initial session restore / tab switch.
 *  Backend validates limit 1–200; 200 covers 95%+ of sessions in one fetch.
 *  The infinite-scroll page size in loadOlderMessages is a separate concern. */
const INITIAL_MESSAGE_LOAD_LIMIT = 200;

/** Convert a backend ChatMessage to the frontend Message shape. */
export default function ChatPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { addToast, removeToast } = useToast();
  const { health } = useHealth();
  const { setActiveSessionMeta } = useSessionMeta();
  const { isLimited, getRemainingSeconds } = useRateLimiter();
  const chatRateLimitCountdown = useRateLimitCountdown({ getRemainingSeconds, endpoint: '/chat' });

  // Core chat state — streaming lifecycle delegated to extracted hook
  const [inputValue, setInputValue] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>('default');
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [messagesReady, setMessagesReady] = useState(false);
  const mountTimeRef = useRef(performance.now());

  // ── A2 liveness wiring (run_d2f25153) ──────────────────────────────────────
  // The loop-independent backend-liveness verdict, exposed to the SSE stall timer
  // (chat.ts) AND the per-tab MessageStore watchdog so NEITHER blind-cancels a
  // live-but-silent stream (Gate-1 Finding 1, single authority). health.status is
  // read through a ref so the verdict is LIVE when a timer fires minutes later —
  // a value captured at send time would be stale. deriveBackendLiveness pins the
  // mapping (connected/degraded→alive, disconnected→dead, initializing→unknown).
  const healthStatusRef = useRef(health.status);
  healthStatusRef.current = health.status;
  const isBackendLiveRef = useRef(() => deriveBackendLiveness(healthStatusRef.current));
  const streamLiveness = useMemo(
    () => ({ isBackendLive: isBackendLiveRef.current }),
    [],
  );
  // Feed the registry once so every per-tab MessageStore watchdog consults the
  // SAME verdict (AC4). Stable closure → safe to run on every render.
  useEffect(() => {
    messageStoreRegistry.setDefaultLiveness(isBackendLiveRef.current);
  }, []);
  /** Per-tab draft text storage — NOT serialized to open_tabs.json to avoid large text writes. */
  const inputValueMapRef = useRef<Map<string, string>>(new Map());
  /** ToDo Dispatch (A2): append-only pending records whose target tab has no
   *  session_id yet — backfilled at first send, dropped per-tab after backfill or
   *  on tab close (bounds growth, Gate-2 MEDIUM #9). */
  const dispatchPendingRef = useRef<{ tabId: string; todoId: string; tabLabel: string }[]>([]);
  /** Ref mirror of isExpanded for synchronous reads in handleTabSelect (avoids dep array churn). */
  const isExpandedRef = useRef(isExpanded);
  isExpandedRef.current = isExpanded;
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);

  // Per-tab permission loading guard — prevents double-click during API call.
  // Keyed by tabId so parallel tabs don't block each other.
  const permissionLoadingTabs = useRef(new Set<string>());
  const [deleteConfirmSession, setDeleteConfirmSession] = useState<ChatSession | null>(null);
  // R6b: pending close-confirmation for a STREAMING tab. Closing an idle tab
  // releases the backend slot silently; closing a streaming tab asks first so a
  // half-finished response isn't silently discarded.
  const [closeConfirmTab, setCloseConfirmTab] = useState<{ tabId: string; sessionId?: string } | null>(null);
  const [isEditAgentOpen, setIsEditAgentOpen] = useState(false);

  // File preview state
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);
  const [showRefreshModal, setShowRefreshModal] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // LayoutContext — attachment state removed (now in useUnifiedAttachments)

  // Data queries
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: agentsService.list,
  });

  const { data: skills = [] } = useQuery({
    queryKey: ['skills'],
    queryFn: skillsService.list,
    enabled: messagesReady,
  });

  const { data: plugins = [] } = useQuery({
    queryKey: ['plugins'],
    queryFn: pluginsService.listPlugins,
    enabled: messagesReady,
  });

  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['chatSessions', selectedAgentId],
    queryFn: () => chatService.listSessions(selectedAgentId || undefined),
    enabled: !!selectedAgentId && messagesReady,
  });

  const taskId = searchParams.get('taskId');
  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => taskId ? tasksService.get(taskId) : null,
    enabled: !!taskId,
  });

  const { data: agentWorkDir } = useQuery({
    queryKey: ['agentWorkDir', selectedAgentId],
    queryFn: () => agentsService.getWorkingDirectory(selectedAgentId!),
    enabled: !!selectedAgentId,
  });

  // Derived state
  const effectiveBasePath = agentWorkDir?.path;
  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  // Tab state management — unified hook (single source of truth)
  const {
    openTabs,
    activeTabId,
    addTab,
    closeTab,
    selectTab,
    updateTabTitle,
    updateTabSessionId,
    setTabIsNew,
    removeInvalidTabs,
    tabStatuses,
    updateTabStatus,
    getTabState,
    updateTabState,
    tabMapRef,
    activeTabIdRef,
    restoreTab,
    initTabState,
    restoreFromFile,
    fetchMaxTabs,
    maxTabsInfo,
  } = useUnifiedTabState(selectedAgentId || 'default');

  // File attachment — unified hook replaces both useFileAttachment and LayoutContext.attachedFiles
  const { attachments, addFiles, addWorkspaceFiles, removeAttachment, clearAll: clearAttachments,
    isProcessing: isProcessingFiles, error: fileError, canAddMore, restoreAttachments } = useUnifiedAttachments(
    activeTabId, tabMapRef, selectedAgentId, effectiveBasePath
  );

  // ── Explorer → Chat custom event bridge ──────────────────────────────
  // Handles "Attach to Chat" and "Ask Swarm about this" from context menu.
  useEffect(() => {
    const handleAttach = (e: Event) => {
      const file = (e as CustomEvent).detail;
      if (file) addWorkspaceFiles([file]);
    };
    const handleAsk = (e: Event) => {
      const file = (e as CustomEvent).detail;
      if (file) {
        addWorkspaceFiles([file]);
        // Focus the chat input after a tick (allow React state update)
        requestAnimationFrame(() => {
          const input = document.querySelector<HTMLTextAreaElement>('[data-testid="chat-input"]');
          input?.focus();
        });
      }
    };
    window.addEventListener(EXPLORER_ATTACH_FILE, handleAttach);
    window.addEventListener(EXPLORER_ASK_ABOUT_FILE, handleAsk);
    return () => {
      window.removeEventListener(EXPLORER_ATTACH_FILE, handleAttach);
      window.removeEventListener(EXPLORER_ASK_ABOUT_FILE, handleAsk);
    };
  }, [addWorkspaceFiles]);

// Ref-bridge for the queue drain callback: drainQueuedMessage depends on
  // createStreamHandler et al. from the lifecycle hook, so it can't be passed
  // directly at hook-construction time. Instead we pass a stable wrapper that
  // reads from this ref, then update the ref once drainQueuedMessage is defined.
  const drainQueueRef = useRef<(tabId: string) => void>(() => {});
  // Same forward-ref pattern for the recovery_exhausted toast's "Start fresh
  // session" action — handleNewChat is defined below the hook call, so the hook
  // gets a stable wrapper that reads this ref.
  const startFreshRef = useRef<(tabId: string) => void>(() => {});
  // Same forward-ref pattern for the per-tab ToDo backfill (backfillDispatchForTab
  // is defined below the hook call): the hook's onTabSessionBound reads this ref.
  const backfillDispatchForTabRef = useRef<(tabId: string, sessionId: string) => void>(() => {});
  // Dedup guard: (tabId::sessionId) already backfilled — the active tab reaches
  // the backfill by two paths (callback + effect); dispatch each pair only once.
  const backfilledTabSessionsRef = useRef<Set<string>>(new Set());

  // Streaming lifecycle hook — owns messages, sessionId, pendingQuestion,
  // isStreaming, refs, and stream handler factories (Phase 0 extraction).
  // Tab state is now managed by useUnifiedTabState; unified hook methods
  // are passed as deps so stream handlers can read/write the Tab_Map.
  // Called before useTSCCState so sessionId is available for TSCC.
  const {
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
    pendingStreamTabs,
    clearPendingStreamTab,
    bumpStreamingDerivation,
    messagesEndRef,
    incrementStreamGen,
    userScrolledUpRef,
    resetUserScroll,
    createStreamHandler,
    createCompleteHandler,
    createDisconnectHandler,
    createErrorHandler,
    contextWarning,
    setContextWarning,
    clearContextWarning: _clearContextWarning,
    promptMetadata,
    setPromptMetadata,
    isLikelyStalled,
    isWaitingForBusy,
    cancelBusyWait,
  } = useChatStreamingLifecycle({
    queryClient,
    getSession: (sid: string) => chatService.getSession(sid),
    getTabState,
    updateTabState,
    updateTabStatus,
    tabMapRef,
    activeTabIdRef,
    onDrainQueue: (tabId: string) => drainQueueRef.current(tabId),
    onSelectTab: (tabId: string) => selectTab(tabId),
    onStartFresh: (tabId: string) => startFreshRef.current(tabId),
    // Backfill a dispatched ToDo's session_id the moment its tab binds a session
    // — fires for BACKGROUND tabs too (the active-only effect misses those).
    // Decoupled: the hook fires this opaque callback; all ToDo logic stays here.
    onTabSessionBound: (tabId: string, boundSessionId: string) => backfillDispatchForTabRef.current(tabId, boundSessionId),
  });

  // TSCC state management — lifecycle state and UI preferences only.
  // System prompt metadata is now delivered via SSE and managed by useChatStreamingLifecycle.
  useTSCCState(sessionId ?? null);

  // ─── Voice Conversation Mode ──────────────────────────────────────
  // Derive streaming text content from the last assistant message for TTS.
  const latestTextContent = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && m.content) {
        for (const block of m.content) {
          if (block.type === 'text' && block.text) return block.text;
        }
      }
    }
    return '';
  }, [messages]);

  // Ref for handleSendMessage to avoid circular dependency
  const handleSendMessageRef = useRef<() => void>(() => {});

  // Voice conversation orchestrator
  const voiceConversation = useVoiceConversation({
    sessionId: sessionId ?? null,
    onSendMessage: useCallback((text: string) => {
      // Direct ref mutation + synchronous call — same pattern as handleFocusClick.
      // Avoids the setTimeout race condition where React state updates
      // could interleave before the send reads inputValueRef.
      inputValueRef.current = text;
      setInputValue(text);
      handleSendMessageRef.current();
    }, [setInputValue]),
    isStreaming,
    latestTextContent,
    isResponseComplete: !isStreaming,
  });

  // NOTE: `lastAssistantIdx` and `lastResumeBoundaryIdx` were removed here in
  // Migration Step 1 — they are pure functions of `messages` and are now
  // computed internally by `TabView` (the only consumer of those indices).

  // Refs for frequently-changing values — stabilizes useCallback identity for
  // handleSendMessage (Req 7.1, 7.3). Without these, the callback would need
  // every volatile dep in its dependency array and re-create on every keystroke.
  const inputValueRef = useRef(inputValue);
  inputValueRef.current = inputValue;
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  // Canvas host (bugs 2+3): ChatPage owns the Canvas because it owns the tabs.
  // Per-tab state (file/pin/mute/expand/manuallyOpen) + the swarm:open-file
  // listener + streaming-gated auto-surface + proprioception emits all live in
  // useCanvasHost, keyed by activeTabId. Rendered BELOW ChatHeader (JSX further
  // down) so Canvas visibly belongs to the current tab. Replaces the former
  // global Canvas state that lived in ThreeColumnLayout.
  const canvas = useCanvasHost({ activeTabId, sessionId, isStreaming });

  // Agent UI proprioception (SENSE): assemble a request-time snapshot of the
  // agent's own UI state — open file + Canvas state + which nav overlay is open —
  // so the agent can perceive what it is currently showing the user. Merged from
  // three existing signals into ONE uiStateRef snapshot (the send path reads
  // .current and serializes via toEditorContextPayload). Superset of the former
  // file-only editorContextRef; the wire key stays `editorContext`.
  const uiStateRef = useRef<UiContextSnapshot | null>(null);
  const mergeUiState = useCallback((patch: Partial<UiContextSnapshot>) => {
    uiStateRef.current = { ...(uiStateRef.current ?? {}), ...patch };
  }, []);
  // (a) open file — swarm:editor-file-changed carries {filePath, fileName} | null
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | { filePath: string; fileName: string }
        | null;
      mergeUiState({ filePath: detail?.filePath ?? '', fileName: detail?.fileName ?? '' });
    };
    window.addEventListener('swarm:editor-file-changed', handler);
    return () => window.removeEventListener('swarm:editor-file-changed', handler);
  }, [mergeUiState]);
  // (b) Canvas state — swarm:canvas-state carries a CanvasSnapshot (open/count/
  //     pin/mute/collapsed). ThreeColumnLayout emits {open:false,...} on close so
  //     a stale count never lingers (Gate-1 CRITICAL: panel unmounts when closed).
  useEffect(() => {
    const handler = (e: Event) => {
      mergeUiState({ canvas: (e as CustomEvent).detail as CanvasSnapshot | null });
    };
    window.addEventListener('swarm:canvas-state', handler);
    return () => window.removeEventListener('swarm:canvas-state', handler);
  }, [mergeUiState]);
  // (c) which fullscreen nav overlay is open — read from the OverlayHost state
  //     authority (OverlayContext), NOT the legacy useActiveOverlayEvent singleton.
  //     Since every ALL_SHOW_EVENTS surface migrated to the host (M2–M4), the legacy
  //     singleton is only written by the now-unmounted legacy overlays → it is
  //     permanently null, which silently blanked BOTH the agent's SENSE payload
  //     (uiContext active_overlay) AND the AlertsPill/tab-switch BACK_TO_CHAT guard
  //     below. useOverlay() is the live source (ChatPage renders inside OverlayProvider).
  const { activeOverlay, setAgentId: setOverlayAgentId } = useOverlay();
  // Ref mirror for synchronous reads in handleTabSelect's overlay-close guard (avoids
  // dep-array churn on that hot path — same pattern as isExpandedRef).
  const activeOverlayRef = useRef(activeOverlay);
  activeOverlayRef.current = activeOverlay;
  useEffect(() => {
    mergeUiState({ activeOverlay });
  }, [activeOverlay, mergeUiState]);
  // (d) SEND-TIME canvas refresh (race fix, run_e45a04d3). The swarm:canvas-state
  //     subscription (b) is async — it can lag a fast send, leaving canvas stale
  //     in the snapshot. Call this SYNCHRONOUSLY right before each send to read
  //     the LIVE canvas state (getCanvasSnapshot reads the ref source of truth),
  //     so ACT → SENSE closes deterministically instead of racing. Subscription
  //     (b) stays as the passive keep-fresh path (zero-regression).
  const refreshCanvasForSend = useCallback(() => {
    mergeUiState({ canvas: canvas.getCanvasSnapshot() });
  }, [canvas, mergeUiState]);

  // P2: attached terminal output — set once when the user clicks "Attach to
  // chat" on a terminal, consumed on the NEXT send, then cleared (one-shot, so
  // stale build logs don't ride every subsequent turn).
  const terminalContextRef = useRef<{ bufferTail: string; cwd: string } | null>(null);
  useEffect(() => {
    const handler = (e: Event) => {
      terminalContextRef.current = (e as CustomEvent).detail ?? null;
    };
    window.addEventListener('swarm:attach-terminal', handler);
    return () => window.removeEventListener('swarm:attach-terminal', handler);
  }, []);

  const agentSkills = selectedAgent?.allowAllSkills
    ? skills
    : selectedAgent?.allowedSkills
      ? skills.filter((s) => selectedAgent.allowedSkills.includes(s.folderName))
      : [];

  const agentPlugins = selectedAgent?.pluginIds
    ? plugins.filter((p) => selectedAgent.pluginIds.includes(p.id))
    : [];

  const enableSkills = selectedAgent?.allowAllSkills || agentSkills.length > 0 || agentPlugins.length > 0;
  // Always enable MCP — the backend discovers MCP servers from
  // .claude/mcps/mcp-catalog.json and mcp-dev.json at session start.
  // load_mcp_config() safely returns empty dict when no servers are configured.
  const enableMCP = true;

  // Load session messages helper.
  // Uses a generation counter to discard stale results when multiple
  // loadSessionMessages calls race (e.g. rapid tab switches, restart restore).
  const loadGenRef = useRef(0);
  const loadSessionMessages = useCallback(async (sid: string) => {
    const thisGen = ++loadGenRef.current;
    // ── Phase Gate (MessageStore) ──
    // If the active tab's store is streaming, DB fetch would overwrite
    // in-progress content. Skip entirely — reconcile will run on endStreaming().
    const currentTabForGate = activeTabIdRef.current;
    if (currentTabForGate) {
      const tabForGate = tabMapRef.current.get(currentTabForGate);
      if (tabForGate?.isStreaming) {
        console.log('[ChatPage] loadSessionMessages skipped — tab is streaming', { sid });
        return;
      }
    }
    setIsLoadingHistory(true);
    try {
      const sessionMessages = await chatService.getSessionMessagesPaginated(sid, INITIAL_MESSAGE_LOAD_LIMIT);
      // Async guard: discard if a newer load was started while we awaited
      if (loadGenRef.current !== thisGen) return;
      // Tab-switch guard: discard if active tab changed during the async fetch
      // (prevents session A's messages from being applied to tab B)
      if (activeTabIdRef.current !== currentTabForGate) return;
      // Phase guard (post-fetch): streaming may have started during the async fetch
      const postFetchTab = activeTabIdRef.current ? tabMapRef.current.get(activeTabIdRef.current) : null;
      if (postFetchTab?.isStreaming) {
        console.log('[ChatPage] loadSessionMessages discarded (streaming started during fetch)', { sid });
        return;
      }
      const formattedMessages: Message[] = sessionMessages.map(toDisplayMessage);
      setMessages(formattedMessages);
      setSessionId(sid);
      setPendingQuestion(null);
      setHasMoreMessages(sessionMessages.length === INITIAL_MESSAGE_LOAD_LIMIT);
      // Sync loaded messages back into the tab map so subsequent tab switches
      // don't see empty messages and re-fetch unnecessarily.
      const currentTabId = activeTabIdRef.current;
      if (currentTabId) {
        const tab = tabMapRef.current.get(currentTabId);
        if (tab && tab.sessionId === sid && !tab.isStreaming) {
          tab.messages = formattedMessages;
          // Seed the store too — keep store and tabState.messages in sync so the
          // store→React sync effect doesn't later clobber React with an empty
          // snapshot on the next tab switch. seedFromLoad (NOT replace): several of
          // loadSessionMessages's call sites are not gated on an empty store
          // (session-open/task-drag/active-restore), so a persist-lagged shorter
          // paginated row must NOT clobber a fuller just-streamed last-assistant
          // already in the store — the source of TabView rescue (b). (OT01 Scope B,
          // run_2aea0237; more-complete-wins guard reused from the reconcile path.)
          messageStoreRegistry.getOrCreate(currentTabId).seedFromLoad(formattedMessages);
        }
      }
    } catch (error) {
      if (loadGenRef.current !== thisGen) return; // stale — discard
      console.error('Failed to load session messages:', error);
    } finally {
      if (loadGenRef.current === thisGen) {
        setIsLoadingHistory(false);
        setMessagesReady(true);
      }
    }
  }, [setMessages, setSessionId, setPendingQuestion, setIsLoadingHistory]);

  // Re-reconcile a tab's view from the authoritative DB content. Used when a
  // stream was force-cleared but the inline DB-recovery fetch failed (backend
  // unreachable) — leaving a frozen partial response. Uses store.replace (not
  // reconcile) so the temp streaming placeholder is dropped, not duplicated.
  // Guarded against streaming (replace is a no-op mid-stream).
  const reconcileTabFromDb = useCallback(async (tabId: string) => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab || !tab.sessionId || tab.isStreaming) return;
    const sid = tab.sessionId;
    try {
      const msgs = await chatService.getSessionMessagesPaginated(sid, INITIAL_MESSAGE_LOAD_LIMIT);
      const tabNow = tabMapRef.current.get(tabId);
      // Guards: tab gone, session changed, or streaming restarted during fetch.
      if (!tabNow || tabNow.sessionId !== sid || tabNow.isStreaming) return;
      const formatted: Message[] = msgs.map(toDisplayMessage);
      const store = messageStoreRegistry.getOrCreate(tabId);
      if (store.phase !== 'idle') return;
      store.replace(formatted);
      // Bridge: sync store → tabState for legacy readers
      tabNow.messages = store.messages;
      tabNow._dbReconcileFailed = false;
      if (tabId === activeTabIdRef.current) {
        setMessages(formatted);
        setSessionId(sid);
      }
      console.log(`[ChatPage] Recovery reconcile succeeded for tab ${tabId} (${formatted.length} msgs)`);
    } catch (err) {
      // Still unreachable — keep _dbReconcileFailed=true so the next
      // backend-recovered event retries.
      console.warn(`[ChatPage] Recovery reconcile retry failed for tab ${tabId}:`, err);
    }
  }, [tabMapRef, activeTabIdRef, setMessages, setSessionId]);

  // Content reconcile (no-loss): MERGE the tab's view with authoritative DB
  // content using MessageStore.reconcile → _applyMerge. Unlike reconcileTabFromDb
  // (store.replace, for frozen-partial recovery), this is boundary-aware and
  // append-or-update by id, so it SURFACES responses the backend persisted while
  // the SSE stream was detached (Stop / kill / cold-resume / daemon restart)
  // WITHOUT re-introducing scrolled-past prior-session history (the regression the
  // old `messages.length === 0` gate was guarding against — now handled by
  // _applyMerge's resume-boundary + DB-wins-by-id logic). Idempotent; phase-gated
  // (no-op while streaming); fully error-guarded (never aborts the live stream).
  const mergeTabFromDb = useCallback(async (tabId: string, force = false) => {
    const tab = tabMapRef.current.get(tabId);
    // `force` bypasses the isStreaming gate — used ONLY by the foreground-resume
    // reconcile AFTER the backend has confirmed (via streaming-state) that this
    // session is no longer streaming, so it cannot clobber a genuinely live stream.
    if (!tab || !tab.sessionId || (tab.isStreaming && !force)) return;
    const sid = tab.sessionId;
    try {
      const msgs = await chatService.getSessionMessagesPaginated(sid, INITIAL_MESSAGE_LOAD_LIMIT);
      const tabNow = tabMapRef.current.get(tabId);
      // Re-guard: tab gone, session changed, or streaming restarted during fetch.
      if (!tabNow || tabNow.sessionId !== sid || (tabNow.isStreaming && !force)) return;
      const store = messageStoreRegistry.getOrCreate(tabId);
      // reconcile() is a no-op (queues a thunk) while phase==='streaming'. On the
      // force path the store may still be stuck in 'streaming' (the App-Nap bug),
      // so end it first — the backend is confirmed idle, the turn is over.
      if (force) store.endStreaming();
      store.reconcile(msgs);
      // Bridge store → tabState cache for legacy readers.
      tabNow.messages = store.messages;
      if (tabId === activeTabIdRef.current) setMessages(store.messages);
    } catch (err) {
      // Keep current in-memory messages; the next backend-recovered / reconcile
      // tick retries. Never clear the tab on a failed reconcile.
      console.warn(`[ChatPage] mergeTabFromDb failed for tab ${tabId}:`, err);
    }
  }, [tabMapRef, activeTabIdRef, setMessages]);

  // Auto-resend a swallowed question (option A): a CONNECTION-PHASE send that
  // exhausted its ~7s reconnect budget while the backend was down (e.g. a ~60s
  // daemon redeploy) is flagged with _pendingResendOnRecovery by the streaming
  // error handler. When health flips back, re-issue the SAME stream via the tab's
  // stored retryStreamFn so the question isn't silently lost. Returns true if a
  // resend was started (caller then SKIPS mergeTabFromDb for this tab — the merge
  // would only re-surface DB content, which has no response for a question that
  // never reached the backend). Tab-scoped per isolation Principles 1/3/4/8:
  // retryStreamFn re-resolves the tab's OWN sessionId and reuses the same
  // assistant placeholder id.
  const resendTabOnRecovery = useCallback((tabId: string): boolean => {
    const tab = tabMapRef.current.get(tabId);
    if (!tab || !tab._pendingResendOnRecovery || !tab.retryStreamFn || tab.isStreaming) {
      return false;
    }
    // Clear the flag + bump the attempt count ATOMICALLY before resending so a
    // duplicate 'backend-recovered' event can't double-fire the same resend.
    tab._pendingResendOnRecovery = false;
    tab._pendingResendAttempts = (tab._pendingResendAttempts ?? 0) + 1;
    const asstId = tab._pendingResendAssistantId;
    tab._pendingResendAssistantId = undefined;

    // Strip the error placeholder so the fresh response lands in a clean bubble
    // (retryStreamFn writes streamed tokens to this same assistant id). The store
    // is idle here (onError called endStreaming), so replace() applies immediately.
    if (asstId) {
      const store = messageStoreRegistry.get(tabId);
      if (store) {
        const cleaned = store.messages.map((m) =>
          m.id === asstId ? { ...m, content: [], isError: false } : m,
        );
        store.replace(cleaned);
        tab.messages = store.messages;
        if (tabId === activeTabIdRef.current) setMessages(store.messages);
      }
    }

    // Fresh attempt — reset reconnection bookkeeping so the new stream's error
    // handler treats it as connection-phase again.
    tab.reconnectionAttempt = 0;
    tab.isReconnecting = false;
    tab.hasReceivedData = false;

    // Mark THIS tab streaming (explicit tabId per Principle 1) and re-initiate.
    setIsStreaming(true, tabId);
    updateTabStatus(tabId, 'streaming');
    try {
      const newAbort = tab.retryStreamFn();
      tab.abortController = {
        abort: () => { newAbort(); },
        signal: { aborted: false },
      } as unknown as AbortController;
      console.log(
        `[ChatPage] Backend recovered — auto-resent swallowed question for tab ${tabId} (attempt ${tab._pendingResendAttempts}/${2})`,
      );
      return true;
    } catch (err) {
      console.warn(`[ChatPage] Auto-resend failed for tab ${tabId}:`, err);
      setIsStreaming(false, tabId);
      updateTabStatus(tabId, 'error');
      return false;
    }
  }, [tabMapRef, activeTabIdRef, setMessages, setIsStreaming, updateTabStatus]);

  // Backend recovery: when health transitions disconnected → connected,
  // useHealthMonitor dispatches 'swarm:backend-recovered'. We clear
  // any error state on the active tab and re-sync messages (the last
  // assistant response may be truncated if the backend restarted mid-stream).
  useEffect(() => {
    const handleBackendRecovered = () => {
      const activeId = activeTabIdRef.current;
      if (!activeId) return;
      const tabState = tabMapRef.current.get(activeId);
      if (!tabState) return;
      tabState.reconnectionAttempt = 0;
      tabState.isReconnecting = false;
      // AUTO-RESEND (option A) takes precedence over merge: if this tab's
      // connection-phase send was swallowed during the outage, re-issue it.
      // mergeTabFromDb would only surface DB content, which has no response for
      // a question that never reached the backend.
      if (!resendTabOnRecovery(activeId)) {
        // CONTENT RECONCILE (root-cause fix): on reconnect, the backend may have
        // produced & persisted responses while the SSE stream was detached (it
        // keeps running post-disconnect). Previously we SKIPPED refetch whenever a
        // tab had any in-memory messages → those responses were stuck in the DB and
        // never displayed ("前端好多没 response"). Now:
        //   - empty tab → full load (loadSessionMessages)
        //   - non-empty, non-streaming tab → safe MERGE (mergeTabFromDb / _applyMerge)
        // The merge is boundary-aware, so it does NOT re-surface scrolled-past
        // prior-session history (the bug the old gate guarded against).
        if (tabState.sessionId && !tabState.isStreaming) {
          if (!tabState.messages || tabState.messages.length === 0) {
            loadSessionMessages(tabState.sessionId);
            console.log(`[ChatPage] Backend recovered — active tab ${activeId}, full load (empty state)`);
          } else {
            mergeTabFromDb(activeId);
            console.log(`[ChatPage] Backend recovered — active tab ${activeId}, content reconcile (merge)`);
          }
        }
      }
      // Reconcile EVERY other non-streaming tab from DB too — background tabs also
      // lose responses during a disconnect window.
      for (const [tabId, ts] of tabMapRef.current.entries()) {
        if (tabId === activeId) continue; // handled above
        // Background tab with a swallowed question → resend first.
        if (resendTabOnRecovery(tabId)) continue;
        if (!ts.sessionId || ts.isStreaming) continue;
        if (ts._dbReconcileFailed) {
          // Frozen-PARTIAL tab whose earlier force-clear fetch failed: use the
          // replace-based recovery (drops the stale placeholder, clears the flag).
          reconcileTabFromDb(tabId);
        } else {
          // Otherwise: safe content merge (surfaces disconnect-window responses).
          mergeTabFromDb(tabId);
        }
      }
    };
    window.addEventListener('swarm:backend-recovered', handleBackendRecovered);
    return () => window.removeEventListener('swarm:backend-recovered', handleBackendRecovered);
  }, [loadSessionMessages, reconcileTabFromDb, mergeTabFromDb, resendTabOnRecovery]);

  // Foreground-resume reconcile (OT01 recurrence — App Nap stale store).
  // macOS App Nap throttles requestAnimationFrame AND setTimeout AND suspends
  // SSE reading on a backgrounded Tauri WebView. A tab that streamed while the
  // window was backgrounded can land with its MessageStore stuck behind the
  // backend (which kept running and persisting to the DB) and isStreaming
  // falsely true → the !isStreaming gate makes mergeTabFromDb skip → the tab
  // renders frozen on its last pre-background snapshot (verified: session
  // 98610bf9 — store showed 1 Read, DB had 60+ messages).
  //
  // On foreground we: (1) flush EVERY store (drain App-Nap-throttled rAF/timeout
  // notifications so the React mirror catches up to store content), and (2) for
  // the active tab, ask the backend whether the session is STILL streaming. The
  // backend is the SOLE authority on streaming-ness, so this can NEVER clobber a
  // live stream: only when the backend reports NOT-streaming do we clear the
  // stale flag and force-reconcile the authoritative DB content into the store.
  useEffect(() => {
    const onForeground = async () => {
      if (document.hidden) return;
      // (1) Drain throttled notifications across all tabs (cheap, always safe).
      messageStoreRegistry.flushAll();
      // (2) Active-tab backend-truth reconcile.
      const activeId = activeTabIdRef.current;
      if (!activeId) return;
      const tab = tabMapRef.current.get(activeId);
      if (!tab?.sessionId) return;
      try {
        const state = await chatService.getStreamingState();
        const backendStreaming = state[tab.sessionId]?.streaming === true;
        if (backendStreaming) return; // genuine live stream — flush above suffices, don't touch
        // Backend is idle/done. If the frontend still shows streaming, this is
        // the App-Nap stuck case: clear the stale flag + force-merge DB content.
        const tabNow = tabMapRef.current.get(activeId);
        if (tabNow?.isStreaming) {
          setIsStreaming(false, activeId);
          updateTabStatus(activeId, 'idle');
        }
        await mergeTabFromDb(activeId, true);
        console.log(`[ChatPage] Foreground reconcile — active tab ${activeId} re-synced from DB (backend idle)`);
      } catch {
        // Backend unreachable — leave state as-is; backend-recovered handles it.
      }
    };
    document.addEventListener('visibilitychange', onForeground);
    window.addEventListener('focus', onForeground);
    return () => {
      document.removeEventListener('visibilitychange', onForeground);
      window.removeEventListener('focus', onForeground);
    };
  }, [mergeTabFromDb, setIsStreaming, updateTabStatus]);

  // Load older messages for infinite scroll (paginated)
  const loadOlderMessages = useCallback(async () => {
    if (!sessionId || !hasMoreMessages || isLoadingOlderMessages) return;
    const activeId = activeTabIdRef.current;
    const store = activeId ? messageStoreRegistry.get(activeId) : null;
    // Current displayed messages — the store is authoritative for the active
    // TabView; fall back to the shared mirror only if there is no store.
    const current = store?.messages ?? messagesRef.current;
    const oldestMessage = current[0];
    if (!oldestMessage) return;

    setIsLoadingOlderMessages(true);
    try {
      const olderMessages = await chatService.getSessionMessagesPaginated(
        sessionId, 50, oldestMessage.id
      );
      if (olderMessages.length < 50) setHasMoreMessages(false);
      // Seam merge: if the agent response straddles the page boundary, the
      // last older message and first current message are both assistant —
      // merge them so it renders as one bubble (backend can't merge across fetches).
      const merged = mergeOlderMessages(olderMessages.map(toDisplayMessage), current);
      // Route the prepended history through the active tab's STORE — the
      // keep-mounted TabView renders from its own store, not the shared
      // `messages`. replace() is a no-op during streaming (guarded). Also keep
      // the per-tab cache + shared mirror in sync for non-display consumers.
      if (store && store.phase !== 'streaming') {
        store.replace(merged);
      }
      if (activeId) {
        const tab = tabMapRef.current.get(activeId);
        if (tab) tab.messages = merged;
      }
      setMessages(merged);
    } finally {
      setIsLoadingOlderMessages(false);
    }
  }, [sessionId, hasMoreMessages, isLoadingOlderMessages]);

  // Insert optimistic message(s) through the MessageStore — the single source
  // of truth. initTabState eagerly creates a per-tab store and the store→React
  // sync effect treats it as authoritative; a setMessages-only write gets
  // clobbered by the (empty/stale) snapshot, AND the assistant placeholder
  // never reaches the store, so streaming updateLast() (keyed by message id)
  // silently no-ops every delta. Routing every optimistic insert through the
  // store fixes both. Falls back to React state only when there is no tab id.
  const insertOptimisticMessages = useCallback(
    (tabId: string | null | undefined, msgs: Message[]) => {
      const store = tabId ? messageStoreRegistry.getOrCreate(tabId) : null;
      if (store) {
        store.appendMany(msgs);
      } else {
        setMessages((prev) => [...prev, ...msgs]);
      }
    },
    [setMessages],
  );

  // Handle new chat — clear store first, then React state (subscription will sync)
  const handleNewChat = useCallback(() => {
    const tabId = activeTabIdRef.current;
    if (tabId) {
      const store = messageStoreRegistry.get(tabId);
      if (store) {
        // End streaming phase first so replace() isn't a no-op
        if (store.phase === 'streaming') store.endStreaming();
        store.replace([]);
      }
    }
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
  }, [activeTabIdRef]);


  // Handle new session - creates new tab with "New Session" title (Req 2.2, 2.3)
  // Fix 6: Save current tab state before creating new tab, initialize new tab in per-tab map
  // Fix 7: Guard against exceeding dynamic max tabs limit
  const handleNewSession = useCallback(() => {
    if (!selectedAgentId) return;
    if (tabMapRef.current.size >= maxTabsInfo.chatMax) {
      addToast({ severity: 'info', message: 'Memory usage is high. Close an idle tab or quit other apps to free memory, then try again.', autoDismiss: true });
      return;
    }
    // Save current React state into the active tab's map entry before switching.
    // Same streaming guard as handleTabSelect — don't overwrite authoritative tabMapRef.
    const currentTabId = activeTabIdRef.current;
    if (currentTabId && tabMapRef.current.has(currentTabId)) {
      const currentTab = tabMapRef.current.get(currentTabId)!;
      const isTabStreaming = currentTab.isStreaming;
      updateTabState(currentTabId, {
        ...(!isTabStreaming ? { messages: messagesRef.current, sessionId: sessionIdRef.current } : {}),
        pendingQuestion: null,
        scrollPosition: messagesContainerRef.current?.scrollTop ?? undefined,
      });
    }
    const newTab = addTab(selectedAgentId);
    initTabState(newTab!.id, []);
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
    setPendingPermissionRequestId(null);
    setContextWarning(null);
    setIsStreaming(false, newTab!.id); // New tab is not streaming
    setIsExpanded(false); // New tab always starts in compact mode
  }, [selectedAgentId, addTab, initTabState, tabMapRef, updateTabState, activeTabIdRef, setIsStreaming, setContextWarning, maxTabsInfo.chatMax, addToast]);

  // Handle tab selection - switches active tab and loads session messages (Req 1.6)
  // Fix 6: Save current tab state, restore target tab state from per-tab map
  const handleTabSelect = useCallback(async (tabId: string) => {
    const tab = openTabs.find(t => t.id === tabId);
    if (!tab) return;

    // Overlay-close guard (the "tab-switch BACK_TO_CHAT guard" the comment at the
    // activeOverlay effect names): with the overlay now mounted BELOW the tab strip
    // (2026-08-06 relocate), the tab strip is clickable under an open overlay — so a
    // tab click must first return to chat, else the tab switches invisibly UNDER the
    // scrim. Mirrors the AlertsPill guard. Ref read = no dep-array churn on this path.
    if (activeOverlayRef.current) window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT));

    // Save current React state into the active tab's map entry before switching.
    // IMPORTANT: messages and sessionId are NOT written back — the stream handler
    // updates tabMapRef synchronously (authoritative), while messagesRef lags
    // behind React's async commit cycle. Overwriting would lose recent stream data.
    const currentTabId = activeTabIdRef.current;
    if (currentTabId && tabMapRef.current.has(currentTabId)) {
      const currentTab = tabMapRef.current.get(currentTabId)!;
      // Only write messages/sessionId for IDLE tabs (React state is authoritative).
      // For streaming tabs, tabMapRef is already up-to-date from the stream handler.
      const isTabStreaming = currentTab.isStreaming;
      updateTabState(currentTabId, {
        ...(!isTabStreaming ? { messages: messagesRef.current, sessionId: sessionIdRef.current } : {}),
        pendingQuestion: pendingQuestion,
        pendingPermissionRequestId: pendingPermissionRequestId,
        isExpanded: isExpandedRef.current,
        scrollPosition: messagesContainerRef.current?.scrollTop ?? undefined,
      });
      inputValueMapRef.current.set(currentTabId, inputValueRef.current);
    }
    
    selectTab(tabId);
    
    // Try to restore from per-tab map first (authoritative)
    const restored = restoreTab(tabId);
    if (restored) {
      // Restore React state from the unified tab map
      const tabState = getTabState(tabId);
      if (tabState) {
        // If the tab has a sessionId but empty messages (e.g. after app restart,
        // hydrateTab sets messages=[]), load messages from the backend API
        // instead of displaying the empty array.
        // GUARD: Skip API reload for streaming tabs — their messages are being
        // accumulated in tabMapRef by the stream handler. Reloading would
        // overwrite in-flight content with stale DB data.
        if (tabState.sessionId && tabState.messages.length === 0 && !tabState.isStreaming) {
          setSessionId(tabState.sessionId);
          // Root 3 / 3A: restore the tab's own pending question (not null) — a
          // hydrated background tab that received an AskUserQuestion must stay
          // answerable on switch-back, not be silently cleared.
          setPendingQuestion(tabState.pendingQuestion ?? null);
          setContextWarning(tabState.contextWarning ?? null);
          setPromptMetadata(tabState.promptMetadata ?? null);
          setIsExpanded(tabState.isExpanded ?? false);
          setInputValue(inputValueMapRef.current.get(tabId) ?? '');
          bumpStreamingDerivation();
          setPendingPermissionRequestId(null);
          if (tabStatuses[tabId] === 'complete_unread') {
            updateTabStatus(tabId, 'idle');
          }
          loadSessionMessages(tabState.sessionId);
          return;
        }
        // Guard 1: Suppress auto-scroll during tab switch — prevents the
        // [messages] effect from calling scrollToBottom() before the
        // useLayoutEffect scroll restore fires.
        userScrolledUpRef.current = true;

        // Defensive store seed — POPULATE an empty store, never CLOBBER a
        // populated one. This was previously an UNCONDITIONAL
        // `store.replace(tabState.messages)` — the reverse-flow half of the
        // reconcile-gap split-brain: it overwrote a store that already held
        // authoritative (streamed/loaded) content with a staler tabState.messages
        // mirror, truncating the visible reply. The empty-guard inverts the
        // semantics: the store is the authority.
        //
        // NOTE (verified run_9db9f987): the cold-restore path does NOT reach here
        // — a hydrated tab has messages=[] (hydrateTab) and returns early at the
        // `messages.length===0` branch above via loadSessionMessages (which seeds
        // the store at line ~417). And tab-close destroys store + tabState
        // together (ChatPage:823 / useUnifiedTabState:685-687), so a populated
        // tabState never coexists with an empty store mid-session. This guard is
        // therefore a near-dead belt-and-suspenders for the rare case where a
        // store was emptied but its tabState retained content — kept (not deleted)
        // so store-only render can never blank such a tab. Synchronous
        // length-check + replace (no await between) → no TOCTOU. Streaming skipped
        // (store authoritative; replace() is a phase-gated no-op anyway).
        if (!tabState.isStreaming) {
          const switchStore = messageStoreRegistry.getOrCreate(tabId);
          if (switchStore.messages.length === 0 && tabState.messages.length > 0) {
            switchStore.replace(tabState.messages);
          }
        }
        setMessages(tabState.messages);
        setSessionId(tabState.sessionId);
        setPendingQuestion(tabState.pendingQuestion);
        setPendingPermissionRequestId(tabState.pendingPermissionRequestId ?? null);
        setContextWarning(tabState.contextWarning ?? null);
        setPromptMetadata(tabState.promptMetadata ?? null);
        setIsExpanded(tabState.isExpanded ?? false);
        setInputValue(inputValueMapRef.current.get(tabId) ?? '');
        // Reset hasMoreMessages based on restored message count — the tab
        // was loaded with INITIAL_MESSAGE_LOAD_LIMIT, so if we have that many
        // messages there are likely more on the server.
        setHasMoreMessages(tabState.messages.length >= INITIAL_MESSAGE_LOAD_LIMIT);
        // isStreaming derivation automatically reflects target tab's state
        // from tabMapRef — no need to call setIsStreaming which would corrupt
        // the source tab's streaming state. Just bump to re-derive.
        bumpStreamingDerivation();

        // Mark this switch as handled — prevents the sync-active-tab effect
        // from redundantly calling setMessages with the same data (extra render).
        tabSelectHandledRef.current = true;

        // Guard 2: Schedule scroll restore via useLayoutEffect (fires
        // synchronously after React commits DOM changes but BEFORE browser
        // paint — eliminates the 2-3 frame visual flash from double-rAF).
        pendingScrollRestoreRef.current = {
          tabId,
          scrollPosition: tabState.scrollPosition,
        };
      }
      // Restore per-tab pending permission state from tabMapRef
      const targetTabState = getTabState(tabId);
      setPendingPermissionRequestId(targetTabState?.pendingPermissionRequestId ?? null);
      // Fix 8: Clear unread indicator when switching to a tab with 'complete_unread' status
      if (tabStatuses[tabId] === 'complete_unread') {
        updateTabStatus(tabId, 'idle');
      }
      return;
    }
    
    // Not in map — load from API or initialize fresh
    activeTabIdRef.current = tabId;
    setPendingPermissionRequestId(null);
    setContextWarning(null);
    bumpStreamingDerivation(); // re-derive isStreaming for new active tab
    if (tab.sessionId) {
      // New tab with existing session — load from API with async guard
      const loadedTabId = tabId; // capture for closure
      setIsLoadingHistory(true);
      try {
        const sessionMessages = await chatService.getSessionMessages(tab.sessionId);
        // Async guard: only apply if user hasn't switched away during the load
        if (activeTabIdRef.current !== loadedTabId) return;
        // Phase guard: streaming may have started during the async fetch
        const tabAfterFetch = tabMapRef.current.get(loadedTabId);
        if (tabAfterFetch?.isStreaming) return;
        const formattedMessages: Message[] = sessionMessages.map(toDisplayMessage);
        setMessages(formattedMessages);
        setSessionId(tab.sessionId);
        // Initialize the tab in the per-tab map now that we have data
        initTabState(loadedTabId, formattedMessages);
        updateTabState(loadedTabId, { sessionId: tab.sessionId });
      } catch (error) {
        console.error('Failed to load session messages:', error);
      } finally {
        if (activeTabIdRef.current === loadedTabId) {
          setIsLoadingHistory(false);
        }
      }
      setPendingQuestion(null);
    } else {
      // Brand new tab — initialize empty
      setMessages([]);
      setSessionId(undefined);
      setPendingQuestion(null);
      initTabState(tabId, []);
    }
  }, [openTabs, selectTab, restoreTab, getTabState, initTabState, updateTabState, activeTabIdRef, tabMapRef, tabStatuses, updateTabStatus, pendingQuestion, setContextWarning]);

  // R6b: shared close+release routine. Runs the FULL frontend cleanup, closes
  // the tab, then fires a best-effort backend slot release. Both the idle path
  // and the streaming-confirm path call this — keeping them identical prevents
  // dropping a cleanup step (Gate-1 finding #4). `force` is true only when the
  // user confirmed closing a STREAMING tab (server routes that through the
  // generation-safe interrupt path).
  const doCloseTab = useCallback((tabId: string, sessionId: string | undefined, force: boolean) => {
    // Clean up pendingStreamTabs entry for this tab (prevents stale entries)
    clearPendingStreamTab(tabId);
    // Clean up per-tab draft text to prevent unbounded memory growth
    inputValueMapRef.current.delete(tabId);
    // Clean up any unfilled ToDo dispatch records for this tab (Gate-2 MEDIUM #9:
    // a closed tab's session never materializes → its record would leak forever).
    dispatchPendingRef.current = dispatchPendingRef.current.filter((p) => p.tabId !== tabId);

    // Destroy MessageStore for this tab (cleanup timers, listeners, prevent leak)
    messageStoreRegistry.destroy(tabId);

    // GUI28: clear any persistent recovery_exhausted toast for this tab — else
    // it outlives the tab and its "Start fresh" action would clear the WRONG
    // (now-active) tab (adversarial #3/#4, run_d8dce02a).
    removeToast(`recovery-exhausted-${tabId}`);

    // Let closeTab handle map deletion + auto-create of last tab.
    // closeTab also aborts the tab's in-flight stream (abortController.abort),
    // which on the backend triggers _recover_streaming_on_disconnect
    // (STREAMING→IDLE). The release below is ordered AFTER closeTab so it acts
    // on a settling/idle unit, not racing a live stream (Gate-1 finding #5a).
    // Do NOT call cleanupTabState before closeTab — it deletes the tab
    // from the map, causing closeTab to early-return and skip the
    // "auto-create new tab when last one is closed" logic.
    closeTab(tabId);

    // Fire-and-forget backend slot release (R6b). Frees the concurrency slot
    // without deleting messages. Guarded on sessionId — a brand-new tab has
    // sessionId === undefined and nothing to release.
    if (sessionId) {
      chatService.releaseSession(sessionId, force).catch((err) => {
        console.warn('[handleTabClose] Failed to release backend session:', err);
      });
    }

    // If closing the last tab, closeTab auto-creates a fresh one.
    // Reset React state so the welcome screen shows instead of stale messages.
    const newActiveId = activeTabIdRef.current;
    if (newActiveId && newActiveId !== tabId) {
      const newTab = tabMapRef.current.get(newActiveId);
      if (newTab && !newTab.sessionId && newTab.messages.length === 0) {
        setMessages([]);
        setSessionId(undefined);
        setPendingQuestion(null);
        setContextWarning(null);
        setPendingPermissionRequestId(null);
        setIsExpanded(false);
      }
    }
  }, [closeTab, clearPendingStreamTab, tabMapRef, activeTabIdRef, setMessages, setSessionId, setPendingQuestion, setContextWarning, setPendingPermissionRequestId, setIsExpanded, removeToast]);

  // Handle tab close - removes tab, handles last-tab case (Req 3.3)
  // R6b: ANY close releases the backend slot. Idle tab → release silently.
  // Streaming tab → confirm first (avoid silently discarding a live response).
  const handleTabClose = useCallback((tabId: string) => {
    const tab = tabMapRef.current.get(tabId);
    const tabSessionId = tab?.sessionId;
    const isStreaming = tab?.isStreaming || pendingStreamTabs.has(tabId);

    if (isStreaming) {
      // Defer close until the user confirms (handled by the ConfirmDialog).
      setCloseConfirmTab({ tabId, sessionId: tabSessionId });
      return;
    }

    doCloseTab(tabId, tabSessionId, false);
  }, [doCloseTab, pendingStreamTabs, tabMapRef]);

  // R6b: user confirmed closing a streaming tab. Re-read live state — the stream
  // may have completed while the dialog was open (Gate-1 finding #2c), so only
  // pass force=true if it's still streaming; the server's interrupt path is
  // generation-safe either way.
  const confirmCloseStreamingTab = useCallback(() => {
    const pending = closeConfirmTab;
    setCloseConfirmTab(null);
    if (!pending) return;
    const tab = tabMapRef.current.get(pending.tabId);
    const stillStreaming = (tab?.isStreaming || pendingStreamTabs.has(pending.tabId)) ?? false;
    doCloseTab(pending.tabId, pending.sessionId, stillStreaming);
  }, [closeConfirmTab, doCloseTab, pendingStreamTabs, tabMapRef]);

  // ToDo Dispatch ①→② (A2, run_5088b841). APPEND-ONLY record of dispatches whose
  // target tab has no session_id yet — backfilled at the tab's first send (the
  // :1542 sessionId→tabMap effect). Append-only (NOT a mutable map) so two
  // dispatches to the same tab before send can't silently overwrite each other
  // (Gate-1 HIGH). Each entry filled exactly once (filled flag).

  // Shared landing helpers (run_754fe3e8): the 3 handlers below used to each
  // re-implement build-tabs → resolveResumeTarget → toast → draft-guard. That
  // prefix now lives in the PURE classifyLanding (pages/chat/landPromptInTab.ts);
  // this thin wrapper turns a `blocked` verdict into the ONE toast per reason
  // (streaming gets its own text), parametrized only by the action verb.
  const tabsSnapshot = useCallback((): ResumeTabInfo[] =>
    Array.from(tabMapRef.current.values()).map((t) => ({
      id: t.id, sessionId: t.sessionId, status: t.status, isStreaming: t.isStreaming,
    })), [tabMapRef]);

  const landingToast = useCallback((v: Extract<LandingVerdict, { kind: 'blocked' }>, verb: string): void => {
    let message: string;
    if (v.reason === 'draft') {
      message = `This tab has an unsent draft — send or clear it, or open a new tab, then ${verb}.`;
    } else if (v.reason === 'occupied') {
      // Dispatch convergence: at cap and the only idle tab holds a conversation we
      // won't wipe. User must free a slot rather than lose that history. For a
      // single-tab user (chatMax===1) "close a tab" sounds like a dead end — tell
      // them closing their only tab is safe (closeTab auto-respawns an empty one).
      message = maxTabsInfo.chatMax === 1
        ? `This tab holds a conversation — closing it starts a fresh one (a new empty tab opens automatically). Close it, then ${verb}.`
        : `All ${maxTabsInfo.chatMax} tab(s) are busy — close a tab, then ${verb}.`;
    } else if (v.reason === 'busy') {
      // Never leak the raw machine status token (e.g. "permission_needed") to the
      // user. Map to a human phrase + the right action. A completed/errored tab is
      // NOT "busy" and cannot be "finished" — it just needs closing/replacing.
      switch (v.busyStatus) {
        case 'streaming':
          message = `The current tab is still streaming a response — wait for it to finish (or close a tab), then ${verb}.`;
          break;
        case 'waiting_input':
          message = `The current tab is waiting for your input — answer or close it (or open a new tab), then ${verb}.`;
          break;
        case 'permission_needed':
          message = `The current tab is waiting for a permission decision — resolve or close it (or open a new tab), then ${verb}.`;
          break;
        case 'complete_unread':
          message = `The current tab has an unread response — open a new tab (or close this one), then ${verb}.`;
          break;
        case 'error':
          message = `The current tab is in an error state — close it (or open a new tab), then ${verb}.`;
          break;
        default:
          message = `The current tab is busy — finish or close it (or open a new tab), then ${verb}.`;
      }
    } else { // cap
      message = `All ${maxTabsInfo.chatMax} tab(s) are busy — close a tab (or wait for one to finish), then ${verb}.`;
    }
    addToast({ severity: 'warning', message, autoDismiss: true });
  }, [addToast, maxTabsInfo.chatMax]);

  const activeTabHasDraft = useCallback((): boolean =>
    inputValueRef.current.trim().length > 0 || attachmentsRef.current.length > 0,
    [inputValueRef, attachmentsRef]);

  // Dispatch a todo into a chat tab: land via the shared resolver (newtab /
  // reuse-current / needs-close — never focus, a todo is new work), inject its
  // text (not auto-sent), write the dead snapshot, and (when the tab already has
  // a session) set dispatched_session_id now; else defer to first-send backfill.
  // Returns true if it landed (caller auto-closes the overlay), false on needs-close.
  // Execute a fresh-work landing verdict (dispatch = new work, so mode is only ever
  // reuse or newtab — focus is impossible for a sentinel key). Seeds the target tab
  // empty and returns its id, or null if a newtab race lost the last slot. Shared by
  // the two dispatch handlers (todo + jobprompt); resume does NOT use this (its
  // body loads a session, not an empty tab).
  const setupDispatchTarget = useCallback(
    (verdict: Extract<LandingVerdict, { kind: 'land' }>): string | null => {
      if (verdict.mode === 'reuse') {
        const targetTabId = verdict.tabId;
        initTabState(targetTabId, []);
        updateTabState(targetTabId, { sessionId: undefined });
        setSessionId(undefined);
        setMessages([]);
        return targetTabId;
      }
      // newtab
      const newTab = addTab(selectedAgentId || 'default');
      if (!newTab) {
        addToast({ severity: 'warning', message: 'Could not open a new tab — all tabs are busy.', autoDismiss: true });
        return null;
      }
      initTabState(newTab.id, []);
      return newTab.id;
    },
    [initTabState, updateTabState, setSessionId, setMessages, addTab, selectedAgentId, addToast],
  );

  const handleDispatchTodo = useCallback((todo: ToDo): boolean => {
    // Sentinel key never matches an open session → focus branch unreachable (a todo
    // is new work). Dispatch opts INTO the draft guard (reuse would clobber a draft).
    const verdict = classifyLanding(
      `__dispatch__${todo.id}`, tabsSnapshot(), maxTabsInfo.chatMax, activeTabIdRef.current ?? undefined,
      { hasDraft: activeTabHasDraft(), applyDraftGuard: true, allowReuseCurrent: false },
    );
    if (verdict.kind === 'blocked') { landingToast(verdict, 'Dispatch'); return false; }

    const targetTabId = setupDispatchTarget(verdict);
    if (targetTabId == null) return false;

    const tabIdx = openTabsRef.current.findIndex((t) => t.id === targetTabId);
    const tabLabel = `Tab ${tabIdx >= 0 ? tabIdx + 1 : openTabsRef.current.length}`;
    const existingSid = tabMapRef.current.get(targetTabId)?.sessionId;
    // Build the FULL work packet as the injected prompt (B6 fix, run_937f5cc9):
    // dispatching used to inject only todo.title, silently dropping the files /
    // design docs / next step / acceptance the drawer promised — the user reviewed
    // a rich packet and got a one-line title. Now we assemble the whole packet so
    // the agent lands with all context. Falls back to just the title when there's
    // no linked context (parity with old behavior for a bare todo).
    dispatchInjectChatInput({ text: buildDispatchPrompt(todo), focus: true, autoSend: false });
    // Snapshot: tab_label + timestamp now; session_id now if the tab already has
    // one (reuse-current of a tab that previously sent), else backfill at send.
    void todosService.dispatch(todo.id, tabLabel, existingSid).catch(() => {/* non-fatal */});
    if (!existingSid) {
      // Drop any prior unsent dispatch record for this tab (superseded — newest
      // wins), then record this one. Bounds growth + keeps newest-wins semantics.
      dispatchPendingRef.current = dispatchPendingRef.current.filter((p) => p.tabId !== targetTabId);
      dispatchPendingRef.current.push({ tabId: targetTabId, todoId: todo.id, tabLabel });
    }
    return true;
  }, [tabsSnapshot, maxTabsInfo.chatMax, activeTabIdRef, activeTabHasDraft, landingToast, setupDispatchTarget]);

  // Land an arbitrary prompt into a chat tab (Jobs & Runs overlay: create/pause/
  // edit/delete → chat, since s_job-manager owns user-jobs.yaml integrity).
  // Mirrors handleDispatchTodo's tab-landing (Gate-1 #7): a bare window
  // swarm:inject-chat-input no-ops with no active chat tab — the ChatInput
  // listener only sets the ACTIVE tab's input, it doesn't create/switch tabs.
  // So we resolve+activate a tab FIRST, THEN inject. Returns true if it landed.
  const handleDispatchJobPrompt = useCallback((prompt: string): boolean => {
    // Same fresh-work landing as todo (sentinel key, opt into the draft guard).
    const verdict = classifyLanding(
      `__jobprompt__${prompt.slice(0, 24)}`, tabsSnapshot(), maxTabsInfo.chatMax, activeTabIdRef.current ?? undefined,
      { hasDraft: activeTabHasDraft(), applyDraftGuard: true, allowReuseCurrent: false },
    );
    if (verdict.kind === 'blocked') { landingToast(verdict, 'Dispatch'); return false; }

    if (setupDispatchTarget(verdict) == null) return false;
    // Tab is now active — inject the prompt (not auto-sent; user reviews + sends).
    dispatchInjectChatInput({ text: prompt, focus: true, autoSend: false });
    return true;
  }, [tabsSnapshot, maxTabsInfo.chatMax, activeTabIdRef, activeTabHasDraft, landingToast, setupDispatchTarget]);

  // Resume a session from the History overlay into a chat tab (方案 B).
  // Pure decision (resolveResumeTarget) + real execution via handleTabSelect —
  // agent switch + message load happen ONLY after we hold the target tab, so a
  // streaming tab is never left in the old half-state. Returns whether it
  // landed; the overlay closes only on true (all-busy → toast + stay open).
  const handleResumeSession = useCallback((session: ChatSession): boolean => {
    // Resume passes the REAL session id (can hit focus) and does NOT opt into the
    // draft guard — it never had one, and adding it would change behavior (Gate-1).
    const verdict = classifyLanding(
      session.id, tabsSnapshot(), maxTabsInfo.chatMax, activeTabIdRef.current ?? undefined,
      { hasDraft: false, applyDraftGuard: false, allowReuseCurrent: true },
    );
    if (verdict.kind === 'blocked') { landingToast(verdict, 'Resume'); return false; }

    // focus: the session is already open with its messages loaded — switching
    // to it goes through handleTabSelect (it IS in the captured openTabs).
    if (verdict.mode === 'focus') {
      void handleTabSelect(verdict.tabId);
      return true;
    }
    const decision = verdict; // mode: 'reuse' | 'newtab' — its own load-session body below

    // reuse / newtab: agent switch happens BEFORE we activate, but AFTER the
    // decision — never leaving a streaming tab half-loaded (the old bug). We do
    // NOT call handleTabSelect here: it reads a `useMemo(openTabs)` snapshot that
    // does not yet contain a just-added tab (bump only schedules a re-render), so
    // it would silently no-op. Instead we mirror handleNewSession: save the
    // outgoing tab, seed the target's session + empty store via initTabState,
    // selectTab (tabMapRef-based, not openTabs), and let the sync-active-tab
    // effect load the session (it fires on activeTabId change for a tab with a
    // sessionId + empty messages).
    if (session.agentId) setSelectedAgentId(session.agentId);

    // Save current React state into the outgoing active tab (skip messages for a
    // streaming tab — tabMapRef is authoritative there).
    const currentTabId = activeTabIdRef.current;
    if (currentTabId && tabMapRef.current.has(currentTabId)) {
      const currentTab = tabMapRef.current.get(currentTabId)!;
      updateTabState(currentTabId, {
        ...(!currentTab.isStreaming ? { messages: messagesRef.current, sessionId: sessionIdRef.current } : {}),
        pendingQuestion,
        scrollPosition: messagesContainerRef.current?.scrollTop ?? undefined,
      });
    }

    let targetTabId: string;
    if (decision.mode === 'reuse') {
      // The active idle tab, cleared + reused (the only reuse we allow — never a
      // background idle tab with a different session; A2 Gate-1 CRITICAL fix).
      targetTabId = decision.tabId;
    } else {
      const newTab = addTab(session.agentId || selectedAgentId || 'default');
      if (!newTab) {
        // Race: cap reached between decision and addTab → treat as busy.
        addToast({ severity: 'warning', message: 'Could not open a new tab — all tabs are busy.', autoDismiss: true });
        return false;
      }
      targetTabId = newTab.id;
    }

    // Seed the target tab: empty its messages via initTabState (which also
    // clears the tab's MessageStore — a raw `tab.messages = []` would leave the
    // reused tab's store showing the PREVIOUS session until the load resolves).
    initTabState(targetTabId, []);
    updateTabSessionId(targetTabId, session.id);

    // Load the target tab. When targetTabId is already the active tab,
    // selectTab(sameId) is a React no-op → activeTabId never changes → the
    // sync-active-tab effect early-returns (prevActiveTabIdRef===activeTabId) →
    // the session would never load (blank tab). So load DIRECTLY in that case.
    //
    // This direct-load branch is hit by BOTH: (a) reuse into the active idle tab
    // (the ordinary case when chatMax===1), AND (b) newtab — because addTab()
    // sets activeTabIdRef SYNCHRONOUSLY, so a freshly-added tab is already the
    // active tab by the time we get here. The selectTab() branch below is
    // therefore reached ONLY by reuse into a NON-active idle tab (chatMax>1).
    // Do NOT "simplify" this to always-selectTab: that reintroduces the no-op.
    if (targetTabId === activeTabIdRef.current) {
      setSessionId(session.id);
      setMessages([]);
      setPendingQuestion(null);
      void loadSessionMessages(session.id);
    } else {
      selectTab(targetTabId); // activeTabId change → sync effect loads the session
    }
    return true;
  }, [tabsSnapshot, tabMapRef, maxTabsInfo.chatMax, landingToast, addToast, handleTabSelect, updateTabSessionId,
      addTab, initTabState, selectTab, updateTabState, activeTabIdRef, messagesRef, sessionIdRef, pendingQuestion,
      selectedAgentId, loadSessionMessages]);

  // Publish ChatPage's tab-owned closures + agent scope to the OverlayHost bridge
  // (decision B): dispatch/resume/delete are ChatPage responsibilities (it owns the
  // tab store); host-rendered surfaces (NewBrain, History, the 4 workbench) reach them
  // via ctx. A ref-backed bridge (not context) so this does not re-render the host on
  // ChatPage updates. History self-fetches its reactive session DATA off `agentId`.
  useEffect(() => {
    setOverlayCtxBridge({
      dispatchPrompt: handleDispatchJobPrompt,
      dispatchTodo: (todo) => handleDispatchTodo(todo as ToDo),
      resumeSession: (s) => handleResumeSession(s as ChatSession),
      deleteSession: (s) => setDeleteConfirmSession(s as ChatSession),
    });
  }, [handleDispatchJobPrompt, handleDispatchTodo, handleResumeSession]);

  // agentId is published REACTIVELY through OverlayContext (not the non-reactive ref
  // bridge): an OPEN History overlay must re-render its ['chatSessions', agentId] query
  // when the agent switches while open (e.g. the delete-agent fallback effect fires with
  // History open — Gate-2 MED, run_06c49540). The context re-renders OverlayHost; the ref
  // bridge (host reads at render time) would leave it stale.
  useEffect(() => {
    setOverlayAgentId(selectedAgentId);
  }, [selectedAgentId, setOverlayAgentId]);

  // Handle delete session
  const handleDeleteSession = async (session: ChatSession) => {
    try {
      await chatService.deleteSession(session.id);
      refetchSessions();
      if (sessionId === session.id) {
        handleNewChat();
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
    setDeleteConfirmSession(null);
  };

  // Scroll to bottom on new messages — conditional on user scroll position (Fix 2)
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  // (chatAreaBounds/observeChatArea removed M5 2026-08-04: the fullscreen card-detail
  // Modal that consumed the measured chat-area rect is retired — the OverlayHost bounds
  // itself via `absolute inset:0` of the in-flow chat area, no viewport measurement.)
  const prevScrollHeightRef = useRef(0);
  /** Pending scroll restore from tab switch — consumed by useLayoutEffect. */
  const pendingScrollRestoreRef = useRef<{ tabId: string; scrollPosition?: number } | null>(null);
  /** Flag to skip redundant sync-active-tab effect after handleTabSelect. */
  const tabSelectHandledRef = useRef(false);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    // Only auto-scroll if user hasn't scrolled up (Fix 2)
    if (!userScrolledUpRef.current) {
      scrollToBottom();
    }
  }, [messages]);

  // Scroll position preservation when prepending older messages
  useLayoutEffect(() => {
    if (!prevScrollHeightRef.current) return;
    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight - prevScrollHeightRef.current;
      prevScrollHeightRef.current = 0;
    }
  }, [messages]);

  // Tab-switch scroll landing — fires synchronously after React commits the new
  // messages to the DOM but BEFORE the browser paints (eliminates the 2-3 frame
  // flash where messages render at scrollTop=0 then jump).
  //
  // UX decision (2026-08-03, user choice A): EVERY tab switch lands at the BOTTOM
  // (latest message), NOT the previously-saved scroll position. Restoring the old
  // position forced the user to scroll down every time they returned to chat
  // (e.g. after a Canvas detour) — a real friction point. Landing at bottom
  // matches the chat mental model: coming back to a conversation shows the newest
  // message. The saved scrollPosition is intentionally ignored here (kept in
  // tabState only for potential future "resume where you were" affordance).
  useLayoutEffect(() => {
    const pending = pendingScrollRestoreRef.current;
    if (!pending) return;
    // Only consume if this render is for the correct tab
    if (activeTabIdRef.current !== pending.tabId) return;
    pendingScrollRestoreRef.current = null;

    const container = messagesContainerRef.current;
    if (!container) return;

    // Always land at the bottom — newest message visible, no manual scroll needed.
    container.scrollTop = container.scrollHeight;
    // At bottom → not scrolled up, so auto-scroll on new messages stays enabled.
    userScrolledUpRef.current = false;
  }, [messages]);

  // Time-to-interactive logging (dev only)
  useEffect(() => {
    if (messagesReady && mountTimeRef.current) {
      if (import.meta.env.DEV) {
        console.log(`[ChatPage] Time to interactive: ${(performance.now() - mountTimeRef.current).toFixed(0)}ms`);
      }
      mountTimeRef.current = 0; // Only log once
    }
  }, [messagesReady]);

  // Scroll to bottom after tab restore completes.
  // The normal scroll effect ([messages]) may fire before the messages container
  // is rendered (it's gated behind messagesReady). This effect ensures a
  // reliable scroll-to-bottom once the DOM is fully laid out after restore.
  useEffect(() => {
    if (messagesReady && messages.length > 0) {
      // Double-rAF: first rAF runs after React commit, second runs after
      // the browser has painted the new DOM, guaranteeing scroll targets exist.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
        });
      });
    }
    // Only fire once when messagesReady transitions to true — not on every message change
     
  }, [messagesReady]);

  // Register the initial/default tab in the per-tab state map on mount.
  // Without this, the first tab has no entry in tabMapRef and all
  // per-tab features (message tracking, abort isolation, status) are broken.
  useEffect(() => {
    if (activeTabId && !tabMapRef.current.has(activeTabId)) {
      initTabState(activeTabId, messages.length > 0 ? messages : []);
    }
  }, [activeTabId]);

  /**
   * File-based tab restore: load tab state from ~/.swarm-ai/open_tabs.json
   * via the backend API. Replaces the old localStorage/DB fallback approach.
   *
   * On success, the exact tabs the user had open are restored with their
   * sessionIds. The sync-active-tab effect then loads messages from the DB.
   *
   * On failure (file missing = fresh install), keeps the default tab.
   * Retries up to 3 times with 500ms delay if the backend isn't ready yet.
   */
  useEffect(() => {
    let mounted = true;

    const doRestore = async () => {
      setIsLoadingHistory(true);

      // Retry loop: backend daemon may not be ready on first mount.
      // Try up to 5 times with 500ms delay between attempts.
      let restored = false;
      for (let attempt = 0; attempt < 5 && !restored && mounted; attempt++) {
        if (attempt > 0) {
          await new Promise(r => setTimeout(r, 500));
          if (!mounted) return;
        }
        try {
          restored = await restoreFromFile();
        } catch (err) {
          console.warn(`[ChatPage] Tab restore attempt ${attempt + 1} failed:`, err);
        }
      }

      if (!mounted) return;

      if (restored) {
        console.log('[ChatPage] Tabs restored from open_tabs.json');
        const activeId = activeTabIdRef.current;
        const activeState = activeId ? tabMapRef.current.get(activeId) : null;
        if (activeState?.sessionId) {
          try {
            await loadSessionMessages(activeState.sessionId);
            // Task 3 fix: force a visible repaint after messages load.
            // React 18 batching may hold the render until interaction;
            // breaking out via setTimeout ensures the paint fires immediately.
            if (mounted) {
              setTimeout(() => bumpStreamingDerivation(), 0);
            }
          } catch {
            // Session may no longer exist — reset to fresh tab
            if (mounted) {
              setMessages([]);
              setSessionId(undefined);
              setIsLoadingHistory(false);
              setMessagesReady(true);
            }
          }
        } else {
          // No session to load — show welcome screen
          if (mounted) {
            setMessages([]);
            setSessionId(undefined);
            setIsLoadingHistory(false);
            setMessagesReady(true);
          }
        }

        // Task 2 fix: preload messages for non-active restored tabs.
        // Routes through MessageStore.reconcile() which respects streaming
        // phase gate — if a tab starts streaming before preload resolves,
        // reconcile queues the data for post-stream flush instead of overwriting.
        if (mounted) {
          const preloadPromises: Promise<void>[] = [];
          for (const [tabId, tab] of tabMapRef.current.entries()) {
            if (tabId === activeId) continue;  // already loaded above
            if (!tab.sessionId) continue;       // new tab, no session
            if (tab.messages.length > 0) continue; // already has messages
            const sid = tab.sessionId;
            preloadPromises.push(
              chatService.getSessionMessagesPaginated(sid, INITIAL_MESSAGE_LOAD_LIMIT)
                .then(msgs => {
                  if (!mounted) return;
                  const tabRef = tabMapRef.current.get(tabId);
                  if (tabRef && tabRef.sessionId === sid) {
                    // Phase-gated via store: reconcile() is NO-OP during streaming,
                    // queues a thunk for execution on endStreaming().
                    const store = messageStoreRegistry.getOrCreate(tabId, { sessionId: sid });
                    store.reconcile(msgs);
                    // Only sync back if reconcile actually executed (not queued).
                    // During streaming, store.messages may not reflect the reconcile
                    // result — the deferred thunk will update after endStreaming().
                    if (store.phase === 'idle') {
                      tabRef.messages = store.messages;
                    }
                  }
                })
                .catch(err => {
                  console.warn(`[ChatPage] Background preload failed for tab ${tabId}:`, err);
                })
            );
          }
          // Fire all in parallel — don't block the UI
          if (preloadPromises.length > 0) {
            Promise.all(preloadPromises).then(() => {
              console.log(`[ChatPage] Background preloaded ${preloadPromises.length} tab(s)`);
            });
          }
        }
      } else {
        console.log('[ChatPage] No saved tabs found, using default tab');
        if (mounted) {
          setIsLoadingHistory(false);
          setMessagesReady(true);
        }
      }
    };

    doRestore();
    return () => { mounted = false; };
  }, []);

  // ── Dynamic tab limit polling (Req 5.1, 6.4) ────────────────────────
  // Fetch max tabs on mount and poll every 30 seconds for memory pressure.
  // The fetched value updates maxTabsInfo (used for "+" button disabled state
  // and memory pressure indicator). Polling stops on unmount.
  useEffect(() => {
    // Initial fetch on mount
    fetchMaxTabs();

    const interval = setInterval(() => {
      fetchMaxTabs();
    }, 30_000);

    return () => clearInterval(interval);
  }, [fetchMaxTabs]);

  // Initialize with default agent — validate the selected agent exists in the DB.
  // Since selectedAgentId defaults to 'default' (the built-in SwarmAgent),
  // this effect only needs to handle the edge case where the agent was deleted.
  useEffect(() => {
    if (!selectedAgentId) return;
    // If agents list is loaded and our selected agent exists, nothing to do
    if (agents.length > 0) {
      const existingAgent = agents.find((a) => a.id === selectedAgentId);
      if (existingAgent) return;
    }
    // Agents not loaded yet or selected agent not found — fetch default
    setAgentLoadError(null);
    agentsService.getDefault().then(defaultAgent => {
      if (defaultAgent.id !== selectedAgentId) {
        setSelectedAgentId(defaultAgent.id);
      }
    }).catch(error => {
      console.error('Failed to fetch default agent:', error);
      setAgentLoadError(t('chat.defaultAgentError', 'Failed to load the default agent. Please restart the application or check the backend service.'));
    });
  }, [agents, selectedAgentId, t]);

  // Clear agentId URL parameter
  useEffect(() => {
    if (searchParams.get('agentId')) {
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  // Load task session
  useEffect(() => {
    if (task) {
      if (task.agentId && task.agentId !== selectedAgentId) {
        setSelectedAgentId(task.agentId);
      }
      if (task.sessionId && task.sessionId !== sessionId) {
        loadSessionMessages(task.sessionId);
      }
    }
  }, [task, selectedAgentId, sessionId, loadSessionMessages]);

  // Refetch sessions when conversation completes
  useEffect(() => {
    if (sessionId && !isStreaming) {
      refetchSessions();
    }
  }, [sessionId, isStreaming, refetchSessions]);

  // Fire a persistent error toast when context warning reaches critical level.
  // Replaces the old declarative <Toast> JSX that was rendered inline.
  useEffect(() => {
    if (contextWarning && contextWarning.level === 'critical') {
      addToast({
        severity: 'error',
        message: contextWarning.message,
        autoDismiss: false,
        id: 'context-warning-critical',
      });
    }
  }, [contextWarning, addToast]);

  // Push session metadata to TopBar via LayoutContext (pure reads, no new API calls).
  // Use primitive deps only to avoid re-firing on every render (selectedAgent is
  // a new object ref each time). The cleanup sets null on unmount so the TopBar
  // falls back to the "SwarmAI" placeholder when ChatPage is not mounted.
  // Note: React runs old-cleanup → new-effect synchronously in the commit phase,
  // so the intermediate null is never painted.
  const activeTabTitle = openTabs.find(t => t.id === activeTabId)?.title;
  const agentName = selectedAgent?.name;
  const contextPct = contextWarning?.pct ?? null;
  const fileCount = attachments.length;
  useEffect(() => {
    setActiveSessionMeta({
      topic: activeTabTitle || 'New Session',
      contextPct,
      fileCount,
      agentName: agentName || 'SwarmAI',
      sessionId,
      tabId: activeTabId ?? undefined,
    });
    return () => setActiveSessionMeta(null);
  }, [activeTabTitle, contextPct, fileCount, agentName, sessionId, activeTabId, setActiveSessionMeta]);

  // Cmd+1-9 / Ctrl+1-9 keyboard shortcuts for tab switching.
  // Uses openTabsRef to avoid re-registering the listener on every tab change.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9') {
        e.preventDefault();
        const idx = parseInt(e.key) - 1;
        const tabs = openTabsRef.current;
        if (tabs[idx]) {
          handleTabSelect(tabs[idx].id);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleTabSelect]);

  // Validate tabs against sessions - filter out tabs referencing deleted sessions (Req 3.4)
  // Guard: skip during initial restore — sessions query may return stale data
  // before the full session list is loaded, causing valid tabs to be invalidated.
  useEffect(() => {
    if (sessions.length === 0) return;
    if (!messagesReady) return; // Don't invalidate tabs before restore completes
    
    const validSessionIds = new Set(sessions.map(s => s.id));
    removeInvalidTabs(validSessionIds);
  }, [sessions, messagesReady, removeInvalidTabs]);

  // Sync active tab content when activeTabId changes (for tab switching/closing)
  // IMPORTANT: Only react to activeTabId changes — NOT sessionId or openTabs changes.
  // sessionId changes during streaming (session_start event) must not trigger
  // a reload, or it will wipe in-progress messages.
  // openTabs changes (from render counter bumps) must not re-trigger this effect.
  //
  // RACE FIX: Skip during initial tab restore (isLoadingHistory=true) to prevent
  // the else branch from wiping messages before doRestore finishes loading them.
  // Without this guard, restoreFromFile() sets activeTabId (triggering this effect)
  // before loadSessionMessages completes, causing messages=[] → layout collapse.
  const prevActiveTabIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activeTabId) return;
    // Skip if activeTabId hasn't actually changed
    if (prevActiveTabIdRef.current === activeTabId) return;
    prevActiveTabIdRef.current = activeTabId;

    // Skip if handleTabSelect already synced state for this tab switch.
    // Reset the flag for the next switch.
    if (tabSelectHandledRef.current) {
      tabSelectHandledRef.current = false;
      return;
    }

    // Guard: skip during initial restore — doRestore handles message loading
    // directly and will set messagesReady when done. Without this, the else
    // branch below fires before the tab map is fully populated, wiping messages.
    if (isLoadingHistory) return;

    // Read tab metadata from the map (stable, not from openTabs which triggers re-renders)
    const activeTabState = tabMapRef.current.get(activeTabId);
    if (!activeTabState) return;

    // FIX (P0 tab-switch streaming content loss):
    // If the tab already has messages in memory (from streaming or prior load),
    // sync directly from tabState — do NOT fetch from backend.
    // Backend fetch overwrites in-progress streaming content with stale data.
    if (activeTabState.messages.length > 0) {
      setMessages([...activeTabState.messages]);
      setSessionId(activeTabState.sessionId);
      setPendingQuestion(activeTabState.pendingQuestion ?? null);
      return;
    }

    // Tab has no in-memory messages — load from backend if it has a session
    if (activeTabState.sessionId && activeTabState.sessionId !== sessionId) {
      loadSessionMessages(activeTabState.sessionId);
    } else if (!activeTabState.sessionId) {
      // Tab has no session — reset to welcome.
      // This covers both: switching to a fresh tab while another tab had
      // a session, AND the auto-created tab after closing the last one.
      setMessages([]);
      setSessionId(undefined);
      setPendingQuestion(null);
    }
  }, [activeTabId, sessionId, isStreaming, isLoadingHistory, loadSessionMessages, tabMapRef, setMessages, setSessionId, setPendingQuestion]);



  // Backfill a ToDo dispatch record once its tab owns a session. Pure decision
  // in planDispatchBackfill; here we perform the effect (dispatch + prune ref).
  // Reused by BOTH the active-tab bind effect below AND the background-tab
  // onTabSessionBound callback (fired from session_start for any tab) — one
  // helper, no per-branch duplication. Keeps ToDo logic in ChatPage, out of the
  // streaming hot path (the hook only fires an opaque callback).
  const backfillDispatchForTab = useCallback((tabId: string, boundSessionId: string) => {
    const plan = planDispatchBackfill(dispatchPendingRef.current, tabId, boundSessionId);
    if (!plan.toDispatch) return;
    // Idempotency guard: a session_start on the ACTIVE tab reaches this helper by
    // BOTH paths — the onTabSessionBound callback (background/any-tab) AND the
    // global-sessionId bind effect. Without a guard the same record could dispatch
    // twice (Gate-2 race). One dispatch per (tab,session) pair, whichever path wins.
    const key = `${tabId}::${boundSessionId}`;
    if (backfilledTabSessionsRef.current.has(key)) return;
    backfilledTabSessionsRef.current.add(key);
    void todosService.dispatch(plan.toDispatch.todoId, plan.toDispatch.tabLabel, boundSessionId)
      .catch(() => {/* non-fatal */});
    dispatchPendingRef.current = plan.remaining;
  }, []);
  backfillDispatchForTabRef.current = backfillDispatchForTab;

  // Bind the global sessionId to the active tab when a session materializes FOR
  // that tab — and backfill any pending ToDo-dispatch record. UNIFIED GUARD
  // (run_3e0672d2 perpetual-thinking fix): both actions gate on
  // canBindSessionToActiveTab, which refuses to bind/act on a session already
  // owned by ANOTHER tab. Previously this stamped ANY ambient global sessionId
  // onto a session-less active tab — so opening a fresh tab (dispatch / "+ New
  // Session" / resume) WHILE the prior tab was still streaming made the fresh
  // empty tab inherit the streaming session → two tabs, one session → perpetual
  // "thinking" + queued sends. The real per-tab bind already happens at the
  // source (session_start writes tabState.sessionId; resume calls
  // updateTabSessionId), so this effect is a backstop that must NEVER claim a
  // foreign session. One chokepoint fixes every tab-open entrance at once.
  useEffect(() => {
    // Read the LIVE map (not the memoized openTabs projection, which can lag the
    // direct tabState.sessionId writes session_start makes) so ownership is
    // decided against current truth.
    const liveTabs = [...tabMapRef.current.values()].map((t) => ({ id: t.id, sessionId: t.sessionId }));
    if (!canBindSessionToActiveTab(sessionId, activeTabId ?? undefined, liveTabs)) return;
    const boundSessionId = sessionId as string;
    const boundTabId = activeTabId as string;
    // Read from the map directly (stable, avoids openTabs dependency)
    const tabState = tabMapRef.current.get(boundTabId);
    if (tabState && !tabState.sessionId) {
      updateTabSessionId(boundTabId, boundSessionId);
    }
    // ToDo Dispatch backfill: this tab's session just materialized (active path).
    // Same helper the background-tab onTabSessionBound callback uses (unified).
    backfillDispatchForTab(boundTabId, boundSessionId);
    // `openTabs` in deps (not just consumed) makes this SELF-HEALING: if the guard
    // early-returned because another tab transiently owned this session, closing
    // or rebinding that tab changes the memoized openTabs identity → the effect
    // re-evaluates and completes the now-unblocked bind. Without it the effect
    // only re-runs on sessionId/activeTabId change and a transient block could
    // (in an unreached-but-possible ordering) strand the bind.
  }, [sessionId, activeTabId, updateTabSessionId, tabMapRef, openTabs, backfillDispatchForTab]);

  // Build content array from text and attachments using delivery strategy
  const buildContentArray = useCallback(
    async (text: string, fileAttachments: typeof attachments): Promise<ContentBlock[]> => {
      const content: ContentBlock[] = [];

      if (text.trim()) {
        content.push({ type: 'text', text } as ContentBlock);
      }

      for (const att of fileAttachments) {
        if (att.error || att.isLoading) continue;

        switch (att.deliveryStrategy) {
          case 'base64_image': {
            // Claude API image blocks only accept jpeg/png/gif/webp.
            // Guard: reject unsupported image types (should not reach here after
            // determineDeliveryStrategy fix, but defend in depth).
            const imgMime = (att.mediaType || '').trim().toLowerCase();
            if (imgMime && !CLAUDE_NATIVE_IMAGE_MIMES.has(imgMime)) {
              content.push({
                type: 'text',
                text: `[Attached image: ${att.name}] — ${imgMime} is not supported for native image processing. Use the Read tool to access this file.`,
              } as ContentBlock);
              break;
            }
            content.push({
              type: 'image',
              source: { type: 'base64', media_type: att.mediaType, data: att.base64! },
              _filename: att.name,
            } as unknown as ContentBlock);
            break;
          }
          case 'base64_document':
            // Claude API document blocks ONLY accept application/pdf.
            // Guard: reject non-PDF media types (should not reach here after
            // determineDeliveryStrategy fix, but defend in depth).
            if (att.mediaType && att.mediaType !== 'application/pdf') {
              content.push({
                type: 'text',
                text: `[Attached file: ${att.name}] — non-PDF document cannot be sent as base64. Use the Read tool to access this file.`,
              } as ContentBlock);
              break;
            }
            content.push({
              type: 'document',
              source: { type: 'base64', media_type: 'application/pdf', data: att.base64! },
              _filename: att.name,
            } as unknown as ContentBlock);
            break;
          case 'inline_text': {
            // Workspace files: read content at send time (fresh read)
            // File Picker files: textContent was set at attach time
            let textContent = att.textContent;
            if (!textContent && att.workspacePath && selectedAgentId) {
              try {
                const raw = await workspaceService.readFile(selectedAgentId, att.workspacePath);
                // Only use inline text if the file is UTF-8 text
                if (raw.encoding === 'utf-8') {
                  textContent = raw.content;
                } else {
                  // Binary file — fall back to path hint
                  content.push({
                    type: 'text',
                    text: `[Attached file: ${att.name}] saved at ${att.workspacePath} - use Read tool to access`,
                  } as ContentBlock);
                  continue;
                }
              } catch (err) {
                console.error(`Failed to read workspace file: ${att.name}`, err);
                content.push({ type: 'text', text: `[Failed to read file: ${att.name}]` } as ContentBlock);
                continue;
              }
            }
            content.push({
              type: 'text',
              text: `--- File: ${att.name} ---\n${textContent}\n--- End: ${att.name} ---`,
            } as ContentBlock);
            break;
          }
          case 'path_hint':
            if (att.workspacePath) {
              // Workspace Explorer drag — file already on disk, just reference it
              content.push({
                type: 'text',
                text: `[Attached file: ${att.name}] saved at ${att.workspacePath} - use Read tool to access`,
              } as ContentBlock);
            } else if (att.base64) {
              // File Picker: all path_hint files (binary docs, large text, audio, video).
              // Send as a document block so the backend can save to Attachments/
              // and generate a smart path hint with file-type-specific guidance.
              content.push({
                type: 'document',
                source: { type: 'base64', media_type: att.mediaType || 'application/octet-stream', data: att.base64 },
                _filename: att.name,
              } as unknown as ContentBlock);
            } else {
              // Fallback: mention the file by name so agent knows it was attached
              content.push({
                type: 'text',
                text: `[Attached ${att.type} file: ${att.name} (${(att.size / (1024 * 1024)).toFixed(1)}MB) — file was attached from system file picker]`,
              } as ContentBlock);
            }
            break;
        }
      }

      return content;
    },
    [selectedAgentId]
  );

  // Create tab-aware stream handler — permission handling is now fully inline
  // in the hook (appends content block to messages). No ChatPage wrapper needed.
  // Fix 6: Pass activeTabIdRef.current as tabId for tab-aware streaming.
  const wrappedCreateStreamHandler = useCallback((assistantMessageId: string) => {
    const tabId = activeTabIdRef.current ?? undefined;
    return createStreamHandler(assistantMessageId, tabId);
  }, [createStreamHandler, activeTabIdRef]);

  // Handle plugin commands (Req 7.2 — memoized with useCallback)
  const handlePluginCommand = useCallback(async (command: string): Promise<boolean> => {
    const parts = command.trim().split(/\s+/);
    if (parts[0] !== '/plugin') return false;

    const subCommand = parts[1];
    const args = parts.slice(2).join(' ');

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: [{ type: 'text', text: command }],
      timestamp: new Date().toISOString(),
    };
    // SINGLE-WRITER: route through the store so the keep-mounted TabView (which
    // reads its own store, not the shared `messages`) displays plugin output.
    insertOptimisticMessages(activeTabIdRef.current, [userMessage]);

    const assistantMessageId = (Date.now() + 1).toString();
    let responseText = '';

    try {
      switch (subCommand) {
        case 'list': {
          const pluginList = await pluginsService.listPlugins();
          if (pluginList.length === 0) {
            responseText = '📦 No plugins installed.\n\nUse `/plugin install {name}@{marketplace}` to install a plugin.';
          } else {
            responseText = '📦 **Installed Plugins:**\n\n| Name | Version | Source | Status |\n|------|---------|--------|--------|\n';
            for (const plugin of pluginList) {
              const statusIcon = plugin.status === 'installed' ? '✅' : plugin.status === 'disabled' ? '⏸️' : '❌';
              responseText += `| ${plugin.name} | ${plugin.version} | ${plugin.marketplaceName || 'Unknown'} | ${statusIcon} ${plugin.status} |\n`;
            }
          }
          break;
        }
        case 'install': {
          if (!args) {
            responseText = '❌ **Usage:** `/plugin install {name}@{marketplace}`\n\nExample: `/plugin install my-skill@official-marketplace`';
          } else {
            const atIndex = args.lastIndexOf('@');
            if (atIndex === -1) {
              responseText = '❌ **Invalid format.** Use: `/plugin install {name}@{marketplace}`';
            } else {
              const pluginName = args.substring(0, atIndex);
              const marketplaceName = args.substring(atIndex + 1);
              const marketplaces = await pluginsService.listMarketplaces();
              const marketplace = marketplaces.find((m) => m.name.toLowerCase() === marketplaceName.toLowerCase());
              if (!marketplace) {
                responseText = `❌ **Marketplace not found:** "${marketplaceName}"\n\nAvailable marketplaces:\n${marketplaces.map((m) => `- ${m.name}`).join('\n') || 'No marketplaces configured.'}`;
              } else {
                const plugin = await pluginsService.installPlugin({ pluginName, marketplaceId: marketplace.id });
                responseText = `✅ **Plugin installed successfully!**\n\n**${plugin.name}** v${plugin.version}\n\n`;
                if (plugin.installedSkills.length > 0) responseText += `- Skills: ${plugin.installedSkills.join(', ')}\n`;
                if (plugin.installedCommands.length > 0) responseText += `- Commands: ${plugin.installedCommands.join(', ')}\n`;
                if (plugin.installedAgents.length > 0) responseText += `- Agents: ${plugin.installedAgents.join(', ')}\n`;
                if (plugin.installedHooks.length > 0) responseText += `- Hooks: ${plugin.installedHooks.join(', ')}\n`;
                if (plugin.installedMcpServers.length > 0) responseText += `- MCP Servers: ${plugin.installedMcpServers.join(', ')}\n`;
              }
            }
          }
          break;
        }
        case 'uninstall': {
          if (!args) {
            responseText = '❌ **Usage:** `/plugin uninstall {plugin-id}`\n\nUse `/plugin list` to see installed plugins.';
          } else {
            const result = await pluginsService.uninstallPlugin(args);
            responseText = `✅ **Plugin uninstalled successfully!**\n\n`;
            if (result.removedSkills.length > 0) responseText += `- Removed skills: ${result.removedSkills.join(', ')}\n`;
            if (result.removedCommands.length > 0) responseText += `- Removed commands: ${result.removedCommands.join(', ')}\n`;
            if (result.removedAgents.length > 0) responseText += `- Removed agents: ${result.removedAgents.join(', ')}\n`;
            if (result.removedHooks.length > 0) responseText += `- Removed hooks: ${result.removedHooks.join(', ')}\n`;
          }
          break;
        }
        case 'marketplace': {
          if (parts[2] === 'list') {
            const marketplaces = await pluginsService.listMarketplaces();
            if (marketplaces.length === 0) {
              responseText = '🏪 No marketplaces configured.\n\nAdd a marketplace from the Plugins page.';
            } else {
              responseText = '🏪 **Available Marketplaces:**\n\n| Name | URL | Plugins |\n|------|-----|--------|\n';
              for (const m of marketplaces) {
                responseText += `| ${m.name} | ${m.url} | ${m.cachedPlugins?.length || '-'} |\n`;
              }
            }
          } else {
            responseText = `❌ **Unknown marketplace command**\n\nAvailable: \`/plugin marketplace list\``;
          }
          break;
        }
        default:
          responseText = `❌ **Unknown plugin command:** "${subCommand}"\n\nAvailable:\n- \`/plugin list\`\n- \`/plugin install {name}@{marketplace}\`\n- \`/plugin uninstall {id}\`\n- \`/plugin marketplace list\``;
      }
    } catch (error) {
      responseText = `❌ **Error:** ${error instanceof Error ? error.message : 'An error occurred'}`;
    }

    insertOptimisticMessages(activeTabIdRef.current, [
      { id: assistantMessageId, role: 'assistant', content: [{ type: 'text', text: responseText }], timestamp: new Date().toISOString() },
    ]);
    return true;
  }, [insertOptimisticMessages, activeTabIdRef]);

  // Handle send message (Req 7.1 — memoized with useCallback, volatile deps via refs)
  const handleSendMessage = useCallback(async () => {
    const messageText = inputValueRef.current;
    const currentAttachments = attachmentsRef.current;
    const hasText = messageText.trim().length > 0;
    const hasAttachments = currentAttachments.some((a) => !a.error && !a.isLoading);

    if ((!hasText && !hasAttachments) || !selectedAgentId) return;

    // Per-tab streaming guard: check only the active tab's state
    const activeTabForGuard = tabMapRef.current.get(activeTabIdRef.current ?? '');

    // Hard guard: session creation in-flight — block ONLY during true pre-session
    // phase (tab has no sessionId yet). Once a session exists, the streaming guard
    // at L1333 handles queueing correctly. Previously this blocked ALL sends when
    // pendingStreamTabs contained the tab — but setIsStreaming(true) during drain
    // re-adds to pendingStreamTabs (for re-render), silently swallowing user input.
    if (!activeTabForGuard?.sessionId && pendingStreamTabs.has(activeTabIdRef.current ?? '')) return;

    // ──── QUEUE PATH: session is busy or in an uncertain post-disconnect state ──
    // shouldQueueSend covers ALL states where the backend session may still be
    // busy: isStreaming, isWaitingForBusy (SESSION_BUSY poll), isReconnecting,
    // _healGraceActive, and _postDisconnectUncertain (heal-grace expired but the
    // backend subprocess may still be streaming a long agent turn). Previously
    // isWaitingForBusy hit a `return` that DROPPED the message, and the queue
    // path only fired on isStreaming — so a post-disconnect send escaped to a
    // normal send → SESSION_BUSY → orphan delete → silent loss. Queue instead.
    if (activeTabForGuard && shouldQueueSend(activeTabForGuard)) {
      const trimmedText = messageText.trim();
      if (!trimmedText && !hasAttachments) return;

      const displayText = trimmedText || '';
      const displayContent: ContentBlock[] = [];
      if (displayText) {
        displayContent.push({ type: 'text', text: displayText });
      }
      if (hasAttachments) {
        displayContent.push({ type: 'text', text: `📎 ${currentAttachments.filter((a) => !a.error && !a.isLoading).map((a) => a.name).join(', ')}` });
      }

      // APPEND PATH: if a message is already queued, concatenate new text
      // and merge attachments. User's first thought shouldn't vanish silently.
      const existingQueued = activeTabForGuard.queuedMessage;
      if (existingQueued) {
        const combinedText = trimmedText
          ? `${existingQueued.text}\n\n${messageText}`
          : existingQueued.text;
        const newAttachments = (currentAttachments || []).filter((a) => !a.error && !a.isLoading);
        const combinedAttachments = [
          ...existingQueued.attachments,
          ...newAttachments,
        ].slice(0, MAX_ATTACHMENTS); // enforce limit across appends
        const combinedDisplayText = trimmedText
          ? `${existingQueued.text.trim()}\n\n${trimmedText}`
          : existingQueued.text.trim();
        const combinedDisplayContent: ContentBlock[] = [];
        if (combinedDisplayText) {
          combinedDisplayContent.push({ type: 'text', text: combinedDisplayText });
        }
        if (combinedAttachments.length > 0) {
          combinedDisplayContent.push({
            type: 'text',
            text: `📎 ${combinedAttachments.map((a) => a.name).join(', ')}`,
          });
        }

        // SINGLE-WRITER: update queued message content through the store.
        // updateById is not phase-gated — always succeeds during streaming.
        // Store subscription propagates → setMessages + tabState.messages.
        const appendStore = activeTabIdRef.current
          ? messageStoreRegistry.get(activeTabIdRef.current)
          : null;
        if (appendStore) {
          appendStore.updateById(existingQueued.messageId, (m) => ({
            ...m,
            content: combinedDisplayContent,
            timestamp: new Date().toISOString(),
          }));
        } else {
          // Fallback: no store
          setMessages((prev) =>
            prev.map((m) =>
              m.id === existingQueued.messageId
                ? { ...m, content: combinedDisplayContent, timestamp: new Date().toISOString() }
                : m
            )
          );
          if (activeTabForGuard.messages) {
            activeTabForGuard.messages = activeTabForGuard.messages.map((m) =>
              m.id === existingQueued.messageId
                ? { ...m, content: combinedDisplayContent, timestamp: new Date().toISOString() }
                : m
            );
          }
        }

        activeTabForGuard.queuedMessage = {
          text: combinedText,
          attachments: combinedAttachments,
          displayContent: combinedDisplayContent,
          messageId: existingQueued.messageId,
        };
        activeTabForGuard._queuedAt = activeTabForGuard._queuedAt || Date.now(); // preserve original queue time

        setInputValue('');
        clearAttachments();
        return;
      }

      // NEW QUEUE: no existing queued message — create one
      const queuedMessageId = `queued-${crypto.randomUUID()}`;

      const queuedUserMessage: Message = {
        id: queuedMessageId,
        role: 'user',
        content: displayContent,
        timestamp: new Date().toISOString(),
        isQueued: true,
      };

      // SINGLE-WRITER: route through store — append() is NOT phase-gated,
      // so it succeeds during streaming. The store→React subscription effect
      // propagates to setMessages + tabState.messages on the next rAF frame.
      // Without this, the subscription overwrites the setMessages/tabState
      // write with the store snapshot (which lacks the queued message),
      // causing the user message to "disappear" during streaming.
      const queueStore = activeTabIdRef.current
        ? messageStoreRegistry.get(activeTabIdRef.current)
        : null;
      if (queueStore) {
        queueStore.append(queuedUserMessage);
      } else {
        // Fallback: no store (should not happen in normal flow)
        setMessages((prev) => [...prev, queuedUserMessage]);
        if (activeTabForGuard.messages) {
          activeTabForGuard.messages = [...activeTabForGuard.messages, queuedUserMessage];
        }
      }

      activeTabForGuard.queuedMessage = {
        text: messageText,
        attachments: (currentAttachments || []).filter((a) => !a.error && !a.isLoading),
        displayContent,
        messageId: queuedMessageId,
      };
      activeTabForGuard._queuedAt = Date.now();

      setInputValue('');
      clearAttachments();
      return;
    }

    // ──── NORMAL SEND PATH (existing code) ──────────────────────────────
    // Register the active tab in the per-tab map BEFORE setIsStreaming(true).
    // setIsStreaming only writes tabState.isStreaming when the tab exists; if
    // registration happened later (it used to, ~60 lines down), the flag write
    // here would be a silent no-op and only pendingStreamTabs would be set.
    // Since the isStreaming derivation treats the per-tab flag as authoritative
    // once a tab is registered, a dropped flag write would leave the spinner
    // missing for the first turn on a brand-new tab. Registering first makes
    // the flag write deterministic.
    const sendTabId = activeTabIdRef.current;
    if (sendTabId && !tabMapRef.current.has(sendTabId)) {
      initTabState(sendTabId, messagesRef.current);
    }

    // Set streaming flag IMMEDIATELY after guard passes to close the race
    // window between guard check and the old setIsStreaming call ~20 lines
    // below.  setIsStreaming synchronously mutates tabMapRef.isStreaming,
    // so a second rapid click/Enter will be caught by the guard above.
    setIsStreaming(true, activeTabIdRef.current ?? undefined);

    // Clear userStopped and reset streaming-related state from a previous
    // stop — this is a fresh send. Without this, stale flags from the old
    // stream can suppress errors or break indicators on the new stream.
    if (activeTabForGuard) {
      activeTabForGuard.userStopped = false;
      activeTabForGuard.hasReceivedData = false;
      activeTabForGuard.isReconnecting = false;
      activeTabForGuard.reconnectionAttempt = 0;
      activeTabForGuard.isResuming = false;
      // A genuine new stream is starting — the post-disconnect uncertainty is
      // resolved (this send opens a fresh SSE connection / session).
      activeTabForGuard._postDisconnectUncertain = false;
    }

    if (messageText.trim().startsWith('/plugin')) {
      setIsStreaming(false, activeTabIdRef.current ?? undefined);
      setInputValue('');
      await handlePluginCommand(messageText.trim());
      return;
    }

    const content = await buildContentArray(messageText, currentAttachments);
    if (content.length === 0) {
      setIsStreaming(false, activeTabIdRef.current ?? undefined);
      return;
    }

    const displayText = hasText ? messageText : '[Attachments]';
    const userMessageContent: ContentBlock[] = [{ type: 'text', text: displayText }];
    if (hasAttachments && hasText) {
      userMessageContent.push({ type: 'text', text: `📎 ${currentAttachments.map((a) => a.name).join(', ')}` });
    }

    // Generate clientId for optimistic message dedup — this ID is sent to
    // backend and echoed in result event so MessageStore can correlate
    // optimistic→DB messages during reconcile (eliminates R2/R4 duplication).
    const clientId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const userMessage: Message = { id: clientId, role: 'user', content: userMessageContent, timestamp: new Date().toISOString() };
    // NOTE: optimistic insertion is deferred — the user message is appended to
    // the MessageStore (single source of truth) together with the assistant
    // placeholder below, so the store→React sync effect doesn't clobber a
    // setMessages-only write on a fresh tab (whose store starts empty).
    setInputValue('');
    clearAttachments();
    resetUserScroll(); // Fix 2: resume auto-scroll on new user message
    incrementStreamGen(); // Fix 1: new stream generation

    // Fix 8: Transition tab status to 'streaming' (handles error → streaming case too)
    const currentActiveTabId = activeTabIdRef.current;
    if (currentActiveTabId) {
      updateTabStatus(currentActiveTabId, 'streaming');
    }

    // Update tab title on first message (Req 2.4)
    if (currentActiveTabId) {
      const activeTab = openTabsRef.current.find(t => t.id === currentActiveTabId);
      if (activeTab?.isNew && messageText.trim()) {
        const newTitle = messageText.slice(0, 25) + (messageText.length > 25 ? '...' : '');
        updateTabTitle(currentActiveTabId, newTitle);
        setTabIsNew(currentActiveTabId, false);
      }
    }

    // Assistant placeholder id shares the turn's clientId with an "-asst" suffix
    // so MessageStore._applyMerge can correlate it to the persisted DB assistant
    // row (whose metadata.client_id is `{clientId}-asst`). A numeric id (the old
    // Date.now()+1) never started with "local-" → never correlated → empty bubble
    // stayed and the stream never finalized (P4, run_af36e709). The "-asst" suffix
    // keeps it distinct from the user placeholder (id === clientId) so each row
    // maps to its own placeholder.
    const assistantMessageId = `${clientId}-asst`;
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };

    // Tab registration — normally already done at the top of the send path
    // (before setIsStreaming). Kept as a defensive fallback for any path that
    // reaches here without prior registration. Idempotent: no-op if present.
    // Also guarantees a MessageStore exists for the append below.
    if (currentActiveTabId && !tabMapRef.current.has(currentActiveTabId)) {
      initTabState(currentActiveTabId, messagesRef.current);
    }

    // SINGLE-WRITER: insert the optimistic user message + assistant placeholder
    // through the MessageStore — NOT setMessages/tabState directly. initTabState
    // eagerly creates an (empty) store per tab, and the store→React sync effect
    // treats the store as authoritative. A setMessages-only write gets clobbered
    // by the empty snapshot, AND the placeholder never reaches the store — so
    // updateLast() (keyed by assistantMessageId) silently no-ops every streaming
    // delta, leaving the WelcomeScreen up and the spinner stuck. Appending to the
    // store fixes both: the briefing clears and streamed tokens land. The sync
    // effect propagates store → React state + tabState.messages cache.
    const sendStore = currentActiveTabId
      ? messageStoreRegistry.getOrCreate(currentActiveTabId)
      : null;
    if (sendStore) {
      sendStore.appendMany([userMessage, assistantPlaceholder]);
    } else {
      // Degenerate fallback (no active tab id) — React state only.
      setMessages((prev) => [...prev, userMessage, assistantPlaceholder]);
    }

    // Resolve sessionId STRICTLY from this tab's own state (tabMapRef, which the
    // session_start handler updates synchronously). NEVER fall back to the shared
    // sessionIdRef — that holds the *active tab's* id and would misroute a NEW
    // tab's first send into the previously-active tab's session (cross-tab leak,
    // Principle 4). A tab with no sessionId yet must send undefined → backend
    // creates a fresh session for it.
    const resolvedSessionId = currentActiveTabId
      ? tabMapRef.current.get(currentActiveTabId)?.sessionId
      : undefined;

    refreshCanvasForSend(); // sync-read live Canvas state before send (race fix)
    const abort = chatService.streamChat(
      {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId: resolvedSessionId,
        enableSkills,
        enableMCP,
        ...(uiStateRef.current && { editorContext: uiStateRef.current }),
        ...(terminalContextRef.current && { terminalContext: terminalContextRef.current }),
        clientId,  // Correlation ID for optimistic message dedup
      },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, activeTabIdRef.current ?? undefined),
      createCompleteHandler(activeTabIdRef.current ?? undefined),
      createDisconnectHandler(activeTabIdRef.current ?? undefined),
      streamLiveness,
    );

    // P2 one-shot: an attached terminal rides exactly ONE turn, then clears so
    // a stale build log doesn't silently attach to every subsequent message.
    terminalContextRef.current = null;

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (currentActiveTabId) {
      // Build a retry function for reconnection logic — re-initiates the
      // same streamChat call with the same request and fresh handlers.
      const streamRequest = {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId: resolvedSessionId,
        enableSkills,
        enableMCP,
        // Carry the SAME clientId as the initial send (placeholder is
        // `${clientId}-asst`) — a reconnect-resend re-persists the assistant row,
        // and without clientId that row is keyless, so a later reconcile-tail cut
        // landing on it duplicates the bubble (run_7263ff67, the 5th entrance).
        clientId,
        ...(uiStateRef.current && { editorContext: uiStateRef.current }),
        ...(terminalContextRef.current ? { terminalContext: terminalContextRef.current } : {}),
      };
      const capturedTabIdForRetry = currentActiveTabId;
      const retryStreamFn = () => {
        return chatService.streamChat(
          { ...streamRequest, sessionId: tabMapRef.current.get(capturedTabIdForRetry)?.sessionId ?? streamRequest.sessionId },
          // run_26aa6caa (R27 half-migration close): use the CAPTURED retry tab, NOT
          // wrappedCreateStreamHandler (which reads activeTabIdRef.current). On a
          // background tab's reconnect/resend the active tab has changed, so the
          // wrapped form stamped this tab's stream events (incl. the file_changed
          // tabId) onto the ACTIVE tab → cross-tab bleed. The other 3 handlers here
          // already use capturedTabIdForRetry; this makes all 4 symmetric.
          createStreamHandler(assistantMessageId, capturedTabIdForRetry),
          createErrorHandler(assistantMessageId, capturedTabIdForRetry),
          createCompleteHandler(capturedTabIdForRetry),
          createDisconnectHandler(capturedTabIdForRetry),
          streamLiveness,
        );
      };

      updateTabState(currentActiveTabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
        hasReceivedData: false,
        isReconnecting: false,
        reconnectionAttempt: 0,
        retryStreamFn,
        // A manual send supersedes any armed auto-resend for this tab: clear the
        // flag (so a late 'backend-recovered' can't double-send) and reset the
        // episode attempt counter.
        _pendingResendOnRecovery: false,
        _pendingResendAssistantId: undefined,
        _pendingResendAttempts: 0,
      });
    }
  }, [selectedAgentId, enableSkills, enableMCP, handlePluginCommand, buildContentArray, clearAttachments, resetUserScroll, incrementStreamGen, setIsStreaming, setMessages, setInputValue, updateTabStatus, updateTabTitle, setTabIsNew, initTabState, wrappedCreateStreamHandler, createStreamHandler, createErrorHandler, createCompleteHandler, createDisconnectHandler, activeTabIdRef, tabMapRef, pendingStreamTabs, queryClient, t]);

  // Wire handleSendMessage ref for voice conversation mode (avoids circular dep)
  handleSendMessageRef.current = handleSendMessage;

  // Handle WelcomeScreen focus item click — injects title as input and sends immediately
  const handleFocusClick = useCallback((title: string) => {
    if (!title.trim()) return;
    // Direct ref mutation bypasses React batching — handleSendMessage reads inputValueRef.current synchronously
    inputValueRef.current = title;
    handleSendMessage();
  }, [handleSendMessage]);

  // Handle Briefing Hub v2 item click — populate ChatInput with message + context (no auto-send)
  const handleItemClick = useCallback((message: string, context?: string) => {
    const lines = [message];
    if (context) {
      lines.push('');
      lines.push(...context.split('\n').map((l: string) => `> ${l}`));
    }
    const text = lines.join('\n');
    // Sync ref AND state — handleSendMessage reads inputValueRef.current synchronously
    inputValueRef.current = text;
    setInputValue(text);
    // Focus the ChatInput textarea
    const textarea = document.querySelector<HTMLTextAreaElement>('[data-chat-input]');
    textarea?.focus();
  }, [setInputValue]);

  /**
   * Drain the queued message for a tab — builds content and starts a new stream.
   *
   * Called from:
   * - createStreamHandler (result event) via onSendQueued — normal completion (site A)
   * - handleStop (finally block) — user-initiated stop (site B)
   *
   * CRITICAL: This is a trusted internal call. It MUST NOT check
   * pendingStreamTabs or isStreaming — those guards are for user-initiated
   * sends only. By the time this runs, the previous stream is already done.
   *
   * Async because buildContentArray does network I/O (deferred from queue time).
   */
  const drainQueuedMessage = useCallback(async (tabId: string) => {
    const tabState = tabMapRef.current.get(tabId);
    if (!tabState?.queuedMessage) return; // idempotent — safe if both sites race

    const queued = tabState.queuedMessage;
    tabState.queuedMessage = undefined; // clear BEFORE send (exactly-once)
    tabState._queuedAt = undefined;     // clear queue timestamp (reconcile immunity ends)
    tabState.userStopped = false; // prevent stale flag from suppressing new stream errors

    // Key this drained turn like the main send: the request carries `clientId`,
    // the assistant placeholder is `${clientId}-asst`, AND — the fix for the
    // user-row dup (run_cba89e65) — the QUEUED USER BUBBLE is stamped with the
    // SAME clientId in metadata.client_id. The main path keeps them in sync by
    // using `id === clientId` on the user bubble; the queued bubble's id is
    // `queued-{uuid}` (assigned at queue time, before any clientId exists), so
    // instead we carry the key in metadata. Without it the DB user row (which
    // the backend stamps with this request's client_id, session_router.py:1958)
    // correlates with the queued bubble by neither id nor client_id nor the H2
    // numeric-placeholder guard → a full-history reconcile renders TWO user
    // bubbles. `_applyMerge` indexes carried metadata.client_id role-agnostically,
    // so stamping it here makes the queued bubble correlate exactly like the main
    // path. Generated HERE (before the badge-clear) so both writes are one update.
    const clientId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    // Remove the "queued" badge from the user message + stamp the correlation key
    // — route through store for consistency (subscription propagates to React + tabState).
    const drainStore = messageStoreRegistry.get(tabId);
    if (drainStore) {
      drainStore.updateById(queued.messageId, (m) => ({
        ...m,
        isQueued: false,
        metadata: { ...(m.metadata ?? {}), client_id: clientId },
      }));
    } else {
      // Fallback: no store (rare edge case)
      const stampQueued = (m: Message): Message =>
        m.id === queued.messageId
          ? { ...m, isQueued: false, metadata: { ...(m.metadata ?? {}), client_id: clientId } }
          : m;
      const isActive = activeTabIdRef.current === tabId;
      if (isActive) {
        setMessages((prev) => prev.map(stampQueued));
      }
      const drainTab = tabMapRef.current.get(tabId);
      if (drainTab?.messages) {
        drainTab.messages = drainTab.messages.map(stampQueued);
      }
    }

    // Helper: clean up streaming state on drain failure.
    // The result handler keeps isStreaming=true when a queued message exists
    // (seamless indicator), so we must clean up if drain can't start a stream.
    const cleanupStreamingState = () => {
      setIsStreaming(false, tabId);
      updateTabStatus(tabId, 'idle');
    };

    try {
      // Build content NOW (deferred from queue time — async, reads files)
      const content = await buildContentArray(queued.text, queued.attachments);
      if (content.length === 0) {
        cleanupStreamingState();
        return;
      }

      // Set streaming state — idempotent if result handler already kept it
      // true, but ensures correctness if drain is called from other sites.
      setIsStreaming(true, tabId);
      if (tabId) updateTabStatus(tabId, 'streaming');
      incrementStreamGen();
      resetUserScroll();

      // clientId was generated above (before the badge-clear) so the queued user
      // bubble could be stamped with it. The assistant placeholder shares it via
      // the `${clientId}-asst` suffix; the request carries `clientId`. Without any
      // of these the turn persists keyless and a later reconcile-tail cut
      // duplicates the bubble (run_7263ff67 assistant side, run_cba89e65 user side).
      const assistantMessageId = `${clientId}-asst`;
      const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };

      // SINGLE-WRITER: route through the store so streaming deltas land
      // (see insertOptimisticMessages). A setMessages-only write left the
      // placeholder out of the store → updateLast() no-op'd every token.
      insertOptimisticMessages(tabId, [assistantPlaceholder]);

      // Resolve sessionId STRICTLY from this queued tab's own state — never
      // fall back to the shared sessionIdRef (would misroute into another tab's
      // session, Principle 4). No per-tab session yet → undefined (fresh session).
      const resolvedSessionId = tabState.sessionId;

      refreshCanvasForSend(); // sync-read live Canvas state before send (race fix)
      const abort = chatService.streamChat(
        {
          agentId: selectedAgentId!,
          content,
          sessionId: resolvedSessionId,
          enableSkills,
          enableMCP,
          clientId,
          ...(uiStateRef.current && { editorContext: uiStateRef.current }),
          ...(terminalContextRef.current && { terminalContext: terminalContextRef.current }),
        },
        createStreamHandler(assistantMessageId, tabId),
        createErrorHandler(assistantMessageId, tabId),
        createCompleteHandler(tabId),
        createDisconnectHandler(tabId),
        streamLiveness,
      );

      // P2 one-shot: clear attached terminal on the drain path too, so a
      // queued message consumes the attachment exactly once (matches the
      // primary send path clear). Without this the same buffer could ride a
      // later drained message.
      terminalContextRef.current = null;

      // Store abort function in tab map
      tabState.abortController = { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController;
      tabState.hasReceivedData = false;
      tabState.isReconnecting = false;
      tabState.reconnectionAttempt = 0;
      // Drain started successfully — reconcile can resume normal checks.
      tabState.drainPending = false;
      // Post-disconnect recovery succeeded — the queued message is now a live
      // stream, so the uncertainty is resolved. Cleared HERE (on success) not in
      // the reconcile block, so a FAILED drain (catch restores the queue) leaves
      // the flag set and reconcile retries instead of orphaning the message.
      tabState._postDisconnectUncertain = false;
    } catch (e) {
      // Send failed — restore queue so user doesn't lose their message
      tabState.queuedMessage = queued;
      tabState.drainPending = false;  // drain over (failed) — reconcile can resume
      cleanupStreamingState();
      console.error('[queue-drain] failed, queue restored:', e);
    }
  }, [buildContentArray, selectedAgentId, enableSkills, enableMCP, setIsStreaming, setMessages, updateTabStatus, incrementStreamGen, resetUserScroll, createStreamHandler, createErrorHandler, createCompleteHandler, tabMapRef, activeTabIdRef]);

  // Bridge the ref so useChatStreamingLifecycle can drain via deps.onDrainQueue.
  // Must be assigned after drainQueuedMessage is defined (circular dep with hook).
  drainQueueRef.current = drainQueuedMessage;

  // Bridge the recovery_exhausted "Start fresh session" action. Switch to the
  // target tab first (the toast may belong to a non-active tab), then clear it
  // to a new session via handleNewChat (operates on the active tab).
  startFreshRef.current = (tabId: string) => {
    selectTab(tabId);
    handleNewChat();
  };

  // Cancel a queued message: remove from chat, restore text + attachments to input.
  const handleCancelQueued = useCallback((tabId: string) => {
    const tabState = tabMapRef.current.get(tabId);
    if (!tabState?.queuedMessage) return;

    const queued = tabState.queuedMessage;

    // SINGLE-WRITER: remove through store (propagates via subscription).
    const cancelStore = messageStoreRegistry.get(tabId);
    if (cancelStore) {
      cancelStore.remove((m) => m.id === queued.messageId);
    } else {
      // Fallback: no store
      setMessages((prev) => prev.filter((m) => m.id !== queued.messageId));
      const cancelTab = tabMapRef.current.get(tabId);
      if (cancelTab?.messages) {
        cancelTab.messages = cancelTab.messages.filter((m) => m.id !== queued.messageId);
      }
    }

    // Clear queue
    tabState.queuedMessage = undefined;

    // Restore text to input
    setInputValue(queued.text);

    // Restore attachments to the attachment bar
    if (queued.attachments.length > 0) {
      restoreAttachments(queued.attachments);
    }
  }, [setMessages, setInputValue, tabMapRef, restoreAttachments]);

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (toolUseId: string, answers: Record<string, string>) => {
    const tabId = activeTabIdRef.current ?? undefined;
    const tabSessionId = tabId ? tabMapRef.current.get(tabId)?.sessionId : undefined;
    if (!selectedAgentId || !tabSessionId) {
      return;
    }

    // Defensive guard: prevent double-submit if already streaming.
    const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
    if (tabState?.isStreaming) {
      return;
    }

    // Clear the cross-tab "Swarm is asking…" toast (if one was raised because
    // the question arrived on a background tab) now that it's been answered.
    removeToast(`ask-uq-${toolUseId}`);

    // Root-1 SSOT Phase 3 (AC5 answer-in-flight guard): record the answered
    // toolUseId so the reconcile-loop re-surface does NOT re-inject this same
    // question during the window where the backend mirror may still report
    // waiting_input for it before transitioning out. Cleared by the reconcile
    // loop once the backend moves past it (useChatStreamingLifecycle.ts re-surface block).
    // Also clear the per-tab pendingQuestion REF (not just React state): the ref
    // is what the reconcile loop reads as currentPendingToolUseId. If only React
    // state is nulled, a subsequent tab-switch copies the (stale) ref back and the
    // answered question's id is lost as the idempotency key — leaving _answeredToolUseId
    // as the sole guard. Clearing the ref makes the answered state survive switches.
    if (tabState) {
      tabState._answeredToolUseId = toolUseId;
      tabState.pendingQuestion = null;
    }

    // Persist the submitted answers ONTO the ask_user_question block so the
    // renderer shows a read-only "Answered: …" summary instead of a disabled,
    // selection-less form. Written on the block (not component state) so it
    // survives tab-switch + store reconcile. The store write below is the
    // authority (single render source); the tabState.messages mirror is kept in
    // sync only so the cold-restore seed (handleSelectTab now seeds an EMPTY
    // store from tabState.messages on app-restart) carries the answered state.
    // Post reconcile-gap fix, switch-back no longer clobbers a populated store,
    // so the mirror is belt-and-suspenders for the restart path, not a guard
    // against reverse-flow truncation (run_9db9f987).
    if (tabId) {
      const writeAnswersOntoBlock = (msg: Message): Message => {
        let touched = false;
        const content = msg.content.map((b) => {
          const blk = b as { type?: string; toolUseId?: string };
          if (blk.type === 'ask_user_question' && blk.toolUseId === toolUseId) {
            touched = true;
            return { ...b, answers } as typeof b;
          }
          return b;
        });
        return touched ? { ...msg, content } : msg;
      };
      const hasBlock = (msg: Message) =>
        msg.content.some(
          (b) => (b as { type?: string; toolUseId?: string }).type === 'ask_user_question' &&
            (b as { toolUseId?: string }).toolUseId === toolUseId,
        );
      const store = messageStoreRegistry.get(tabId);
      if (store) {
        const matched = store.messages.some(hasBlock);
        if (!matched && import.meta.env.DEV) {
          // Silent answer-loss guard: if no block matches the just-submitted
          // toolUseId, updateLast no-ops → the optimistic "Answered" summary is
          // skipped AND (since the backend doesn't persist ask_user_question
          // blocks) reconcile carry-forward also lacks answers → the form
          // silently reverts. Surface it in dev so a toolUseId drift is visible.
          console.warn(
            '[ChatPage] answer submitted but no ask_user_question block matched toolUseId — summary will not render',
            { toolUseId, tabId },
          );
        }
        store.updateLast(writeAnswersOntoBlock, hasBlock);
        if (tabState) tabState.messages = store.messages;
      } else if (tabState) {
        tabState.messages = tabState.messages.map(writeAnswersOntoBlock);
      }
    }

    setPendingQuestion(null);
    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from waiting_input → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = Date.now().toString();
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    // SINGLE-WRITER: route through the store so streaming deltas land
    // (see insertOptimisticMessages).
    insertOptimisticMessages(tabId, [assistantPlaceholder]);

    const abort = chatService.streamAnswerQuestion(
      { agentId: selectedAgentId, sessionId: tabSessionId, toolUseId, answers, enableSkills, enableMCP },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, tabId),
      createCompleteHandler(tabId),
      createDisconnectHandler(tabId),
      streamLiveness,
    );

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (tabId) {
      const capturedTabIdForRetry = tabId;
      const retryStreamFn = () => {
        return chatService.streamAnswerQuestion(
          { agentId: selectedAgentId, sessionId: tabMapRef.current.get(capturedTabIdForRetry)?.sessionId ?? tabSessionId, toolUseId, answers, enableSkills, enableMCP },
          // run_26aa6caa (R27 half-migration close): CAPTURED retry tab, not
          // wrappedCreateStreamHandler (reads activeTabIdRef.current) — see the
          // streamChat retryStreamFn for the full rationale. All 4 handlers symmetric.
          createStreamHandler(assistantMessageId, capturedTabIdForRetry),
          createErrorHandler(assistantMessageId, capturedTabIdForRetry),
          createCompleteHandler(capturedTabIdForRetry),
          createDisconnectHandler(capturedTabIdForRetry),
          streamLiveness,
        );
      };
      updateTabState(tabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
        hasReceivedData: false,
        isReconnecting: false,
        reconnectionAttempt: 0,
        retryStreamFn,
      });
    }
  };

  // Handle QUEUE_TIMEOUT retry — re-sends the saved message when user clicks Retry.
  // Reads the retryPayload that was stashed in tabState by useChatStreamingLifecycle
  // when the backend returned QUEUE_TIMEOUT with a retryPayload.
  const handleRetryQueueTimeout = useCallback(() => {
    const tabId = activeTabIdRef.current;
    if (!tabId) return;
    const tabState = tabMapRef.current.get(tabId);
    const retry = tabState?.queueTimeoutRetry;
    if (!retry || !selectedAgentId) return;

    // Clear the retry payload so button disappears
    if (tabState) tabState.queueTimeoutRetry = null;

    // Remove the error message from the chat (last message should be the error)
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.isError) return prev.slice(0, -1);
      return prev;
    });

    // Re-initiate the stream with the saved payload
    incrementStreamGen();
    setIsStreaming(true, tabId);
    if (tabId) updateTabStatus(tabId, 'streaming');

    // Key this retry like the main send (run_7263ff67): clientId +
    // `${clientId}-asst` placeholder + clientId on the request — otherwise the
    // re-sent turn persists keyless and a later reconcile-tail cut duplicates it.
    const clientId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const assistantMessageId = `${clientId}-asst`;
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    // SINGLE-WRITER: clear the error from the store, then append the placeholder
    // so streaming deltas land (see insertOptimisticMessages).
    const retryStore = tabId ? messageStoreRegistry.getOrCreate(tabId) : null;
    if (retryStore) {
      retryStore.remove((m) => !!m.isError);
      retryStore.append(assistantPlaceholder);
    } else {
      setMessages((prev) => [...prev.filter(m => !m.isError), assistantPlaceholder]);
    }

    const abort = chatService.streamChat(
      {
        agentId: retry.agentId ?? selectedAgentId,
        sessionId: retry.sessionId,
        ...(retry.content ? { content: retry.content as ContentBlock[] } : { message: retry.userMessage ?? '' }),
        enableSkills,
        enableMCP,
        clientId,
      },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, tabId),
      createCompleteHandler(tabId),
      createDisconnectHandler(tabId),
      streamLiveness,
    );

    // Store abort function
    updateTabState(tabId, {
      abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
      hasReceivedData: false,
      isReconnecting: false,
      reconnectionAttempt: 0,
    });
  }, [selectedAgentId, enableSkills, enableMCP, incrementStreamGen, setIsStreaming, setMessages, updateTabStatus, wrappedCreateStreamHandler, createErrorHandler, createCompleteHandler, createDisconnectHandler, activeTabIdRef, tabMapRef]);

  // Handle escalation option click — sends the chosen option as a chat message.
  // Marks the escalation block as resolved optimistically, then sends.
  const handleEscalationSelect = useCallback((escalationId: string, optionLabel: string) => {
    // Optimistic UI: mark escalation as resolved — in the active tab's STORE
    // (the display source for the keep-mounted TabView) and the shared mirror.
    const escTabId = activeTabIdRef.current;
    const escStore = escTabId ? messageStoreRegistry.get(escTabId) : null;
    if (escStore) {
      const target = escStore.messages.find((m) =>
        m.content.some((b) => b.type === 'escalation' && (b as { id: string }).id === escalationId));
      if (target) {
        escStore.updateById(target.id, (msg) => ({
          ...msg,
          content: msg.content.map((block) =>
            block.type === 'escalation' && (block as { id: string }).id === escalationId
              ? { ...block, status: 'resolved', resolution: optionLabel }
              : block,
          ),
        }));
      }
    }
    setMessages((prev) => prev.map((msg) => ({
      ...msg,
      content: msg.content.map((block) =>
        block.type === 'escalation' && (block as { id: string }).id === escalationId
          ? { ...block, status: 'resolved', resolution: optionLabel }
          : block,
      ),
    })));
    // Send the choice as a user message (same path as handleFocusClick)
    inputValueRef.current = optionLabel;
    handleSendMessage();
  }, [handleSendMessage, setMessages, activeTabIdRef]);

  // Handle Continue — sends "continue" as user message when model stopped prematurely.
  // Matches Claude Code terminal /continue behavior.
  // Sets both ref AND state to avoid stale ref if handleSendMessage returns early.
  const handleContinue = useCallback(() => {
    inputValueRef.current = 'continue';
    setInputValue('continue');
    handleSendMessage();
  }, [handleSendMessage, setInputValue]);

  // Handle inline permission decision — called from InlinePermissionRequest component
  // via ContentBlockRenderer → AssistantMessageView → MessageBubble prop chain.
  const handlePermissionDecision = async (requestId: string, decision: 'approve' | 'deny') => {
    const tabId = activeTabIdRef.current ?? undefined;
    const tabSessionId = tabId ? tabMapRef.current.get(tabId)?.sessionId : undefined;
    if (!tabSessionId || !selectedAgentId) return;
    if (tabId && permissionLoadingTabs.current.has(tabId)) return; // per-tab double-click guard
    // Defensive guard: prevent double-submit if already streaming.
    // permissionLoadingTabs guards the API call, but a rapid approve click
    // could race with a stream that just started. Read tabMapRef directly.
    const currentTabState = tabId ? tabMapRef.current.get(tabId) : undefined;
    if (currentTabState?.isStreaming) return;

    if (tabId) permissionLoadingTabs.current.add(tabId);
    setPendingPermissionRequestId(null);

    // Update the content block's decision field so it renders decided state
    setMessages((prev) => prev.map((msg) => ({
      ...msg,
      content: msg.content.map((block) =>
        block.type === 'cmd_permission_request' && block.requestId === requestId
          ? { ...block, decision }
          : block,
      ),
    })));

    // Direct tabState write (synchronous — avoids rAF cross-tab race)
    if (tabId) {
      const tabState = tabMapRef.current.get(tabId);
      if (tabState) {
        tabState.messages = tabState.messages.map((msg) => ({
          ...msg,
          content: msg.content.map((block) =>
            block.type === 'cmd_permission_request' && block.requestId === requestId
              ? { ...block, decision }
              : block,
          ),
        }));
        tabState.pendingPermissionRequestId = null;
      }
      // Mirror the decided state into the STORE (display source for TabView).
      const permStore = messageStoreRegistry.get(tabId);
      if (permStore) {
        const target = permStore.messages.find((m) =>
          m.content.some((b) => b.type === 'cmd_permission_request' && b.requestId === requestId));
        if (target) {
          permStore.updateById(target.id, (msg) => ({
            ...msg,
            content: msg.content.map((block) =>
              block.type === 'cmd_permission_request' && block.requestId === requestId
                ? { ...block, decision }
                : block,
            ),
          }));
        }
      }
    }

    // Approve AND deny both stream through /cmd-permission-continue: the backend
    // signals the decision to the blocked dangerous_command_gate hook, which
    // returns permissionDecision:allow|deny to the SDK. On DENY the SDK receives
    // the refusal as the tool_result and the model CONTINUES — acknowledging the
    // denial and (usually) proposing an alternative — instead of the turn being
    // hard-killed (the old deny branch called the non-streaming endpoint +
    // interrupt(), which left the tab at "Denied" with no agent response, an
    // ambiguous dead-looking stop). The denied command is NOT re-runnable: the
    // hook only calls approve_command() on approve, so a model retry of the same
    // command re-triggers a fresh approval prompt (security_hooks.py:649).
    //
    // Convergence (not a new deny path): the two decisions share ONE streaming
    // path — the only difference is the `decision` value threaded at :streamCmd
    // below. All race/cross-tab guards above (permissionLoadingTabs double-click,
    // isStreaming re-entry return, incrementStreamGen stale-stream kill,
    // capturedTabId closure) therefore protect deny identically to approve.
    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from permission_needed → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = (Date.now() + 1).toString();
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    // SINGLE-WRITER: route through the store so streaming deltas land
    // (see insertOptimisticMessages).
    insertOptimisticMessages(tabId, [assistantPlaceholder]);

    // Capture tabId for cleanup in async callbacks (closure safety).
    // Create the stream handler ONCE now (captures tabId at creation time)
    // instead of calling wrappedCreateStreamHandler on every SSE event
    // (which would re-read activeTabIdRef.current and get the wrong tab).
    const capturedTabId = tabId;
    const streamHandler = createStreamHandler(assistantMessageId, capturedTabId);
    const abort = chatService.streamCmdPermissionContinue(
      { sessionId: tabSessionId, requestId, decision, enableSkills, enableMCP },
      // All events go to the standard stream handler — the backend never
      // emits 'cmd_permission_acknowledged', so no special-casing needed.
      streamHandler,
      (error) => { createErrorHandler(assistantMessageId, capturedTabId)(error); if (capturedTabId) permissionLoadingTabs.current.delete(capturedTabId); },
      () => { createCompleteHandler(capturedTabId)(); if (capturedTabId) permissionLoadingTabs.current.delete(capturedTabId); },
      createDisconnectHandler(capturedTabId),
      streamLiveness,
    );

    // Store abort function in the tab map for per-tab stop isolation.
    if (tabId) {
      updateTabState(tabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
        hasReceivedData: false,
        isReconnecting: false,
        reconnectionAttempt: 0,
      });
    }
  };

  // ── Stable callback identities for memoized MessageBubble ──────────────
  // handleAnswerQuestion and handlePermissionDecision are intentionally NOT
  // useCallback-wrapped (their bodies close over many values and live in a
  // regression-prone area). To let React.memo(MessageBubble) short-circuit
  // historical bubbles, we expose stable wrappers via the latest-ref pattern:
  // identity never changes, but each call invokes the freshest impl — zero
  // stale-closure risk and no dependency array to get wrong.
  const handleAnswerQuestionRef = useRef(handleAnswerQuestion);
  handleAnswerQuestionRef.current = handleAnswerQuestion;
  const stableHandleAnswerQuestion = useCallback(
    (toolUseId: string, answers: Record<string, string>) =>
      handleAnswerQuestionRef.current(toolUseId, answers),
    [],
  );

  const handlePermissionDecisionRef = useRef(handlePermissionDecision);
  handlePermissionDecisionRef.current = handlePermissionDecision;
  const stableHandlePermissionDecision = useCallback(
    (requestId: string, decision: 'approve' | 'deny') =>
      handlePermissionDecisionRef.current(requestId, decision),
    [],
  );

  // Handle stop — UI feedback is SYNCHRONOUS (no waiting for backend).
  // Backend stop is fire-and-forget (best effort). This eliminates the
  // race window where error events from the interrupted stream leak
  // through before the UI has updated.
  const handleStop = () => {
    const currentTabId = activeTabIdRef.current;
    const tabSessionId = currentTabId ? tabMapRef.current.get(currentTabId)?.sessionId : undefined;
    if (!tabSessionId) return;

    // 1. Mark stopped + abort SSE FIRST (synchronous, before any async work)
    if (currentTabId) {
      const tabState = tabMapRef.current.get(currentTabId);
      if (tabState) {
        tabState.userStopped = true;
      }
      if (tabState?.abortController) {
        try { tabState.abortController.abort(); } catch { /* already aborted */ }
        tabState.abortController = null;
      }
    }

    // 2. End store streaming — unblocks replace()/reconcile() for this tab.
    // MUST NOT trigger notification here (endStreaming flushes reconcile thunk
    // which could async-notify during tab switch → cross-tab leak).
    // Safe: endStreaming() only notifies if reconcile thunk was pending AND
    // async fetch completes. The actual notification is async (not immediate).
    if (currentTabId) {
      const stopStorePhase = messageStoreRegistry.get(currentTabId);
      if (stopStorePhase) stopStorePhase.endStreaming();
    }

    // 3. Update streaming state immediately — don't wait for backend
    setIsStreaming(false, currentTabId ?? undefined);
    incrementStreamGen();
    if (currentTabId) updateTabStatus(currentTabId, 'idle');

    // 4. Append "Stopped" indicator to messages (synchronous)
    setMessages((prev) => {
      const lastAssistantIndex = prev.reduce(
        (lastIdx, m, i) => m.role === 'assistant' ? i : lastIdx, -1,
      );
      if (lastAssistantIndex >= 0) {
        const updated = [...prev];
        const lastMsg = { ...updated[lastAssistantIndex] };
        lastMsg.content = [
          ...lastMsg.content,
          { type: 'text' as const, text: '\n\n---\n*Stopped*' },
        ];
        updated[lastAssistantIndex] = lastMsg;
        return updated;
      }
      // Edge case: no assistant message exists — fall back to appending a new one
      return [...prev, {
        id: Date.now().toString(),
        role: 'assistant' as const,
        content: [{ type: 'text' as const, text: '\n\n---\n*Stopped*' }],
        timestamp: new Date().toISOString(),
      }];
    });

    // 4. Append "Stopped" to tabMapRef (synchronous, no rAF race)
    // MUST stay as direct tabState.messages write — store.updateById triggers
    // rAF-deferred subscription notification. During that delay the user can
    // switch tabs, causing the subscription to push this tab's messages into
    // the new tab's React state (cross-tab leak, P0 regression 2026-06-18).
    if (currentTabId) {
      const tabState = tabMapRef.current.get(currentTabId);
      if (tabState && tabState.messages && tabState.messages.length > 0) {
        const lastIdx = tabState.messages.reduce(
          (acc: number, m: Message, i: number) => m.role === 'assistant' ? i : acc, -1,
        );
        if (lastIdx >= 0) {
          const updated = [...tabState.messages];
          const lastMsg = { ...updated[lastIdx] };
          lastMsg.content = [
            ...lastMsg.content,
            { type: 'text' as const, text: '\n\n---\n*Stopped*' },
          ];
          updated[lastIdx] = lastMsg;
          tabState.messages = updated;
        }
      }
    }

    // 4b. Mirror the Stopped marker into the per-tab STORE — the display source
    // for the keep-mounted TabView. Safe now (post keep-mounted): each TabView
    // subscribes to its OWN store, and the shared-messages bridge re-subscribes
    // per active tab, so a late rAF notify cannot push this tab's content into
    // another tab's view (the 2026-06-18 cross-tab leak vector no longer exists).
    if (currentTabId) {
      const stopStore = messageStoreRegistry.get(currentTabId);
      if (stopStore && stopStore.messages.some((m) => m.role === 'assistant')) {
        stopStore.updateLast(
          (m) => ({ ...m, content: [...m.content, { type: 'text' as const, text: '\n\n---\n*Stopped*' }] }),
          (m) => m.role === 'assistant',
        );
      }
    }

    // 5. Backend stop — fire-and-forget (best effort, don't await)
    chatService.stopSession(tabSessionId).catch((err) => {
      console.warn('[handleStop] Backend stop failed (best effort):', err);
    });

    // 6. Drain site B: auto-send queued message after stop
    if (currentTabId) {
      setTimeout(() => drainQueuedMessage(currentTabId), 0);
    }
  };

  // --- Context Refresh (Same-Tab Restart) ---
  const handleRefreshContext = useCallback(async () => {
    if (!sessionId || isStreaming) return;
    setIsRefreshing(true);
    try {
      await chatService.refreshSession(sessionId);
      // Append a system separator (refresh- prefix → NOT a resume boundary, so
      // it does not dim prior messages). Route through the MessageStore — a
      // setMessages-only write would be clobbered by the store→React sync.
      insertOptimisticMessages(activeTabIdRef.current, [
        {
          id: `refresh-${Date.now()}`,
          role: 'system' as const,
          content: [{ type: 'text' as const, text: 'Context refreshed' }],
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Refresh failed';
      addToast({ severity: 'warning', message: msg.includes('active') ? 'Cannot refresh while the AI is busy. Stop or answer first.' : `Refresh failed: ${msg}`, autoDismiss: true });
    } finally {
      setIsRefreshing(false);
      setShowRefreshModal(false);
    }
  }, [sessionId, isStreaming, addToast, insertOptimisticMessages, activeTabIdRef]);


  // Handle agent save
  const handleSaveAgent = async (agent: Agent | AgentCreateRequest) => {
    if ('id' in agent) {
      await agentsService.update(agent.id, agent);
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    }
  };

  // 🔔 Attention queue — polled ONCE here (single 30s poll). Feeds the Alerts
  // "Needs You" pill badge (count). 2026-08-08: unified backend authority
  // (GET /api/attention) via useAttentionQueue — replaces the old 3-source
  // frontend merge (paused+jobs+waiting-tabs); the waiting/streaming coupling is
  // GONE (Need You is decoupled from chat). Clicking the pill opens the fullscreen
  // needs-you overlay, which loads + dispatches items itself.
  const { counts: attentionCounts } = useAttentionQueue();
  const attentionCount = attentionCounts.blocking + attentionCounts.review;

  // 🔔 Alerts pill lives in the LEFT SIDEBAR (run_2bdc68ad), not the tab row —
  // the attention signal is GLOBAL (cross-tab / paused runs / failing jobs), so
  // it belongs on the fixed-width left chrome, unaffected by Canvas open/close.
  // But useRadarAttention (+ its openTabs/currentSessionId dependency) and the
  // action callbacks (handleItemClick / selectTab) are OWNED here by ChatPage;
  // the sidebar (ThreeColumnLayout's LeftSidebar) is a SIBLING tree with no
  // access to them. So we keep the hook + callbacks here and createPortal the
  // pill INTO the sidebar's #sidebar-alerts-slot node. This keeps a SINGLE poll,
  // uses the LIVE callbacks (no closure-snapshot / stale-tab OT01 trap), and adds
  // zero new cross-tree event (Gate-1 verified: lifting the hook up would instead
  // require bridging tab-state INTO the sidebar — net more mechanism).
  const [alertsSlot, setAlertsSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    // The slot is rendered by LeftSidebar, which renders BEFORE ChatPage in the
    // same ThreeColumnLayout pass, so this effect (post-commit) normally finds it
    // on the first synchronous try. The bounded rAF retries are belt-and-suspenders
    // for a ChatPage remount where the sidebar hasn't repainted yet — they close
    // the "portal silently renders nothing" tail (Gate-1 #2 + REVIEW Finding 2)
    // without an unbounded poll.
    let raf = 0;
    let tries = 0;
    const MAX_TRIES = 5;
    const resolve = () => {
      const el = document.getElementById('sidebar-alerts-slot');
      if (el) { setAlertsSlot(el); return; }
      if (tries++ < MAX_TRIES) raf = requestAnimationFrame(resolve);
    };
    resolve();
    return () => { if (raf) cancelAnimationFrame(raf); };
  }, []);

  // Render
  return (
    <ChatDropZone addFiles={addFiles} addWorkspaceFiles={addWorkspaceFiles}>
    <div className="flex-1 flex flex-col min-h-0">
      <ChatHeader
        openTabs={openTabs}
        activeTabId={activeTabId}
        onTabSelect={handleTabSelect}
        onTabClose={handleTabClose}
        onNewSession={handleNewSession}
        tabStatuses={tabStatuses}
        isNewTabDisabled={openTabs.length >= maxTabsInfo.chatMax}
        outputCount={canvas.outputCount}
        lastSeenOutputCount={canvas.lastSeenOutputCount}
        canvasOpen={canvas.isOpen}
        onOpenCanvas={() => window.dispatchEvent(new CustomEvent('swarm:open-canvas'))}
      />

      {/* 🔔 Alerts pill — portaled into the left-sidebar slot (run_2bdc68ad).
          Rendered here (not in the sidebar) so it uses ChatPage's live poll +
          callbacks directly; createPortal only relocates the DOM node, the pill
          stays a ChatPage child in the React tree (fresh props every render).
          Overlay-aware wrappers (meta-review): the sidebar (and thus this pill)
          stays clickable UNDER an open fullscreen domain overlay, whose scrim
          covers the chat area. So acting on an alert — injecting a prompt or
          switching tabs — must first return to chat (dispatch back-to-chat, which
          every overlay closes on), else the user acts but keeps seeing the overlay,
          not the result. Fires only when an overlay is actually open. */}
      {alertsSlot && createPortal(
        <AlertsPill count={attentionCount} />,
        alertsSlot,
      )}

      {/* `relative` makes this content row (chat body + Canvas, BELOW ChatHeader) the
          OverlayHost's positioning parent — so the host's `absolute inset:0` scrim covers
          only the chat body and NOT the tab strip above it (2026-08-06 relocate: the host
          used to fill MainChatPanel and occlude ChatHeader). */}
      <div className="relative flex flex-1 overflow-hidden">
        {/* Delete Confirmation Dialog */}
        <ConfirmDialog
          isOpen={!!deleteConfirmSession}
          title={t('chat.deleteSession')}
          message={t('chat.deleteSessionConfirm')}
          confirmText={t('common.button.delete')}
          cancelText={t('common.button.cancel')}
          variant="danger"
          onConfirm={() => deleteConfirmSession && handleDeleteSession(deleteConfirmSession)}
          onClose={() => setDeleteConfirmSession(null)}
        />

        {/* R6b: Close-while-streaming Confirmation Dialog */}
        <ConfirmDialog
          isOpen={!!closeConfirmTab}
          title={t('chat.closeStreamingTab', 'Close tab?')}
          message={t(
            'chat.closeStreamingTabConfirm',
            'This tab is still generating a response. Closing it will stop the response. Close anyway?',
          )}
          confirmText={t('chat.closeAnyway', 'Close anyway')}
          cancelText={t('common.button.cancel')}
          variant="warning"
          onConfirm={confirmCloseStreamingTab}
          onClose={() => setCloseConfirmTab(null)}
        />

        {/* Main Chat Area.
            `contain:layout` (run_26172836) SEVERS the layout coupling between this
            column and the Canvas (FileViewerPanel) — its sibling in the parent flex
            row above. Root fix for the recurring "Canvas 开着时 chat input 输入卡死":
            the chat textarea's autogrow (on older WebKit without field-sizing) writes
            style.height and reads scrollHeight, forcing a synchronous flush of the
            shared row that re-lays-out the large Canvas surface. Layout containment on
            THIS column (the reflow SOURCE) means its internal reflow can no longer
            propagate to the row/Canvas sibling — belt-and-suspenders with the
            field-sizing elimination in ChatInput (which removes the reflow entirely on
            modern WebKit). The prior fix contained the Canvas side (the amplifier); this
            contains the source side (Gate-1 finding). Safe: the column is flex-1 +
            overflow-hidden, so its size is imposed by the row, not by content — plain
            `contain:layout` (NOT size) needs no explicit dimensions. */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden [contain:layout]">
          <ErrorBoundary variant="tab">
          {agentLoadError ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center max-w-md">
                <span className="material-symbols-outlined text-6xl text-red-500 mb-4">error</span>
                <h2 className="text-xl font-semibold text-[var(--color-text)] mb-2">{t('chat.agentLoadFailed', 'Failed to Load Agent')}</h2>
                <p className="text-[var(--color-text-muted)] mb-4">{agentLoadError}</p>
                <button
                  onClick={() => window.location.reload()}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
                >
                  <span className="material-symbols-outlined">refresh</span>
                  {t('common.button.retry', 'Retry')}
                </button>
              </div>
            </div>
          ) : !selectedAgentId ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <Spinner size="lg" />
                <p className="text-[var(--color-text-muted)] mt-4">{t('chat.loadingAgent', 'Loading agent...')}</p>
              </div>
            </div>
          ) : isLoadingHistory || !messagesReady ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <Spinner size="lg" />
                <p className="text-[var(--color-text-muted)] mt-4">{t('common.status.loading')}</p>
              </div>
            </div>
          ) : (
            <>
              {/* Messages — one keep-mounted TabView per open tab (Step 5.1).
                  Only the active tab is visible (display:none otherwise); each
                  reads its OWN per-tab MessageStore, so switching is a pure
                  visibility toggle — zero remount, zero markdown re-parse.
                  Per-tab values come from tabMapRef (authoritative); the active
                  tab additionally uses the freshest React-state values. */}
              {openTabs.map((tab) => {
                const ts = tabMapRef.current.get(tab.id);
                const active = tab.id === activeTabId;
                return (
                  <TabView
                    key={tab.id}
                    tabId={tab.id}
                    isActive={active}
                    messages={ts?.messages ?? []}
                    sessionId={active ? sessionId : ts?.sessionId}
                    isStreaming={active ? isStreaming : !!ts?.isStreaming}
                    pendingQuestion={active ? pendingQuestion : (ts?.pendingQuestion ?? null)}
                    activeTabPendingQuestion={ts?.pendingQuestion ?? null}
                    pendingPermissionRequestId={active ? pendingPermissionRequestId : (ts?.pendingPermissionRequestId ?? null)}
                    contextWarning={active ? contextWarning : (ts?.contextWarning ?? null)}
                    isReconnecting={ts?.isReconnecting}
                    isResuming={ts?.isResuming}
                    isWaitingForBusy={active ? isWaitingForBusy : false}
                    isBackendOffline={health.status === 'disconnected'}
                    onCancelBusyWait={cancelBusyWait}
                    hasMoreMessages={active ? hasMoreMessages : false}
                    isLoadingOlderMessages={active ? isLoadingOlderMessages : false}
                    onLoadOlder={loadOlderMessages}
                    onAnswerQuestion={stableHandleAnswerQuestion}
                    onPermissionDecision={stableHandlePermissionDecision}
                    onEscalationSelect={handleEscalationSelect}
                    onCancelQueued={handleCancelQueued}
                    onContinue={handleContinue}
                    onFocusClick={handleFocusClick}
                    onItemClick={handleItemClick}
                    onRetryQueueTimeout={handleRetryQueueTimeout}
                  />
                );
              })}

              {/* Rate limit countdown indicator */}
              {isLimited('/chat') && chatRateLimitCountdown > 0 && (
                <div className="px-4 py-2 text-sm text-yellow-400 flex items-center gap-2">
                  <span className="material-symbols-outlined text-base">schedule</span>
                  Rate limited — resuming in {chatRateLimitCountdown}s
                </div>
              )}

              {/* Input Area */}
              <ChatInput
                inputValue={inputValue}
                onInputChange={setInputValue}
                onSend={handleSendMessage}
                onStop={handleStop}
                isStreaming={isStreaming}
                isExpanded={isExpanded}
                onExpandedChange={setIsExpanded}
                selectedAgentId={selectedAgentId}
                attachments={attachments}
                onAddFiles={addFiles}
                onCaptureError={(msg) => addToast({ severity: 'error', message: msg, autoDismiss: true })}
                onRemoveFile={removeAttachment}
                isProcessingFiles={isProcessingFiles}
                fileError={fileError}
                canAddMore={canAddMore}
                sessionId={sessionId}
                contextPct={contextWarning?.pct ?? null}
                promptMetadata={promptMetadata}
                disabled={health.status === 'disconnected' || isLimited('/chat') || isWaitingForBusy || isRefreshing}
                activeTabIdRef={activeTabIdRef}
                inputValueMapRef={inputValueMapRef}
                onInputValueChange={(tabId: string, value: string) => {
                  inputValueMapRef.current.set(tabId, value);
                }}
                isLikelyStalled={isLikelyStalled}
                onRefreshContext={() => setShowRefreshModal(true)}
                skills={skills}
                voiceConversationState={voiceConversation.state}
                onVoiceConversationToggle={voiceConversation.toggle}
                onVoiceConversationInterrupt={voiceConversation.interrupt}
              />
            </>
          )}
          </ErrorBoundary>
        </div>

        {/* Canvas — the session's output surface (bugs 2+3). Mounted HERE, inside
            the flex row BELOW ChatHeader and to the RIGHT of the chat area, so it
            visibly belongs to the CURRENT tab (the tab bar spans above it). State
            is per-tab via useCanvasHost. Renders only when this tab has a file OR
            Canvas was manually opened. */}
        {canvas.isOpen && (
          <FileViewerPanel
            // tabScopeKey (NOT a React `key`): FileViewer keeps its OWN internal
            // tab list (useFileViewerTabs, append-only) that must clear on chat-tab
            // switch so tab A's files don't bleed into tab B. Passing activeTabId
            // as tabScopeKey clears that list WITHOUT remounting — a `key` remount
            // (the first cut) replayed the 220ms width-reveal AND dropped the
            // content cache on every switch (E2E finding, run_0fb40bbc).
            tabScopeKey={activeTabId ?? undefined}
            initialFile={canvas.file ?? undefined}
            onClose={canvas.close}
            pinned={canvas.pinned}
            onTogglePin={canvas.togglePin}
            muted={canvas.muted}
            onToggleMute={canvas.toggleMute}
            referencedFiles={canvas.referencedFiles}
            // Bug 2: per-tab collapse state (railed/outputsCollapsed) — stable refs
            // (memoized slice + useCallback) so the load-bearing memo below is intact.
            collapse={canvas.collapse}
            setCollapse={canvas.setCollapse}
          />
        )}

        {/* SwarmRadar removed (2026-08-02): all 3 sections have live replacements —
            Changes → Canvas (CanvasOutputRail), Attention → ChatHeader AlertsPill
            (same useRadarAttention poll → attentionItems), Jobs & Runs → left-nav
            overlay (swarm:show-jobs). Right column width reclaimed for chat. */}

        {/* History migrated to OverlayHost registry (M3) — self-fetches its reactive
            session data; resume/delete/agentId reach it via the ctx bridge. */}

        {/* ToDo / Jobs & Runs / Pipeline / Pollinate migrated to the OverlayHost
            registry (M4, overlaySurfaces.tsx). Dispatch reaches them via the ctx
            bridge: ToDo uses dispatchTodo (work-packet), the other three use
            dispatchPrompt — both ChatPage-owned (handleDispatchTodo /
            handleDispatchJobPrompt), published through setOverlayCtxBridge. */}

        {/* OverlayHost — the single fullscreen-surface host. Mounted HERE (inside the
            content row, below ChatHeader) rather than in MainChatPanel, so its
            `absolute inset:0` scrim is scoped to the chat body and the tab strip stays
            visible + clickable under an open overlay (2026-08-06). Logically decoupled:
            reads only OverlayContext + ExplorerContext (both wrap ChatPage), no ChatPage
            state — a DOM descendant, never a state child. */}
        <OverlayHost />
      </div>

      {/* Modals */}
      <FilePreviewModal isOpen={!!previewFile} onClose={() => setPreviewFile(null)} agentId={selectedAgentId || ''} file={previewFile} basePath={effectiveBasePath} />
      <AgentFormModal isOpen={isEditAgentOpen} onClose={() => setIsEditAgentOpen(false)} onSave={handleSaveAgent} agent={selectedAgent} />
      <RefreshContextModal
        isOpen={showRefreshModal}
        onClose={() => setShowRefreshModal(false)}
        onConfirm={handleRefreshContext}
        isLoading={isRefreshing}
      />
    </div>
    </ChatDropZone>
  );
}
