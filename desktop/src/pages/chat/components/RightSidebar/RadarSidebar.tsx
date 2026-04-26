/**
 * Unified Radar sidebar — single scrollable view with all briefing sections.
 *
 * D6: Mode toggle killed. History → search popover.
 * D7: Jobs = bottom status bar with expand.
 * D8: Section order = action priority gradient.
 *
 * Sections: Todo → Working → Signals → Hot → Stocks → Output → Jobs bar
 * Each section uses CollapsibleSection wrapper.
 * Empty sections auto-hide (D3).
 *
 * @exports RadarSidebar
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import type { RadarSidebarProps } from './types';
import { RADAR_SIDEBAR_WIDTH_KEY } from './types';
import { CollapsibleSection } from './shared/CollapsibleSection';
import { TodoSection } from './TodoSection';
import { ArtifactsSection } from './ArtifactsSection';
import {
  systemService,
  type SessionBriefing,
} from '../../../../services/system';
import {
  WorkingSection,
  SignalsSection,
  HotNewsSection,
  StocksSection,
  SwarmOutputSection,
  JobsBar,
} from '../briefing';
import { HistoryPopover } from './HistoryPopover';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_WIDTH = 320;
const MIN_WIDTH = 240;
const MAX_WIDTH = 600;
const BRIEFING_POLL_MS = 60_000; // 60s — match backend cache TTL

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readPersistedWidth(): number {
  try {
    const raw = localStorage.getItem(RADAR_SIDEBAR_WIDTH_KEY);
    if (raw !== null) {
      const parsed = parseInt(raw, 10);
      if (!Number.isNaN(parsed) && parsed >= MIN_WIDTH && parsed <= MAX_WIDTH) return parsed;
    }
  } catch { /* noop */ }
  return DEFAULT_WIDTH;
}

function persistWidth(width: number): void {
  try { localStorage.setItem(RADAR_SIDEBAR_WIDTH_KEY, String(width)); } catch { /* noop */ }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function RadarSidebar({
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
  workspaceId,
  onItemClick,
}: RadarSidebarProps) {
  // Auto-hide when file editor panel is open
  const [hiddenByEditorPanel, setHiddenByEditorPanel] = useState(false);
  useEffect(() => {
    const handler = (e: Event) => {
      const { open } = (e as CustomEvent<{ open: boolean }>).detail ?? {};
      setHiddenByEditorPanel(!!open);
    };
    window.addEventListener('swarm:editor-panel-state', handler);
    return () => window.removeEventListener('swarm:editor-panel-state', handler);
  }, []);

  // Width state
  const [width, setWidth] = useState<number>(readPersistedWidth);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => { persistWidth(width); }, [width]);

  useEffect(() => {
    if (!isResizing) return;
    const handleMouseMove = (e: MouseEvent) => {
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= MIN_WIDTH && newWidth <= MAX_WIDTH) setWidth(newWidth);
    };
    const handleMouseUp = () => setIsResizing(false);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // History popover
  const [historyOpen, setHistoryOpen] = useState(false);

  // Briefing data (polled every 60s)
  const [briefing, setBriefing] = useState<SessionBriefing | null>(null);

  const fetchBriefing = useCallback(async () => {
    try {
      const data = await systemService.getBriefing();
      setBriefing(data);
    } catch { /* graceful */ }
  }, []);

  useEffect(() => { fetchBriefing(); }, [fetchBriefing]);
  useEffect(() => {
    const id = setInterval(fetchBriefing, BRIEFING_POLL_MS);
    return () => clearInterval(id);
  }, [fetchBriefing]);

  // Section counts
  const [todoCount, setTodoCount] = useState(0);
  const [artifactCount, setArtifactCount] = useState(0);

  const workingCount = briefing?.working.length ?? 0;
  const signalsCount = briefing?.signals.length ?? 0;
  const hotCount = briefing?.hotNews.length ?? 0;
  const stocksCount = briefing?.stocks.length ?? 0;
  const outputCount = useMemo(() => {
    if (!briefing) return 0;
    return briefing.output.builds.length + briefing.output.content.length + briefing.output.files.length;
  }, [briefing]);

  // History popover session select → switch tab
  const handleHistorySelect = useCallback(
    (session: Parameters<typeof onSelectSession>[0]) => {
      setHistoryOpen(false);
      onSelectSession(session);
    },
    [onSelectSession],
  );

  if (hiddenByEditorPanel) return null;

  return (
    <div
      className="relative flex flex-col h-full border-l border-[var(--color-border)] bg-[var(--color-bg-secondary,var(--color-bg))]"
      style={{ width, minWidth: MIN_WIDTH, maxWidth: MAX_WIDTH }}
    >
      {/* Resize handle */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 cursor-ew-resize hover:bg-primary/30 transition-colors z-10"
        onMouseDown={(e) => { e.preventDefault(); setIsResizing(true); }}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
      />

      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="flex items-center gap-1.5 flex-1">
          <span className="material-symbols-outlined text-[13px] text-[var(--color-text-secondary)]">radar</span>
          <span className="text-[11px] font-bold uppercase tracking-[0.6px] text-[var(--color-text-secondary)]">
            SwarmRadar
          </span>
        </span>
        {/* History search button — uses onMouseDown to avoid race with popover's click-outside */}
        <div className="relative">
          <button
            onMouseDown={(e) => {
              e.stopPropagation(); // prevent popover's click-outside from firing first
              setHistoryOpen((prev) => !prev);
            }}
            className="p-1 rounded hover:bg-[var(--color-hover)] transition-colors"
            aria-label="Search chat history"
            title="Search history"
          >
            <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">search</span>
          </button>
          {historyOpen && (
            <HistoryPopover
              groupedSessions={groupedSessions}
              agents={agents}
              onSelectSession={handleHistorySelect}
              onDeleteSession={onDeleteSession}
              onClose={() => setHistoryOpen(false)}
            />
          )}
        </div>
      </div>

      {/* Scrollable sections */}
      <div className="flex-1 overflow-y-auto">
        {/* Todo */}
        <CollapsibleSection name="todo" icon="checklist" label="ToDo" count={todoCount} defaultExpanded={true}>
          <TodoSection workspaceId={workspaceId} onCountChange={setTodoCount} onItemClick={onItemClick} />
        </CollapsibleSection>

        {/* Working */}
        {workingCount > 0 && (
          <CollapsibleSection name="working" icon="assignment" label="Working" count={workingCount} defaultExpanded={true}>
            <WorkingSection items={briefing!.working} onItemClick={onItemClick} />
          </CollapsibleSection>
        )}

        {/* Signals */}
        {signalsCount > 0 && (
          <CollapsibleSection name="signals" icon="cell_tower" label="Signals" count={signalsCount} defaultExpanded={true}>
            <SignalsSection items={briefing!.signals} onItemClick={onItemClick} compact />
          </CollapsibleSection>
        )}

        {/* Hot News */}
        {hotCount > 0 && (
          <CollapsibleSection name="hot" icon="whatshot" label="Hot" count={hotCount} defaultExpanded={true}>
            <HotNewsSection items={briefing!.hotNews} onItemClick={onItemClick} compact />
          </CollapsibleSection>
        )}

        {/* Stocks */}
        {stocksCount > 0 && (
          <CollapsibleSection name="stocks" icon="trending_up" label="Stocks" count={stocksCount} defaultExpanded={false}>
            <StocksSection items={briefing!.stocks} compact />
          </CollapsibleSection>
        )}

        {/* Swarm Output */}
        {outputCount > 0 && (
          <CollapsibleSection name="output" icon="hive" label="Output" count={outputCount} defaultExpanded={false}>
            <SwarmOutputSection output={briefing!.output} compact />
          </CollapsibleSection>
        )}

        {/* Artifacts (existing — now under Output umbrella conceptually but kept separate for independent fetch) */}
        <CollapsibleSection name="artifacts" icon="folder_open" label="Artifacts" count={artifactCount} defaultExpanded={false}>
          <ArtifactsSection workspaceId={workspaceId} onCountChange={setArtifactCount} />
        </CollapsibleSection>
      </div>

      {/* Jobs status bar (bottom) */}
      {briefing?.jobsSummary && briefing.jobsSummary.total > 0 && (
        <JobsBar summary={briefing.jobsSummary} />
      )}
    </div>
  );
}
