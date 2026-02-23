import { describe, it, expect, vi, beforeEach } from 'vitest';
import { toCamelCase, toSnakeCase } from '../todos';

// Mock the api module
vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { todosService } from '../todos';

describe('ToDos Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('toCamelCase', () => {
    it('should convert all snake_case fields to camelCase', () => {
      const backendData = {
        id: 'todo-1',
        workspace_id: 'ws-1',
        title: 'Test ToDo',
        description: 'A description',
        source: 'email-client',
        source_type: 'email',
        status: 'pending',
        priority: 'high',
        due_date: '2025-01-15T00:00:00Z',
        task_id: 'task-1',
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z',
      };

      const result = toCamelCase(backendData);

      expect(result.id).toBe('todo-1');
      expect(result.workspaceId).toBe('ws-1');
      expect(result.title).toBe('Test ToDo');
      expect(result.description).toBe('A description');
      expect(result.source).toBe('email-client');
      expect(result.sourceType).toBe('email');
      expect(result.status).toBe('pending');
      expect(result.priority).toBe('high');
      expect(result.dueDate).toBe('2025-01-15T00:00:00Z');
      expect(result.taskId).toBe('task-1');
      expect(result.createdAt).toBe('2025-01-01T00:00:00Z');
      expect(result.updatedAt).toBe('2025-01-02T00:00:00Z');
    });

    it('should handle optional fields as undefined', () => {
      const backendData = {
        id: 'todo-2',
        workspace_id: 'ws-1',
        title: 'Minimal ToDo',
        source_type: 'manual',
        status: 'pending',
        priority: 'none',
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
      };

      const result = toCamelCase(backendData);

      expect(result.description).toBeUndefined();
      expect(result.source).toBeUndefined();
      expect(result.dueDate).toBeUndefined();
      expect(result.taskId).toBeUndefined();
    });
  });

  describe('toSnakeCase', () => {
    it('should convert create request fields to snake_case', () => {
      const request = {
        workspaceId: 'ws-1',
        title: 'New ToDo',
        description: 'Desc',
        source: 'slack',
        sourceType: 'slack' as const,
        priority: 'high' as const,
        dueDate: '2025-02-01T00:00:00Z',
      };

      const result = toSnakeCase(request);

      expect(result.workspace_id).toBe('ws-1');
      expect(result.title).toBe('New ToDo');
      expect(result.description).toBe('Desc');
      expect(result.source).toBe('slack');
      expect(result.source_type).toBe('slack');
      expect(result.priority).toBe('high');
      expect(result.due_date).toBe('2025-02-01T00:00:00Z');
    });

    it('should only include defined fields in update request', () => {
      const request = { title: 'Updated Title', priority: 'low' as const };
      const result = toSnakeCase(request);

      expect(result.title).toBe('Updated Title');
      expect(result.priority).toBe('low');
      expect(result.workspace_id).toBeUndefined();
      expect(result.description).toBeUndefined();
    });
  });

  describe('API methods', () => {
    it('list should call GET /todos with query params', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });

      await todosService.list('ws-1', 'pending', 10, 0);

      expect(api.get).toHaveBeenCalledWith(
        '/todos?workspace_id=ws-1&status=pending&limit=10&offset=0'
      );
    });

    it('list should call GET /todos without params when none provided', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });

      await todosService.list();

      expect(api.get).toHaveBeenCalledWith('/todos');
    });

    it('get should call GET /todos/{id}', async () => {
      const mockData = {
        id: 'todo-1', workspace_id: 'ws-1', title: 'Test',
        source_type: 'manual', status: 'pending', priority: 'none',
        created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z',
      };
      vi.mocked(api.get).mockResolvedValue({ data: mockData });

      const result = await todosService.get('todo-1');

      expect(api.get).toHaveBeenCalledWith('/todos/todo-1');
      expect(result.id).toBe('todo-1');
      expect(result.workspaceId).toBe('ws-1');
    });

    it('create should call POST /todos with snake_case body', async () => {
      const mockResponse = {
        id: 'todo-new', workspace_id: 'ws-1', title: 'New',
        source_type: 'manual', status: 'pending', priority: 'none',
        created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z',
      };
      vi.mocked(api.post).mockResolvedValue({ data: mockResponse });

      const result = await todosService.create({ title: 'New', workspaceId: 'ws-1' });

      expect(api.post).toHaveBeenCalledWith('/todos', expect.objectContaining({
        title: 'New',
        workspace_id: 'ws-1',
      }));
      expect(result.workspaceId).toBe('ws-1');
    });

    it('update should call PUT /todos/{id}', async () => {
      const mockResponse = {
        id: 'todo-1', workspace_id: 'ws-1', title: 'Updated',
        source_type: 'manual', status: 'pending', priority: 'high',
        created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-02T00:00:00Z',
      };
      vi.mocked(api.put).mockResolvedValue({ data: mockResponse });

      await todosService.update('todo-1', { title: 'Updated', priority: 'high' });

      expect(api.put).toHaveBeenCalledWith('/todos/todo-1', expect.objectContaining({
        title: 'Updated',
        priority: 'high',
      }));
    });

    it('delete should call DELETE /todos/{id}', async () => {
      vi.mocked(api.delete).mockResolvedValue({});

      await todosService.delete('todo-1');

      expect(api.delete).toHaveBeenCalledWith('/todos/todo-1');
    });

    it('convertToTask should call POST /todos/{id}/convert-to-task', async () => {
      vi.mocked(api.post).mockResolvedValue({ data: { id: 'task-1' } });

      await todosService.convertToTask('todo-1');

      expect(api.post).toHaveBeenCalledWith('/todos/todo-1/convert-to-task');
    });
  });
});
