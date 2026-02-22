import api from './api';
import type { MCPServer, MCPServerCreateRequest, MCPServerUpdateRequest } from '../types';

// Convert camelCase to snake_case for API requests
const toSnakeCase = (data: MCPServerCreateRequest | MCPServerUpdateRequest) => {
  return {
    name: data.name,
    description: data.description,
    connection_type: data.connectionType,
    config: data.config,
    allowed_tools: data.allowedTools,
    rejected_tools: data.rejectedTools,
  };
};

// Convert snake_case response to camelCase
const toCamelCase = (data: Record<string, unknown>): MCPServer => {
  return {
    id: data.id as string,
    name: data.name as string,
    description: data.description as string | undefined,
    connectionType: data.connection_type as 'stdio' | 'sse' | 'http',
    config: data.config as Record<string, unknown>,
    allowedTools: data.allowed_tools as string[] | undefined,
    rejectedTools: data.rejected_tools as string[] | undefined,
    endpoint: data.endpoint as string | undefined,
    version: data.version as string | undefined,
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

export const mcpService = {
  // List all MCP servers
  async list(): Promise<MCPServer[]> {
    const response = await api.get<Record<string, unknown>[]>('/mcp');
    return response.data.map(toCamelCase);
  },

  // Get MCP server by ID
  async get(id: string): Promise<MCPServer> {
    const response = await api.get<Record<string, unknown>>(`/mcp/${id}`);
    return toCamelCase(response.data);
  },

  // Create new MCP server
  async create(data: MCPServerCreateRequest): Promise<MCPServer> {
    const response = await api.post<Record<string, unknown>>('/mcp', toSnakeCase(data));
    return toCamelCase(response.data);
  },

  // Update MCP server
  async update(id: string, data: MCPServerUpdateRequest): Promise<MCPServer> {
    const response = await api.put<Record<string, unknown>>(`/mcp/${id}`, toSnakeCase(data));
    return toCamelCase(response.data);
  },

  // Delete MCP server
  async delete(id: string): Promise<void> {
    await api.delete(`/mcp/${id}`);
  },
};
