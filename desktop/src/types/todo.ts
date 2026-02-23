export type ToDoStatus = 'pending' | 'overdue' | 'inDiscussion' | 'handled' | 'cancelled' | 'deleted';

export type ToDoSourceType = 'manual' | 'email' | 'slack' | 'meeting' | 'integration';

export type Priority = 'high' | 'medium' | 'low' | 'none';

export interface ToDo {
  id: string;
  workspaceId: string;
  title: string;
  description?: string;
  source?: string;
  sourceType: ToDoSourceType;
  status: ToDoStatus;
  priority: Priority;
  dueDate?: string;
  taskId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ToDoCreateRequest {
  workspaceId?: string;
  title: string;
  description?: string;
  source?: string;
  sourceType?: ToDoSourceType;
  priority?: Priority;
  dueDate?: string;
}

export interface ToDoUpdateRequest {
  title?: string;
  description?: string;
  status?: ToDoStatus;
  priority?: Priority;
  dueDate?: string;
}
