import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import { SessionTabBar } from './SessionTabBar';

interface ChatHeaderProps {
  // Tab management
  openTabs: OpenTab[];
  activeTabId: string | null;
  onTabSelect: (tabId: string) => void;
  onTabClose: (tabId: string) => void;
  onNewSession: () => void;

  // Sidebar toggles
  chatSidebarCollapsed: boolean;
  todoRadarCollapsed: boolean;
  onToggleChatSidebar: () => void;
  onToggleTodoRadar: () => void;
}

/**
 * Chat Header Component - spans full width with session tabs and action buttons.
 * 
 * Layout:
 * ┌─────────────────────────────────────────────────────────────────────┐
 * │ [Tab1][Tab2][Tab3]...←scroll→        │  [+] [checklist] [history]  │
 * │ ◄─── SessionTabBar (flex-1) ───►     │  ◄─── HeaderActions ───►    │
 * └─────────────────────────────────────────────────────────────────────┘
 * 
 * Validates: Requirements 2.1, 4.2, 4.3, 4.4
 */
export function ChatHeader({
  openTabs,
  activeTabId,
  onTabSelect,
  onTabClose,
  onNewSession,
  chatSidebarCollapsed,
  todoRadarCollapsed,
  onToggleChatSidebar,
  onToggleTodoRadar,
}: ChatHeaderProps) {
  const { t } = useTranslation();

  return (
    <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0 gap-4">
      {/* Left Section: Session Tab Bar */}
      <SessionTabBar
        tabs={openTabs}
        activeTabId={activeTabId}
        onTabSelect={onTabSelect}
        onTabClose={onTabClose}
      />

      {/* Right Section: Header Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {/* New Session Button (+) - Validates: Requirement 2.1 */}
        <button
          onClick={onNewSession}
          className="p-2 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
          title={t('chat.newSession', 'New Session')}
          aria-label={t('chat.newSession', 'New Session')}
        >
          <span className="material-symbols-outlined">add</span>
        </button>

        {/* ToDo Radar Toggle (checklist) - Validates: Requirements 4.2, 4.4 */}
        <button
          onClick={onToggleTodoRadar}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            !todoRadarCollapsed
              ? 'text-primary bg-primary/10 hover:bg-primary/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          )}
          title={t('chat.todoRadar', 'ToDo Radar')}
          aria-label={t('chat.todoRadar', 'ToDo Radar')}
          aria-pressed={!todoRadarCollapsed}
        >
          <span className="material-symbols-outlined">checklist</span>
        </button>

        {/* Chat History Toggle (history) - Validates: Requirements 4.3, 4.4 */}
        <button
          onClick={onToggleChatSidebar}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            !chatSidebarCollapsed
              ? 'text-primary bg-primary/10 hover:bg-primary/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
          )}
          title={t('chat.history', 'Chat History')}
          aria-label={t('chat.history', 'Chat History')}
          aria-pressed={!chatSidebarCollapsed}
        >
          <span className="material-symbols-outlined">history</span>
        </button>
      </div>
    </div>
  );
}
