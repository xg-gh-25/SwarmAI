/* eslint-disable react-refresh/only-export-components */
/**
 * Backend startup overlay component.
 *
 * Displays a splash screen while the FastAPI backend daemon initializes,
 * showing user-friendly progress steps and dismissing once the agent and
 * workspace are ready.  The overlay uses SVG status icons, a ~700ms
 * animation budget (100ms × 3 steps + 200ms delay + 200ms fade-out), and
 * a fast-startup shortcut that skips step-by-step animation when everything
 * is ready on the first poll.
 *
 * Key exports:
 * - ``BackendStartupOverlay`` — default export, the overlay React component
 * - ``checkReadiness``        — named export consumed by tests
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { listen, UnlistenFn } from '@tauri-apps/api/event';
// getApiBaseUrl: health checks; getBackendPort/initializeBackend: Tauri daemon port negotiation
import { getApiBaseUrl, getBackendPort, initializeBackend, isDesktop } from '../../services/tauri';
import { systemService, SystemStatus } from '../../services/system';
import logo from '../../assets/swarm-avatar.svg';

// ============================================================================
// Constants
// ============================================================================

const TIMING = {
  healthCheckTimeout: 3000,
  // Consecutive no_response polls before giving up. no_response = backend truly
  // unreachable (network error / SPA-fallback), NOT merely "still booting" — so
  // this is a genuine-failure cap, not a cold-start clock. An `alive` reply does
  // NOT count toward it (see pollHealth). Kept modest: on desktop the Rust probe
  // is the primary cold-start gate; this only fires when the backend never answers.
  maxNoResponse: 60,
  // Absolute ceiling for the whole readiness wait (O030 disaster-recovery backstop),
  // matching the Rust COLD_START_CEILING_SECS. A slow-but-alive backend keeps
  // waiting up to here instead of the old fixed 60s; only a genuine hang is bounded.
  readinessTimeout: 300000,
  pollInterval: 1000,
  stepAnimationDelay: 100,   // 100ms per step (was 150ms)
  fadeOutDelay: 200,          // 200ms delay before fade (was 500ms)
  fadeOutDuration: 200,       // 200ms fade-out (was 500ms)
  initialPollDelay: 500,
} as const;

// ============================================================================
// Types
// ============================================================================

type StartupStatus = 'starting' | 'connecting' | 'fetching_status' | 'waiting_for_ready' | 'connected' | 'error';

type InitStepStatus = 'pending' | 'in_progress' | 'success' | 'error';

interface InitStep {
  id: string;
  label: string;
  status: InitStepStatus;
  error?: string;
}

interface ReadinessCheckResult {
  agentReady: boolean;
  workspaceReady: boolean;
  allReady: boolean;
  error?: string;
}

// ============================================================================
// Reusable Components
// ============================================================================

const SPINNER_SIZES = {
  sm: 'h-3 w-3',
  md: 'h-4 w-4',
} as const;

interface SpinnerProps {
  size?: keyof typeof SPINNER_SIZES;
}

function Spinner({ size = 'md' }: SpinnerProps) {
  return (
    <svg
      className={`animate-spin ${SPINNER_SIZES[size]}`}
      style={{ color: 'var(--color-primary)' }}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

/**
 * SVG status icon component replacing text-character indicators.
 * Renders a filled green checkmark (success), animated spinner (in_progress),
 * filled red X (error), or open circle (pending).
 */
function StatusIcon({ status }: { status: InitStepStatus }) {
  if (status === 'in_progress') return <Spinner size="sm" />;
  if (status === 'success') return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="8" fill="var(--color-success, #22c55e)" />
      <path d="M5 8l2 2 4-4" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
  if (status === 'error') return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="8" fill="var(--color-error, #ef4444)" />
      <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="7" stroke="var(--color-text-muted)" strokeWidth="1.5" />
    </svg>
  );
}

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Check if both SwarmAgent and SwarmWorkspace are ready.
 * Returns a ReadinessCheckResult with individual and combined readiness flags.
 *
 * Dismissal gate: agentReady AND workspaceReady.
 * The `initialized` field from SystemStatusResponse is intentionally ignored
 * (it requires channel_gateway.running=True, which conflicts with deferred gateway).
 *
 * @param systemStatus - The current system status from the backend
 * @returns ReadinessCheckResult with agentReady, workspaceReady, and allReady flags
 */
export function checkReadiness(systemStatus: SystemStatus): ReadinessCheckResult {
  const agentReady = systemStatus.agent.ready === true;
  const workspaceReady = systemStatus.swarmWorkspace.ready === true;
  const allReady = agentReady && workspaceReady;

  let error: string | undefined;
  if (!agentReady && systemStatus.agent.error) {
    error = systemStatus.agent.error;
  } else if (!workspaceReady && systemStatus.swarmWorkspace.error) {
    error = systemStatus.swarmWorkspace.error;
  }

  return { agentReady, workspaceReady, allReady, error };
}

/** Get the log directory path — same for all platforms. */
function getLogPath(): string {
  return '~/.swarm-ai/logs/';
}

/**
 * Three-way health classification (pure — unit-tested).
 *
 * `ready`       — /health returned status=healthy; backend up and serving.
 * `alive`       — backend responded (any HTTP/JSON reply that isn't the SPA-fallback
 *                 HTML) but is not yet healthy; it is up and still booting → keep waiting.
 * `no_response` — no usable reply: a thrown network error (connection refused /
 *                 timeout), OR the Tauri asset-protocol SPA-fallback HTML (which
 *                 means the request never reached the real backend). Only this
 *                 counts toward giving up.
 *
 * Why this exists (run_e3dbc009): the old checkHealth collapsed everything except
 * `healthy` into a single failure signal, so a slow-but-alive backend on a new
 * user's first launch was indistinguishable from a dead one and was false-killed
 * at a fixed 60s cap. Distinguishing `alive` from `no_response` lets the overlay
 * keep waiting while the backend is genuinely booting.
 *
 * @param result - either the parsed response body (`{data}`) or a thrown error (`{error}`)
 */
export function classifyHealth(
  result: { data: unknown } | { error: unknown },
): 'ready' | 'alive' | 'no_response' {
  if ('error' in result) {
    return 'no_response';
  }
  const data = result.data;
  // SPA-fallback HTML string → request hit the Tauri asset protocol, not the
  // real backend (v1.9.0 bug class) → treat as no_response, not alive.
  if (typeof data === 'string' && data.includes('<!')) {
    return 'no_response';
  }
  const status = (data as { status?: unknown } | null)?.status;
  if (status === 'healthy') {
    return 'ready';
  }
  // Any other structured reply (e.g. {status:"initializing"}, or any JSON the
  // backend served while booting) proves the process is up → alive, keep waiting.
  return 'alive';
}

/**
 * #4a: absolute wall-clock ceiling for the health-check phase (pollHealth).
 *
 * pollHealth's only give-up was `noResponseStreak >= maxNoResponse`, which an
 * `alive` reply resets to 0 — so a backend flapping between `alive` and
 * `no_response` polls forever and never reaches the readiness phase (which owns
 * the readinessTimeout ceiling). On desktop the Rust COLD_START_CEILING is the
 * backstop; Hive/browser mode has none → infinite spinner. This helper gives
 * pollHealth the SAME absolute bound (readinessTimeout) so the total overlay
 * lifetime is bounded regardless of flapping — NOT a second independent timer.
 *
 * Uses `performance.now()` deltas (monotonic) at the call site; a null start
 * (before the first poll) never trips, so a slow Tauri cold start that hasn't
 * begun polling is never false-fatal'd.
 *
 * @param elapsedMs  performance.now() - firstPollTime, or null if polling hasn't started
 * @param ceilingMs  the absolute budget (TIMING.readinessTimeout)
 */
export function hasExceededStartupCeiling(
  elapsedMs: number | null,
  ceilingMs: number,
): boolean {
  if (elapsedMs === null) return false;
  return elapsedMs >= ceilingMs;
}

// ============================================================================
// Main Component
// ============================================================================

interface BackendStartupOverlayProps {
  onReady?: () => void;
}

export default function BackendStartupOverlay({ onReady }: BackendStartupOverlayProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<StartupStatus>('starting');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [isVisible, setIsVisible] = useState(true);
  const [isFadingOut, setIsFadingOut] = useState(false);
  const [initSteps, setInitSteps] = useState<InitStep[]>([]);
  const [visibleStepCount, setVisibleStepCount] = useState(0);
  const [appVersion, setAppVersion] = useState('');
  const startTimeRef = useRef<number | null>(null);
  const firstPollTimeRef = useRef<number | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [daemonProgress, setDaemonProgress] = useState<{ elapsed: number; total: number } | null>(null);

  const logPath = useMemo(() => getLogPath(), []);

  /** Retry handler — resets all state and restarts initialization. */
  const handleRetry = useCallback(() => {
    setStatus('starting');
    setErrorMessage('');
    setInitSteps([]);
    setVisibleStepCount(0);
    setIsFadingOut(false);
    setIsVisible(true);
    setAppVersion('');
    setDaemonProgress(null);
    startTimeRef.current = null;
    firstPollTimeRef.current = null;
    setRetryCount(prev => prev + 1);
  }, []);

  // Listen for daemon startup progress events from Rust backend
  useEffect(() => {
    if (!isDesktop()) return;
    let mounted = true;
    let unlisten: UnlistenFn | null = null;

    listen<{ attempt: number; maxAttempts: number; elapsedSecs: number; totalSecs: number }>(
      'backend-starting-progress',
      (event) => {
        if (mounted) setDaemonProgress({ elapsed: event.payload.elapsedSecs, total: event.payload.totalSecs });
      }
    ).then((fn) => {
      if (mounted) unlisten = fn;
      else fn(); // already unmounted — immediately unlisten
    });

    return () => { mounted = false; unlisten?.(); };
  }, [retryCount]);

  /** Map a ready/error pair to a step status. */
  const getStepStatus = useCallback((ready: boolean, error?: string): InitStepStatus => {
    if (ready) return 'success';
    if (error) return 'error';
    return 'in_progress';
  }, []);

  /**
   * Build exactly 3 flat initialization steps from system status.
   * No channel gateway step. No children (skills count, MCP count, workspace path).
   */
  const buildInitSteps = useCallback((systemStatus: SystemStatus): InitStep[] => [
    {
      id: 'database',
      label: 'Loading your data',
      status: getStepStatus(systemStatus.database.healthy, systemStatus.database.error),
      error: systemStatus.database.error,
    },
    {
      id: 'agent',
      label: 'Preparing your agent',
      status: getStepStatus(systemStatus.agent.ready, systemStatus.agent.error),
      error: systemStatus.agent.error,
    },
    {
      id: 'workspace',
      label: 'Setting up workspace',
      status: getStepStatus(systemStatus.swarmWorkspace.ready, systemStatus.swarmWorkspace.error),
      error: systemStatus.swarmWorkspace.error,
    },
  ], [getStepStatus]);

  // Animate steps appearing sequentially (flat — no children to count)
  useEffect(() => {
    if (initSteps.length === 0) return;
    if (visibleStepCount >= initSteps.length) return;

    const timer = setTimeout(() => {
      setVisibleStepCount(prev => prev + 1);
    }, TIMING.stepAnimationDelay);

    return () => clearTimeout(timer);
  }, [initSteps, visibleStepCount]);

  // Fade-out after all steps visible AND status is connected
  useEffect(() => {
    if (status !== 'connected') return;
    if (initSteps.length === 0) return;
    if (visibleStepCount < initSteps.length) return;

    const timer = setTimeout(() => {
      setIsFadingOut(true);
      setTimeout(() => {
        // Log overlay timing
        if (firstPollTimeRef.current) {
          console.log(
            `[Overlay] Health poll to dismissal: ${(performance.now() - firstPollTimeRef.current).toFixed(0)}ms`
          );
        }
        setIsVisible(false);
        onReady?.();
      }, TIMING.fadeOutDuration);
    }, TIMING.fadeOutDelay);

    return () => clearTimeout(timer);
  }, [status, initSteps, visibleStepCount, onReady]);

  const checkHealth = useCallback(async (): Promise<{
    outcome: 'ready' | 'alive' | 'no_response';
    version?: string;
  }> => {
    try {
      const apiBase = getApiBaseUrl();
      console.log(`[Health Check] Checking health at ${apiBase || '(same-origin)'}/health...`);
      const response = await axios.get(`${apiBase}/health`, {
        timeout: TIMING.healthCheckTimeout,
      });
      const outcome = classifyHealth({ data: response.data });
      if (outcome === 'no_response' && typeof response.data === 'string') {
        // SPA-fallback HTML — request hit the Tauri asset protocol, not the real
        // backend. This is the v1.9.0 bug class (isDesktop()=false → same-origin → HTML).
        console.error(`[Health Check] FATAL: got HTML instead of JSON — API base URL is wrong. isDesktop()=${isDesktop()}, url=${apiBase || '(same-origin)'}/health`);
      }
      console.log(`[Health Check] Response (${outcome}):`, response.data);
      return {
        outcome,
        version: (response.data as { version?: string } | null)?.version,
      };
    } catch (error) {
      console.error(`[Health Check] Failed:`, error);
      return { outcome: classifyHealth({ error }) };
    }
  }, []);

  const fetchSystemStatus = useCallback(async (): Promise<SystemStatus | null> => {
    try {
      console.log('[System Status] Fetching system status...');
      const systemStatus = await systemService.getStatus();
      console.log('[System Status] Response:', systemStatus);
      return systemStatus;
    } catch (error) {
      console.warn('[System Status] Failed to fetch (graceful degradation):', error);
      return null;
    }
  }, []);

  useEffect(() => {
    let noResponseStreak = 0;
    let timeoutId: ReturnType<typeof setTimeout>;
    let mounted = true;

    // Poll for readiness after initial status fetch
    const pollReadiness = async () => {
      if (!mounted) return;

      const currentElapsed = startTimeRef.current !== null ? Date.now() - startTimeRef.current : 0;
      if (currentElapsed >= TIMING.readinessTimeout) {
        console.log('[Readiness] Timeout reached after', currentElapsed, 'ms');
        setStatus('error');
        setErrorMessage(t('startup.initializationTimeout', { seconds: Math.round(currentElapsed / 1000) }));
        return;
      }

      console.log('[Readiness] Polling system status...');
      const systemStatus = await fetchSystemStatus();
      if (!mounted) return;

      if (systemStatus) {
        const readiness = checkReadiness(systemStatus);
        console.log('[Readiness] Check result:', readiness);

        const steps = buildInitSteps(systemStatus);
        setInitSteps(steps);

        if (readiness.allReady) {
          console.log('[Readiness] All components ready, transitioning to connected');
          setStatus('connected');
        } else {
          console.log('[Readiness] Not all ready, continuing to poll...');
          timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
        }
      } else {
        console.warn('[Readiness] Status fetch failed, continuing to poll...');
        timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
      }
    };

    const pollHealth = async () => {
      if (!mounted) return;

      if (!firstPollTimeRef.current) {
        firstPollTimeRef.current = performance.now();
      }

      // #4a: absolute wall-clock ceiling for the health phase. Without this, a
      // backend flapping between `alive` and `no_response` resets the
      // noResponseStreak every `alive` and polls forever (Hive/browser mode has
      // no Rust COLD_START_CEILING backstop). Uses the monotonic performance.now
      // delta from the first poll. NOTE: this bounds the HEALTH phase alone; the
      // readiness phase has its own separate startTimeRef-based readinessTimeout
      // (:381), so the two phases are independent windows — worst-case total
      // overlay lifetime is ~2×readinessTimeout. That is acceptable (each phase
      // is generously bounded); the point of THIS ceiling is that the health
      // phase, previously unbounded under alive/no_response flapping, now has ANY
      // ceiling at all.
      const healthElapsed = firstPollTimeRef.current !== null
        ? performance.now() - firstPollTimeRef.current
        : null;
      if (hasExceededStartupCeiling(healthElapsed, TIMING.readinessTimeout)) {
        console.error('[Health Check] Startup ceiling reached after', healthElapsed, 'ms without readiness');
        setStatus('error');
        setErrorMessage(t('startup.initializationTimeout', { seconds: Math.round((healthElapsed ?? 0) / 1000) }));
        return;
      }

      const healthResult = await checkHealth();
      if (!mounted) return;

      if (healthResult.outcome === 'ready') {
        // Capture app version from health response
        if (healthResult.version) {
          setAppVersion(healthResult.version);
        }

        setStatus('fetching_status');
        const systemStatus = await fetchSystemStatus();
        if (!mounted) return;

        if (systemStatus) {
          const steps = buildInitSteps(systemStatus);
          setInitSteps(steps);

          const readiness = checkReadiness(systemStatus);
          console.log('[Startup] Initial readiness check:', readiness);

          if (readiness.allReady) {
            // Fast startup shortcut: show all 3 steps simultaneously, skip animation
            setVisibleStepCount(steps.length);
            setStatus('connected');
          } else {
            console.log('[Startup] Not all ready, transitioning to waiting_for_ready');
            setStatus('waiting_for_ready');
            startTimeRef.current = Date.now();
            timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
          }
        } else {
          // Graceful degradation: proceed without status display
          setStatus('connected');
          setIsFadingOut(true);
          setTimeout(() => {
            if (mounted) {
              setIsVisible(false);
              onReady?.();
            }
          }, TIMING.fadeOutDuration);
        }
      } else if (healthResult.outcome === 'alive') {
        // Backend responded but is still booting — it is ALIVE, so keep waiting
        // WITHOUT counting toward the give-up cap. This is the false-fatal fix:
        // a slow-but-alive backend must never be declared "failed to start".
        // Reset the no-response streak: any live reply means it's not dead.
        noResponseStreak = 0;
        timeoutId = setTimeout(pollHealth, TIMING.pollInterval);
      } else {
        // no_response — backend genuinely unreachable (network error / SPA-fallback).
        noResponseStreak++;
        if (noResponseStreak >= TIMING.maxNoResponse) {
          const apiBase = getApiBaseUrl();
          console.error(`[Health Check] Exhausted ${noResponseStreak} consecutive no-response attempts. apiBase=${apiBase || '(same-origin)'}, isDesktop=${isDesktop()}, port=${getBackendPort()}`);
          setStatus('error');
          setErrorMessage(`Backend service is not responding (${apiBase || 'same-origin'}, ${noResponseStreak} attempts with no reply). Check logs at ~/.swarm-ai/logs/`);
        } else {
          timeoutId = setTimeout(pollHealth, TIMING.pollInterval);
        }
      }
    };

    const startHealthPolling = async () => {
      try {
        if (isDesktop()) {
          // Desktop: connect to daemon via Tauri start_backend()
          console.log('[Startup] Calling initializeBackend()...');
          const port = await initializeBackend();
          console.log(`[Startup] initializeBackend() returned port: ${port}`);
          console.log(`[Startup] getBackendPort() returns: ${getBackendPort()}`);
        } else {
          // Hive/browser: backend is already running, skip Tauri init
          console.log('[Startup] Hive mode — backend managed externally, skipping Tauri init');
        }

        if (!mounted) return;

        // Clear daemon progress — backend is confirmed up, now checking readiness
        setDaemonProgress(null);
        setStatus('connecting');
        console.log('[Startup] Starting health polling...');
        timeoutId = setTimeout(pollHealth, TIMING.initialPollDelay);
      } catch (error) {
        console.error('[Startup] Failed to initialize backend:', error);
        if (mounted) {
          setStatus('error');
          // Tauri invoke errors may be objects — ensure we display a string
          const msg = error instanceof Error ? error.message : String(error);
          setErrorMessage(`Failed to initialize backend: ${msg}`);
        }
      }
    };

    startHealthPolling();

    return () => {
      mounted = false;
      clearTimeout(timeoutId);
    };
  }, [checkHealth, fetchSystemStatus, buildInitSteps, onReady, t, retryCount]);

  /** Render a single flat init step. */
  const renderInitStep = (step: InitStep, index: number) => {
    if (index >= visibleStepCount) return null;

    return (
      <div
        key={step.id}
        className="flex items-center gap-2 animate-fade-in"
        style={{
          fontSize: '14px',
          opacity: 1,
          transition: 'opacity 0.2s ease-in',
        }}
      >
        <StatusIcon status={step.status} />
        <span className="text-[var(--color-text)]">{step.label}</span>
        {step.error && (
          <span className="text-[var(--color-error,#ef4444)] text-xs ml-2">
            ({step.error})
          </span>
        )}
      </div>
    );
  };

  /** Render all init steps (flat list, no children). */
  const renderInitSteps = () =>
    initSteps.map((step, index) => renderInitStep(step, index));

  if (!isVisible) {
    return null;
  }

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)] transition-opacity duration-200 ${
        isFadingOut ? 'opacity-0' : 'opacity-100'
      }`}
    >
      <div className="flex flex-col items-center gap-6 max-w-md px-8">
        {/* Logo */}
        <div className="w-24 h-24 rounded-2xl overflow-hidden">
          <img src={logo} alt="SwarmAI" className="w-full h-full object-contain" />
        </div>

        {/* App Name + Version */}
        <div className="flex flex-col items-center gap-1">
          <h1 className="text-3xl font-bold text-[var(--color-text)]">SwarmAI</h1>
          {appVersion && (
            <span className="text-sm text-[var(--color-text-muted)]">v{appVersion}</span>
          )}
        </div>

        {/* Connecting state — show spinner with progress */}
        {(status === 'starting' || status === 'connecting') && (
          <>
            <div className="flex items-center gap-3">
              <Spinner size="md" />
              <span className="text-[var(--color-text-muted)]">
                {daemonProgress
                  ? `Starting backend... ${daemonProgress.elapsed}s`
                  : t('startup.connectingToBackend')
                }
              </span>
            </div>
            <div className="w-64 h-1 bg-[var(--color-border)] rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full transition-all duration-1000 ease-out"
                style={{
                  width: daemonProgress
                    ? `${Math.max(10, Math.min(95, (daemonProgress.elapsed / daemonProgress.total) * 100))}%`
                    : '15%',
                }}
              />
            </div>
          </>
        )}

        {/* Fetching status state */}
        {status === 'fetching_status' && (
          <div className="flex items-center gap-3">
            <Spinner size="md" />
            <span className="text-[var(--color-text-muted)]">
              {t('startup.connectingToBackend')}
            </span>
          </div>
        )}

        {/* Waiting for ready — show steps with polling indicator */}
        {status === 'waiting_for_ready' && initSteps.length > 0 && (
          <div className="flex flex-col gap-2 w-full max-w-sm">
            {renderInitSteps()}
            <div className="flex items-center gap-2 mt-2" style={{ fontSize: '12px' }}>
              <Spinner size="sm" />
              <span className="text-[var(--color-text-muted)]">
                {t('startup.waitingForReady')}
              </span>
            </div>
          </div>
        )}

        {/* Connected — show steps (fade-out handled by effect) */}
        {status === 'connected' && initSteps.length > 0 && (
          <div className="flex flex-col gap-2 w-full max-w-sm">
            {renderInitSteps()}
          </div>
        )}

        {/* Error state */}
        {status === 'error' && (
          <div className="flex flex-col items-center gap-4">
            <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center">
              <span className="material-symbols-outlined text-2xl text-red-400">
                error
              </span>
            </div>
            <div className="text-center">
              <p className="text-red-400 font-medium mb-2">Failed to start</p>
              <p className="text-[var(--color-text-muted)] text-sm">{errorMessage}</p>
            </div>
            <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg p-4 mt-2">
              <p className="text-sm text-[var(--color-text-muted)] mb-2">Please check the logs at:</p>
              <code className="text-xs text-primary bg-[var(--color-hover)] px-2 py-1 rounded block">
                {logPath}
              </code>
            </div>
            <button
              onClick={handleRetry}
              className="mt-4 px-6 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors flex items-center gap-2"
            >
              <span className="material-symbols-outlined text-xl">refresh</span>
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
