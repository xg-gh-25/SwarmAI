import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams, useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import type { Message, ContentBlock, StreamEvent, AskUserQuestion as AskUserQuestionType, ChatSession, TodoItem, PermissionRequest, Agent, AgentCreateRequest, FileAttachment } from '../types';
import { chatService } from '../services/chat';
import { agentsService } from '../services/agents';
import { skillsService } from '../services/skills';
import { mcpService } from '../services/mcp';
import { pluginsService } from '../services/plugins';
import { workspaceService } from '../services/workspace';
import { tasksService } from '../services/tasks';
import { Spinner, ReadOnlyChips, AskUserQuestion, Dropdown, MarkdownRenderer, ConfirmDialog, TodoWriteWidget, AgentFormModal } from '../components/common';
import { PermissionRequestModal, FileAttachmentButton, FileAttachmentPreview } from '../components/chat';
import { FileBrowser } from '../components/workspace/FileBrowser';
import { FilePreviewModal } from '../components/workspace/FilePreviewModal';
import { FolderPickerModal } from '../components/workspace/FolderPickerModal';
import { useFileAttachment } from '../hooks/useFileAttachment';

// Pending question state
interface PendingQuestion {
  toolUseId: string;
  questions: AskUserQuestionType[];
}

// Time group types for session grouping
type TimeGroup = 'today' | 'yesterday' | 'thisWeek' | 'thisMonth' | 'older';

interface GroupedSessions {
  group: TimeGroup;
  sessions: ChatSession[];
}

// Group sessions by time periods
const groupSessionsByTime = (sessions: ChatSession[]): GroupedSessions[] => {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);

  // Week starts on Monday
  const dayOfWeek = now.getDay();
  const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const weekStart = new Date(today.getTime() - mondayOffset * 86400000);

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);

  const groups: Record<TimeGroup, ChatSession[]> = {
    today: [],
    yesterday: [],
    thisWeek: [],
    thisMonth: [],
    older: [],
  };

  for (const session of sessions) {
    const date = new Date(session.lastAccessedAt);
    const sessionDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());

    if (sessionDay.getTime() === today.getTime()) {
      groups.today.push(session);
    } else if (sessionDay.getTime() === yesterday.getTime()) {
      groups.yesterday.push(session);
    } else if (sessionDay >= weekStart) {
      groups.thisWeek.push(session);
    } else if (sessionDay >= monthStart) {
      groups.thisMonth.push(session);
    } else {
      groups.older.push(session);
    }
  }

  // Return only non-empty groups in order
  const order: TimeGroup[] = ['today', 'yesterday', 'thisWeek', 'thisMonth', 'older'];
  return order
    .filter(group => groups[group].length > 0)
    .map(group => ({ group, sessions: groups[group] }));
};

// i18n keys for time group labels
const timeGroupLabelKey: Record<TimeGroup, string> = {
  today: 'chat.today',
  yesterday: 'chat.yesterday',
  thisWeek: 'chat.thisWeek',
  thisMonth: 'chat.thisMonth',
  older: 'chat.older',
};

export default function ChatPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();

  // Initialize selectedAgentId from localStorage
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(() => {
    return localStorage.getItem('lastSelectedAgentId');
  });

  // Background task mode - run chat as a background task
  // Initialize from URL parameter (taskMode=true when coming from Tasks page)
  const [runAsTask, setRunAsTask] = useState(() => {
    return searchParams.get('taskMode') === 'true';
  });

  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [pendingPermission, setPendingPermission] = useState<PermissionRequest | null>(null);
  const [isPermissionLoading, setIsPermissionLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [deleteConfirmSession, setDeleteConfirmSession] = useState<ChatSession | null>(null);

  // Agent edit modal state
  const [isEditAgentOpen, setIsEditAgentOpen] = useState(false);

  // File attachment state
  const { attachments, addFiles, removeFile, clearAll: clearAttachments, isProcessing: isProcessingFiles, error: fileError, canAddMore } = useFileAttachment();

  // Drag and drop state
  const [isDragging, setIsDragging] = useState(false);

  // Work directory state - persisted per agent
  const [workDir, setWorkDirState] = useState<string | null>(null);
  const isRestoringWorkDirRef = useRef(false); // Track when we're restoring from localStorage

  // Wrapper to set workDir and track if it's a user action
  const setWorkDir = (value: string | null, isRestoring = false) => {
    isRestoringWorkDirRef.current = isRestoring;
    setWorkDirState(value);
  };

  // Slash command suggestions
  const [showCommandSuggestions, setShowCommandSuggestions] = useState(false);
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0);
  const slashCommands = [
    { name: '/clear', description: 'Clear conversation context' },
    { name: '/compact', description: 'Compact conversation history' },
    { name: '/plugin list', description: 'List installed plugins' },
    { name: '/plugin install', description: 'Install a plugin: /plugin install {name}@{marketplace}' },
    { name: '/plugin uninstall', description: 'Uninstall a plugin: /plugin uninstall {id}' },
    { name: '/plugin marketplace list', description: 'List available marketplaces' },
  ];

  // File preview state
  const [previewFile, setPreviewFile] = useState<{ path: string; name: string } | null>(null);

  // Folder picker modal state (for web browser mode)
  const [isFolderPickerOpen, setIsFolderPickerOpen] = useState(false);

  // Right sidebar state (for File Browser)
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem('rightSidebarCollapsed');
    return saved !== null ? saved === 'true' : false; // Default: open
  });
  const [rightSidebarWidth, setRightSidebarWidth] = useState(() => {
    const saved = localStorage.getItem('rightSidebarWidth');
    return saved ? parseInt(saved, 10) : 320; // Default 320px
  });
  const [isResizingRight, setIsResizingRight] = useState(false);

  // Chat sidebar collapsed state (default: collapsed for immersive mode)
  const [chatSidebarCollapsed, setChatSidebarCollapsed] = useState(() => {
    const saved = localStorage.getItem('chatSidebarCollapsed');
    return saved !== null ? saved === 'true' : true;
  });

  // Resizable sidebar state
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem('chatSidebarWidth');
    return saved ? parseInt(saved, 10) : 256; // Default 256px (w-64)
  });
  const [isResizing, setIsResizing] = useState(false);

  // Persist chat sidebar collapsed state
  useEffect(() => {
    localStorage.setItem('chatSidebarCollapsed', String(chatSidebarCollapsed));
  }, [chatSidebarCollapsed]);

  // Persist right sidebar state
  useEffect(() => {
    localStorage.setItem('rightSidebarCollapsed', String(rightSidebarCollapsed));
  }, [rightSidebarCollapsed]);

  useEffect(() => {
    localStorage.setItem('rightSidebarWidth', String(rightSidebarWidth));
  }, [rightSidebarWidth]);

  // Persist selected agent to localStorage
  useEffect(() => {
    if (selectedAgentId) {
      localStorage.setItem('lastSelectedAgentId', selectedAgentId);
    }
  }, [selectedAgentId]);

  // Load workDir from localStorage when agent changes
  useEffect(() => {
    if (selectedAgentId) {
      const savedWorkDir = localStorage.getItem(`workDir_${selectedAgentId}`);
      setWorkDir(savedWorkDir, true); // true = restoring from storage, don't reset session
    } else {
      setWorkDir(null, true);
    }
  }, [selectedAgentId]);

  // Persist workDir to localStorage when it changes
  useEffect(() => {
    if (selectedAgentId) {
      if (workDir) {
        localStorage.setItem(`workDir_${selectedAgentId}`, workDir);
      } else {
        localStorage.removeItem(`workDir_${selectedAgentId}`);
      }
    }
  }, [selectedAgentId, workDir]);

  // Fetch agents list
  const { data: agents = [], isLoading: isLoadingAgents } = useQuery({
    queryKey: ['agents'],
    queryFn: agentsService.list,
  });

  // Fetch skills list
  const { data: skills = [], isLoading: isLoadingSkills } = useQuery({
    queryKey: ['skills'],
    queryFn: skillsService.list,
  });

  // Fetch MCP servers list
  const { data: mcpServers = [], isLoading: isLoadingMCPs } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: mcpService.list,
  });

  // Fetch plugins list
  const { data: plugins = [], isLoading: isLoadingPlugins } = useQuery({
    queryKey: ['plugins'],
    queryFn: pluginsService.listPlugins,
  });

  // Fetch chat sessions for the selected agent
  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['chatSessions', selectedAgentId],
    queryFn: () => chatService.listSessions(selectedAgentId || undefined),
    enabled: !!selectedAgentId,
  });

  // Task support: fetch task if taskId URL parameter is present
  const taskId = searchParams.get('taskId');
  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => taskId ? tasksService.get(taskId) : null,
    enabled: !!taskId,
  });

  // Load session messages helper (defined here so it can be used by task effect below)
  const loadSessionMessages = useCallback(async (sid: string) => {
    setIsLoadingHistory(true);
    try {
      const sessionMessages = await chatService.getSessionMessages(sid);
      // Convert to Message format
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

  // When task is loaded, set agent and load session messages
  useEffect(() => {
    if (task) {
      if (task.agentId && task.agentId !== selectedAgentId) {
        setSelectedAgentId(task.agentId);
      }
      // Load the session messages if task has a sessionId
      if (task.sessionId && task.sessionId !== sessionId) {
        loadSessionMessages(task.sessionId);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Only run when task changes, not when selectedAgentId/sessionId change
  }, [task]);

  // Sync taskMode URL parameter with runAsTask state
  useEffect(() => {
    const taskMode = searchParams.get('taskMode') === 'true';
    setRunAsTask(taskMode);
  }, [searchParams]);

  // Memoize grouped sessions to avoid recalculating on every render
  const groupedSessions = useMemo(() => groupSessionsByTime(sessions), [sessions]);

  // Fetch agent's effective working directory
  const { data: agentWorkDir } = useQuery({
    queryKey: ['agentWorkDir', selectedAgentId],
    queryFn: () => agentsService.getWorkingDirectory(selectedAgentId!),
    enabled: !!selectedAgentId,
  });

  // Compute effective base path for file browser: workDir overrides agent's default
  const effectiveBasePath = workDir || agentWorkDir?.path;

  // Get the selected agent object
  const selectedAgent = agents.find((a) => a.id === selectedAgentId);

  // Get configured skills for selected agent
  // If allowAllSkills is true, show all skills; otherwise filter by skillIds
  const agentSkills = selectedAgent?.allowAllSkills
    ? skills
    : selectedAgent?.skillIds
      ? skills.filter((s) => selectedAgent.skillIds.includes(s.id))
      : [];

  // Get configured MCPs for selected agent
  const agentMCPs = selectedAgent?.mcpIds
    ? mcpServers.filter((m) => selectedAgent.mcpIds.includes(m.id))
    : [];

  // Get configured plugins for selected agent
  const agentPlugins = selectedAgent?.pluginIds
    ? plugins.filter((p) => selectedAgent.pluginIds.includes(p.id))
    : [];

  // Determine if skills and MCPs should be enabled based on agent config
  const enableSkills = selectedAgent?.allowAllSkills || agentSkills.length > 0 || agentPlugins.length > 0;
  const enableMCP = agentMCPs.length > 0;

  // Reset session when work directory changes by user action (not when restoring from localStorage)
  // Changing the work directory means starting a new conversation context
  const prevWorkDirRef = useRef<string | null | undefined>(undefined); // undefined = uninitialized
  useEffect(() => {
    // Skip if we're restoring from localStorage (agent switch or page mount)
    if (isRestoringWorkDirRef.current) {
      isRestoringWorkDirRef.current = false;
      prevWorkDirRef.current = workDir;
      return;
    }

    // Only reset if workDir actually changed by user action
    if (prevWorkDirRef.current !== undefined && prevWorkDirRef.current !== workDir) {
      prevWorkDirRef.current = workDir;

      // Reset session state - backend will create a new session on next message
      setSessionId(undefined);
      setMessages([]);
      setPendingQuestion(null);

      // Show a message indicating the context change
      if (selectedAgent) {
        const contextMessage = workDir
          ? `Working directory changed to: ${workDir}`
          : 'Working directory cleared';
        setMessages([
          {
            id: Date.now().toString(),
            role: 'assistant',
            content: [
              {
                type: 'text',
                text: `${contextMessage}\n\nHello, I'm ${selectedAgent.name}. ${selectedAgent.description || 'How can I assist you today?'}`,
              },
            ],
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    } else {
      prevWorkDirRef.current = workDir;
    }
  }, [workDir, selectedAgent]);

  // Toggle chat sidebar
  const toggleChatSidebar = () => {
    setChatSidebarCollapsed((prev) => !prev);
  };

  // Handle session selection from history
  const handleSelectSession = useCallback(async (session: ChatSession) => {
    // Set the agent for this session
    if (session.agentId && session.agentId !== selectedAgentId) {
      setSelectedAgentId(session.agentId);
    }
    // Restore workDir from session (use true to mark as restoring, avoid session reset)
    setWorkDir(session.workDir || null, true);
    await loadSessionMessages(session.id);
    // Collapse sidebar after selection for immersive experience
    setChatSidebarCollapsed(true);
  }, [selectedAgentId, loadSessionMessages]);

  // Handle new chat
  const handleNewChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
    if (selectedAgent) {
      // Add welcome message
      setMessages([
        {
          id: '1',
          role: 'assistant',
          content: [
            {
              type: 'text',
              text: `Hello, I'm ${selectedAgent.name}. ${selectedAgent.description || 'How can I assist you today?'}`,
            },
          ],
          timestamp: new Date().toISOString(),
        },
      ]);
    }
    // Collapse sidebar after creating new chat
    setChatSidebarCollapsed(true);
  }, [selectedAgent]);

  // Handle delete session
  const handleDeleteSession = async (session: ChatSession) => {
    try {
      await chatService.deleteSession(session.id);
      refetchSessions();
      // If deleted the current session, start a new chat
      if (sessionId === session.id) {
        handleNewChat();
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
    setDeleteConfirmSession(null);
  };

  // Handle URL parameter for agent selection
  useEffect(() => {
    const agentIdFromUrl = searchParams.get('agentId');
    if (agentIdFromUrl && agents.length > 0) {
      const agent = agents.find((a) => a.id === agentIdFromUrl);
      if (agent && selectedAgentId !== agentIdFromUrl) {
        setSelectedAgentId(agentIdFromUrl);
        // Initialize chat with welcome message
        setMessages([
          {
            id: '1',
            role: 'assistant',
            content: [
              {
                type: 'text',
                text: `Hello, I'm ${agent.name}. ${agent.description || 'How can I assist you today?'}`,
              },
            ],
            timestamp: new Date().toISOString(),
          },
        ]);
        setSessionId(undefined);
        // Clear the URL parameter after processing
        setSearchParams({});
      }
    }
  }, [agents, searchParams, selectedAgentId, setSearchParams]);

  // Restore last selected agent on mount (validate it still exists)
  // Note: Using selectedAgentId instead of selectedAgent in deps to avoid infinite loop
  useEffect(() => {
    if (agents.length > 0 && !selectedAgentId) {
      const lastId = localStorage.getItem('lastSelectedAgentId');
      if (lastId) {
        const existingAgent = agents.find((a) => a.id === lastId);
        if (existingAgent) {
          setSelectedAgentId(lastId);
          // Show welcome message for restored agent
          setMessages([
            {
              id: '1',
              role: 'assistant',
              content: [
                {
                  type: 'text',
                  text: `Hello, I'm ${existingAgent.name}. ${existingAgent.description || 'How can I assist you today?'}`,
                },
              ],
              timestamp: new Date().toISOString(),
            },
          ]);
        } else {
          // Agent was deleted, clear localStorage
          localStorage.removeItem('lastSelectedAgentId');
        }
      }
    }
  }, [agents, selectedAgentId]);

  // Refetch sessions when conversation completes
  useEffect(() => {
    if (sessionId && !isStreaming) {
      refetchSessions();
    }
  }, [sessionId, isStreaming, refetchSessions]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Build content array from text and attachments
  const buildContentArray = useCallback(
    async (text: string, fileAttachments: FileAttachment[]): Promise<ContentBlock[]> => {
      const content: ContentBlock[] = [];

      // Add text content if present
      if (text.trim()) {
        content.push({ type: 'text', text } as ContentBlock);
      }

      // Process each attachment
      for (const att of fileAttachments) {
        if (!att.base64) continue; // Skip if not yet loaded

        if (att.type === 'image') {
          content.push({
            type: 'image',
            source: {
              type: 'base64',
              media_type: att.mediaType,
              data: att.base64,
            },
          } as unknown as ContentBlock);
        } else if (att.type === 'pdf') {
          content.push({
            type: 'document',
            source: {
              type: 'base64',
              media_type: 'application/pdf',
              data: att.base64,
            },
          } as unknown as ContentBlock);
        } else if ((att.type === 'text' || att.type === 'csv') && selectedAgentId) {
          // Upload TXT/CSV to workspace and reference them
          try {
            const result = await workspaceService.uploadFile(
              selectedAgentId,
              att.name,
              att.base64
            );
            content.push({
              type: 'text',
              text: `[Attached file: ${att.name}] saved at ${result.path} - use Read tool to access`,
            } as ContentBlock);
          } catch (err) {
            console.error('Failed to upload file:', err);
            content.push({
              type: 'text',
              text: `[Failed to attach file: ${att.name}]`,
            } as ContentBlock);
          }
        }
      }

      return content;
    },
    [selectedAgentId]
  );

  const handleSendMessage = async () => {
    // Save input value before any async operations or state changes
    const messageText = inputValue;

    // Allow sending if there's text OR attachments (or both)
    const hasText = messageText.trim().length > 0;
    const hasAttachments = attachments.length > 0 && attachments.some((a) => a.base64);

    if ((!hasText && !hasAttachments) || isStreaming || !selectedAgentId) return;

    // Intercept /plugin commands - these are handled locally, not sent to agent
    if (messageText.trim().startsWith('/plugin')) {
      const command = messageText.trim();
      setInputValue('');
      await handlePluginCommand(command);
      return;
    }

    // Build content array from text and attachments
    const content = await buildContentArray(messageText, attachments);
    if (content.length === 0) return;

    // If runAsTask is enabled, create a background task instead of streaming
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
        setRunAsTask(false); // Reset toggle after creating task
        // Invalidate task queries to refresh the list
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
        // Navigate to tasks page
        navigate('/tasks');
      } catch (error) {
        console.error('Failed to create task:', error);
        // Show error to user via a simple alert (could be improved with toast)
        alert(t('chat.taskCreateFailed'));
      }
      return;
    }

    // Display text for user message (show text + attachment indicators)
    const displayText = hasText ? messageText : '[Attachments]';
    const userMessageContent: ContentBlock[] = [{ type: 'text', text: displayText }];
    if (hasAttachments) {
      const attachmentNames = attachments.map((a) => a.name).join(', ');
      if (hasText) {
        userMessageContent.push({ type: 'text', text: `📎 ${attachmentNames}` });
      }
    }

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: userMessageContent,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    clearAttachments();
    setIsStreaming(true);

    // Create a placeholder for streaming response
    const assistantMessageId = (Date.now() + 1).toString();
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: [],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);

    // Start streaming
    // Only send content array if there are attachments, otherwise send message string
    const abort = chatService.streamChat(
      {
        agentId: selectedAgentId,
        ...(hasAttachments ? { content } : { message: messageText }),
        sessionId,
        enableSkills,
        enableMCP,
        addDirs: workDir ? [workDir] : undefined,
      },
      (event: StreamEvent) => {
        // Handle session_start event to get session_id early for stop functionality
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          // /clear command executed - update to new session and clear messages
          console.log('Session cleared:', event.oldSessionId, '->', event.newSessionId);
          setSessionId(event.newSessionId);
          setMessages([]); // Clear messages since old session is deleted
          // Refetch sessions list to update sidebar
          queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantMessageId) return msg;
              // Deduplicate content blocks
              const existingContent = msg.content;
              const newContent = event.content!.filter((newBlock) => {
                return !existingContent.some((existing) => {
                  if (newBlock.type !== existing.type) return false;
                  if (newBlock.type === 'text' && existing.type === 'text') {
                    return newBlock.text === existing.text;
                  }
                  if (newBlock.type === 'tool_use' && existing.type === 'tool_use') {
                    return newBlock.id === existing.id;
                  }
                  if (newBlock.type === 'tool_result' && existing.type === 'tool_result') {
                    return newBlock.toolUseId === existing.toolUseId;
                  }
                  return false;
                });
              });
              return { ...msg, content: [...existingContent, ...newContent], model: event.model };
            })
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          // Store pending question for user to answer
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          // Set session ID from the event if available
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Add question to messages as a content block
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'permission_request') {
          // Handle permission request for dangerous commands
          // The stream ends here - frontend will call /permission-continue on user decision
          setPendingPermission({
            requestId: event.requestId!,
            toolName: event.toolName!,
            toolInput: event.toolInput!,
            reason: event.reason!,
            options: event.options || ['approve', 'deny'],
          });
          // Set session ID from the event if available
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Stream ends after permission_request, set isStreaming to false
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [{ type: 'text', text: `Error: ${errorMsg}` }],
                  }
                : msg
            )
          );
        }
      },
      (error) => {
        console.error('Stream error:', error);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? {
                  ...msg,
                  content: [{ type: 'text', text: `Connection error: ${error.message}` }],
                }
              : msg
          )
        );
        setIsStreaming(false);
      },
      () => {
        setIsStreaming(false);
      }
    );

    abortRef.current = abort;
  };

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (toolUseId: string, answers: Record<string, string>) => {
    if (!selectedAgentId || !sessionId) return;

    setPendingQuestion(null);
    setIsStreaming(true);

    // Create assistant message placeholder for continued response
    const assistantMessageId = Date.now().toString();
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: [],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);

    const abort = chatService.streamAnswerQuestion(
      {
        agentId: selectedAgentId,
        sessionId,
        toolUseId,
        answers,
        enableSkills,
        enableMCP,
      },
      (event: StreamEvent) => {
        // Handle session_start event to get session_id early for stop functionality
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          // /clear command executed - update to new session and clear messages
          console.log('Session cleared:', event.oldSessionId, '->', event.newSessionId);
          setSessionId(event.newSessionId);
          setMessages([]); // Clear messages since old session is deleted
          // Refetch sessions list to update sidebar
          queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantMessageId) return msg;
              // Deduplicate content blocks
              const existingContent = msg.content;
              const newContent = event.content!.filter((newBlock) => {
                return !existingContent.some((existing) => {
                  if (newBlock.type !== existing.type) return false;
                  if (newBlock.type === 'text' && existing.type === 'text') {
                    return newBlock.text === existing.text;
                  }
                  if (newBlock.type === 'tool_use' && existing.type === 'tool_use') {
                    return newBlock.id === existing.id;
                  }
                  if (newBlock.type === 'tool_result' && existing.type === 'tool_result') {
                    return newBlock.toolUseId === existing.toolUseId;
                  }
                  return false;
                });
              });
              return { ...msg, content: [...existingContent, ...newContent], model: event.model };
            })
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          // Set session ID from the event if available
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'permission_request') {
          // Handle permission request for dangerous commands
          // Stream ends here - frontend will call /permission-continue on user decision
          setPendingPermission({
            requestId: event.requestId!,
            toolName: event.toolName!,
            toolInput: event.toolInput!,
            reason: event.reason!,
            options: event.options || ['approve', 'deny'],
          });
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Stream ends after permission_request
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [{ type: 'text', text: `Error: ${errorMsg}` }] }
                : msg
            )
          );
        }
      },
      (error) => {
        console.error('Stream error:', error);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: [{ type: 'text', text: `Connection error: ${error.message}` }] }
              : msg
          )
        );
        setIsStreaming(false);
      },
      () => {
        setIsStreaming(false);
      }
    );

    abortRef.current = abort;
  };

  // Handle permission decision for dangerous commands
  const handlePermissionDecision = async (decision: 'approve' | 'deny', feedback?: string) => {
    if (!pendingPermission || !sessionId || !selectedAgentId) return;

    setIsPermissionLoading(true);
    setPendingPermission(null);

    // Add a message showing the decision
    const decisionText = decision === 'approve'
      ? '✓ Command approved, executing...'
      : '✗ Command denied by user';
    const decisionMessage: Message = {
      id: Date.now().toString(),
      role: 'assistant',
      content: [{ type: 'text', text: decisionText }],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, decisionMessage]);

    // For deny, we need to notify the backend so the hook can resume
    if (decision === 'deny') {
      try {
        await chatService.submitPermissionDecision({
          sessionId,
          requestId: pendingPermission.requestId,
          decision: 'deny',
          feedback,
        });
      } catch (error) {
        console.error('Failed to submit deny decision:', error);
      } finally {
        setIsPermissionLoading(false);
        setIsStreaming(false);
      }
      return;
    }

    // For approve, continue with streaming
    setIsStreaming(true);

    // Create assistant message placeholder for continued response
    const assistantMessageId = (Date.now() + 1).toString();
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: [],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);

    // Start streaming with permission continue
    const abort = chatService.streamPermissionContinue(
      {
        sessionId,
        requestId: pendingPermission.requestId,
        decision,
        feedback,
        enableSkills,
        enableMCP,
      },
      (event: StreamEvent) => {
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'session_cleared' && event.newSessionId) {
          // /clear command executed - update to new session and clear messages
          console.log('Session cleared:', event.oldSessionId, '->', event.newSessionId);
          setSessionId(event.newSessionId);
          setMessages([]); // Clear messages since old session is deleted
          // Refetch sessions list to update sidebar
          queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id !== assistantMessageId) return msg;
              // Deduplicate content blocks
              const existingContent = msg.content;
              const newContent = event.content!.filter((newBlock) => {
                return !existingContent.some((existing) => {
                  if (newBlock.type !== existing.type) return false;
                  if (newBlock.type === 'text' && existing.type === 'text') {
                    return newBlock.text === existing.text;
                  }
                  if (newBlock.type === 'tool_use' && existing.type === 'tool_use') {
                    return newBlock.id === existing.id;
                  }
                  if (newBlock.type === 'tool_result' && existing.type === 'tool_result') {
                    return newBlock.toolUseId === existing.toolUseId;
                  }
                  return false;
                });
              });
              return { ...msg, content: [...existingContent, ...newContent], model: event.model };
            })
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'permission_request') {
          // Handle another permission request
          setPendingPermission({
            requestId: event.requestId!,
            toolName: event.toolName!,
            toolInput: event.toolInput!,
            reason: event.reason!,
            options: event.options || ['approve', 'deny'],
          });
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          setIsStreaming(false);
        } else if (event.type === 'permission_acknowledged') {
          // Permission decision sent, remove empty placeholder message
          // The original stream will continue with actual results
          setMessages((prev) => prev.filter((msg) => msg.id !== assistantMessageId));
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [{ type: 'text', text: `Error: ${errorMsg}` }] }
                : msg
            )
          );
        }
      },
      (error) => {
        console.error('Stream error:', error);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMessageId
              ? { ...msg, content: [{ type: 'text', text: `Connection error: ${error.message}` }] }
              : msg
          )
        );
        setIsStreaming(false);
        setIsPermissionLoading(false);
      },
      () => {
        setIsStreaming(false);
        setIsPermissionLoading(false);
      }
    );

    abortRef.current = abort;
  };

  // Handle input change with slash command detection
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInputValue(value);

    // Show suggestions when input starts with '/' and has no space yet
    if (value.startsWith('/') && !value.includes(' ')) {
      setShowCommandSuggestions(true);
      setSelectedCommandIndex(0);
    } else {
      setShowCommandSuggestions(false);
    }
  };

  // Handle folder selection for "Work in a folder" feature
  const handleSelectFolder = useCallback(async () => {
    // Check if running in Tauri environment
    if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) {
      // Web browser mode: open folder picker modal
      setIsFolderPickerOpen(true);
      return;
    }

    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const selected = await open({
        directory: true,
        multiple: false,
        title: 'Select a folder to work in',
      });
      if (selected && typeof selected === 'string') {
        setWorkDir(selected);
      }
    } catch (error) {
      console.error('Failed to select folder:', error);
    }
  }, []);

  // Handle folder selection from web modal
  const handleFolderPickerSelect = useCallback((path: string) => {
    setWorkDir(path);
    setIsFolderPickerOpen(false);
  }, []);

  // Clear work directory
  const handleClearWorkDir = useCallback(() => {
    setWorkDir(null);
  }, []);

  // Handle paste event for images
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            imageFiles.push(file);
          }
        }
      }
      if (imageFiles.length > 0) {
        e.preventDefault();
        addFiles(imageFiles);
      }
    },
    [addFiles]
  );

  // Drag handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      setIsDragging(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files')) {
      e.preventDefault();
      setIsDragging(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files);
      if (files.length > 0) {
        addFiles(files);
      }
    },
    [addFiles]
  );

  // Filter commands based on input
  const filteredCommands = slashCommands.filter((cmd) =>
    cmd.name.toLowerCase().startsWith(inputValue.toLowerCase())
  );

  // Handle command selection
  const handleSelectCommand = (command: string) => {
    setInputValue(command + ' ');
    setShowCommandSuggestions(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Handle slash command navigation
    if (showCommandSuggestions && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev + 1) % filteredCommands.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedCommandIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
        return;
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault();
        handleSelectCommand(filteredCommands[selectedCommandIndex].name);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowCommandSuggestions(false);
        return;
      }
    }

    // Normal enter to send
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Handle stop button
  const handleStop = async () => {
    if (!sessionId) return;

    try {
      // Abort the current stream if there's an abort function
      if (abortRef.current) {
        abortRef.current();
        abortRef.current = null;
      }

      // Call the backend to interrupt the session
      await chatService.stopSession(sessionId);

      // Add a system message indicating the stop
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: [{ type: 'text', text: '⏹️ Generation stopped by user.' }],
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      console.error('Failed to stop session:', error);
    } finally {
      setIsStreaming(false);
    }
  };

  // Handle /plugin slash commands
  const handlePluginCommand = async (command: string): Promise<boolean> => {
    const parts = command.trim().split(/\s+/);
    if (parts[0] !== '/plugin') return false;

    const subCommand = parts[1];
    const args = parts.slice(2).join(' ');

    // Add user message showing the command
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: [{ type: 'text', text: command }],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);

    // Create assistant message for response
    const assistantMessageId = (Date.now() + 1).toString();

    try {
      let responseText = '';

      switch (subCommand) {
        case 'list': {
          const plugins = await pluginsService.listPlugins();
          if (plugins.length === 0) {
            responseText = '📦 No plugins installed.\n\nUse `/plugin install {name}@{marketplace}` to install a plugin.';
          } else {
            responseText = '📦 **Installed Plugins:**\n\n';
            responseText += '| Name | Version | Source | Status |\n';
            responseText += '|------|---------|--------|--------|\n';
            for (const plugin of plugins) {
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
            // Parse name@marketplace format
            const atIndex = args.lastIndexOf('@');
            if (atIndex === -1) {
              responseText = '❌ **Invalid format.** Use: `/plugin install {name}@{marketplace}`';
            } else {
              const pluginName = args.substring(0, atIndex);
              const marketplaceName = args.substring(atIndex + 1);

              // Find marketplace ID by name
              const marketplaces = await pluginsService.listMarketplaces();
              const marketplace = marketplaces.find(
                (m) => m.name.toLowerCase() === marketplaceName.toLowerCase()
              );

              if (!marketplace) {
                responseText = `❌ **Marketplace not found:** "${marketplaceName}"\n\nAvailable marketplaces:\n${marketplaces.map((m) => `- ${m.name}`).join('\n') || 'No marketplaces configured. Add one from the Plugins page.'}`;
              } else {
                const plugin = await pluginsService.installPlugin({
                  pluginName,
                  marketplaceId: marketplace.id,
                });
                responseText = `✅ **Plugin installed successfully!**\n\n**${plugin.name}** v${plugin.version}\n\n`;
                if (plugin.installedSkills.length > 0) {
                  responseText += `- Skills: ${plugin.installedSkills.join(', ')}\n`;
                }
                if (plugin.installedCommands.length > 0) {
                  responseText += `- Commands: ${plugin.installedCommands.join(', ')}\n`;
                }
                if (plugin.installedAgents.length > 0) {
                  responseText += `- Agents: ${plugin.installedAgents.join(', ')}\n`;
                }
                if (plugin.installedHooks.length > 0) {
                  responseText += `- Hooks: ${plugin.installedHooks.join(', ')}\n`;
                }
                if (plugin.installedMcpServers.length > 0) {
                  responseText += `- MCP Servers: ${plugin.installedMcpServers.join(', ')}\n`;
                }
              }
            }
          }
          break;
        }

        case 'uninstall': {
          if (!args) {
            responseText = '❌ **Usage:** `/plugin uninstall {plugin-id}`\n\nUse `/plugin list` to see installed plugins and their IDs.';
          } else {
            const result = await pluginsService.uninstallPlugin(args);
            responseText = `✅ **Plugin uninstalled successfully!**\n\n`;
            if (result.removedSkills.length > 0) {
              responseText += `- Removed skills: ${result.removedSkills.join(', ')}\n`;
            }
            if (result.removedCommands.length > 0) {
              responseText += `- Removed commands: ${result.removedCommands.join(', ')}\n`;
            }
            if (result.removedAgents.length > 0) {
              responseText += `- Removed agents: ${result.removedAgents.join(', ')}\n`;
            }
            if (result.removedHooks.length > 0) {
              responseText += `- Removed hooks: ${result.removedHooks.join(', ')}\n`;
            }
          }
          break;
        }

        case 'marketplace': {
          const marketplaceSubCommand = parts[2];
          if (marketplaceSubCommand === 'list') {
            const marketplaces = await pluginsService.listMarketplaces();
            if (marketplaces.length === 0) {
              responseText = '🏪 No marketplaces configured.\n\nAdd a marketplace from the Plugins page to browse and install plugins.';
            } else {
              responseText = '🏪 **Available Marketplaces:**\n\n';
              responseText += '| Name | URL | Plugins |\n';
              responseText += '|------|-----|--------|\n';
              for (const m of marketplaces) {
                responseText += `| ${m.name} | ${m.url} | ${m.cachedPlugins?.length || '-'} |\n`;
              }
              responseText += '\n\nUse `/plugin install {name}@{marketplace}` to install a plugin.';
            }
          } else {
            responseText = `❌ **Unknown marketplace command:** "${marketplaceSubCommand || ''}"\n\nAvailable commands:\n- \`/plugin marketplace list\` - List available marketplaces`;
          }
          break;
        }

        default:
          responseText = `❌ **Unknown plugin command:** "${subCommand}"\n\nAvailable commands:\n- \`/plugin list\` - List installed plugins\n- \`/plugin install {name}@{marketplace}\` - Install a plugin\n- \`/plugin uninstall {id}\` - Uninstall a plugin\n- \`/plugin marketplace list\` - List available marketplaces`;
      }

      // Add response message
      setMessages((prev) => [
        ...prev,
        {
          id: assistantMessageId,
          role: 'assistant',
          content: [{ type: 'text', text: responseText }],
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'An error occurred';
      setMessages((prev) => [
        ...prev,
        {
          id: assistantMessageId,
          role: 'assistant',
          content: [{ type: 'text', text: `❌ **Error:** ${errorMessage}` }],
          timestamp: new Date().toISOString(),
        },
      ]);
    }

    return true;
  };

  const handleSelectAgent = (agentId: string) => {
    const agent = agents.find((a) => a.id === agentId);
    if (!agent) return;

    setSelectedAgentId(agentId);
    // Reset chat state when switching agents
    setMessages([
      {
        id: '1',
        role: 'assistant',
        content: [
          {
            type: 'text',
            text: `Hello, I'm ${agent.name}. ${agent.description || 'How can I assist you today?'}`,
          },
        ],
        timestamp: new Date().toISOString(),
      },
    ]);
    setSessionId(undefined);
    // Collapse sidebar after selecting agent
    setChatSidebarCollapsed(true);
  };

  // Open agent edit modal
  const handleOpenEditAgent = () => {
    if (selectedAgent) {
      setIsEditAgentOpen(true);
    }
  };

  // Save agent changes (edit mode only, so agent will always be Agent type)
  const handleSaveAgent = async (agent: Agent | AgentCreateRequest) => {
    // In edit mode, the agent will always have an id
    if ('id' in agent) {
      await agentsService.update(agent.id, agent);
      // Invalidate React Query cache so the agents list gets updated
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    }
  };

  // Format timestamp for display
  const formatTimestamp = (timestamp: string | undefined) => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return '';

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  // Handle sidebar resizing
  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;

      const newWidth = e.clientX;
      // Min width: 200px, Max width: 600px
      if (newWidth >= 200 && newWidth <= 600) {
        setSidebarWidth(newWidth);
        localStorage.setItem('chatSidebarWidth', newWidth.toString());
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // Handle right sidebar resizing
  const handleMouseDownRight = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizingRight(true);
  };

  useEffect(() => {
    const handleMouseMoveRight = (e: MouseEvent) => {
      if (!isResizingRight) return;

      const newWidth = window.innerWidth - e.clientX;
      // Min width: 240px, Max width: 600px
      if (newWidth >= 240 && newWidth <= 600) {
        setRightSidebarWidth(newWidth);
      }
    };

    const handleMouseUpRight = () => {
      setIsResizingRight(false);
    };

    if (isResizingRight) {
      document.addEventListener('mousemove', handleMouseMoveRight);
      document.addEventListener('mouseup', handleMouseUpRight);
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMoveRight);
      document.removeEventListener('mouseup', handleMouseUpRight);
      if (!isResizing) {
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };
  }, [isResizingRight, isResizing]);

  // Toggle right sidebar
  const toggleRightSidebar = useCallback(() => {
    setRightSidebarCollapsed((prev) => !prev);
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Enhanced Chat Header - spans full width */}
      <div className="h-16 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
        <div className="flex items-center gap-3">
          {/* Agent info - clickable to expand sidebar */}
          {selectedAgent ? (
            <button
              onClick={toggleChatSidebar}
              className="flex items-center gap-3 hover:bg-[var(--color-hover)] rounded-lg px-3 py-2 transition-colors"
            >
              <div className="w-10 h-10 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0">
                <span className="material-symbols-outlined text-primary">history</span>
              </div>
              <div className="text-left">
                <h1 className="font-semibold text-[var(--color-text)]">{selectedAgent.name}</h1>
                <p className="text-xs text-[var(--color-text-muted)] truncate max-w-[200px]">
                  {selectedAgent.description || 'AI Assistant'}
                </p>
              </div>
              <span className="material-symbols-outlined text-[var(--color-text-muted)] text-sm">expand_more</span>
            </button>
          ) : (
            <button
              onClick={toggleChatSidebar}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
            >
              <span className="material-symbols-outlined">history</span>
              Select Agent
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {selectedAgent && (
            <span className="px-2 py-1 text-xs bg-[var(--color-hover)] text-[var(--color-text-muted)] rounded">
              {selectedAgent.model || 'Default Model'}
            </span>
          )}
          <button
            onClick={toggleRightSidebar}
            disabled={!selectedAgent}
            className={clsx(
              'p-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
              !rightSidebarCollapsed
                ? 'text-primary bg-primary/10 hover:bg-primary/20'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            )}
            title={rightSidebarCollapsed ? 'Open file browser' : 'Close file browser'}
          >
            <span className="material-symbols-outlined">folder_open</span>
          </button>
          <button
            onClick={handleOpenEditAgent}
            disabled={!selectedAgent}
            className="p-2 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title={t('chat.editAgentSettings')}
          >
            <span className="material-symbols-outlined">settings</span>
          </button>
        </div>
      </div>

      {/* Content area below header */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chat History Sidebar - part of flex layout, slides from left */}
        {!chatSidebarCollapsed && (
          <div
            className="flex flex-col bg-[var(--color-card)] border-r border-[var(--color-border)] relative flex-shrink-0"
            style={{ width: sidebarWidth }}
          >
            {/* Sidebar Header with Close Button */}
            <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-primary text-lg">chat</span>
                <span className="font-medium text-[var(--color-text)] text-sm">{t('chat.history')}</span>
              </div>
              <button
                onClick={toggleChatSidebar}
                className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
                aria-label="Close chat sidebar"
              >
                <span className="material-symbols-outlined text-lg">close</span>
              </button>
            </div>

            {/* Agent Selector */}
            <div className="p-3 border-b border-[var(--color-border)]">
              {isLoadingAgents ? (
                <div className="flex items-center justify-center py-3">
                  <Spinner size="sm" />
                </div>
              ) : agents.length === 0 ? (
                <div className="text-sm text-[var(--color-text-muted)] text-center py-3">
                  {t('chat.noAgentsAvailable')}
                  <br />
                  <a href="/agents" className="text-primary hover:underline">
                    {t('chat.createAgentFirst')}
                  </a>
                </div>
              ) : (
                <Dropdown
                  label={t('chat.selectAgent')}
                  placeholder={t('chat.chooseAgent')}
                  options={agents.map((agent) => ({
                    id: agent.id,
                    name: agent.name,
                    description: agent.description,
                  }))}
                  selectedId={selectedAgentId}
                  onChange={handleSelectAgent}
                />
              )}
            </div>

            {/* Header with New Chat button */}
            <div className="p-3 border-b border-[var(--color-border)]">
              <button
                onClick={handleNewChat}
                disabled={!selectedAgentId}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
              >
                <span className="material-symbols-outlined text-xl">add</span>
                {t('chat.newChat')}
              </button>
            </div>

            {/* Chat History List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {groupedSessions.length === 0 ? (
                <p className="px-3 py-2 text-xs text-[var(--color-text-muted)]">{t('chat.noHistory')}</p>
              ) : (
                groupedSessions.map((group, groupIndex) => (
                  <div key={group.group}>
                    <p className={clsx(
                      'px-3 py-2 text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider',
                      groupIndex > 0 && 'mt-3'
                    )}>
                      {t(timeGroupLabelKey[group.group])}
                    </p>
                    {group.sessions.map((session) => {
                      const agentForSession = agents.find((a) => a.id === session.agentId);
                      return (
                        <div
                          key={session.id}
                          className={clsx(
                            'group w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-left transition-colors cursor-pointer',
                            sessionId === session.id
                              ? 'bg-primary text-white'
                              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
                          )}
                          onClick={() => handleSelectSession(session)}
                        >
                          <span className="material-symbols-outlined text-lg flex-shrink-0">chat_bubble_outline</span>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium truncate">{session.title}</p>
                            <p className="text-xs opacity-70">
                              {agentForSession?.name || 'Unknown'} • {formatTimestamp(session.lastAccessedAt)}
                            </p>
                          </div>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setDeleteConfirmSession(session);
                            }}
                            className={clsx(
                              'p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity',
                              sessionId === session.id
                                ? 'hover:bg-white/20 text-white'
                                : 'hover:bg-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                            )}
                          >
                            <span className="material-symbols-outlined text-sm">delete</span>
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </div>

            {/* Resize Handle */}
            <div
              className={clsx(
                'absolute top-0 right-0 w-1 h-full cursor-ew-resize hover:bg-primary/50 transition-colors z-10',
                isResizing && 'bg-primary'
              )}
              onMouseDown={handleMouseDown}
            >
              <div className="absolute inset-y-0 -right-1 w-3" />
            </div>
          </div>
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
          {/* Messages or Empty State */}
        {!selectedAgentId ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <span className="material-symbols-outlined text-6xl text-[var(--color-text-muted)] mb-4">smart_toy</span>
              <h2 className="text-xl font-semibold text-[var(--color-text)] mb-2">{t('chat.selectAgent')}</h2>
              <p className="text-[var(--color-text-muted)] max-w-md">
                {t('chat.noAgent')}
              </p>
              {agents.length === 0 && !isLoadingAgents && (
                <a
                  href="/agents"
                  className="inline-flex items-center gap-2 mt-4 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
                >
                  <span className="material-symbols-outlined">add</span>
                  {t('agents.createAgent')}
                </a>
              )}
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
            <div className="p-6">
              <div className="max-w-3xl mx-auto">
                {/* Background Task Mode Toggle - Prominent Banner */}
                <div
                  onClick={() => !isStreaming && setRunAsTask(!runAsTask)}
                  className={clsx(
                    'mb-3 flex items-center justify-between px-4 py-2.5 rounded-xl cursor-pointer transition-all',
                    runAsTask
                      ? 'bg-gradient-to-r from-green-500/20 to-emerald-500/20 border border-green-500/50 shadow-[0_0_15px_rgba(34,197,94,0.15)]'
                      : 'bg-[var(--color-hover)]/50 border border-[var(--color-border)] hover:border-[var(--color-text-muted)]/50',
                    isStreaming && 'opacity-50 cursor-not-allowed'
                  )}
                >
                  <div className="flex items-center gap-3">
                    <span className={clsx(
                      'material-symbols-outlined text-xl',
                      runAsTask ? 'text-green-500' : 'text-[var(--color-text-muted)]'
                    )}>
                      {runAsTask ? 'rocket_launch' : 'task_alt'}
                    </span>
                    <div>
                      <p className={clsx(
                        'text-sm font-medium',
                        runAsTask ? 'text-green-500' : 'text-[var(--color-text)]'
                      )}>
                        {t('chat.runAsTask')}
                      </p>
                      <p className="text-xs text-[var(--color-text-muted)]">
                        {t('chat.runAsTaskDescription')}
                      </p>
                    </div>
                  </div>
                  {/* Toggle Switch */}
                  <div
                    className={clsx(
                      'relative w-11 h-6 rounded-full transition-colors flex-shrink-0',
                      runAsTask ? 'bg-green-500' : 'bg-[var(--color-border)]'
                    )}
                  >
                    <span
                      className={clsx(
                        'absolute top-1 left-1 w-4 h-4 rounded-full bg-white shadow-sm transition-transform duration-200',
                        runAsTask && 'translate-x-5'
                      )}
                    />
                  </div>
                </div>

                {/* Input Container with drag-and-drop */}
                <div
                  className={clsx(
                    'bg-[var(--color-card)] border rounded-2xl p-3 relative transition-all',
                    isDragging
                      ? 'border-primary bg-primary/5'
                      : runAsTask
                        ? 'border-green-500/50 shadow-[0_0_20px_rgba(34,197,94,0.1)]'
                        : 'border-[var(--color-border)]'
                  )}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                >
                  {/* Drag Overlay */}
                  {isDragging && (
                    <div className="absolute inset-0 bg-primary/10 flex items-center justify-center rounded-2xl z-10 pointer-events-none">
                      <div className="flex flex-col items-center gap-2">
                        <span className="material-symbols-outlined text-primary text-3xl">upload_file</span>
                        <span className="text-primary font-medium">Drop files here</span>
                      </div>
                    </div>
                  )}

                  {/* File Attachment Preview */}
                  {attachments.length > 0 && (
                    <FileAttachmentPreview
                      attachments={attachments}
                      onRemove={removeFile}
                    />
                  )}

                  {/* File Error */}
                  {fileError && (
                    <div className="mb-3 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
                      {fileError}
                    </div>
                  )}

                  {/* Work Directory Indicator */}
                  {workDir && (
                    <div className="mb-3 px-3 py-2 bg-primary/10 border border-primary/30 rounded-lg flex items-center justify-between">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="material-symbols-outlined text-primary text-lg">folder</span>
                        <span className="text-primary font-medium">{t('chat.workingIn')}</span>
                        <span className="text-[var(--color-text-muted)] truncate max-w-[300px]">{workDir}</span>
                      </div>
                      <button
                        onClick={handleClearWorkDir}
                        className="p-1 rounded hover:bg-primary/20 text-primary transition-colors"
                        title={t('chat.clearWorkDir')}
                      >
                        <span className="material-symbols-outlined text-sm">close</span>
                      </button>
                    </div>
                  )}

                  {/* Input Row */}
                  <div className="relative flex items-center gap-3">
                    {/* File Attachment Button */}
                    <FileAttachmentButton
                      onFilesSelected={addFiles}
                      disabled={isProcessingFiles}
                      canAddMore={canAddMore}
                    />

                    {/* Work in Folder Button */}
                    <button
                      onClick={handleSelectFolder}
                      className={clsx(
                        'p-2 rounded-lg transition-colors',
                        workDir
                          ? 'text-primary bg-primary/10 hover:bg-primary/20'
                          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
                      )}
                      title={workDir ? `Working in: ${workDir}` : 'Select a folder to work in'}
                    >
                      <span className="material-symbols-outlined">folder</span>
                    </button>

                    {/* Slash Command Suggestions */}
                    {showCommandSuggestions && filteredCommands.length > 0 && (
                      <div className="absolute bottom-full left-0 mb-2 w-64 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden z-10">
                        <div className="px-3 py-2 border-b border-[var(--color-border)]">
                          <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">Commands</span>
                        </div>
                        {filteredCommands.map((cmd, index) => (
                          <button
                            key={cmd.name}
                            onClick={() => handleSelectCommand(cmd.name)}
                            className={clsx(
                              'w-full px-3 py-2.5 flex items-start gap-3 text-left transition-colors',
                              index === selectedCommandIndex
                                ? 'bg-primary text-white'
                                : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                            )}
                          >
                            <span className="material-symbols-outlined text-lg mt-0.5">terminal</span>
                            <div>
                              <p className="font-medium">{cmd.name}</p>
                              <p className={clsx(
                                'text-xs',
                                index === selectedCommandIndex ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                              )}>
                                {cmd.description}
                              </p>
                            </div>
                          </button>
                        ))}
                        <div className="px-3 py-1.5 border-t border-[var(--color-border)] bg-[var(--color-hover)]/50">
                          <span className="text-xs text-[var(--color-text-muted)]">
                            <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">↑↓</kbd> navigate
                            <span className="mx-2">·</span>
                            <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Tab</kbd> select
                            <span className="mx-2">·</span>
                            <kbd className="px-1 py-0.5 bg-[var(--color-border)] rounded text-xs">Esc</kbd> close
                          </span>
                        </div>
                      </div>
                    )}

                    {/* Text Input */}
                    <textarea
                      value={inputValue}
                      onChange={handleInputChange}
                      onKeyDown={handleKeyDown}
                      onPaste={handlePaste}
                      placeholder={t('chat.placeholder')}
                      rows={1}
                      className="flex-1 bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] resize-none focus:outline-none py-2"
                    />

                    {/* Send Button */}
                    <button
                      onClick={isStreaming ? handleStop : handleSendMessage}
                      disabled={!isStreaming && (!inputValue.trim() && !attachments.some((a) => a.base64)) || !selectedAgentId}
                      className={clsx(
                        'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 transition-colors',
                        isStreaming
                          ? 'bg-red-500 hover:bg-red-600'
                          : runAsTask
                            ? 'bg-green-500 hover:bg-green-600'
                            : 'bg-primary hover:bg-primary-hover',
                        !isStreaming && ((!inputValue.trim() && !attachments.some((a) => a.base64)) || !selectedAgentId) && 'opacity-50 cursor-not-allowed'
                      )}
                      title={isStreaming ? 'Stop generation' : runAsTask ? t('chat.runAsTask') : (attachments.length > 0 ? 'Send with attachments' : 'Send message')}
                    >
                      {isStreaming ? (
                        <span className="material-symbols-outlined text-white text-xl">stop</span>
                      ) : runAsTask ? (
                        <span className="material-symbols-outlined text-white text-xl">rocket_launch</span>
                      ) : (
                        <span className="material-symbols-outlined text-white text-xl">arrow_upward</span>
                      )}
                    </button>
                  </div>

                  {/* Bottom Row - Skills & Commands hint */}
                  <div className="flex items-center justify-between mt-3 pt-3 border-t border-[var(--color-border)]/50">
                    <div className="flex items-center gap-4">
                      <ReadOnlyChips
                        label="Plugins"
                        icon="extension"
                        items={agentPlugins.map((p) => ({
                          id: p.id,
                          name: p.name,
                          description: p.description,
                        }))}
                        emptyText=""
                        loading={isLoadingPlugins}
                      />

                      <ReadOnlyChips
                        label="Skills"
                        icon="auto_fix_high"
                        items={agentSkills.map((s) => ({
                          id: s.id,
                          name: s.name,
                          description: s.description,
                        }))}
                        emptyText=""
                        loading={isLoadingSkills}
                        badgeOverride={selectedAgent?.globalUserMode ? 'All' : undefined}
                      />

                      <ReadOnlyChips
                        label="MCPs"
                        icon="widgets"
                        items={agentMCPs.map((m) => ({
                          id: m.id,
                          name: m.name,
                          description: m.description,
                        }))}
                        emptyText=""
                        loading={isLoadingMCPs}
                      />
                    </div>

                    <span className="text-xs text-[var(--color-text-muted)]">
                      Type <kbd className="px-1.5 py-0.5 bg-[var(--color-hover)] rounded text-xs mx-1">/</kbd> for commands
                    </span>
                  </div>
                </div>

                {/* Footer */}
                <p className="text-center text-xs text-[var(--color-text-muted)]/60 mt-4 uppercase tracking-wider">
                  {"Immersive Workspace • Powered by Claude Code"}
                </p>
              </div>
            </div>
          </>
        )}
        </div>
        {/* End Main Chat Area */}

        {/* Right Sidebar - File Browser (part of flex layout) */}
        {!rightSidebarCollapsed && (
          <div
            className="flex flex-col bg-[var(--color-card)] border-l border-[var(--color-border)] relative"
            style={{ width: rightSidebarWidth }}
          >
            {/* Resize Handle (on the left side) */}
            <div
              className={clsx(
                'absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-primary/50 transition-colors z-10',
                isResizingRight && 'bg-primary'
              )}
              onMouseDown={handleMouseDownRight}
            >
              <div className="absolute inset-y-0 -left-1 w-3" />
            </div>

            {/* Header */}
            <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-primary text-lg">folder</span>
                <span className="font-medium text-[var(--color-text)] text-sm">Files</span>
              </div>
              <button
                onClick={toggleRightSidebar}
                className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
                aria-label="Close file browser"
              >
                <span className="material-symbols-outlined text-lg">close</span>
              </button>
            </div>

            {/* File Browser Content */}
            <div className="flex-1 overflow-hidden">
              {selectedAgentId ? (
                <FileBrowser
                  agentId={selectedAgentId}
                  onFileSelect={setPreviewFile}
                  className="h-full"
                  basePath={effectiveBasePath}
                />
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)] p-4 text-center">
                  <span className="material-symbols-outlined text-3xl mb-2">folder_off</span>
                  <p className="text-sm">{t('chat.noAgent')}</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {/* End Content area */}

      {/* File Preview Modal */}
      <FilePreviewModal
        isOpen={!!previewFile}
        onClose={() => setPreviewFile(null)}
        agentId={selectedAgentId || ''}
        file={previewFile}
        basePath={effectiveBasePath}
      />

      {/* Folder Picker Modal (Web browser mode) */}
      <FolderPickerModal
        isOpen={isFolderPickerOpen}
        onClose={() => setIsFolderPickerOpen(false)}
        onSelect={handleFolderPickerSelect}
      />

      {/* Permission Request Modal */}
      {pendingPermission && (
        <PermissionRequestModal
          request={pendingPermission}
          onDecision={handlePermissionDecision}
          isLoading={isPermissionLoading}
        />
      )}

      {/* Agent Edit Modal */}
      <AgentFormModal
        isOpen={isEditAgentOpen}
        onClose={() => setIsEditAgentOpen(false)}
        onSave={handleSaveAgent}
        agent={selectedAgent}
      />
    </div>
  );
}

// Collapsible Tool Use Input Component
const TOOL_INPUT_COLLAPSE_LENGTH = 200; // Collapse if content exceeds this character length

function ToolUseBlock({ name, input }: { name: string; input: Record<string, unknown> }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  // Memoize expensive JSON serialization
  const { content, shouldCollapse, hiddenChars } = useMemo(() => {
    const content = JSON.stringify(input, null, 2);
    return {
      content,
      shouldCollapse: content.length > TOOL_INPUT_COLLAPSE_LENGTH,
      hiddenChars: content.length - TOOL_INPUT_COLLAPSE_LENGTH,
    };
  }, [input]);

  const displayContent = shouldCollapse && !isExpanded
    ? content.slice(0, TOOL_INPUT_COLLAPSE_LENGTH) + '...'
    : content;

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-sm">terminal</span>
          <span className="text-sm font-medium text-[var(--color-text)]">Tool Call: {name}</span>
        </div>
      </div>
      <div className="p-4 relative">
        <button
          onClick={handleCopy}
          className="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] rounded transition-colors"
        >
          <span className="material-symbols-outlined text-sm">
            {copied ? 'check' : 'content_copy'}
          </span>
          {copied ? 'Copied!' : 'Copy'}
        </button>
        <pre className="text-sm text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words">
          <code>{displayContent}</code>
        </pre>
        {shouldCollapse && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            aria-expanded={isExpanded}
            className="mt-2 text-xs text-primary hover:text-primary-hover transition-colors flex items-center gap-1"
          >
            <span className="material-symbols-outlined text-sm">
              {isExpanded ? 'expand_less' : 'expand_more'}
            </span>
            {isExpanded ? 'Show less' : `Show more (${hiddenChars} more chars)`}
          </button>
        )}
      </div>
    </div>
  );
}

// Message Bubble Component
interface MessageBubbleProps {
  message: Message;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

function MessageBubble({ message, onAnswerQuestion, pendingToolUseId, isStreaming }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className={clsx('flex gap-4', isUser && 'flex-row-reverse')}>
      <div
        className={clsx(
          'w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0',
          isUser ? 'bg-orange-500/20' : 'bg-[var(--color-card)]'
        )}
      >
        <span className={clsx('material-symbols-outlined', isUser ? 'text-orange-400' : 'text-primary')}>
          {isUser ? 'person' : 'smart_toy'}
        </span>
      </div>

      <div className={clsx('flex-1 max-w-3xl', isUser && 'text-right')}>
        <div className={clsx('flex items-center gap-2 mb-1', isUser && 'justify-end')}>
          <span className="font-medium text-[var(--color-text)]">{isUser ? 'User' : 'AI Agent'}</span>
          <span className="text-xs text-[var(--color-text-muted)]">
            {new Date(message.timestamp).toLocaleTimeString([], {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        </div>

        <div className={clsx('space-y-3', isUser && 'inline-block text-left')}>
          {message.content.map((block, index) => (
            <ContentBlockRenderer
              key={index}
              block={block}
              onAnswerQuestion={onAnswerQuestion}
              pendingToolUseId={pendingToolUseId}
              isStreaming={isStreaming}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// Content Block Renderer
interface ContentBlockRendererProps {
  block: ContentBlock;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

function ContentBlockRenderer({ block, onAnswerQuestion, pendingToolUseId, isStreaming }: ContentBlockRendererProps) {
  if (block.type === 'text') {
    return <MarkdownRenderer content={block.text || ''} />;
  }

  if (block.type === 'tool_use') {
    // Special handling for TodoWrite
    if (block.name === 'TodoWrite') {
      const todos = block.input?.todos as TodoItem[] | undefined;
      if (Array.isArray(todos) && todos.length > 0) {
        return <TodoWriteWidget todos={todos} />;
      }
    }

    // Generic tool use rendering with collapsible input
    return <ToolUseBlock name={block.name || 'Unknown'} input={block.input || {}} />;
  }

  if (block.type === 'tool_result') {
    return (
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="material-symbols-outlined text-status-online text-sm">check_circle</span>
          <span className="text-sm font-medium text-[var(--color-text)]">Tool Result</span>
        </div>
        <pre className="text-sm text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words">
          <code>{block.content}</code>
        </pre>
      </div>
    );
  }

  if (block.type === 'ask_user_question') {
    const isPending = pendingToolUseId === block.toolUseId;
    const isAnswered = !isPending && !isStreaming;

    return (
      <AskUserQuestion
        questions={block.questions}
        toolUseId={block.toolUseId}
        onSubmit={onAnswerQuestion || (() => {})}
        disabled={isAnswered || isStreaming}
      />
    );
  }

  return null;
}
