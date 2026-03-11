import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { SessionTabBar } from './SessionTabBar';
import type { RightSidebarId } from '../constants';
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

  // Sidebar controls (mutual exclusion - only one sidebar visible at a time)
  activeSidebar: RightSidebarId;
  onOpenSidebar: (id: RightSidebarId) => void;
}

/**
 * Chat Header Component - spans full width with session tabs and action buttons.
 *
 * Layout:
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ [Tab1][Tab2][Tab3]...←scroll→        │  [+] [checklist]            │
 * │ ◄─── SessionTabBar (flex-1) ───►     │  [history] [folder]         │
 * └─────────────────────────────────────────────────────────────────────┘
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4
 */
export function ChatHeader({
  openTabs,
  activeTabId,
  onTabSelect,
  onTabClose,
  onNewSession,
  tabStatuses,
  activeSidebar,
  onOpenSidebar,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const { health } = useHealth();

  return (
    <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0 gap-4 relative z-10 bg-[var(--color-bg)]">
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

        {/* ToDo Radar Toggle (checklist) - Validates: Requirements 5.1, 5.4 */}
        <button
          onClick={() => onOpenSidebar('todoRadar')}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            activeSidebar === 'todoRadar'
              ? 'text-primary bg-primary/10 hover:bg-primary/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          )}
          title={t('chat.todoRadar', 'ToDo Radar')}
          aria-label={t('chat.todoRadar', 'ToDo Radar')}
          aria-pressed={activeSidebar === 'todoRadar'}
        >
          <span className="material-symbols-outlined">checklist</span>
        </button>

        {/* Chat History Toggle (history) - Validates: Requirements 5.2, 5.4 */}
        <button
          onClick={() => onOpenSidebar('chatHistory')}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            activeSidebar === 'chatHistory'
              ? 'text-primary bg-primary/10 hover:bg-primary/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          )}
          title={t('chat.history', 'Chat History')}
          aria-label={t('chat.history', 'Chat History')}
          aria-pressed={activeSidebar === 'chatHistory'}
        >
          <span className="material-symbols-outlined">history</span>
        </button>

        {/* FileBrowser Toggle (folder) - Validates: Requirements 1.3, 2.1, 5.3 */}
        <button
          onClick={() => onOpenSidebar('fileBrowser')}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            activeSidebar === 'fileBrowser'
              ? 'text-primary bg-primary/10 hover:bg-primary/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          )}
          title={t('chat.fileBrowser', 'File Browser')}
          aria-label={t('chat.fileBrowser', 'File Browser')}
          aria-pressed={activeSidebar === 'fileBrowser'}
        >
          <span className="material-symbols-outlined">folder</span>
        </button>
      </div>
    </div>
  );
}

