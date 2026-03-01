import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { Message, ContentBlock, ChatSession, SwarmWorkspace } from '../types';
import { chatService } from '../services/chat';
import type { PendingQuestion } from '../pages/chat/types';
import { createWelcomeMessage, createWorkspaceChangeMessage } from '../pages/chat/constants';

interface UseChatSessionOptions {
  selectedAgentId: string | null;
}

interface UseChatSessionReturn {
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  sessionId: string | undefined;
  setSessionId: React.Dispatch<React.SetStateAction<string | undefined>>;
  pendingQuestion: PendingQuestion | null;
  setPendingQuestion: React.Dispatch<React.SetStateAction<PendingQuestion | null>>;
  isLoadingHistory: boolean;
  sessions: ChatSession[];
  refetchSessions: () => void;
  loadSessionMessages: (sid: string) => Promise<void>;
  handleNewChat: () => void;
  handleDeleteSession: (session: ChatSession) => Promise<void>;
  showWelcomeMessage: () => void;
  showWorkspaceChangeMessage: (workspace: SwarmWorkspace | null) => void;
}

/**
 * Custom hook for managing chat session state and operations
 */
export function useChatSession({ selectedAgentId }: UseChatSessionOptions): UseChatSessionReturn {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [pendingQuestion, setPendingQuestion] = useState<PendingQuestion | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  // Fetch chat sessions for the selected agent
  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['chatSessions', selectedAgentId],
    queryFn: () => chatService.listSessions(selectedAgentId || undefined),
    enabled: !!selectedAgentId,
  });

  // Load session messages
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

  // Show welcome message
  const showWelcomeMessage = useCallback(() => {
    setMessages([createWelcomeMessage()]);
  }, []);

  // Show workspace change message
  const showWorkspaceChangeMessage = useCallback((workspace: SwarmWorkspace | null) => {
    setSessionId(undefined);
    setMessages([]);
    setPendingQuestion(null);
    setMessages([createWorkspaceChangeMessage(workspace?.name, workspace?.filePath)]);
  }, []);

  // Handle new chat
  const handleNewChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setPendingQuestion(null);
    showWelcomeMessage();
  }, [showWelcomeMessage]);

  // Handle delete session
  const handleDeleteSession = useCallback(async (session: ChatSession) => {
    try {
      await chatService.deleteSession(session.id);
      refetchSessions();
      if (sessionId === session.id) {
        handleNewChat();
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
  }, [sessionId, handleNewChat, refetchSessions]);

  return {
    messages,
    setMessages,
    sessionId,
    setSessionId,
    pendingQuestion,
    setPendingQuestion,
    isLoadingHistory,
    sessions,
    refetchSessions,
    loadSessionMessages,
    handleNewChat,
    handleDeleteSession,
    showWelcomeMessage,
    showWorkspaceChangeMessage,
  };
}
