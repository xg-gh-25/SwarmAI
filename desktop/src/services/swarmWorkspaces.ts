import api from './api';
import type {
  SwarmWorkspace,
  SwarmWorkspaceCreateRequest,
  SwarmWorkspaceUpdateRequest,
} from '../types';

/**
 * Convert snake_case API response to camelCase for frontend use.
 * Backend uses: file_path, is_default, created_at, updated_at
 * Frontend uses: filePath, isDefault, createdAt, updatedAt
 */
const toCamelCase = (data: Record<string, unknown>): SwarmWorkspace => {
  return {
    id: data.id as string,
    name: data.name as string,
    filePath: data.file_path as string,
    context: data.context as string,
    icon: data.icon as string | undefined,
    isDefault: (data.is_default as boolean) ?? false,
    isArchived: (data.is_archived as boolean) ?? false,
    archivedAt: (data.archived_at as string) ?? null,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

/**
 * Convert camelCase frontend request to snake_case for API.
 * Frontend uses: filePath, icon
 * Backend expects: file_path, icon
 */
const toSnakeCase = (
  data: SwarmWorkspaceCreateRequest | SwarmWorkspaceUpdateRequest
): Record<string, unknown> => {
  const result: Record<string, unknown> = {};
  if (data.name !== undefined) result.name = data.name;
  if (data.filePath !== undefined) result.file_path = data.filePath;
  if (data.context !== undefined) result.context = data.context;
  if (data.icon !== undefined) result.icon = data.icon;
  return result;
};

export const swarmWorkspacesService = {
  /**
   * List all swarm workspaces.
   * GET /swarm-workspaces
   */
  async list(includeArchived?: boolean): Promise<SwarmWorkspace[]> {
    const params = new URLSearchParams();
    if (includeArchived !== undefined) params.append('include_archived', String(includeArchived));
    const queryString = params.toString();
    const url = queryString ? `/swarm-workspaces?${queryString}` : '/swarm-workspaces';
    const response = await api.get<Record<string, unknown>[]>(url);
    return response.data.map(toCamelCase);
  },

  /**
   * Get a swarm workspace by ID.
   * GET /swarm-workspaces/{id}
   */
  async get(id: string): Promise<SwarmWorkspace> {
    const response = await api.get<Record<string, unknown>>(`/swarm-workspaces/${id}`);
    return toCamelCase(response.data);
  },

  /**
   * Get the default swarm workspace.
   * GET /swarm-workspaces/default
   */
  async getDefault(): Promise<SwarmWorkspace> {
    const response = await api.get<Record<string, unknown>>('/swarm-workspaces/default');
    return toCamelCase(response.data);
  },

  /**
   * Create a new swarm workspace.
   * POST /swarm-workspaces
   */
  async create(data: SwarmWorkspaceCreateRequest): Promise<SwarmWorkspace> {
    const response = await api.post<Record<string, unknown>>(
      '/swarm-workspaces',
      toSnakeCase(data)
    );
    return toCamelCase(response.data);
  },

  /**
   * Update an existing swarm workspace.
   * PUT /swarm-workspaces/{id}
   */
  async update(id: string, data: SwarmWorkspaceUpdateRequest): Promise<SwarmWorkspace> {
    const response = await api.put<Record<string, unknown>>(
      `/swarm-workspaces/${id}`,
      toSnakeCase(data)
    );
    return toCamelCase(response.data);
  },

  /**
   * Delete a swarm workspace.
   * DELETE /swarm-workspaces/{id}
   * Note: Cannot delete the default workspace (will return 403).
   */
  async delete(id: string): Promise<void> {
    await api.delete(`/swarm-workspaces/${id}`);
  },

  /**
   * Initialize folder structure for a workspace.
   * POST /swarm-workspaces/{id}/init-folders
   * Creates: Context, Docs, Projects, Tasks, ToDos, Plans, Historical-Chats, Reports
   */
  async initFolders(id: string): Promise<void> {
    await api.post(`/swarm-workspaces/${id}/init-folders`);
  },

  /**
   * Archive a workspace (sets is_archived=true).
   * POST /swarm-workspaces/{id}/archive
   */
  async archive(id: string): Promise<SwarmWorkspace> {
    const response = await api.post<Record<string, unknown>>(`/swarm-workspaces/${id}/archive`);
    return toCamelCase(response.data);
  },

  /**
   * Unarchive a workspace (sets is_archived=false).
   * POST /swarm-workspaces/{id}/unarchive
   */
  async unarchive(id: string): Promise<SwarmWorkspace> {
    const response = await api.post<Record<string, unknown>>(`/swarm-workspaces/${id}/unarchive`);
    return toCamelCase(response.data);
  },
};
