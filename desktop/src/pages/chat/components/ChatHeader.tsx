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

      {/* Right Section: Health Indicator + Header Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {/* Backend Health Indicator — Validates: Requirements 1.6, 1.7 */}
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
        {/* New Session Button (+) - Validates: Requirement 2.1 */}
        <button
          onClick={onNewSession}
          className="p-2 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
          title={t('chat.newSession', 'New Session')}
          aria-label={t('chat.newSession', 'New Session')}
        >
          <span className="material-symbols-outlined">add</span>
        </button>
      </div>
    </div>
  );
}

