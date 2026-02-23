export type ChatMode = 'explore' | 'execute';

export type MessageRole = 'user' | 'assistant' | 'tool' | 'system';

export type SummaryType = 'rolling' | 'final';

export interface ChatThread {
  id: string;
  workspaceId: string;
  agentId: string;
  taskId?: string;
  todoId?: string;
  mode: ChatMode;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface ChatMessage {
  id: string;
  threadId: string;
  role: MessageRole;
  content: string;
  toolCalls?: string;
  createdAt: string;
}

export interface ThreadSummary {
  id: string;
  threadId: string;
  summaryType: SummaryType;
  summaryText: string;
  keyDecisions?: string[];
  openQuestions?: string[];
  updatedAt: string;
}
