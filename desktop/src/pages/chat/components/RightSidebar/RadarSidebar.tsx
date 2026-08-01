/**
 * Unified Radar sidebar — a live "what needs me / what did it just do" HUD.
 *
 * Redesign (Run 1): the sidebar is an attention queue, not a data feed. It
 * answers ONE question — "what should I be looking at right now?" — with three
 * sections plus a bottom FYI bar:
 *
 *   ① ToDo          — what you queued for yourself (top; you own the priority)
 *   ② 🔔 需要你      — the attention queue: paused pipelines (with the decision
 *                      they're blocked on), failing jobs, and background tabs
 *                      waiting on a question. Empty → the section disappears.
 *   ③ Changes        — files touched this session (session context)
 *   ④ ⚡ Jobs & Runs — the INVENTORY: every scheduled job (status + schedule +
 *                      last-run) and every ACTIVE pipeline run (running/paused
 *                      only; completed/failed/etc dropped as history). Replaces
 *                      the old bottom PipelinesBar (which was a pinned FYI bar);
 *                      this is a default-expanded section in the scroll stack.
 *                      The 🔔 queue owns the ACTIONABLE copies; this owns the rest.
 *
 * The prior briefing feed (Working/Signals/Hot/Output/Artifacts/Stocks + the 60s
 * SessionBriefing poll) was removed from the sidebar — those feed sections still
 * live on the WelcomeScreen. The attention queue is aggregated by
 * useRadarAttention; Jobs & Runs by useJobsRuns — both from pre-existing,
 * pure-read backend sources with zero backend changes.
 *
 * @exports RadarSidebar
 */

import { useState, useEffect } from 'react';
import type { RadarSidebarProps } from './types';
import { RADAR_SIDEBAR_WIDTH_KEY } from './types';
import { CollapsibleSection } from './shared/CollapsibleSection';
import { TodoSection } from './TodoSection';
import { ChangesSection } from './ChangesSection';
import { AttentionSection } from './AttentionSection';
import { JobsRunsSection } from './JobsRunsSection';
import { useReferencedFiles } from '../../../../hooks/useReferencedFiles';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_WIDTH = 320;
const MIN_WIDTH = 240;
const MAX_WIDTH = 600;

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
  workspaceId,
  sessionId,
  onItemClick,
  onSelectTab,
  attentionItems = [],
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

  // Section counts
  const [todoCount, setTodoCount] = useState(0);

  // Attention queue (3 pure-read sources) is now polled ONCE at ChatPage via
  // useRadarAttention and passed down as `attentionItems` — shared with the
  // ChatHeader Alerts pill so there is a SINGLE 30s poll (run_843962a5). The
  // running-pipeline FYI list is no longer surfaced here — active (running/
  // paused) runs live in the Jobs & Runs section (the single run-status inventory).

  // Referenced Files tracking
  const { files: referencedFiles, totalCount: referencedCount } = useReferencedFiles(sessionId);

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

      {/* Header. History search moved to the left-nav History row (HistoryOverlay). */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="flex items-center gap-1.5 flex-1">
          <span className="material-symbols-outlined text-[13px] text-[var(--color-text-secondary)]">radar</span>
          <span className="text-[11px] font-bold uppercase tracking-[0.6px] text-[var(--color-text-secondary)]">
            SwarmRadar
          </span>
        </span>
      </div>

      {/* Scrollable sections: ToDo → 🔔 需要你 → Files */}
      <div className="flex-1 overflow-y-auto">
        {/* ① ToDo — red (action urgency); you own the priority */}
        <CollapsibleSection name="todo" icon="checklist" label="ToDo" count={todoCount} defaultExpanded={true} accent="rgba(239,68,68,0.35)">
          <TodoSection workspaceId={workspaceId} onCountChange={setTodoCount} onItemClick={onItemClick} />
        </CollapsibleSection>

        {/* ② 🔔 需要你 — the attention queue (paused pipelines / failed jobs /
            waiting tabs). Renders null when empty (section disappears). */}
        <AttentionSection
          items={attentionItems}
          onItemClick={onItemClick}
          onSelectTab={onSelectTab}
        />

        {/* ③ Changes — teal (session context): files written/edited this session,
            with a git NEW/UPD badge; click → diff. Read/searched files dropped. */}
        {referencedCount > 0 && referencedFiles.written.length > 0 && (
          <CollapsibleSection name="changes" icon="edit_note" label="Changes" count={referencedFiles.written.length} defaultExpanded={true} accent="rgba(20,184,166,0.35)">
            <ChangesSection grouped={referencedFiles} totalCount={referencedCount} />
          </CollapsibleSection>
        )}

        {/* ④ ⚡ Jobs & Runs — indigo (inventory): all scheduled jobs + ACTIVE pipeline
            runs (running/paused only). Replaces the old bottom PipelinesBar;
            renders its own CollapsibleSection (default-expanded). Hides when empty. */}
        <JobsRunsSection />
      </div>
    </div>
  );
}
