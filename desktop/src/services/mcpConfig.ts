/**
 * MCP file-based configuration service.
 *
 * Replaces the DB-backed mcp.ts service with file-based endpoints.
 * Talks to the new validation router at /mcp/*.
 *
 * Key exports:
 * - mcpConfigService — REST client for catalog + dev MCP operations
 * - ConfigEntry, DevCreateRequest, DevUpdateRequest — TypeScript interfaces
 */

import api from './api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ConfigEntry {
  id: string;
  name: string;
  description?: string;
  connectionType: 'stdio' | 'sse' | 'http';
  config: Record<string, unknown>;
  enabled: boolean;
  rejectedTools?: string[];
  category?: string;
  source?: string;
  pluginId?: string;
  layer: 'catalog' | 'dev';
  // Catalog-only
  requiredEnv?: Array<{ key: string; label: string; placeholder?: string; secret?: boolean }>;
  optionalEnv?: Array<{ key: string; label: string; default?: string }>;
  presets?: Record<string, { label: string; env: Record<string, string>; setup_hint?: string }>;
}

export interface DevCreateRequest {
  id: string;
  name: string;
  connectionType: 'stdio' | 'sse' | 'http';
  config: Record<string, unknown>;
  description?: string;
  enabled?: boolean;
  rejectedTools?: string[];
}

export interface DevUpdateRequest {
  name?: string;
  connectionType?: 'stdio' | 'sse' | 'http';
  config?: Record<string, unknown>;
  description?: string;
  enabled?: boolean;
  rejectedTools?: string[];
}

// ---------------------------------------------------------------------------
// snake_case ↔ camelCase conversion
// ---------------------------------------------------------------------------

const toCamelCase = (data: Record<string, unknown>): ConfigEntry => ({
  id: data.id as string,
  name: data.name as string,
  description: data.description as string | undefined,
  connectionType: data.connection_type as 'stdio' | 'sse' | 'http',
  config: data.config as Record<string, unknown>,
  enabled: data.enabled as boolean,
  rejectedTools: data.rejected_tools as string[] | undefined,
  category: data.category as string | undefined,
  source: data.source as string | undefined,
  pluginId: data.plugin_id as string | undefined,
  layer: data.layer as 'catalog' | 'dev',
  requiredEnv: data.required_env as ConfigEntry['requiredEnv'],
  optionalEnv: data.optional_env as ConfigEntry['optionalEnv'],
  presets: data.presets as ConfigEntry['presets'],
});

const devToSnakeCase = (data: DevCreateRequest | DevUpdateRequest) => {
  const result: Record<string, unknown> = {};
  if ('id' in data && data.id !== undefined) result.id = data.id;
  if (data.name !== undefined) result.name = data.name;
  if (data.connectionType !== undefined) result.connection_type = data.connectionType;
  if (data.config !== undefined) result.config = data.config;
  if (data.description !== undefined) result.description = data.description;
  if (data.enabled !== undefined) result.enabled = data.enabled;
  if (data.rejectedTools !== undefined) result.rejected_tools = data.rejectedTools;
  return result;
};

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export const mcpConfigService = {
  async listAll(): Promise<ConfigEntry[]> {
    const response = await api.get<Record<string, unknown>[]>('/mcp');
    return response.data.map(toCamelCase);
  },

  async listCatalog(): Promise<ConfigEntry[]> {
    const response = await api.get<Record<string, unknown>[]>('/mcp/catalog');
    return response.data.map(toCamelCase);
  },

  async updateCatalogEntry(
    id: string,
    update: { enabled?: boolean; env?: Record<string, string> },
  ): Promise<ConfigEntry> {
    const response = await api.patch<Record<string, unknown>>(`/mcp/catalog/${id}`, update);
    return toCamelCase(response.data);
  },

  async listDev(): Promise<ConfigEntry[]> {
    const response = await api.get<Record<string, unknown>[]>('/mcp/dev');
    return response.data.map(toCamelCase);
  },

  async createDevEntry(entry: DevCreateRequest): Promise<ConfigEntry> {
    const response = await api.post<Record<string, unknown>>('/mcp/dev', devToSnakeCase(entry));
    return toCamelCase(response.data);
  },

  async updateDevEntry(id: string, update: DevUpdateRequest): Promise<ConfigEntry> {
    const response = await api.put<Record<string, unknown>>(`/mcp/dev/${id}`, devToSnakeCase(update));
    return toCamelCase(response.data);
  },

  async deleteDevEntry(id: string): Promise<void> {
    await api.delete(`/mcp/dev/${id}`);
  },
};
