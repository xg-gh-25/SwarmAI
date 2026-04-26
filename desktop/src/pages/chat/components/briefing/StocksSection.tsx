/**
 * Stocks section — today's portfolio analysis reports.
 *
 * Click → open report in file editor (no chat input).
 * Shared across WelcomeScreen and RadarSidebar.
 */

// StocksSection — no React import needed (JSX transform)
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
}

export function StocksSection({ items, compact }: StocksSectionProps) {
  if (items.length === 0) return null;

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

  // Welcome: grid layout
  return (
    <div className="grid grid-cols-2 gap-1">
      {items.map((item) => {
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
  );
}
