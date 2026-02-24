import { useState } from 'react';
import clsx from 'clsx';

export interface ChipItem {
  id: string;
  name: string;
  description?: string;
}

interface ReadOnlyChipsProps {
  label: string;
  icon?: string;
  items: ChipItem[];
  emptyText?: string;
  loading?: boolean;
  className?: string;
  /** Override the count badge with custom text (e.g., "All") */
  badgeOverride?: string;
}

/**
 * A compact read-only component to display a count badge with hover tooltip.
 * Shows icon + count, hovering reveals the full list of item names.
 * Used in ChatPage to show agent's configured Skills, MCPs, and Plugins.
 */
export default function ReadOnlyChips({
  label,
  icon,
  items,
  emptyText = 'None configured',
  loading = false,
  className,
  badgeOverride,
}: ReadOnlyChipsProps) {
  const [isHovering, setIsHovering] = useState(false);

  const count = items.length;
  const showBadge = badgeOverride || count > 0;

  return (
    <div
      className={clsx('relative', className)}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
    >
      {/* Compact Badge */}
      <div
        className={clsx(
          'flex items-center gap-1.5 px-2 py-1 rounded-lg cursor-default transition-colors',
          showBadge
            ? 'bg-[var(--color-hover)]/50 hover:bg-[var(--color-hover)]'
            : 'text-[var(--color-text-muted)]/50'
        )}
      >
        {icon && (
          <span className={clsx(
            'material-symbols-outlined text-sm',
            showBadge ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-muted)]/50'
          )}>
            {icon}
          </span>
        )}
        <span className={clsx(
          'text-xs font-medium',
          showBadge ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-muted)]/50'
        )}>
          {label}
        </span>
        {loading ? (
          <div className="w-3 h-3 border border-muted border-t-primary rounded-full animate-spin" />
        ) : showBadge ? (
          <span className="text-xs font-semibold text-primary bg-primary/20 px-1.5 py-0.5 rounded-full min-w-[1.25rem] text-center">
            {badgeOverride || count}
          </span>
        ) : null}
      </div>

      {/* Hover Tooltip */}
      {isHovering && !loading && (
        <div className="absolute bottom-full left-0 mb-2 z-50 animate-fade-in">
          <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl p-3 min-w-[180px] max-w-[300px]">
            {/* Header */}
            <div className="flex items-center gap-2 mb-2 pb-2 border-b border-[var(--color-border)]">
              {icon && (
                <span className="material-symbols-outlined text-primary text-sm">{icon}</span>
              )}
              <span className="text-xs font-semibold text-[var(--color-text)] uppercase tracking-wider">
                {label}
              </span>
              {count > 0 && (
                <span className="text-xs text-[var(--color-text-muted)] ml-auto">
                  {count} item{count !== 1 ? 's' : ''}
                </span>
              )}
            </div>

            {/* Items List */}
            {count === 0 ? (
              <span className="text-xs text-[var(--color-text-muted)] italic">{emptyText}</span>
            ) : (
              <div className="space-y-1 max-h-[200px] overflow-y-auto">
                {items.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-start gap-2 text-xs"
                    title={item.description}
                  >
                    <span className="text-primary mt-0.5">•</span>
                    <div className="flex-1 min-w-0">
                      <span className="text-[var(--color-text)]">{item.name}</span>
                      {item.description && (
                        <p className="text-[var(--color-text-muted)]/70 text-[10px] truncate mt-0.5">
                          {item.description}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          {/* Arrow */}
          <div className="absolute left-4 -bottom-1 w-2 h-2 bg-[var(--color-card)] border-b border-r border-[var(--color-border)] transform rotate-45" />
        </div>
      )}
    </div>
  );
}
