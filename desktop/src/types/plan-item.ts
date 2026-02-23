import type { Priority } from './todo';

export type PlanItemStatus = 'active' | 'deferred' | 'completed' | 'cancelled';

export type FocusType = 'today' | 'upcoming' | 'blocked';

export interface PlanItem {
  id: string;
  workspaceId: string;
  title: string;
  description?: string;
  sourceTodoId?: string;
  sourceTaskId?: string;
  status: PlanItemStatus;
  priority: Priority;
  scheduledDate?: string;
  focusType: FocusType;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
}

export interface PlanItemCreateRequest {
  workspaceId?: string;
  title: string;
  description?: string;
  sourceTodoId?: string;
  sourceTaskId?: string;
  priority?: Priority;
  scheduledDate?: string;
  focusType?: FocusType;
  sortOrder?: number;
}

export interface PlanItemUpdateRequest {
  title?: string;
  description?: string;
  status?: PlanItemStatus;
  priority?: Priority;
  scheduledDate?: string;
  focusType?: FocusType;
  sortOrder?: number;
}
