/**
 * Unit tests for workspaceConfigApiService (singleton workspace config).
 *
 * Tests the GET/PUT /api/workspace endpoints and the configToCamelCase /
 * configUpdateToSnakeCase conversion functions.
 *
 * Testing methodology: unit tests with mocked API layer.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { configToCamelCase, configUpdateToSnakeCase } from '../workspace';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { workspaceConfigApiService } from '../workspace';

const sampleBackendConfig = {
  id: 'ws-001',
  name: 'SwarmWS',
  file_path: '/home/user/.swarm-ai/swarm-workspaces/SwarmWS',
  icon: '🐝',
  context: 'Workspace context',
  created_at: '2025-01-01T00:00:00+00:00',
  updated_at: '2025-01-02T00:00:00+00:00',
};

describe('Workspace Config Service - Unit Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('configToCamelCase', () => {
    it('should convert all snake_case fields to camelCase', () => {
      const result = configToCamelCase(sampleBackendConfig);
      expect(result.id).toBe('ws-001');
      expect(result.name).toBe('SwarmWS');
      expect(result.filePath).toBe(sampleBackendConfig.file_path);
      expect(result.icon).toBe('🐝');
      expect(result.context).toBe('Workspace context');
      expect(result.createdAt).toBe(sampleBackendConfig.created_at);
      expect(result.updatedAt).toBe(sampleBackendConfig.updated_at);
    });

    it('should handle undefined optional fields', () => {
      const data = { ...sampleBackendConfig, icon: undefined, context: undefined };
      const result = configToCamelCase(data as any);
      expect(result.icon).toBeUndefined();
      expect(result.context).toBeUndefined();
    });
  });

  describe('configUpdateToSnakeCase', () => {
    it('should convert present fields only', () => {
      const result = configUpdateToSnakeCase({ icon: '🚀', context: 'New context' });
      expect(result).toEqual({ icon: '🚀', context: 'New context' });
    });

    it('should omit undefined fields', () => {
      const result = configUpdateToSnakeCase({ icon: '🚀' });
      expect(result).toEqual({ icon: '🚀' });
      expect(result.context).toBeUndefined();
    });
  });

  describe('workspaceConfigApiService.getConfig', () => {
    it('should call GET /workspace and return camelCase config', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: sampleBackendConfig });
      const result = await workspaceConfigApiService.getConfig();
      expect(api.get).toHaveBeenCalledWith('/workspace');
      expect(result.filePath).toBe(sampleBackendConfig.file_path);
      expect(result.createdAt).toBe(sampleBackendConfig.created_at);
    });
  });

  describe('workspaceConfigApiService.updateConfig', () => {
    it('should call PUT /workspace with snake_case body and return camelCase', async () => {
      const updated = { ...sampleBackendConfig, icon: '🚀' };
      vi.mocked(api.put).mockResolvedValue({ data: updated });
      const result = await workspaceConfigApiService.updateConfig({ icon: '🚀' });
      expect(api.put).toHaveBeenCalledWith('/workspace', { icon: '🚀' });
      expect(result.icon).toBe('🚀');
    });
  });
});
