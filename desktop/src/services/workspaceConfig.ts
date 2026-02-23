/**
 * Workspace configuration service for Skills, MCPs, Knowledgebases, Context, and Audit Log.
 */
import api from './api';
import type {
  WorkspaceSkillConfig,
  WorkspaceMcpConfig,
  WorkspaceKnowledgebaseConfig,
  AuditLogEntry,
} from '../types/workspace-config';

/** Convert snake_case skill config to camelCase. */
export function skillConfigToCamelCase(data: Record<string, unknown>): WorkspaceSkillConfig {
  return {
    id: data.id as string,
    workspaceId: data.workspace_id as string,
    skillId: data.skill_id as string,
    enabled: data.enabled as boolean,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
}

/** Convert snake_case MCP config to camelCase. */
export function mcpConfigToCamelCase(data: Record<string, unknown>): WorkspaceMcpConfig {
  return {
    id: data.id as string,
    workspaceId: data.workspace_id as string,
    mcpServerId: data.mcp_server_id as string,
    enabled: data.enabled as boolean,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
}

/** Convert snake_case knowledgebase config to camelCase. */
export function kbConfigToCamelCase(data: Record<string, unknown>): WorkspaceKnowledgebaseConfig {
  return {
    id: data.id as string,
    workspaceId: data.workspace_id as string,
    sourceType: data.source_type as string,
    sourcePath: data.source_path as string,
    displayName: data.display_name as string,
    metadata: data.metadata as Record<string, unknown> | undefined,
    excludedSources: data.excluded_sources as string[] | undefined,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
}

/** Convert snake_case audit log entry to camelCase. */
export function auditLogToCamelCase(data: Record<string, unknown>): AuditLogEntry {
  return {
    id: data.id as string,
    workspaceId: data.workspace_id as string,
    changeType: data.change_type as string,
    entityType: data.entity_type as string,
    entityId: data.entity_id as string,
    oldValue: data.old_value as string | undefined,
    newValue: data.new_value as string | undefined,
    changedBy: data.changed_by as string,
    changedAt: data.changed_at as string,
  };
}

/** Convert camelCase skill config to snake_case for API. */
export function skillConfigToSnakeCase(
  config: Partial<WorkspaceSkillConfig>
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (config.skillId !== undefined) result.skill_id = config.skillId;
  if (config.enabled !== undefined) result.enabled = config.enabled;
  return result;
}

/** Convert camelCase MCP config to snake_case for API. */
export function mcpConfigToSnakeCase(
  config: Partial<WorkspaceMcpConfig>
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (config.mcpServerId !== undefined) result.mcp_server_id = config.mcpServerId;
  if (config.enabled !== undefined) result.enabled = config.enabled;
  return result;
}

/** Convert camelCase knowledgebase config to snake_case for API. */
export function kbConfigToSnakeCase(
  config: Partial<WorkspaceKnowledgebaseConfig>
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (config.sourceType !== undefined) result.source_type = config.sourceType;
  if (config.sourcePath !== undefined) result.source_path = config.sourcePath;
  if (config.displayName !== undefined) result.display_name = config.displayName;
  if (config.metadata !== undefined) result.metadata = config.metadata;
  if (config.excludedSources !== undefined) result.excluded_sources = config.excludedSources;
  return result;
}

export const workspaceConfigService = {
  // ---- Skills ----

  /** Get effective skills for a workspace. */
  async getSkills(workspaceId: string): Promise<WorkspaceSkillConfig[]> {
    const response = await api.get(`/workspaces/${workspaceId}/skills`);
    return response.data.map(skillConfigToCamelCase);
  },

  /** Update skill configurations for a workspace. */
  async updateSkills(
    workspaceId: string,
    configs: Partial<WorkspaceSkillConfig>[]
  ): Promise<void> {
    await api.put(
      `/workspaces/${workspaceId}/skills`,
      configs.map(skillConfigToSnakeCase)
    );
  },

  // ---- MCPs ----

  /** Get effective MCP servers for a workspace. */
  async getMcps(workspaceId: string): Promise<WorkspaceMcpConfig[]> {
    const response = await api.get(`/workspaces/${workspaceId}/mcps`);
    return response.data.map(mcpConfigToCamelCase);
  },

  /** Update MCP server configurations for a workspace. */
  async updateMcps(
    workspaceId: string,
    configs: Partial<WorkspaceMcpConfig>[]
  ): Promise<void> {
    await api.put(
      `/workspaces/${workspaceId}/mcps`,
      configs.map(mcpConfigToSnakeCase)
    );
  },

  // ---- Knowledgebases ----

  /** Get knowledgebases for a workspace. */
  async getKnowledgebases(workspaceId: string): Promise<WorkspaceKnowledgebaseConfig[]> {
    const response = await api.get(`/workspaces/${workspaceId}/knowledgebases`);
    return response.data.map(kbConfigToCamelCase);
  },

  /** Add a knowledgebase to a workspace. */
  async addKnowledgebase(
    workspaceId: string,
    data: Partial<WorkspaceKnowledgebaseConfig>
  ): Promise<WorkspaceKnowledgebaseConfig> {
    const response = await api.post(
      `/workspaces/${workspaceId}/knowledgebases`,
      kbConfigToSnakeCase(data)
    );
    return kbConfigToCamelCase(response.data);
  },

  /** Update a knowledgebase in a workspace. */
  async updateKnowledgebase(
    workspaceId: string,
    kbId: string,
    data: Partial<WorkspaceKnowledgebaseConfig>
  ): Promise<WorkspaceKnowledgebaseConfig> {
    const response = await api.put(
      `/workspaces/${workspaceId}/knowledgebases/${kbId}`,
      kbConfigToSnakeCase(data)
    );
    return kbConfigToCamelCase(response.data);
  },

  /** Delete a knowledgebase from a workspace. */
  async deleteKnowledgebase(workspaceId: string, kbId: string): Promise<void> {
    await api.delete(`/workspaces/${workspaceId}/knowledgebases/${kbId}`);
  },

  // ---- Context ----

  /** Get workspace context content. */
  async getContext(workspaceId: string): Promise<string> {
    const response = await api.get(`/workspaces/${workspaceId}/context`);
    return response.data.content as string;
  },

  /** Update workspace context content. */
  async updateContext(workspaceId: string, content: string): Promise<void> {
    await api.put(`/workspaces/${workspaceId}/context`, { content });
  },

  /** Trigger context compression. */
  async compressContext(workspaceId: string): Promise<string> {
    const response = await api.post(`/workspaces/${workspaceId}/context/compress`);
    return response.data.content as string;
  },

  // ---- Audit Log ----

  /** Get audit log entries for a workspace. */
  async getAuditLog(
    workspaceId: string,
    limit?: number,
    offset?: number
  ): Promise<AuditLogEntry[]> {
    const params = new URLSearchParams();
    if (limit !== undefined) params.append('limit', String(limit));
    if (offset !== undefined) params.append('offset', String(offset));

    const queryString = params.toString();
    const url = `/workspaces/${workspaceId}/audit-log${queryString ? `?${queryString}` : ''}`;
    const response = await api.get(url);
    return response.data.map(auditLogToCamelCase);
  },
};
