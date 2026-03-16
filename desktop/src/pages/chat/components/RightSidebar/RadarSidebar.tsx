/**
 * Top-level Radar sidebar shell component.
 *
 * Persistent right-side panel, always visible when the chat page is mounted.
 * Manages two modes — Radar (default) and History — via local ``useState``.
 * Mode is NOT persisted to ``localStorage``; the sidebar always starts in
 * Radar mode on mount.
 *
 * Features:
 * - Header row with mode label, swap icon (Mode_Toggle), and 💡 Feature Tip
 * - Left-edge drag handle for horizontal resizing (mousedown/mousemove/mouseup)
 * - Width persisted to ``localStorage`` key ``radar-sidebar-width``
 * - Renders ``RadarView`` in radar mode, ``HistoryView`` in history mode
 *
 * Key exports:
 * - ``RadarSidebar`` — The persistent sidebar shell component
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import type { RadarSidebarProps } from './types';
import { RADAR_SIDEBAR_WIDTH_KEY, RADAR_TIP_DISMISSED_KEY } from './types';
import { RadarView } from './RadarView';
import { HistoryView } from './HistoryView';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_WIDTH = 320;
const MIN_WIDTH = 240;
const MAX_WIDTH = 600;

const FEATURE_TIP_TEXT =
  'SwarmRadar keeps you in the loop. ToDos show your pending tasks — ask your agent to create them or pull from Slack and email. Artifacts show recently changed files in your workspace — drag any item to chat. Sessions show your open tabs and their live status. Jobs display background automations. Drag items from ToDo or Artifacts into chat to reference them.';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Read persisted width from localStorage, falling back to default. */
function readPersistedWidth(): number {
  try {
    const raw = localStorage.getItem(RADAR_SIDEBAR_WIDTH_KEY);
    if (raw !== null) {
      const parsed = parseInt(raw, 10);
      if (!Number.isNaN(parsed) && parsed >= MIN_WIDTH && parsed <= MAX_WIDTH) {
        return parsed;
      }
    }
  } catch {
    // localStorage unavailable — use default
  }
  return DEFAULT_WIDTH;
}

/** Persist width to localStorage, silently ignoring errors. */
function persistWidth(width: number): void {
  try {
    localStorage.setItem(RADAR_SIDEBAR_WIDTH_KEY, String(width));
  } catch {
    // best-effort
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type SidebarMode = 'radar' | 'history';

export function RadarSidebar({
  openTabs,
  tabStatuses,
  onTabSelect,
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
  workspaceId,
}: RadarSidebarProps) {
  // -------------------------------------------------------------------------
  // Legacy localStorage key cleanup (Req 2.10) — runs once on first mount
  // -------------------------------------------------------------------------

  useEffect(() => {
    const legacyKeys = [
      'todoRadarSidebarWidth',
      'chatSidebarWidth',
      'rightSidebarWidth',
      'chatSidebarCollapsed',
      'rightSidebarCollapsed',
      'todoRadarSidebarCollapsed',
    ];
    try {
      for (const key of legacyKeys) {
        localStorage.removeItem(key);
      }
    } catch {
      // best-effort — localStorage may be unavailable
    }
  }, []);

  // -------------------------------------------------------------------------
  // Auto-hide when file editor panel is open (user focuses on doc, not radar)
  // -------------------------------------------------------------------------

  const [hiddenByEditorPanel, setHiddenByEditorPanel] = useState(false);

  useEffect(() => {
    const handleEditorPanelState = (e: Event) => {
      const { open } = (e as CustomEvent<{ open: boolean }>).detail ?? {};
      setHiddenByEditorPanel(!!open);
    };
    window.addEventListener('swarm:editor-panel-state', handleEditorPanelState);
    return () => window.removeEventListener('swarm:editor-panel-state', handleEditorPanelState);
  }, []);

  // -------------------------------------------------------------------------
  // Mode state — NOT persisted, always starts as 'radar'
  // -------------------------------------------------------------------------

  const [mode, setMode] = useState<SidebarMode>('radar');

  // -------------------------------------------------------------------------
  // Feature Tip state
  // -------------------------------------------------------------------------

  const [tipDismissed, setTipDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(RADAR_TIP_DISMISSED_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const [tipOpen, setTipOpen] = useState(false);
  const tipButtonRef = useRef<HTMLButtonElement>(null);
  const tipPopoverRef = useRef<HTMLDivElement>(null);

  // -------------------------------------------------------------------------
  // Width state — persisted to localStorage
  // -------------------------------------------------------------------------

  const [width, setWidth] = useState<number>(readPersistedWidth);
  const [isResizing, setIsResizing] = useState(false);

  // Persist width whenever it changes
  useEffect(() => {
    persistWidth(width);
  }, [width]);

  // -------------------------------------------------------------------------
  // Resize logic (left-edge drag handle)
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Sidebar is on the right — width = viewport right edge minus cursor X
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= MIN_WIDTH && newWidth <= MAX_WIDTH) {
        setWidth(newWidth);
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

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

  const handleResizeMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsResizing(true);
    },
    [],
  );

  // -------------------------------------------------------------------------
  // Feature Tip handlers
  // -------------------------------------------------------------------------

  const handleTipClick = useCallback(() => {
    setTipOpen((prev) => !prev);
  }, []);

  const handleTipMouseEnter = useCallback(() => {
    if (!tipDismissed) {
      setTipOpen(true);
    }
  }, [tipDismissed]);

  const handleTipMouseLeave = useCallback(() => {
    if (!tipDismissed) {
      setTipOpen(false);
    }
  }, [tipDismissed]);

  const handleDismissTip = useCallback(() => {
    try {
      localStorage.setItem(RADAR_TIP_DISMISSED_KEY, 'true');
    } catch {
      // best-effort
    }
    setTipDismissed(true);
    setTipOpen(false);
  }, []);

  // Close popover on click outside
  useEffect(() => {
    if (!tipOpen) return;

    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        tipPopoverRef.current &&
        !tipPopoverRef.current.contains(target) &&
        tipButtonRef.current &&
        !tipButtonRef.current.contains(target)
      ) {
        setTipOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [tipOpen]);

  // -------------------------------------------------------------------------
  // Mode toggle
  // -------------------------------------------------------------------------

  const toggleMode = useCallback(() => {
    setMode((prev) => (prev === 'radar' ? 'history' : 'radar'));
  }, []);

  // -------------------------------------------------------------------------
  // History → Radar on session select
  // -------------------------------------------------------------------------

  const handleHistorySelectSession = useCallback(
    (session: Parameters<typeof onSelectSession>[0]) => {
      setMode('radar');
      onSelectSession(session);
    },
    [onSelectSession],
  );

  // -------------------------------------------------------------------------
  // Switch to history from RadarView (e.g. "Chat History" link)
  // -------------------------------------------------------------------------

  const handleSwitchToHistory = useCallback(() => {
    setMode('history');
  }, []);

  // -------------------------------------------------------------------------
  // Back from history to radar
  // -------------------------------------------------------------------------

  const handleBackToRadar = useCallback(() => {
    setMode('radar');
  }, []);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  // When file editor panel is open, hide the radar sidebar entirely
  if (hiddenByEditorPanel) {
    return null;
  }

  return (
    <div
      className="relative flex flex-col h-full border-l border-[var(--color-border)]
        bg-[var(--color-bg-secondary,var(--color-bg))]"
      style={{ width, minWidth: MIN_WIDTH, maxWidth: MAX_WIDTH }}
    >
      {/* Left-edge resize handle */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1 cursor-ew-resize
          hover:bg-primary/30 transition-colors z-10"
        onMouseDown={handleResizeMouseDown}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
      />

      {/* Header row: mode label, toggle, feature tip */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[var(--color-text-muted)] flex-1">
          {mode === 'radar' ? 'Radar' : 'History'}
        </span>

        {/* Mode toggle button */}
        <button
          onClick={toggleMode}
          className="p-1 rounded hover:bg-[var(--color-hover)] transition-colors"
          aria-label={
            mode === 'radar' ? 'Switch to History mode' : 'Switch to Radar mode'
          }
          title={
            mode === 'radar' ? 'Switch to History' : 'Switch to Radar'
          }
        >
          <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
            swap_horiz
          </span>
        </button>

        {/* Feature Tip icon with popover */}
        <div
          className="relative"
          onMouseEnter={handleTipMouseEnter}
          onMouseLeave={handleTipMouseLeave}
        >
          <button
            ref={tipButtonRef}
            onClick={handleTipClick}
            className="p-1 rounded hover:bg-[var(--color-hover)] transition-colors"
            aria-label="Feature tips"
            title="Feature tips"
            aria-expanded={tipOpen}
            aria-haspopup="true"
          >
            <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
              lightbulb
            </span>
          </button>

          {tipOpen && (
            <div
              ref={tipPopoverRef}
              role="tooltip"
              className="absolute right-0 top-full mt-2 w-72 rounded-lg shadow-lg border
                border-[var(--color-border)] bg-[var(--color-bg)] p-3 z-50"
            >
              {/* Arrow pointing up */}
              <div
                className="absolute -top-2 right-3 w-0 h-0
                  border-l-[8px] border-l-transparent
                  border-r-[8px] border-r-transparent
                  border-b-[8px] border-b-[var(--color-border)]"
              />
              <div
                className="absolute -top-[7px] right-3 w-0 h-0
                  border-l-[8px] border-l-transparent
                  border-r-[8px] border-r-transparent
                  border-b-[8px] border-b-[var(--color-bg)]"
              />

              <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
                {FEATURE_TIP_TEXT}
              </p>

              {!tipDismissed && (
                <button
                  onClick={handleDismissTip}
                  className="mt-2 text-xs text-[var(--color-primary,#2b6cee)] hover:underline"
                >
                  Don&apos;t show again
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Scrollable content area */}
      <div className="flex-1 overflow-y-auto">
        {mode === 'radar' ? (
          <RadarView
            workspaceId={workspaceId}
            openTabs={openTabs}
            tabStatuses={tabStatuses}
            onTabSelect={onTabSelect}
            onSwitchToHistory={handleSwitchToHistory}
          />
        ) : (
          <HistoryView
            groupedSessions={groupedSessions}
            agents={agents}
            onSelectSession={handleHistorySelectSession}
            onDeleteSession={onDeleteSession}
            onBack={handleBackToRadar}
          />
        )}
      </div>
    </div>
  );
}
