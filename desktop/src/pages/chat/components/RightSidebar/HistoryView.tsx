/**
 * History mode view — a searchable, time-grouped list of all chat sessions.
 *
 * Two modes, selected by props:
 * - **Uncontrolled (legacy):** no `searchText`/`onSearchTextChange` → HistoryView
 *   owns its search state and filters the pre-grouped `groupedSessions` by TITLE
 *   (client-side substring). This is the original Radar-sidebar behavior.
 * - **Controlled (HistoryOverlay):** parent passes `searchText` +
 *   `onSearchTextChange` and (when a query is active) `searchResults` — a flat
 *   list from the backend CONTENT FTS endpoint. When `searchResults` is non-null
 *   it is rendered INSTEAD of the grouped fallback, so real message-content
 *   search results actually surface (the point of the History overlay). An empty
 *   query → `searchResults=null` → the time-grouped full list (fallback).
 *
 * Sessions are pre-grouped by the parent (Today/Yesterday/This Week/This
 * Month/Older). Clicking a session calls `onSelectSession`; the back arrow (when
 * not `hideHeader`) calls `onBack`.
 *
 * Key exports:
 * - `HistoryView` — the History mode component
 */

import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { ChatSession } from '../../../../types';
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
  searchText: controlledSearchText,
  onSearchTextChange,
  searchResults,
  isSearching = false,
  hideHeader = false,
}: HistoryViewProps) {
  const { t } = useTranslation();

  // Controlled when the parent supplies BOTH the value and the change handler.
  const isControlled = controlledSearchText !== undefined && onSearchTextChange !== undefined;
  const [internalSearchText, setInternalSearchText] = useState('');
  const searchText = isControlled ? controlledSearchText! : internalSearchText;
  const setSearchText = (value: string) => {
    if (isControlled) onSearchTextChange!(value);
    else setInternalSearchText(value);
  };

  // -------------------------------------------------------------------------
  // Uncontrolled fallback: title-only client filter over groupedSessions.
  // (In controlled mode with injected searchResults we do NOT filter here —
  // the backend already matched by content.)
  // -------------------------------------------------------------------------

  const filteredGroups: GroupedSessions[] = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (isControlled || !query) return groupedSessions;

    return groupedSessions
      .map((g) => ({
        group: g.group,
        sessions: g.sessions.filter((s) =>
          s.title.toLowerCase().includes(query),
        ),
      }))
      .filter((g) => g.sessions.length > 0);
  }, [groupedSessions, searchText, isControlled]);

  // Are we showing injected content-search results instead of the grouped list?
  // Show the results pane when we have results (incl. empty []) OR when a query
  // is active and its first fetch is still in flight — so the initial search
  // shows a "Searching…" hint instead of briefly flashing the grouped fallback.
  const queryActive = searchText.trim().length > 0;
  const showingResults = searchResults != null || (isControlled && queryActive && isSearching);

  const agentName = (agentId: string): string => {
    const agent = agents.find((a) => a.id === agentId);
    return agent?.name ?? 'Unknown';
  };

  // -------------------------------------------------------------------------
  // A single session row (shared by grouped + flat-results rendering)
  // -------------------------------------------------------------------------

  const renderSessionRow = (session: ChatSession) => (
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
        <p className="text-[13px] leading-5 font-medium truncate text-[var(--color-text)]">
          {session.title}
        </p>
        <p className="text-[10px] opacity-70">
          {agentName(session.agentId)} • {formatTimestamp(session.lastAccessedAt)}
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
        <span className="material-symbols-outlined text-sm">delete</span>
      </button>
    </div>
  );

  const hasQuery = searchText.trim().length > 0;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full">
      {/* Header: back arrow + title (suppressed when host provides its own) */}
      {!hideHeader && (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
          <button
            onClick={onBack}
            className="p-1 rounded hover:bg-[var(--color-hover)] transition-colors"
            aria-label="Back"
          >
            <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
              arrow_back
            </span>
          </button>
          <span className="text-sm font-medium text-[var(--color-text)]">
            Chat History
          </span>
        </div>
      )}

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
            placeholder={isControlled ? 'Search conversations…' : 'Search sessions…'}
            className="w-full pl-7 pr-2 py-1.5 text-xs rounded
              bg-[var(--color-input-bg,var(--color-bg))]
              border border-[var(--color-border)]
              text-[var(--color-text)]
              placeholder:text-[var(--color-text-muted)]
              focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>

      {/* Result list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-1">
        {showingResults ? (
          /* Controlled content-search results (flat list). `searchResults` is
             null while the first query is still in flight → treat as empty +
             isSearching so the "Searching…" hint shows. */
          (searchResults?.length ?? 0) === 0 ? (
            <p className="px-3 py-4 text-xs text-[var(--color-text-muted)] text-center">
              {isSearching ? 'Searching…' : 'No matching conversations'}
            </p>
          ) : (
            <>
              <p className="px-3 py-2 text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
                {searchResults!.length} result{searchResults!.length === 1 ? '' : 's'}
              </p>
              {searchResults!.map(renderSessionRow)}
            </>
          )
        ) : filteredGroups.length === 0 ? (
          <p className="px-3 py-4 text-xs text-[var(--color-text-muted)] text-center">
            {hasQuery ? 'No matching sessions' : t('chat.noHistory')}
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
              {group.sessions.map(renderSessionRow)}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
