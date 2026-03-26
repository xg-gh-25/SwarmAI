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
  onboardingComplete: boolean;  // True after first-run onboarding wizard
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
    onboardingComplete: (data.onboarding_complete as boolean) ?? false,
    startupTimeMs: (data.startup_time_ms as number) ?? null,
    phaseTimings: (data.phase_timings as Record<string, number>) ?? null,
    timestamp: data.timestamp as string,
  };
};

// ============== Service ==============

// ============== Briefing Types ==============

export interface BriefingFocusItem {
  title: string;
  priority: string;  // P0, P1, P2
  score: number;
  source: string;    // "thread" or "hint"
  momentum: boolean;
}

export interface BriefingSignal {
  title: string;
  summary: string;
  source: string;
  url: string;
  urgency: string;   // "high", "medium", "low"
  relevance: number;
}

export interface BriefingJob {
  name: string;
  status: string;  // "success", "failed", etc.
  duration: number;
}

export interface SessionBriefing {
  focus: BriefingFocusItem[];
  signals: BriefingSignal[];
  jobs: BriefingJob[];
  learning: string | null;
  generatedAt: string | null;
}

const STATUS_TIMEOUT_MS = 5000;

export interface MaxTabsInfo {
  maxTabs: number;
  /** Max chat tabs allowed (maxTabs - 1, reserving 1 slot for channels). */
  chatMax: number;
  memoryPressure: 'ok' | 'warning' | 'critical';
}

// ============== Onboarding Types ==============

export interface VerifyAuthResponse {
  success: boolean;
  model?: string;
  bedrockModel?: string;
  region?: string;
  latencyMs?: number;
  error?: string;
  errorType?: string;
  fixHint?: string;
}

export interface AuthHintResponse {
  hasAdaDir: boolean;
  hasSsoCache: boolean;
  hasApiKey: boolean;
  suggestedMethod: 'ada' | 'sso' | 'apikey';
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
    const maxTabs = typeof data.max_tabs === 'number' ? data.max_tabs : 2;
    const chatMax = typeof data.chat_max === 'number' ? data.chat_max : Math.max(1, maxTabs - 1);
    return {
      maxTabs,
      chatMax,
      memoryPressure: (['ok', 'warning', 'critical'].includes(data.memory_pressure as string)
        ? data.memory_pressure
        : 'ok') as MaxTabsInfo['memoryPressure'],
    };
  },

  /**
   * Get session briefing data for the Welcome Screen.
   *
   * Returns focus suggestions, external signals, job results, and
   * learning insights from the proactive intelligence engine.
   */
  async getBriefing(): Promise<SessionBriefing> {
    try {
      const response = await api.get<Record<string, unknown>>('/system/briefing');
      const d = response.data;
      return {
        focus: (d.focus as BriefingFocusItem[]) ?? [],
        signals: (d.signals as BriefingSignal[]) ?? [],
        jobs: (d.jobs as BriefingJob[]) ?? [],
        learning: (d.learning as string) ?? null,
        generatedAt: (d.generated_at as string) ?? null,
      };
    } catch {
      return { focus: [], signals: [], jobs: [], learning: null, generatedAt: null };
    }
  },

  /**
   * Verify LLM authentication by making a real API call.
   * Returns success/failure with model name, latency, and error details.
   */
  async verifyAuth(): Promise<VerifyAuthResponse> {
    const response = await api.post<Record<string, unknown>>('/system/verify-auth');
    const d = response.data;
    return {
      success: d.success as boolean,
      model: d.model as string | undefined,
      bedrockModel: d.bedrock_model as string | undefined,
      region: d.region as string | undefined,
      latencyMs: d.latency_ms as number | undefined,
      error: d.error as string | undefined,
      errorType: d.error_type as string | undefined,
      fixHint: d.fix_hint as string | undefined,
    };
  },

  /**
   * Get hints about the local credential environment.
   * Helps pick a sensible default auth method.
   */
  async getAuthHint(): Promise<AuthHintResponse> {
    const response = await api.get<Record<string, unknown>>('/system/auth-hint');
    const d = response.data;
    return {
      hasAdaDir: d.has_ada_dir as boolean,
      hasSsoCache: d.has_sso_cache as boolean,
      hasApiKey: d.has_api_key as boolean,
      suggestedMethod: d.suggested_method as AuthHintResponse['suggestedMethod'],
    };
  },

  /**
   * Mark onboarding as complete.
   */
  async setOnboardingComplete(): Promise<void> {
    await api.put('/system/onboarding-complete');
  },

  /**
   * Reset onboarding (re-run setup wizard).
   */
  async resetOnboarding(): Promise<void> {
    await api.delete('/system/onboarding-complete');
  },

  /**
   * Get Core Engine growth metrics for the dashboard.
   * Returns learning state, memory effectiveness, DDD health, session stats.
   */
  async getEngineMetrics(): Promise<EngineMetrics> {
    try {
      const response = await api.get<EngineMetrics>('/system/engine-metrics');
      return response.data;
    } catch {
      return {
        collectedAt: '',
        engineLevel: { current: 'unknown', l3Progress: '0/0', l3Features: {}, levels: {} },
        learning: {},
        memory: { status: 'error' },
        dddSuggestions: [],
        dddHealth: { projects: [] },
        contextHealth: { findings: [] },
        hooks: { available: false },
        sessions: {},
      };
    }
  },
};

// ============== Engine Metrics Types ==============

export interface EngineMetrics {
  collectedAt: string;
  engineLevel: {
    current: string;
    l3Progress: string;
    l3Features: Record<string, boolean>;
    levels: Record<string, string>;
  };
  learning: Record<string, unknown>;
  memory: Record<string, unknown>;
  dddSuggestions: Array<Record<string, string>>;
  dddHealth: { projects: Array<Record<string, unknown>> };
  contextHealth: { findings: Array<Record<string, string>>; lastCheck?: string };
  hooks: Record<string, unknown>;
  sessions: Record<string, unknown>;
}
