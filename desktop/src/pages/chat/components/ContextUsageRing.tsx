/* eslint-disable react-refresh/only-export-components */
/**
 * ContextUsageRing — shared SVG circular progress ring for context window usage.
 *
 * Rendered in ChatInput bottom row (size=18, showLabel).
 * Colour follows the BACKEND's level for the snapshot (the backend owns the
 * warn/critical thresholds — PromptBuilder._CONTEXT_WARN_PCT / _CONTEXT_CRITICAL_PCT):
 *   green (#22c55e) ok, yellow (#eab308) warn, red (#ef4444) critical, gray for no data.
 *
 * @exports ContextUsageRing      — The ring component
 * @exports ContextUsageRingProps  — Props interface
 * @exports getContextRingColor   — Shared color function for external callers
 */
import { useTranslation } from 'react-i18next';

/** Ring colour for a backend context level (null = no data yet). */
export function getContextRingColor(level: 'ok' | 'warn' | 'critical' | null): string {
  if (level === null) return 'var(--color-border)';
  if (level === 'critical') return '#ef4444';   // red
  if (level === 'warn') return '#eab308';       // yellow
  return '#22c55e';                             // green
}

export interface ContextUsageRingProps {
  /** Context usage percentage (0–100). Null = no data yet (gray ring). */
  pct: number | null;
  /** The backend's level for this snapshot; drives the colour. */
  level?: 'ok' | 'warn' | 'critical' | null;
  /** Size in pixels (default: 18). */
  size?: number;
  /** Show percentage label beside the ring (default: false). */
  showLabel?: boolean;
}

export function ContextUsageRing({ pct, level = null, size = 18, showLabel = false }: ContextUsageRingProps) {
  const { t } = useTranslation();
  const ringLabel = pct !== null
    ? t('ui.ContextUsageRing.used', { pct: Math.round(pct) })
    : t('ui.ContextUsageRing.noData');
  const strokeWidth = 2.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const fillPct = Math.min(Math.max(Number.isFinite(pct) ? pct! : 0, 0), 100);
  const offset = circumference - (fillPct / 100) * circumference;
  const strokeColor = getContextRingColor(pct === null ? null : (level ?? 'ok'));

  return (
    <div
      className="relative inline-flex items-center gap-1.5"
      title={ringLabel}
      aria-label={ringLabel}
    >
      <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="transform -rotate-90">
          <circle cx={size/2} cy={size/2} r={radius}
            fill="none" stroke="var(--color-hover)" strokeWidth={strokeWidth}
            {...(pct === null ? { strokeDasharray: '2 2', opacity: 0.5 } : {})} />
          <circle cx={size/2} cy={size/2} r={radius}
            fill="none" stroke={strokeColor} strokeWidth={strokeWidth}
            strokeDasharray={circumference} strokeDashoffset={offset}
            strokeLinecap="round" className="transition-all duration-500" />
        </svg>
      </div>
      {showLabel && (
        <span className="text-[11px] font-medium text-[var(--color-text-muted)]" style={{ lineHeight: 1 }}>
          {pct !== null ? `${Math.round(pct)}%` : '—'}
        </span>
      )}
    </div>
  );
}
