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

  // Dynamic tab scaling — disabled "+" button and memory pressure indicator
  /** True when open tab count >= dynamic max tabs (disables the "+" button). */
  isNewTabDisabled?: boolean;
  /** Current memory pressure level from backend polling. */
  memoryPressure?: 'ok' | 'warning' | 'critical';
}

/**
 * Chat Header Component - spans full width with session tabs and action buttons.
 *
 * Layout:
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ [Tab1][Tab2][Tab3]...←scroll→                          │  [+]      │
 * │ ◄─── SessionTabBar (flex-1) ───►                       │           │
 * └─────────────────────────────────────────────────────────────────────┘
 *
 * Sidebar toggle buttons removed — the Radar sidebar is now always visible.
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
  memoryPressure,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const { health } = useHealth();

  return (
    <div className="h-10 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0 gap-4 relative z-10 bg-[var(--color-bg-chrome)]">
      {/* Left Section: Session Tab Bar */}
      <SessionTabBar
        tabs={openTabs}
        activeTabId={activeTabId}
        onTabSelect={onTabSelect}
        onTabClose={onTabClose}
        tabStatuses={tabStatuses}
      />

      {/* Right Section: Health Warning + Header Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
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
        {/* Memory pressure indicator — informational only, no auto-close (Req 6.1–6.5) */}
        {memoryPressure === 'warning' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-500/10 text-amber-400 text-xs font-medium mr-1"
            role="status"
            aria-label={t('chat.memoryWarning', 'Memory pressure: warning')}
          >
            <span className="w-2 h-2 rounded-full bg-amber-500" />
            {t('chat.memoryWarning', 'Memory')}
          </div>
        )}
        {memoryPressure === 'critical' && (
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-red-500/10 text-red-400 text-xs font-medium mr-1"
            role="status"
            aria-label={t('chat.memoryCritical', 'Memory pressure: critical')}
          >
            <span className="w-2 h-2 rounded-full bg-red-500" />
            {t('chat.memoryCritical', 'Memory')}
          </div>
        )}
        {/* New Session Button (+) - Validates: Requirement 2.1, 5.1, 5.2, 5.3 */}
        <button
          onClick={onNewSession}
          disabled={isNewTabDisabled}
          className={`p-2 rounded-lg transition-colors ${
            isNewTabDisabled
              ? 'text-[var(--color-text-disabled,var(--color-text-muted))] opacity-50 cursor-not-allowed'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          }`}
          title={isNewTabDisabled
            ? t('chat.tabLimitReached', 'System resources are limited. Close a tab or free memory to open another.')
            : t('chat.newSession', 'New Session (⌘N)')
          }
          aria-label={isNewTabDisabled
            ? t('chat.tabLimitReached', 'System resources are limited. Close a tab or free memory to open another.')
            : t('chat.newSession', 'New Session')
          }
        >
          <span className="material-symbols-outlined text-[18px]">add</span>
        </button>
      </div>
    </div>
  );
}

