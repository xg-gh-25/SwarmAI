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
 * - Listens for Tauri backend events for instant crash/restart detection
 * - Uses `useRef` for interval/failure tracking to avoid re-renders
 *   on every poll; only updates React state on actual transitions
 * - Uses plain `fetch` (not axios) to avoid circular dependency with
 *   the rate limiter interceptor added to the axios instance
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import type { HealthState, BackendStatus, AuthStatus } from '../types';
import { getApiBaseUrl, isDesktop, setBackendPort, tauriService } from '../services/tauri';
import { useToast } from '../contexts/ToastContext';

/** Default polling interval in milliseconds. */
const DEFAULT_INTERVAL_MS = 30_000;

/** Default number of consecutive failures before marking disconnected. */
const DEFAULT_FAILURE_THRESHOLD = 2;

/** Liveness window (ms) for the JS poll's disconnected decision.
 *  The Rust watchdog emits `backend-degraded` every ~3s (RECOVERY_POLL_SECS)
 *  while the daemon PROCESS is alive but the event loop is stalled (heartbeat
 *  fresh). If such a signal arrived within this window, a JS-poll failure is a
 *  *busy* backend, not a dead one → map to 'degraded' (inputs stay usable), not
 *  'disconnected'. 10s comfortably spans multiple 3s emits yet is short enough
 *  that a genuine death (no more degraded events) escalates promptly. */
const DEGRADED_LIVENESS_WINDOW_MS = 10_000;

/** Toast id used for the persistent disconnected warning. */
const HEALTH_DISCONNECTED_TOAST_ID = 'health-disconnected';

/** Initial health state before the first poll completes. */
const INITIAL_HEALTH_STATE: HealthState = {
  status: 'initializing',
  lastCheckedAt: null,
  consecutiveFailures: 0,
};

/** Why the last disconnected→connected recovery happened.
 *  - 'restarted': a NEW backend process (Rust watchdog saw boot_id change)
 *  - 'resumed':   the SAME process resumed after a transient stall (boot_id unchanged)
 *  - null:        a plain JS-poller recovery with no Tauri event — we don't know which */
type RecoveryKind = 'restarted' | 'resumed' | null;

/** Map a recovery kind to an honest success toast. Pure + exported for tests.
 *  Only claims "restarted" when the watchdog PROVED a new process; a resume
 *  says "responding again" (no restart happened); unknown → generic reconnect. */
export function recoveryToastMessage(kind: RecoveryKind): string {
  if (kind === 'restarted') return 'Backend restarted and reconnected';
  if (kind === 'resumed') return 'Backend responding again';
  return 'Backend reconnected';
}

/** Loop-independent backend-liveness verdict, derived from {@link BackendStatus}.
 *  Consumed by the streaming stall/watchdog terminators (chat.ts, MessageStore)
 *  so they NEVER cancel a live-but-silent stream (run_d2f25153).
 *  - 'alive'   → the backend is up (steady or busy-but-proven-live). NEVER terminate.
 *  - 'dead'    → proven death → termination is authorized.
 *  - 'unknown' → we have no verdict yet (app boot) → bounded re-arm, then treat dead. */
export type BackendLiveness = 'alive' | 'dead' | 'unknown';

/** Map a {@link BackendStatus} to a {@link BackendLiveness} verdict. PINNED —
 *  this mapping is the safety story of the no-interrupt-while-alive fix.
 *
 *  🔒 'connected' MUST be 'alive' (Gate-1 Finding 4): status stays 'connected'
 *  for a window after the backend goes silent, and treating 'connected' as
 *  'unknown' would route the common event-loop-starvation case through the
 *  bounded-kill path → re-creating the exact 158s blind-cancel this fix removes.
 *  The bound on that "connected-but-actually-dead" window differs by environment,
 *  and BOTH are acceptable (a dead backend still reaches 'dead' within ~1 stall
 *  interval, never re-arms forever):
 *    • Desktop (Tauri): the Rust watchdog reads the loop-independent heartbeat
 *      FILE and emits 'degraded' (busy-but-proven-alive) / 'disconnected' (dead)
 *      — so a starved-but-alive loop is provably 'degraded'→alive, and a real
 *      death flips 'disconnected'→dead. This is the strong path.
 *    • Hive (production browser, PE-review MEDIUM #2): there is NO Rust watchdog
 *      and the heartbeat file is inert (Tauri event listeners are guarded by
 *      isDesktop() below and never register). The sole judge is the JS /health
 *      poll (30s interval, 2-failure threshold), so a dead backend flips to
 *      'disconnected'→dead within ~≤65s of death — NOT via any watchdog. Do not
 *      assume a watchdog exists in Hive.
 *
 *  'degraded' is also 'alive' — it is the STRONGEST proof of life (the watchdog
 *  read a fresh, non-wedged heartbeat from the loop-independent file). Only
 *  'initializing' (app boot, before the first health signal) is genuinely
 *  'unknown'; only 'disconnected' (a proven death / wedge) is 'dead'. Pure +
 *  exported for tests. */
export function deriveBackendLiveness(status: BackendStatus): BackendLiveness {
  switch (status) {
    case 'connected':
    case 'degraded':
      return 'alive';
    case 'disconnected':
      return 'dead';
    case 'initializing':
      return 'unknown';
  }
}

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
  // Why the last disconnected→connected recovery happened, set by the Tauri
  // event handlers just before they trigger a re-check. handleSuccess reads it
  // to pick an honest toast: a real restart (new process) vs a resume (same
  // process that was merely blocked) vs a plain JS-poller recovery (null).
  const recoveryKindRef = useRef<RecoveryKind>(null);
  // Timestamp (ms) of the last Tauri `backend-degraded` event = the last time the
  // Rust watchdog PROVED the daemon process alive (heartbeat fresh) despite a
  // missed /health. handleFailure reads it so the JS poll doesn't independently
  // declare 'disconnected' on a backend that is merely busy. 0 = never seen
  // (Hive/browser has no Tauri events → legacy behavior preserved). A real death
  // event resets it to 0 so a genuine outage is never masked.
  const lastDegradedAtRef = useRef(0);
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
    (now: number, backendStatus: BackendStatus, auth?: AuthStatus) => {
      if (!mountedRef.current) return;

      const previousStatus = currentStatusRef.current;
      failureCountRef.current = 0;
      currentStatusRef.current = backendStatus;

      // Transition: disconnected OR degraded → ANY responsive state — clear the
      // error/reconnecting toast + notify. degraded (run_13094a88) uses the SAME
      // toast id (HEALTH_DISCONNECTED_TOAST_ID) for its "reconnecting…" banner, so
      // a degraded→connected recovery must clear it too (else the banner sticks).
      // Previous bug: only cleared on disconnected→connected, missing
      // disconnected→initializing→connected path (toast stayed permanently).
      if (
        (previousStatus === 'disconnected' || previousStatus === 'degraded') &&
        backendStatus !== 'disconnected' &&
        backendStatus !== 'degraded'
      ) {
        removeToastRef.current(HEALTH_DISCONNECTED_TOAST_ID);
        if (backendStatus === 'connected') {
          // Pick an honest recovery message based on what actually happened.
          // Default (JS-poller-only recovery, no Tauri event) → generic reconnect.
          const kind = recoveryKindRef.current;
          recoveryKindRef.current = null;
          addToastRef.current({
            severity: 'success',
            message: recoveryToastMessage(kind),
            autoDismiss: true,
          });
          // Notify chat layer so active tabs can recover SSE streams.
          // ChatPage listens for this to show recovery UI or auto-retry.
          window.dispatchEvent(new CustomEvent('swarm:backend-recovered'));
        }
      }

      setHealthState({
        status: backendStatus,
        auth,
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
        // Liveness override: if the Rust watchdog recently proved the process
        // alive (a `backend-degraded` within the window = heartbeat fresh), a JS
        // /health failure means BUSY, not DEAD. Map to 'degraded' (inputs stay
        // usable) instead of independently declaring 'disconnected'. This closes
        // the second, independent offline judge — the JS poll — against the same
        // heartbeat signal the Rust watchdog already honors (run_4f32022a). Only
        // reachable on desktop (Tauri emits the event); Hive/browser keeps
        // lastDegradedAt=0 → the else-branch = exact legacy behavior.
        const livenessFresh =
          lastDegradedAtRef.current > 0 &&
          now - lastDegradedAtRef.current <= DEGRADED_LIVENESS_WINDOW_MS;

        if (livenessFresh) {
          // Don't downgrade a real 'disconnected' (a proven death already
          // disabled inputs) back to 'degraded' — mirrors the degraded handler.
          if (previousStatus !== 'disconnected') {
            currentStatusRef.current = 'degraded';
            if (previousStatus !== 'degraded') {
              recoveryKindRef.current = null;
              addToastRef.current({
                severity: 'warning',
                message: 'Backend busy — reconnecting…',
                id: HEALTH_DISCONNECTED_TOAST_ID,
              });
            }
          }
        } else {
          currentStatusRef.current = 'disconnected';

          // Transition: connected/initializing → disconnected — fire warning.
          if (previousStatus !== 'disconnected') {
            // Start each disconnect episode with a clean recovery kind so a
            // value set by a prior, never-consumed event can't mislabel this
            // episode's recovery toast.
            recoveryKindRef.current = null;
            addToastRef.current({
              severity: 'warning',
              message: 'Backend is unavailable',
              id: HEALTH_DISCONNECTED_TOAST_ID,
            });
          }
        }
      }

      setHealthState((prev) => ({
        ...prev,
        status: currentStatusRef.current,
        // When we lose the backend we no longer have a fresh auth signal —
        // clear it so the credential banner doesn't assert a state we can't
        // currently confirm (Gate-2 M2). The next successful poll re-supplies
        // it. Preserve auth while still 'connected'/'initializing'.
        auth: currentStatusRef.current === 'disconnected' ? undefined : prev.auth,
        lastCheckedAt: now,
        consecutiveFailures: failures,
      }));
    },
    [failureThreshold], // only re-creates if threshold option changes
  );

  // ------------------------------------------------------------------
  // Core polling logic
  // ------------------------------------------------------------------

  const performHealthCheck = useCallback(async () => {
    const apiBase = getApiBaseUrl();
    const url = `${apiBase}/health`;
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

      // Parse the response body to detect "initializing" status + auth state.
      let backendStatus: BackendStatus = 'connected';
      let auth: AuthStatus | undefined;
      try {
        const body = (await response.json()) as { status?: string; auth?: string };
        if (body?.status === 'initializing') {
          backendStatus = 'initializing';
        }
        if (body?.auth === 'valid' || body?.auth === 'expired' || body?.auth === 'unknown') {
          auth = body.auth;
        }
      } catch {
        // If JSON parsing fails, treat as connected (response was 2xx).
      }

      handleSuccess(now, backendStatus, auth);
    } catch {
      // Network error, timeout, or any other fetch failure.
      handleFailure(now);
    }
  }, [handleSuccess, handleFailure]);

  // Stable ref for performHealthCheck — used by backend event handlers
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
  // Tauri backend events: instant health transitions on daemon death/restart
  // ------------------------------------------------------------------

  useEffect(() => {
    // Only relevant in production Tauri DESKTOP (daemon mode). Guard on
    // isDesktop(), not just isDev: in Hive (production browser, isDev=false, no
    // Tauri), tauriService.onBackend*() → Tauri listen() calls
    // window.__TAURI_INTERNALS__.transformCallback (undefined in a browser) and
    // throws → 4 unhandled promise rejections on every Hive boot. Every sibling
    // (BackendStartupOverlay, App.tsx overlays) guards with isDesktop(); match it.
    const isDev = import.meta.env.DEV;
    if (isDev || !isDesktop()) return;

    const unlisteners: Array<Promise<() => void>> = [];

    // Health probe failed once — mark disconnected. This fires on a SINGLE
    // missed /health (Rust watchdog, 3s timeout), which cannot distinguish a
    // real process death from a transient stall (e.g. the event loop blocked
    // by heavy synchronous work for >3s). Most fires self-heal on the next
    // probe (boot_id unchanged → never actually restarted), so the wording
    // must NOT assert "crashed" or "restarting" — both overclaim what a single
    // timeout proves. A truly-dead daemon escalates to onBackendTerminated.
    unlisteners.push(
      tauriService.onBackendTerminatedRestarting(() => {
        if (!mountedRef.current) return;

        failureCountRef.current = DEFAULT_FAILURE_THRESHOLD;
        currentStatusRef.current = 'disconnected';
        recoveryKindRef.current = null; // clean episode; resumed/restarted sets it next
        lastDegradedAtRef.current = 0;  // real death → drop stale liveness proof so it can't mask this outage

        addToastRef.current({
          severity: 'warning',
          message: 'Backend not responding — reconnecting…',
          id: HEALTH_DISCONNECTED_TOAST_ID,
        });

        setHealthState((prev) => ({
          ...prev,
          status: 'disconnected',
          // Lost backend → no fresh auth signal; clear it (Gate-2 M2).
          auth: undefined,
          lastCheckedAt: Date.now(),
          consecutiveFailures: DEFAULT_FAILURE_THRESHOLD,
        }));
      }),
    );

    // Backend DEGRADED — a /health probe was missed but the daemon PROCESS is
    // verified alive (run_13094a88). This is a transient >3s stall, NOT death.
    // Show a reconnecting banner but keep status usable: map to 'degraded', which
    // does NOT disable chat inputs (ChatPage disables iff status==='disconnected').
    // Do NOT touch failureCount — a real persistent outage escalates to
    // onBackendTerminatedRestarting on the Rust side (miss streak), and the 30s
    // poll's own failureThreshold path is independent.
    unlisteners.push(
      tauriService.onBackendDegraded(() => {
        if (!mountedRef.current) return;

        // Don't downgrade a real 'disconnected' back to 'degraded' — a live-death
        // that already disabled inputs must stay disabled until a genuine recovery.
        if (currentStatusRef.current === 'disconnected') return;

        // Record the liveness proof: the Rust watchdog verified the process alive
        // (heartbeat fresh). handleFailure reads this so the JS poll won't
        // independently flip to 'disconnected' within DEGRADED_LIVENESS_WINDOW_MS.
        lastDegradedAtRef.current = Date.now();
        currentStatusRef.current = 'degraded';
        recoveryKindRef.current = null;

        addToastRef.current({
          severity: 'warning',
          message: 'Backend busy — reconnecting…',
          id: HEALTH_DISCONNECTED_TOAST_ID,
        });

        setHealthState((prev) => ({
          ...prev,
          status: 'degraded',
          lastCheckedAt: Date.now(),
        }));
      }),
    );

    // Backend RESTARTED on new port (a NEW process — boot_id changed, or a
    // subprocess re-spawn) — update port + trigger health check.
    unlisteners.push(
      tauriService.onBackendRestarted((newPort: number) => {
        if (!mountedRef.current) return;

        console.log(`[HealthMonitor] Backend restarted on port ${newPort}`);
        setBackendPort(newPort);
        recoveryKindRef.current = 'restarted';

        // Give the new backend a moment to become healthy, then check
        setTimeout(() => {
          if (mountedRef.current) {
            performHealthCheckRef.current();
          }
        }, 2_000);
      }),
    );

    // Backend RESUMED after a transient stall — SAME process (boot_id unchanged),
    // never actually died. It is already responsive (the recovery poll read its
    // boot_id), so re-check almost immediately and report "responding again"
    // rather than claiming a restart.
    unlisteners.push(
      tauriService.onBackendResumed((port: number) => {
        if (!mountedRef.current) return;

        console.log(`[HealthMonitor] Backend resumed (stall, no restart) on port ${port}`);
        setBackendPort(port);
        recoveryKindRef.current = 'resumed';

        setTimeout(() => {
          if (mountedRef.current) {
            performHealthCheckRef.current();
          }
        }, 250);
      }),
    );

    // Backend terminated — watchdog exhausted recovery attempts.
    // In daemon mode (macOS), launchd WILL eventually restart it — just takes longer.
    // Don't tell user to "restart the app" unless it's truly unrecoverable.
    unlisteners.push(
      tauriService.onBackendTerminated(() => {
        if (!mountedRef.current) return;

        failureCountRef.current = DEFAULT_FAILURE_THRESHOLD;
        currentStatusRef.current = 'disconnected';
        recoveryKindRef.current = null;
        lastDegradedAtRef.current = 0;  // permanent death → drop stale liveness proof

        addToastRef.current({
          severity: 'warning',
          message: 'Backend is taking longer than expected to restart…',
          id: HEALTH_DISCONNECTED_TOAST_ID,
        });

        setHealthState((prev) => ({
          ...prev,
          status: 'disconnected',
          // Lost backend → no fresh auth signal; clear it (Gate-2 M2).
          auth: undefined,
          lastCheckedAt: Date.now(),
          consecutiveFailures: DEFAULT_FAILURE_THRESHOLD,
        }));
      }),
    );

    return () => {
      unlisteners.forEach((p) => p.then((unlisten) => unlisten()));
    };
  }, []);  

  return { state: healthState, checkNow: performHealthCheck };
}
