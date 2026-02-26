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
