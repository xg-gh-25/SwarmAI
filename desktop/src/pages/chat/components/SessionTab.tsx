import { forwardRef } from 'react';
import clsx from 'clsx';
import type { OpenTab } from '../types';
import type { TabStatus } from '../../../hooks/useUnifiedTabState';
import { TabStatusIndicator } from './TabStatusIndicator';

interface SessionTabProps {
  tab: OpenTab;
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
 * Displays a chat icon, truncated title, and close button.
 * Supports keyboard navigation via onKeyDown prop.
 * 
 * Validates: Requirements 1.3, 1.4, 1.5, 1.6
 */
export const SessionTab = forwardRef<HTMLDivElement, SessionTabProps>(function SessionTab(
  {
    tab,
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
        'flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors',
        'min-w-0 max-w-[200px] flex-shrink-0',
        isActive
          ? 'bg-primary/15 text-primary'
          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      )}
    >
      {/* Chat icon */}
      <span className="material-symbols-outlined text-[18px] flex-shrink-0">
        chat_bubble
      </span>

      {/* Fix 8: Tab status indicator */}
      {status && <TabStatusIndicator status={status} />}

      {/* Truncated title */}
      <span className="truncate text-sm font-medium" title={tab.title}>
        {displayTitle}
      </span>

      {/* Close button */}
      <button
        onClick={handleClose}
        aria-label={`Close ${tab.title}`}
        className={clsx(
          'p-0.5 rounded transition-colors flex-shrink-0',
          'hover:bg-[var(--color-hover)]',
          isActive
            ? 'text-primary/70 hover:text-primary'
            : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        )}
      >
        <span className="material-symbols-outlined text-[16px]">close</span>
      </button>
    </div>
  );
}
);
