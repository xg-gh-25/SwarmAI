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
 *   ③ Files          — files touched this session (session context)
 *   ⚡ PipelinesBar  — bottom FYI bar: RUNNING pipelines, read-only, not clickable.
 *
 * The prior briefing feed (Working/Signals/Hot/Output/Artifacts/Stocks + the 60s
 * SessionBriefing poll + JobsBar) was removed from the sidebar — those feed
 * sections still live on the WelcomeScreen. The attention queue is aggregated by
 * useRadarAttention from three pre-existing, pure-read backend sources with zero
 * backend changes.
 *
 * @exports RadarSidebar
 */

import { useState, useEffect, useCallback } from 'react';
import type { RadarSidebarProps } from './types';
import { RADAR_SIDEBAR_WIDTH_KEY } from './types';
import { CollapsibleSection } from './shared/CollapsibleSection';
import { TodoSection } from './TodoSection';
import { ChangesSection } from './ChangesSection';
import { AttentionSection } from './AttentionSection';
import { PipelinesBar } from './PipelinesBar';
import { useReferencedFiles } from '../../../../hooks/useReferencedFiles';
import { useRadarAttention } from '../../../../hooks/useRadarAttention';
import { HistoryPopover } from './HistoryPopover';

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
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
  workspaceId,
  sessionId,
  onItemClick,
  onSelectTab,
  openTabs = [],
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

  // Section counts
  const [todoCount, setTodoCount] = useState(0);

  // Attention queue + running-pipeline FYI list (3 pure-read sources, polled).
  const { attentionItems, runningPipelines } = useRadarAttention(sessionId, openTabs);

  // Referenced Files tracking
  const { files: referencedFiles, totalCount: referencedCount } = useReferencedFiles(sessionId);

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
          <CollapsibleSection name="changes" icon="edit_note" label="改动" count={referencedFiles.written.length} defaultExpanded={true} accent="rgba(20,184,166,0.35)">
            <ChangesSection grouped={referencedFiles} totalCount={referencedCount} />
          </CollapsibleSection>
        )}
      </div>

      {/* ⚡ Running-pipeline FYI bar (bottom) — read-only, hides when none. */}
      <PipelinesBar running={runningPipelines} />
    </div>
  );
}
