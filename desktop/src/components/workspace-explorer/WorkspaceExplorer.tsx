/**
 * WorkspaceExplorer component — middle column of the three-column layout.
 *
 * Refactored for the single-workspace (SwarmWS) model. All multi-workspace
 * listing, archive/unarchive/delete logic, workspace dropdown, and showArchived
 * toggle have been removed. The explorer always shows the singleton SwarmWS.
 *
 * Uses section-based navigation following the Daily Work Operating Loop:
 * Signals → Plan → Execute → Communicate → Artifacts → Reflection
 *
 * Requirements: 1.6, 3.1, 9.1, 27.2
 */

import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLayout } from '../../contexts/LayoutContext';
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
import type { WorkspaceSection, SectionCounts } from '../../types/section';

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

/** Hardcoded singleton workspace ID used until the new workspace service is wired (task 13.2). */
const SWARMWS_ID = 'swarmws';

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

  const [activeSection, setActiveSection] = useState<WorkspaceSection | null>(null);
  const [isEditingContext, setIsEditingContext] = useState(false);

  // Fetch section counts for the singleton workspace
  const { data: sectionCounts = EMPTY_COUNTS } = useQuery<SectionCounts>({
    queryKey: ['sectionCounts', SWARMWS_ID],
    queryFn: () => sectionsService.getCounts(SWARMWS_ID),
  });

  // Fetch workspace context
  const { data: contextContent = '' } = useQuery<string>({
    queryKey: ['workspaceContext', SWARMWS_ID],
    queryFn: () => workspaceConfigService.getContext(SWARMWS_ID),
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

  const handleSectionClick = useCallback(
    (section: WorkspaceSection) => {
      setActiveSection(section);
      onSectionClick?.(section);
    },
    [onSectionClick]
  );

  const handleContextSave = useCallback(
    async (data: ContextData) => {
      try {
        const md = serializeContextMd(data);
        await workspaceConfigService.updateContext(SWARMWS_ID, md);
      } catch (err) {
        console.error('Failed to save context:', err);
      }
    },
    []
  );

  const handleSettings = useCallback(() => {
    setWorkspaceSettingsId(SWARMWS_ID);
    openModal('workspace-settings');
  }, [openModal, setWorkspaceSettingsId]);

  // Render extra content for artifacts section (file tree)
  const renderSectionExtra = useCallback(
    (section: WorkspaceSection) => {
      if (section === 'artifacts') {
        return <ArtifactsFileTree />;
      }
      return null;
    },
    []
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

      {/* Header: simplified for singleton workspace */}
      <WorkspaceHeader
        workspaces={[]}
        selectedWorkspaceId={SWARMWS_ID}
        viewScope="scoped"
        isLoading={false}
        showArchived={false}
        onWorkspaceChange={() => {}}
        onViewScopeChange={() => {}}
        onShowArchivedChange={() => {}}
        onSearch={onSearch}
        onCollapseToggle={handleCollapseToggle}
      />

      {/* Overview Context Card */}
      <OverviewContextCard
        contextData={contextData}
        isEditing={isEditingContext}
        onEditToggle={() => setIsEditingContext(!isEditingContext)}
        onSave={handleContextSave}
      />

      {/* Recommended group */}
      <RecommendedGroup
        counts={sectionCounts}
        onItemClick={(section) => handleSectionClick(section as WorkspaceSection)}
      />

      {/* Section Navigation */}
      <SectionNavigation
        counts={sectionCounts}
        activeSection={activeSection}
        effectiveWorkspaceId={SWARMWS_ID}
        onSectionClick={handleSectionClick}
        onSubCategoryClick={onSubCategoryClick}
        renderSectionExtra={renderSectionExtra}
      />

      {/* Footer: simplified — no archive/delete/new workspace */}
      <WorkspaceFooter
        isDefaultWorkspace={true}
        isArchived={false}
        onSettings={handleSettings}
      />
    </div>
  );
}
