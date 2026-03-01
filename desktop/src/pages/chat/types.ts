import type { AskUserQuestion as AskUserQuestionType, PermissionRequest } from '../../types';

// Pending question state for user interaction
export interface PendingQuestion {
  toolUseId: string;
  questions: AskUserQuestionType[];
}

// Open tab state for session tab management
export interface OpenTab {
  id: string;           // Unique tab ID (can match sessionId or be temporary)
  sessionId?: string;   // Backend session ID (undefined for new unsaved sessions)
  title: string;        // Display title
  agentId: string;      // Associated agent
  isNew: boolean;       // True if no messages sent yet
}

// Re-export PermissionRequest for convenience
export type { PermissionRequest };
