/**
 * History mode view for the Radar sidebar.
 *
 * Displays a searchable, time-grouped list of all chat sessions.
 * Sessions are pre-grouped by the parent ``ChatPage`` via the
 * ``groupedSessions`` prop (Today, Yesterday, This Week, This Month,
 * Older).  A local search input filters sessions case-insensitively
 * by title.  Clicking a session calls ``onSelectSession`` so the
 * parent can switch to Radar mode and activate the tab.  A back
 * arrow at the top calls ``onBack()`` to return to Radar mode.
 *
 * Key exports:
 * - ``HistoryView`` — The History mode component
 */

import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { HistoryViewProps } from './types';
import { TIME_GROUP_LABEL_KEYS } from '../../constants';
import { formatTimestamp, type GroupedSessions } from '../../utils';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HistoryView({
  groupedSessions,
  agents,
  onSelectSession,
  onDeleteSession,
  onBack,
}: HistoryViewProps) {
  const { t } = useTranslation();
  const [searchText, setSearchText] = useState('');

  // -------------------------------------------------------------------------
  // Filter sessions by search text (case-insensitive title match)
  // -------------------------------------------------------------------------

  const filteredGroups: GroupedSessions[] = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return groupedSessions;

    return groupedSessions
      .map((g) => ({
        group: g.group,
        sessions: g.sessions.filter((s) =>
          s.title.toLowerCase().includes(query),
        ),
      }))
      .filter((g) => g.sessions.length > 0);
  }, [groupedSessions, searchText]);

  // -------------------------------------------------------------------------
  // Agent lookup helper
  // -------------------------------------------------------------------------

  const agentName = (agentId: string): string => {
    const agent = agents.find((a) => a.id === agentId);
    return agent?.name ?? 'Unknown';
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full">
      {/* Header: back arrow + title */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <button
          onClick={onBack}
          className="p-1 rounded hover:bg-[var(--color-hover)] transition-colors"
          aria-label="Back to Radar"
        >
          <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
            arrow_back
          </span>
        </button>
        <span className="text-sm font-medium text-[var(--color-text)]">
          Chat History
        </span>
      </div>

      {/* Search input */}
      <div className="px-3 py-2">
        <div className="relative">
          <span
            className="material-symbols-outlined absolute left-2 top-1/2 -translate-y-1/2
              text-sm text-[var(--color-text-muted)]"
            aria-hidden="true"
          >
            search
          </span>
          <input
            type="text"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="Search sessions…"
            className="w-full pl-7 pr-2 py-1.5 text-xs rounded
              bg-[var(--color-input-bg,var(--color-bg))]
              border border-[var(--color-border)]
              text-[var(--color-text)]
              placeholder:text-[var(--color-text-muted)]
              focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>

      {/* Session list — time-grouped */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
        {filteredGroups.length === 0 ? (
          <p className="px-3 py-4 text-xs text-[var(--color-text-muted)] text-center">
            {searchText.trim()
              ? 'No matching sessions'
              : t('chat.noHistory')}
          </p>
        ) : (
          filteredGroups.map((group, groupIndex) => (
            <div key={group.group}>
              {/* Time-period heading */}
              <p
                className={`px-3 py-2 text-xs font-medium text-[var(--color-text-muted)]
                  uppercase tracking-wider${groupIndex > 0 ? ' mt-3' : ''}`}
              >
                {t(TIME_GROUP_LABEL_KEYS[group.group])}
              </p>

              {/* Session rows */}
              {group.sessions.map((session) => (
                <div
                  key={session.id}
                  className="group flex items-center gap-2 px-3 py-2 rounded-lg
                    text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]
                    hover:text-[var(--color-text)] transition-colors cursor-pointer"
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
                  <span className="material-symbols-outlined text-lg shrink-0">
                    chat_bubble_outline
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate text-[var(--color-text)]">
                      {session.title}
                    </p>
                    <p className="text-[10px] opacity-70">
                      {agentName(session.agentId)} •{' '}
                      {formatTimestamp(session.lastAccessedAt)}
                    </p>
                  </div>

                  {/* Delete button — visible on hover */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteSession(session);
                    }}
                    aria-label={`Delete session ${session.title}`}
                    className="p-1 rounded opacity-0 group-hover:opacity-100
                      hover:bg-[var(--color-border)]
                      text-[var(--color-text-muted)] hover:text-[var(--color-text)]
                      transition-opacity"
                  >
                    <span className="material-symbols-outlined text-sm">
                      delete
                    </span>
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
