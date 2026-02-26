/**
 * Unit tests for the context assembly preview service.
 *
 * Tests the context service layer including:
 * - ``toCamelCase`` / ``layerToCamelCase`` snake_case → camelCase conversion
 *   (including truncationSummary, etag, truncationStage fields)
 * - ``getContextPreview`` URL construction, query params, and ETag caching
 * - ``bindThread`` request body snake_case conversion and response mapping
 *
 * Testing methodology: unit tests with mocked fetch and axios API layer.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock getBackendPort before importing the module under test
vi.mock('../tauri', () => ({
  getBackendPort: vi.fn(() => 9999),
}));

// Mock api.post for bindThread
vi.mock('../api', () => ({
  default: {
    post: vi.fn(),
  },
}));

import { toCamelCase, layerToCamelCase, getContextPreview, bindThread } from '../context';
import api from '../api';

// --- Sample snake_case backend responses ---

const sampleBackendLayer = {
  layer_number: 1,
  name: 'System Prompt',
  source_path: 'system-prompts.md',
  token_count: 250,
  content_preview: 'You are a helpful assistant...',
  truncated: false,
  truncation_stage: 0,
};

const sampleTruncatedLayer = {
  layer_number: 6,
  name: 'Memory',
  source_path: 'Knowledge/Memory/prefs.md',
  token_count: 800,
  content_preview: 'User prefers concise answers...',
  truncated: true,
  truncation_stage: 2,
};

const sampleBackendPreview = {
  project_id: 'proj-uuid-123',
  thread_id: 'thread-abc',
  layers: [sampleBackendLayer, sampleTruncatedLayer],
  total_token_count: 1050,
  budget_exceeded: true,
  token_budget: 10000,
  truncation_summary: '[Context truncated: Memory layer reduced by stage 2]',
  etag: 'abc123hash',
};

describe('Context Service - Unit Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ---------------------------------------------------------------
  // layerToCamelCase
  // ---------------------------------------------------------------
  describe('layerToCamelCase', () => {
    it('should convert all snake_case layer fields to camelCase', () => {
      const result = layerToCamelCase(sampleBackendLayer);
      expect(result.layerNumber).toBe(1);
      expect(result.name).toBe('System Prompt');
      expect(result.sourcePath).toBe('system-prompts.md');
      expect(result.tokenCount).toBe(250);
      expect(result.contentPreview).toBe('You are a helpful assistant...');
      expect(result.truncated).toBe(false);
      expect(result.truncationStage).toBe(0);
    });

    it('should map truncationStage for truncated layers', () => {
      const result = layerToCamelCase(sampleTruncatedLayer);
      expect(result.truncated).toBe(true);
      expect(result.truncationStage).toBe(2);
    });

    it('should default truncationStage to 0 when missing', () => {
      const data = { ...sampleBackendLayer, truncation_stage: undefined };
      const result = layerToCamelCase(data as any);
      expect(result.truncationStage).toBe(0);
    });
  });

  // ---------------------------------------------------------------
  // toCamelCase
  // ---------------------------------------------------------------
  describe('toCamelCase', () => {
    it('should convert all snake_case preview fields to camelCase', () => {
      const result = toCamelCase(sampleBackendPreview);
      expect(result.projectId).toBe('proj-uuid-123');
      expect(result.threadId).toBe('thread-abc');
      expect(result.totalTokenCount).toBe(1050);
      expect(result.budgetExceeded).toBe(true);
      expect(result.tokenBudget).toBe(10000);
      expect(result.truncationSummary).toBe(
        '[Context truncated: Memory layer reduced by stage 2]',
      );
      expect(result.etag).toBe('abc123hash');
    });

    it('should convert nested layers to camelCase', () => {
      const result = toCamelCase(sampleBackendPreview);
      expect(result.layers).toHaveLength(2);
      expect(result.layers[0].layerNumber).toBe(1);
      expect(result.layers[1].truncationStage).toBe(2);
    });

    it('should default threadId to null when missing', () => {
      const data = { ...sampleBackendPreview, thread_id: undefined };
      const result = toCamelCase(data as any);
      expect(result.threadId).toBeNull();
    });

    it('should default truncationSummary to empty string when missing', () => {
      const data = { ...sampleBackendPreview, truncation_summary: undefined };
      const result = toCamelCase(data as any);
      expect(result.truncationSummary).toBe('');
    });

    it('should default etag to empty string when missing', () => {
      const data = { ...sampleBackendPreview, etag: undefined };
      const result = toCamelCase(data as any);
      expect(result.etag).toBe('');
    });

    it('should handle empty layers array', () => {
      const data = { ...sampleBackendPreview, layers: [] };
      const result = toCamelCase(data as any);
      expect(result.layers).toEqual([]);
    });

    it('should handle undefined layers gracefully', () => {
      const data = { ...sampleBackendPreview, layers: undefined };
      const result = toCamelCase(data as any);
      expect(result.layers).toEqual([]);
    });
  });

  // ---------------------------------------------------------------
  // getContextPreview
  // ---------------------------------------------------------------
  describe('getContextPreview', () => {
    let fetchSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      fetchSpy = vi.fn();
      global.fetch = fetchSpy;
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('should construct correct URL with projectId only', async () => {
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleBackendPreview),
      });

      await getContextPreview('proj-1');

      const calledUrl = fetchSpy.mock.calls[0][0] as string;
      expect(calledUrl).toBe('http://localhost:9999/api/projects/proj-1/context');
    });

    it('should include thread_id query param when provided', async () => {
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleBackendPreview),
      });

      await getContextPreview('proj-1', 'thread-42');

      const calledUrl = fetchSpy.mock.calls[0][0] as string;
      expect(calledUrl).toContain('thread_id=thread-42');
    });

    it('should include token_budget query param when provided', async () => {
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleBackendPreview),
      });

      await getContextPreview('proj-1', undefined, 5000);

      const calledUrl = fetchSpy.mock.calls[0][0] as string;
      expect(calledUrl).toContain('token_budget=5000');
    });

    it('should include both query params when both provided', async () => {
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleBackendPreview),
      });

      await getContextPreview('proj-1', 'thread-42', 5000);

      const calledUrl = fetchSpy.mock.calls[0][0] as string;
      expect(calledUrl).toContain('thread_id=thread-42');
      expect(calledUrl).toContain('token_budget=5000');
    });

    it('should return camelCase ContextPreview on 200', async () => {
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(sampleBackendPreview),
      });

      const result = await getContextPreview('proj-1');
      expect(result).not.toBeNull();
      expect(result!.projectId).toBe('proj-uuid-123');
      expect(result!.truncationSummary).toBe(
        '[Context truncated: Memory layer reduced by stage 2]',
      );
      expect(result!.etag).toBe('abc123hash');
    });

    it('should send If-None-Match header on subsequent requests', async () => {
      // First request — returns ETag
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ ...sampleBackendPreview, project_id: 'proj-etag' }),
      });
      await getContextPreview('proj-etag');

      // Second request — should include If-None-Match
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ ...sampleBackendPreview, project_id: 'proj-etag' }),
      });
      await getContextPreview('proj-etag');

      const secondCallHeaders = fetchSpy.mock.calls[1][1]?.headers as Record<string, string>;
      expect(secondCallHeaders['If-None-Match']).toBe('abc123hash');
    });

    it('should return cached response on 304 Not Modified', async () => {
      // First request — populate cache
      fetchSpy.mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({ ...sampleBackendPreview, project_id: 'proj-304' }),
      });
      const first = await getContextPreview('proj-304');

      // Second request — 304
      fetchSpy.mockResolvedValue({
        ok: false,
        status: 304,
        json: () => Promise.reject(new Error('should not be called')),
      });
      const second = await getContextPreview('proj-304');

      expect(second).not.toBeNull();
      expect(second!.projectId).toBe('proj-304');
      expect(second).toEqual(first);
    });

    it('should throw on non-ok, non-304 responses', async () => {
      fetchSpy.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
      });

      await expect(getContextPreview('proj-err')).rejects.toThrow(
        'Context preview request failed: 500',
      );
    });
  });

  // ---------------------------------------------------------------
  // bindThread
  // ---------------------------------------------------------------
  describe('bindThread', () => {
    it('should send snake_case body via api.post', async () => {
      vi.mocked(api.post).mockResolvedValue({
        data: {
          thread_id: 'thread-1',
          task_id: 'task-99',
          todo_id: null,
          context_version: 5,
        },
      });

      await bindThread('thread-1', { taskId: 'task-99', mode: 'replace' });

      expect(api.post).toHaveBeenCalledWith('/chat_threads/thread-1/bind', {
        task_id: 'task-99',
        todo_id: undefined,
        mode: 'replace',
      });
    });

    it('should return camelCase ThreadBindResponse', async () => {
      vi.mocked(api.post).mockResolvedValue({
        data: {
          thread_id: 'thread-1',
          task_id: 'task-99',
          todo_id: 'todo-7',
          context_version: 3,
        },
      });

      const result = await bindThread('thread-1', {
        taskId: 'task-99',
        todoId: 'todo-7',
        mode: 'add',
      });

      expect(result.threadId).toBe('thread-1');
      expect(result.taskId).toBe('task-99');
      expect(result.todoId).toBe('todo-7');
      expect(result.contextVersion).toBe(3);
    });

    it('should default null task_id/todo_id in response', async () => {
      vi.mocked(api.post).mockResolvedValue({
        data: {
          thread_id: 'thread-2',
          task_id: null,
          todo_id: null,
          context_version: 1,
        },
      });

      const result = await bindThread('thread-2', { mode: 'replace' });
      expect(result.taskId).toBeNull();
      expect(result.todoId).toBeNull();
    });
  });
});
