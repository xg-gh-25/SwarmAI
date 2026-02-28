import api from './api';

// ============== Interfaces ==============

export interface DatabaseStatus {
  healthy: boolean;
  error?: string;
}

export interface AgentStatus {
  ready: boolean;
  name?: string;
  skillsCount: number;
  mcpServersCount: number;
}

export interface ChannelGatewayStatus {
  running: boolean;
}

export interface SystemStatus {
  database: DatabaseStatus;
  agent: AgentStatus;
  channelGateway: ChannelGatewayStatus;
  initialized: boolean;
  timestamp: string;
}

// ============== Case Conversion ==============

/**
 * Convert snake_case API response to camelCase for TypeScript consumption.
 * 
 * Backend response (snake_case):
 * - skills_count -> skillsCount
 * - mcp_servers_count -> mcpServersCount
 * - channel_gateway -> channelGateway
 */
const toCamelCase = (data: Record<string, unknown>): SystemStatus => {
  const database = data.database as Record<string, unknown>;
  const agent = data.agent as Record<string, unknown>;
  const channelGateway = data.channel_gateway as Record<string, unknown>;

  return {
    database: {
      healthy: database.healthy as boolean,
      error: database.error as string | undefined,
    },
    agent: {
      ready: agent.ready as boolean,
      name: agent.name as string | undefined,
      skillsCount: (agent.skills_count as number) ?? 0,
      mcpServersCount: (agent.mcp_servers_count as number) ?? 0,
    },
    channelGateway: {
      running: channelGateway.running as boolean,
    },
    initialized: data.initialized as boolean,
    timestamp: data.timestamp as string,
  };
};

// ============== Service ==============

const STATUS_TIMEOUT_MS = 5000;

export const systemService = {
  /**
   * Get current system initialization status.
   * 
   * Fetches status from /api/system/status endpoint with a 5-second timeout.
   * Converts snake_case response to camelCase for TypeScript consumption.
   * 
   * @throws Error if the API call fails or times out
   */
  async getStatus(): Promise<SystemStatus> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);

    try {
      const response = await api.get<Record<string, unknown>>('/system/status', {
        signal: controller.signal,
      });
      return toCamelCase(response.data);
    } finally {
      clearTimeout(timeoutId);
    }
  },
};
