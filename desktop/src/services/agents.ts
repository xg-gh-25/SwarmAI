import api from './api';
import type { Agent, AgentCreateRequest, AgentUpdateRequest } from '../types';

// Convert camelCase to snake_case for API requests
const toSnakeCase = (data: AgentCreateRequest | AgentUpdateRequest) => {
  const result: Record<string, unknown> = {};
  if (data.name !== undefined) result.name = data.name;
  if (data.description !== undefined) result.description = data.description;
  if (data.model !== undefined) result.model = data.model;
  if (data.permissionMode !== undefined) result.permission_mode = data.permissionMode;
  if (data.systemPrompt !== undefined) result.system_prompt = data.systemPrompt;
  if (data.pluginIds !== undefined) result.plugin_ids = data.pluginIds;
  if (data.allowedSkills !== undefined) result.allowed_skills = data.allowedSkills;
  if (data.allowAllSkills !== undefined) result.allow_all_skills = data.allowAllSkills;
  if (data.mcpIds !== undefined) result.mcp_ids = data.mcpIds;
  if (data.allowedTools !== undefined) result.allowed_tools = data.allowedTools;
  if (data.enableBashTool !== undefined) result.enable_bash_tool = data.enableBashTool;
  if (data.enableFileTools !== undefined) result.enable_file_tools = data.enableFileTools;
  if (data.enableWebTools !== undefined) result.enable_web_tools = data.enableWebTools;
  if (data.globalUserMode !== undefined) result.global_user_mode = data.globalUserMode;
  if (data.enableHumanApproval !== undefined) result.enable_human_approval = data.enableHumanApproval;
  return result;
};

// Convert snake_case response to camelCase
const toCamelCase = (data: Record<string, unknown>): Agent => {
  return {
    id: data.id as string,
    name: data.name as string,
    description: data.description as string | undefined,
    model: data.model as string | undefined,
    permissionMode: (data.permission_mode as Agent['permissionMode']) ?? 'default',
    systemPrompt: data.system_prompt as string | undefined,
    allowedTools: (data.allowed_tools as string[]) || [],
    pluginIds: (data.plugin_ids as string[]) || [],
    allowedSkills: (data.allowed_skills as string[]) || [],
    allowAllSkills: (data.allow_all_skills as boolean) ?? false,
    mcpIds: (data.mcp_ids as string[]) || [],
    workingDirectory: data.working_directory as string | undefined,
    enableBashTool: (data.enable_bash_tool as boolean) ?? true,
    enableFileTools: (data.enable_file_tools as boolean) ?? true,
    enableWebTools: (data.enable_web_tools as boolean) ?? false,
    enableToolLogging: (data.enable_tool_logging as boolean) ?? true,
    enableSafetyChecks: (data.enable_safety_checks as boolean) ?? true,
    globalUserMode: (data.global_user_mode as boolean) ?? false,
    enableHumanApproval: (data.enable_human_approval as boolean) ?? true,
    isDefault: (data.is_default as boolean) ?? false,
    isSystemAgent: (data.is_system_agent as boolean) ?? false,
    status: (data.status as 'active' | 'inactive') ?? 'active',
    createdAt: (data.created_at as string) ?? '',
    updatedAt: (data.updated_at as string) ?? '',
  };
};

export const agentsService = {
  // List all agents
  async list(): Promise<Agent[]> {
    const response = await api.get<Record<string, unknown>[]>('/agents');
    return response.data.map(toCamelCase);
  },

  // Get agent by ID
  async get(id: string): Promise<Agent> {
    const response = await api.get<Record<string, unknown>>(`/agents/${id}`);
    return toCamelCase(response.data);
  },

  // Create new agent
  async create(data: AgentCreateRequest): Promise<Agent> {
    const response = await api.post<Record<string, unknown>>('/agents', toSnakeCase(data));
    return toCamelCase(response.data);
  },

  // Update agent
  async update(id: string, data: AgentUpdateRequest): Promise<Agent> {
    const response = await api.put<Record<string, unknown>>(`/agents/${id}`, toSnakeCase(data));
    return toCamelCase(response.data);
  },

  // Delete agent
  async delete(id: string): Promise<void> {
    await api.delete(`/agents/${id}`);
  },

  // Get default agent
  async getDefault(): Promise<Agent> {
    const response = await api.get<Record<string, unknown>>('/agents/default');
    return toCamelCase(response.data);
  },

  // Get available models
  async listModels(): Promise<string[]> {
    const response = await api.get<string[]>('/agents/models');
    return response.data;
  },

  // Get agent's effective working directory
  async getWorkingDirectory(id: string): Promise<{ path: string; isGlobalMode: boolean }> {
    const response = await api.get<{ path: string; is_global_mode: boolean }>(
      `/agents/${id}/working-directory`
    );
    return {
      path: response.data.path,
      isGlobalMode: response.data.is_global_mode,
    };
  },
};
