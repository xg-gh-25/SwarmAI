/**
 * Unit tests for projectService API methods and case conversion.
 *
 * Tests the project CRUD service layer including list, get, create, update,
 * delete, getProjectByName, and getHistory methods. Verifies correct API
 * endpoint calls and snake_case → camelCase response conversion.
 *
 * Testing methodology: unit tests with mocked API layer.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  projectToCamelCase,
  projectUpdateToSnakeCase,
  historyEntryToCamelCase,
} from '../workspace';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { projectService } from '../workspace';

// Sample snake_case backend response
const sampleBackendProject = {
  id: '550e8400-e29b-41d4-a716-446655440000',
  name: 'Test Project',
  description: 'A test project',
  path: 'Projects/Test Project',
  created_at: '2025-01-15T10:30:00+00:00',
  updated_at: '2025-01-15T10:30:00+00:00',
  status: 'active',
  priority: 'medium',
  tags: ['test', 'demo'],
  schema_version: '1.0.0',
  version: 1,
  context_l0: 'Level 0 context',
  context_l1: 'Level 1 context',
};

const sampleBackendHistoryEntry = {
  version: 2,
  timestamp: '2025-01-16T10:30:00+00:00',
  action: 'updated',
  changes: { description: { from: '', to: 'A test project' } },
  source: 'user',
};

describe('Project Service - Unit Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('projectToCamelCase', () => {
    it('should convert all snake_case fields to camelCase', () => {
      const result = projectToCamelCase(sampleBackendProject);
      expect(result.id).toBe(sampleBackendProject.id);
      expect(result.name).toBe('Test Project');
      expect(result.description).toBe('A test project');
      expect(result.path).toBe('Projects/Test Project');
      expect(result.createdAt).toBe(sampleBackendProject.created_at);
      expect(result.updatedAt).toBe(sampleBackendProject.updated_at);
      expect(result.status).toBe('active');
      expect(result.priority).toBe('medium');
      expect(result.tags).toEqual(['test', 'demo']);
      expect(result.schemaVersion).toBe('1.0.0');
      expect(result.version).toBe(1);
      expect(result.contextL0).toBe('Level 0 context');
      expect(result.contextL1).toBe('Level 1 context');
    });

    it('should default description to empty string when missing', () => {
      const data = { ...sampleBackendProject, description: undefined };
      const result = projectToCamelCase(data as any);
      expect(result.description).toBe('');
    });

    it('should default path to empty string when missing', () => {
      const data = { ...sampleBackendProject, path: undefined };
      const result = projectToCamelCase(data as any);
      expect(result.path).toBe('');
    });

    it('should handle undefined priority', () => {
      const data = { ...sampleBackendProject, priority: undefined };
      const result = projectToCamelCase(data as any);
      expect(result.priority).toBeUndefined();
    });

    it('should handle undefined context fields', () => {
      const data = { ...sampleBackendProject, context_l0: undefined, context_l1: undefined };
      const result = projectToCamelCase(data as any);
      expect(result.contextL0).toBeUndefined();
      expect(result.contextL1).toBeUndefined();
    });
  });

  describe('projectUpdateToSnakeCase', () => {
    it('should convert all present fields', () => {
      const result = projectUpdateToSnakeCase({
        name: 'New Name',
        description: 'New desc',
        status: 'archived',
        tags: ['a'],
        priority: 'high',
      });
      expect(result).toEqual({
        name: 'New Name',
        description: 'New desc',
        status: 'archived',
        tags: ['a'],
        priority: 'high',
      });
    });

    it('should omit undefined fields', () => {
      const result = projectUpdateToSnakeCase({ name: 'Only Name' });
      expect(result).toEqual({ name: 'Only Name' });
      expect(result.description).toBeUndefined();
      expect(result.status).toBeUndefined();
    });
  });

  describe('historyEntryToCamelCase', () => {
    it('should convert all history entry fields', () => {
      const result = historyEntryToCamelCase(sampleBackendHistoryEntry);
      expect(result.version).toBe(2);
      expect(result.timestamp).toBe('2025-01-16T10:30:00+00:00');
      expect(result.action).toBe('updated');
      expect(result.changes).toEqual({ description: { from: '', to: 'A test project' } });
      expect(result.source).toBe('user');
    });
  });

  describe('projectService.listProjects', () => {
    it('should call GET /projects and return camelCase array', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [sampleBackendProject] });
      const result = await projectService.listProjects();
      expect(api.get).toHaveBeenCalledWith('/projects');
      expect(result).toHaveLength(1);
      expect(result[0].createdAt).toBe(sampleBackendProject.created_at);
      expect(result[0].schemaVersion).toBe('1.0.0');
    });
  });

  describe('projectService.createProject', () => {
    it('should call POST /projects with name and return camelCase', async () => {
      vi.mocked(api.post).mockResolvedValue({ data: sampleBackendProject });
      const result = await projectService.createProject({ name: 'Test Project' });
      expect(api.post).toHaveBeenCalledWith('/projects', { name: 'Test Project' });
      expect(result.id).toBe(sampleBackendProject.id);
      expect(result.schemaVersion).toBe('1.0.0');
    });
  });

  describe('projectService.getProject', () => {
    it('should call GET /projects/{id} and return camelCase', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendProject });
      const result = await projectService.getProject(sampleBackendProject.id);
      expect(api.get).toHaveBeenCalledWith(`/projects/${sampleBackendProject.id}`);
      expect(result.name).toBe('Test Project');
    });
  });

  describe('projectService.updateProject', () => {
    it('should call PUT /projects/{id} with snake_case body and return camelCase', async () => {
      const updated = { ...sampleBackendProject, description: 'Updated desc', version: 2 };
      vi.mocked(api.put).mockResolvedValue({ data: updated });
      const result = await projectService.updateProject(sampleBackendProject.id, {
        description: 'Updated desc',
      });
      expect(api.put).toHaveBeenCalledWith(`/projects/${sampleBackendProject.id}`, {
        description: 'Updated desc',
      });
      expect(result.description).toBe('Updated desc');
      expect(result.version).toBe(2);
    });
  });

  describe('projectService.deleteProject', () => {
    it('should call DELETE /projects/{id}', async () => {
      vi.mocked(api.delete).mockResolvedValue({});
      await projectService.deleteProject(sampleBackendProject.id);
      expect(api.delete).toHaveBeenCalledWith(`/projects/${sampleBackendProject.id}`);
    });
  });

  describe('projectService.getProjectByName', () => {
    it('should call GET /projects with name param and return first match', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [sampleBackendProject] });
      const result = await projectService.getProjectByName('Test Project');
      expect(api.get).toHaveBeenCalledWith('/projects', { params: { name: 'Test Project' } });
      expect(result).not.toBeNull();
      expect(result!.name).toBe('Test Project');
    });

    it('should return null when no match found', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      const result = await projectService.getProjectByName('Nonexistent');
      expect(result).toBeNull();
    });
  });

  describe('projectService.getHistory', () => {
    it('should call GET /projects/{id}/history and return camelCase entries', async () => {
      vi.mocked(api.get).mockResolvedValue({
        data: { project_id: sampleBackendProject.id, history: [sampleBackendHistoryEntry] },
      });
      const result = await projectService.getHistory(sampleBackendProject.id);
      expect(api.get).toHaveBeenCalledWith(`/projects/${sampleBackendProject.id}/history`);
      expect(result).toHaveLength(1);
      expect(result[0].version).toBe(2);
      expect(result[0].action).toBe('updated');
    });

    it('should return empty array when history is empty', async () => {
      vi.mocked(api.get).mockResolvedValue({
        data: { project_id: sampleBackendProject.id, history: [] },
      });
      const result = await projectService.getHistory(sampleBackendProject.id);
      expect(result).toEqual([]);
    });
  });
});
