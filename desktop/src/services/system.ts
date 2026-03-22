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
  error?: string;
}

export interface ChannelGatewayStatus {
  running: boolean;
  startupState: string;  // "not_started" | "starting" | "started" | "failed"
}

export interface SwarmWorkspaceStatus {
  ready: boolean;
  name?: string;
  path?: string;
  error?: string;
}

export interface SystemStatus {
  database: DatabaseStatus;
  agent: AgentStatus;
  channelGateway: ChannelGatewayStatus;
  swarmWorkspace: SwarmWorkspaceStatus;
  initialized: boolean;
  initializationMode: string;  // 'first_run', 'quick_validation', or 'reset'
  initializationComplete: boolean;  // The persistent flag value
  startupTimeMs: number | null;                    // Total backend startup duration in ms
  phaseTimings: Record<string, number> | null;     // Per-phase durations (database_ms, workspace_ms, etc.)
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
 * - swarm_workspace -> swarmWorkspace
 */
const toCamelCase = (data: Record<string, unknown>): SystemStatus => {
  const database = data.database as Record<string, unknown>;
  const agent = data.agent as Record<string, unknown>;
  const channelGateway = data.channel_gateway as Record<string, unknown>;
  const swarmWorkspace = data.swarm_workspace as Record<string, unknown>;

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
      error: agent.error as string | undefined,
    },
    channelGateway: {
      running: channelGateway.running as boolean,
      startupState: (channelGateway.startup_state as string) ?? 'not_started',
    },
    swarmWorkspace: {
      ready: swarmWorkspace.ready as boolean,
      name: swarmWorkspace.name as string | undefined,
      path: swarmWorkspace.path as string | undefined,
      error: swarmWorkspace.error as string | undefined,
    },
    initialized: data.initialized as boolean,
    initializationMode: (data.initialization_mode as string) ?? 'unknown',
    initializationComplete: (data.initialization_complete as boolean) ?? false,
    startupTimeMs: (data.startup_time_ms as number) ?? null,
    phaseTimings: (data.phase_timings as Record<string, number>) ?? null,
    timestamp: data.timestamp as string,
  };
};

// ============== Service ==============

const STATUS_TIMEOUT_MS = 5000;

export interface MaxTabsInfo {
  maxTabs: number;
  memoryPressure: 'ok' | 'warning' | 'critical';
}

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

  /**
   * Get the dynamic max-tabs limit based on available system RAM.
   *
   * Returns 1–4 depending on memory headroom. Each tab requires ~500MB
   * for CLI + MCP subprocesses.
   */
  async getMaxTabs(): Promise<MaxTabsInfo> {
    const response = await api.get<Record<string, unknown>>('/system/max-tabs');
    const data = response.data;
    return {
      maxTabs: typeof data.max_tabs === 'number' ? data.max_tabs : 2,
      memoryPressure: (['ok', 'warning', 'critical'].includes(data.memory_pressure as string)
        ? data.memory_pressure
        : 'ok') as MaxTabsInfo['memoryPressure'],
    };
  },
};
