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
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import type { Message, ContentBlock, StreamEvent, Agent, AgentCreateRequest, ChatSession, SystemPromptMetadata } from '../types';
import { chatService } from '../services/chat';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { pluginsService } from '../services/plugins';
import { workspaceService } from '../services/workspace';
import { tasksService } from '../services/tasks';
import { Spinner, ConfirmDialog, AgentFormModal, ErrorBoundary } from '../components/common';
import { useToast } from '../contexts/ToastContext';
import { useHealth } from '../contexts/HealthContext';
import { useSessionMeta } from '../contexts/LayoutContext';
import { EvolutionMessage, ChatErrorMessage } from '../components/chat';
import { ChatDropZone } from '../components/chat/ChatDropZone';
import type { EvolutionEventType } from '../services/evolution';
import { FilePreviewModal } from '../components/workspace/FilePreviewModal';
import { useRateLimiter, useRateLimitCountdown } from '../hooks';
import { useUnifiedAttachments } from '../hooks/useUnifiedAttachments';
import { useTSCCState } from '../hooks/useTSCCState';
import { useUnifiedTabState, MAX_OPEN_TABS } from '../hooks/useUnifiedTabState';
import { useChatStreamingLifecycle, formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS } from '../hooks/useChatStreamingLifecycle';
import { ChatHeader, ChatInput, MessageBubble, WelcomeScreen } from './chat/components';
import { RadarSidebar } from './chat/components/RightSidebar';

import { groupSessionsByTime } from './chat/utils';
import { EXPLORER_ATTACH_FILE, EXPLORER_ASK_ABOUT_FILE } from '../constants/explorerEvents';
import { CLAUDE_NATIVE_IMAGE_MIMES } from '../utils/fileClassification';

/**
 * Re-export ``deriveStreamingActivity`` and ``MAX_OPEN_TABS`` from the
 * extracted hooks so existing test imports (``from '../pages/ChatPage'``)
 * continue to resolve.
 */
export { deriveStreamingActivity, formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS, MIN_ACTIVITY_DISPLAY_MS } from '../hooks/useChatStreamingLifecycle';
export { MAX_OPEN_TABS } from '../hooks/useUnifiedTabState';

/** Convert a backend ChatMessage to the frontend Message shape. */
function toDisplayMessage(msg: { id: string; role: string; content: ContentBlock[]; createdAt: string; model?: string }): Message {
  return {
    id: msg.id,
    role: msg.role as 'user' | 'assistant',
    content: msg.content as ContentBlock[],
    timestamp: msg.createdAt,
    model: msg.model,
  };
}

export default function ChatPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { addToast } = useToast();
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
  /** Per-tab draft text storage — NOT serialized to open_tabs.json to avoid large text writes. */
  const inputValueMapRef = useRef<Map<string, string>>(new Map());
  /** Ref mirror of isExpanded for synchronous reads in handleTabSelect (avoids dep array churn). */
  const isExpandedRef = useRef(isExpanded);
  isExpandedRef.current = isExpanded;
  const [hasMoreMessages, setHasMoreMessages] = useState(true);
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);

  // Per-tab permission loading guard — prevents double-click during API call.
  // Keyed by tabId so parallel tabs don't block each other.
  const permissionLoadingTabs = useRef(new Set<string>());
  const [deleteConfirmSession, setDeleteConfirmSession] = useState<ChatSession | null>(null);
  const [isEditAgentOpen, setIsEditAgentOpen] = useState(false);

  // File preview state
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);

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
  const groupedSessions = useMemo(() => groupSessionsByTime(sessions), [sessions]);
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
  } = useUnifiedTabState(selectedAgentId || 'default');

  // File attachment — unified hook replaces both useFileAttachment and LayoutContext.attachedFiles
  const { attachments, addFiles, addWorkspaceFiles, removeAttachment, clearAll: clearAttachments,
    isProcessing: isProcessingFiles, error: fileError, canAddMore } = useUnifiedAttachments(
    activeTabId, tabMapRef
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
    displayedActivity,
    elapsedSeconds,
    pendingStreamTabs,
    clearPendingStreamTab,
    bumpStreamingDerivation,
    messagesEndRef,
    incrementStreamGen,
    userScrolledUpRef,
    resetUserScroll,
    createStreamHandler,
    createCompleteHandler,
    createErrorHandler,
    contextWarning,
    setContextWarning,
    clearContextWarning: _clearContextWarning,
    promptMetadata,
    setPromptMetadata,
  } = useChatStreamingLifecycle({
    queryClient,
    getSession: (sid: string) => chatService.getSession(sid),
    getTabState,
    updateTabState,
    updateTabStatus,
    tabMapRef,
    activeTabIdRef,
  });

  // TSCC state management — lifecycle state and UI preferences only.
  // System prompt metadata is now delivered via SSE and managed by useChatStreamingLifecycle.
  useTSCCState(sessionId ?? null);

  // Last assistant message index — memoized for Save-to-Memory button placement
  const lastAssistantIdx = useMemo(
    () => messages.reduce((lastIdx, m, i) => m.role === 'assistant' ? i : lastIdx, -1),
    [messages],
  );

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

  // Track currently-open file in editor panel — included in chat requests
  // so the agent knows what doc the user is viewing.
  const editorContextRef = useRef<{ filePath: string; fileName: string } | null>(null);
  useEffect(() => {
    const handler = (e: Event) => {
      editorContextRef.current = (e as CustomEvent).detail ?? null;
    };
    window.addEventListener('swarm:editor-file-changed', handler);
    return () => window.removeEventListener('swarm:editor-file-changed', handler);
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
    setIsLoadingHistory(true);
    try {
      const sessionMessages = await chatService.getSessionMessagesPaginated(sid, 50);
      // Async guard: discard if a newer load was started while we awaited
      if (loadGenRef.current !== thisGen) return;
      const formattedMessages: Message[] = sessionMessages.map(toDisplayMessage);
      setMessages(formattedMessages);
      setSessionId(sid);
      setPendingQuestion(null);
      setHasMoreMessages(sessionMessages.length === 50);
      // Sync loaded messages back into the tab map so subsequent tab switches
      // don't see empty messages and re-fetch unnecessarily.
      // GUARD: Never overwrite a streaming tab's messages — the stream handler
      // has newer content than the backend fetch (which returns last-committed state).
      const currentTabId = activeTabIdRef.current;
      if (currentTabId) {
        const tab = tabMapRef.current.get(currentTabId);
        if (tab && tab.sessionId === sid && !tab.isStreaming) {
          tab.messages = formattedMessages;
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

  // Load older messages for infinite scroll (paginated)
  const loadOlderMessages = useCallback(async () => {
    if (!sessionId || !hasMoreMessages || isLoadingOlderMessages) return;
    const oldestMessage = messagesRef.current[0];
    if (!oldestMessage) return;

    setIsLoadingOlderMessages(true);
    try {
      const olderMessages = await chatService.getSessionMessagesPaginated(
        sessionId, 50, oldestMessage.id
      );
      if (olderMessages.length < 50) setHasMoreMessages(false);
      // Capture scroll height before prepending for position preservation
      const container = messagesContainerRef.current;
      if (container) prevScrollHeightRef.current = container.scrollHeight;
      setMessages(prev => [...olderMessages.map(toDisplayMessage), ...prev]);
    } finally {
      setIsLoadingOlderMessages(false);
    }
  }, [sessionId, hasMoreMessages, isLoadingOlderMessages]);

  // Handle new chat
  const handleNewChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
    // Note: Sidebar visibility is now managed by toggle buttons, no need to collapse
  }, []);


  // Handle new session - creates new tab with "New Session" title (Req 2.2, 2.3)
  // Fix 6: Save current tab state before creating new tab, initialize new tab in per-tab map
  // Fix 7: Guard against exceeding MAX_OPEN_TABS
  const handleNewSession = useCallback(() => {
    if (!selectedAgentId) return;
    if (tabMapRef.current.size >= MAX_OPEN_TABS) {
      addToast({ severity: 'info', message: 'Maximum tabs reached. Close a tab to open a new one.', autoDismiss: true });
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
  }, [selectedAgentId, addTab, initTabState, tabMapRef, updateTabState, activeTabIdRef, setIsStreaming, setContextWarning]);

  // Handle tab selection - switches active tab and loads session messages (Req 1.6)
  // Fix 6: Save current tab state, restore target tab state from per-tab map
  const handleTabSelect = useCallback(async (tabId: string) => {
    const tab = openTabs.find(t => t.id === tabId);
    if (!tab) return;
    
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
          setPendingQuestion(null);
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
        // double-rAF scroll restore fires.
        userScrolledUpRef.current = true;

        setMessages(tabState.messages);
        setSessionId(tabState.sessionId);
        setPendingQuestion(tabState.pendingQuestion);
        setPendingPermissionRequestId(tabState.pendingPermissionRequestId ?? null);
        setContextWarning(tabState.contextWarning ?? null);
        setPromptMetadata(tabState.promptMetadata ?? null);
        setIsExpanded(tabState.isExpanded ?? false);
        setInputValue(inputValueMapRef.current.get(tabId) ?? '');
        // isStreaming derivation automatically reflects target tab's state
        // from tabMapRef — no need to call setIsStreaming which would corrupt
        // the source tab's streaming state. Just bump to re-derive.
        bumpStreamingDerivation();

        // Guard 2: Double-rAF scroll restore — ensures React has committed
        // new messages to the DOM before setting scrollTop.
        const savedScrollPosition = tabState.scrollPosition;
        const restoreTabId = tabId; // capture for closure
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            // Async guard: if user switched tabs during rAF delay, no-op
            if (activeTabIdRef.current !== restoreTabId) return;

            const container = messagesContainerRef.current;
            if (!container) return;

            if (savedScrollPosition !== undefined) {
              container.scrollTop = savedScrollPosition;
            } else {
              // New tab or no saved position — scroll to bottom
              messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
            }

            // Guard 4: Recompute userScrolledUpRef based on restored position
            // to avoid stale auto-scroll suppression from the previous tab.
            const threshold = 100;
            const isNearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - threshold;
            userScrolledUpRef.current = !isNearBottom;
          });
        });
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

  // Handle tab close - removes tab, handles last-tab case (Req 3.3)
  // Fix 6: Clean up per-tab state map entry and abort controller
  // Fix 10: Stop backend session for streaming tabs, clean up pendingStreamTabs
  const handleTabClose = useCallback((tabId: string) => {
    // Read tab state before cleanup to determine if backend stop is needed
    const tab = tabMapRef.current.get(tabId);
    const tabSessionId = tab?.sessionId;
    const wasStreaming = tab?.isStreaming || pendingStreamTabs.has(tabId);

    // Clean up pendingStreamTabs entry for this tab (prevents stale entries)
    clearPendingStreamTab(tabId);
    // Clean up per-tab draft text to prevent unbounded memory growth
    inputValueMapRef.current.delete(tabId);

    // Let closeTab handle map deletion + auto-create of last tab.
    // Do NOT call cleanupTabState before closeTab — it deletes the tab
    // from the map, causing closeTab to early-return and skip the
    // "auto-create new tab when last one is closed" logic.
    closeTab(tabId);

    // Fire-and-forget backend stop for tabs that were actively streaming.
    if (wasStreaming && tabSessionId) {
      chatService.stopSession(tabSessionId).catch((err) => {
        console.warn('[handleTabClose] Failed to stop backend session:', err);
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
  }, [closeTab, clearPendingStreamTab, pendingStreamTabs, tabMapRef, activeTabIdRef, setMessages, setSessionId, setPendingQuestion, setContextWarning]);

  // Handle session selection
  const handleSelectSession = useCallback(async (session: ChatSession) => {
    if (session.agentId && session.agentId !== selectedAgentId) {
      setSelectedAgentId(session.agentId);
    }
    await loadSessionMessages(session.id);
    // Note: Sidebar visibility is now managed by toggle buttons, no need to collapse
  }, [selectedAgentId, loadSessionMessages]);

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
  const prevScrollHeightRef = useRef(0);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  /** Fix 2: Detect user scroll-up to suppress auto-scroll during streaming. */
  const handleMessagesScroll = useCallback(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const threshold = 100; // px from bottom
    const isNearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - threshold;
    userScrolledUpRef.current = !isNearBottom;

    // Infinite scroll: load older messages when scrolled to top
    if (el.scrollTop === 0) {
      loadOlderMessages();
    }
  }, [userScrolledUpRef, loadOlderMessages]);

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
  }, [messages]); // eslint-disable-line react-hooks/exhaustive-deps — fires after prepend

  // Log time-to-interactive when messagesReady becomes true (Req 8.4)
  useEffect(() => {
    if (messagesReady && mountTimeRef.current) {
      console.log(`[ChatPage] Time to interactive: ${(performance.now() - mountTimeRef.current).toFixed(0)}ms`);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messagesReady]);

  // Register the initial/default tab in the per-tab state map on mount.
  // Without this, the first tab has no entry in tabMapRef and all
  // per-tab features (message tracking, abort isolation, status) are broken.
  useEffect(() => {
    if (activeTabId && !tabMapRef.current.has(activeTabId)) {
      initTabState(activeTabId, messages.length > 0 ? messages : []);
    }
  }, [activeTabId]); // eslint-disable-line react-hooks/exhaustive-deps — mount-only for initial tab

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

      // Retry loop: backend sidecar may not be ready on first mount.
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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — mount-only

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
    });
    return () => setActiveSessionMeta(null);
  }, [activeTabTitle, contextPct, fileCount, agentName, setActiveSessionMeta]);

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



  // Update tab's sessionId when a new session is created
  useEffect(() => {
    if (sessionId && activeTabId) {
      // Read from the map directly (stable, avoids openTabs dependency)
      const tabState = tabMapRef.current.get(activeTabId);
      if (tabState && !tabState.sessionId) {
        updateTabSessionId(activeTabId, sessionId);
      }
    }
  }, [sessionId, activeTabId, updateTabSessionId, tabMapRef]);

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
            // File Picker files with path_hint have textContent but no workspacePath.
            // Fall back to inline_text delivery for these (content already read at attach time).
            if (att.textContent && !att.workspacePath) {
              content.push({
                type: 'text',
                text: `--- File: ${att.name} ---\n${att.textContent}\n--- End: ${att.name} ---`,
              } as ContentBlock);
            } else if (att.workspacePath) {
              content.push({
                type: 'text',
                text: `[Attached file: ${att.name}] saved at ${att.workspacePath} - use Read tool to access`,
              } as ContentBlock);
            } else if (att.base64) {
              // Audio/video files can't be sent as base64 documents to Claude.
              // Inform the agent about the file so it can suggest alternatives.
              content.push({
                type: 'text',
                text: `[Attached ${att.type} file: ${att.name} (${(att.size / (1024 * 1024)).toFixed(1)}MB) — binary file attached from system file picker, cannot be processed inline. Save to workspace and use tools to access.]`,
              } as ContentBlock);
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
    setMessages((prev) => [...prev, userMessage]);

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

    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [{ type: 'text', text: responseText }], timestamp: new Date().toISOString() }]);
    return true;
  }, [setMessages]);

  // Handle send message (Req 7.1 — memoized with useCallback, volatile deps via refs)
  const handleSendMessage = useCallback(async () => {
    const messageText = inputValueRef.current;
    const currentAttachments = attachmentsRef.current;
    const hasText = messageText.trim().length > 0;
    const hasAttachments = currentAttachments.some((a) => !a.error && !a.isLoading);

    if ((!hasText && !hasAttachments) || !selectedAgentId) return;

    // Per-tab streaming guard: check only the active tab's state
    const activeTabForGuard = tabMapRef.current.get(activeTabIdRef.current ?? '');
    if (activeTabForGuard?.isStreaming || pendingStreamTabs.has(activeTabIdRef.current ?? '')) return;

    // Set streaming flag IMMEDIATELY after guard passes to close the race
    // window between guard check and the old setIsStreaming call ~20 lines
    // below.  setIsStreaming synchronously mutates tabMapRef.isStreaming,
    // so a second rapid click/Enter will be caught by the guard above.
    setIsStreaming(true, activeTabIdRef.current ?? undefined);

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

    const userMessage: Message = { id: Date.now().toString(), role: 'user', content: userMessageContent, timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, userMessage]);
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

    const assistantMessageId = (Date.now() + 1).toString();
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, assistantPlaceholder]);

    // Ensure the active tab is registered in the per-tab state map BEFORE
    // creating the stream handler. Without this, capturedTabId would be null
    // and isActiveTab would become false once initTabState fires later.
    if (currentActiveTabId && !tabMapRef.current.has(currentActiveTabId)) {
      initTabState(currentActiveTabId, messagesRef.current);
    }

    // Sync user message + assistant placeholder to tabMapRef (authoritative store).
    // Without this, the stream handler's updateMessages() can't find the
    // assistantMessageId in tabState.messages, so streaming content is silently
    // dropped for background tabs. When switching back, tabState.messages would
    // be stale — missing the entire current conversation turn.
    if (currentActiveTabId) {
      const tabState = tabMapRef.current.get(currentActiveTabId);
      if (tabState) {
        tabState.messages = [...tabState.messages, userMessage, assistantPlaceholder];
      }
    }

    // Resolve sessionId: prefer tabMapRef (synchronously updated by
    // session_start handler) over sessionIdRef (async React state).
    // Closes the race window where sessionIdRef hasn't been committed
    // yet but tabMapRef already has the correct session ID.
    const resolvedSessionId = (
      (currentActiveTabId && tabMapRef.current.get(currentActiveTabId)?.sessionId)
      || sessionIdRef.current
    );

    const abort = chatService.streamChat(
      {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId: resolvedSessionId,
        enableSkills,
        enableMCP,
        ...(editorContextRef.current && { editorContext: editorContextRef.current }),
      },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, activeTabIdRef.current ?? undefined),
      createCompleteHandler(activeTabIdRef.current ?? undefined)
    );

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
        ...(editorContextRef.current && { editorContext: editorContextRef.current }),
      };
      const capturedTabIdForRetry = currentActiveTabId;
      const retryStreamFn = () => {
        return chatService.streamChat(
          { ...streamRequest, sessionId: tabMapRef.current.get(capturedTabIdForRetry)?.sessionId ?? streamRequest.sessionId },
          wrappedCreateStreamHandler(assistantMessageId),
          createErrorHandler(assistantMessageId, capturedTabIdForRetry),
          createCompleteHandler(capturedTabIdForRetry),
        );
      };

      updateTabState(currentActiveTabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
        hasReceivedData: false,
        isReconnecting: false,
        reconnectionAttempt: 0,
        retryStreamFn,
      });
    }
  }, [selectedAgentId, enableSkills, enableMCP, handlePluginCommand, buildContentArray, clearAttachments, resetUserScroll, incrementStreamGen, setIsStreaming, setMessages, setInputValue, updateTabStatus, updateTabTitle, setTabIsNew, initTabState, wrappedCreateStreamHandler, createErrorHandler, createCompleteHandler, activeTabIdRef, tabMapRef, pendingStreamTabs, queryClient, t]);

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (toolUseId: string, answers: Record<string, string>) => {
    const tabId = activeTabIdRef.current ?? undefined;
    const tabSessionId = tabId ? tabMapRef.current.get(tabId)?.sessionId : undefined;
    if (!selectedAgentId || !tabSessionId) return;

    // Defensive guard: prevent double-submit if already streaming.
    // The UI disables the button, but programmatic calls or rapid clicks
    // could bypass it. Read tabMapRef directly (synchronous, authoritative).
    const tabState = tabId ? tabMapRef.current.get(tabId) : undefined;
    if (tabState?.isStreaming) return;

    setPendingQuestion(null);
    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from waiting_input → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = Date.now().toString();
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, assistantPlaceholder]);

    // Sync assistant placeholder to tabMapRef so background stream handler
    // can find assistantMessageId in tabState.messages (same fix as handleSendMessage).
    if (tabId) {
      const tabState = tabMapRef.current.get(tabId);
      if (tabState) {
        tabState.messages = [...tabState.messages, assistantPlaceholder];
      }
    }

    const abort = chatService.streamAnswerQuestion(
      { agentId: selectedAgentId, sessionId: tabSessionId, toolUseId, answers, enableSkills, enableMCP },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, tabId),
      createCompleteHandler(tabId)
    );

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (tabId) {
      const capturedTabIdForRetry = tabId;
      const retryStreamFn = () => {
        return chatService.streamAnswerQuestion(
          { agentId: selectedAgentId, sessionId: tabMapRef.current.get(capturedTabIdForRetry)?.sessionId ?? tabSessionId, toolUseId, answers, enableSkills, enableMCP },
          wrappedCreateStreamHandler(assistantMessageId),
          createErrorHandler(assistantMessageId, capturedTabIdForRetry),
          createCompleteHandler(capturedTabIdForRetry),
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

    // Also update tabMapRef messages (authoritative source)
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
    }

    if (decision === 'deny') {
      try {
        await chatService.submitCmdPermissionDecision({ sessionId: tabSessionId, requestId, decision: 'deny' });
      } catch (error) {
        console.error('Failed to submit deny decision:', error);
      } finally {
        if (tabId) permissionLoadingTabs.current.delete(tabId);
        setIsStreaming(false, tabId);
      }
      return;
    }

    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from permission_needed → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = (Date.now() + 1).toString();
    const assistantPlaceholder: Message = { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, assistantPlaceholder]);

    // Sync assistant placeholder to tabMapRef so background stream handler
    // can find assistantMessageId in tabState.messages (same fix as handleSendMessage).
    if (tabId) {
      const tabState = tabMapRef.current.get(tabId);
      if (tabState) {
        tabState.messages = [...tabState.messages, assistantPlaceholder];
      }
    }

    // Capture tabId for cleanup in async callbacks (closure safety).
    // Create the stream handler ONCE now (captures tabId at creation time)
    // instead of calling wrappedCreateStreamHandler on every SSE event
    // (which would re-read activeTabIdRef.current and get the wrong tab).
    const capturedTabId = tabId;
    const streamHandler = createStreamHandler(assistantMessageId, capturedTabId);
    const abort = chatService.streamCmdPermissionContinue(
      { sessionId: tabSessionId, requestId, decision, enableSkills, enableMCP },
      (event: StreamEvent) => {
        if (event.type === 'cmd_permission_acknowledged') {
          // Sync removal to tabMapRef (authoritative store) so background
          // tab switches don't restore the stale placeholder ghost.
          if (capturedTabId) {
            const tabState = tabMapRef.current.get(capturedTabId);
            if (tabState) {
              tabState.messages = tabState.messages.filter((msg) => msg.id !== assistantMessageId);
            }
          }
          setMessages((prev) => prev.filter((msg) => msg.id !== assistantMessageId));
          setIsStreaming(false, capturedTabId);
        } else {
          streamHandler(event);
        }
      },
      (error) => { createErrorHandler(assistantMessageId, capturedTabId)(error); if (capturedTabId) permissionLoadingTabs.current.delete(capturedTabId); },
      () => { createCompleteHandler(capturedTabId)(); if (capturedTabId) permissionLoadingTabs.current.delete(capturedTabId); }
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

  // Handle stop
  const handleStop = async () => {
    const currentTabId = activeTabIdRef.current;
    const tabSessionId = currentTabId ? tabMapRef.current.get(currentTabId)?.sessionId : undefined;
    if (!tabSessionId) return;
    try {
      // Use the active tab's abort controller from the tab map (per-tab isolation)
      if (currentTabId) {
        const tabState = tabMapRef.current.get(currentTabId);
        if (tabState?.abortController) {
          try { tabState.abortController.abort(); } catch { /* already aborted */ }
          tabState.abortController = null;
        }
      }
      await chatService.stopSession(tabSessionId);
      // Preserve partial content: append stop indicator to the last assistant
      // message instead of creating a separate message (Requirement 3.3).
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
    } catch (error) {
      console.error('Failed to stop session:', error);
    } finally {
      setIsStreaming(false, currentTabId ?? undefined);
      // Update tab status to idle
      if (currentTabId) updateTabStatus(currentTabId, 'idle');
    }
  };


  // Handle agent save
  const handleSaveAgent = async (agent: Agent | AgentCreateRequest) => {
    if ('id' in agent) {
      await agentsService.update(agent.id, agent);
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    }
  };

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
      />

      <div className="flex flex-1 overflow-hidden">
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

        {/* Main Chat Area */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
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
              {/* Messages */}
              <div
                ref={messagesContainerRef}
                onScroll={handleMessagesScroll}
                className={messages.length === 0
                  ? 'flex-1 overflow-hidden flex flex-col'
                  : 'flex-1 overflow-y-auto pl-2 pr-4 py-3.5 space-y-2.5 min-w-0'
                }
              >
                {isLoadingOlderMessages && (
                  <div className="flex justify-center py-2">
                    <Spinner size="sm" />
                  </div>
                )}
                {messages.length === 0 ? (
                  <WelcomeScreen />
                ) : (
                  messages.map((msg, idx) => {
                    // Evolution events get their own renderer
                    if (msg.evolutionEvent) {
                      return (
                        <EvolutionMessage
                          key={msg.id}
                          eventType={msg.evolutionEvent.eventType as EvolutionEventType}
                          data={msg.evolutionEvent.data}
                        />
                      );
                    }
                    // Error messages get the structured error renderer
                    if (msg.isError) {
                      const textBlock = msg.content.find(b => b.type === 'text');
                      const errorText = textBlock && 'text' in textBlock ? textBlock.text : 'An error occurred';
                      return (
                        <ChatErrorMessage
                          key={msg.id}
                          error={{
                            code: (msg as unknown as Record<string, unknown>).errorCode as string | undefined,
                            message: errorText,
                            detail: (msg as unknown as Record<string, unknown>).errorDetail as string | undefined,
                            suggestedAction: (msg as unknown as Record<string, unknown>).suggestedAction as string | undefined,
                            retryAfter: (msg as unknown as Record<string, unknown>).retryAfter as number | undefined,
                          }}
                        />
                      );
                    }
                    // Only pass isStreaming to the last assistant message
                    const isLastAssistantForStreaming = isStreaming
                      && msg.role === 'assistant'
                      && idx === messages.length - 1;
                    return (
                      <MessageBubble
                        key={msg.id}
                        message={msg}
                        onAnswerQuestion={handleAnswerQuestion}
                        onPermissionDecision={handlePermissionDecision}
                        pendingToolUseId={pendingQuestion?.toolUseId}
                        pendingPermissionRequestId={pendingPermissionRequestId ?? undefined}
                        isStreaming={isLastAssistantForStreaming}
                        sessionId={sessionId}
                        isLastAssistant={idx === lastAssistantIdx}
                        contextWarning={contextWarning}
                      />
                    );
                  })
                )}
                {/* Reconnecting indicator — reads from tabMapRef (authoritative) for the active tab */}
                {activeTabIdRef.current && tabMapRef.current.get(activeTabIdRef.current)?.isReconnecting && (
                  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                    <Spinner size="sm" />
                    <span className="text-sm">{t('chat.reconnecting', 'Reconnecting...')}</span>
                  </div>
                )}
                {/* Resuming indicator — cold-start resume when subprocess was killed after long idle */}
                {activeTabIdRef.current && tabMapRef.current.get(activeTabIdRef.current)?.isResuming && (
                  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                    <Spinner size="sm" />
                    <span className="text-sm">{t('chat.resuming', 'Resuming session...')}</span>
                  </div>
                )}
                {isStreaming && !(activeTabIdRef.current && tabMapRef.current.get(activeTabIdRef.current)?.isResuming) && (
                  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                    <Spinner size="sm" />
                    <span className="text-sm">
                      {displayedActivity?.toolName
                        ? (displayedActivity.toolContext
                            ? t('chat.runningToolWithContext', {
                                tool: displayedActivity.toolName,
                                context: displayedActivity.toolContext,
                                count: displayedActivity.toolCount,
                              })
                            : displayedActivity.toolCount > 1
                              ? t('chat.runningToolWithCount', {
                                  tool: displayedActivity.toolName,
                                  count: displayedActivity.toolCount,
                                })
                              : t('chat.runningTool', { tool: displayedActivity.toolName }))
                        : displayedActivity?.hasContent
                          ? t('chat.processing')
                          : elapsedSeconds >= ELAPSED_DISPLAY_THRESHOLD_MS / 1000
                            ? t('chat.thinkingWithElapsed', { elapsed: formatElapsed(elapsedSeconds) })
                            : t('chat.thinking')}
                    </span>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

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
                onRemoveFile={removeAttachment}
                isProcessingFiles={isProcessingFiles}
                fileError={fileError}
                canAddMore={canAddMore}
                sessionId={sessionId}
                promptMetadata={promptMetadata}
                contextPct={contextWarning?.pct ?? null}
                disabled={health.status === 'disconnected' || isLimited('/chat')}
                activeTabIdRef={activeTabIdRef}
                inputValueMapRef={inputValueMapRef}
                onInputValueChange={(tabId: string, value: string) => {
                  inputValueMapRef.current.set(tabId, value);
                }}
              />
            </>
          )}
          </ErrorBoundary>
        </div>

        {/* Right Sidebar — persistent Radar panel */}
        <RadarSidebar
          tabMapRef={tabMapRef}
          activeTabIdRef={activeTabIdRef}
          openTabs={openTabs}
          tabStatuses={tabStatuses}
          onTabSelect={selectTab}
          inputValueMapRef={inputValueMapRef}
          onInputValueChange={(tabId: string, value: string) => {
            inputValueMapRef.current.set(tabId, value);
          }}
          groupedSessions={groupedSessions}
          agents={agents}
          onSelectSession={handleSelectSession}
          onDeleteSession={(session) => setDeleteConfirmSession(session)}
          workspaceId={selectedAgentId}
        />
      </div>

      {/* Modals */}
      <FilePreviewModal isOpen={!!previewFile} onClose={() => setPreviewFile(null)} agentId={selectedAgentId || ''} file={previewFile} basePath={effectiveBasePath} />
      <AgentFormModal isOpen={isEditAgentOpen} onClose={() => setIsEditAgentOpen(false)} onSave={handleSaveAgent} agent={selectedAgent} />
    </div>
    </ChatDropZone>
  );
}
