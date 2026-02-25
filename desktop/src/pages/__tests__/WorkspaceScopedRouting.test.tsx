/**
 * Integration tests for workspace-scoped routing (Task 26.6)
 *
 * Tests that workspace-scoped URLs (/workspaces/:workspaceId/signals, etc.)
 * render the correct section pages.
 *
 * Requirements: 15.1, 15.2, 15.3
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// ============== Mocks ==============

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

// Mock todosService
vi.mock('../../services/todos', () => ({
  todosService: {
    list: () => Promise.resolve([]),
    delete: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    convertToTask: vi.fn(),
  },
}));

// Mock sectionsService
vi.mock('../../services/sections', () => ({
  sectionsService: {
    getPlan: () => Promise.resolve({
      counts: {}, groups: [], pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
      sortKeys: [], lastUpdatedAt: null,
    }),
    getCommunicate: () => Promise.resolve({
      counts: {}, groups: [], pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
      sortKeys: [], lastUpdatedAt: null,
    }),
    getArtifacts: () => Promise.resolve({
      counts: {}, groups: [], pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
      sortKeys: [], lastUpdatedAt: null,
    }),
    getReflection: () => Promise.resolve({
      counts: {}, groups: [], pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
      sortKeys: [], lastUpdatedAt: null,
    }),
    getExecute: () => Promise.resolve({
      counts: {}, groups: [], pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
      sortKeys: [], lastUpdatedAt: null,
    }),
  },
}));

// Mock tasksService
vi.mock('../../services/tasks', () => ({
  tasksService: {
    list: () => Promise.resolve([]),
    cancel: vi.fn(),
    delete: vi.fn(),
  },
}));

// Mock agentsService
vi.mock('../../services/agents', () => ({
  agentsService: {
    list: () => Promise.resolve([]),
  },
}));

// swarmWorkspacesService removed — singleton workspace model (task 12.9)

// ============== Helpers ==============

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
}

// Pre-import section pages at module level (mocks are hoisted before imports)
import SignalsPage from '../SignalsPage';
import ExecutePage from '../ExecutePage';
import PlanPage from '../PlanPage';
import CommunicatePage from '../CommunicatePage';
import ArtifactsPage from '../ArtifactsPage';
import ReflectionPage from '../ReflectionPage';

function renderAtRoute(initialPath: string) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          {/* Non-scoped routes */}
          <Route path="/signals" element={<SignalsPage />} />
          <Route path="/execute" element={<ExecutePage />} />
          <Route path="/plan" element={<PlanPage />} />
          <Route path="/communicate" element={<CommunicatePage />} />
          <Route path="/artifacts" element={<ArtifactsPage />} />
          <Route path="/reflection" element={<ReflectionPage />} />
          {/* Workspace-scoped routes (Req 15.2) */}
          <Route path="/workspaces/:workspaceId/signals" element={<SignalsPage />} />
          <Route path="/workspaces/:workspaceId/execute" element={<ExecutePage />} />
          <Route path="/workspaces/:workspaceId/plan" element={<PlanPage />} />
          <Route path="/workspaces/:workspaceId/communicate" element={<CommunicatePage />} />
          <Route path="/workspaces/:workspaceId/artifacts" element={<ArtifactsPage />} />
          <Route path="/workspaces/:workspaceId/reflection" element={<ReflectionPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ============== Tests ==============

describe('Workspace-scoped routing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders SignalsPage at /workspaces/:id/signals', async () => {
    renderAtRoute('/workspaces/ws-123/signals');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'signals.title' })).toBeInTheDocument();
    });
  });

  it('renders ExecutePage at /workspaces/:id/execute', async () => {
    renderAtRoute('/workspaces/ws-123/execute');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'execute.title' })).toBeInTheDocument();
    });
  });

  it('renders PlanPage at /workspaces/:id/plan', async () => {
    renderAtRoute('/workspaces/ws-123/plan');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'plan.title' })).toBeInTheDocument();
    });
  });

  it('renders CommunicatePage at /workspaces/:id/communicate', async () => {
    renderAtRoute('/workspaces/ws-123/communicate');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'communicate.title' })).toBeInTheDocument();
    });
  });

  it('renders ArtifactsPage at /workspaces/:id/artifacts', async () => {
    renderAtRoute('/workspaces/ws-123/artifacts');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'artifacts.title' })).toBeInTheDocument();
    });
  });

  it('renders ReflectionPage at /workspaces/:id/reflection', async () => {
    renderAtRoute('/workspaces/ws-123/reflection');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'reflection.title' })).toBeInTheDocument();
    });
  });

  it('renders SignalsPage at non-scoped /signals route too', async () => {
    renderAtRoute('/signals');
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'signals.title' })).toBeInTheDocument();
    });
  });
});
