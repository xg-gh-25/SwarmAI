/**
 * Root application component for SwarmAI desktop app.
 *
 * Sets up routing, React Query, theme provider, and backend startup overlay.
 * Uses a three-column layout (Left Sidebar, Workspace Explorer, Main Chat Panel).
 */

import { useEffect, useState } from 'react';
import { useZoom } from './hooks/useZoom';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';
import { HealthProvider } from './contexts/HealthContext';
import { BackendStartupOverlay, UpdateNotification, ShutdownOverlay } from './components/common';
import { getBackendPort } from './services/tauri';
import { ErrorBoundary } from './components/common/ErrorBoundary';
import { ToastStack } from './components/common/ToastStack';
import ThreeColumnLayout from './components/layout/ThreeColumnLayout';
import ChatPage from './pages/ChatPage';
import TasksPage from './pages/TasksPage';
import PluginsPage from './pages/PluginsPage';
import ChannelsPage from './pages/ChannelsPage';
import SwarmCorePage from './pages/SwarmCorePage';

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
            const port = getBackendPort();
            await fetch(`http://localhost:${port}/shutdown`, { method: 'POST' });
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

    // Web/dev fallback
    const handleBeforeUnload = () => {
      const port = getBackendPort();
      navigator.sendBeacon(`http://localhost:${port}/shutdown`);
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
          <ShutdownOverlay />
          {/* Backend startup overlay - only shown in production mode */}
          {/* onReady callback sets isBackendReady to true, allowing routes to mount */}
          {!isDev && <BackendStartupOverlay onReady={() => setIsBackendReady(true)} />}
          {/* Update notification - only shown in production mode */}
          {!isDev && <UpdateNotification />}
          {/* Only render routes after backend is ready to prevent race conditions */}
          {isBackendReady && (
            <BrowserRouter>
              <Routes>
                {/* Main route with ThreeColumnLayout - ChatPage is the main content */}
                {/* Requirements: 1.1 - Three-column layout with Left_Sidebar, Workspace_Explorer, Main_Chat_Panel */}
                <Route path="/" element={
                  <ThreeColumnLayout>
                    <ChatPage />
                  </ThreeColumnLayout>
                } />
                <Route path="/dashboard" element={
                  <ThreeColumnLayout>
                    <SwarmCorePage />
                  </ThreeColumnLayout>
                } />
                <Route path="/tasks" element={
                  <ThreeColumnLayout>
                    <TasksPage />
                  </ThreeColumnLayout>
                } />
                <Route path="/plugins" element={
                  <ThreeColumnLayout>
                    <PluginsPage />
                  </ThreeColumnLayout>
                } />
                <Route path="/channels" element={
                  <ThreeColumnLayout>
                    <ChannelsPage />
                  </ThreeColumnLayout>
                } />
              </Routes>
            </BrowserRouter>
          )}
          </ErrorBoundary>
          </HealthProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
