/**
 * ZoneSeparator — renders a non-interactive zone label with horizontal line separators.
 *
 * Used by `VirtualizedTree` to visually divide the workspace explorer into
 * semantic zones ("Shared Knowledge", "Active Work"). The component is
 * positioned by `react-window` via the `style` prop.
 *
 * Key exports:
 * - `ZoneSeparator`      — The memoised separator component
 * - `ZoneSeparatorProps`  — Prop interface consumed by `VirtualizedTree`
 *
 * Visual behaviour:
 * - Centered label text flanked by horizontal lines
 * - Fixed 32 px height matching tree row height
 * - Uses `--color-explorer-zone-label` for text colour
 * - Uses `--color-explorer-zone-separator` for line colour
 * - Non-interactive: no click handler, no pointer cursor
 *
 * Accessibility:
 * - `role="separator"` with `aria-orientation="horizontal"`
 *
 * Requirements: 10.1, 10.2, 14.3
 */

import React from 'react';

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

export interface ZoneSeparatorProps {
  /** The zone label text (e.g. "Shared Knowledge", "Active Work"). */
  label: string;
  /** Positioning style injected by react-window (top, height, position). */
  style: React.CSSProperties;
}

/* ------------------------------------------------------------------ */
/*  Styles                                                             */
/* ------------------------------------------------------------------ */

const LINE_STYLE: React.CSSProperties = {
  flex: 1,
  height: '1px',
  backgroundColor: 'var(--color-explorer-zone-separator)',
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const ZoneSeparator: React.FC<ZoneSeparatorProps> = React.memo(function ZoneSeparator({
  label,
  style,
}) {
  return (
    <div
      data-testid="zone-separator"
      role="separator"
      aria-orientation="horizontal"
      style={{
        ...style,
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '0 12px',
        height: '32px',
        boxSizing: 'border-box',
        userSelect: 'none',
        pointerEvents: 'none',
      }}
    >
      <span style={LINE_STYLE} aria-hidden="true" />
      <span
        style={{
          fontSize: '11px',
          fontWeight: 500,
          letterSpacing: '0.03em',
          textTransform: 'uppercase',
          whiteSpace: 'nowrap',
          color: 'var(--color-explorer-zone-label)',
        }}
      >
        {label}
      </span>
      <span style={LINE_STYLE} aria-hidden="true" />
    </div>
  );
});

export default ZoneSeparator;

/* ------------------------------------------------------------------ */
/*  SectionHeader — 3-tier primary/system section header               */
/* ------------------------------------------------------------------ */

export interface SectionHeaderProps {
  label: string;
  count: number;
  isCollapsed: boolean;
  dimmed?: boolean;
  accentBg?: string;
  accentBorder?: string;
  onToggle: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  style: React.CSSProperties;
}

export const SectionHeader: React.FC<SectionHeaderProps> = React.memo(function SectionHeader({
  label,
  count,
  isCollapsed,
  dimmed,
  accentBg,
  accentBorder,
  onToggle,
  onContextMenu,
  style,
}) {
  return (
    <button
      data-testid="section-header"
      onClick={onToggle}
      onContextMenu={onContextMenu}
      style={{
        ...style,
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        padding: '0 12px',
        height: '32px',
        boxSizing: 'border-box',
        userSelect: 'none',
        cursor: 'pointer',
        width: '100%',
        border: 'none',
        borderTop: '2px solid var(--color-section-divider, #222236)',
        borderLeft: accentBorder ? `3px solid ${accentBorder}` : 'none',
        background: accentBg ?? 'transparent',
        opacity: dimmed ? 0.5 : 1,
        transition: 'opacity 150ms ease',
        fontFamily: 'inherit',
      }}
      onMouseEnter={(e) => {
        if (dimmed) (e.currentTarget.style.opacity = '0.7');
      }}
      onMouseLeave={(e) => {
        if (dimmed) (e.currentTarget.style.opacity = '0.5');
      }}
    >
      {/* Chevron */}
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{
          flexShrink: 0,
          transition: 'transform 150ms ease',
          transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
          color: 'var(--color-text-muted)',
        }}
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>

      {/* Section label */}
      <span
        style={{
          fontSize: '13.5px',
          fontWeight: 700,
          color: 'var(--color-text-secondary, var(--color-text))',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>

      {/* Count pill */}
      {count > 0 && (
        <span
          style={{
            marginLeft: 'auto',
            fontSize: '10px',
            fontWeight: 500,
            padding: '0 6px',
            borderRadius: '9999px',
            backgroundColor: 'var(--color-hover, rgba(255,255,255,0.06))',
            color: 'var(--color-text-dim, var(--color-text-muted))',
            lineHeight: '18px',
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
});
