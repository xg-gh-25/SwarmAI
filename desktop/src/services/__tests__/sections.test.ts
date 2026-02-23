import { describe, it, expect, vi, beforeEach } from 'vitest';
import { sectionCountsToCamelCase, sectionResponseToCamelCase } from '../sections';

// Mock the api module
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

import api from '../api';
import { sectionsService } from '../sections';

describe('Sections Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('sectionCountsToCamelCase', () => {
    it('should convert all nested snake_case keys to camelCase', () => {
      const backendData = {
        signals: { total: 5, pending: 2, overdue: 1, in_discussion: 2 },
        plan: { total: 3, today: 1, upcoming: 1, blocked: 1 },
        execute: { total: 4, draft: 1, wip: 1, blocked: 1, completed: 1 },
        communicate: { total: 3, pending_reply: 1, ai_draft: 1, follow_up: 1 },
        artifacts: { total: 4, plan: 1, report: 1, doc: 1, decision: 1 },
        reflection: { total: 3, daily_recap: 1, weekly_summary: 1, lessons_learned: 1 },
      };

      const result = sectionCountsToCamelCase(backendData);

      expect(result.signals.inDiscussion).toBe(2);
      expect(result.communicate.pendingReply).toBe(1);
      expect(result.communicate.aiDraft).toBe(1);
      expect(result.communicate.followUp).toBe(1);
      expect(result.reflection.dailyRecap).toBe(1);
      expect(result.reflection.weeklySummary).toBe(1);
      expect(result.reflection.lessonsLearned).toBe(1);
    });

    it('should default missing sections to zero counts', () => {
      const result = sectionCountsToCamelCase({});

      expect(result.signals.total).toBe(0);
      expect(result.plan.total).toBe(0);
      expect(result.execute.total).toBe(0);
      expect(result.communicate.total).toBe(0);
      expect(result.artifacts.total).toBe(0);
      expect(result.reflection.total).toBe(0);
    });
  });

  describe('sectionResponseToCamelCase', () => {
    it('should convert pagination has_more to hasMore', () => {
      const backendData = {
        counts: { total: 5 },
        groups: [],
        pagination: { limit: 50, offset: 0, total: 5, has_more: true },
        sort_keys: ['status', 'created_at'],
        last_updated_at: '2025-01-01T00:00:00Z',
      };

      const result = sectionResponseToCamelCase(backendData, (item) => item);

      expect(result.pagination.hasMore).toBe(true);
      expect(result.sortKeys).toEqual(['status', 'created_at']);
      expect(result.lastUpdatedAt).toBe('2025-01-01T00:00:00Z');
    });

    it('should map items within groups using the provided mapper', () => {
      const backendData = {
        counts: { pending: 1 },
        groups: [
          {
            name: 'pending',
            items: [{ id: 'todo-1', workspace_id: 'ws-1', title: 'Test' }],
          },
        ],
        pagination: { limit: 50, offset: 0, total: 1, has_more: false },
        sort_keys: [],
        last_updated_at: null,
      };

      const mapper = (item: Record<string, unknown>) => ({
        id: item.id as string,
        workspaceId: item.workspace_id as string,
        title: item.title as string,
      });

      const result = sectionResponseToCamelCase(backendData, mapper);

      expect(result.groups).toHaveLength(1);
      expect(result.groups[0].name).toBe('pending');
      expect(result.groups[0].items[0]).toEqual({
        id: 'todo-1',
        workspaceId: 'ws-1',
        title: 'Test',
      });
    });

    it('should handle empty response gracefully', () => {
      const result = sectionResponseToCamelCase({}, (item) => item);

      expect(result.counts).toEqual({});
      expect(result.groups).toEqual([]);
      expect(result.pagination.hasMore).toBe(false);
      expect(result.sortKeys).toEqual([]);
      expect(result.lastUpdatedAt).toBeNull();
    });
  });

  describe('API methods', () => {
    it('getCounts should call GET /workspaces/{id}/sections', async () => {
      const mockCounts = {
        signals: { total: 0, pending: 0, overdue: 0, in_discussion: 0 },
        plan: { total: 0, today: 0, upcoming: 0, blocked: 0 },
        execute: { total: 0, draft: 0, wip: 0, blocked: 0, completed: 0 },
        communicate: { total: 0, pending_reply: 0, ai_draft: 0, follow_up: 0 },
        artifacts: { total: 0, plan: 0, report: 0, doc: 0, decision: 0 },
        reflection: { total: 0, daily_recap: 0, weekly_summary: 0, lessons_learned: 0 },
      };
      vi.mocked(api.get).mockResolvedValue({ data: mockCounts });

      await sectionsService.getCounts('ws-1');

      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/sections');
    });

    it('getSignals should call correct URL with params', async () => {
      const mockResponse = {
        counts: {}, groups: [],
        pagination: { limit: 10, offset: 0, total: 0, has_more: false },
        sort_keys: [], last_updated_at: null,
      };
      vi.mocked(api.get).mockResolvedValue({ data: mockResponse });

      await sectionsService.getSignals('ws-1', { limit: 10, offset: 0, sortBy: 'status' });

      expect(api.get).toHaveBeenCalledWith(
        '/workspaces/ws-1/sections/signals?limit=10&offset=0&sort_by=status'
      );
    });

    it('getExecute should support globalView param', async () => {
      const mockResponse = {
        counts: {}, groups: [],
        pagination: { limit: 50, offset: 0, total: 0, has_more: false },
        sort_keys: [], last_updated_at: null,
      };
      vi.mocked(api.get).mockResolvedValue({ data: mockResponse });

      await sectionsService.getExecute('all', { globalView: true });

      expect(api.get).toHaveBeenCalledWith(
        '/workspaces/all/sections/execute?global_view=true'
      );
    });
  });
});
