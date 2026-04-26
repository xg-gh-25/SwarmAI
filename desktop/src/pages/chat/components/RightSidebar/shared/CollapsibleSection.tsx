/**
 * Shared collapsible section wrapper for the Radar sidebar.
 *
 * Renders a clickable header row with a Material Symbols icon, section
 * label, count badge (small rounded pill), and one-line status hint.
 * Toggling expand/collapse is persisted to ``localStorage`` keyed by
 * ``radar-section-{name}`` using the ``RADAR_SECTION_KEY_PREFIX`` constant.
 *
 * On mount the component restores its state from ``localStorage``.  If the
 * stored value is missing or corrupt (non-boolean JSON), the
 * ``defaultExpanded`` prop is used as fallback.
 *
 * Expand/collapse is animated via a CSS ``max-height`` transition on the
 * content wrapper.
 *
 * Key exports:
 * - ``CollapsibleSection`` — The reusable section wrapper component
 */

import { useState, useCallback } from 'react';
import { CollapsibleSectionProps, RADAR_SECTION_KEY_PREFIX } from '../types';

/** Read persisted expand/collapse state from localStorage. */
function readPersistedState(name: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(`${RADAR_SECTION_KEY_PREFIX}${name}`);
    if (raw === null) return fallback;
    const parsed = JSON.parse(raw);
    return typeof parsed === 'boolean' ? parsed : fallback;
  } catch {
    return fallback;
  }
}

/** Write expand/collapse state to localStorage. */
function writePersistedState(name: string, value: boolean): void {
  try {
    localStorage.setItem(`${RADAR_SECTION_KEY_PREFIX}${name}`, JSON.stringify(value));
  } catch {
    // localStorage may be full or disabled — silently ignore
  }
}

export function CollapsibleSection({
  name,
  icon,
  label,
  count,
  statusHint,
  defaultExpanded = false,
  accent,
  children,
}: CollapsibleSectionProps) {
  const [expanded, setExpanded] = useState(() =>
    readPersistedState(name, defaultExpanded),
  );

  const contentId = `radar-section-content-${name}`;

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      const next = !prev;
      writePersistedState(name, next);
      return next;
    });
  }, [name]);

  return (
    <div
      className="border-b border-[var(--color-border)] last:border-b-0"
      style={accent ? { borderLeft: `2px solid ${accent}` } : undefined}
    >
      {/* Header row — clickable toggle */}
      <button
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left hover:bg-[var(--color-hover)] transition-colors"
        onClick={handleToggle}
        aria-expanded={expanded}
        aria-controls={contentId}
      >
        <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">
          {icon}
        </span>

        <span className="text-[10.5px] font-semibold uppercase tracking-[0.8px] text-[var(--color-text-muted)] truncate">
          {label}
        </span>

        {/* Count badge — small rounded pill */}
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {statusHint && !expanded && (
            <span className="text-[10px] text-[var(--color-text-muted)] truncate max-w-[120px]">
              {statusHint}
            </span>
          )}
          <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-medium rounded-full bg-[var(--color-hover)] text-[var(--color-text-muted)]">
            {count}
          </span>
          <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)] transition-transform duration-200"
            style={{ transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)' }}
          >
            expand_more
          </span>
        </span>
      </button>

      {/* Collapsible content with max-height transition */}
      <div
        id={contentId}
        className="overflow-hidden transition-[max-height] duration-200 ease-in-out"
        style={{ maxHeight: expanded ? '2000px' : '0px' }}
        role="region"
        aria-label={`${label} section content`}
      >
        <div className="px-3 pb-2">
          {children}
        </div>
      </div>
    </div>
  );
}
