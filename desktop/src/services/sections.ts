/**
 * Sections service for workspace section-based navigation data.
 */
import api from './api';
import type { SectionCounts, SectionResponse } from '../types/section';
import type { ToDo } from '../types/todo';
import type { PlanItem } from '../types/plan-item';
import type { Communication } from '../types/communication';
import type { Artifact } from '../types/artifact';
import type { Reflection } from '../types/reflection';
import type { Task } from '../types';

export interface SectionParams {
  limit?: number;
  offset?: number;
  sortBy?: string;
  sortOrder?: string;
  globalView?: boolean;
}

/** Convert snake_case section counts to camelCase. */
export function sectionCountsToCamelCase(data: Record<string, unknown>): SectionCounts {
  const signals = data.signals as Record<string, unknown> | undefined;
  const plan = data.plan as Record<string, unknown> | undefined;
  const execute = data.execute as Record<string, unknown> | undefined;
  const communicate = data.communicate as Record<string, unknown> | undefined;
  const artifacts = data.artifacts as Record<string, unknown> | undefined;
  const reflection = data.reflection as Record<string, unknown> | undefined;

  return {
    signals: {
      total: (signals?.total as number) ?? 0,
      pending: (signals?.pending as number) ?? 0,
      overdue: (signals?.overdue as number) ?? 0,
      inDiscussion: (signals?.in_discussion as number) ?? 0,
    },
    plan: {
      total: (plan?.total as number) ?? 0,
      today: (plan?.today as number) ?? 0,
      upcoming: (plan?.upcoming as number) ?? 0,
      blocked: (plan?.blocked as number) ?? 0,
    },
    execute: {
      total: (execute?.total as number) ?? 0,
      draft: (execute?.draft as number) ?? 0,
      wip: (execute?.wip as number) ?? 0,
      blocked: (execute?.blocked as number) ?? 0,
      completed: (execute?.completed as number) ?? 0,
    },
    communicate: {
      total: (communicate?.total as number) ?? 0,
      pendingReply: (communicate?.pending_reply as number) ?? 0,
      aiDraft: (communicate?.ai_draft as number) ?? 0,
      followUp: (communicate?.follow_up as number) ?? 0,
    },
    artifacts: {
      total: (artifacts?.total as number) ?? 0,
      plan: (artifacts?.plan as number) ?? 0,
      report: (artifacts?.report as number) ?? 0,
      doc: (artifacts?.doc as number) ?? 0,
      decision: (artifacts?.decision as number) ?? 0,
    },
    reflection: {
      total: (reflection?.total as number) ?? 0,
      dailyRecap: (reflection?.daily_recap as number) ?? 0,
      weeklySummary: (reflection?.weekly_summary as number) ?? 0,
      lessonsLearned: (reflection?.lessons_learned as number) ?? 0,
    },
  };
}

/** Convert snake_case section response to camelCase. */
export function sectionResponseToCamelCase<T>(
  data: Record<string, unknown>,
  itemMapper: (item: Record<string, unknown>) => T
): SectionResponse<T> {
  const groups = (data.groups as Array<Record<string, unknown>> | undefined) ?? [];
  const pagination = data.pagination as Record<string, unknown> | undefined;

  return {
    counts: (data.counts as Record<string, number>) ?? {},
    groups: groups.map((g) => ({
      name: g.name as string,
      items: ((g.items as Array<Record<string, unknown>>) ?? []).map(itemMapper),
    })),
    pagination: {
      limit: (pagination?.limit as number) ?? 50,
      offset: (pagination?.offset as number) ?? 0,
      total: (pagination?.total as number) ?? 0,
      hasMore: (pagination?.has_more as boolean) ?? false,
    },
    sortKeys: (data.sort_keys as string[]) ?? [],
    lastUpdatedAt: (data.last_updated_at as string | null) ?? null,
  };
}

function buildSectionQuery(params?: SectionParams): string {
  if (!params) return '';
  const qs = new URLSearchParams();
  if (params.limit !== undefined) qs.append('limit', String(params.limit));
  if (params.offset !== undefined) qs.append('offset', String(params.offset));
  if (params.sortBy) qs.append('sort_by', params.sortBy);
  if (params.sortOrder) qs.append('sort_order', params.sortOrder);
  if (params.globalView !== undefined) qs.append('global_view', String(params.globalView));
  const str = qs.toString();
  return str ? `?${str}` : '';
}

/** Identity mapper for items that don't need deep conversion (used as fallback). */
function todoItemMapper(item: Record<string, unknown>): ToDo {
  return {
    id: item.id as string,
    workspaceId: item.workspace_id as string,
    title: item.title as string,
    description: item.description as string | undefined,
    source: item.source as string | undefined,
    sourceType: item.source_type as ToDo['sourceType'],
    status: item.status as ToDo['status'],
    priority: item.priority as ToDo['priority'],
    dueDate: item.due_date as string | undefined,
    taskId: item.task_id as string | undefined,
    createdAt: item.created_at as string,
    updatedAt: item.updated_at as string,
  };
}

function planItemMapper(item: Record<string, unknown>): PlanItem {
  return {
    id: item.id as string,
    workspaceId: item.workspace_id as string,
    title: item.title as string,
    description: item.description as string | undefined,
    sourceTodoId: item.source_todo_id as string | undefined,
    sourceTaskId: item.source_task_id as string | undefined,
    status: item.status as PlanItem['status'],
    priority: item.priority as PlanItem['priority'],
    scheduledDate: item.scheduled_date as string | undefined,
    focusType: item.focus_type as PlanItem['focusType'],
    sortOrder: item.sort_order as number,
    createdAt: item.created_at as string,
    updatedAt: item.updated_at as string,
  };
}

function taskItemMapper(item: Record<string, unknown>): Task {
  return {
    id: item.id as string,
    workspaceId: (item.workspace_id as string) ?? null,
    agentId: item.agent_id as string,
    sessionId: (item.session_id as string) ?? null,
    status: item.status as Task['status'],
    title: item.title as string,
    description: (item.description as string) ?? null,
    priority: (item.priority as string) ?? null,
    sourceTodoId: (item.source_todo_id as string) ?? null,
    blockedReason: (item.blocked_reason as string) ?? null,
    model: (item.model as string) ?? null,
    createdAt: item.created_at as string,
    startedAt: (item.started_at as string) ?? null,
    completedAt: (item.completed_at as string) ?? null,
    error: (item.error as string) ?? null,
    workDir: (item.work_dir as string) ?? null,
  };
}

function communicationItemMapper(item: Record<string, unknown>): Communication {
  return {
    id: item.id as string,
    workspaceId: item.workspace_id as string,
    title: item.title as string,
    description: item.description as string | undefined,
    recipient: item.recipient as string,
    channelType: item.channel_type as Communication['channelType'],
    status: item.status as Communication['status'],
    priority: item.priority as Communication['priority'],
    dueDate: item.due_date as string | undefined,
    aiDraftContent: item.ai_draft_content as string | undefined,
    sourceTaskId: item.source_task_id as string | undefined,
    sourceTodoId: item.source_todo_id as string | undefined,
    sentAt: item.sent_at as string | undefined,
    createdAt: item.created_at as string,
    updatedAt: item.updated_at as string,
  };
}

function artifactItemMapper(item: Record<string, unknown>): Artifact {
  return {
    id: item.id as string,
    workspaceId: item.workspace_id as string,
    taskId: item.task_id as string | undefined,
    artifactType: item.artifact_type as Artifact['artifactType'],
    title: item.title as string,
    filePath: item.file_path as string,
    version: item.version as number,
    createdBy: item.created_by as string,
    tags: item.tags as string[] | undefined,
    createdAt: item.created_at as string,
    updatedAt: item.updated_at as string,
  };
}

function reflectionItemMapper(item: Record<string, unknown>): Reflection {
  return {
    id: item.id as string,
    workspaceId: item.workspace_id as string,
    reflectionType: item.reflection_type as Reflection['reflectionType'],
    title: item.title as string,
    filePath: item.file_path as string,
    periodStart: item.period_start as string,
    periodEnd: item.period_end as string,
    generatedBy: item.generated_by as string,
    createdAt: item.created_at as string,
    updatedAt: item.updated_at as string,
  };
}

export const sectionsService = {
  /** Get aggregated counts for all six sections. */
  async getCounts(workspaceId: string): Promise<SectionCounts> {
    const response = await api.get(`/workspaces/${workspaceId}/sections`);
    return sectionCountsToCamelCase(response.data);
  },

  /** Get Signals section (ToDos grouped by status). */
  async getSignals(workspaceId: string, params?: SectionParams): Promise<SectionResponse<ToDo>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/signals${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, todoItemMapper);
  },

  /** Get Plan section (PlanItems grouped by focus_type). */
  async getPlan(workspaceId: string, params?: SectionParams): Promise<SectionResponse<PlanItem>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/plan${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, planItemMapper);
  },

  /** Get Execute section (Tasks grouped by status). */
  async getExecute(workspaceId: string, params?: SectionParams): Promise<SectionResponse<Task>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/execute${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, taskItemMapper);
  },

  /** Get Communicate section (Communications grouped by status). */
  async getCommunicate(workspaceId: string, params?: SectionParams): Promise<SectionResponse<Communication>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/communicate${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, communicationItemMapper);
  },

  /** Get Artifacts section (Artifacts grouped by type). */
  async getArtifacts(workspaceId: string, params?: SectionParams): Promise<SectionResponse<Artifact>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/artifacts${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, artifactItemMapper);
  },

  /** Get Reflection section (Reflections grouped by type). */
  async getReflection(workspaceId: string, params?: SectionParams): Promise<SectionResponse<Reflection>> {
    const response = await api.get(`/workspaces/${workspaceId}/sections/reflection${buildSectionQuery(params)}`);
    return sectionResponseToCamelCase(response.data, reflectionItemMapper);
  },
};
