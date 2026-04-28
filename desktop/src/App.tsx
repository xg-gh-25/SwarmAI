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
import { ToastProvider } from './contexts/ToastContext';
import { HealthProvider } from './contexts/HealthContext';
import { BackendStartupOverlay, UpdateNotification, ShutdownOverlay, DaemonNudgeBanner } from './components/common';
import { getApiBaseUrl, isDesktop } from './services/tauri';
import { ErrorBoundary } from './components/common/ErrorBoundary';
import { ToastStack } from './components/common/ToastStack';
import { AudioKeepAlive } from './components/AudioKeepAlive';
import ThreeColumnLayout from './components/layout/ThreeColumnLayout';
import ChatPage from './pages/ChatPage';
import OnboardingPage from './pages/OnboardingPage';
import { systemService } from './services/system';

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

  // Graceful shutdown on app close
  useEffect(() => {
    let unlisten: (() => void) | undefined;

    const setupTauriCloseHandler = async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        const { getCurrentWindow } = await import('@tauri-apps/api/window');
        unlisten = await listen('tauri://close-requested', async () => {
          try {
            const apiBase = getApiBaseUrl();
            await fetch(`${apiBase}/shutdown`, { method: 'POST' });
          } catch {
            // Backend may already be down
          }
          await getCurrentWindow().close();
        });
      } catch {
        // Not in Tauri environment — beforeunload fallback only
      }
    };
    setupTauriCloseHandler();

    // Desktop-only: shutdown backend when browser tab closes.
    // In Hive mode, closing a tab must NOT shut down the shared backend
    // (other tabs or Slack may still be using it).
    const handleBeforeUnload = () => {
      if (!isDesktop()) return;
      const apiBase = getApiBaseUrl();
      navigator.sendBeacon(`${apiBase}/shutdown`);
    };
    window.addEventListener('beforeunload', handleBeforeUnload);

    return () => {
      unlisten?.();
      window.removeEventListener('beforeunload', handleBeforeUnload);
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
          {/* Update notification + daemon nudge — Desktop only (Tauri plugin imports) */}
          {!isDev && isDesktop() && <UpdateNotification />}
          {!isDev && isDesktop() && <DaemonNudgeBanner />}
          {/* Only render routes after backend is ready to prevent race conditions */}
          {isBackendReady && <AppRoutes />}
          </ErrorBoundary>
          </HealthProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
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

  // Show onboarding on first run (only if backend is initialized and onboarding not done)
  if (status?.initialized && !status?.onboardingComplete) {
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
