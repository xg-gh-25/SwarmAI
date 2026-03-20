/**
 * Backend health monitoring hook.
 *
 * Polls `GET /health` at a configurable interval to detect backend
 * availability. Tracks consecutive failures and transitions between
 * three states: `connected`, `disconnected`, and `initializing`.
 *
 * Key behaviors:
 * - Fires a persistent warning toast on connected → disconnected
 * - Fires a success toast on disconnected → connected
 * - Handles `initializing` status from the backend response body
 * - Listens for Tauri sidecar events for instant crash/restart detection
 * - Uses `useRef` for interval/failure tracking to avoid re-renders
 *   on every poll; only updates React state on actual transitions
 * - Uses plain `fetch` (not axios) to avoid circular dependency with
 *   the rate limiter interceptor added to the axios instance
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import type { HealthState, BackendStatus } from '../types';
import { getBackendPort, setBackendPort, tauriService } from '../services/tauri';
import { useToast } from '../contexts/ToastContext';

/** Default polling interval in milliseconds. */
const DEFAULT_INTERVAL_MS = 30_000;

/** Default number of consecutive failures before marking disconnected. */
const DEFAULT_FAILURE_THRESHOLD = 2;

/** Toast id used for the persistent disconnected warning. */
const HEALTH_DISCONNECTED_TOAST_ID = 'health-disconnected';

/** Initial health state before the first poll completes. */
const INITIAL_HEALTH_STATE: HealthState = {
  status: 'initializing',
  lastCheckedAt: null,
  consecutiveFailures: 0,
};

interface UseHealthMonitorOptions {
  /** Polling interval in ms. Default: 30_000 (30 seconds). */
  intervalMs?: number;
  /** Consecutive failures before transitioning to disconnected. Default: 2. */
  failureThreshold?: number;
}

/** Return type for {@link useHealthMonitor}. */
export interface UseHealthMonitorReturn {
  /** Current health state. */
  state: HealthState;
  /** Trigger an immediate out-of-cycle health check. */
  checkNow: () => void;
}

/**
 * Poll the backend `/health` endpoint and expose the current
 * {@link HealthState}. Fires toast notifications on status transitions.
 *
 * Returns `{ state, checkNow }` so callers (e.g. `HealthProvider`) can
 * trigger an immediate check for scenarios like SERVICE_UNAVAILABLE.
 */
export function useHealthMonitor(options?: UseHealthMonitorOptions): UseHealthMonitorReturn {
  const {
    intervalMs = DEFAULT_INTERVAL_MS,
    failureThreshold = DEFAULT_FAILURE_THRESHOLD,
  } = options ?? {};

  const { addToast, removeToast } = useToast();

  // React state — only updated on actual status transitions so
  // consumers re-render only when something meaningful changes.
  const [healthState, setHealthState] = useState<HealthState>(INITIAL_HEALTH_STATE);

  // Refs for mutable tracking that should NOT trigger re-renders.
  const failureCountRef = useRef(0);
  const currentStatusRef = useRef<BackendStatus>('initializing');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Guard against state updates after unmount.
  const mountedRef = useRef(true);

  // Stable refs for toast functions — avoids useCallback/useEffect churn.
  const addToastRef = useRef(addToast);
  addToastRef.current = addToast;
  const removeToastRef = useRef(removeToast);
  removeToastRef.current = removeToast;

  // ------------------------------------------------------------------
  // Transition helpers (read toast fns from refs — no deps needed)
  // ------------------------------------------------------------------

  const handleSuccess = useCallback(
    (now: number, backendStatus: BackendStatus) => {
      if (!mountedRef.current) return;

      const previousStatus = currentStatusRef.current;
      failureCountRef.current = 0;
      currentStatusRef.current = backendStatus;

      // Transition: disconnected → connected — fire recovery toast.
      if (previousStatus === 'disconnected' && backendStatus === 'connected') {
        removeToastRef.current(HEALTH_DISCONNECTED_TOAST_ID);
        addToastRef.current({
          severity: 'success',
          message: 'Backend reconnected',
          autoDismiss: true,
        });
      }

      setHealthState({
        status: backendStatus,
        lastCheckedAt: now,
        consecutiveFailures: 0,
      });
    },
    [], // stable — reads from refs
  );

  const handleFailure = useCallback(
    (now: number) => {
      if (!mountedRef.current) return;

      failureCountRef.current += 1;
      const failures = failureCountRef.current;
      const previousStatus = currentStatusRef.current;

      if (failures >= failureThreshold) {
        currentStatusRef.current = 'disconnected';

        // Transition: connected/initializing → disconnected — fire warning.
        if (previousStatus !== 'disconnected') {
          addToastRef.current({
            severity: 'warning',
            message: 'Backend is unavailable',
            id: HEALTH_DISCONNECTED_TOAST_ID,
          });
        }
      }

      setHealthState({
        status: currentStatusRef.current,
        lastCheckedAt: now,
        consecutiveFailures: failures,
      });
    },
    [failureThreshold], // only re-creates if threshold option changes
  );

  // ------------------------------------------------------------------
  // Core polling logic
  // ------------------------------------------------------------------

  const performHealthCheck = useCallback(async () => {
    const port = getBackendPort();
    const url = `http://localhost:${port}/health`;
    const now = Date.now();

    try {
      const response = await fetch(url, {
        method: 'GET',
        signal: AbortSignal.timeout(5_000), // 5 s timeout per check
      });

      if (!response.ok) {
        handleFailure(now);
        return;
      }

      // Parse the response body to detect "initializing" status.
      let backendStatus: BackendStatus = 'connected';
      try {
        const body = (await response.json()) as { status?: string };
        if (body?.status === 'initializing') {
          backendStatus = 'initializing';
        }
      } catch {
        // If JSON parsing fails, treat as connected (response was 2xx).
      }

      handleSuccess(now, backendStatus);
    } catch {
      // Network error, timeout, or any other fetch failure.
      handleFailure(now);
    }
  }, [handleSuccess, handleFailure]);

  // Stable ref for performHealthCheck — used by sidecar event handlers
  // so they don't cause effect re-subscriptions.
  const performHealthCheckRef = useRef(performHealthCheck);
  performHealthCheckRef.current = performHealthCheck;

  // ------------------------------------------------------------------
  // Lifecycle: start polling on mount, clean up on unmount
  // ------------------------------------------------------------------

  useEffect(() => {
    mountedRef.current = true;

    // Fire an immediate check — don't wait for the first interval tick.
    performHealthCheck();

    intervalRef.current = setInterval(performHealthCheck, intervalMs);

    return () => {
      mountedRef.current = false;
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [performHealthCheck, intervalMs]);

  // ------------------------------------------------------------------
  // Tauri sidecar events: instant health transitions on backend death/restart
  // ------------------------------------------------------------------

  useEffect(() => {
    // Only relevant in production (Tauri sidecar mode)
    const isDev = import.meta.env.DEV;
    if (isDev) return;

    const unlisteners: Array<Promise<() => void>> = [];

    // Backend died unexpectedly — immediately mark disconnected
    unlisteners.push(
      tauriService.onBackendTerminatedRestarting(() => {
        if (!mountedRef.current) return;

        failureCountRef.current = DEFAULT_FAILURE_THRESHOLD;
        currentStatusRef.current = 'disconnected';

        addToastRef.current({
          severity: 'warning',
          message: 'Backend crashed — restarting automatically…',
          id: HEALTH_DISCONNECTED_TOAST_ID,
        });

        setHealthState({
          status: 'disconnected',
          lastCheckedAt: Date.now(),
          consecutiveFailures: DEFAULT_FAILURE_THRESHOLD,
        });
      }),
    );

    // Backend auto-restarted on new port — update port + trigger health check
    unlisteners.push(
      tauriService.onBackendRestarted((newPort: number) => {
        if (!mountedRef.current) return;

        console.log(`[HealthMonitor] Backend restarted on port ${newPort}`);
        setBackendPort(newPort);

        // Give the new backend a moment to become healthy, then check
        setTimeout(() => {
          if (mountedRef.current) {
            performHealthCheckRef.current();
          }
        }, 2_000);
      }),
    );

    // Backend terminated permanently (intentional shutdown OR restart budget exhausted)
    unlisteners.push(
      tauriService.onBackendTerminated(() => {
        if (!mountedRef.current) return;

        failureCountRef.current = DEFAULT_FAILURE_THRESHOLD;
        currentStatusRef.current = 'disconnected';

        addToastRef.current({
          severity: 'error',
          message: 'Backend stopped — restart the app to recover',
          id: HEALTH_DISCONNECTED_TOAST_ID,
        });

        setHealthState({
          status: 'disconnected',
          lastCheckedAt: Date.now(),
          consecutiveFailures: DEFAULT_FAILURE_THRESHOLD,
        });
      }),
    );

    return () => {
      unlisteners.forEach((p) => p.then((unlisten) => unlisten()));
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- stable: all callbacks read from refs

  return { state: healthState, checkNow: performHealthCheck };
}
