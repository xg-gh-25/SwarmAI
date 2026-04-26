/**
 * Working section — actionable items from email/slack/calendar.
 *
 * Click → populate ChatInput with context.
 * Shared across WelcomeScreen and RadarSidebar.
 */

// WorkingSection — no React import needed (JSX transform)
import type { WorkingItem } from '../../../../services/system';
import type { ItemClickHandler } from '../RightSidebar/types';
import { buildWorkingContext } from './BriefingUtils';

const PRIORITY_DOT: Record<string, string> = {
  high: 'bg-red-400',
  medium: 'bg-yellow-400',
  low: 'bg-[var(--color-text-muted)]',
};

const SOURCE_LABEL: Record<string, string> = {
  email: 'email',
  'slack-dm': 'slack',
  'slack-channel': 'slack',
  calendar: 'cal',
  reflect: 'reflect',
};

interface WorkingSectionProps {
  items: WorkingItem[];
  onItemClick?: ItemClickHandler;
  /** Compact mode for sidebar (no summary text) */
  compact?: boolean;
}

export function WorkingSection({ items, onItemClick }: WorkingSectionProps) {
  if (items.length === 0) return null;

  return (
    <div className="space-y-0.5">
      {items.map((item, i) => {
        const dotCls = PRIORITY_DOT[item.priority] ?? PRIORITY_DOT.low;
        const sourceLabel = SOURCE_LABEL[item.source] ?? item.source;

        return (
          <button
            key={`${item.title}-${i}`}
            type="button"
            onClick={() => onItemClick?.(item.title, buildWorkingContext(item))}
            className="flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer group"
          >
            <span className={`shrink-0 w-2 h-2 rounded-full ${dotCls}`} />
            <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
              {item.title}
            </span>
            <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded">
              {sourceLabel}
            </span>
          </button>
        );
      })}
    </div>
  );
}
