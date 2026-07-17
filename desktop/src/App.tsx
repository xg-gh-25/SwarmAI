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
          {/* Backend startup overlay - production mode only */}
          {!isDev && <BackendStartupOverlay onReady={() => setIsBackendReady(true)} />}
          {/* Non-blocking banner for background daemon version-sync status.
              Sibling to the overlay — overlay dismissal is independent of
              upgrade lifetime (see daemon-startup-timeout-regression fix). */}
          {!isDev && isDesktop() && <BackendUpgradeBanner />}
          {/* Credential-expiry banner — health-poll driven (reads health.auth).
              Not gated on isDev/isDesktop: expired creds matter in every mode
              and the data comes from /health, not a Tauri event. */}
          <CredentialBanner />
          {/* Update notification — Desktop only (Tauri plugin imports) */}
          {!isDev && isDesktop() && <UpdateNotification />}
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
 * Route guard component.
 *
 * Checks onboarding status and shows OnboardingPage on first run.
 * Must be inside QueryClientProvider for useQuery.
 */
function AppRoutes() {
  const { data: status, refetch } = useQuery({
    queryKey: ['system-status-onboarding'],
    queryFn: systemService.getStatus,
    staleTime: 1000 * 60 * 10, // 10 min — only check once
    retry: 2,
  });

  // Show onboarding whenever the user hasn't completed it — even if the backend
  // is only partially initialized. Step1 System Check is the in-wizard wait state.
  if (shouldShowOnboarding(status)) {
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
