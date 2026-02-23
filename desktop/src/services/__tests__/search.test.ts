import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  searchResultItemToCamelCase,
  searchResultsToCamelCase,
  threadSummaryToCamelCase,
} from '../search';

// Mock the api module
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

import api from '../api';
import { searchService } from '../search';

describe('Search Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('toCamelCase conversions', () => {
    it('searchResultItemToCamelCase should convert all fields', () => {
      const data = {
        id: 'item-1',
        entity_type: 'todo',
        title: 'Test Item',
        description: 'A description',
        workspace_id: 'ws-1',
        workspace_name: 'SwarmWS',
        status: 'pending',
        updated_at: '2025-01-01T00:00:00Z',
      };

      const result = searchResultItemToCamelCase(data);

      expect(result.entityType).toBe('todo');
      expect(result.workspaceId).toBe('ws-1');
      expect(result.workspaceName).toBe('SwarmWS');
      expect(result.updatedAt).toBe('2025-01-01T00:00:00Z');
    });

    it('searchResultsToCamelCase should convert nested results', () => {
      const data = {
        query: 'test',
        scope: 'all',
        results: [
          { id: '1', entity_type: 'todo', title: 'T1', workspace_id: 'ws-1', updated_at: '2025-01-01T00:00:00Z' },
          { id: '2', entity_type: 'task', title: 'T2', workspace_id: 'ws-2', updated_at: '2025-01-02T00:00:00Z' },
        ],
        total: 2,
      };

      const result = searchResultsToCamelCase(data);

      expect(result.query).toBe('test');
      expect(result.scope).toBe('all');
      expect(result.total).toBe(2);
      expect(result.results).toHaveLength(2);
      expect(result.results[0].entityType).toBe('todo');
      expect(result.results[1].entityType).toBe('task');
    });

    it('searchResultsToCamelCase should handle empty results', () => {
      const result = searchResultsToCamelCase({ query: 'test', scope: 'all', total: 0 });
      expect(result.results).toEqual([]);
      expect(result.total).toBe(0);
    });

    it('threadSummaryToCamelCase should convert all fields', () => {
      const data = {
        id: 'ts-1',
        thread_id: 'thread-1',
        summary_type: 'rolling',
        summary_text: 'Summary of the thread',
        key_decisions: ['Decision 1'],
        open_questions: ['Question 1'],
        updated_at: '2025-01-01T00:00:00Z',
      };

      const result = threadSummaryToCamelCase(data);

      expect(result.threadId).toBe('thread-1');
      expect(result.summaryType).toBe('rolling');
      expect(result.summaryText).toBe('Summary of the thread');
      expect(result.keyDecisions).toEqual(['Decision 1']);
      expect(result.openQuestions).toEqual(['Question 1']);
      expect(result.updatedAt).toBe('2025-01-01T00:00:00Z');
    });
  });

  describe('API methods', () => {
    it('search should call GET /search with query params', async () => {
      const mockResponse = { query: 'test', scope: 'all', results: [], total: 0 };
      vi.mocked(api.get).mockResolvedValue({ data: mockResponse });

      await searchService.search('test', 'all', ['todo', 'task']);

      expect(api.get).toHaveBeenCalledWith(
        '/search?query=test&scope=all&entity_types=todo%2Ctask'
      );
    });

    it('search should work with only query param', async () => {
      const mockResponse = { query: 'test', scope: 'all', results: [], total: 0 };
      vi.mocked(api.get).mockResolvedValue({ data: mockResponse });

      await searchService.search('test');

      expect(api.get).toHaveBeenCalledWith('/search?query=test');
    });

    it('searchThreads should call GET /search/threads', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });

      await searchService.searchThreads('test', 'ws-1');

      expect(api.get).toHaveBeenCalledWith('/search/threads?query=test&scope=ws-1');
    });

    it('searchThreads should work without scope', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });

      await searchService.searchThreads('test');

      expect(api.get).toHaveBeenCalledWith('/search/threads?query=test');
    });
  });
});
