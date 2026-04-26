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
 * Deep snake_case → camelCase key converter for arbitrary nested objects.
 * Arrays are traversed, primitives pass through unchanged.
 */
function deepSnakeToCamel(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(deepSnakeToCamel);
  if (obj !== null && typeof obj === 'object') {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      const camelKey = k.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
      result[camelKey] = deepSnakeToCamel(v);
    }
    return result;
  }
  return obj;
}

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
  sourceUrl: string;
  urgency: string;   // "high", "medium", "low"
  relevance: number;
  lang: string;      // "en", "zh"
}

export interface BriefingJob {
  name: string;
  status: string;  // "success", "failed", etc.
  duration: number;
  summary?: string;      // Truncated summary from job output
  resultFile?: string;   // Workspace-relative path to result markdown
}

export interface BriefingTodo {
  id: string;        // 8-char prefix
  title: string;
  priority: string;  // "high", "medium", "low", "none"
  status: string;    // "pending", "overdue"
  dueDate?: string;
  nextStep?: string;
  files?: string[];
  description?: string;
}

// ============== Briefing Hub v2 Types ==============

export interface WorkingItem {
  title: string;
  priority: "high" | "medium" | "low";
  source: "email" | "slack-dm" | "slack-channel" | "calendar" | "reflect";
  sourceDetail: string;
  summary: string;
  action: "reply" | "review" | "attend" | "follow-up" | "read";
  resultFile?: string;
  timestamp: string;
}

export interface HotNewsItem {
  title: string;
  platform: string;
  rank: number;
  url: string;
  region: "cn" | "intl";
  lang: "en" | "zh";
}

export interface StockItem {
  ticker: string;
  name: string;
  status: "success" | "partial" | "failed";
  reportFile: string;
}

export interface BuildItem {
  runId: string;
  project: string;
  title: string;
  confidence: number | null;
  status: "complete" | "partial" | "in-progress";
  date: string;
  reportFile: string;
}

export interface ContentItem {
  slug: string;
  title: string;
  type: "video" | "poster" | "podcast" | "article";
  contentPackage: string;
  date: string;
}

export interface ArtifactItem {
  path: string;
  title: string;
  type: string;
  modifiedAt: string;
}

export interface SwarmOutput {
  builds: BuildItem[];
  content: ContentItem[];
  files: ArtifactItem[];
}

export interface JobStatusItem {
  id: string;
  name: string;
  status: "healthy" | "failed" | "disabled" | "running";
  lastRun: string | null;
  lastStatus: "success" | "failed" | "skipped" | null;
  schedule: string;
}

export interface JobsSummary {
  total: number;
  healthy: number;
  failed: number;
  disabled: number;
  lastRun: string | null;
  jobs: JobStatusItem[];
}

export interface SessionBriefing {
  focus: BriefingFocusItem[];
  signals: BriefingSignal[];
  hotNews: HotNewsItem[];
  working: WorkingItem[];
  stocks: StockItem[];
  output: SwarmOutput;
  jobsSummary: JobsSummary;
  jobs: BriefingJob[];        // backward compat
  todos: BriefingTodo[];
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
  adaDetails?: {
    accountId?: string;
    roleName?: string;
    region?: string;
    configured?: boolean;
    keyPrefix?: string;
  };
  awsProfiles?: string[];
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

      // Parse signals with new sourceUrl/lang fields
      const signals: BriefingSignal[] = ((d.signals as Record<string, unknown>[]) ?? []).map((s) => ({
        title: s.title as string,
        summary: (s.summary as string) ?? '',
        source: (s.source as string) ?? '',
        sourceUrl: (s.sourceUrl as string) ?? (s.source_url as string) ?? (s.url as string) ?? '',
        urgency: (s.urgency as string) ?? 'medium',
        relevance: (s.relevance as number) ?? (s.relevance_score as number) ?? 0,
        lang: (s.lang as string) ?? 'en',
      }));

      // Parse jobs (backward compat)
      const jobs: BriefingJob[] = ((d.jobs as Record<string, unknown>[]) ?? []).map((j) => ({
        name: j.name as string,
        status: j.status as string,
        duration: j.duration as number,
        summary: (j.summary as string) || undefined,
        resultFile: (j.result_file as string) || undefined,
      }));

      // Parse todos with new fields
      const todos: BriefingTodo[] = ((d.todos as Record<string, unknown>[]) ?? []).map((t) => ({
        id: t.id as string,
        title: t.title as string,
        priority: t.priority as string,
        status: t.status as string,
        dueDate: (t.due_date as string) || undefined,
        nextStep: (t.next_step as string) || undefined,
        files: (t.files as string[]) || undefined,
        description: (t.description as string) || undefined,
      }));

      // Parse hotNews
      const hotNews: HotNewsItem[] = ((d.hotNews as Record<string, unknown>[]) ?? []).map((h) => ({
        title: h.title as string,
        platform: (h.platform as string) ?? '',
        rank: (h.rank as number) ?? 0,
        url: (h.url as string) ?? '',
        region: ((h.region as string) ?? 'cn') as HotNewsItem['region'],
        lang: ((h.lang as string) ?? 'zh') as HotNewsItem['lang'],
      }));

      // Parse working items
      const working: WorkingItem[] = ((d.working as Record<string, unknown>[]) ?? []).map((w) => ({
        title: w.title as string,
        priority: (w.priority as WorkingItem['priority']) ?? 'low',
        source: (w.source as WorkingItem['source']) ?? 'reflect',
        sourceDetail: (w.sourceDetail ?? w.source_detail ?? '') as string,
        summary: (w.summary as string) ?? '',
        action: (w.action as WorkingItem['action']) ?? 'read',
        resultFile: (w.resultFile ?? w.result_file) as string | undefined,
        timestamp: (w.timestamp as string) ?? '',
      }));

      // Parse stocks
      const stocks: StockItem[] = ((d.stocks as Record<string, unknown>[]) ?? []).map((s) => ({
        ticker: s.ticker as string,
        name: s.name as string,
        status: (s.status as StockItem['status']) ?? 'success',
        reportFile: (s.reportFile ?? s.report_file ?? '') as string,
      }));

      // Parse output
      const rawOutput = (d.output as Record<string, unknown>) ?? {};
      const output: SwarmOutput = {
        builds: ((rawOutput.builds as Record<string, unknown>[]) ?? []).map((b) => ({
          runId: (b.runId ?? b.run_id) as string,
          project: b.project as string,
          title: b.title as string,
          confidence: (b.confidence as number) ?? null,
          status: (b.status as BuildItem['status']) ?? 'complete',
          date: b.date as string,
          reportFile: (b.reportFile ?? b.report_file) as string,
        })),
        content: ((rawOutput.content as Record<string, unknown>[]) ?? []).map((c) => ({
          slug: c.slug as string,
          title: c.title as string,
          type: (c.type as ContentItem['type']) ?? 'article',
          contentPackage: (c.contentPackage ?? c.content_package) as string,
          date: c.date as string,
        })),
        files: ((rawOutput.files as Record<string, unknown>[]) ?? []).map((f) => ({
          path: f.path as string,
          title: f.title as string,
          type: (f.type as string) ?? 'other',
          modifiedAt: (f.modifiedAt ?? f.modified_at) as string,
        })),
      };

      // Parse jobsSummary
      const rawJobs = (d.jobsSummary as Record<string, unknown>) ?? {};
      const jobsSummary: JobsSummary = {
        total: (rawJobs.total as number) ?? 0,
        healthy: (rawJobs.healthy as number) ?? 0,
        failed: (rawJobs.failed as number) ?? 0,
        disabled: (rawJobs.disabled as number) ?? 0,
        lastRun: (rawJobs.lastRun ?? rawJobs.last_run) as string | null ?? null,
        jobs: ((rawJobs.jobs as Record<string, unknown>[]) ?? []).map((j) => ({
          id: j.id as string,
          name: j.name as string,
          status: (j.status as JobStatusItem['status']) ?? 'healthy',
          lastRun: (j.lastRun ?? j.last_run) as string | null ?? null,
          lastStatus: (j.lastStatus ?? j.last_status) as JobStatusItem['lastStatus'] ?? null,
          schedule: (j.schedule as string) ?? '',
        })),
      };

      return {
        focus: (d.focus as BriefingFocusItem[]) ?? [],
        signals,
        hotNews,
        working,
        stocks,
        output,
        jobsSummary,
        jobs,
        todos,
        learning: (d.learning as string) ?? null,
        generatedAt: (d.generated_at as string) ?? null,
      };
    } catch {
      return {
        focus: [], signals: [], hotNews: [], working: [], stocks: [],
        output: { builds: [], content: [], files: [] },
        jobsSummary: { total: 0, healthy: 0, failed: 0, disabled: 0, lastRun: null, jobs: [] },
        jobs: [], todos: [], learning: null, generatedAt: null,
      };
    }
  },

  /**
   * Dismiss a focus item so it won't appear in future briefings.
   * Stored server-side with a 7-day TTL.
   */
  async dismissFocus(title: string): Promise<void> {
    await api.post('/system/briefing/dismiss', { title });
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
    return deepSnakeToCamel(response.data) as AuthHintResponse;
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
      const response = await api.get<Record<string, unknown>>('/system/engine-metrics');
      return deepSnakeToCamel(response.data) as EngineMetrics;
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
