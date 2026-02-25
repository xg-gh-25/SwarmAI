/**
 * Unit tests for Section Pages (Task 19.8)
 * Tests rendering, filtering, search, and actions for all section pages.
 * Validates: Requirements 11.1-11.8
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// ============== Mocks ==============

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
  };
});

// Mock todosService
const mockTodosList = vi.fn();
const mockTodosDelete = vi.fn();
const mockTodosCreate = vi.fn();
const mockTodosUpdate = vi.fn();
const mockTodosConvertToTask = vi.fn();

vi.mock('../../services/todos', () => ({
  todosService: {
    list: (...args: unknown[]) => mockTodosList(...args),
    delete: (...args: unknown[]) => mockTodosDelete(...args),
    create: (...args: unknown[]) => mockTodosCreate(...args),
    update: (...args: unknown[]) => mockTodosUpdate(...args),
    convertToTask: (...args: unknown[]) => mockTodosConvertToTask(...args),
  },
}));

// Mock sectionsService
const mockGetPlan = vi.fn();
const mockGetCommunicate = vi.fn();
const mockGetArtifacts = vi.fn();
const mockGetReflection = vi.fn();
const mockGetExecute = vi.fn();

vi.mock('../../services/sections', () => ({
  sectionsService: {
    getPlan: (...args: unknown[]) => mockGetPlan(...args),
    getCommunicate: (...args: unknown[]) => mockGetCommunicate(...args),
    getArtifacts: (...args: unknown[]) => mockGetArtifacts(...args),
    getReflection: (...args: unknown[]) => mockGetReflection(...args),
    getExecute: (...args: unknown[]) => mockGetExecute(...args),
  },
}));

// Mock tasksService
const mockTasksList = vi.fn();
const mockTasksCancel = vi.fn();
const mockTasksDelete = vi.fn();

vi.mock('../../services/tasks', () => ({
  tasksService: {
    list: (...args: unknown[]) => mockTasksList(...args),
    cancel: (...args: unknown[]) => mockTasksCancel(...args),
    delete: (...args: unknown[]) => mockTasksDelete(...args),
  },
}));

// Mock agentsService
vi.mock('../../services/agents', () => ({
  agentsService: {
    list: () => Promise.resolve([]),
  },
}));

// swarmWorkspacesService removed — singleton workspace model (task 12.9)

// ============== Test Helpers ==============

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
}

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

// ============== Mock Data ==============

const mockTodos = [
  {
    id: '1', workspaceId: 'ws1', title: 'Review PR', description: 'Review pull request',
    sourceType: 'manual' as const, status: 'pending' as const, priority: 'high' as const,
    dueDate: '2025-01-15', createdAt: '2025-01-01', updatedAt: '2025-01-01',
  },
  {
    id: '2', workspaceId: 'ws1', title: 'Fix bug', description: 'Fix login bug',
    sourceType: 'slack' as const, status: 'overdue' as const, priority: 'medium' as const,
    createdAt: '2025-01-01', updatedAt: '2025-01-01',
  },
  {
    id: '3', workspaceId: 'ws1', title: 'Write docs', description: 'API documentation',
    sourceType: 'manual' as const, status: 'handled' as const, priority: 'low' as const,
    createdAt: '2025-01-01', updatedAt: '2025-01-01',
  },
];

const mockSectionResponse = <T,>(groups: { name: string; items: T[] }[]) => ({
  counts: {},
  groups,
  pagination: { limit: 50, offset: 0, total: 0, hasMore: false },
  sortKeys: [],
  lastUpdatedAt: null,
});

// ============== SignalsPage Tests ==============

describe('SignalsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTodosList.mockResolvedValue(mockTodos);
    mockTodosCreate.mockResolvedValue({ id: '4', title: 'New signal' });
    mockTodosDelete.mockResolvedValue(undefined);
  });

  it('renders page with title and table headers', async () => {
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    expect(screen.getByRole('heading', { name: 'signals.title' })).toBeInTheDocument();
    expect(screen.getByText('signals.columns.title')).toBeInTheDocument();
    expect(screen.getByText('signals.columns.status')).toBeInTheDocument();
    expect(screen.getByText('signals.columns.priority')).toBeInTheDocument();
  });

  it('renders todos from service', async () => {
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    await waitFor(() => {
      expect(screen.getByText('Review PR')).toBeInTheDocument();
      expect(screen.getByText('Fix bug')).toBeInTheDocument();
      expect(screen.getByText('Write docs')).toBeInTheDocument();
    });
  });

  it('filters todos by search query', async () => {
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    await waitFor(() => {
      expect(screen.getByText('Review PR')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('signals.search');
    fireEvent.change(searchInput, { target: { value: 'bug' } });

    expect(screen.getByText('Fix bug')).toBeInTheDocument();
    expect(screen.queryByText('Review PR')).not.toBeInTheDocument();
    expect(screen.queryByText('Write docs')).not.toBeInTheDocument();
  });

  it('shows quick capture form when button clicked', async () => {
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    const quickCaptureBtn = screen.getByText('signals.quickCapture');
    fireEvent.click(quickCaptureBtn);

    expect(screen.getByPlaceholderText('signals.quickCapturePlaceholder')).toBeInTheDocument();
  });

  it('shows delete confirmation dialog', async () => {
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    await waitFor(() => {
      expect(screen.getByText('Review PR')).toBeInTheDocument();
    });

    // Click the first delete button
    const deleteButtons = screen.getAllByTitle('signals.actions.delete');
    fireEvent.click(deleteButtons[0]);

    expect(screen.getByText('signals.confirmDelete')).toBeInTheDocument();
  });

  it('renders empty state when no todos', async () => {
    mockTodosList.mockResolvedValue([]);
    const { default: SignalsPage } = await import('../SignalsPage');
    renderWithProviders(<SignalsPage />);

    await waitFor(() => {
      expect(screen.getByText('signals.empty')).toBeInTheDocument();
    });
  });
});

// ============== ExecutePage Tests ==============

describe('ExecutePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTasksList.mockResolvedValue([
      {
        id: 't1', workspaceId: 'ws1', agentId: 'a1', sessionId: null,
        status: 'wip' as const, title: 'Running task', description: null,
        priority: null, sourceTodoId: null, blockedReason: null,
        model: 'claude-3', createdAt: '2025-01-01', startedAt: '2025-01-01',
        completedAt: null, error: null, workDir: null,
      },
      {
        id: 't2', workspaceId: 'ws1', agentId: 'a1', sessionId: null,
        status: 'completed' as const, title: 'Done task', description: null,
        priority: null, sourceTodoId: null, blockedReason: null,
        model: null, createdAt: '2025-01-01', startedAt: '2025-01-01',
        completedAt: '2025-01-01', error: null, workDir: null,
      },
    ]);
  });

  it('renders page with title and table', async () => {
    const { default: ExecutePage } = await import('../ExecutePage');
    renderWithProviders(<ExecutePage />);

    expect(screen.getByRole('heading', { name: 'execute.title' })).toBeInTheDocument();
    expect(screen.getByText('execute.columns.name')).toBeInTheDocument();
  });

  it('renders tasks from service', async () => {
    const { default: ExecutePage } = await import('../ExecutePage');
    renderWithProviders(<ExecutePage />);

    await waitFor(() => {
      expect(screen.getByText('Running task')).toBeInTheDocument();
      expect(screen.getByText('Done task')).toBeInTheDocument();
    });
  });

  it('has new status filter options', async () => {
    const { default: ExecutePage } = await import('../ExecutePage');
    renderWithProviders(<ExecutePage />);

    const select = screen.getAllByRole('combobox')[0];
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value);
    expect(options).toContain('draft');
    expect(options).toContain('wip');
    expect(options).toContain('blocked');
    expect(options).toContain('completed');
    expect(options).toContain('cancelled');
  });

  it('filters tasks by search', async () => {
    const { default: ExecutePage } = await import('../ExecutePage');
    renderWithProviders(<ExecutePage />);

    await waitFor(() => {
      expect(screen.getByText('Running task')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('execute.search');
    fireEvent.change(searchInput, { target: { value: 'Done' } });

    expect(screen.getByText('Done task')).toBeInTheDocument();
    expect(screen.queryByText('Running task')).not.toBeInTheDocument();
  });

  it('shows cancel button only for wip tasks', async () => {
    const { default: ExecutePage } = await import('../ExecutePage');
    renderWithProviders(<ExecutePage />);

    await waitFor(() => {
      expect(screen.getByText('Running task')).toBeInTheDocument();
    });

    // Only one cancel button (for the wip task)
    const cancelButtons = screen.getAllByTitle('execute.actions.cancel');
    expect(cancelButtons).toHaveLength(1);
  });
});

// ============== PlanPage Tests ==============

describe('PlanPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPlan.mockResolvedValue(
      mockSectionResponse([
        {
          name: 'today',
          items: [
            { id: 'p1', workspaceId: 'ws1', title: 'Focus item', status: 'active', priority: 'high', focusType: 'today', sortOrder: 0, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
        {
          name: 'upcoming',
          items: [
            { id: 'p2', workspaceId: 'ws1', title: 'Later item', status: 'active', priority: 'low', focusType: 'upcoming', sortOrder: 0, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
      ])
    );
  });

  it('renders page with grouped plan items', async () => {
    const { default: PlanPage } = await import('../PlanPage');
    renderWithProviders(<PlanPage />);

    await waitFor(() => {
      expect(screen.getByText('Focus item')).toBeInTheDocument();
      expect(screen.getByText('Later item')).toBeInTheDocument();
    });

    expect(screen.getByText('plan.group.today')).toBeInTheDocument();
    expect(screen.getByText('plan.group.upcoming')).toBeInTheDocument();
  });

  it('filters plan items by search', async () => {
    const { default: PlanPage } = await import('../PlanPage');
    renderWithProviders(<PlanPage />);

    await waitFor(() => {
      expect(screen.getByText('Focus item')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('plan.search');
    fireEvent.change(searchInput, { target: { value: 'Later' } });

    expect(screen.getByText('Later item')).toBeInTheDocument();
    expect(screen.queryByText('Focus item')).not.toBeInTheDocument();
  });

  it('shows reorder buttons', async () => {
    const { default: PlanPage } = await import('../PlanPage');
    renderWithProviders(<PlanPage />);

    await waitFor(() => {
      expect(screen.getByText('Focus item')).toBeInTheDocument();
    });

    // Each item has up/down buttons
    expect(screen.getAllByTitle('plan.moveUp').length).toBeGreaterThan(0);
    expect(screen.getAllByTitle('plan.moveDown').length).toBeGreaterThan(0);
  });

  it('shows empty state when no items', async () => {
    mockGetPlan.mockResolvedValue(mockSectionResponse([]));
    const { default: PlanPage } = await import('../PlanPage');
    renderWithProviders(<PlanPage />);

    await waitFor(() => {
      expect(screen.getByText('plan.empty')).toBeInTheDocument();
    });
  });
});

// ============== CommunicatePage Tests ==============

describe('CommunicatePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCommunicate.mockResolvedValue(
      mockSectionResponse([
        {
          name: 'pendingReply',
          items: [
            { id: 'c1', workspaceId: 'ws1', title: 'Reply to Alice', recipient: 'alice@test.com', channelType: 'email', status: 'pendingReply', priority: 'high', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
        {
          name: 'aiDraft',
          items: [
            { id: 'c2', workspaceId: 'ws1', title: 'Draft for Bob', recipient: 'bob@test.com', channelType: 'slack', status: 'aiDraft', priority: 'medium', aiDraftContent: 'Hello Bob, here is the update...', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
      ])
    );
  });

  it('renders page with grouped communications', async () => {
    const { default: CommunicatePage } = await import('../CommunicatePage');
    renderWithProviders(<CommunicatePage />);

    await waitFor(() => {
      expect(screen.getByText('Reply to Alice')).toBeInTheDocument();
      expect(screen.getByText('Draft for Bob')).toBeInTheDocument();
    });
  });

  it('shows AI draft expandable section', async () => {
    const { default: CommunicatePage } = await import('../CommunicatePage');
    renderWithProviders(<CommunicatePage />);

    await waitFor(() => {
      expect(screen.getByText('Draft for Bob')).toBeInTheDocument();
    });

    // AI draft toggle should be visible
    const draftToggle = screen.getByText('communicate.aiDraft');
    expect(draftToggle).toBeInTheDocument();

    // Click to expand
    fireEvent.click(draftToggle);
    expect(screen.getByText('Hello Bob, here is the update...')).toBeInTheDocument();
  });

  it('filters by search', async () => {
    const { default: CommunicatePage } = await import('../CommunicatePage');
    renderWithProviders(<CommunicatePage />);

    await waitFor(() => {
      expect(screen.getByText('Reply to Alice')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('communicate.search');
    fireEvent.change(searchInput, { target: { value: 'Bob' } });

    expect(screen.getByText('Draft for Bob')).toBeInTheDocument();
    expect(screen.queryByText('Reply to Alice')).not.toBeInTheDocument();
  });

  it('shows empty state', async () => {
    mockGetCommunicate.mockResolvedValue(mockSectionResponse([]));
    const { default: CommunicatePage } = await import('../CommunicatePage');
    renderWithProviders(<CommunicatePage />);

    await waitFor(() => {
      expect(screen.getByText('communicate.empty')).toBeInTheDocument();
    });
  });
});

// ============== ArtifactsPage Tests ==============

describe('ArtifactsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetArtifacts.mockResolvedValue(
      mockSectionResponse([
        {
          name: 'doc',
          items: [
            { id: 'a1', workspaceId: 'ws1', artifactType: 'doc', title: 'API Guide', filePath: '/docs/api.md', version: 2, createdBy: 'user', tags: ['api', 'guide'], createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
        {
          name: 'report',
          items: [
            { id: 'a2', workspaceId: 'ws1', artifactType: 'report', title: 'Weekly Report', filePath: '/reports/weekly.md', version: 1, createdBy: 'agent', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
          ],
        },
      ])
    );
  });

  it('renders page with grouped artifacts', async () => {
    const { default: ArtifactsPage } = await import('../ArtifactsPage');
    renderWithProviders(<ArtifactsPage />);

    await waitFor(() => {
      expect(screen.getByText('API Guide')).toBeInTheDocument();
      expect(screen.getByText('Weekly Report')).toBeInTheDocument();
    });
  });

  it('displays version and tags', async () => {
    const { default: ArtifactsPage } = await import('../ArtifactsPage');
    renderWithProviders(<ArtifactsPage />);

    await waitFor(() => {
      expect(screen.getByText('API Guide')).toBeInTheDocument();
    });

    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('api')).toBeInTheDocument();
    expect(screen.getByText('guide')).toBeInTheDocument();
  });

  it('filters by type', async () => {
    const { default: ArtifactsPage } = await import('../ArtifactsPage');
    renderWithProviders(<ArtifactsPage />);

    await waitFor(() => {
      expect(screen.getByText('API Guide')).toBeInTheDocument();
    });

    const select = screen.getAllByRole('combobox')[0];
    fireEvent.change(select, { target: { value: 'report' } });

    expect(screen.getByText('Weekly Report')).toBeInTheDocument();
    expect(screen.queryByText('API Guide')).not.toBeInTheDocument();
  });

  it('filters by search including tags', async () => {
    const { default: ArtifactsPage } = await import('../ArtifactsPage');
    renderWithProviders(<ArtifactsPage />);

    await waitFor(() => {
      expect(screen.getByText('API Guide')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('artifacts.search');
    fireEvent.change(searchInput, { target: { value: 'api' } });

    expect(screen.getByText('API Guide')).toBeInTheDocument();
    expect(screen.queryByText('Weekly Report')).not.toBeInTheDocument();
  });
});

// ============== ReflectionPage Tests ==============

describe('ReflectionPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetReflection.mockResolvedValue(
      mockSectionResponse([
        {
          name: 'dailyRecap',
          items: [
            { id: 'r1', workspaceId: 'ws1', reflectionType: 'dailyRecap', title: 'Monday Recap', filePath: '/reflections/monday.md', periodStart: '2025-01-13', periodEnd: '2025-01-13', generatedBy: 'system', createdAt: '2025-01-13', updatedAt: '2025-01-13' },
          ],
        },
        {
          name: 'weeklySummary',
          items: [
            { id: 'r2', workspaceId: 'ws1', reflectionType: 'weeklySummary', title: 'Week 3 Summary', filePath: '/reflections/week3.md', periodStart: '2025-01-13', periodEnd: '2025-01-17', generatedBy: 'agent', createdAt: '2025-01-17', updatedAt: '2025-01-17' },
          ],
        },
      ])
    );
  });

  it('renders page with grouped reflections', async () => {
    const { default: ReflectionPage } = await import('../ReflectionPage');
    renderWithProviders(<ReflectionPage />);

    await waitFor(() => {
      expect(screen.getByText('Monday Recap')).toBeInTheDocument();
      expect(screen.getByText('Week 3 Summary')).toBeInTheDocument();
    });

    expect(screen.getByText('reflection.group.dailyRecap')).toBeInTheDocument();
    expect(screen.getByText('reflection.group.weeklySummary')).toBeInTheDocument();
  });

  it('filters by type', async () => {
    const { default: ReflectionPage } = await import('../ReflectionPage');
    renderWithProviders(<ReflectionPage />);

    await waitFor(() => {
      expect(screen.getByText('Monday Recap')).toBeInTheDocument();
    });

    const select = screen.getAllByRole('combobox')[0];
    fireEvent.change(select, { target: { value: 'weeklySummary' } });

    expect(screen.getByText('Week 3 Summary')).toBeInTheDocument();
    expect(screen.queryByText('Monday Recap')).not.toBeInTheDocument();
  });

  it('filters by search', async () => {
    const { default: ReflectionPage } = await import('../ReflectionPage');
    renderWithProviders(<ReflectionPage />);

    await waitFor(() => {
      expect(screen.getByText('Monday Recap')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText('reflection.search');
    fireEvent.change(searchInput, { target: { value: 'Week' } });

    expect(screen.getByText('Week 3 Summary')).toBeInTheDocument();
    expect(screen.queryByText('Monday Recap')).not.toBeInTheDocument();
  });

  it('shows empty state', async () => {
    mockGetReflection.mockResolvedValue(mockSectionResponse([]));
    const { default: ReflectionPage } = await import('../ReflectionPage');
    renderWithProviders(<ReflectionPage />);

    await waitFor(() => {
      expect(screen.getByText('reflection.empty')).toBeInTheDocument();
    });
  });
});
