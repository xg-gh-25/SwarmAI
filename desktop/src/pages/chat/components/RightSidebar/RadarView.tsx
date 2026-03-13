/**
 * Radar mode view for the Radar sidebar.
 *
 * Composes the four Radar sections — ToDo, Artifacts, Sessions, Jobs —
 * each wrapped in a ``CollapsibleSection`` with section-specific config
 * (icon, label, default expansion state).  Props are passed through from
 * ``RadarSidebar`` to each child section.
 *
 * Section badge counts and status hints are placeholder values (``count=0``,
 * ``statusHint=""``) because each section fetches its own data internally.
 * These will be wired to real data once section-level state is lifted or
 * exposed via callbacks.
 *
 * Key exports:
 * - ``RadarView``       — The composed Radar mode component
 * - ``RadarViewProps``  — Props interface for RadarView
 */

import { CollapsibleSection } from './shared/CollapsibleSection';
import { TodoSection } from './TodoSection';
import { ArtifactsSection } from './ArtifactsSection';
import { SessionsSection } from './SessionsSection';
import { JobsSection } from './JobsSection';
import type { OpenTab } from '../../types';
import type { TabStatus } from '../../../../hooks/useUnifiedTabState';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

/** Props for the RadarView component, passed through from RadarSidebar. */
export interface RadarViewProps {
  /** Active workspace ID; null means no workspace selected. */
  workspaceId: string | null;
  /** Currently open tabs (derived view from useUnifiedTabState). */
  openTabs: OpenTab[];
  /** Per-tab status map (derived view from useUnifiedTabState). */
  tabStatuses: Record<string, TabStatus>;
  /** Callback to switch the active tab. */
  onTabSelect: (tabId: string) => void;
  /** Callback to switch the sidebar into History mode. */
  onSwitchToHistory: () => void;
  /** Callback invoked when the user clicks an artifact row. */
  onPreviewFile?: (path: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RadarView({
  workspaceId,
  openTabs,
  tabStatuses,
  onTabSelect,
  onSwitchToHistory,
  onPreviewFile,
}: RadarViewProps) {
  return (
    <div>
      {/* ToDo — expanded by default */}
      <CollapsibleSection
        name="todo"
        icon="checklist"
        label="ToDo"
        count={0}
        statusHint=""
        defaultExpanded={true}
      >
        <TodoSection workspaceId={workspaceId} />
      </CollapsibleSection>

      {/* Artifacts — collapsed by default */}
      <CollapsibleSection
        name="artifacts"
        icon="folder_open"
        label="Artifacts"
        count={0}
        statusHint=""
        defaultExpanded={false}
      >
        <ArtifactsSection
          workspaceId={workspaceId}
          onPreviewFile={onPreviewFile}
        />
      </CollapsibleSection>

      {/* Sessions — collapsed by default */}
      <CollapsibleSection
        name="sessions"
        icon="chat_bubble"
        label="Sessions"
        count={0}
        statusHint=""
        defaultExpanded={false}
      >
        <SessionsSection
          openTabs={openTabs}
          tabStatuses={tabStatuses}
          onTabSelect={onTabSelect}
          onSwitchToHistory={onSwitchToHistory}
        />
      </CollapsibleSection>

      {/* Jobs — collapsed by default */}
      <CollapsibleSection
        name="jobs"
        icon="smart_toy"
        label="Jobs"
        count={0}
        statusHint=""
        defaultExpanded={false}
      >
        <JobsSection />
      </CollapsibleSection>
    </div>
  );
}
