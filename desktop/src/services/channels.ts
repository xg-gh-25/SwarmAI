import api from './api';
import type {
  Channel,
  ChannelCreateRequest,
  ChannelUpdateRequest,
  ChannelStatusResponse,
  ChannelSession,
  ChannelTypeInfo,
} from '../types';

// Convert snake_case response to camelCase
const toCamelCase = (data: Record<string, unknown>): Channel => {
  // Parse list fields that may be JSON strings
  const parseList = (val: unknown): string[] => {
    if (typeof val === 'string') {
      try {
        return JSON.parse(val) as string[];
      } catch {
        return [];
      }
    }
    return (val as string[]) || [];
  };

  // Parse config if string
  let config = data.config as Record<string, unknown>;
  if (typeof config === 'string') {
    try {
      config = JSON.parse(config) as Record<string, unknown>;
    } catch {
      config = {};
    }
  }

  return {
    id: data.id as string,
    name: data.name as string,
    channelType: data.channel_type as Channel['channelType'],
    agentId: data.agent_id as string,
    agentName: data.agent_name as string | undefined,
    config: config || {},
    status: (data.status as Channel['status']) || 'inactive',
    errorMessage: data.error_message as string | undefined,
    accessMode: (data.access_mode as Channel['accessMode']) || 'allowlist',
    allowedSenders: parseList(data.allowed_senders),
    blockedSenders: parseList(data.blocked_senders),
    rateLimitPerMinute: (data.rate_limit_per_minute as number) ?? 10,
    enableSkills: Boolean(data.enable_skills),
    enableMcp: Boolean(data.enable_mcp),
    createdAt: data.created_at as string,
    updatedAt: data.updated_at as string,
  };
};

// Convert camelCase request to snake_case
const toSnakeCase = (data: ChannelCreateRequest | ChannelUpdateRequest) => {
  const result: Record<string, unknown> = {};
  if ('name' in data && data.name !== undefined) result.name = data.name;
  if ('channelType' in data && data.channelType !== undefined) result.channel_type = data.channelType;
  if ('agentId' in data && data.agentId !== undefined) result.agent_id = data.agentId;
  if (data.config !== undefined) result.config = data.config;
  if (data.accessMode !== undefined) result.access_mode = data.accessMode;
  if (data.allowedSenders !== undefined) result.allowed_senders = data.allowedSenders;
  if ('blockedSenders' in data && data.blockedSenders !== undefined) result.blocked_senders = data.blockedSenders;
  if (data.rateLimitPerMinute !== undefined) result.rate_limit_per_minute = data.rateLimitPerMinute;
  if (data.enableSkills !== undefined) result.enable_skills = data.enableSkills;
  if (data.enableMcp !== undefined) result.enable_mcp = data.enableMcp;
  return result;
};

const toSessionCamelCase = (data: Record<string, unknown>): ChannelSession => ({
  id: data.id as string,
  channelId: data.channel_id as string,
  externalChatId: data.external_chat_id as string,
  externalSenderId: data.external_sender_id as string | undefined,
  externalThreadId: data.external_thread_id as string | undefined,
  sessionId: data.session_id as string,
  senderDisplayName: data.sender_display_name as string | undefined,
  messageCount: (data.message_count as number) ?? 0,
  lastMessageAt: data.last_message_at as string | undefined,
  createdAt: data.created_at as string,
});

const toStatusCamelCase = (data: Record<string, unknown>): ChannelStatusResponse => ({
  channelId: data.channel_id as string,
  status: data.status as string,
  uptimeSeconds: data.uptime_seconds as number | undefined,
  messagesProcessed: (data.messages_processed as number) ?? 0,
  activeSessions: (data.active_sessions as number) ?? 0,
  errorMessage: data.error_message as string | undefined,
});

export const channelsService = {
  // ============== CRUD ==============

  async list(): Promise<Channel[]> {
    const response = await api.get<Record<string, unknown>[]>('/channels');
    return response.data.map(toCamelCase);
  },

  async get(id: string): Promise<Channel> {
    const response = await api.get<Record<string, unknown>>(`/channels/${id}`);
    return toCamelCase(response.data);
  },

  async create(data: ChannelCreateRequest): Promise<Channel> {
    const response = await api.post<Record<string, unknown>>('/channels', toSnakeCase(data));
    return toCamelCase(response.data);
  },

  async update(id: string, data: ChannelUpdateRequest): Promise<Channel> {
    const response = await api.put<Record<string, unknown>>(`/channels/${id}`, toSnakeCase(data));
    return toCamelCase(response.data);
  },

  async delete(id: string): Promise<void> {
    await api.delete(`/channels/${id}`);
  },

  // ============== Channel Types ==============

  async listTypes(): Promise<ChannelTypeInfo[]> {
    const response = await api.get<Record<string, unknown>[]>('/channels/types');
    return response.data.map((item) => ({
      id: item.id as string,
      label: item.label as string,
      description: item.description as string,
      configFields: ((item.config_fields || item.configFields) as ChannelTypeInfo['configFields']) || [],
      available: item.available as boolean,
    }));
  },

  // ============== Lifecycle ==============

  async start(id: string): Promise<ChannelStatusResponse> {
    const response = await api.post<Record<string, unknown>>(`/channels/${id}/start`);
    return toStatusCamelCase(response.data);
  },

  async stop(id: string): Promise<{ channelId: string; status: string }> {
    const response = await api.post<Record<string, unknown>>(`/channels/${id}/stop`);
    return {
      channelId: response.data.channel_id as string,
      status: response.data.status as string,
    };
  },

  async restart(id: string): Promise<ChannelStatusResponse> {
    const response = await api.post<Record<string, unknown>>(`/channels/${id}/restart`);
    return toStatusCamelCase(response.data);
  },

  async getStatus(id: string): Promise<ChannelStatusResponse> {
    const response = await api.get<Record<string, unknown>>(`/channels/${id}/status`);
    return toStatusCamelCase(response.data);
  },

  async test(id: string): Promise<{ channelId: string; channelType: string; valid: boolean; error?: string }> {
    const response = await api.post<Record<string, unknown>>(`/channels/${id}/test`);
    return {
      channelId: response.data.channel_id as string,
      channelType: response.data.channel_type as string,
      valid: response.data.valid as boolean,
      error: response.data.error as string | undefined,
    };
  },

  // ============== Sessions ==============

  async listSessions(channelId: string): Promise<ChannelSession[]> {
    const response = await api.get<Record<string, unknown>[]>(`/channels/${channelId}/sessions`);
    return response.data.map(toSessionCamelCase);
  },
};
