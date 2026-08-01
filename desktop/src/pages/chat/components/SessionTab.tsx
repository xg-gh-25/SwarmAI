/* eslint-disable react-refresh/only-export-components */
import { forwardRef } from 'react';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { TabStatusIndicator } from './TabStatusIndicator';

interface SessionTabProps {
  tab: OpenTab;
  index?: number;
  isActive: boolean;
  onSelect: (tabId: string) => void;
  onClose: (tabId: string) => void;
  status?: TabStatus;
  maxTitleLength?: number;
  onKeyDown?: (e: React.KeyboardEvent) => void;
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
  },
  ref
) {
  const displayTitle = truncateTitle(tab.title, maxTitleLength);
  const isUnread = !isActive && status === 'complete_unread';
  // Show shortcut hint for first 9 tabs (Cmd/Ctrl+1 through Cmd/Ctrl+9)
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
  const shortcutLabel = index != null && index < 9 ? `${isMac ? '\u2318' : 'Ctrl+'}${index + 1}` : null;

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
        // border-b-2 on ALL tabs reserves the underline height → no layout shift
        // between active/inactive. Inactive uses a transparent placeholder.
        'group/tab flex items-center gap-1.5 px-3 py-1 rounded-md cursor-pointer',
        'transition-all duration-150 ease-out',
        'min-w-0 max-w-[200px] flex-shrink-0',
        // border-b-2 on ALL states (transparent when inactive) keeps the tab
        // height constant so the active underline appears without layout shift (GUI12).
        'border-b-2',
        isActive
          // Active: accent underline + subtle accent-tinted fill + faint top ring
          // for a "lifted" chip feel. Uses the product accent var
          // (--color-primary, which follows the user's accent preset) so the
          // active tab matches the app theme instead of a hardcoded blue (GUI12).
          ? 'border-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_10%,var(--color-card))] text-[var(--color-text)] font-medium shadow-[inset_0_1px_0_var(--color-border)]'
          : 'border-transparent text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      )}
    >
      {/* Unread dot — 6px accent pulsing dot with a soft glow. Uses the product
          accent var so it matches the theme (not a hardcoded blue). */}
      {isUnread && (
        <span
          className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse bg-[var(--color-primary)]"
          style={{ boxShadow: '0 0 5px color-mix(in srgb, var(--color-primary) 55%, transparent)' }}
          role="img"
          aria-label="Unread messages"
        />
      )}

      {/* Chat icon */}
      <span className="material-symbols-outlined text-[14px] flex-shrink-0">
        chat_bubble
      </span>

      {/* Tab status indicator (streaming, error, etc.) — skip for unread since we show dot */}
      {status && status !== 'complete_unread' && <TabStatusIndicator status={status} />}

      {/* Truncated title */}
      <span className="truncate text-xs" title={tab.title}>
        {displayTitle}
      </span>

      {/* Shortcut hint — shown on hover, 9px mono font, dim color */}
      {shortcutLabel && (
        <span className="text-[9px] font-mono text-[var(--color-text-dim)] opacity-0 group-hover/tab:opacity-100 transition-opacity flex-shrink-0 ml-auto">
          {shortcutLabel}
        </span>
      )}

      {/* Close button — hidden by default, fades in on tab hover (also visible
          when the tab is active so the current tab is always closable). */}
      <button
        onClick={handleClose}
        aria-label={`Close ${tab.title}`}
        className={clsx(
          'p-0.5 rounded-md transition-all duration-150 flex-shrink-0',
          isActive ? 'opacity-70 hover:opacity-100' : 'opacity-0 group-hover/tab:opacity-100',
          'hover:bg-[var(--color-hover)]',
          'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        )}
      >
        <span className="material-symbols-outlined text-[14px]">close</span>
      </button>
    </div>
  );
}
);
