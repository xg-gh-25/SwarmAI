import api from './api';
import type { ToastSeverity } from '../types';

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

// The Welcome Screen consumes only focus + working + learning. Other briefing
// fields (hotNews / stocks / signals / output / jobs / jobsSummary / todos)
// were removed 2026-08-05: no UI reads them (Jobs&Runs uses useJobsRuns; ToDos
// use the /todos endpoint).
export interface SessionBriefing {
  focus: BriefingFocusItem[];
  working: WorkingItem[];
  learning: string | null;
}

const STATUS_TIMEOUT_MS = 5000;

/** Idle (between-events) stall timeout for the restore SSE stream. Matches the
 * chat stream's STALL_TIMEOUT_MS (chat.ts:47). This is a HANG-guard on IDLE
 * time, reset on every event — NOT a cap on total restore duration (O030). */
export const RESTORE_STALL_TIMEOUT_MS = 90_000;

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
  deploymentContext: 'internal' | 'external';
  // "high" when a positive internal/SSO signal was detected; "low" when we only
  // DEFAULTED to external (no signal). Frontend elevates the context toggle on "low".
  detectionConfidence?: 'high' | 'low';
  suggestedMethod: 'ada' | 'sso' | 'apikey' | 'iam_role' | 'bedrock_api_key';
  adaDetails?: {
    accountId?: string;
    roleName?: string;
    region?: string;
    configured?: boolean;
    keyPrefix?: string;
  };
  iamDetails?: {
    accountId?: string;
    region?: string;
    roleName?: string;
    instanceId?: string;
  };
  awsProfiles?: string[];
  runMode?: 'daemon' | 'hive';
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
   * Returns focus suggestions, working items, and a learning insight from the
   * proactive intelligence engine. The backend payload may carry more fields
   * (hotNews/jobs/todos/etc.) but the Welcome Screen consumes only these three.
   */
  async getBriefing(): Promise<SessionBriefing> {
    try {
      const response = await api.get<Record<string, unknown>>('/system/briefing');
      const d = response.data;

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

      return {
        focus: (d.focus as BriefingFocusItem[]) ?? [],
        working,
        learning: (d.learning as string) ?? null,
      };
    } catch {
      return { focus: [], working: [], learning: null };
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
  async verifyAuth(override?: Record<string, unknown>): Promise<VerifyAuthResponse> {
    // `override` lets the caller verify a NOT-YET-PERSISTED config (onboarding
    // wizard). Omitted (Settings tab) → backend falls back to stored config.
    const response = await api.post<Record<string, unknown>>('/system/verify-auth', override);
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
   * Persist the user's Anthropic API key (Anthropic-direct auth). Stored in the
   * daemon's durable secret store — never echoed back, no relaunch needed.
   */
  async persistApiKey(apiKey: string): Promise<void> {
    await api.post('/system/anthropic-api-key', { api_key: apiKey });
  },

  /**
   * Persist the user's Bedrock bearer token (AWS_BEARER_TOKEN_BEDROCK). Stored
   * in the daemon's durable secret store — never echoed back, no relaunch
   * needed. Injected as AWS_BEARER_TOKEN_BEDROCK at the next spawn.
   */
  async persistBearerToken(bearerToken: string): Promise<void> {
    await api.post('/system/bedrock-api-key', { bearer_token: bearerToken });
  },

  /**
   * Persist the chosen auth method (+ deployment context) so credential-error
   * remediation is method-aware.
   */
  async setAuthMethod(method: string, deploymentContext?: string): Promise<void> {
    await api.post('/system/auth-method', { method, deployment_context: deploymentContext });
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
  // ── Workspace Backup & Sync ──

  /** Get backup status: last_backup, repo_url, schedule, enabled. */
  async getBackupStatus(): Promise<BackupStatus> {
    const response = await api.get<Record<string, unknown>>('/system/backup/status');
    return deepSnakeToCamel(response.data) as BackupStatus;
  },

  /** Trigger immediate backup. Returns tables_exported, commit SHA, push status. */
  async runBackup(): Promise<BackupResult> {
    const response = await api.post<Record<string, unknown>>('/system/backup');
    return deepSnakeToCamel(response.data) as BackupResult;
  },

  /** Update backup config: repo_url, token, schedule. */
  async updateBackupConfig(config: { repoUrl?: string; token?: string; schedule?: string }): Promise<void> {
    await api.put('/system/backup/config', {
      repo_url: config.repoUrl,
      token: config.token,
      schedule: config.schedule,
    });
  },

  /** Restore from backup repo. Returns async iterator of SSE progress events.
   *
   * Stall-guard: the read loop is bounded by an IDLE timeout
   * (RESTORE_STALL_TIMEOUT_MS) that RESETS on every event — a legitimately slow
   * but progressing restore (git clone + db import can take minutes) is never
   * killed; only a stream that goes silent for the full idle window is. On idle
   * timeout we abort the underlying fetch (releasing the reader) and yield a
   * terminal `.error` event — the field StepRestore keys on (OnboardingPage.tsx
   * :360 break + :437 escape UI). This is a hang-guard, NOT a total-duration cap
   * (O030: never guillotine slow-but-progressing work). */
  async *restoreBackup(repoUrl: string, token?: string, externalSignal?: AbortSignal): AsyncGenerator<RestoreEvent> {
    const baseUrl = api.defaults.baseURL || '';
    const controller = new AbortController();
    // Link an optional caller-owned signal (e.g. component unmount) to our
    // controller. Aborting it errors the fetch → the in-flight reader.read()
    // rejects → the generator exits promptly. This is the ONLY way to abort a
    // generator parked at `await reader.read()`: .return() alone queues behind
    // the pending await and never runs until the read settles.
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', () => controller.abort(), { once: true });
    }
    const response = await fetch(`${baseUrl}/system/backup/restore`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: repoUrl, token }),
      signal: controller.signal,
    });

    if (!response.ok || !response.body) {
      // Include the `.error` field (not just `detail`/`stage`) so the consumer's
      // `if (event.error) break` + the escape UI both trigger on HTTP failure too.
      yield { stage: 'error', progress: 0, detail: `HTTP ${response.status}`, error: `Restore failed: HTTP ${response.status}` };
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastProgress = 0;

    // IDLE stall-guard: a promise that rejects when no event arrives within the
    // idle window. clearTimeout-before-reschedule per read (chat.ts:240-249
    // pattern) so a fast stream doesn't accumulate abandoned timers.
    let idleTimerId: ReturnType<typeof setTimeout> | null = null;
    let stalled = false;
    const clearIdle = () => { if (idleTimerId !== null) { clearTimeout(idleTimerId); idleTimerId = null; } };

    try {
      while (true) {
        // Race the real read against the idle timer. On idle: controller.abort()
        // fires (rejecting reader.read()), stalled=true is latched, and we emit
        // the terminal error event below. `settled` guards the late-timer race:
        // if the read WINS the race, the timer callback must NOT still latch
        // stalled/abort (which would kill a healthy stream on the next read).
        let readResult: ReadableStreamReadResult<Uint8Array>;
        try {
          readResult = await new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
            let settled = false;
            clearIdle();
            idleTimerId = setTimeout(() => {
              if (settled) return;          // read already won — do not abort a healthy stream
              settled = true;
              stalled = true;
              controller.abort();
              reject(new Error('restore-idle-timeout'));
            }, RESTORE_STALL_TIMEOUT_MS);
            reader.read().then(
              (v) => { if (settled) return; settled = true; clearIdle(); resolve(v); },
              (e) => { if (settled) return; settled = true; clearIdle(); reject(e); },
            );
          });
        } catch {
          // Either the idle timer fired, or the abort rejected the read.
          if (stalled) {
            yield {
              stage: 'error',
              progress: lastProgress,
              error: `Restore stalled — no progress for ${RESTORE_STALL_TIMEOUT_MS / 1000}s. The backup server may be unreachable.`,
            };
          }
          return;
        }

        const { done, value } = readResult;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // Bound buffer growth — a malformed/hostile stream with no line breaks
        // must not grow the buffer without limit (renderer OOM guard). A single
        // SSE line far larger than this is not a legitimate restore event.
        if (buffer.length > 1_000_000) {
          stalled = true;
          yield { stage: 'error', progress: lastProgress, error: 'Restore stream malformed (oversized frame). Aborting.' };
          return;
        }
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const evt = JSON.parse(line.slice(6)) as RestoreEvent;
              if (typeof evt.progress === 'number') lastProgress = evt.progress;
              yield evt;
            } catch { /* skip malformed */ }
          }
        }
      }
    } finally {
      // Release the stream on ANY exit (done / stall / consumer `break` /
      // component unmount closing the generator) — no zombie fetch.
      clearIdle();
      controller.abort();
    }
  },
};

// ============== Backup Types ==============

export interface BackupStatus {
  lastBackup: string | null;
  repoUrl: string | null;
  schedule: string;
  enabled: boolean;
}

export interface BackupResult {
  status: string;
  tablesExported: number;
  commit: string | null;
  pushStatus: string;
  /** Set by the backend only when pushStatus === 'refused' — the fail-closed
   *  destination-guard reason (no_configured_destination | destination_mismatch | no_remote). */
  refuseReason?: string;
}

/**
 * Pure decision function: map a BackupResult to the toast to show.
 *
 * Extracted from BackupTab so every push_status is unit-testable (the inline
 * if/else only handled ok/failed and masked 'refused'/'skipped_disabled' as
 * "No changes to backup." — a fail-closed backup refusal was invisible).
 *
 * A 'refused' result is a fail-closed destination-guard refusal: surface it as a
 * warning with an actionable, reason-specific message (destination_mismatch is NOT
 * fixed by "configure a target", so it gets its own message).
 */
export function backupToastFor(result: BackupResult): { severity: ToastSeverity; message: string } {
  switch (result.pushStatus) {
    case 'ok':
      return {
        severity: 'success',
        message: `Backup complete — ${result.tablesExported} tables, commit ${result.commit}`,
      };
    case 'failed':
      return { severity: 'warning', message: 'Backup committed locally but push failed. Check network.' };
    case 'refused': {
      const map: Record<string, string> = {
        no_configured_destination: 'Backup not configured. Set up a backup repository in Settings.',
        destination_mismatch:
          'Backup refused — remote mismatch: git origin differs from the configured backup target. Reconfigure in Settings.',

        no_remote: 'Backup refused — no backup remote is configured. Set one up in Settings.',
      };
      const message =
        (result.refuseReason && map[result.refuseReason]) ||
        'Backup refused: destination not verified. Check your backup settings.';
      return { severity: 'warning', message };
    }
    case 'skipped_disabled':
    case 'skipped':
      return { severity: 'info', message: 'Backup is disabled. Enable it in Settings to run backups.' };
    default:
      // no_changes and any unknown status → benign no-op
      return { severity: 'info', message: 'No changes to backup.' };
  }
}

export interface RestoreEvent {
  stage: string;
  progress: number;
  detail?: string;
  error?: string;
  tablesImported?: number;
  messagesCount?: number;
  sessionsCount?: number;
  todosCount?: number;
}

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
