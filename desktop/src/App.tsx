/**
 * Root application component for SwarmAI desktop app.
 *
 * Sets up routing, React Query, theme provider, and backend startup overlay.
 * Uses a three-column layout (Left Sidebar, Workspace Explorer, Main Chat Panel).
 */

import { useEffect, useState } from 'react';
import { useZoom } from './hooks/useZoom';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider, useToast } from './contexts/ToastContext';
import type { ToastSeverity } from './types';
import { HealthProvider } from './contexts/HealthContext';
import { BackendStartupOverlay, BackendUpgradeBanner, CredentialBanner, UpdateNotification, ShutdownOverlay } from './components/common';
import { getApiBaseUrl, isDesktop } from './services/tauri';
import { ErrorBoundary } from './components/common/ErrorBoundary';
import { ToastStack } from './components/common/ToastStack';
import { AudioKeepAlive } from './components/AudioKeepAlive';
import ThreeColumnLayout from './components/layout/ThreeColumnLayout';
import ChatPage from './pages/ChatPage';
import OnboardingPage from './pages/OnboardingPage';
import { systemService, type SystemStatus } from './services/system';

/**
 * Onboarding gate predicate.
 *
 * A user who has NOT completed onboarding must reach the wizard — REGARDLESS of
 * `initialized`. This deliberately does NOT require `initialized` (the old gate
 * did, which stranded a partial-init new user on an unusable ChatPage): the
 * BackendStartupOverlay dismisses on agent+workspace readiness while `initialized`
 * additionally requires db+gateway, so a partial-init new user satisfied the
 * overlay but failed the gate. Step1 System Check inside OnboardingPage is the
 * live wait state for the not-yet-ready backend. Returning users (migration sets
 * onboarding_complete=1 where initialization_complete=1) are never re-onboarded.
 */
export function shouldShowOnboarding(status: SystemStatus | undefined): boolean {
  return !!status && !status.onboardingComplete;
}

/**
 * What AppRoutes should render given the onboarding-status query state.
 * 'error' → the status query failed after all retries (no blank-screen dead-end);
 * 'loading' → render nothing (avoid the new-user ChatPage flash while status is
 * undefined); 'onboarding' → wizard; 'app' → ChatPage. Pure, so it's testable
 * without wiring a full useQuery render.
 *
 * `isError` is checked FIRST and BEFORE the `status === undefined` clause: on a
 * failed query react-query leaves status=undefined + isLoading=false, which would
 * otherwise fall into 'loading' and render null forever (the only no-exit dead-end
 * in the startup chain — the overlay has already faded). It defaults to false so
 * every existing 2-arg caller/test behaves identically. `isLoading` still wins over
 * `isError` so a mid-retry tick never flashes the error card, and a resolved status
 * (success) is never overridden by a stale error flag.
 */
export function routeDecision(
  status: SystemStatus | undefined,
  isLoading: boolean,
  isError = false,
): 'loading' | 'onboarding' | 'app' | 'error' {
  if (isLoading) return 'loading';
  if (isError && status === undefined) return 'error';
  if (status === undefined) return 'loading';
  return shouldShowOnboarding(status) ? 'onboarding' : 'app';
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5, // 5 minutes
      retry: 1,
    },
  },
});

// Check if running in development mode
const isDev = import.meta.env.DEV;

export default function App() {
  // Track if backend is ready - prevents routes from mounting before backend is initialized
  const [isBackendReady, setIsBackendReady] = useState(isDev);

  // App-wide zoom: Cmd+Plus / Cmd+Minus / Cmd+0
  useZoom();

  // Log mode on startup
  useEffect(() => {
    if (isDev) {
      console.log('Development mode: using manual backend on port 8000');
    }
    // In production mode, BackendStartupOverlay handles backend initialization
  }, []);

  // Window close handling — daemon-only architecture.
  // The daemon survives app close (channels, jobs, Slack stay alive).
  // We do NOT call /shutdown — that would kill all active sessions including Slack.
  // Tauri's graceful_shutdown_and_kill already correctly leaves the daemon running.
  useEffect(() => {
    let unlisten: (() => void) | undefined;

    const setupTauriCloseHandler = async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        const { getCurrentWindow } = await import('@tauri-apps/api/window');
        unlisten = await listen('tauri://close-requested', async () => {
          // No /shutdown call — daemon keeps running for background services.
          // Tauri lib.rs graceful_shutdown_and_kill handles the rest.
          await getCurrentWindow().close();
        });
      } catch {
        // Not in Tauri environment — no-op
      }
    };
    setupTauriCloseHandler();

    return () => {
      unlisten?.();
    };
  }, []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <HealthProvider>
          <ErrorBoundary variant="app">
          <ToastStack />
          <AudioKeepAlive />
          {/* Desktop-only overlays — Tauri imports crash in browser (Hive mode) */}
          {isDesktop() && <ShutdownOverlay />}
          {/* Backend startup overlay - production mode only.
              NOT wrapped in an isolating ErrorBoundary: it is the SOLE trigger of
              setIsBackendReady (onReady below), and AppRoutes mounts only once
              isBackendReady=true. Swallowing its crash to null would strand
              isBackendReady=false forever → permanent boot hang with no exit.
              It stays under the app-level ErrorBoundary above, which offers Reload. */}
          {!isDev && <BackendStartupOverlay onReady={() => setIsBackendReady(true)} />}
          {/* Passive global banners — each isolated in its OWN ErrorBoundary so a
              crash in one degrades to that banner disappearing (componentDidCatch
              logs it — not silent), instead of the app-level boundary escalating a
              single-banner crash to a full-screen "Something went wrong". These are
              root-mounted (outside LayoutProvider); the boot-crash class they belong
              to is also guarded loudly by root-mounted-no-shell-context.test.ts.
              Safe to isolate (unlike the startup overlay): each already renders null
              conditionally and none gates app mount. */}
          {/* Non-blocking banner for background daemon version-sync status.
              Sibling to the overlay — overlay dismissal is independent of
              upgrade lifetime (see daemon-startup-timeout-regression fix). */}
          {!isDev && isDesktop() && <ErrorBoundary fallback={null}><BackendUpgradeBanner /></ErrorBoundary>}
          {/* Credential-expiry banner — health-poll driven (reads health.auth).
              Not gated on isDev/isDesktop: expired creds matter in every mode
              and the data comes from /health, not a Tauri event. */}
          <ErrorBoundary fallback={null}><CredentialBanner /></ErrorBoundary>
          {/* Update notification — Desktop only (Tauri plugin imports) */}
          {!isDev && isDesktop() && <ErrorBoundary fallback={null}><UpdateNotification /></ErrorBoundary>}
          {/* swarm:toast document-event → ToastContext bridge — always mounted (not
              backend-gated), so decoupled dispatchers (Canvas 404 notice, overlays)
              surface real toasts even before routes render. */}
          <ErrorBoundary fallback={null}><SwarmToastBridge /></ErrorBoundary>
          {/* Post-update welcome toast (both Desktop and Hive) — inside backend gate */}
          {/* Only render routes after backend is ready to prevent race conditions */}
          {isBackendReady && <>
            {!isDev && <PostUpdateToast />}
            <AppRoutes />
          </>}
          </ErrorBoundary>
          </HealthProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

/**
 * Detects version change and shows a welcome-back toast.
 * Uses localStorage to track the last-seen version.
 * Must be inside ToastProvider.
 */
function PostUpdateToast() {
  const { addToast } = useToast();

  useEffect(() => {
    const checkVersion = async () => {
      try {
        const apiBase = getApiBaseUrl();
        const resp = await fetch(`${apiBase}/health`, {
          signal: AbortSignal.timeout(3000),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const currentVersion = data.version;
        if (!currentVersion) return;

        const lastVersion = localStorage.getItem('swarmai_last_version');
        if (lastVersion && lastVersion !== currentVersion) {
          addToast({
            severity: 'success',
            message: `Updated to v${currentVersion}. All your data is exactly where you left it.`,
            durationMs: 8000,
            id: 'post-update-toast',
          });
        }
        localStorage.setItem('swarmai_last_version', currentVersion);
      } catch {
        // Health endpoint not ready yet — skip silently
      }
    };

    // Small delay to let the health endpoint stabilize after backend ready signal.
    // This component is only mounted after isBackendReady=true, so no long wait needed.
    const timer = setTimeout(checkVersion, 500);
    return () => clearTimeout(timer);
  }, [addToast]);

  return null;
}

/**
 * SwarmToastBridge — the ONE document `swarm:toast` → ToastContext bridge.
 *
 * Several decoupled surfaces (useCanvasHost's 404 notice, LibraryOverlay, NewBrainOverlay)
 * dispatch a `document` CustomEvent `swarm:toast` instead of threading `useToast()` down —
 * a decoupled-producer pattern. But NOTHING was listening (the toast system is React-Context
 * only), so every `swarm:toast` was a DEAD event and its notice never rendered (Gate-2 HIGH,
 * run_f49d3ff3). This mounts the single listener that turns those events into real toasts, so
 * the pattern works for ALL current + future dispatchers. Must be inside ToastProvider.
 */
export function SwarmToastBridge() {
  const { addToast } = useToast();
  useEffect(() => {
    const onToast = (e: Event) => {
      const detail = (e as CustomEvent<{ message?: string; severity?: ToastSeverity; durationMs?: number; id?: string }>).detail;
      if (!detail?.message) return;
      addToast({
        severity: detail.severity ?? 'info',
        message: detail.message,
        ...(detail.durationMs !== undefined ? { durationMs: detail.durationMs } : {}),
        ...(detail.id ? { id: detail.id } : {}),
      });
    };
    document.addEventListener('swarm:toast', onToast);
    return () => document.removeEventListener('swarm:toast', onToast);
  }, [addToast]);
  return null;
}

/**
 * Route guard component.
 *
 * Checks onboarding status and shows OnboardingPage on first run.
 * Must be inside QueryClientProvider for useQuery.
 */
function AppRoutes() {
  const { data: status, isLoading, isError, refetch } = useQuery({
    queryKey: ['system-status-onboarding'],
    queryFn: systemService.getStatus,
    staleTime: 1000 * 60 * 10, // 10 min — only check once
    retry: 2,
  });

  // While the onboarding status is still loading, render nothing — do NOT fall
  // through to ChatPage. Otherwise a brand-new user (status undefined until the
  // query resolves) would see a sub-second flash of the unusable, un-onboarded
  // ChatPage before the wizard appears (meta-review MED, run_61c4c939).
  const decision = routeDecision(status, isLoading, isError);
  if (decision === 'loading') {
    return null;
  }
  // The status query failed all retries after the overlay already faded. Without
  // this branch routeDecision returned 'loading' → render null → permanent blank
  // screen with no exit (the only no-exit dead-end in the startup chain). Give the
  // user a visible Retry that re-runs the query (recovers to app/onboarding the
  // moment the backend responds). Plain div — renders in place of the route tree,
  // needs no router context.
  if (decision === 'error') {
    return (
      <div
        role="alert"
        className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-bg)]"
      >
        <div className="flex flex-col items-center gap-4 max-w-md px-8 text-center">
          <div className="w-12 h-12 rounded-full bg-red-500/20 flex items-center justify-center">
            <span className="material-symbols-outlined text-2xl text-red-400">error</span>
          </div>
          <p className="text-red-400 font-medium">Couldn’t reach the backend</p>
          <p className="text-[var(--color-text-muted)] text-sm">
            SwarmAI couldn’t load its status. The backend may still be starting or may have
            stopped.{' '}
            {isDesktop()
              ? <>Check the logs at <code className="text-primary">~/.swarm-ai/logs/</code>.</>
              : <>It may be restarting — retry in a moment, or contact your administrator if this persists.</>}
          </p>
          <button
            onClick={() => refetch()}
            className="mt-2 px-6 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors flex items-center gap-2"
          >
            <span className="material-symbols-outlined text-xl">refresh</span>
            Retry
          </button>
        </div>
      </div>
    );
  }
  if (decision === 'onboarding') {
    return <OnboardingPage onComplete={() => refetch()} />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={
          <ThreeColumnLayout>
            <ChatPage />
          </ThreeColumnLayout>
        } />
      </Routes>
    </BrowserRouter>
  );
}
