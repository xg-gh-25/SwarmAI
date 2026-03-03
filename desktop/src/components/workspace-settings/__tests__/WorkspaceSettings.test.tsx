/**
 * Unit tests for Workspace Settings components (Task 20.7)
 * Tests tab switching, toggle interactions, knowledgebase CRUD, and privileged confirmation.
 * Validates: Requirements 20.1-20.9
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// ============== Mocks ==============

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, string>) => {
      if (params) return `${key} ${JSON.stringify(params)}`;
      return key;
    },
  }),
}));

// Mock workspaceConfigService
const mockGetSkills = vi.fn();
const mockUpdateSkills = vi.fn();
const mockGetMcps = vi.fn();
const mockUpdateMcps = vi.fn();
const mockGetKnowledgebases = vi.fn();
const mockAddKnowledgebase = vi.fn();
const mockUpdateKnowledgebase = vi.fn();
const mockDeleteKnowledgebase = vi.fn();

vi.mock('../../../services/workspaceConfig', () => ({
  workspaceConfigService: {
    getSkills: (...args: unknown[]) => mockGetSkills(...args),
    updateSkills: (...args: unknown[]) => mockUpdateSkills(...args),
    getMcps: (...args: unknown[]) => mockGetMcps(...args),
    updateMcps: (...args: unknown[]) => mockUpdateMcps(...args),
    getKnowledgebases: (...args: unknown[]) => mockGetKnowledgebases(...args),
    addKnowledgebase: (...args: unknown[]) => mockAddKnowledgebase(...args),
    updateKnowledgebase: (...args: unknown[]) => mockUpdateKnowledgebase(...args),
    deleteKnowledgebase: (...args: unknown[]) => mockDeleteKnowledgebase(...args),
  },
}));

// Mock skillsService
const mockSkillsList = vi.fn();
vi.mock('../../../services/skills', () => ({
  skillsService: {
    list: (...args: unknown[]) => mockSkillsList(...args),
  },
}));

// Mock mcpService
const mockMcpList = vi.fn();
vi.mock('../../../services/mcp', () => ({
  mcpService: {
    list: (...args: unknown[]) => mockMcpList(...args),
  },
}));

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

const mockSkillConfigs = [
  { id: 'sc1', workspaceId: 'ws1', skillId: 'skill-1', enabled: true, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
  { id: 'sc2', workspaceId: 'ws1', skillId: 'skill-2', enabled: false, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
];

const mockSkills = [
  { id: 'skill-1', folderName: 'skill-1', name: 'Code Analysis', description: 'Analyzes code', isSystem: false, version: '1.0', createdAt: '2025-01-01', updatedAt: '2025-01-01', sourceType: 'user', currentVersion: 1, hasDraft: false, readOnly: false },
  { id: 'skill-2', folderName: 'skill-2', name: 'File Access', description: 'Privileged file access', isSystem: false, version: '1.0', createdAt: '2025-01-01', updatedAt: '2025-01-01', sourceType: 'user', currentVersion: 1, hasDraft: false, readOnly: true },
];

const mockMcpConfigs = [
  { id: 'mc1', workspaceId: 'ws1', mcpServerId: 'mcp-1', enabled: true, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
  { id: 'mc2', workspaceId: 'ws1', mcpServerId: 'mcp-2', enabled: false, createdAt: '2025-01-01', updatedAt: '2025-01-01' },
];

const mockMcps = [
  { id: 'mcp-1', name: 'GitHub', description: 'GitHub integration', connectionType: 'stdio', config: {}, isSystem: false, sourceType: 'user', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
  { id: 'mcp-2', name: 'Database', description: 'DB access', connectionType: 'stdio', config: {}, isSystem: false, sourceType: 'user', createdAt: '2025-01-01', updatedAt: '2025-01-01', isPrivileged: true },
];

const mockKnowledgebases = [
  { id: 'kb1', workspaceId: 'ws1', sourceType: 'local_file', sourcePath: '/docs/api.md', displayName: 'API Docs', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
  { id: 'kb2', workspaceId: 'ws1', sourceType: 'url', sourcePath: 'https://example.com/docs', displayName: 'External Docs', createdAt: '2025-01-01', updatedAt: '2025-01-01' },
];

// ============== WorkspaceSettingsModal Tests ==============

describe('WorkspaceSettingsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSkills.mockResolvedValue(mockSkillConfigs);
    mockSkillsList.mockResolvedValue(mockSkills);
    mockGetMcps.mockResolvedValue(mockMcpConfigs);
    mockMcpList.mockResolvedValue(mockMcps);
    mockGetKnowledgebases.mockResolvedValue(mockKnowledgebases);
  });

  it('renders with three tabs and defaults to Skills tab', async () => {
    const { default: WorkspaceSettingsModal } = await import('../../modals/WorkspaceSettingsModal');
    renderWithProviders(
      <WorkspaceSettingsModal isOpen={true} onClose={vi.fn()} workspaceId="ws1" />
    );

    expect(screen.getByText('settings.tabs.skills')).toBeInTheDocument();
    expect(screen.getByText('settings.tabs.mcps')).toBeInTheDocument();
    expect(screen.getByText('settings.tabs.knowledgebases')).toBeInTheDocument();

    // Skills tab content should be visible (default)
    await waitFor(() => {
      expect(screen.getByText('Code Analysis')).toBeInTheDocument();
    });
  });

  it('switches between tabs', async () => {
    const { default: WorkspaceSettingsModal } = await import('../../modals/WorkspaceSettingsModal');
    renderWithProviders(
      <WorkspaceSettingsModal isOpen={true} onClose={vi.fn()} workspaceId="ws1" />
    );

    // Default: Skills tab
    await waitFor(() => {
      expect(screen.getByText('Code Analysis')).toBeInTheDocument();
    });

    // Switch to MCPs tab
    fireEvent.click(screen.getByText('settings.tabs.mcps'));
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeInTheDocument();
    });

    // Switch to Knowledgebases tab
    fireEvent.click(screen.getByText('settings.tabs.knowledgebases'));
    await waitFor(() => {
      expect(screen.getByText('API Docs')).toBeInTheDocument();
    });
  });

  it('does not render when isOpen is false', async () => {
    const { default: WorkspaceSettingsModal } = await import('../../modals/WorkspaceSettingsModal');
    renderWithProviders(
      <WorkspaceSettingsModal isOpen={false} onClose={vi.fn()} workspaceId="ws1" />
    );

    expect(screen.queryByText('settings.tabs.skills')).not.toBeInTheDocument();
  });
});

// ============== SkillsTab Tests ==============

describe('SkillsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSkills.mockResolvedValue(mockSkillConfigs);
    mockSkillsList.mockResolvedValue(mockSkills);
    mockUpdateSkills.mockResolvedValue(undefined);
  });

  it('renders skill list with toggle switches', async () => {
    const { default: SkillsTab } = await import('../SkillsTab');
    renderWithProviders(<SkillsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('Code Analysis')).toBeInTheDocument();
      expect(screen.getByText('File Access')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    expect(toggles).toHaveLength(2);
    expect(toggles[0]).toBeChecked(); // skill-1 enabled
    expect(toggles[1]).not.toBeChecked(); // skill-2 disabled
  });

  it('shows warning icon for privileged skills', async () => {
    const { default: SkillsTab } = await import('../SkillsTab');
    renderWithProviders(<SkillsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('File Access')).toBeInTheDocument();
    });

    // Privileged skill should have warning emoji
    expect(screen.getByTitle('settings.skills.privileged')).toBeInTheDocument();
  });

  it('calls updateSkills when toggling a non-privileged skill', async () => {
    const { default: SkillsTab } = await import('../SkillsTab');
    renderWithProviders(<SkillsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('Code Analysis')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    fireEvent.click(toggles[0]); // Toggle off Code Analysis

    await waitFor(() => {
      expect(mockUpdateSkills).toHaveBeenCalledWith('ws1', [{ skillId: 'skill-1', enabled: false }]);
    });
  });

  it('shows privileged confirmation when enabling a privileged skill', async () => {
    const { default: SkillsTab } = await import('../SkillsTab');
    renderWithProviders(<SkillsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('File Access')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    fireEvent.click(toggles[1]); // Toggle on File Access (privileged)

    // Should show privileged confirmation modal
    await waitFor(() => {
      expect(screen.getByText('settings.privileged.title')).toBeInTheDocument();
    });
  });

  it('shows empty state when no skills configured', async () => {
    mockGetSkills.mockResolvedValue([]);
    const { default: SkillsTab } = await import('../SkillsTab');
    renderWithProviders(<SkillsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('settings.skills.empty')).toBeInTheDocument();
    });
  });
});

// ============== McpsTab Tests ==============

describe('McpsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetMcps.mockResolvedValue(mockMcpConfigs);
    mockMcpList.mockResolvedValue(mockMcps);
    mockUpdateMcps.mockResolvedValue(undefined);
  });

  it('renders MCP list with toggle switches', async () => {
    const { default: McpsTab } = await import('../McpsTab');
    renderWithProviders(<McpsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeInTheDocument();
      expect(screen.getByText('Database')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    expect(toggles).toHaveLength(2);
    expect(toggles[0]).toBeChecked();
    expect(toggles[1]).not.toBeChecked();
  });

  it('shows warning icon for privileged MCPs', async () => {
    const { default: McpsTab } = await import('../McpsTab');
    renderWithProviders(<McpsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('Database')).toBeInTheDocument();
    });

    expect(screen.getByTitle('settings.mcps.privileged')).toBeInTheDocument();
  });

  it('calls updateMcps when toggling a non-privileged MCP', async () => {
    const { default: McpsTab } = await import('../McpsTab');
    renderWithProviders(<McpsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    fireEvent.click(toggles[0]);

    await waitFor(() => {
      expect(mockUpdateMcps).toHaveBeenCalledWith('ws1', [{ mcpServerId: 'mcp-1', enabled: false }]);
    });
  });

  it('shows privileged confirmation when enabling a privileged MCP', async () => {
    const { default: McpsTab } = await import('../McpsTab');
    renderWithProviders(<McpsTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('Database')).toBeInTheDocument();
    });

    const toggles = screen.getAllByRole('checkbox');
    fireEvent.click(toggles[1]);

    await waitFor(() => {
      expect(screen.getByText('settings.privileged.title')).toBeInTheDocument();
    });
  });
});

// ============== KnowledgebasesTab Tests ==============

describe('KnowledgebasesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetKnowledgebases.mockResolvedValue(mockKnowledgebases);
    mockAddKnowledgebase.mockResolvedValue({ id: 'kb3', sourceType: 'local_file', sourcePath: '/new', displayName: 'New KB' });
    mockDeleteKnowledgebase.mockResolvedValue(undefined);
  });

  it('renders knowledgebase list', async () => {
    const { default: KnowledgebasesTab } = await import('../KnowledgebasesTab');
    renderWithProviders(<KnowledgebasesTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('API Docs')).toBeInTheDocument();
      expect(screen.getByText('External Docs')).toBeInTheDocument();
    });
  });

  it('shows add form when add button clicked', async () => {
    const { default: KnowledgebasesTab } = await import('../KnowledgebasesTab');
    renderWithProviders(<KnowledgebasesTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('API Docs')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('settings.knowledgebases.add'));

    expect(screen.getByPlaceholderText('settings.knowledgebases.displayNamePlaceholder')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('settings.knowledgebases.sourcePathPlaceholder')).toBeInTheDocument();
  });

  it('submits new knowledgebase', async () => {
    const { default: KnowledgebasesTab } = await import('../KnowledgebasesTab');
    renderWithProviders(<KnowledgebasesTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('API Docs')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('settings.knowledgebases.add'));

    const nameInput = screen.getByPlaceholderText('settings.knowledgebases.displayNamePlaceholder');
    const pathInput = screen.getByPlaceholderText('settings.knowledgebases.sourcePathPlaceholder');

    fireEvent.change(nameInput, { target: { value: 'New KB' } });
    fireEvent.change(pathInput, { target: { value: '/docs/new.md' } });

    // Find the submit button (the second "add" button in the form)
    const buttons = screen.getAllByText('settings.knowledgebases.add');
    fireEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => {
      expect(mockAddKnowledgebase).toHaveBeenCalledWith('ws1', {
        sourceType: 'local_file',
        sourcePath: '/docs/new.md',
        displayName: 'New KB',
      });
    });
  });

  it('shows delete confirmation and deletes', async () => {
    const { default: KnowledgebasesTab } = await import('../KnowledgebasesTab');
    renderWithProviders(<KnowledgebasesTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('API Docs')).toBeInTheDocument();
    });

    // Click delete on first item
    const deleteButtons = screen.getAllByTitle('common.button.delete');
    fireEvent.click(deleteButtons[0]);

    // Confirm button should appear
    expect(screen.getByText('common.button.confirm')).toBeInTheDocument();

    fireEvent.click(screen.getByText('common.button.confirm'));

    await waitFor(() => {
      expect(mockDeleteKnowledgebase).toHaveBeenCalledWith('ws1', 'kb1');
    });
  });

  it('shows empty state when no knowledgebases', async () => {
    mockGetKnowledgebases.mockResolvedValue([]);
    const { default: KnowledgebasesTab } = await import('../KnowledgebasesTab');
    renderWithProviders(<KnowledgebasesTab workspaceId="ws1" />);

    await waitFor(() => {
      expect(screen.getByText('settings.knowledgebases.empty')).toBeInTheDocument();
    });
  });
});

// ============== PrivilegedCapabilityModal Tests ==============

describe('PrivilegedCapabilityModal', () => {
  it('renders warning with capability name and type', async () => {
    const { default: PrivilegedCapabilityModal } = await import('../../modals/PrivilegedCapabilityModal');
    renderWithProviders(
      <PrivilegedCapabilityModal
        isOpen={true}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        capabilityName="File Access"
        capabilityType="skill"
      />
    );

    expect(screen.getByText('settings.privileged.title')).toBeInTheDocument();
    expect(screen.getByText('settings.privileged.explanation')).toBeInTheDocument();
  });

  it('calls onConfirm when confirm button clicked', async () => {
    const onConfirm = vi.fn();
    const { default: PrivilegedCapabilityModal } = await import('../../modals/PrivilegedCapabilityModal');
    renderWithProviders(
      <PrivilegedCapabilityModal
        isOpen={true}
        onClose={vi.fn()}
        onConfirm={onConfirm}
        capabilityName="File Access"
        capabilityType="skill"
      />
    );

    fireEvent.click(screen.getByText('settings.privileged.confirm'));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('calls onClose when cancel button clicked', async () => {
    const onClose = vi.fn();
    const { default: PrivilegedCapabilityModal } = await import('../../modals/PrivilegedCapabilityModal');
    renderWithProviders(
      <PrivilegedCapabilityModal
        isOpen={true}
        onClose={onClose}
        onConfirm={vi.fn()}
        capabilityName="File Access"
        capabilityType="skill"
      />
    );

    fireEvent.click(screen.getByText('common.button.cancel'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('does not render when isOpen is false', async () => {
    const { default: PrivilegedCapabilityModal } = await import('../../modals/PrivilegedCapabilityModal');
    renderWithProviders(
      <PrivilegedCapabilityModal
        isOpen={false}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        capabilityName="File Access"
        capabilityType="skill"
      />
    );

    expect(screen.queryByText('settings.privileged.title')).not.toBeInTheDocument();
  });
});
