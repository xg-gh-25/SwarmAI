/* eslint-disable react-refresh/only-export-components */
import { forwardRef } from 'react';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { TabStatusIndicator } from './TabStatusIndicator';
import { TabNoticeDot } from './TabNoticeBar';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';

interface SessionTabProps {
  tab: OpenTab;
  index?: number;
  isActive: boolean;
  onSelect: (tabId: string) => void;
  onClose: (tabId: string) => void;
  status?: TabStatus;
  maxTitleLength?: number;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  /** 'swarm-out': the pinned Swarm Out tab (pages/chat/swarmOut.ts) — its own colour
   *  and icon, no close button, no shortcut hint. */
  variant?: 'swarm-out';
}

/**
 * Truncates a title to the specified max length, adding "..." if truncated.
 * @param title - The title to truncate
 * @param maxLength - Maximum length before truncation (default 25)
 * @returns Truncated title with "..." suffix if needed
 */
export function truncateTitle(title: string, maxLength: number = 25): string {
  if (title.length <= maxLength) {
    return title;
  }
  return title.slice(0, maxLength) + '...';
}

/**
 * Individual session tab component for the tab bar.
 * Displays a chat icon, truncated title, close button, unread dot, and shortcut hint.
 * Supports keyboard navigation via onKeyDown prop.
 *
 * Validates: Requirements 1.3, 1.4, 1.5, 1.6
 */
export const SessionTab = forwardRef<HTMLDivElement, SessionTabProps>(function SessionTab(
  {
    tab,
    index,
    isActive,
    onSelect,
    onClose,
    status,
    maxTitleLength = 25,
    onKeyDown,
    variant,
  },
  ref
) {
  const pinned = variant === 'swarm-out';
  const { t } = useTranslation();
  const displayTitle = truncateTitle(tab.title, maxTitleLength);
  const isUnread = !isActive && status === 'complete_unread';
  // Show shortcut hint for first 9 tabs (Cmd/Ctrl+1 through Cmd/Ctrl+9)
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
  const shortcutLabel = !pinned && index != null && index < 9 ? `${isMac ? '\u2318' : 'Ctrl+'}${index + 1}` : null;

  const handleClick = () => {
    if (!isActive) {
      onSelect(tab.id);
    }
  };

  const handleClose = (e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent tab selection when clicking close
    onClose(tab.id);
  };

  return (
    <div
      ref={ref}
      role="tab"
      aria-selected={isActive}
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => {
        // Handle Enter/Space for selection
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleClick();
          return;
        }
        // Delegate arrow key navigation to parent
        onKeyDown?.(e);
      }}
      className={clsx(
        // Piano keys (2026-10-24, index.css .session-tab): inactive tabs are keys lit
        // from above (top light, 3px edge, drop shadow; hover lifts 1px, press sinks
        // 2px). The active tab takes the chat body's colour, keeps the 2px accent rule
        // on top, and joins the chat below through a 5px foot + two reverse corners
        // drawn by pseudo-elements. Every state keeps the same 30px box and 1px
        // border, so selecting a tab never shifts the strip (GUI12).
        'session-tab group/tab flex items-center gap-1.5 h-[30px] px-2.5 cursor-pointer border',
        'min-w-0 max-w-[220px] flex-shrink-0',
        pinned && 'session-tab--swarm-out',
        isActive
          ? 'session-tab--active text-[var(--color-text)] font-medium'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
      )}
    >
      {/* Unread dot — 6px accent pulsing dot with a soft glow. Uses the product
          accent var so it matches the theme (not a hardcoded blue). */}
      {isUnread && (
        <span
          className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse bg-[var(--color-primary)]"
          style={{ boxShadow: '0 0 5px color-mix(in srgb, var(--color-primary) 55%, transparent)' }}
          role="img"
          aria-label={tf(t, 'chat.tabStatus.unread', 'Unread messages')}
        />
      )}

      {/* Chat icon */}
      <span className="material-symbols-outlined icon-xs flex-shrink-0">
        {pinned ? 'hive' : 'chat_bubble'}
      </span>

      {/* Tab status indicator (streaming, error, etc.) — skip for unread since we show dot */}
      {status && status !== 'complete_unread' && <TabStatusIndicator status={status} />}

      {/* Background tab with an in-tab warning/error notice */}
      <TabNoticeDot tabId={tab.id} sessionId={tab.sessionId} isActive={isActive} />

      {/* Truncated title */}
      <span className="truncate text-[12.5px]" title={tab.title}>
        {displayTitle}
      </span>

      {/* Shortcut hint — shown on hover, 9px mono font, dim color */}
      {shortcutLabel && (
        <span className="text-[11px] font-mono text-[var(--color-text-dim)] opacity-0 group-hover/tab:opacity-100 transition-opacity flex-shrink-0 ml-auto">
          {shortcutLabel}
        </span>
      )}

      {/* Close button — hidden by default, fades in on tab hover (also visible
          when the tab is active so the current tab is always closable). */}
      {!pinned && <button
        onClick={handleClose}
        aria-label={`Close ${tab.title}`}
        className={clsx(
          'w-[18px] h-[18px] inline-flex items-center justify-center rounded transition-all duration-150 flex-shrink-0',
          isActive ? 'opacity-70 hover:opacity-100' : 'opacity-0 group-hover/tab:opacity-100',
          'hover:bg-[var(--color-hover)]',
          'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        )}
      >
        <span className="material-symbols-outlined icon-xs">close</span>
      </button>}
    </div>
  );
}
);
