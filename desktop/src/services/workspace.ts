/**
 * Workspace service for agent file operations, singleton workspace config,
 * project CRUD, folder management, and workspace tree browsing.
 *
 * Key exports:
 * - ``workspaceService``        — Agent-scoped file browsing, reading, writing, uploading;
 *                                  also ``getTree()`` and ``refreshTree()`` for the workspace
 *                                  explorer tree (GET /api/workspace/tree) with ETag caching
 * - ``workspaceConfigService``  — Singleton SwarmWS config (GET/PUT /api/workspace)
 * - ``projectService``          — Project CRUD (GET/POST/PUT/DELETE /api/projects)
 * - ``folderService``           — Folder create, delete, rename (/api/workspace/folders, /api/workspace/rename)
 *
 * Conversion helpers:
 * - ``configToCamelCase``         — snake_case workspace config response → camelCase
 * - ``configUpdateToSnakeCase``   — camelCase update request → snake_case
 * - ``projectToCamelCase``        — snake_case project response → camelCase
 * - ``projectUpdateToSnakeCase``  — camelCase project update → snake_case
 * - ``historyEntryToCamelCase``   — snake_case history entry → camelCase
 * - ``treeNodeToCamelCase``       — snake_case tree node → camelCase (recursive)
 */
import api from './api';
import type {
  WorkspaceListResponse,
  WorkspaceFile,
  WorkspaceFileContent,
  WorkspaceConfig,
  WorkspaceConfigUpdateRequest,
  Project,
  ProjectCreateRequest,
  ProjectUpdateRequest,
  ProjectHistoryEntry,
  TreeNode,
} from '../types';

// ─────────────────────────────────────────────────────────────────────────────
// Agent workspace file operation converters (existing)
// ─────────────────────────────────────────────────────────────────────────────

// Convert snake_case file to camelCase
const fileToCamelCase = (data: Record<string, unknown>): WorkspaceFile => {
  return {
    name: data.name as string,
    type: data.type as 'file' | 'directory',
    size: data.size as number,
    modified: data.modified as string,
  };
};

// Convert snake_case list response to camelCase
const listResponseToCamelCase = (data: Record<string, unknown>): WorkspaceListResponse => {
  const files = (data.files as Record<string, unknown>[]) || [];
  return {
    files: files.map(fileToCamelCase),
    currentPath: data.current_path as string,
    parentPath: (data.parent_path as string | null) ?? null,
  };
};

// Convert snake_case file content to camelCase
const fileContentToCamelCase = (data: Record<string, unknown>): WorkspaceFileContent => {
  return {
    content: data.content as string,
    encoding: data.encoding as 'utf-8' | 'base64',
    size: data.size as number,
    mimeType: data.mime_type as string,
  };
};

// ─────────────────────────────────────────────────────────────────────────────
// Workspace config converters (snake_case ↔ camelCase)
// ─────────────────────────────────────────────────────────────────────────────

/** Convert a snake_case workspace config API response to camelCase. */
export const configToCamelCase = (data: Record<string, unknown>): WorkspaceConfig => {
  return {
    id: data.id as string,
    name: data.name as string,
    filePath: data.file_path as string,
    icon: data.icon as string | undefined,
    context: data.context as string | undefined,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

/** Convert a camelCase workspace config update request to snake_case. */
export const configUpdateToSnakeCase = (
  data: WorkspaceConfigUpdateRequest
): Record<string, unknown> => {
  const result: Record<string, unknown> = {};
  if (data.icon !== undefined) result.icon = data.icon;
  if (data.context !== undefined) result.context = data.context;
  return result;
};

// ─────────────────────────────────────────────────────────────────────────────
// Project converters (snake_case ↔ camelCase)
// ─────────────────────────────────────────────────────────────────────────────

/** Convert a snake_case project API response to camelCase (Cadence 2). */
export const projectToCamelCase = (data: Record<string, unknown>): Project => {
  return {
    id: data.id as string,
    name: data.name as string,
    description: (data.description as string) || '',
    path: (data.path as string) || '',
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
    status: data.status as 'active' | 'archived' | 'completed',
    priority: data.priority as 'low' | 'medium' | 'high' | 'critical' | undefined,
    tags: (data.tags as string[]) || [],
    schemaVersion: data.schema_version as string,
    version: data.version as number,
    contextL0: data.context_l0 as string | undefined,
    contextL1: data.context_l1 as string | undefined,
  };
};

/** Convert a camelCase project update request to snake_case (Cadence 2). */
export const projectUpdateToSnakeCase = (
  data: ProjectUpdateRequest
): Record<string, unknown> => {
  const result: Record<string, unknown> = {};
  if (data.name !== undefined) result.name = data.name;
  if (data.description !== undefined) result.description = data.description;
  if (data.status !== undefined) result.status = data.status;
  if (data.tags !== undefined) result.tags = data.tags;
  if (data.priority !== undefined) result.priority = data.priority;
  return result;
};

/** Convert a snake_case history entry to camelCase. */
export const historyEntryToCamelCase = (data: Record<string, unknown>): ProjectHistoryEntry => ({
  version: data.version as number,
  timestamp: data.timestamp as string,
  action: data.action as string,
  changes: data.changes as Record<string, { from: unknown; to: unknown }>,
  source: data.source as 'user' | 'agent' | 'system' | 'migration',
});

// ─────────────────────────────────────────────────────────────────────────────
// Workspace tree converters and ETag cache (Cadence 3)
// ─────────────────────────────────────────────────────────────────────────────

/** Module-level ETag cache for the workspace tree endpoint.
 *
 * Stores the last ETag received from `GET /api/workspace/tree` and the
 * corresponding tree data. On subsequent `getTree()` calls the cached ETag
 * is sent via `If-None-Match`; if the server returns 304 the cached data
 * is returned without re-parsing. `refreshTree()` bypasses the cache to
 * force a fresh response after CRUD mutations.
 */
let _treeETag: string | null = null;
let _cachedTree: TreeNode[] | null = null;

/** Convert a snake_case tree node from the backend to a camelCase TreeNode (recursive).
 *
 * Children are recursively converted so the entire tree is camelCase on the frontend.
 * All files are user-manageable — no system-managed restrictions.
 */
export function treeNodeToCamelCase(data: Record<string, unknown>): TreeNode {
  return {
    name: data.name as string,
    path: data.path as string,
    type: data.type as 'file' | 'directory',
    children: data.children
      ? (data.children as Record<string, unknown>[]).map(treeNodeToCamelCase)
      : undefined,
    gitStatus: (data.git_status ?? data.gitStatus) as TreeNode['gitStatus'],
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent-scoped file operations (existing)
// ─────────────────────────────────────────────────────────────────────────────

export const workspaceService = {
  /** Fetch the workspace filesystem tree.
   *
   * Sends `If-None-Match` with the cached ETag when available. If the
   * server responds with 304 Not Modified, the previously cached tree is
   * returned immediately. Otherwise the fresh response is cached and
   * converted from snake_case to camelCase via `treeNodeToCamelCase`.
   *
   * @param depth - Maximum folder depth (1–5). Omit for server default.
   */
  async getTree(depth?: number): Promise<TreeNode[]> {
    const params = depth != null ? `?depth=${depth}` : '';
    const headers: Record<string, string> = {};
    if (_treeETag) {
      headers['If-None-Match'] = _treeETag;
    }

    try {
      const response = await api.get<Record<string, unknown>[]>(
        `/workspace/tree${params}`,
        { headers }
      );

      // Cache the ETag from the response
      const etag = response.headers?.['etag'] as string | undefined;
      if (etag) {
        _treeETag = etag;
      }

      const tree = response.data.map(treeNodeToCamelCase);
      _cachedTree = tree;
      return tree;
    } catch (error: unknown) {
      // Axios wraps 304 as an error in some configurations; also handle
      // the case where the interceptor converts it to an ApiError.
      const axiosErr = error as { statusCode?: number; response?: { status?: number } };
      const status = axiosErr.statusCode ?? axiosErr.response?.status;
      if (status === 304 && _cachedTree) {
        return _cachedTree;
      }
      throw error;
    }
  },

  /** Force-fetch the workspace tree, bypassing the ETag cache.
   *
   * Call this after CRUD mutations (create/delete project, create/delete
   * file, rename, etc.) to ensure the explorer reflects the latest state.
   */
  async refreshTree(depth?: number): Promise<TreeNode[]> {
    _treeETag = null;
    _cachedTree = null;
    return this.getTree(depth);
  },

  /**
   * Browse server filesystem for folder selection (web browser mode)
   * @param path Absolute path to browse (default: home directory)
   */
  async browseFilesystem(path: string = '.'): Promise<WorkspaceListResponse> {
    const response = await api.post<Record<string, unknown>>('/workspace/browse', { path });
    return listResponseToCamelCase(response.data);
  },

  /**
   * List files and directories in the specified path
   * @param agentId The agent ID
   * @param path Relative path to list (default: ".")
   * @param basePath Optional custom base path (e.g., from "work in a folder" selection)
   */
  async listFiles(
    agentId: string,
    path: string = '.',
    basePath?: string
  ): Promise<WorkspaceListResponse> {
    const response = await api.post<Record<string, unknown>>(
      `/workspace/${agentId}/list`,
      { path },
      { params: basePath ? { base_path: basePath } : undefined }
    );
    return listResponseToCamelCase(response.data);
  },

  /**
   * Read file content
   * @param agentId The agent ID
   * @param path Relative path to the file
   * @param basePath Optional custom base path (e.g., from "work in a folder" selection)
   */
  async readFile(agentId: string, path: string, basePath?: string): Promise<WorkspaceFileContent> {
    const params: Record<string, string> = { path };
    if (basePath) {
      params.base_path = basePath;
    }
    const response = await api.get<Record<string, unknown>>(`/workspace/${agentId}/read`, {
      params,
    });
    return fileContentToCamelCase(response.data);
  },

  /**
   * Upload a file to the agent's workspace
   * Used for TXT/CSV files that Claude reads via Read tool
   * @param agentId The agent ID
   * @param filename Original filename
   * @param content Base64 encoded file content
   * @param path Target directory path (default: ".")
   */
  async uploadFile(
    agentId: string,
    filename: string,
    content: string,
    path: string = '.'
  ): Promise<{ path: string; filename: string; size: number }> {
    const response = await api.post<Record<string, unknown>>(`/workspace/${agentId}/upload`, {
      filename,
      content,
      path,
    });
    return {
      path: response.data.path as string,
      filename: response.data.filename as string,
      size: response.data.size as number,
    };
  },

  /**
   * Write content to a file
   * Used by the File Editor Modal to save changes
   * @param agentId The agent ID
   * @param path Relative path to the file
   * @param content UTF-8 text content to write
   * @param basePath Optional custom base path
   */
  async writeFile(
    agentId: string,
    path: string,
    content: string,
    basePath?: string
  ): Promise<{ path: string; size: number }> {
    const response = await api.put<Record<string, unknown>>(
      `/workspace/${agentId}/write`,
      { path, content },
      { params: basePath ? { base_path: basePath } : undefined }
    );
    return {
      path: response.data.path as string,
      size: response.data.size as number,
    };
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Singleton workspace config service (GET/PUT /api/workspace)
// ─────────────────────────────────────────────────────────────────────────────

export const workspaceConfigApiService = {
  /** Get the singleton SwarmWS workspace configuration. */
  async getConfig(): Promise<WorkspaceConfig> {
    const response = await api.get<Record<string, unknown>>('/workspace');
    return configToCamelCase(response.data);
  },

  /** Update the singleton workspace configuration (icon, context). */
  async updateConfig(data: WorkspaceConfigUpdateRequest): Promise<WorkspaceConfig> {
    const response = await api.put<Record<string, unknown>>(
      '/workspace',
      configUpdateToSnakeCase(data)
    );
    return configToCamelCase(response.data);
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Project CRUD service (GET/POST/PUT/DELETE /api/projects)
// ─────────────────────────────────────────────────────────────────────────────

export const projectService = {
  /** List all projects in the workspace. */
  async listProjects(): Promise<Project[]> {
    const response = await api.get<Record<string, unknown>[]>('/projects');
    return response.data.map(projectToCamelCase);
  },

  /** Create a new project. */
  async createProject(data: ProjectCreateRequest): Promise<Project> {
    const response = await api.post<Record<string, unknown>>('/projects', { name: data.name });
    return projectToCamelCase(response.data);
  },

  /** Get a project by UUID. */
  async getProject(id: string): Promise<Project> {
    const response = await api.get<Record<string, unknown>>(`/projects/${id}`);
    return projectToCamelCase(response.data);
  },

  /** Update a project by UUID. */
  async updateProject(id: string, data: ProjectUpdateRequest): Promise<Project> {
    const response = await api.put<Record<string, unknown>>(
      `/projects/${id}`,
      projectUpdateToSnakeCase(data)
    );
    return projectToCamelCase(response.data);
  },

  /** Delete a project by UUID. */
  async deleteProject(id: string): Promise<void> {
    await api.delete(`/projects/${id}`);
  },

  /** Get a project by name via query parameter. */
  async getProjectByName(name: string): Promise<Project | null> {
    const response = await api.get<Record<string, unknown>[]>('/projects', { params: { name } });
    const projects = response.data.map(projectToCamelCase);
    return projects.length > 0 ? projects[0] : null;
  },

  /** Get the update history for a project by UUID. */
  async getHistory(id: string): Promise<ProjectHistoryEntry[]> {
    const response = await api.get<Record<string, unknown>>(`/projects/${id}/history`);
    const data = response.data;
    // Backend returns { project_id, history: [...] } — extract the history array
    const historyData = (data.history as Record<string, unknown>[] | undefined) ?? data;
    if (Array.isArray(historyData)) {
      return historyData.map((entry: Record<string, unknown>) => historyEntryToCamelCase(entry));
    }
    return [];
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Folder CRUD service (POST/DELETE /api/workspace/folders, PUT /api/workspace/rename)
// ─────────────────────────────────────────────────────────────────────────────

export const folderService = {
  /** Create a folder at the given relative path within the workspace. */
  async createFolder(path: string): Promise<void> {
    await api.post('/workspace/folders', { path });
  },

  /** Delete a folder or file at the given relative path within the workspace. */
  async deleteFolder(path: string): Promise<void> {
    await api.delete('/workspace/folders', { data: { path } });
  },

  /** Rename or move an item within the workspace. */
  async renameItem(oldPath: string, newPath: string): Promise<void> {
    await api.put('/workspace/rename', { old_path: oldPath, new_path: newPath });
  },
};
