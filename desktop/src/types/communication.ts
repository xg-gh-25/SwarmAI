import type { Priority } from './todo';

export type CommunicationStatus = 'pendingReply' | 'aiDraft' | 'followUp' | 'sent' | 'cancelled';

export type ChannelType = 'email' | 'slack' | 'meeting' | 'other';

export interface Communication {
  id: string;
  workspaceId: string;
  title: string;
  description?: string;
  recipient: string;
  channelType: ChannelType;
  status: CommunicationStatus;
  priority: Priority;
  dueDate?: string;
  aiDraftContent?: string;
  sourceTaskId?: string;
  sourceTodoId?: string;
  sentAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface CommunicationCreateRequest {
  workspaceId?: string;
  title: string;
  description?: string;
  recipient: string;
  channelType?: ChannelType;
  priority?: Priority;
  dueDate?: string;
  aiDraftContent?: string;
  sourceTaskId?: string;
  sourceTodoId?: string;
}

export interface CommunicationUpdateRequest {
  title?: string;
  description?: string;
  recipient?: string;
  channelType?: ChannelType;
  status?: CommunicationStatus;
  priority?: Priority;
  dueDate?: string;
  aiDraftContent?: string;
}
