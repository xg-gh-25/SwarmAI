import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { LayoutProvider } from '../../contexts/LayoutContext';
import SectionHeader, { SECTION_CONFIG } from './SectionHeader';
import SectionContent, { SECTION_SUB_CATEGORIES } from './SectionContent';
import SectionNavigation from './SectionNavigation';
import WorkspaceFooter from './WorkspaceFooter';
import ArtifactsFileTree from './ArtifactsFileTree';
import WorkspaceHeader from './WorkspaceHeader';
import OverviewContextCard, { parseContextMd, serializeContextMd } from './OverviewContextCard';
import type { SectionCounts } from '../../types/section';
import type { SwarmWorkspace } from '../../types';

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// Mock services
vi.mock('../../services/swarmWorkspaces', () => ({
  swarmWorkspacesService: {
    list: vi.fn().mockResolvedValue([]),
    archive: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('../../services/sections', () => ({
  sectionsService: {
    getCounts: vi.fn().mockResolvedValue({
      signals: { total: 0, pending: 0, overdue: 0, inDiscussion: 0 },
      plan: { total: 0, today: 0, upcoming: 0, blocked: 0 },
      execute: { total: 0, draft: 0, wip: 0, blocked: 0, completed: 0 },
      communicate: { total: 0, pendingReply: 0, aiDraft: 0, followUp: 0 },
      artifacts: { total: 0, plan: 0, report: 0, doc: 0, decision: 0 },
      reflection: { total: 0, dailyRecap: 0, weeklySummary: 0, lessonsLearned: 0 },
    }),
  },
}));

vi.mock('../../services/workspaceConfig', () => ({
  workspaceConfigService: {
    getContext: vi.fn().mockResolvedValue(''),
    updateContext: vi.fn().mockResolvedValue(undefined),
  },
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

function TestWrapper({ children }: { children: React.ReactNode }) {
  const qc = createTestQueryClient();
  return (
    <QueryClientProvider client={qc}>
      <LayoutProvider>{children}</LayoutProvider>
    </QueryClientProvider>
  );
}

const MOCK_COUNTS: SectionCounts = {
  signals: { total: 5, pending: 3, overdue: 1, inDiscussion: 1 },
  plan: { total: 4, today: 2, upcoming: 1, blocked: 1 },
  execute: { total: 8, draft: 2, wip: 3, blocked: 1, completed: 2 },
  communicate: { total: 3, pendingReply: 1, aiDraft: 1, followUp: 1 },
  artifacts: { total: 6, plan: 2, report: 1, doc: 2, decision: 1 },
  reflection: { total: 2, dailyRecap: 1, weeklySummary: 1, lessonsLearned: 0 },
};

const MOCK_WORKSPACES: SwarmWorkspace[] = [
  {
    id: 'ws-1', name: 'SwarmWS', filePath: '/path/SwarmWS', context: '',
    isDefault: true, isArchived: false, archivedAt: null,
    createdAt: '2025-01-01', updatedAt: '2025-01-01',
  },
  {
    id: 'ws-2', name: 'TestWS', filePath: '/path/TestWS', context: '',
    isDefault: false, isArchived: false, archivedAt: null,
    createdAt: '2025-01-02', updatedAt: '2025-01-02',
  },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ============== SectionHeader Tests ==============

describe('SectionHeader', () => {
  it('renders section icon, label, and count', () => {
    render(
      <SectionHeader
        section="signals"
        totalCount={5}
        isExpanded={false}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByTestId('section-header-signals')).toBeInTheDocument();
    expect(screen.getByText('Signals')).toBeInTheDocument();
    expect(screen.getByText('🔔')).toBeInTheDocument();
    expect(screen.getByTestId('section-count-signals')).toHaveTextContent('5');
  });

  it('does not show count badge when count is 0', () => {
    render(
      <SectionHeader
        section="plan"
        totalCount={0}
        isExpanded={false}
        onToggle={vi.fn()}
      />
    );
    expect(screen.queryByTestId('section-count-plan')).not.toBeInTheDocument();
  });

  it('calls onToggle when clicked', () => {
    const onToggle = vi.fn();
    render(
      <SectionHeader
        section="execute"
        totalCount={3}
        isExpanded={false}
        onToggle={onToggle}
      />
    );
    fireEvent.click(screen.getByTestId('section-header-execute'));
    expect(onToggle).toHaveBeenCalledWith('execute');
  });

  it('calls onToggle on Enter key', () => {
    const onToggle = vi.fn();
    render(
      <SectionHeader
        section="communicate"
        totalCount={1}
        isExpanded={false}
        onToggle={onToggle}
      />
    );
    fireEvent.keyDown(screen.getByTestId('section-header-communicate'), { key: 'Enter' });
    expect(onToggle).toHaveBeenCalledWith('communicate');
  });

  it('has correct aria-expanded attribute', () => {
    const { rerender } = render(
      <SectionHeader section="signals" totalCount={1} isExpanded={false} onToggle={vi.fn()} />
    );
    expect(screen.getByTestId('section-header-signals')).toHaveAttribute('aria-expanded', 'false');

    rerender(
      <SectionHeader section="signals" totalCount={1} isExpanded={true} onToggle={vi.fn()} />
    );
    expect(screen.getByTestId('section-header-signals')).toHaveAttribute('aria-expanded', 'true');
  });

  it('has config for all six sections', () => {
    const sections = ['signals', 'plan', 'execute', 'communicate', 'artifacts', 'reflection'] as const;
    sections.forEach((s) => {
      expect(SECTION_CONFIG[s]).toBeDefined();
      expect(SECTION_CONFIG[s].icon).toBeTruthy();
      expect(SECTION_CONFIG[s].label).toBeTruthy();
    });
  });
});

// ============== SectionContent Tests ==============

describe('SectionContent', () => {
  it('renders sub-categories for signals section', () => {
    render(
      <SectionContent
        section="signals"
        subCounts={{ pending: 3, overdue: 1, inDiscussion: 1 }}
      />
    );
    expect(screen.getByText('Pending')).toBeInTheDocument();
    expect(screen.getByText('Overdue')).toBeInTheDocument();
    expect(screen.getByText('In Discussion')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('calls onSubCategoryClick when a sub-category is clicked', () => {
    const onClick = vi.fn();
    render(
      <SectionContent
        section="plan"
        subCounts={{ today: 2, upcoming: 1, blocked: 0 }}
        onSubCategoryClick={onClick}
      />
    );
    fireEvent.click(screen.getByTestId('sub-category-plan-today'));
    expect(onClick).toHaveBeenCalledWith('plan', 'today');
  });

  it('has sub-category definitions for all six sections', () => {
    const sections = ['signals', 'plan', 'execute', 'communicate', 'artifacts', 'reflection'] as const;
    sections.forEach((s) => {
      expect(SECTION_SUB_CATEGORIES[s]).toBeDefined();
      expect(SECTION_SUB_CATEGORIES[s].length).toBeGreaterThan(0);
    });
  });
});

// ============== SectionNavigation Tests ==============

describe('SectionNavigation', () => {
  it('renders all six section headers', () => {
    render(<SectionNavigation counts={MOCK_COUNTS} />);
    expect(screen.getByTestId('section-header-signals')).toBeInTheDocument();
    expect(screen.getByTestId('section-header-plan')).toBeInTheDocument();
    expect(screen.getByTestId('section-header-execute')).toBeInTheDocument();
    expect(screen.getByTestId('section-header-communicate')).toBeInTheDocument();
    expect(screen.getByTestId('section-header-artifacts')).toBeInTheDocument();
    expect(screen.getByTestId('section-header-reflection')).toBeInTheDocument();
  });

  it('expands section content on click', () => {
    render(<SectionNavigation counts={MOCK_COUNTS} />);
    expect(screen.queryByTestId('section-content-signals')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('section-header-signals'));
    expect(screen.getByTestId('section-content-signals')).toBeInTheDocument();
  });

  it('collapses section content on second click', () => {
    render(<SectionNavigation counts={MOCK_COUNTS} />);
    fireEvent.click(screen.getByTestId('section-header-signals'));
    expect(screen.getByTestId('section-content-signals')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('section-header-signals'));
    expect(screen.queryByTestId('section-content-signals')).not.toBeInTheDocument();
  });

  it('calls onSectionClick callback', () => {
    const onSectionClick = vi.fn();
    render(<SectionNavigation counts={MOCK_COUNTS} onSectionClick={onSectionClick} />);
    fireEvent.click(screen.getByTestId('section-header-execute'));
    expect(onSectionClick).toHaveBeenCalledWith('execute');
  });

  it('renders extra content for artifacts section when expanded', () => {
    const renderExtra = vi.fn((section) =>
      section === 'artifacts' ? <div data-testid="extra-artifacts">Extra</div> : null
    );
    render(
      <SectionNavigation counts={MOCK_COUNTS} renderSectionExtra={renderExtra} />
    );
    fireEvent.click(screen.getByTestId('section-header-artifacts'));
    expect(screen.getByTestId('extra-artifacts')).toBeInTheDocument();
  });

  it('supports keyboard navigation between sections', () => {
    render(<SectionNavigation counts={MOCK_COUNTS} />);
    const signalsHeader = screen.getByTestId('section-header-signals');
    signalsHeader.focus();
    fireEvent.keyDown(signalsHeader, { key: 'ArrowDown' });
    // The focus should move to the next section (plan)
    expect(document.activeElement).toBe(screen.getByTestId('section-header-plan'));
  });
});

// ============== WorkspaceHeader Tests ==============

describe('WorkspaceHeader', () => {
  it('renders workspace selector with workspaces', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    const selector = screen.getByTestId('workspace-selector');
    expect(selector).toBeInTheDocument();
    expect(selector).toHaveValue('ws-1');
  });

  it('shows 🏠 for default workspace and 📁 for custom', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    const options = screen.getAllByRole('option');
    expect(options[0].textContent).toContain('🏠');
    expect(options[1].textContent).toContain('📁');
  });

  it('calls onWorkspaceChange when selection changes', () => {
    const onChange = vi.fn();
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={onChange}
        onViewScopeChange={vi.fn()}
      />
    );
    fireEvent.change(screen.getByTestId('workspace-selector'), { target: { value: 'ws-2' } });
    expect(onChange).toHaveBeenCalledWith('ws-2');
  });

  it('renders scope toggle buttons', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    expect(screen.getByTestId('scope-toggle-global')).toBeInTheDocument();
    expect(screen.getByTestId('scope-toggle-scoped')).toBeInTheDocument();
  });

  it('calls onViewScopeChange when toggle is clicked', () => {
    const onScopeChange = vi.fn();
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={onScopeChange}
      />
    );
    fireEvent.click(screen.getByTestId('scope-toggle-global'));
    expect(onScopeChange).toHaveBeenCalledWith('global');
  });

  it('renders search bar with placeholder', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    expect(screen.getByPlaceholderText('Search… (threads, tasks, signals, artifacts)')).toBeInTheDocument();
  });

  it('shows SwarmWS-specific labels when default workspace selected', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-1"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    expect(screen.getByTestId('scope-toggle-global')).toHaveTextContent('Global');
    expect(screen.getByTestId('scope-toggle-scoped')).toHaveTextContent('SwarmWS Only');
  });

  it('shows custom workspace labels when non-default workspace selected', () => {
    render(
      <WorkspaceHeader
        workspaces={MOCK_WORKSPACES}
        selectedWorkspaceId="ws-2"
        viewScope="scoped"
        onWorkspaceChange={vi.fn()}
        onViewScopeChange={vi.fn()}
      />
    );
    expect(screen.getByTestId('scope-toggle-global')).toHaveTextContent('All Workspaces');
    expect(screen.getByTestId('scope-toggle-scoped')).toHaveTextContent('This Workspace');
  });
});

// ============== OverviewContextCard Tests ==============

describe('OverviewContextCard', () => {
  it('renders context fields when data is provided', () => {
    render(
      <OverviewContextCard
        contextData={{
          goal: 'Ship v2',
          focus: 'Backend refactor',
          context: 'Working on workspace system',
          priorities: ['Fix bugs', 'Write tests'],
        }}
      />
    );
    expect(screen.getByText('Ship v2')).toBeInTheDocument();
    expect(screen.getByText('Backend refactor')).toBeInTheDocument();
    expect(screen.getByText('Fix bugs')).toBeInTheDocument();
  });

  it('shows empty state when no context data', () => {
    render(<OverviewContextCard contextData={{}} />);
    expect(screen.getByText('No workspace context set')).toBeInTheDocument();
    expect(screen.getByText('+ Add Context')).toBeInTheDocument();
  });

  it('shows edit form when Edit Context is clicked', () => {
    render(
      <OverviewContextCard
        contextData={{ goal: 'Ship v2' }}
      />
    );
    fireEvent.click(screen.getByTestId('edit-context-button'));
    expect(screen.getByTestId('save-context-button')).toBeInTheDocument();
    expect(screen.getByTestId('cancel-context-button')).toBeInTheDocument();
  });

  it('calls onSave with updated data', () => {
    const onSave = vi.fn();
    render(
      <OverviewContextCard
        contextData={{ goal: 'Ship v2' }}
        isEditing={true}
        onEditToggle={vi.fn()}
        onSave={onSave}
      />
    );
    fireEvent.click(screen.getByTestId('save-context-button'));
    expect(onSave).toHaveBeenCalled();
  });
});

describe('parseContextMd', () => {
  it('parses goal, focus, context, and priorities', () => {
    const md = `## Goal\nShip v2\n\n## Focus\nBackend\n\n## Context\nRefactoring\n\n## Priorities\n- Fix bugs\n- Write tests\n`;
    const result = parseContextMd(md);
    expect(result.goal).toBe('Ship v2');
    expect(result.focus).toBe('Backend');
    expect(result.context).toBe('Refactoring');
    expect(result.priorities).toEqual(['Fix bugs', 'Write tests']);
  });

  it('handles empty content', () => {
    expect(parseContextMd('')).toEqual({});
  });
});

describe('serializeContextMd', () => {
  it('serializes context data to markdown', () => {
    const md = serializeContextMd({
      goal: 'Ship v2',
      focus: 'Backend',
      priorities: ['Fix bugs'],
    });
    expect(md).toContain('## Goal');
    expect(md).toContain('Ship v2');
    expect(md).toContain('## Focus');
    expect(md).toContain('- Fix bugs');
  });
});

// ============== WorkspaceFooter Tests ==============

describe('WorkspaceFooter', () => {
  it('renders New Workspace and Settings buttons', () => {
    render(
      <WorkspaceFooter isDefaultWorkspace={true} />
    );
    expect(screen.getByTestId('new-workspace-button')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-settings-button')).toBeInTheDocument();
  });

  it('does not show context menu for default workspace', () => {
    render(<WorkspaceFooter isDefaultWorkspace={true} />);
    expect(screen.queryByTestId('workspace-more-button')).not.toBeInTheDocument();
  });

  it('shows context menu button for custom workspace', () => {
    render(<WorkspaceFooter isDefaultWorkspace={false} />);
    expect(screen.getByTestId('workspace-more-button')).toBeInTheDocument();
  });

  it('opens context menu with archive and delete options', () => {
    render(<WorkspaceFooter isDefaultWorkspace={false} />);
    fireEvent.click(screen.getByTestId('workspace-more-button'));
    expect(screen.getByTestId('workspace-context-menu')).toBeInTheDocument();
    expect(screen.getByTestId('archive-workspace-option')).toBeInTheDocument();
    expect(screen.getByTestId('delete-workspace-option')).toBeInTheDocument();
  });

  it('calls onArchive when archive option is clicked', () => {
    const onArchive = vi.fn();
    render(<WorkspaceFooter isDefaultWorkspace={false} onArchive={onArchive} />);
    fireEvent.click(screen.getByTestId('workspace-more-button'));
    fireEvent.click(screen.getByTestId('archive-workspace-option'));
    expect(onArchive).toHaveBeenCalled();
  });

  it('calls onNewWorkspace when button is clicked', () => {
    const onNew = vi.fn();
    render(<WorkspaceFooter isDefaultWorkspace={true} onNewWorkspace={onNew} />);
    fireEvent.click(screen.getByTestId('new-workspace-button'));
    expect(onNew).toHaveBeenCalled();
  });
});

// ============== ArtifactsFileTree Tests ==============

describe('ArtifactsFileTree', () => {
  it('renders Browse Files toggle', () => {
    render(<ArtifactsFileTree />);
    expect(screen.getByTestId('file-tree-toggle')).toBeInTheDocument();
    expect(screen.getByText('Browse Files')).toBeInTheDocument();
  });

  it('expands to show workspace folders on click', () => {
    render(<ArtifactsFileTree />);
    fireEvent.click(screen.getByTestId('file-tree-toggle'));
    expect(screen.getByTestId('file-node-Artifacts')).toBeInTheDocument();
    expect(screen.getByTestId('file-node-ContextFiles')).toBeInTheDocument();
    expect(screen.getByTestId('file-node-Transcripts')).toBeInTheDocument();
  });

  it('expands Artifacts folder to show subfolders', () => {
    render(<ArtifactsFileTree />);
    fireEvent.click(screen.getByTestId('file-tree-toggle'));
    fireEvent.click(screen.getByTestId('file-node-Artifacts'));
    expect(screen.getByTestId('file-node-Artifacts-Plans')).toBeInTheDocument();
    expect(screen.getByTestId('file-node-Artifacts-Reports')).toBeInTheDocument();
    expect(screen.getByTestId('file-node-Artifacts-Docs')).toBeInTheDocument();
    expect(screen.getByTestId('file-node-Artifacts-Decisions')).toBeInTheDocument();
  });

  it('collapses Browse Files on second click', () => {
    render(<ArtifactsFileTree />);
    fireEvent.click(screen.getByTestId('file-tree-toggle'));
    expect(screen.getByTestId('file-node-Artifacts')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('file-tree-toggle'));
    expect(screen.queryByTestId('file-node-Artifacts')).not.toBeInTheDocument();
  });
});
