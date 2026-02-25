import { useState, useEffect, useCallback, useMemo } from 'react';
import axios from 'axios';
import { getBackendPort, initializeBackend } from '../../services/tauri';
import logo from '../../assets/logo.png';

type StartupStatus = 'starting' | 'connected' | 'error';

// Get the log directory path based on the current platform
function getLogPath(): string {
  const userAgent = navigator.userAgent.toLowerCase();
  if (userAgent.includes('mac')) {
    return '~/Library/Application Support/Owork/logs/';
  } else if (userAgent.includes('win')) {
    return '%LOCALAPPDATA%\\Owork\\logs\\';
  } else {
    // Linux and other Unix-like systems
    return '~/.local/share/owork/logs/';
  }
}

interface BackendStartupOverlayProps {
  onReady?: () => void;
}

export default function BackendStartupOverlay({ onReady }: BackendStartupOverlayProps) {
  const [status, setStatus] = useState<StartupStatus>('starting');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [isVisible, setIsVisible] = useState(true);
  const [isFadingOut, setIsFadingOut] = useState(false);

  // Get platform-specific log path
  const logPath = useMemo(() => getLogPath(), []);

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
        setStatus('connected');
        // Start fade out animation
        setIsFadingOut(true);
        setTimeout(() => {
          if (mounted) {
            setIsVisible(false);
            onReady?.();
          }
        }, 500); // Match animation duration
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
  }, [checkHealth, onReady]);

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
          <img src={logo} alt="Owork" className="w-full h-full object-contain" />
        </div>

        {/* App Name */}
        <h1 className="text-3xl font-bold text-[var(--color-text)]">Owork</h1>

        {status === 'starting' && (
          <>
            {/* Loading Spinner */}
            <div className="flex items-center gap-3">
              <svg
                className="animate-spin h-5 w-5 text-primary"
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
              <span className="text-[var(--color-text-muted)]">Starting...</span>
            </div>

            {/* Progress bar */}
            <div className="w-64 h-1 bg-[var(--color-border)] rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full animate-pulse" style={{ width: '60%' }} />
            </div>
          </>
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
