import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { getBackendPort, initializeBackend } from '../../services/tauri';
import { systemService, SystemStatus } from '../../services/system';
import logo from '../../assets/logo.png';

// ============================================================================
// Constants
// ============================================================================

const TIMING = {
  healthCheckTimeout: 3000,
  maxHealthAttempts: 60,
  readinessTimeout: 60000,
  pollInterval: 1000,
  stepAnimationDelay: 150,
  fadeOutDelay: 500,      // Delay before starting fade-out (after all steps visible)
  fadeOutDuration: 500,   // Duration of fade-out animation (matches CSS duration-500)
  initialPollDelay: 500,
} as const;

// ============================================================================
// Types
// ============================================================================

type StartupStatus = 'starting' | 'connecting' | 'fetching_status' | 'waiting_for_ready' | 'connected' | 'error';

type InitStepStatus = 'pending' | 'in_progress' | 'success' | 'error';

// ============================================================================
// Status Display Mappings
// ============================================================================

const STATUS_ICONS = {
  success: '✓',
  error: '✗',
  pending: '○',
  in_progress: null, // Uses spinner component instead
} as const satisfies Record<InitStepStatus, string | null>;

const STATUS_COLORS = {
  success: 'var(--color-success, #22c55e)',
  error: 'var(--color-error, #ef4444)',
  pending: 'var(--color-text-muted)',
  in_progress: 'var(--color-primary)',
} as const satisfies Record<InitStepStatus, string>;

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
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

interface InitStep {
  id: string;
  labelKey: string;
  status: InitStepStatus;
  error?: string;
  interpolation?: Record<string, string | number>;
  children?: InitStep[];
}

interface ReadinessCheckResult {
  agentReady: boolean;
  workspaceReady: boolean;
  allReady: boolean;
  error?: string;
}

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Check if both SwarmAgent and SwarmWorkspace are ready.
 * Returns a ReadinessCheckResult with individual and combined readiness flags.
 * 
 * @param systemStatus - The current system status from the backend
 * @returns ReadinessCheckResult with agentReady, workspaceReady, and allReady flags
 */
export function checkReadiness(systemStatus: SystemStatus): ReadinessCheckResult {
  const agentReady = systemStatus.agent.ready === true;
  const workspaceReady = systemStatus.swarmWorkspace.ready === true;
  const allReady = agentReady && workspaceReady;

  // Collect any errors from components that aren't ready
  let error: string | undefined;
  if (!agentReady && systemStatus.agent.error) {
    error = systemStatus.agent.error;
  } else if (!workspaceReady && systemStatus.swarmWorkspace.error) {
    error = systemStatus.swarmWorkspace.error;
  }

  return {
    agentReady,
    workspaceReady,
    allReady,
    error,
  };
}

// Get the log directory path - same for all platforms
function getLogPath(): string {
  return '~/.swarm-ai/logs/';
}

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
  // Ref for internal timeout tracking (avoids stale closure issues)
  const startTimeRef = useRef<number | null>(null);
  // Retry trigger to restart initialization (Requirements 7.3, 7.4)
  const [retryCount, setRetryCount] = useState(0);

  // Get platform-specific log path
  const logPath = useMemo(() => getLogPath(), []);

  /**
   * Retry handler that resets all state and restarts initialization.
   * Implements Requirements 7.3 (restart from beginning) and 7.4 (reset timeout counter).
   */
  const handleRetry = useCallback(() => {
    // Reset all state (Requirements 7.3)
    setStatus('starting');
    setErrorMessage('');
    setInitSteps([]);
    setVisibleStepCount(0);
    setIsFadingOut(false);
    setIsVisible(true);
    
    // Reset timeout counter (Requirements 7.4)
    startTimeRef.current = null;
    
    // Increment retry count to trigger useEffect re-run
    setRetryCount(prev => prev + 1);
  }, []);

  /**
   * Determine the status of an initialization step based on readiness and error state.
   * - 'success' when the component is ready
   * - 'error' when the component has an error
   * - 'in_progress' when the component is not ready but has no error
   */
  const getStepStatus = useCallback((ready: boolean, error?: string): InitStepStatus => {
    if (ready) return 'success';
    if (error) return 'error';
    return 'in_progress';
  }, []);

  // Build initialization steps from system status
  const buildInitSteps = useCallback((systemStatus: SystemStatus): InitStep[] => {
    const steps: InitStep[] = [];

    // Database step
    steps.push({
      id: 'database',
      labelKey: 'startup.databaseInitialized',
      status: getStepStatus(systemStatus.database.healthy, systemStatus.database.error),
      error: systemStatus.database.error,
    });

    // SwarmAgent step with children
    const agentStatus = getStepStatus(systemStatus.agent.ready, systemStatus.agent.error);
    const agentChildren: InitStep[] = [
      {
        id: 'skills',
        labelKey: 'startup.systemSkillsBound',
        status: systemStatus.agent.ready ? 'success' : 'in_progress',
        interpolation: { count: systemStatus.agent.skillsCount },
      },
      {
        id: 'mcpServers',
        labelKey: 'startup.systemMcpServersBound',
        status: systemStatus.agent.ready ? 'success' : 'in_progress',
        interpolation: { count: systemStatus.agent.mcpServersCount },
      },
    ];

    steps.push({
      id: 'agent',
      labelKey: 'startup.swarmAgentReady',
      status: agentStatus,
      error: systemStatus.agent.error,
      children: agentChildren,
    });

    // Channel gateway step (no error field available, so only success or in_progress)
    steps.push({
      id: 'channelGateway',
      labelKey: 'startup.channelGatewayStarted',
      status: systemStatus.channelGateway.running ? 'success' : 'in_progress',
    });

    // Swarm Workspace step with path child
    const workspaceStatus = getStepStatus(systemStatus.swarmWorkspace.ready, systemStatus.swarmWorkspace.error);
    const workspaceChildren: InitStep[] = [];
    if (systemStatus.swarmWorkspace.path) {
      workspaceChildren.push({
        id: 'workspacePath',
        labelKey: 'startup.swarmWorkspacePath',
        status: systemStatus.swarmWorkspace.ready ? 'success' : 'in_progress',
        interpolation: { path: systemStatus.swarmWorkspace.path },
      });
    }

    steps.push({
      id: 'swarmWorkspace',
      labelKey: 'startup.swarmWorkspaceInitialized',
      status: workspaceStatus,
      error: systemStatus.swarmWorkspace.error,
      children: workspaceChildren.length > 0 ? workspaceChildren : undefined,
    });

    return steps;
  }, [getStepStatus]);

  // Count total visible items (including children)
  const getTotalStepCount = useCallback((steps: InitStep[]): number => {
    let count = 0;
    for (const step of steps) {
      count++;
      if (step.children) {
        count += step.children.length;
      }
    }
    return count;
  }, []);

  // Animate steps appearing sequentially
  useEffect(() => {
    if (initSteps.length === 0) return;

    const totalSteps = getTotalStepCount(initSteps);
    if (visibleStepCount >= totalSteps) return;

    const timer = setTimeout(() => {
      setVisibleStepCount((prev) => prev + 1);
    }, TIMING.stepAnimationDelay);

    return () => clearTimeout(timer);
  }, [initSteps, visibleStepCount, getTotalStepCount]);

  // Proceed to fade out after all steps are visible AND status is connected
  // Requirements 5.1, 5.2, 5.3: Fade-out only starts after all initialization complete
  useEffect(() => {
    // Only start fade-out when status is 'connected' (both agent and workspace ready)
    if (status !== 'connected') return;
    if (initSteps.length === 0) return;

    const totalSteps = getTotalStepCount(initSteps);
    if (visibleStepCount < totalSteps) return;

    // All steps visible AND connected, wait 500ms then start fade-out (Requirement 5.3)
    const timer = setTimeout(() => {
      setIsFadingOut(true);
      // Wait for fade-out animation to complete before calling onReady (Requirement 5.5)
      // This ensures Main_Chat_Window is fully rendered and ready for interaction when visible
      setTimeout(() => {
        setIsVisible(false);
        onReady?.();
      }, TIMING.fadeOutDuration);
    }, TIMING.fadeOutDelay);

    return () => clearTimeout(timer);
  }, [status, initSteps, visibleStepCount, getTotalStepCount, onReady]);

  const checkHealth = useCallback(async (): Promise<boolean> => {
    try {
      const port = getBackendPort();
      console.log(`[Health Check] Checking health on port ${port}...`);
      const response = await axios.get(`http://127.0.0.1:${port}/health`, {
        timeout: TIMING.healthCheckTimeout,
      });
      console.log(`[Health Check] Response:`, response.data);
      return response.data?.status === 'healthy';
    } catch (error) {
      console.error(`[Health Check] Failed:`, error);
      return false;
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

      // Check for timeout using startTimeRef (avoids stale closure)
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

        // Update init steps with current status
        const steps = buildInitSteps(systemStatus);
        setInitSteps(steps);

        if (readiness.allReady) {
          console.log('[Readiness] All components ready, transitioning to connected');
          setStatus('connected');
          // Animation will handle fade out
        } else {
          // Continue polling
          console.log('[Readiness] Not all ready, continuing to poll...');
          timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
        }
      } else {
        // Status fetch failed, continue polling
        console.warn('[Readiness] Status fetch failed, continuing to poll...');
        timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
      }
    };

    const pollHealth = async () => {
      if (!mounted) return;

      const isHealthy = await checkHealth();

      if (!mounted) return;

      if (isHealthy) {
        setStatus('fetching_status');

        // Fetch system status (with graceful degradation)
        const systemStatus = await fetchSystemStatus();

        if (!mounted) return;

        if (systemStatus) {
          // Build and display initialization steps
          const steps = buildInitSteps(systemStatus);
          setInitSteps(steps);

          // Check if all components are ready
          const readiness = checkReadiness(systemStatus);
          console.log('[Startup] Initial readiness check:', readiness);

          if (readiness.allReady) {
            // All ready, proceed to connected state
            setStatus('connected');
            // Animation will handle fade out
          } else {
            // Not all ready, transition to waiting_for_ready and start polling
            console.log('[Startup] Not all ready, transitioning to waiting_for_ready');
            setStatus('waiting_for_ready');
            const now = Date.now();
            startTimeRef.current = now; // Set ref for internal timeout tracking
            timeoutId = setTimeout(pollReadiness, TIMING.pollInterval);
          }
        } else {
          // Graceful degradation: proceed without status display
          setStatus('connected');
          setIsFadingOut(true);
          // Wait for fade-out animation to complete before calling onReady (Requirement 5.5)
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
          setStatus('error');
          setErrorMessage('Backend service failed to start within 60 seconds');
        } else {
          timeoutId = setTimeout(pollHealth, TIMING.pollInterval);
        }
      }
    };

    // First initialize backend to ensure port is set, then start polling
    const startHealthPolling = async () => {
      try {
        console.log('[Startup] Calling initializeBackend()...');
        // Wait for backend initialization to complete (this sets the correct port)
        const port = await initializeBackend();
        console.log(`[Startup] initializeBackend() returned port: ${port}`);
        console.log(`[Startup] getBackendPort() returns: ${getBackendPort()}`);

        if (!mounted) return;

        setStatus('connecting');
        // Start polling after backend is initialized
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

  // Render a single init step
  const renderInitStep = (step: InitStep, index: number, isChild: boolean = false) => {
    const isStepVisible = index < visibleStepCount;
    if (!isStepVisible) return null;

    const statusIcon = STATUS_ICONS[step.status];
    const statusColor = STATUS_COLORS[step.status];

    const prefix = isChild ? '└─ ' : '';
    const label = t(step.labelKey, step.interpolation);

    return (
      <div
        key={step.id}
        className="flex items-start gap-2 animate-fade-in"
        style={{
          fontFamily: 'monospace',
          fontSize: '14px',
          paddingLeft: isChild ? '20px' : '0',
          opacity: isStepVisible ? 1 : 0,
          transition: 'opacity 0.2s ease-in',
        }}
      >
        {step.status === 'in_progress' ? (
          <Spinner size="sm" />
        ) : (
          <span style={{ color: statusColor, fontWeight: 'bold' }}>
            {statusIcon}
          </span>
        )}
        <span className="text-[var(--color-text)]">
          {prefix}{label}
        </span>
        {step.error && (
          <span className="text-[var(--color-error,#ef4444)] text-xs ml-2">
            ({step.error})
          </span>
        )}
      </div>
    );
  };

  // Render all init steps with proper indexing for animation
  const renderInitSteps = () => {
    let globalIndex = 0;
    const elements: ReactNode[] = [];

    for (const step of initSteps) {
      const stepElement = renderInitStep(step, globalIndex, false);
      if (stepElement) elements.push(stepElement);
      globalIndex++;

      // Render children
      if (step.children) {
        for (const child of step.children) {
          const childElement = renderInitStep(child, globalIndex, true);
          if (childElement) elements.push(childElement);
          globalIndex++;
        }
      }
    }

    return elements;
  };

  if (!isVisible) {
    return null;
  }

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)] transition-opacity duration-500 ${
        isFadingOut ? 'opacity-0' : 'opacity-100'
      }`}
    >
      <div className="flex flex-col items-center gap-6 max-w-md px-8">
        {/* Logo */}
        <div className="w-24 h-24 rounded-2xl overflow-hidden">
          <img src={logo} alt="SwarmAI" className="w-full h-full object-contain" />
        </div>

        {/* App Name */}
        <h1 className="text-3xl font-bold text-[var(--color-text)]">SwarmAI</h1>

        {/* Connecting state - show spinner */}
        {(status === 'starting' || status === 'connecting') && (
          <>
            {/* Loading Spinner with connecting message */}
            <div className="flex items-center gap-3" style={{ fontFamily: 'monospace' }}>
              <Spinner size="md" />
              <span className="text-[var(--color-text-muted)]">
                {t('startup.connectingToBackend')}
              </span>
            </div>

            {/* Progress bar */}
            <div className="w-64 h-1 bg-[var(--color-border)] rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full animate-pulse" style={{ width: '60%' }} />
            </div>
          </>
        )}

        {/* Fetching status state */}
        {status === 'fetching_status' && (
          <div className="flex items-center gap-3" style={{ fontFamily: 'monospace' }}>
            <Spinner size="md" />
            <span className="text-[var(--color-text-muted)]">
              {t('startup.connectingToBackend')}
            </span>
          </div>
        )}

        {/* Waiting for ready state - show initialization steps with polling indicator */}
        {status === 'waiting_for_ready' && initSteps.length > 0 && (
          <div className="flex flex-col gap-2 w-full max-w-sm">
            {renderInitSteps()}
            {/* Polling indicator */}
            <div className="flex items-center gap-2 mt-2" style={{ fontFamily: 'monospace', fontSize: '12px' }}>
              <Spinner size="sm" />
              <span className="text-[var(--color-text-muted)]">
                {t('startup.waitingForReady')}
              </span>
            </div>
          </div>
        )}

        {/* Connected state - show initialization steps */}
        {status === 'connected' && initSteps.length > 0 && (
          <div className="flex flex-col gap-2 w-full max-w-sm">
            {renderInitSteps()}
          </div>
        )}

        {status === 'error' && (
          <div className="flex flex-col items-center gap-4">
            {/* Error Icon */}
            <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center">
              <span className="material-symbols-outlined text-2xl text-red-400">
                error
              </span>
            </div>

            {/* Error Message */}
            <div className="text-center">
              <p className="text-red-400 font-medium mb-2">Failed to start</p>
              <p className="text-[var(--color-text-muted)] text-sm">{errorMessage}</p>
            </div>

            {/* Log Path Info */}
            <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg p-4 mt-2">
              <p className="text-sm text-[var(--color-text-muted)] mb-2">Please check the logs at:</p>
              <code className="text-xs text-primary bg-[var(--color-hover)] px-2 py-1 rounded block">
                {logPath}
              </code>
            </div>

            {/* Retry Button */}
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
