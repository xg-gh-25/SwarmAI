import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams, useNavigate } from 'react-router-dom';
import type { Message, ContentBlock, StreamEvent, PermissionRequest, Agent, AgentCreateRequest, ChatSession } from '../types';
import { chatService } from '../services/chat';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';
import { pluginsService } from '../services/plugins';
import { workspaceService } from '../services/workspace';
import { tasksService } from '../services/tasks';
import { Spinner, ConfirmDialog, AgentFormModal } from '../components/common';
import { PermissionRequestModal } from '../components/chat';
import { FilePreviewModal } from '../components/workspace/FilePreviewModal';
import { useFileAttachment, useSidebarState, useTabState } from '../hooks';
import { ChatHeader, ChatInput, ChatSidebar, FileBrowserSidebar, MessageBubble, TodoRadarSidebar } from './chat/components';
import { groupSessionsByTime } from './chat/utils';
import { createWelcomeMessage, createWorkspaceChangeMessage } from './chat/constants';
import { DEFAULT_SIDEBAR_WIDTH, DEFAULT_RIGHT_SIDEBAR_WIDTH, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH,
  MIN_RIGHT_SIDEBAR_WIDTH, MAX_RIGHT_SIDEBAR_WIDTH } from './chat/constants';
import type { PendingQuestion } from './chat/types';
import { useWorkspaceSelection } from '../hooks/useWorkspaceSelection';

export default function ChatPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Core chat state
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [agentLoadError, setAgentLoadError] = useState<string | null>(null);

  // Background task mode
  const [runAsTask, setRunAsTask] = useState(() => searchParams.get('taskMode') === 'true');

  // Pending states
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [pendingPermission, setPendingPermission] = useState<PermissionRequest | null>(null);
  const [isPermissionLoading, setIsPermissionLoading] = useState(false);
  const [deleteConfirmSession, setDeleteConfirmSession] = useState<ChatSession | null>(null);
  const [isEditAgentOpen, setIsEditAgentOpen] = useState(false);

  // File attachment
  const { attachments, addFiles, removeFile, clearAll: clearAttachments, isProcessing: isProcessingFiles, 
    error: fileError, canAddMore } = useFileAttachment();

  // File preview state
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);

  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);

  // Sidebar states using custom hook
  const chatSidebar = useSidebarState({
    storageKey: 'chatSidebarCollapsed',
    widthStorageKey: 'chatSidebarWidth',
    defaultCollapsed: true,
    defaultWidth: DEFAULT_SIDEBAR_WIDTH,
    minWidth: MIN_SIDEBAR_WIDTH,
    maxWidth: MAX_SIDEBAR_WIDTH,
  });

  const rightSidebar = useSidebarState({
    storageKey: 'rightSidebarCollapsed',
    widthStorageKey: 'rightSidebarWidth',
    defaultCollapsed: false,
    defaultWidth: DEFAULT_RIGHT_SIDEBAR_WIDTH,
    minWidth: MIN_RIGHT_SIDEBAR_WIDTH,
    maxWidth: MAX_RIGHT_SIDEBAR_WIDTH,
  });

  // ToDo Radar sidebar state (Req 5.4, 5.5)
  const todoRadarSidebar = useSidebarState({
    storageKey: 'todoRadarSidebarCollapsed',
    widthStorageKey: 'todoRadarSidebarWidth',
    defaultCollapsed: true,
    defaultWidth: 300,
    minWidth: 200,
    maxWidth: 500,
  });

  // Workspace selection with callback for workspace changes
  const handleWorkspaceChanged = useCallback((workspace: typeof selectedWorkspace) => {
    setSessionId(undefined);
    setMessages([]);
    setPendingQuestion(null);
    setMessages([createWorkspaceChangeMessage(workspace?.name, workspace?.filePath)]);
  }, []);

  const { selectedWorkspace, setSelectedWorkspace, workDir } = useWorkspaceSelection({
    selectedAgentId,
    onWorkspaceChange: handleWorkspaceChanged,
  });

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

  // Tab state management (Req 1.7, 2.2, 2.3, 3.1, 3.2, 3.3)
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
  } = useTabState(selectedAgentId || 'default');

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
  }, []);

  // Handle new chat
  const handleNewChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
    setMessages([createWelcomeMessage()]);
    chatSidebar.setCollapsed(true);
  }, [chatSidebar]);

  // Handle new session - creates new tab with "New Session" title (Req 2.2, 2.3)
  const handleNewSession = useCallback(() => {
    if (!selectedAgentId) return;
    addTab(selectedAgentId);
    // Clear current session state for the new tab
    setMessages([createWelcomeMessage()]);
    setSessionId(undefined);
    setPendingQuestion(null);
  }, [selectedAgentId, addTab]);

  // Handle tab selection - switches active tab and loads session messages (Req 1.6)
  const handleTabSelect = useCallback(async (tabId: string) => {
    const tab = openTabs.find(t => t.id === tabId);
    if (!tab) return;
    
    selectTab(tabId);
    
    // If tab has a session, load its messages
    if (tab.sessionId) {
      await loadSessionMessages(tab.sessionId);
    } else {
      // New tab without session - show welcome message
      setMessages([createWelcomeMessage()]);
      setSessionId(undefined);
      setPendingQuestion(null);
    }
  }, [openTabs, selectTab, loadSessionMessages]);

  // Handle tab close - removes tab, handles last-tab case (Req 3.3)
  const handleTabClose = useCallback((tabId: string) => {
    const isActiveTab = tabId === activeTabId;
    closeTab(tabId);
    
    // If closing active tab, the closeTab function handles switching to adjacent tab
    // We need to load the new active tab's content after state updates
    if (isActiveTab) {
      // The closeTab function will update activeTabId, so we need to handle this
      // in a useEffect that watches activeTabId changes
    }
  }, [activeTabId, closeTab]);

  // Handle session selection
  const handleSelectSession = useCallback(async (session: ChatSession) => {
    if (session.agentId && session.agentId !== selectedAgentId) {
      setSelectedAgentId(session.agentId);
    }
    await loadSessionMessages(session.id);
    chatSidebar.setCollapsed(true);
  }, [selectedAgentId, loadSessionMessages, chatSidebar]);

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

  // Scroll to bottom on new messages
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

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
      setMessages([createWelcomeMessage()]);
    }).catch(error => {
      console.error('Failed to fetch default agent:', error);
      setAgentLoadError(t('chat.defaultAgentError', 'Failed to load the default agent. Please restart the application or check the backend service.'));
    });
  }, [agents, selectedAgentId, messages.length, t]);

  // Sync taskMode URL parameter
  useEffect(() => {
    const taskMode = searchParams.get('taskMode') === 'true';
    setRunAsTask(taskMode);
  }, [searchParams]);

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
  useEffect(() => {
    if (!activeTabId) return;
    
    const activeTab = openTabs.find(t => t.id === activeTabId);
    if (!activeTab) return;
    
    // Only load if the current sessionId doesn't match the tab's sessionId
    if (activeTab.sessionId && activeTab.sessionId !== sessionId) {
      loadSessionMessages(activeTab.sessionId);
    } else if (!activeTab.sessionId && sessionId) {
      // Tab has no session but we have a sessionId - reset to welcome
      setMessages([createWelcomeMessage()]);
      setSessionId(undefined);
      setPendingQuestion(null);
    }
  }, [activeTabId, openTabs, sessionId, loadSessionMessages]);

  // Update tab's sessionId when a new session is created
  useEffect(() => {
    if (sessionId && activeTabId) {
      const activeTab = openTabs.find(t => t.id === activeTabId);
      if (activeTab && !activeTab.sessionId) {
        updateTabSessionId(activeTabId, sessionId);
      }
    }
  }, [sessionId, activeTabId, openTabs, updateTabSessionId]);

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

  // Stream event handler - extracted to reduce duplication
  const createStreamHandler = useCallback((assistantMessageId: string) => {
    return (event: StreamEvent) => {
      if (event.type === 'session_start' && event.sessionId) {
        setSessionId(event.sessionId);
      } else if (event.type === 'session_cleared' && event.newSessionId) {
        setSessionId(event.newSessionId);
        setMessages([]);
        queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
      } else if (event.type === 'assistant' && event.content) {
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id !== assistantMessageId) return msg;
            const existingContent = msg.content;
            const newContent = event.content!.filter((newBlock) => {
              return !existingContent.some((existing) => {
                if (newBlock.type !== existing.type) return false;
                if (newBlock.type === 'text' && existing.type === 'text') return newBlock.text === existing.text;
                if (newBlock.type === 'tool_use' && existing.type === 'tool_use') return newBlock.id === existing.id;
                if (newBlock.type === 'tool_result' && existing.type === 'tool_result') return newBlock.toolUseId === existing.toolUseId;
                return false;
              });
            });
            return { ...msg, content: [...existingContent, ...newContent], model: event.model };
          })
        );
      } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
        setPendingQuestion({ toolUseId: event.toolUseId, questions: event.questions });
        if (event.sessionId) setSessionId(event.sessionId);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: [...msg.content, { type: 'ask_user_question' as const, toolUseId: event.toolUseId!, questions: event.questions! }] }
              : msg
          )
        );
        setIsStreaming(false);
      } else if (event.type === 'permission_request') {
        setPendingPermission({
          requestId: event.requestId!,
          toolName: event.toolName!,
          toolInput: event.toolInput!,
          reason: event.reason!,
          options: event.options || ['approve', 'deny'],
        });
        if (event.sessionId) setSessionId(event.sessionId);
        setIsStreaming(false);
      } else if (event.type === 'result' && event.sessionId) {
        setSessionId(event.sessionId);
      } else if (event.type === 'error') {
        const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
        setMessages((prev) =>
          prev.map((msg) => msg.id === assistantMessageId ? { ...msg, content: [{ type: 'text', text: `Error: ${errorMsg}` }] } : msg)
        );
      }
    };
  }, [queryClient]);

  const createErrorHandler = useCallback((assistantMessageId: string) => {
    return (error: Error) => {
      console.error('Stream error:', error);
      setMessages((prev) =>
        prev.map((msg) => msg.id === assistantMessageId ? { ...msg, content: [{ type: 'text', text: `Connection error: ${error.message}` }] } : msg)
      );
      setIsStreaming(false);
    };
  }, []);

  const createCompleteHandler = useCallback(() => {
    return () => setIsStreaming(false);
  }, []);

  // Handle plugin commands
  const handlePluginCommand = async (command: string): Promise<boolean> => {
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
  };

  // Handle send message
  const handleSendMessage = async () => {
    const messageText = inputValue;
    const hasText = messageText.trim().length > 0;
    const hasAttachments = attachments.some((a) => a.base64);

    if ((!hasText && !hasAttachments) || isStreaming || !selectedAgentId) return;

    if (messageText.trim().startsWith('/plugin')) {
      setInputValue('');
      await handlePluginCommand(messageText.trim());
      return;
    }

    const content = await buildContentArray(messageText, attachments);
    if (content.length === 0) return;

    if (runAsTask) {
      try {
        await tasksService.create({
          agentId: selectedAgentId,
          message: hasAttachments ? undefined : messageText,
          content: hasAttachments ? content : undefined,
          enableSkills,
          enableMcp: enableMCP,
          addDirs: workDir ? [workDir] : undefined,
        });
        setInputValue('');
        clearAttachments();
        setRunAsTask(false);
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
        navigate('/tasks');
      } catch (error) {
        console.error('Failed to create task:', error);
        alert(t('chat.taskCreateFailed'));
      }
      return;
    }

    const displayText = hasText ? messageText : '[Attachments]';
    const userMessageContent: ContentBlock[] = [{ type: 'text', text: displayText }];
    if (hasAttachments && hasText) {
      userMessageContent.push({ type: 'text', text: `📎 ${attachments.map((a) => a.name).join(', ')}` });
    }

    const userMessage: Message = { id: Date.now().toString(), role: 'user', content: userMessageContent, timestamp: new Date().toISOString() };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    clearAttachments();
    setIsStreaming(true);

    // Update tab title on first message (Req 2.4)
    if (activeTabId) {
      const activeTab = openTabs.find(t => t.id === activeTabId);
      if (activeTab?.isNew && messageText.trim()) {
        const newTitle = messageText.slice(0, 25) + (messageText.length > 25 ? '...' : '');
        updateTabTitle(activeTabId, newTitle);
        setTabIsNew(activeTabId, false);
      }
    }

    const assistantMessageId = (Date.now() + 1).toString();
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    const abort = chatService.streamChat(
      {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId,
        enableSkills,
        enableMCP,
        addDirs: workDir ? [workDir] : undefined,
        workspaceId: selectedWorkspace?.id,
        workspaceContext: selectedWorkspace?.context,
      },
      createStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId),
      createCompleteHandler()
    );

    abortRef.current = abort;
  };

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (toolUseId: string, answers: Record<string, string>) => {
    if (!selectedAgentId || !sessionId) return;

    setPendingQuestion(null);
    setIsStreaming(true);

    const assistantMessageId = Date.now().toString();
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    const abort = chatService.streamAnswerQuestion(
      { agentId: selectedAgentId, sessionId, toolUseId, answers, enableSkills, enableMCP },
      createStreamHandler(assistantMessageId),
      createErrorHandler(assistantMessageId),
      createCompleteHandler()
    );

    abortRef.current = abort;
  };

  // Handle permission decision
  const handlePermissionDecision = async (decision: 'approve' | 'deny', feedback?: string) => {
    if (!pendingPermission || !sessionId || !selectedAgentId) return;

    setIsPermissionLoading(true);
    setPendingPermission(null);

    const decisionText = decision === 'approve' ? '✓ Command approved, executing...' : '✗ Command denied by user';
    setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'assistant', content: [{ type: 'text', text: decisionText }], timestamp: new Date().toISOString() }]);

    if (decision === 'deny') {
      try {
        await chatService.submitPermissionDecision({ sessionId, requestId: pendingPermission.requestId, decision: 'deny', feedback });
      } catch (error) {
        console.error('Failed to submit deny decision:', error);
      } finally {
        setIsPermissionLoading(false);
        setIsStreaming(false);
      }
      return;
    }

    setIsStreaming(true);
    const assistantMessageId = (Date.now() + 1).toString();
    setMessages((prev) => [...prev, { id: assistantMessageId, role: 'assistant', content: [], timestamp: new Date().toISOString() }]);

    const abort = chatService.streamPermissionContinue(
      { sessionId, requestId: pendingPermission.requestId, decision, feedback, enableSkills, enableMCP },
      (event: StreamEvent) => {
        if (event.type === 'permission_acknowledged') {
          setMessages((prev) => prev.filter((msg) => msg.id !== assistantMessageId));
          setIsStreaming(false);
        } else {
          createStreamHandler(assistantMessageId)(event);
        }
      },
      (error) => { createErrorHandler(assistantMessageId)(error); setIsPermissionLoading(false); },
      () => { setIsStreaming(false); setIsPermissionLoading(false); }
    );

    abortRef.current = abort;
  };

  // Handle stop
  const handleStop = async () => {
    if (!sessionId) return;
    try {
      if (abortRef.current) { abortRef.current(); abortRef.current = null; }
      await chatService.stopSession(sessionId);
      setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'assistant', content: [{ type: 'text', text: '⏹️ Generation stopped by user.' }], timestamp: new Date().toISOString() }]);
    } catch (error) {
      console.error('Failed to stop session:', error);
    } finally {
      setIsStreaming(false);
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
        chatSidebarCollapsed={chatSidebar.collapsed}
        todoRadarCollapsed={todoRadarSidebar.collapsed}
        onToggleChatSidebar={chatSidebar.toggle}
        onToggleTodoRadar={todoRadarSidebar.toggle}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Chat History Sidebar */}
        {!chatSidebar.collapsed && (
          <ChatSidebar
            width={chatSidebar.width}
            isResizing={chatSidebar.isResizing}
            groupedSessions={groupedSessions}
            currentSessionId={sessionId}
            agents={agents}
            selectedAgentId={selectedAgentId}
            onNewChat={handleNewChat}
            onSelectSession={handleSelectSession}
            onDeleteSession={(session) => setDeleteConfirmSession(session)}
            onClose={() => chatSidebar.setCollapsed(true)}
            onMouseDown={chatSidebar.handleMouseDown}
          />
        )}

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
              {/* Messages */}
              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {messages.map((message) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    onAnswerQuestion={handleAnswerQuestion}
                    pendingToolUseId={pendingQuestion?.toolUseId}
                    isStreaming={isStreaming}
                  />
                ))}
                {isStreaming && (
                  <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
                    <Spinner size="sm" />
                    <span className="text-sm">{t('chat.thinking')}</span>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Input Area */}
              <ChatInput
                inputValue={inputValue}
                onInputChange={setInputValue}
                onSend={handleSendMessage}
                onStop={handleStop}
                isStreaming={isStreaming}
                runAsTask={runAsTask}
                onToggleRunAsTask={() => setRunAsTask(!runAsTask)}
                selectedAgentId={selectedAgentId}
                selectedWorkspace={selectedWorkspace}
                onWorkspaceSelect={setSelectedWorkspace}
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
              />
            </>
          )}
        </div>

        {/* Right Sidebar - File Browser */}
        {!rightSidebar.collapsed && (
          <FileBrowserSidebar
            width={rightSidebar.width}
            isResizing={rightSidebar.isResizing}
            selectedAgentId={selectedAgentId}
            basePath={effectiveBasePath}
            onFileSelect={setPreviewFile}
            onClose={() => rightSidebar.setCollapsed(true)}
            onMouseDown={rightSidebar.handleMouseDown}
          />
        )}

        {/* Right Sidebar - ToDo Radar (Req 5.1, 5.2, 5.3, 5.4) */}
        {!todoRadarSidebar.collapsed && (
          <TodoRadarSidebar
            width={todoRadarSidebar.width}
            isResizing={todoRadarSidebar.isResizing}
            onClose={() => todoRadarSidebar.setCollapsed(true)}
            onMouseDown={todoRadarSidebar.handleMouseDown}
          />
        )}
      </div>

      {/* Modals */}
      <FilePreviewModal isOpen={!!previewFile} onClose={() => setPreviewFile(null)} agentId={selectedAgentId || ''} file={previewFile} basePath={effectiveBasePath} />
      {pendingPermission && <PermissionRequestModal request={pendingPermission} onDecision={handlePermissionDecision} isLoading={isPermissionLoading} />}
      <AgentFormModal isOpen={isEditAgentOpen} onClose={() => setIsEditAgentOpen(false)} onSave={handleSaveAgent} agent={selectedAgent} />
    </div>
  );
}
