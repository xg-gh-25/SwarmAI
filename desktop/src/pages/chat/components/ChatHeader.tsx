import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { SessionTabBar } from './SessionTabBar';
import type { RightSidebarId } from '../constants';
import { Toast } from '../../../components/common/Toast';
import { useState } from 'react';
import { chatService } from '../../../services/chat';

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
 * │ [Tab1][Tab2][Tab3]...←scroll→        │  [compact][+] [checklist]   │
 * │ ◄─── SessionTabBar (flex-1) ───►     │  [history] [folder]         │
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
  tabStatuses,
  activeSidebar,
  onOpenSidebar,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const [compactStatus, setCompactStatus] = useState<'idle' | 'loading' | 'done'>('idle');
  const [compactToast, setCompactToast] = useState<string | null>(null);

  // Resolve the backend session ID for the active tab
  const activeTab = openTabs.find(tab => tab.id === activeTabId);
  const activeSessionId = activeTab?.sessionId;

  const handleCompact = async () => {
    if (!activeSessionId || compactStatus === 'loading') return;
    setCompactStatus('loading');
    try {
      const result = await chatService.compactSession(activeSessionId);
      setCompactStatus('done');
      setCompactToast(result.status === 'compacted' ? 'Context compacted successfully' : result.message);
      setTimeout(() => setCompactStatus('idle'), 3000);
    } catch {
      setCompactStatus('idle');
      setCompactToast('Failed to compact session');
    }
  };

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

      {/* Right Section: Header Actions */}
      <div className="flex items-center gap-1 flex-shrink-0">
        {/* Compact Context Button — manually triggers context window compaction */}
        <button
          onClick={handleCompact}
          disabled={compactStatus === 'loading' || !activeSessionId}
          className={clsx(
            'p-2 rounded-lg transition-colors',
            compactStatus === 'done'
              ? 'text-green-500 bg-green-500/10 hover:bg-green-500/20'
              : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]',
            (!activeSessionId || compactStatus === 'loading') && 'opacity-50 cursor-not-allowed'
          )}
          title={t('chat.compact', 'Compact Context')}
          aria-label={t('chat.compact', 'Compact Context')}
        >
          <span className={clsx(
            'material-symbols-outlined',
            compactStatus === 'loading' && 'animate-spin'
          )}>
            {compactStatus === 'loading' ? 'progress_activity' : compactStatus === 'done' ? 'check_circle' : 'compress'}
          </span>
        </button>

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

      {/* Toast notification for Compact results */}
      {compactToast && (
        <Toast
          message={compactToast}
          type={compactStatus === 'done' ? 'success' : 'error'}
          duration={4000}
          onDismiss={() => setCompactToast(null)}
        />
      )}
    </div>
  );
}
