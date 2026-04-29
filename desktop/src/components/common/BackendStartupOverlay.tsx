/**
 * Backend startup overlay component.
 *
 * Displays a splash screen while the FastAPI backend sidecar initializes,
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
// getApiBaseUrl: health checks; getBackendPort/initializeBackend: Tauri sidecar port negotiation
import { getApiBaseUrl, getBackendPort, initializeBackend, isDesktop } from '../../services/tauri';
import { systemService, SystemStatus } from '../../services/system';
import logo from '../../assets/swarm-avatar.svg';

// ============================================================================
// Constants
// ============================================================================

const TIMING = {
  healthCheckTimeout: 3000,
  maxHealthAttempts: 60,
  readinessTimeout: 60000,
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
    startTimeRef.current = null;
    firstPollTimeRef.current = null;
    setRetryCount(prev => prev + 1);
  }, []);

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

  const checkHealth = useCallback(async (): Promise<{ healthy: boolean; version?: string }> => {
    try {
      const apiBase = getApiBaseUrl();
      console.log(`[Health Check] Checking health at ${apiBase || '(same-origin)'}/health...`);
      const response = await axios.get(`${apiBase}/health`, {
        timeout: TIMING.healthCheckTimeout,
      });
      // Detect SPA fallback: if response is a string containing HTML, the
      // request hit the Tauri asset protocol instead of the real backend.
      // This is the v1.9.0 bug class (isDesktop()=false → same-origin → HTML).
      if (typeof response.data === 'string' && response.data.includes('<!')) {
        console.error(`[Health Check] FATAL: got HTML instead of JSON — API base URL is wrong. isDesktop()=${isDesktop()}, url=${apiBase || '(same-origin)'}/health`);
        return { healthy: false };
      }
      console.log(`[Health Check] Response:`, response.data);
      return {
        healthy: response.data?.status === 'healthy',
        version: response.data?.version as string | undefined,
      };
    } catch (error) {
      console.error(`[Health Check] Failed:`, error);
      return { healthy: false };
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
    let healthAttempts = 0;
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

      const healthResult = await checkHealth();
      if (!mounted) return;

      if (healthResult.healthy) {
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
      } else {
        healthAttempts++;
        if (healthAttempts >= TIMING.maxHealthAttempts) {
          const apiBase = getApiBaseUrl();
          console.error(`[Health Check] Exhausted ${healthAttempts} attempts. apiBase=${apiBase || '(same-origin)'}, isDesktop=${isDesktop()}, port=${getBackendPort()}`);
          setStatus('error');
          setErrorMessage(`Backend service failed to start within 60 seconds (${apiBase || 'same-origin'}, ${healthAttempts} attempts)`);
        } else {
          timeoutId = setTimeout(pollHealth, TIMING.pollInterval);
        }
      }
    };

    const startHealthPolling = async () => {
      try {
        if (isDesktop()) {
          // Desktop: negotiate port with Tauri sidecar/daemon
          console.log('[Startup] Calling initializeBackend()...');
          const port = await initializeBackend();
          console.log(`[Startup] initializeBackend() returned port: ${port}`);
          console.log(`[Startup] getBackendPort() returns: ${getBackendPort()}`);
        } else {
          // Hive/browser: backend is already running, skip Tauri init
          console.log('[Startup] Hive mode — backend managed externally, skipping Tauri init');
        }

        if (!mounted) return;

        setStatus('connecting');
        console.log('[Startup] Starting health polling...');
        timeoutId = setTimeout(pollHealth, TIMING.initialPollDelay);
      } catch (error) {
        console.error('[Startup] Failed to initialize backend:', error);
        if (mounted) {
          setStatus('error');
          setErrorMessage(`Failed to initialize backend: ${error}`);
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

        {/* Connecting state — show spinner */}
        {(status === 'starting' || status === 'connecting') && (
          <>
            <div className="flex items-center gap-3">
              <Spinner size="md" />
              <span className="text-[var(--color-text-muted)]">
                {t('startup.connectingToBackend')}
              </span>
            </div>
            <div className="w-64 h-1 bg-[var(--color-border)] rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full animate-pulse" style={{ width: '60%' }} />
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
