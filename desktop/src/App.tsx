import { useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from './contexts/ThemeContext';
import { Layout, BackendStartupOverlay, UpdateNotification } from './components/common';
import ChatPage from './pages/ChatPage';
import TasksPage from './pages/TasksPage';
import AgentsPage from './pages/AgentsPage';
import SkillsPage from './pages/SkillsPage';
import MCPPage from './pages/MCPPage';
import PluginsPage from './pages/PluginsPage';
import ChannelsPage from './pages/ChannelsPage';
import DashboardPage from './pages/DashboardPage';
import SettingsPage from './pages/SettingsPage';

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
        {!isDev && <BackendStartupOverlay />}
        {/* Update notification - only shown in production mode */}
        {!isDev && <UpdateNotification />}
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<DashboardPage />} />
              <Route path="chat" element={<ChatPage />} />
              <Route path="tasks" element={<TasksPage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="skills" element={<SkillsPage />} />
              <Route path="mcp" element={<MCPPage />} />
              <Route path="plugins" element={<PluginsPage />} />
              <Route path="channels" element={<ChannelsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
