import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { ChatSession, Agent } from '../../../types';
import { TIME_GROUP_LABEL_KEYS } from '../constants';
import { formatTimestamp, type GroupedSessions } from '../utils';

interface ChatSidebarProps {
  width: number;
  isResizing: boolean;
  groupedSessions: GroupedSessions[];
  currentSessionId?: string;
  agents: Agent[];
  selectedAgentId: string | null;
  onNewChat: () => void;
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  onClose: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
}

/**
 * Chat History Sidebar Component
 */
export function ChatSidebar({
  width,
  isResizing,
  groupedSessions,
  currentSessionId,
  agents,
  selectedAgentId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onClose,
  onMouseDown,
}: ChatSidebarProps) {
  const { t } = useTranslation();

  return (
    <div
      className="flex flex-col bg-[var(--color-card)] border-r border-[var(--color-border)] relative flex-shrink-0"
      style={{ width }}
    >
      {/* Sidebar Header with Close Button */}
      <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-lg">chat</span>
          <span className="font-medium text-[var(--color-text)] text-sm">{t('chat.history')}</span>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
          aria-label="Close chat sidebar"
        >
          <span className="material-symbols-outlined text-lg">close</span>
        </button>
      </div>

      {/* SwarmAI Branding */}
      <div className="p-3 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2 px-2 py-1">
          <span className="material-symbols-outlined text-primary text-xl">smart_toy</span>
          <span className="font-semibold text-[var(--color-text)]">SwarmAI</span>
        </div>
      </div>

      {/* Header with New Chat button */}
      <div className="p-3 border-b border-[var(--color-border)]">
        <button
          onClick={onNewChat}
          disabled={!selectedAgentId}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary hover:bg-primary-hover disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
        >
          <span className="material-symbols-outlined text-xl">add</span>
          {t('chat.newChat')}
        </button>
      </div>

      {/* Chat History List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {groupedSessions.length === 0 ? (
          <p className="px-3 py-2 text-xs text-[var(--color-text-muted)]">{t('chat.noHistory')}</p>
        ) : (
          groupedSessions.map((group, groupIndex) => (
            <div key={group.group}>
              <p
                className={clsx(
                  'px-3 py-2 text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider',
                  groupIndex > 0 && 'mt-3'
                )}
              >
                {t(TIME_GROUP_LABEL_KEYS[group.group])}
              </p>
              {group.sessions.map((session) => {
                const agentForSession = agents.find((a) => a.id === session.agentId);
                return (
                  <div
                    key={session.id}
                    className={clsx(
                      'group w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-left transition-colors cursor-pointer',
                      currentSessionId === session.id
                        ? 'bg-primary text-white'
                        : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
                    )}
                    onClick={() => onSelectSession(session)}
                  >
                    <span className="material-symbols-outlined text-lg flex-shrink-0">chat_bubble_outline</span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{session.title}</p>
                      <p className="text-xs opacity-70">
                        {agentForSession?.name || 'Unknown'} • {formatTimestamp(session.lastAccessedAt)}
                      </p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSession(session);
                      }}
                      className={clsx(
                        'p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity',
                        currentSessionId === session.id
                          ? 'hover:bg-white/20 text-white'
                          : 'hover:bg-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                      )}
                    >
                      <span className="material-symbols-outlined text-sm">delete</span>
                    </button>
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      {/* Resize Handle */}
      <div
        className={clsx(
          'absolute top-0 right-0 w-1 h-full cursor-ew-resize hover:bg-primary/50 transition-colors z-10',
          isResizing && 'bg-primary'
        )}
        onMouseDown={onMouseDown}
      >
        <div className="absolute inset-y-0 -right-1 w-3" />
      </div>
    </div>
  );
}
