import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { SessionTabBar } from './SessionTabBar';
import { SessionTab } from './SessionTab';
import { hiveTabOf, isSwarmOutTab, normalTabs } from '../swarmOut';
import { useSwarmOutActivity } from '../../../components/swarm-out/useSwarmOutActivity';
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
  /** outputCount at the last time this tab's Canvas was open (run_9dd59523). The pill
   *  shows only when outputCount > this, so it stops nagging once outputs are reviewed. */
  lastSeenOutputCount?: number;
  /** Whether Canvas is currently open (pill only shows when it is NOT). */
  canvasOpen?: boolean;
  /** Open Canvas on the active tab (dispatches swarm:open-canvas). */
  onOpenCanvas?: () => void;
}

/** The pinned Swarm Out tab with the Hives' activity (polled only while it exists). */
function SwarmOutPinnedTab({ tab, isActive, onSelect, status }: {
  tab: OpenTab; isActive: boolean; onSelect: (id: string) => void; status?: TabStatus;
}) {
  const activity = useSwarmOutActivity();
  return (
    <SessionTab tab={tab} isActive={isActive} onSelect={onSelect} onClose={() => {}} status={status} variant="swarm-out" activity={activity} />
  );
}

/**
 * Chat Header Component - spans full width with session tabs and action buttons.
 *
 * Layout:
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ [Tab1][Tab2][Tab3]...[+]←scroll→ │ [Swarm Out]        [health warn] │
 * │ ◄ SessionTabBar ("+" at tail) ►  ◄ pinned ►   ◄─ right cluster ──► │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * The new-session "+" lives at the TAIL of the tab strip (inside SessionTabBar).
 * The strip only takes the width its tabs need (it shrinks and scrolls when they
 * overflow), so the pinned Swarm Out tab (pages/chat/swarmOut.ts) follows the "+"
 * after a gap and a divider, outside the scrolling strip, which holds the user's
 * own chat tabs only. The right cluster holds the health warning (shown when not
 * connected) and the outputs pill.
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
  lastSeenOutputCount = 0,
  canvasOpen = false,
  onOpenCanvas,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const { health } = useHealth();
  // Show only UNSEEN outputs (run_9dd59523): once the user opened Canvas and reviewed,
  // lastSeenOutputCount caught up to outputCount → pill hides; it reappears only when a
  // NEW output pushes outputCount above what was last seen. (Was `outputCount > 0`, which
  // nagged for the whole session.)
  const showOutputsPill = outputCount > lastSeenOutputCount && !canvasOpen;
  // The strip holds the user's own chat tabs; the pinned Swarm Out tab follows the
  // strip's "+" behind a divider (pages/chat/swarmOut.ts).
  const userTabs = normalTabs(openTabs);
  const swarmOutTab = openTabs.find((tab) => isSwarmOutTab(tab.id));
  // The Hive tab (a dispatched run's session) sits right after Swarm Out, in its group,
  // behind a branch line that marks it as opened from Swarm Out.
  const hiveTab = hiveTabOf(openTabs);

  return (
    <div className="h-10 px-4 flex items-center flex-shrink-0 gap-4 relative z-10 bg-[var(--color-bg-chrome)] shadow-[inset_0_-1px_0_var(--color-border)]">
      {/* Left Section: Session Tab Bar (the "+" new-session button lives at the
          tail of the strip, inside SessionTabBar). */}
      <SessionTabBar
        tabs={userTabs}
        activeTabId={activeTabId}
        onTabSelect={onTabSelect}
        onTabClose={onTabClose}
        tabStatuses={tabStatuses}
        onNewTab={onNewSession}
        isNewTabDisabled={isNewTabDisabled}
      />

      {/* Pinned Swarm Out tab: right after the strip's "+", set apart by a gap and a
          divider so it reads as its own place, not one more chat tab. Outside the
          scrolling strip, so it never scrolls away. */}
      {swarmOutTab && (
        <div className="flex items-center gap-3 flex-shrink-0 self-stretch">
          <span
            aria-hidden="true"
            className="self-center w-px h-5 bg-[var(--color-text-muted)] opacity-40"
            data-testid="swarm-out-divider"
          />
          <div role="tablist" aria-label="Swarm Out" className="flex items-center self-stretch">
            <SwarmOutPinnedTab
              tab={swarmOutTab}
              isActive={swarmOutTab.id === activeTabId}
              onSelect={onTabSelect}
              status={tabStatuses?.[swarmOutTab.id]}
            />
            {/* the Hive tab branches off Swarm Out: a dashed line from it, so the two
                read as parent and child, not as two tabs side by side */}
            {hiveTab && (
              <span aria-hidden="true" data-testid="hive-tab-branch"
                className={clsx('swarm-out-branch', hiveTab.id === activeTabId && 'swarm-out-branch--active')} />
            )}
            {hiveTab && (
              <SessionTab
                tab={hiveTab}
                isActive={hiveTab.id === activeTabId}
                onSelect={onTabSelect}
                onClose={onTabClose}
                status={tabStatuses?.[hiveTab.id]}
                variant="hive"
              />
            )}
          </div>
        </div>
      )}

      {/* Right Section: Outputs pill + Health Warning (pushed to the far right) */}
      <div className="flex items-center gap-1 flex-shrink-0 ml-auto">
        {/* Canvas outputs pill (run_9e42c066) — shown ONLY when the active tab has
            outputs AND Canvas is closed, so a run's deliverables are never invisible.
            Click opens Canvas in the current tab (in-band, mirrors the AlertsPill
            pattern — NOT a separate notification surface). */}
        {showOutputsPill && (
          <button
            type="button"
            onClick={onOpenCanvas}
            className="btn-sm btn-soft mr-2 transition-colors"
            title={t('canvas.openOutputs', 'Open Canvas to review outputs')}
            aria-label={`${outputCount} output${outputCount !== 1 ? 's' : ''} — open Canvas`}
            data-testid="chat-header-outputs-pill"
          >
            <span className="material-symbols-outlined icon-xs">draft</span>
            {outputCount} output{outputCount !== 1 ? 's' : ''}
          </button>
        )}
        {/* Health warning — only shown for non-connected states (BottomBar handles normal status) */}
        {health.status === 'disconnected' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-red-500/10 text-red-400 text-xs font-medium mr-2"
            role="status"
            data-testid="health-pill"
            title={health.issue?.detail}
            aria-label={health.issue?.detail ?? t('health.disconnected', 'Backend Offline')}
          >
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            {health.issue?.short ?? t('health.disconnected', 'Backend Offline')}
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
            data-testid="health-pill"
            title={health.issue?.detail}
            aria-label={health.issue?.detail ?? t('health.degraded', 'Reconnecting…')}
          >
            <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
            {health.issue?.short ?? t('health.degraded', 'Reconnecting…')}
          </div>
        )}
      </div>
    </div>
  );
}

