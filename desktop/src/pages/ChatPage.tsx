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
import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import type { Message, ContentBlock, StreamEvent, PermissionRequest, Agent, AgentCreateRequest, ChatSession, TSCCSnapshot } from '../types';
import { chatService } from '../services/chat';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';
import { pluginsService } from '../services/plugins';
import { workspaceService } from '../services/workspace';
import { tasksService } from '../services/tasks';
import { Spinner, ConfirmDialog, AgentFormModal, Toast } from '../components/common';
import { PermissionRequestModal } from '../components/chat';
import { FilePreviewModal } from '../components/workspace/FilePreviewModal';
import { useFileAttachment, useRightSidebarGroup } from '../hooks';
import { useTSCCState } from '../hooks/useTSCCState';
import { useUnifiedTabState, MAX_OPEN_TABS } from '../hooks/useUnifiedTabState';
import { useChatStreamingLifecycle, formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS } from '../hooks/useChatStreamingLifecycle';
import { ChatHeader, ChatInput, ChatHistorySidebar, FileBrowserSidebar, MessageBubble, SwarmRadar } from './chat/components';
import { TSCCPanel } from './chat/components/TSCCPanel';
import { TSCCSnapshotCard } from './chat/components/TSCCSnapshotCard';
import { listSnapshots } from '../services/tscc';
import { groupSessionsByTime } from './chat/utils';
import { createWelcomeMessage, RIGHT_SIDEBAR_WIDTH_CONFIGS } from './chat/constants';
import { useWorkspaceSelection } from '../hooks/useWorkspaceSelection';
import { useLayout } from '../contexts/LayoutContext';

/**
 * Re-export ``deriveStreamingActivity`` and ``MAX_OPEN_TABS`` from the
 * extracted hooks so existing test imports (``from '../pages/ChatPage'``)
 * continue to resolve.
 */
export { deriveStreamingActivity, formatElapsed, ELAPSED_DISPLAY_THRESHOLD_MS, MIN_ACTIVITY_DISPLAY_MS, sanitizeCommand, extractToolContext } from '../hooks/useChatStreamingLifecycle';
export { MAX_OPEN_TABS } from '../hooks/useUnifiedTabState';

export default function ChatPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  // Core chat state — streaming lifecycle delegated to extracted hook
  const [inputValue, setInputValue] = useState('');

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);

  // Pending states (permission is ChatPage-only; question is in the hook)
  const [pendingPermission, setPendingPermission] = useState<PermissionRequest | null>(null);
  const [isPermissionLoading, setIsPermissionLoading] = useState(false);
  const [deleteConfirmSession, setDeleteConfirmSession] = useState<ChatSession | null>(null);
  const [isEditAgentOpen, setIsEditAgentOpen] = useState(false);

  // File attachment
  const { attachments, addFiles, removeFile, clearAll: clearAttachments, isProcessing: isProcessingFiles, 
    error: fileError, canAddMore } = useFileAttachment();

  // File preview state
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);

  // Right sidebar group with mutual exclusion
  const rightSidebars = useRightSidebarGroup({
    defaultActive: 'todoRadar',
    widthConfigs: RIGHT_SIDEBAR_WIDTH_CONFIGS,
  });

  const { selectedWorkspace, workDir } = useWorkspaceSelection();

  // Get attached files from LayoutContext for ChatInput
  const { attachedFiles, removeAttachedFile } = useLayout();

  // Data queries
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: agentsService.list,
  });

  const { data: skills = [], isLoading: isLoadingSkills } = useQuery({
    queryKey: ['skills'],
    queryFn: skillsService.list,
  });

  const { data: mcpServers = [], isLoading: isLoadingMCPs } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: mcpService.list,
  });

  const { data: plugins = [], isLoading: isLoadingPlugins } = useQuery({
    queryKey: ['plugins'],
    queryFn: pluginsService.listPlugins,
  });

  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['chatSessions', selectedAgentId],
    queryFn: () => chatService.listSessions(selectedAgentId || undefined),
    enabled: !!selectedAgentId,
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
  const effectiveBasePath = workDir || agentWorkDir?.path;
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
    cleanupTabState,
    restoreFromFile,
  } = useUnifiedTabState(selectedAgentId || 'default');

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
  } = useChatStreamingLifecycle({
    queryClient,
    applyTelemetryEvent: (event: unknown) => applyTelemetryEventRef.current(event as never),
    tsccTriggerAutoExpand: (reason: string) => tsccTriggerAutoExpandRef.current(reason as never),
    getTabState,
    updateTabState,
    updateTabStatus,
    tabMapRef,
    activeTabIdRef,
  });

  // TSCC state management — called after the streaming hook so sessionId is available
  const {
    tsccState,
    isExpanded: tsccExpanded,
    isPinned: tsccPinned,
    toggleExpand: tsccToggleExpand,
    togglePin: tsccTogglePin,
    applyTelemetryEvent,
    triggerAutoExpand: tsccTriggerAutoExpand,
  } = useTSCCState(sessionId ?? null);

  // Stable refs to break the circular dependency between the streaming hook
  // (which needs TSCC callbacks) and useTSCCState (which needs sessionId).
  // The hook captures these refs via closures; the refs are updated each render.
  const applyTelemetryEventRef = useRef(applyTelemetryEvent);
  applyTelemetryEventRef.current = applyTelemetryEvent;
  const tsccTriggerAutoExpandRef = useRef(tsccTriggerAutoExpand);
  tsccTriggerAutoExpandRef.current = tsccTriggerAutoExpand;

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

  // TSCC snapshots for inline thread history
  const [threadSnapshots, setThreadSnapshots] = useState<TSCCSnapshot[]>([]);

  // Memoized timeline: merge messages + TSCC snapshots into a single sorted list (Req 8.1, 8.2)
  type TimelineItem =
    | { kind: 'message'; data: Message }
    | { kind: 'snapshot'; data: TSCCSnapshot };

  const timeline = useMemo(() => {
    const items: TimelineItem[] = [
      ...messages.map((m): TimelineItem => ({ kind: 'message', data: m })),
      ...threadSnapshots.map((s): TimelineItem => ({ kind: 'snapshot', data: s })),
    ];

    items.sort((a, b) => {
      // Both Message and TSCCSnapshot share a `timestamp` field
      const tsA = a.data.timestamp;
      const tsB = b.data.timestamp;
      const diff = new Date(tsA || 0).getTime() - new Date(tsB || 0).getTime();
      // Stable sort tiebreaker: messages before snapshots, then by id/snapshotId
      if (diff !== 0) return diff;
      if (a.kind !== b.kind) return a.kind === 'message' ? -1 : 1;
      return 0;
    });

    return items;
  }, [messages, threadSnapshots]);

  const agentSkills = selectedAgent?.allowAllSkills
    ? skills
    : selectedAgent?.skillIds
      ? skills.filter((s) => selectedAgent.skillIds.includes(s.id))
      : [];

  const agentMCPs = selectedAgent?.mcpIds
    ? mcpServers.filter((m) => selectedAgent.mcpIds.includes(m.id))
    : [];

  const agentPlugins = selectedAgent?.pluginIds
    ? plugins.filter((p) => selectedAgent.pluginIds.includes(p.id))
    : [];

  const enableSkills = selectedAgent?.allowAllSkills || agentSkills.length > 0 || agentPlugins.length > 0;
  const enableMCP = agentMCPs.length > 0;

  // Load session messages helper
  const loadSessionMessages = useCallback(async (sid: string) => {
    setIsLoadingHistory(true);
    try {
      const sessionMessages = await chatService.getSessionMessages(sid);
      const formattedMessages: Message[] = sessionMessages.map((msg) => ({
        id: msg.id,
        role: msg.role as 'user' | 'assistant',
        content: msg.content as ContentBlock[],
        timestamp: msg.createdAt,
        model: msg.model,
      }));
      setMessages(formattedMessages);
      setSessionId(sid);
      setPendingQuestion(null);
    } catch (error) {
      console.error('Failed to load session messages:', error);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [setMessages, setSessionId, setPendingQuestion, setIsLoadingHistory]);

  // Handle new chat
  const handleNewChat = useCallback(() => {
    setMessages([createWelcomeMessage()]);
    setSessionId(undefined);
    setPendingQuestion(null);
    // Note: Sidebar visibility is now managed by toggle buttons, no need to collapse
  }, []);

  // Tab limit toast state (Fix 7)
  const [tabLimitToast, setTabLimitToast] = useState<string | null>(null);

  // Handle new session - creates new tab with "New Session" title (Req 2.2, 2.3)
  // Fix 6: Save current tab state before creating new tab, initialize new tab in per-tab map
  // Fix 7: Guard against exceeding MAX_OPEN_TABS
  const handleNewSession = useCallback(() => {
    if (!selectedAgentId) return;
    if (tabMapRef.current.size >= MAX_OPEN_TABS) {
      setTabLimitToast('Maximum tabs reached. Close a tab to open a new one.');
      return;
    }
    // Save current React state into the active tab's map entry before switching
    const currentTabId = activeTabIdRef.current;
    if (currentTabId && tabMapRef.current.has(currentTabId)) {
      updateTabState(currentTabId, {
        messages: messagesRef.current,
        sessionId: sessionIdRef.current,
        pendingQuestion: null,
        // isStreaming is already authoritative in tabMapRef — no need to write it back
      });
    }
    const newTab = addTab(selectedAgentId);
    const welcomeMessages = [createWelcomeMessage()];
    initTabState(newTab!.id, welcomeMessages);
    setMessages(welcomeMessages);
    setSessionId(undefined);
    setPendingQuestion(null);
    setIsStreaming(false, newTab!.id); // New tab is not streaming
  }, [selectedAgentId, addTab, initTabState, tabMapRef, updateTabState, activeTabIdRef, setIsStreaming]);

  // Handle tab selection - switches active tab and loads session messages (Req 1.6)
  // Fix 6: Save current tab state, restore target tab state from per-tab map
  const handleTabSelect = useCallback(async (tabId: string) => {
    const tab = openTabs.find(t => t.id === tabId);
    if (!tab) return;
    
    // Save current React state into the active tab's map entry before switching
    const currentTabId = activeTabIdRef.current;
    if (currentTabId && tabMapRef.current.has(currentTabId)) {
      updateTabState(currentTabId, {
        messages: messagesRef.current,
        sessionId: sessionIdRef.current,
        pendingQuestion: pendingQuestion,
        // isStreaming is already authoritative in tabMapRef — no need to write it back
      });
    }
    
    selectTab(tabId);
    
    // Try to restore from per-tab map first (authoritative)
    const restored = restoreTab(tabId);
    if (restored) {
      // Restore React state from the unified tab map
      const tabState = getTabState(tabId);
      if (tabState) {
        setMessages(tabState.messages);
        setSessionId(tabState.sessionId);
        setPendingQuestion(tabState.pendingQuestion);
        // isStreaming derivation automatically reflects target tab's state
        // from tabMapRef — no need to call setIsStreaming which would corrupt
        // the source tab's streaming state. Just bump to re-derive.
        bumpStreamingDerivation();
      }
      // Clear permission state — it's not per-tab in the map, but it
      // should not carry over from the previous tab.
      setPendingPermission(null);
      // Fix 8: Clear unread indicator when switching to a tab with 'complete_unread' status
      if (tabStatuses[tabId] === 'complete_unread') {
        updateTabStatus(tabId, 'idle');
      }
      return;
    }
    
    // Not in map — load from API or initialize fresh
    activeTabIdRef.current = tabId;
    setPendingPermission(null);
    bumpStreamingDerivation(); // re-derive isStreaming for new active tab
    if (tab.sessionId) {
      // New tab with existing session — load from API with async guard
      const loadedTabId = tabId; // capture for closure
      setIsLoadingHistory(true);
      try {
        const sessionMessages = await chatService.getSessionMessages(tab.sessionId);
        // Async guard: only apply if user hasn't switched away during the load
        if (activeTabIdRef.current !== loadedTabId) return;
        const formattedMessages: Message[] = sessionMessages.map((msg) => ({
          id: msg.id,
          role: msg.role as 'user' | 'assistant',
          content: msg.content as ContentBlock[],
          timestamp: msg.createdAt,
          model: msg.model,
        }));
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
      const welcomeMessages = [createWelcomeMessage()];
      setMessages(welcomeMessages);
      setSessionId(undefined);
      setPendingQuestion(null);
      initTabState(tabId, welcomeMessages);
    }
  }, [openTabs, selectTab, restoreTab, getTabState, initTabState, updateTabState, activeTabIdRef, tabMapRef, tabStatuses, updateTabStatus, pendingQuestion]);

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

    // Clean up per-tab state: remove from map, abort controller if active
    cleanupTabState(tabId);
    closeTab(tabId);

    // Fire-and-forget backend stop for tabs that were actively streaming.
    // The abort controller (handled by cleanupTabState) severs the SSE
    // connection client-side; this call tells the backend to stop the agent.
    if (wasStreaming && tabSessionId) {
      chatService.stopSession(tabSessionId).catch((err) => {
        console.warn('[handleTabClose] Failed to stop backend session:', err);
      });
    }
    // If closing active tab, closeTab handles switching to adjacent tab.
    // The activeTabId change triggers the sync useEffect to load content.
  }, [activeTabId, closeTab, cleanupTabState, clearPendingStreamTab, pendingStreamTabs, tabMapRef]);

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
  }, [userScrolledUpRef]);

  useEffect(() => {
    // Only auto-scroll if user hasn't scrolled up (Fix 2)
    if (!userScrolledUpRef.current) {
      scrollToBottom();
    }
  }, [messages]);

  // Register the initial/default tab in the per-tab state map on mount.
  // Without this, the first tab has no entry in tabMapRef and all
  // per-tab features (message tracking, abort isolation, status) are broken.
  useEffect(() => {
    if (activeTabId && !tabMapRef.current.has(activeTabId)) {
      initTabState(activeTabId, messages.length > 0 ? messages : [createWelcomeMessage()]);
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
   */
  useEffect(() => {
    let mounted = true;

    const doRestore = async () => {
      setIsLoadingHistory(true);
      try {
        const restored = await restoreFromFile();
        if (!mounted) return;
        if (restored) {
          console.log('[ChatPage] Tabs restored from open_tabs.json');
        } else {
          console.log('[ChatPage] No saved tabs found, using default tab');
        }
      } catch (err) {
        console.warn('[ChatPage] File tab restore failed:', err);
      } finally {
        if (mounted) setIsLoadingHistory(false);
      }
    };

    doRestore();
    return () => { mounted = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — mount-only

  // Initialize with default agent - always use the default SwarmAgent
  useEffect(() => {
    // Skip if already have a valid agent
    if (selectedAgentId) {
      const existingAgent = agents.find((a) => a.id === selectedAgentId);
      if (existingAgent) {
        if (messages.length === 0) {
          setMessages([createWelcomeMessage()]);
        }
        return;
      }
    }
    
    // Load the default agent
    setAgentLoadError(null);
    agentsService.getDefault().then(defaultAgent => {
      setSelectedAgentId(defaultAgent.id);
      // Only set welcome message if not currently streaming (avoid wiping in-progress chat)
      if (!messages.some((m: Message) => m.role === 'user')) {
        setMessages([createWelcomeMessage()]);
      }
    }).catch(error => {
      console.error('Failed to fetch default agent:', error);
      setAgentLoadError(t('chat.defaultAgentError', 'Failed to load the default agent. Please restart the application or check the backend service.'));
    });
  }, [agents, selectedAgentId, messages.length, t]);

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

  // Validate tabs against sessions - filter out tabs referencing deleted sessions (Req 3.4)
  useEffect(() => {
    if (sessions.length === 0) return;
    
    const validSessionIds = new Set(sessions.map(s => s.id));
    removeInvalidTabs(validSessionIds);
  }, [sessions, removeInvalidTabs]);

  // Sync active tab content when activeTabId changes (for tab switching/closing)
  // IMPORTANT: Only react to activeTabId changes — NOT sessionId or openTabs changes.
  // sessionId changes during streaming (session_start event) must not trigger
  // a reload, or it will wipe in-progress messages.
  // openTabs changes (from render counter bumps) must not re-trigger this effect.
  const prevActiveTabIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activeTabId) return;
    // Skip if activeTabId hasn't actually changed
    if (prevActiveTabIdRef.current === activeTabId) return;
    prevActiveTabIdRef.current = activeTabId;
    
    // Read tab metadata from the map (stable, not from openTabs which triggers re-renders)
    const activeTabState = tabMapRef.current.get(activeTabId);
    if (!activeTabState) return;
    
    // Only load if the tab has a persisted session we haven't loaded yet
    if (activeTabState.sessionId && activeTabState.sessionId !== sessionId) {
      loadSessionMessages(activeTabState.sessionId);
    } else if (!activeTabState.sessionId && !isStreaming && sessionId) {
      // Tab has no session and we're not streaming — reset to welcome
      setMessages([createWelcomeMessage()]);
      setSessionId(undefined);
      setPendingQuestion(null);
    }
  }, [activeTabId, sessionId, isStreaming, loadSessionMessages, tabMapRef]);

  // Fetch TSCC snapshots when session changes
  useEffect(() => {
    if (!sessionId) {
      setThreadSnapshots([]);
      return;
    }
    let cancelled = false;
    listSnapshots(sessionId)
      .then((snaps) => { if (!cancelled) setThreadSnapshots(snaps); })
      .catch(() => { if (!cancelled) setThreadSnapshots([]); });
    return () => { cancelled = true; };
  }, [sessionId]);

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

  // Build content array from text and attachments
  const buildContentArray = useCallback(
    async (text: string, fileAttachments: typeof attachments): Promise<ContentBlock[]> => {
      const content: ContentBlock[] = [];

      if (text.trim()) {
        content.push({ type: 'text', text } as ContentBlock);
      }

      for (const att of fileAttachments) {
        if (!att.base64) continue;

        if (att.type === 'image') {
          content.push({
            type: 'image',
            source: { type: 'base64', media_type: att.mediaType, data: att.base64 },
          } as unknown as ContentBlock);
        } else if (att.type === 'pdf') {
          content.push({
            type: 'document',
            source: { type: 'base64', media_type: 'application/pdf', data: att.base64 },
          } as unknown as ContentBlock);
        } else if ((att.type === 'text' || att.type === 'csv') && selectedAgentId) {
          try {
            const result = await workspaceService.uploadFile(selectedAgentId, att.name, att.base64);
            content.push({
              type: 'text',
              text: `[Attached file: ${att.name}] saved at ${result.path} - use Read tool to access`,
            } as ContentBlock);
          } catch (err) {
            console.error('Failed to upload file:', err);
            content.push({ type: 'text', text: `[Failed to attach file: ${att.name}]` } as ContentBlock);
          }
        }
      }

      return content;
    },
    [selectedAgentId]
  );

  // Wrap the hook's createStreamHandler to add ChatPage-local cmd_permission_request handling.
  // The hook handles sessionId/isStreaming; ChatPage adds setPendingPermission.
  // Fix 6: Pass activeTabIdRef.current as tabId for tab-aware streaming.
  const wrappedCreateStreamHandler = useCallback((assistantMessageId: string) => {
    const tabId = activeTabIdRef.current ?? undefined;
    const hookHandler = createStreamHandler(assistantMessageId, tabId);
    return (event: StreamEvent) => {
      if (event.type === 'cmd_permission_request') {
        // Extract permission fields (handle both camelCase and snake_case)
        const raw = event as unknown as Record<string, unknown>;
        const requestId = event.requestId || raw.request_id as string;
        const toolName = event.toolName || raw.tool_name as string;
        const toolInput = event.toolInput || raw.tool_input as Record<string, unknown>;
        setPendingPermission({
          requestId: requestId!,
          toolName: toolName!,
          toolInput: toolInput!,
          reason: event.reason!,
          options: event.options || ['approve', 'deny'],
        });
      }
      // Delegate to hook handler for all events (including cmd_permission_request
      // which also needs sessionId/isStreaming updates)
      hookHandler(event);
    };
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
    const hasAttachments = currentAttachments.some((a) => a.base64);

    if ((!hasText && !hasAttachments) || !selectedAgentId) return;

    // Per-tab streaming guard: check only the active tab's state
    const activeTabForGuard = tabMapRef.current.get(activeTabIdRef.current ?? '');
    if (activeTabForGuard?.isStreaming || pendingStreamTabs.has(activeTabIdRef.current ?? '')) return;

    if (messageText.trim().startsWith('/plugin')) {
      setInputValue('');
      await handlePluginCommand(messageText.trim());
      return;
    }

    const content = await buildContentArray(messageText, currentAttachments);
    if (content.length === 0) return;

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
    setIsStreaming(true, activeTabIdRef.current ?? undefined);

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
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    // Ensure the active tab is registered in the per-tab state map BEFORE
    // creating the stream handler. Without this, capturedTabId would be null
    // and isActiveTab would become false once initTabState fires later.
    if (currentActiveTabId && !tabMapRef.current.has(currentActiveTabId)) {
      initTabState(currentActiveTabId, messagesRef.current);
    }

    const abort = chatService.streamChat(
      {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId: sessionIdRef.current,
        enableSkills,
        enableMCP,
        workspaceContext: selectedWorkspace?.context,
      },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, activeTabIdRef.current ?? undefined),
      createCompleteHandler(activeTabIdRef.current ?? undefined)
    );

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (currentActiveTabId) {
      updateTabState(currentActiveTabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
      });
    }
  }, [selectedAgentId, enableSkills, enableMCP, selectedWorkspace, handlePluginCommand, buildContentArray, clearAttachments, resetUserScroll, incrementStreamGen, setIsStreaming, setMessages, setInputValue, updateTabStatus, updateTabTitle, setTabIsNew, initTabState, wrappedCreateStreamHandler, createErrorHandler, createCompleteHandler, activeTabIdRef, tabMapRef, pendingStreamTabs, queryClient, t]);

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (toolUseId: string, answers: Record<string, string>) => {
    const tabId = activeTabIdRef.current ?? undefined;
    const tabSessionId = tabId ? tabMapRef.current.get(tabId)?.sessionId : undefined;
    if (!selectedAgentId || !tabSessionId) return;

    setPendingQuestion(null);
    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from waiting_input → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = Date.now().toString();
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    const abort = chatService.streamAnswerQuestion(
      { agentId: selectedAgentId, sessionId: tabSessionId, toolUseId, answers, enableSkills, enableMCP },
      wrappedCreateStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId, tabId),
      createCompleteHandler(tabId)
    );

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (tabId) {
      updateTabState(tabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
      });
    }
  };

  // Handle permission decision
  const handlePermissionDecision = async (decision: 'approve' | 'deny', feedback?: string) => {
    const tabId = activeTabIdRef.current ?? undefined;
    const tabSessionId = tabId ? tabMapRef.current.get(tabId)?.sessionId : undefined;
    if (!pendingPermission || !tabSessionId || !selectedAgentId) return;

    setIsPermissionLoading(true);
    setPendingPermission(null);

    const decisionText = decision === 'approve' ? '✓ Command approved, executing...' : '✗ Command denied by user';
    setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'assistant', content: [{ type: 'text', text: decisionText }], timestamp: new Date().toISOString() }]);

    if (decision === 'deny') {
      try {
        await chatService.submitCmdPermissionDecision({ sessionId: tabSessionId, requestId: pendingPermission.requestId, decision: 'deny', feedback });
      } catch (error) {
        console.error('Failed to submit deny decision:', error);
      } finally {
        setIsPermissionLoading(false);
        setIsStreaming(false, tabId);
      }
      return;
    }

    incrementStreamGen(); // Fix 1: new stream generation
    setIsStreaming(true, tabId);

    // Fix 8: Transition tab status from permission_needed → streaming
    if (tabId) updateTabStatus(tabId, 'streaming');

    const assistantMessageId = (Date.now() + 1).toString();
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    const abort = chatService.streamCmdPermissionContinue(
      { sessionId: tabSessionId, requestId: pendingPermission.requestId, decision, feedback, enableSkills, enableMCP },
      (event: StreamEvent) => {
        if (event.type === 'cmd_permission_acknowledged') {
          setMessages((prev) => prev.filter((msg) => msg.id !== assistantMessageId));
          setIsStreaming(false, tabId);
        } else {
          wrappedCreateStreamHandler(assistantMessageId)(event);
        }
      },
      (error) => { createErrorHandler(assistantMessageId, tabId)(error); setIsPermissionLoading(false); },
      () => { createCompleteHandler(tabId)(); setIsPermissionLoading(false); }
    );

    // Store abort function in the tab map for per-tab stop isolation.
    // Only the .abort() method is used by handleStop — no signal needed.
    if (tabId) {
      updateTabState(tabId, {
        abortController: { abort: () => { abort(); }, signal: { aborted: false } } as unknown as AbortController,
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
      setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'assistant', content: [{ type: 'text', text: '⏹️ Generation stopped by user.' }], timestamp: new Date().toISOString() }]);
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
    <div className="flex flex-col h-full">
      <ChatHeader
        openTabs={openTabs}
        activeTabId={activeTabId}
        onTabSelect={handleTabSelect}
        onTabClose={handleTabClose}
        onNewSession={handleNewSession}
        tabStatuses={tabStatuses}
        activeSidebar={rightSidebars.activeSidebar}
        onOpenSidebar={rightSidebars.openSidebar}
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
          ) : isLoadingHistory ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <Spinner size="lg" />
                <p className="text-[var(--color-text-muted)] mt-4">{t('common.status.loading')}</p>
              </div>
            </div>
          ) : (
            <>
              {/* Messages — interleaved with TSCC snapshot cards */}
              <div ref={messagesContainerRef} onScroll={handleMessagesScroll} className="flex-1 overflow-y-auto p-6 space-y-6">
                {timeline.map((item) =>
                    item.kind === 'message' ? (
                      <MessageBubble
                        key={item.data.id}
                        message={item.data}
                        onAnswerQuestion={handleAnswerQuestion}
                        pendingToolUseId={pendingQuestion?.toolUseId}
                        isStreaming={isStreaming}
                      />
                    ) : (
                      <TSCCSnapshotCard
                        key={`snap-${item.data.snapshotId}`}
                        snapshot={item.data}
                      />
                    ),
                  )}
                {isStreaming && (
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

              {/* TSCC Panel — between messages and input */}
              <TSCCPanel
                threadId={sessionId ?? null}
                tsccState={tsccState}
                isExpanded={tsccExpanded}
                isPinned={tsccPinned}
                onToggleExpand={tsccToggleExpand}
                onTogglePin={tsccTogglePin}
              />

              {/* Input Area */}
              <ChatInput
                inputValue={inputValue}
                onInputChange={setInputValue}
                onSend={handleSendMessage}
                onStop={handleStop}
                isStreaming={isStreaming}
                selectedAgentId={selectedAgentId}
                selectedWorkspace={selectedWorkspace}
                attachments={attachments}
                onAddFiles={addFiles}
                onRemoveFile={removeFile}
                isProcessingFiles={isProcessingFiles}
                fileError={fileError}
                canAddMore={canAddMore}
                agentSkills={agentSkills}
                agentMCPs={agentMCPs}
                agentPlugins={agentPlugins}
                isLoadingSkills={isLoadingSkills}
                isLoadingMCPs={isLoadingMCPs}
                isLoadingPlugins={isLoadingPlugins}
                allowAllSkills={selectedAgent?.allowAllSkills}
                attachedContextFiles={attachedFiles}
                onRemoveContextFile={removeAttachedFile}
              />
            </>
          )}
        </div>

        {/* Right Sidebars - Order: TodoRadar, ChatHistory, FileBrowser */}
        {rightSidebars.isActive('todoRadar') && (
          <SwarmRadar
            width={rightSidebars.widths.todoRadar.width}
            isResizing={rightSidebars.widths.todoRadar.isResizing}
            onMouseDown={rightSidebars.widths.todoRadar.handleMouseDown}
            pendingQuestion={pendingQuestion}
            pendingPermission={pendingPermission}
            activeSessionId={sessionId}
          />
        )}

        {/* Chat History Sidebar */}
        {rightSidebars.isActive('chatHistory') && (
          <ChatHistorySidebar
            width={rightSidebars.widths.chatHistory.width}
            isResizing={rightSidebars.widths.chatHistory.isResizing}
            groupedSessions={groupedSessions}
            currentSessionId={sessionId}
            agents={agents}
            selectedAgentId={selectedAgentId}
            onNewChat={handleNewChat}
            onSelectSession={handleSelectSession}
            onDeleteSession={(session) => setDeleteConfirmSession(session)}
            onMouseDown={rightSidebars.widths.chatHistory.handleMouseDown}
          />
        )}

        {/* Right Sidebar - File Browser */}
        {rightSidebars.isActive('fileBrowser') && (
          <FileBrowserSidebar
            width={rightSidebars.widths.fileBrowser.width}
            isResizing={rightSidebars.widths.fileBrowser.isResizing}
            selectedAgentId={selectedAgentId}
            basePath={effectiveBasePath}
            onFileSelect={setPreviewFile}
            onMouseDown={rightSidebars.widths.fileBrowser.handleMouseDown}
          />
        )}
      </div>

      {/* Modals */}
      <FilePreviewModal isOpen={!!previewFile} onClose={() => setPreviewFile(null)} agentId={selectedAgentId || ''} file={previewFile} basePath={effectiveBasePath} />
      {pendingPermission && <PermissionRequestModal request={pendingPermission} onDecision={handlePermissionDecision} isLoading={isPermissionLoading} />}
      <AgentFormModal isOpen={isEditAgentOpen} onClose={() => setIsEditAgentOpen(false)} onSave={handleSaveAgent} agent={selectedAgent} />

      {/* Tab Limit Toast - Fix 7 (Req 2.16) */}
      {tabLimitToast && (
        <Toast message={tabLimitToast} type="info" onDismiss={() => setTabLimitToast(null)} />
      )}
    </div>
  );
}
