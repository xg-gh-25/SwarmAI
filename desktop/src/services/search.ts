/**
 * Search service for cross-entity search and thread search.
 */
import api from './api';
import type { ThreadSummary } from '../types/chat-thread';

export interface SearchResultItem {
  id: string;
  entityType: string;
  title: string;
  description?: string;
  workspaceId: string;
  workspaceName?: string;
  status?: string;
  updatedAt: string;
}

export interface SearchResults {
  query: string;
  scope: string;
  results: SearchResultItem[];
  total: number;
}

/** Convert snake_case search result item to camelCase. */
export function searchResultItemToCamelCase(data: Record<string, unknown>): SearchResultItem {
  return {
    id: (data.id as string) ?? '',
    entityType: (data.entity_type as string) ?? 'unknown',
    title: (data.title as string) ?? '',
    description: data.description as string | undefined,
    workspaceId: (data.workspace_id as string) ?? '',
    workspaceName: data.workspace_name as string | undefined,
    status: data.status as string | undefined,
    updatedAt: (data.updated_at as string) ?? '',
  };
}

/** Convert snake_case search results to camelCase. */
export function searchResultsToCamelCase(data: Record<string, unknown>): SearchResults {
  const results = (data.results as Array<Record<string, unknown>>) ?? [];
  return {
    query: data.query as string,
    scope: data.scope as string,
    results: results.map(searchResultItemToCamelCase),
    total: (data.total as number) ?? 0,
  };
}

/** Convert snake_case thread summary to camelCase. */
export function threadSummaryToCamelCase(data: Record<string, unknown>): ThreadSummary {
  return {
    id: data.id as string,
    threadId: data.thread_id as string,
    summaryType: data.summary_type as ThreadSummary['summaryType'],
    summaryText: data.summary_text as string,
    keyDecisions: data.key_decisions as string[] | undefined,
    openQuestions: data.open_questions as string[] | undefined,
    updatedAt: data.updated_at as string,
  };
}

export const searchService = {
  /** Search across all entity types. */
  async search(
    query: string,
    scope?: string,
    entityTypes?: string[]
  ): Promise<SearchResults> {
    const params = new URLSearchParams();
    params.append('query', query);
    if (scope) params.append('scope', scope);
    if (entityTypes && entityTypes.length > 0) {
      params.append('entity_types', entityTypes.join(','));
    }

    const response = await api.get(`/search?${params.toString()}`);
    return searchResultsToCamelCase(response.data);
  },

  /** Search chat threads via ThreadSummary. */
  async searchThreads(query: string, scope?: string): Promise<ThreadSummary[]> {
    const params = new URLSearchParams();
    params.append('query', query);
    if (scope) params.append('scope', scope);

    const response = await api.get(`/search/threads?${params.toString()}`);
    return response.data.map(threadSummaryToCamelCase);
  },
};
