import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  skillConfigToCamelCase,
  mcpConfigToCamelCase,
  kbConfigToCamelCase,
  auditLogToCamelCase,
  skillConfigToSnakeCase,
  mcpConfigToSnakeCase,
  kbConfigToSnakeCase,
} from '../workspaceConfig';

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
import { workspaceConfigService } from '../workspaceConfig';

describe('WorkspaceConfig Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('toCamelCase conversions', () => {
    it('skillConfigToCamelCase should convert all fields', () => {
      const data = {
        id: 'wsk-1',
        workspace_id: 'ws-1',
        skill_id: 'skill-1',
        enabled: true,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z',
      };

      const result = skillConfigToCamelCase(data);

      expect(result.id).toBe('wsk-1');
      expect(result.workspaceId).toBe('ws-1');
      expect(result.skillId).toBe('skill-1');
      expect(result.enabled).toBe(true);
      expect(result.createdAt).toBe('2025-01-01T00:00:00Z');
      expect(result.updatedAt).toBe('2025-01-02T00:00:00Z');
    });

    it('mcpConfigToCamelCase should convert all fields', () => {
      const data = {
        id: 'wmc-1',
        workspace_id: 'ws-1',
        mcp_server_id: 'mcp-1',
        enabled: false,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z',
      };

      const result = mcpConfigToCamelCase(data);

      expect(result.mcpServerId).toBe('mcp-1');
      expect(result.enabled).toBe(false);
    });

    it('kbConfigToCamelCase should convert all fields', () => {
      const data = {
        id: 'kb-1',
        workspace_id: 'ws-1',
        source_type: 'local_file',
        source_path: '/path/to/file',
        display_name: 'My KB',
        metadata: { key: 'value' },
        excluded_sources: ['src-1'],
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-02T00:00:00Z',
      };

      const result = kbConfigToCamelCase(data);

      expect(result.sourceType).toBe('local_file');
      expect(result.sourcePath).toBe('/path/to/file');
      expect(result.displayName).toBe('My KB');
      expect(result.excludedSources).toEqual(['src-1']);
    });

    it('auditLogToCamelCase should convert all fields', () => {
      const data = {
        id: 'audit-1',
        workspace_id: 'ws-1',
        change_type: 'enabled',
        entity_type: 'skill',
        entity_id: 'skill-1',
        old_value: 'false',
        new_value: 'true',
        changed_by: 'user',
        changed_at: '2025-01-01T00:00:00Z',
      };

      const result = auditLogToCamelCase(data);

      expect(result.changeType).toBe('enabled');
      expect(result.entityType).toBe('skill');
      expect(result.entityId).toBe('skill-1');
      expect(result.oldValue).toBe('false');
      expect(result.newValue).toBe('true');
      expect(result.changedBy).toBe('user');
      expect(result.changedAt).toBe('2025-01-01T00:00:00Z');
    });
  });

  describe('toSnakeCase conversions', () => {
    it('skillConfigToSnakeCase should convert defined fields', () => {
      const result = skillConfigToSnakeCase({ skillId: 'skill-1', enabled: true });
      expect(result.skill_id).toBe('skill-1');
      expect(result.enabled).toBe(true);
    });

    it('mcpConfigToSnakeCase should convert defined fields', () => {
      const result = mcpConfigToSnakeCase({ mcpServerId: 'mcp-1', enabled: false });
      expect(result.mcp_server_id).toBe('mcp-1');
      expect(result.enabled).toBe(false);
    });

    it('kbConfigToSnakeCase should convert defined fields', () => {
      const result = kbConfigToSnakeCase({
        sourceType: 'url',
        sourcePath: 'https://example.com',
        displayName: 'Example',
        excludedSources: ['src-1'],
      });
      expect(result.source_type).toBe('url');
      expect(result.source_path).toBe('https://example.com');
      expect(result.display_name).toBe('Example');
      expect(result.excluded_sources).toEqual(['src-1']);
    });

    it('toSnakeCase should omit undefined fields', () => {
      const result = skillConfigToSnakeCase({});
      expect(result.skill_id).toBeUndefined();
      expect(result.enabled).toBeUndefined();
    });
  });

  describe('API methods', () => {
    it('getSkills should call GET /workspaces/{id}/skills', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      await workspaceConfigService.getSkills('ws-1');
      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/skills');
    });

    it('updateSkills should call PUT /workspaces/{id}/skills', async () => {
      vi.mocked(api.put).mockResolvedValue({});
      await workspaceConfigService.updateSkills('ws-1', [{ skillId: 's1', enabled: true }]);
      expect(api.put).toHaveBeenCalledWith('/workspaces/ws-1/skills', [{ skill_id: 's1', enabled: true }]);
    });

    it('getMcps should call GET /workspaces/{id}/mcps', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      await workspaceConfigService.getMcps('ws-1');
      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/mcps');
    });

    it('updateMcps should call PUT /workspaces/{id}/mcps', async () => {
      vi.mocked(api.put).mockResolvedValue({});
      await workspaceConfigService.updateMcps('ws-1', [{ mcpServerId: 'm1', enabled: false }]);
      expect(api.put).toHaveBeenCalledWith('/workspaces/ws-1/mcps', [{ mcp_server_id: 'm1', enabled: false }]);
    });

    it('getKnowledgebases should call GET /workspaces/{id}/knowledgebases', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      await workspaceConfigService.getKnowledgebases('ws-1');
      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/knowledgebases');
    });

    it('addKnowledgebase should call POST with snake_case body', async () => {
      const mockResponse = {
        id: 'kb-1', workspace_id: 'ws-1', source_type: 'url',
        source_path: 'https://example.com', display_name: 'Example',
        created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z',
      };
      vi.mocked(api.post).mockResolvedValue({ data: mockResponse });

      const result = await workspaceConfigService.addKnowledgebase('ws-1', {
        sourceType: 'url', sourcePath: 'https://example.com', displayName: 'Example',
      });

      expect(api.post).toHaveBeenCalledWith('/workspaces/ws-1/knowledgebases', {
        source_type: 'url', source_path: 'https://example.com', display_name: 'Example',
      });
      expect(result.displayName).toBe('Example');
    });

    it('deleteKnowledgebase should call DELETE', async () => {
      vi.mocked(api.delete).mockResolvedValue({});
      await workspaceConfigService.deleteKnowledgebase('ws-1', 'kb-1');
      expect(api.delete).toHaveBeenCalledWith('/workspaces/ws-1/knowledgebases/kb-1');
    });

    it('getContext should return content string', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: { content: '# Context' } });
      const result = await workspaceConfigService.getContext('ws-1');
      expect(result).toBe('# Context');
    });

    it('updateContext should call PUT with content', async () => {
      vi.mocked(api.put).mockResolvedValue({});
      await workspaceConfigService.updateContext('ws-1', '# Updated');
      expect(api.put).toHaveBeenCalledWith('/workspaces/ws-1/context', { content: '# Updated' });
    });

    it('compressContext should call POST and return content', async () => {
      vi.mocked(api.post).mockResolvedValue({ data: { content: '# Compressed' } });
      const result = await workspaceConfigService.compressContext('ws-1');
      expect(result).toBe('# Compressed');
    });

    it('getAuditLog should call GET with pagination params', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      await workspaceConfigService.getAuditLog('ws-1', 20, 10);
      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/audit-log?limit=20&offset=10');
    });

    it('getAuditLog should call GET without params when none provided', async () => {
      vi.mocked(api.get).mockResolvedValue({ data: [] });
      await workspaceConfigService.getAuditLog('ws-1');
      expect(api.get).toHaveBeenCalledWith('/workspaces/ws-1/audit-log');
    });
  });
});
