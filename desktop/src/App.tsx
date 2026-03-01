import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './contexts/ThemeContext';
import { BackendStartupOverlay, UpdateNotification } from './components/common';
import ThreeColumnLayout from './components/layout/ThreeColumnLayout';
import ChatPage from './pages/ChatPage';
import TasksPage from './pages/TasksPage';
import PluginsPage from './pages/PluginsPage';
import ChannelsPage from './pages/ChannelsPage';
import SwarmCorePage from './pages/SwarmCorePage';
import WorkspacesPage from './pages/WorkspacesPage';

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

  // Log mode on startup
  useEffect(() => {
    if (isDev) {
      console.log('Development mode: using manual backend on port 8000');
    }
    // In production mode, BackendStartupOverlay handles backend initialization
  }, []);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
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
              {/* Keep other routes that aren't converted to modals yet */}
              <Route path="/dashboard" element={
                <ThreeColumnLayout>
                  <SwarmCorePage />
                </ThreeColumnLayout>
              } />
              <Route path="/workspaces" element={
                <ThreeColumnLayout>
                  <WorkspacesPage />
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
      </QueryClientProvider>
    </ThemeProvider>
  );
}
