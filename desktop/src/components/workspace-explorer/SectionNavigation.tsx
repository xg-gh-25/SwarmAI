import { useState, useCallback } from 'react';
import type { WorkspaceSection, SectionCounts } from '../../types/section';
import SectionHeader from './SectionHeader';
import SectionContent from './SectionContent';

const SECTIONS: WorkspaceSection[] = [
  'signals',
  'plan',
  'execute',
  'communicate',
  'artifacts',
  'reflection',
];

/**
 * Extract total count for a section from SectionCounts.
 */
function getSectionTotal(counts: SectionCounts, section: WorkspaceSection): number {
  return counts[section]?.total ?? 0;
}

/**
 * Extract sub-category counts for a section from SectionCounts.
 */
function getSubCounts(counts: SectionCounts, section: WorkspaceSection): Record<string, number> {
  const sectionData = counts[section];
  if (!sectionData) return {};
  // Return all keys except 'total'
  const result: Record<string, number> = {};
  for (const [key, value] of Object.entries(sectionData)) {
    if (key !== 'total') {
      result[key] = value;
    }
  }
  return result;
}

export interface SectionNavigationProps {
  counts: SectionCounts;
  activeSection?: WorkspaceSection | null;
  /** Effective workspace ID for API calls: 'all' when global, actual ID when scoped */
  effectiveWorkspaceId?: string;
  onSectionClick?: (section: WorkspaceSection) => void;
  onSubCategoryClick?: (section: WorkspaceSection, subCategory: string) => void;
  /** Render custom content for a section (e.g., file tree for artifacts) */
  renderSectionExtra?: (section: WorkspaceSection) => React.ReactNode;
}

/**
 * SectionNavigation - Six collapsible section headers with icons and counts.
 * Counts are driven by the effectiveWorkspaceId passed from the parent:
 * - Global View passes 'all' → aggregated counts across all non-archived workspaces
 * - Scoped View passes actual workspace_id → workspace-only counts
 * Requirements: 3.4, 3.5, 9.5, 9.6, 9.14, 37.11
 */
export default function SectionNavigation({
  counts,
  activeSection,
  effectiveWorkspaceId: _effectiveWorkspaceId,
  onSectionClick,
  onSubCategoryClick,
  renderSectionExtra,
}: SectionNavigationProps) {
  const [expandedSections, setExpandedSections] = useState<Set<WorkspaceSection>>(new Set());

  const handleToggle = useCallback(
    (section: WorkspaceSection) => {
      setExpandedSections((prev) => {
        const next = new Set(prev);
        if (next.has(section)) {
          next.delete(section);
        } else {
          next.add(section);
        }
        return next;
      });
      onSectionClick?.(section);
    },
    [onSectionClick]
  );

  // Keyboard navigation: arrow keys between sections
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent, section: WorkspaceSection) => {
      const idx = SECTIONS.indexOf(section);
      if (e.key === 'ArrowDown' && idx < SECTIONS.length - 1) {
        e.preventDefault();
        const nextEl = document.querySelector(
          `[data-section="${SECTIONS[idx + 1]}"]`
        ) as HTMLElement;
        nextEl?.focus();
      } else if (e.key === 'ArrowUp' && idx > 0) {
        e.preventDefault();
        const prevEl = document.querySelector(
          `[data-section="${SECTIONS[idx - 1]}"]`
        ) as HTMLElement;
        prevEl?.focus();
      }
    },
    []
  );

  return (
    <div
      className="flex-1 overflow-auto"
      data-testid="section-navigation"
      role="navigation"
      aria-label="Workspace sections"
    >
      {SECTIONS.map((section) => {
        const isExpanded = expandedSections.has(section);
        const total = getSectionTotal(counts, section);
        const subCounts = getSubCounts(counts, section);

        return (
          <div key={section}>
            <SectionHeader
              section={section}
              totalCount={total}
              subCounts={subCounts}
              isExpanded={isExpanded}
              isActive={activeSection === section}
              onToggle={handleToggle}
              onKeyDown={handleKeyDown}
            />
            {isExpanded && (
              <>
                <SectionContent
                  section={section}
                  subCounts={subCounts}
                  onSubCategoryClick={onSubCategoryClick}
                />
                {renderSectionExtra?.(section)}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
