/**
 * ContextUsageRing — shared SVG circular progress ring for context window usage.
 *
 * Rendered in ChatInput bottom row (size=20, showLabel).
 * Single source of truth for color thresholds:
 *   green (#22c55e) < 60%, yellow (#eab308) 60–80%, red (#ef4444) > 80%, gray for null.
 *
 * @exports ContextUsageRing      — The ring component
 * @exports ContextUsageRingProps  — Props interface
 * @exports getContextRingColor   — Shared color function for external callers
 */


/** Unified color thresholds for context usage — single source of truth. */
export function getContextRingColor(pct: number | null): string {
  if (pct === null) return 'var(--color-border)';
  if (pct > 80) return '#ef4444';   // red — critical
  if (pct > 60) return '#eab308';   // yellow — warning
  return '#22c55e';                  // green — healthy
}

export interface ContextUsageRingProps {
  /** Context usage percentage (0–100). Null = no data yet (gray ring). */
  pct: number | null;
  /** Size in pixels (default: 18). */
  size?: number;
  /** Show percentage number inside the ring (default: false). */
  showLabel?: boolean;
}

export function ContextUsageRing({ pct, size = 18, showLabel = false }: ContextUsageRingProps) {
  const strokeWidth = 2.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const fillPct = Math.min(Math.max(Number.isFinite(pct) ? pct! : 0, 0), 100);
  const offset = circumference - (fillPct / 100) * circumference;
  const strokeColor = getContextRingColor(pct);

  return (
    <div
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      title={pct !== null ? `${Math.round(pct)}% context used` : 'No context data yet'}
      aria-label={pct !== null ? `${Math.round(pct)}% context used` : 'No context data yet'}
    >
      <svg width={size} height={size} className="transform -rotate-90">
        <circle cx={size/2} cy={size/2} r={radius}
          fill="none" stroke="var(--color-hover)" strokeWidth={strokeWidth}
          {...(pct === null ? { strokeDasharray: '2 2', opacity: 0.5 } : {})} />
        <circle cx={size/2} cy={size/2} r={radius}
          fill="none" stroke={strokeColor} strokeWidth={strokeWidth}
          strokeDasharray={circumference} strokeDashoffset={offset}
          strokeLinecap="round" className="transition-all duration-500" />
      </svg>
      {showLabel && pct !== null && (
        <span
          className="absolute inset-0 flex items-center justify-center text-[7px] font-semibold text-[var(--color-text-muted)]"
          style={{ lineHeight: 1 }}
        >
          {Math.round(pct)}
        </span>
      )}
    </div>
  );
}
