import { useCallback } from 'react';
import type { WorkspaceSection } from '../../types/section';

/**
 * Section icon and label configuration for the Daily Work Operating Loop.
 * Requirements: 3.6-3.11, 9.5, 9.6, 9.7
 */
export const SECTION_CONFIG: Record<WorkspaceSection, { icon: string; label: string }> = {
  signals: { icon: '🔔', label: 'Signals' },
  plan: { icon: '🗓️', label: 'Plan' },
  execute: { icon: '▶️', label: 'Execute' },
  communicate: { icon: '💬', label: 'Communicate' },
  artifacts: { icon: '📦', label: 'Artifacts' },
  reflection: { icon: '🧠', label: 'Reflection' },
};

export interface SectionHeaderProps {
  section: WorkspaceSection;
  totalCount: number;
  subCounts?: Record<string, number>;
  isExpanded: boolean;
  isActive?: boolean;
  onToggle: (section: WorkspaceSection) => void;
  tabIndex?: number;
  onKeyDown?: (e: React.KeyboardEvent, section: WorkspaceSection) => void;
}

export default function SectionHeader({
  section,
  totalCount,
  subCounts: _subCounts,
  isExpanded,
  isActive = false,
  onToggle,
  tabIndex = 0,
  onKeyDown,
}: SectionHeaderProps) {
  const config = SECTION_CONFIG[section];

  const handleClick = useCallback(() => {
    onToggle(section);
  }, [onToggle, section]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onToggle(section);
      }
      onKeyDown?.(e, section);
    },
    [onToggle, section, onKeyDown]
  );

  return (
    <div
      className={`flex items-center gap-2 px-3 py-2 cursor-pointer select-none transition-colors rounded-sm ${
        isActive
          ? 'bg-[var(--color-primary)] bg-opacity-10 text-[var(--color-text)]'
          : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
      }`}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      role="button"
      aria-expanded={isExpanded}
      aria-label={`${config.label} section, ${totalCount} items`}
      tabIndex={tabIndex}
      data-testid={`section-header-${section}`}
      data-section={section}
    >
      {/* Expand/collapse chevron */}
      <span
        className="text-xs text-[var(--color-text-muted)] w-4 flex-shrink-0 transition-transform"
        style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}
      >
        ▶
      </span>

      {/* Section icon */}
      <span className="text-base flex-shrink-0" aria-hidden="true">
        {config.icon}
      </span>

      {/* Section label */}
      <span className="text-sm font-medium flex-1 truncate">{config.label}</span>

      {/* Count badge */}
      {totalCount > 0 && (
        <span
          className="text-xs px-1.5 py-0.5 rounded-full bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] min-w-[20px] text-center"
          data-testid={`section-count-${section}`}
        >
          {totalCount}
        </span>
      )}
    </div>
  );
}
