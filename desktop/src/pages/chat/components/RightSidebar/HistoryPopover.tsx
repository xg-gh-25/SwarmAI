/**
 * History search popover — replaces the old History mode toggle.
 *
 * Floating panel anchored to the search icon in RadarSidebar header.
 * Shows searchable, time-grouped list of chat sessions.
 * Click outside or select a session to close.
 */

import { useState, useMemo, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { Agent, ChatSession } from '../../../../types';
import type { GroupedSessions } from '../../utils';
import { TIME_GROUP_LABEL_KEYS } from '../../constants';
import { formatTimestamp } from '../../utils';

interface HistoryPopoverProps {
  groupedSessions: GroupedSessions[];
  agents: Agent[];
  onSelectSession: (session: ChatSession) => void;
  onDeleteSession: (session: ChatSession) => void;
  onClose: () => void;
}

export function HistoryPopover({
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
  onClose,
}: HistoryPopoverProps) {
  const { t } = useTranslation();
  const [searchText, setSearchText] = useState('');
  const popoverRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus search input
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const filteredGroups: GroupedSessions[] = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return groupedSessions;
    return groupedSessions
      .map((g) => ({
        group: g.group,
        sessions: g.sessions.filter((s) => s.title.toLowerCase().includes(query)),
      }))
      .filter((g) => g.sessions.length > 0);
  }, [groupedSessions, searchText]);

  const agentName = (agentId: string): string => {
    return agents.find((a) => a.id === agentId)?.name ?? 'Unknown';
  };

  return (
    <div
      ref={popoverRef}
      className="absolute right-0 top-full mt-1 w-80 max-h-[400px] rounded-lg shadow-lg border border-[var(--color-border)] bg-[var(--color-bg)] z-50 flex flex-col"
    >
      {/* Search input */}
      <div className="px-3 py-2 border-b border-[var(--color-border)]">
        <div className="relative">
          <span className="material-symbols-outlined absolute left-2 top-1/2 -translate-y-1/2 text-sm text-[var(--color-text-muted)]">
            search
          </span>
          <input
            ref={inputRef}
            type="text"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Search sessions…"
            className="w-full pl-7 pr-2 py-1.5 text-xs rounded bg-[var(--color-input-bg,var(--color-bg))] border border-[var(--color-border)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
        {filteredGroups.length === 0 ? (
          <p className="px-3 py-4 text-xs text-[var(--color-text-muted)] text-center">
            {searchText.trim() ? 'No matching sessions' : t('chat.noHistory')}
          </p>
        ) : (
          filteredGroups.map((group, groupIndex) => (
            <div key={group.group}>
              <p className={`px-2 py-1 text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wider${groupIndex > 0 ? ' mt-2' : ''}`}>
                {t(TIME_GROUP_LABEL_KEYS[group.group])}
              </p>
              {group.sessions.map((session) => (
                <div
                  key={session.id}
                  className="group flex items-center gap-2 px-2 py-1.5 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors cursor-pointer"
                  onClick={() => onSelectSession(session)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelectSession(session);
                    }
                  }}
                >
                  <span className="material-symbols-outlined text-base shrink-0">chat_bubble_outline</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] leading-4 font-medium truncate text-[var(--color-text)]">
                      {session.title}
                    </p>
                    <p className="text-[10px] opacity-70">
                      {agentName(session.agentId)} • {formatTimestamp(session.lastAccessedAt)}
                    </p>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDeleteSession(session); }}
                    aria-label={`Delete ${session.title}`}
                    className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-opacity"
                  >
                    <span className="material-symbols-outlined text-sm">delete</span>
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
