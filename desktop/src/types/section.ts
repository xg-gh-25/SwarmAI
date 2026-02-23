export type WorkspaceSection = 'signals' | 'plan' | 'execute' | 'communicate' | 'artifacts' | 'reflection';

export interface SectionCounts {
  signals: {
    total: number;
    pending: number;
    overdue: number;
    inDiscussion: number;
  };
  plan: {
    total: number;
    today: number;
    upcoming: number;
    blocked: number;
  };
  execute: {
    total: number;
    draft: number;
    wip: number;
    blocked: number;
    completed: number;
  };
  communicate: {
    total: number;
    pendingReply: number;
    aiDraft: number;
    followUp: number;
  };
  artifacts: {
    total: number;
    plan: number;
    report: number;
    doc: number;
    decision: number;
  };
  reflection: {
    total: number;
    dailyRecap: number;
    weeklySummary: number;
    lessonsLearned: number;
  };
}

export interface SectionGroup<T> {
  name: string;
  items: T[];
}

export interface Pagination {
  limit: number;
  offset: number;
  total: number;
  hasMore: boolean;
}

export interface SectionResponse<T> {
  counts: Record<string, number>;
  groups: SectionGroup<T>[];
  pagination: Pagination;
  sortKeys: string[];
  lastUpdatedAt: string | null;
}
