import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import { getBackendPort, initializeBackend } from '../../services/tauri';
import { systemService, SystemStatus } from '../../services/system';
import logo from '../../assets/logo.png';

type StartupStatus = 'starting' | 'connecting' | 'fetching_status' | 'connected' | 'error';

type InitStepStatus = 'pending' | 'success' | 'error';

interface InitStep {
  id: string;
  labelKey: string;
  status: InitStepStatus;
  error?: string;
  interpolation?: Record<string, string | number>;
  children?: InitStep[];
}

// Get the log directory path based on the current platform
function getLogPath(): string {
  const userAgent = navigator.userAgent.toLowerCase();
  if (userAgent.includes('mac')) {
    return '~/Library/Application Support/SwarmAI/logs/';
  } else if (userAgent.includes('win')) {
    return '%LOCALAPPDATA%\\SwarmAI\\logs\\';
  } else {
    // Linux and other Unix-like systems
    return '~/.local/share/SwarmAI/logs/';
  }
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

  // Get platform-specific log path
  const logPath = useMemo(() => getLogPath(), []);

  // Build initialization steps from system status
  const buildInitSteps = useCallback((systemStatus: SystemStatus): InitStep[] => {
    const steps: InitStep[] = [];

    // Database step
    steps.push({
      id: 'database',
      labelKey: 'startup.databaseInitialized',
      status: systemStatus.database.healthy ? 'success' : 'error',
      error: systemStatus.database.error,
    });

    // SwarmAgent step with children
    const agentChildren: InitStep[] = [
      {
        id: 'skills',
        labelKey: 'startup.systemSkillsBound',
        status: systemStatus.agent.ready ? 'success' : 'pending',
        interpolation: { count: systemStatus.agent.skillsCount },
      },
      {
        id: 'mcpServers',
        labelKey: 'startup.systemMcpServersBound',
        status: systemStatus.agent.ready ? 'success' : 'pending',
        interpolation: { count: systemStatus.agent.mcpServersCount },
      },
    ];

    steps.push({
      id: 'agent',
      labelKey: 'startup.swarmAgentReady',
      status: systemStatus.agent.ready ? 'success' : 'error',
      children: agentChildren,
    });

    // Channel gateway step
    steps.push({
      id: 'channelGateway',
      labelKey: 'startup.channelGatewayStarted',
      status: systemStatus.channelGateway.running ? 'success' : 'error',
    });

    return steps;
  }, []);

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
    }, 150); // 150ms delay between each step

    return () => clearTimeout(timer);
  }, [initSteps, visibleStepCount, getTotalStepCount]);

  // Proceed to fade out after all steps are visible
  useEffect(() => {
    if (initSteps.length === 0) return;

    const totalSteps = getTotalStepCount(initSteps);
    if (visibleStepCount < totalSteps) return;

    // All steps visible, wait a moment then fade out
    const timer = setTimeout(() => {
      setIsFadingOut(true);
      setTimeout(() => {
        setIsVisible(false);
        onReady?.();
      }, 500); // Match animation duration
    }, 500); // Wait 500ms after all steps are shown

    return () => clearTimeout(timer);
  }, [initSteps, visibleStepCount, getTotalStepCount, onReady]);

  const checkHealth = useCallback(async (): Promise<boolean> => {
    try {
      const port = getBackendPort();
      console.log(`[Health Check] Checking health on port ${port}...`);
      const response = await axios.get(`http://127.0.0.1:${port}/health`, {
        timeout: 3000,
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
    let attempts = 0;
    const maxAttempts = 60; // 60 attempts * 1 second = 60 seconds timeout
    let timeoutId: ReturnType<typeof setTimeout>;
    let mounted = true;

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
          setStatus('connected');
          // Animation will handle fade out
        } else {
          // Graceful degradation: proceed without status display
          setStatus('connected');
          setIsFadingOut(true);
          setTimeout(() => {
            if (mounted) {
              setIsVisible(false);
              onReady?.();
            }
          }, 500);
        }
      } else {
        attempts++;
        if (attempts >= maxAttempts) {
          setStatus('error');
          setErrorMessage('Backend service failed to start within 60 seconds');
        } else {
          timeoutId = setTimeout(pollHealth, 1000);
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
        console.log('[Startup] Starting health polling in 500ms...');
        timeoutId = setTimeout(pollHealth, 500);
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
  }, [checkHealth, fetchSystemStatus, buildInitSteps, onReady]);

  // Render a single init step
  const renderInitStep = (step: InitStep, index: number, isChild: boolean = false) => {
    const isVisible = index < visibleStepCount;
    if (!isVisible) return null;

    const statusIcon = step.status === 'success' 
      ? '✓' 
      : step.status === 'error' 
        ? '✗' 
        : '○';
    
    const statusColor = step.status === 'success'
      ? 'var(--color-success, #22c55e)'
      : step.status === 'error'
        ? 'var(--color-error, #ef4444)'
        : 'var(--color-text-muted)';

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
          opacity: isVisible ? 1 : 0,
          transition: 'opacity 0.2s ease-in',
        }}
      >
        <span style={{ color: statusColor, fontWeight: 'bold' }}>
          {statusIcon}
        </span>
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
              <svg
                className="animate-spin h-4 w-4 text-primary"
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
            <svg
              className="animate-spin h-4 w-4 text-primary"
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
            <span className="text-[var(--color-text-muted)]">
              {t('startup.connectingToBackend')}
            </span>
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
              onClick={() => window.location.reload()}
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
