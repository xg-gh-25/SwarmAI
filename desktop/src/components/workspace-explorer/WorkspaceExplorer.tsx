import { useState, useCallback, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLayout } from '../../contexts/LayoutContext';
import { swarmWorkspacesService } from '../../services/swarmWorkspaces';
import { sectionsService } from '../../services/sections';
import { workspaceConfigService } from '../../services/workspaceConfig';
import WorkspaceHeader from './WorkspaceHeader';
import OverviewContextCard, {
  type ContextData,
  parseContextMd,
  serializeContextMd,
} from './OverviewContextCard';
import SectionNavigation from './SectionNavigation';
import WorkspaceFooter from './WorkspaceFooter';
import ArtifactsFileTree from './ArtifactsFileTree';
import ResizeHandle from './ResizeHandle';
import type { FileTreeItem } from './FileTreeNode';
import RecommendedGroup from './RecommendedGroup';
import { useViewScope } from '../../hooks/useViewScope';
import type { SwarmWorkspace } from '../../types';
import type { WorkspaceSection, SectionCounts } from '../../types/section';

/**
 * WorkspaceExplorer component - middle column of the three-column layout.
 *
 * Refactored to use section-based navigation following the Daily Work Operating Loop:
 * Signals → Plan → Execute → Communicate → Artifacts → Reflection
 *
 * Requirements: 3.1, 9.1
 */

export interface WorkspaceExplorerProps {
  collapsed?: boolean;
  width?: number;
  onCollapsedChange?: (collapsed: boolean) => void;
  onWidthChange?: (width: number) => void;
  onSectionClick?: (section: WorkspaceSection) => void;
  onSubCategoryClick?: (section: WorkspaceSection, subCategory: string) => void;
  onSearch?: (query: string) => void;
  onFileDoubleClick?: (file: FileTreeItem) => void;
}

const EMPTY_COUNTS: SectionCounts = {
  signals: { total: 0, pending: 0, overdue: 0, inDiscussion: 0 },
  plan: { total: 0, today: 0, upcoming: 0, blocked: 0 },
  execute: { total: 0, draft: 0, wip: 0, blocked: 0, completed: 0 },
  communicate: { total: 0, pendingReply: 0, aiDraft: 0, followUp: 0 },
  artifacts: { total: 0, plan: 0, report: 0, doc: 0, decision: 0 },
  reflection: { total: 0, dailyRecap: 0, weeklySummary: 0, lessonsLearned: 0 },
};

export default function WorkspaceExplorer({
  collapsed: controlledCollapsed,
  width: controlledWidth,
  onCollapsedChange,
  onWidthChange,
  onSectionClick,
  onSubCategoryClick,
  onSearch,
  onFileDoubleClick: _onFileDoubleClick,
}: WorkspaceExplorerProps) {
  const {
    workspaceExplorerCollapsed,
    workspaceExplorerWidth,
    setWorkspaceExplorerWidth,
    setWorkspaceExplorerCollapsed,
    isNarrowViewport,
    openModal,
    setWorkspaceSettingsId,
  } = useLayout();

  const isCollapsed = controlledCollapsed ?? workspaceExplorerCollapsed;
  const explorerWidth = controlledWidth ?? workspaceExplorerWidth;

  // Workspace selection state - default to first workspace
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>('');
  const [activeSection, setActiveSection] = useState<WorkspaceSection | null>(null);
  const [isEditingContext, setIsEditingContext] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  // Fetch workspaces (include archived when toggle is on)
  const { data: workspaces = [], isLoading: wsLoading } = useQuery<SwarmWorkspace[]>({
    queryKey: ['swarmWorkspaces', showArchived],
    queryFn: () => swarmWorkspacesService.list(showArchived || undefined),
  });

  // Set default selected workspace when workspaces load
  useEffect(() => {
    if (workspaces.length > 0 && !selectedWorkspaceId) {
      const defaultWs = workspaces.find((w) => w.isDefault);
      setSelectedWorkspaceId(defaultWs?.id ?? workspaces[0].id);
    }
  }, [workspaces, selectedWorkspaceId]);

  // Get selected workspace object
  const selectedWorkspace = workspaces.find((w) => w.id === selectedWorkspaceId);

  // View scope management via hook (Requirements: 37.1-37.7)
  const { viewScope, setViewScope, isGlobalView, effectiveWorkspaceId } = useViewScope({
    isDefaultWorkspace: selectedWorkspace?.isDefault ?? true,
    selectedWorkspaceId,
  });

  // Fetch section counts
  const { data: sectionCounts = EMPTY_COUNTS } = useQuery<SectionCounts>({
    queryKey: ['sectionCounts', effectiveWorkspaceId],
    queryFn: () => sectionsService.getCounts(effectiveWorkspaceId),
    enabled: !!effectiveWorkspaceId,
  });

  // Fetch workspace context
  const { data: contextContent = '' } = useQuery<string>({
    queryKey: ['workspaceContext', selectedWorkspaceId],
    queryFn: () => workspaceConfigService.getContext(selectedWorkspaceId),
    enabled: !!selectedWorkspaceId && viewScope === 'scoped',
  });

  const contextData: ContextData = contextContent
    ? parseContextMd(contextContent)
    : {};

  // Handlers
  const handleWidthChange = useCallback(
    (newWidth: number) => {
      if (onWidthChange) {
        onWidthChange(newWidth);
      } else {
        setWorkspaceExplorerWidth(newWidth);
      }
    },
    [onWidthChange, setWorkspaceExplorerWidth]
  );

  const handleCollapseToggle = useCallback(() => {
    const newCollapsed = !isCollapsed;
    if (onCollapsedChange) {
      onCollapsedChange(newCollapsed);
    } else {
      setWorkspaceExplorerCollapsed(newCollapsed);
    }
  }, [isCollapsed, onCollapsedChange, setWorkspaceExplorerCollapsed]);

  const handleWorkspaceChange = useCallback((wsId: string) => {
    setSelectedWorkspaceId(wsId);
    setActiveSection(null);
  }, []);

  const handleSectionClick = useCallback(
    (section: WorkspaceSection) => {
      setActiveSection(section);
      onSectionClick?.(section);
    },
    [onSectionClick]
  );

  const handleContextSave = useCallback(
    async (data: ContextData) => {
      if (!selectedWorkspaceId) return;
      try {
        const md = serializeContextMd(data);
        await workspaceConfigService.updateContext(selectedWorkspaceId, md);
      } catch (err) {
        console.error('Failed to save context:', err);
      }
    },
    [selectedWorkspaceId]
  );

  const handleNewWorkspace = useCallback(() => {
    openModal('workspaces');
  }, [openModal]);

  const handleSettings = useCallback(() => {
    if (selectedWorkspaceId) {
      setWorkspaceSettingsId(selectedWorkspaceId);
    }
    openModal('workspace-settings');
  }, [openModal, selectedWorkspaceId, setWorkspaceSettingsId]);

  const handleArchive = useCallback(async () => {
    if (!selectedWorkspaceId) return;
    try {
      await swarmWorkspacesService.archive(selectedWorkspaceId);
    } catch (err) {
      console.error('Failed to archive workspace:', err);
    }
  }, [selectedWorkspaceId]);

  const handleUnarchive = useCallback(async () => {
    if (!selectedWorkspaceId) return;
    try {
      await swarmWorkspacesService.unarchive(selectedWorkspaceId);
    } catch (err) {
      console.error('Failed to unarchive workspace:', err);
    }
  }, [selectedWorkspaceId]);

  const handleDelete = useCallback(async () => {
    if (!selectedWorkspaceId) return;
    if (!window.confirm('Are you sure you want to delete this workspace? This cannot be undone.')) {
      return;
    }
    try {
      await swarmWorkspacesService.delete(selectedWorkspaceId);
      // Reset to first workspace
      const remaining = workspaces.filter((w) => w.id !== selectedWorkspaceId);
      if (remaining.length > 0) {
        setSelectedWorkspaceId(remaining[0].id);
      }
    } catch (err) {
      console.error('Failed to delete workspace:', err);
    }
  }, [selectedWorkspaceId, workspaces]);

  // Render extra content for artifacts section (file tree)
  const renderSectionExtra = useCallback(
    (section: WorkspaceSection) => {
      if (section === 'artifacts') {
        return (
          <ArtifactsFileTree
            workspacePath={selectedWorkspace?.filePath}
          />
        );
      }
      return null;
    },
    [selectedWorkspace?.filePath]
  );

  // Collapsed state
  if (isCollapsed) {
    return (
      <div
        className="flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] transition-all duration-200 ease-in-out"
        style={{ width: 24 }}
        data-testid="workspace-explorer-collapsed"
      >
        <button
          onClick={handleCollapseToggle}
          className={`w-6 h-full flex items-center justify-center transition-all duration-200 ease-in-out ${
            isNarrowViewport
              ? 'text-[var(--color-text-muted)] cursor-not-allowed opacity-50'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          }`}
          title={isNarrowViewport ? 'Expand disabled (window too narrow)' : 'Expand workspace explorer'}
          disabled={isNarrowViewport}
          aria-label="Expand workspace explorer"
          aria-expanded="false"
          data-testid="expand-button"
        >
          <span className="material-symbols-outlined text-sm">chevron_right</span>
        </button>
      </div>
    );
  }

  // Expanded state
  return (
    <div
      className="relative flex-shrink-0 bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col transition-all duration-200 ease-in-out"
      style={{ width: explorerWidth, minWidth: 200, maxWidth: 500 }}
      data-testid="workspace-explorer"
    >
      <ResizeHandle currentWidth={explorerWidth} onWidthChange={handleWidthChange} />

      {/* Header: workspace selector, scope toggle, search */}
      <WorkspaceHeader
        workspaces={workspaces}
        selectedWorkspaceId={selectedWorkspaceId}
        viewScope={viewScope}
        isLoading={wsLoading}
        showArchived={showArchived}
        onWorkspaceChange={handleWorkspaceChange}
        onViewScopeChange={setViewScope}
        onShowArchivedChange={setShowArchived}
        onSearch={onSearch}
        onCollapseToggle={handleCollapseToggle}
      />

      {/* Read-only banner for archived workspaces (Requirement 36.6) */}
      {selectedWorkspace?.isArchived && (
        <div
          className="mx-3 mt-2 px-3 py-2 rounded text-xs bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-muted)] flex items-center gap-2"
          data-testid="archive-readonly-banner"
          role="status"
        >
          <span>📦</span>
          <span>This workspace is archived (read-only). Unarchive to make changes.</span>
        </div>
      )}

      {/* Overview Context Card (only in scoped view) */}
      {viewScope === 'scoped' && selectedWorkspaceId && (
        <OverviewContextCard
          contextData={contextData}
          isEditing={isEditingContext}
          onEditToggle={() => setIsEditingContext(!isEditingContext)}
          onSave={handleContextSave}
        />
      )}

      {/* Recommended group - only in SwarmWS Global View (opinionated cockpit) */}
      {isGlobalView && selectedWorkspace?.isDefault && (
        <RecommendedGroup
          counts={sectionCounts}
          onItemClick={(section) => handleSectionClick(section as WorkspaceSection)}
        />
      )}

      {/* Section Navigation */}
      <SectionNavigation
        counts={sectionCounts}
        activeSection={activeSection}
        effectiveWorkspaceId={effectiveWorkspaceId}
        onSectionClick={handleSectionClick}
        onSubCategoryClick={onSubCategoryClick}
        renderSectionExtra={renderSectionExtra}
      />

      {/* Footer */}
      <WorkspaceFooter
        isDefaultWorkspace={selectedWorkspace?.isDefault ?? true}
        isArchived={selectedWorkspace?.isArchived ?? false}
        onNewWorkspace={handleNewWorkspace}
        onSettings={handleSettings}
        onArchive={handleArchive}
        onUnarchive={handleUnarchive}
        onDelete={handleDelete}
      />
    </div>
  );
}
