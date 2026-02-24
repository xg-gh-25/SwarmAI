import api from './api';
import type { WorkspaceListResponse, WorkspaceFile, WorkspaceFileContent } from '../types';

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

export const workspaceService = {
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
};
