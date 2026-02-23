import type { SectionCounts } from '../../types/section';

export interface RecommendedItem {
  section: string;
  label: string;
  count: number;
}

/**
 * Extract top N recommended items from section counts.
 * Sorts by count descending (proxy for priority/activity).
 * Only includes sub-categories with count > 0.
 *
 * Requirements: 37 (SwarmWS Global View Aggregation Rules)
 */
export function getRecommendedItems(
  counts: SectionCounts,
  topN: number = 3
): RecommendedItem[] {
  const items: RecommendedItem[] = [];

  // Signals - high priority sub-categories
  if (counts.signals.overdue > 0) {
    items.push({ section: 'signals', label: 'Overdue Signals', count: counts.signals.overdue });
  }
  if (counts.signals.pending > 0) {
    items.push({ section: 'signals', label: 'Pending Signals', count: counts.signals.pending });
  }

  // Plan - today's focus
  if (counts.plan.today > 0) {
    items.push({ section: 'plan', label: "Today's Focus", count: counts.plan.today });
  }
  if (counts.plan.blocked > 0) {
    items.push({ section: 'plan', label: 'Blocked Plans', count: counts.plan.blocked });
  }

  // Execute - active work
  if (counts.execute.blocked > 0) {
    items.push({ section: 'execute', label: 'Blocked Tasks', count: counts.execute.blocked });
  }
  if (counts.execute.wip > 0) {
    items.push({ section: 'execute', label: 'WIP Tasks', count: counts.execute.wip });
  }

  // Communicate - pending replies
  if (counts.communicate.pendingReply > 0) {
    items.push({ section: 'communicate', label: 'Pending Replies', count: counts.communicate.pendingReply });
  }

  // Sort by count descending (higher count = more urgent)
  items.sort((a, b) => b.count - a.count);

  return items.slice(0, topN);
}

const SECTION_ICONS: Record<string, string> = {
  signals: '🔔',
  plan: '🗓️',
  execute: '▶️',
  communicate: '💬',
  artifacts: '📦',
  reflection: '🧠',
};

export interface RecommendedGroupProps {
  counts: SectionCounts;
  topN?: number;
  onItemClick?: (section: string) => void;
}

/**
 * RecommendedGroup - Shows top N recommended items in SwarmWS Global View.
 * Only rendered when in SwarmWS Global View (opinionated cockpit).
 *
 * Requirements: 37 (SwarmWS Global View Aggregation Rules)
 */
export default function RecommendedGroup({
  counts,
  topN = 3,
  onItemClick,
}: RecommendedGroupProps) {
  const items = getRecommendedItems(counts, topN);

  if (items.length === 0) return null;

  return (
    <div
      className="px-3 py-2 border-b border-[var(--color-border)]"
      data-testid="recommended-group"
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-xs">⭐</span>
        <span className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Recommended
        </span>
      </div>
      <div className="space-y-0.5">
        {items.map((item, idx) => (
          <div
            key={`${item.section}-${item.label}-${idx}`}
            className="flex items-center gap-2 px-2 py-1 text-sm rounded cursor-pointer text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            onClick={() => onItemClick?.(item.section)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                onItemClick?.(item.section);
              }
            }}
            data-testid={`recommended-item-${idx}`}
          >
            <span className="text-xs">{SECTION_ICONS[item.section] ?? '📋'}</span>
            <span className="flex-1 truncate text-xs">{item.label}</span>
            <span className="text-xs font-medium text-[var(--color-primary)] min-w-[16px] text-right">
              {item.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
