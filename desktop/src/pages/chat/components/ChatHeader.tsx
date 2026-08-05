import { useTranslation } from 'react-i18next';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { SessionTabBar } from './SessionTabBar';
import { useHealth } from '../../../contexts/HealthContext';

interface ChatHeaderProps {
  // Tab management
  openTabs: OpenTab[];
  activeTabId: string | null;
  onTabSelect: (tabId: string) => void;
  onTabClose: (tabId: string) => void;
  onNewSession: () => void;

  // Fix 8: Tab status indicators
  tabStatuses?: Record<string, TabStatus>;

  // Dynamic tab scaling — disabled "+" button when at limit
  /** True when open tab count >= dynamic max tabs (disables the "+" button). */
  isNewTabDisabled?: boolean;

  // Canvas outputs affordance (run_9e42c066) — when the active tab has produced
  // outputs but Canvas is CLOSED, show an unobtrusive "N outputs" pill so a run's
  // deliverables are visible without the Canvas being open. Clicking opens Canvas.
  /** Number of output rows for the active tab (from the resident useCanvasHost store). */
  outputCount?: number;
  /** Whether Canvas is currently open (pill only shows when it is NOT). */
  canvasOpen?: boolean;
  /** Open Canvas on the active tab (dispatches swarm:open-canvas). */
  onOpenCanvas?: () => void;
}

/**
 * Chat Header Component - spans full width with session tabs and action buttons.
 *
 * Layout:
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ [Tab1][Tab2][Tab3]...[+]←scroll→                    │ [health warn]  │
 * │ ◄─── SessionTabBar (flex-1, "+" at tail) ──────────►  ◄─ (only if ─► │
 * └──────────────────────────────────────────────────────  disconnected)┘
 *
 * The new-session "+" lives at the TAIL of the tab strip (inside SessionTabBar).
 * The right cluster holds ONLY the health warning (shown when not connected).
 * The 🔔 Alerts "Needs You" pill was MOVED to the left-sidebar top slot
 * (run_2bdc68ad) — a global attention signal belongs on the fixed-width left
 * chrome, not on the Canvas-shifting tab row; ChatPage portals it there.
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1
 */
export function ChatHeader({
  openTabs,
  activeTabId,
  onTabSelect,
  onTabClose,
  onNewSession,
  tabStatuses,
  isNewTabDisabled,
  outputCount = 0,
  canvasOpen = false,
  onOpenCanvas,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const { health } = useHealth();
  const showOutputsPill = outputCount > 0 && !canvasOpen;

  return (
    <div className="h-10 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0 gap-4 relative z-10 bg-[var(--color-bg-chrome)]">
      {/* Left Section: Session Tab Bar (the "+" new-session button lives at the
          tail of the strip, inside SessionTabBar). */}
      <SessionTabBar
        tabs={openTabs}
        activeTabId={activeTabId}
        onTabSelect={onTabSelect}
        onTabClose={onTabClose}
        tabStatuses={tabStatuses}
        onNewTab={onNewSession}
        isNewTabDisabled={isNewTabDisabled}
      />

      {/* Right Section: Outputs pill + Health Warning + Header Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {/* Canvas outputs pill (run_9e42c066) — shown ONLY when the active tab has
            outputs AND Canvas is closed, so a run's deliverables are never invisible.
            Click opens Canvas in the current tab (in-band, mirrors the AlertsPill
            pattern — NOT a separate notification surface). */}
        {showOutputsPill && (
          <button
            type="button"
            onClick={onOpenCanvas}
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-[var(--color-primary)]/10 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/20 text-xs font-medium mr-2 transition-colors"
            title={t('canvas.openOutputs', 'Open Canvas to review outputs')}
            aria-label={`${outputCount} output${outputCount !== 1 ? 's' : ''} — open Canvas`}
            data-testid="chat-header-outputs-pill"
          >
            <span className="material-symbols-outlined text-[14px]">draft</span>
            {outputCount} output{outputCount !== 1 ? 's' : ''}
          </button>
        )}
        {/* Health warning — only shown for non-connected states (BottomBar handles normal status) */}
        {health.status === 'disconnected' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-red-500/10 text-red-400 text-xs font-medium mr-2"
            role="status"
            aria-label={t('health.disconnected', 'Backend Offline')}
          >
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            {t('health.disconnected', 'Backend Offline')}
          </div>
        )}
        {health.status === 'initializing' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-500/10 text-amber-400 text-xs font-medium mr-2"
            role="status"
            aria-label={t('health.initializing', 'Starting up...')}
          >
            <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
            {t('health.initializing', 'Starting up...')}
          </div>
        )}
        {/* 'degraded' (run_13094a88): daemon ALIVE but a probe was briefly missed.
            Amber "reconnecting" hint — inputs stay usable (NOT the red offline state). */}
        {health.status === 'degraded' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-500/10 text-amber-400 text-xs font-medium mr-2"
            role="status"
            aria-label={t('health.degraded', 'Reconnecting…')}
          >
            <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
            {t('health.degraded', 'Reconnecting…')}
          </div>
        )}
      </div>
    </div>
  );
}

