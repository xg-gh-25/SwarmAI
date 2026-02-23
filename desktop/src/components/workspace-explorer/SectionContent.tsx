import type { WorkspaceSection } from '../../types/section';

/**
 * Sub-category definitions for each section.
 * Requirements: 3.6-3.11, 9.7
 */
export const SECTION_SUB_CATEGORIES: Record<WorkspaceSection, { key: string; label: string }[]> = {
  signals: [
    { key: 'pending', label: 'Pending' },
    { key: 'overdue', label: 'Overdue' },
    { key: 'inDiscussion', label: 'In Discussion' },
  ],
  plan: [
    { key: 'today', label: "Today's Focus" },
    { key: 'upcoming', label: 'Upcoming' },
    { key: 'blocked', label: 'Blocked' },
  ],
  execute: [
    { key: 'draft', label: 'Draft' },
    { key: 'wip', label: 'WIP' },
    { key: 'blocked', label: 'Blocked' },
    { key: 'completed', label: 'Completed' },
  ],
  communicate: [
    { key: 'pendingReply', label: 'Pending Replies' },
    { key: 'aiDraft', label: 'AI Drafts' },
    { key: 'followUp', label: 'Follow-ups' },
  ],
  artifacts: [
    { key: 'plan', label: 'Plans' },
    { key: 'report', label: 'Reports' },
    { key: 'doc', label: 'Docs' },
    { key: 'decision', label: 'Decisions' },
  ],
  reflection: [
    { key: 'dailyRecap', label: 'Daily Recap' },
    { key: 'weeklySummary', label: 'Weekly Summary' },
    { key: 'lessonsLearned', label: 'Lessons Learned' },
  ],
};

export interface SectionContentProps {
  section: WorkspaceSection;
  subCounts: Record<string, number>;
  onSubCategoryClick?: (section: WorkspaceSection, subCategory: string) => void;
  onKeyDown?: (e: React.KeyboardEvent, section: WorkspaceSection, subCategory: string) => void;
}

export default function SectionContent({
  section,
  subCounts,
  onSubCategoryClick,
  onKeyDown,
}: SectionContentProps) {
  const subCategories = SECTION_SUB_CATEGORIES[section];

  return (
    <div
      className="pl-9 pr-3 pb-1"
      data-testid={`section-content-${section}`}
      role="group"
      aria-label={`${section} sub-categories`}
    >
      {subCategories.map((sub) => {
        const count = subCounts[sub.key] ?? 0;
        return (
          <div
            key={sub.key}
            className="flex items-center gap-2 px-2 py-1 text-sm rounded cursor-pointer text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            onClick={() => onSubCategoryClick?.(section, sub.key)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                onSubCategoryClick?.(section, sub.key);
              }
              onKeyDown?.(e, section, sub.key);
            }}
            role="button"
            tabIndex={0}
            data-testid={`sub-category-${section}-${sub.key}`}
          >
            <span className="flex-1 truncate">{sub.label}</span>
            {count > 0 && (
              <span className="text-xs text-[var(--color-text-muted)] min-w-[16px] text-right">
                {count}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
