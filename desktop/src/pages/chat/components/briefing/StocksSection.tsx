/**
 * Stocks section — today's portfolio analysis reports.
 *
 * Click → open report in file editor (no chat input).
 * Shared across WelcomeScreen and RadarSidebar.
 *
 * `defaultVisible` — when set, shows only N items with a "show more" toggle.
 * Used on WelcomeScreen to keep personal info low-profile.
 */

import { useState } from 'react';
import type { StockItem } from '../../../../services/system';
import { openWorkspaceFile } from './BriefingUtils';

const STATUS_ICON: Record<string, { icon: string; cls: string }> = {
  success: { icon: '✅', cls: '' },
  partial: { icon: '⚠️', cls: 'opacity-70' },
  failed: { icon: '❌', cls: 'opacity-50' },
};

interface StocksSectionProps {
  items: StockItem[];
  compact?: boolean;
  /** When set, show only this many items initially with a "show more" toggle. */
  defaultVisible?: number;
}

export function StocksSection({ items, compact, defaultVisible }: StocksSectionProps) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;

  const hasOverflow = defaultVisible != null && items.length > defaultVisible;
  const visibleItems = (!compact && hasOverflow && !expanded)
    ? items.slice(0, defaultVisible)
    : items;

  if (compact) {
    // Sidebar: compact inline list
    return (
      <div className="space-y-0.5">
        {items.map((item) => {
          const cfg = STATUS_ICON[item.status] ?? STATUS_ICON.success;
          return (
            <button
              key={item.ticker}
              type="button"
              onClick={() => openWorkspaceFile(item.reportFile)}
              className={`flex items-center gap-2 w-full text-left px-1 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer ${cfg.cls}`}
            >
              <span className="text-[11px]">{cfg.icon}</span>
              <span className="text-[13px] leading-5 text-[var(--color-text)] truncate flex-1">
                {item.name}
              </span>
              <span className="shrink-0 text-[10px] text-[var(--color-text-muted)] font-mono">
                {item.ticker}
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  // Welcome: grid layout with optional show more/less
  return (
    <div>
      <div className="grid grid-cols-2 gap-1">
        {visibleItems.map((item) => {
          const cfg = STATUS_ICON[item.status] ?? STATUS_ICON.success;
          return (
            <button
              key={item.ticker}
              type="button"
              onClick={() => openWorkspaceFile(item.reportFile)}
              className={`flex items-center gap-1.5 px-2 py-1 rounded hover:bg-[var(--color-bg-hover)] transition-colors cursor-pointer text-left ${cfg.cls}`}
            >
              <span className="text-[11px]">{cfg.icon}</span>
              <span className="text-[13px] text-[var(--color-text)] truncate">
                {item.name}
              </span>
            </button>
          );
        })}
      </div>
      {hasOverflow && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1.5 px-2 py-0.5 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors cursor-pointer"
        >
          {expanded ? '↑ show less' : `+ ${items.length - defaultVisible!} more`}
        </button>
      )}
    </div>
  );
}
