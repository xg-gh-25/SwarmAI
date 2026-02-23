export type ReflectionType = 'dailyRecap' | 'weeklySummary' | 'lessonsLearned';

export interface Reflection {
  id: string;
  workspaceId: string;
  reflectionType: ReflectionType;
  title: string;
  filePath: string;
  periodStart: string;
  periodEnd: string;
  generatedBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface ReflectionCreateRequest {
  workspaceId?: string;
  reflectionType?: ReflectionType;
  title: string;
  periodStart: string;
  periodEnd: string;
  generatedBy?: string;
}

export interface ReflectionUpdateRequest {
  title?: string;
  reflectionType?: ReflectionType;
  periodStart?: string;
  periodEnd?: string;
}
