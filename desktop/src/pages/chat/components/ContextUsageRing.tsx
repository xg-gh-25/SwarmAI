/**
 * ContextUsageRing — SVG circular progress ring for context window usage.
 *
 * Renders a small ring indicator showing what percentage of the context
 * window has been consumed. Placed in the ChatInput bottom row after the
 * TSCC popover button.
 *
 * Color thresholds: green < 70%, amber 70–84%, red >= 85%, gray for null.
 *
 * @exports ContextUsageRing      — The ring component
 * @exports ContextUsageRingProps  — Props interface
 */


export interface ContextUsageRingProps {
  /** Context usage percentage (0–100). Null = no data yet (gray ring). */
  pct: number | null;
  /** Size in pixels (default: 18). */
  size?: number;
}

export function ContextUsageRing({ pct, size = 18 }: ContextUsageRingProps) {
  const strokeWidth = 2.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const fillPct = Math.min(Math.max(Number.isFinite(pct) ? pct! : 0, 0), 100);
  const offset = circumference - (fillPct / 100) * circumference;

  const strokeColor = pct === null ? 'var(--color-border)'
    : fillPct >= 85 ? '#ef4444'
    : fillPct >= 70 ? '#f59e0b'
    : '#10b981';

  return (
    <div
      className="relative inline-flex items-center justify-center"
      title={pct !== null ? `${pct}% context used` : 'No context data yet'}
      aria-label={pct !== null ? `${pct}% context used` : 'No context data yet'}
    >
      <svg width={size} height={size} className="transform -rotate-90">
        <circle cx={size/2} cy={size/2} r={radius}
          fill="none" stroke="var(--color-border)" strokeWidth={strokeWidth}
          {...(pct === null ? { strokeDasharray: '2 2', opacity: 0.5 } : {})} />
        <circle cx={size/2} cy={size/2} r={radius}
          fill="none" stroke={strokeColor} strokeWidth={strokeWidth}
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round" className="transition-all duration-500" />
      </svg>
    </div>
  );
}
