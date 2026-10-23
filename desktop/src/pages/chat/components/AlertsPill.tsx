/**
 * AlertsPill — the 🔔 "Needs You" entry in the left-sidebar top slot.
 *
 * A LABELLED row (run_843962a5: a bare bell gave no hint). Rendered in the
 * left-sidebar top slot, styled to match the History row (full-width, same
 * padding/gap/font). The red alert color scheme is its distinct attention
 * identity.
 *
 * 2026-08-08 (unified Need You channel): clicking now OPENS THE FULLSCREEN
 * needs-you overlay (dispatchUiCommand('show-needs-you')) instead of a small local
 * popover. The overlay has room for the full double-axis (tier×brain) queue with
 * no see-more fold; the pill is just the entry point + a live count badge. The
 * count comes from GET /api/attention counts (single backend authority).
 *
 * States:
 *   - count === 0  → CALM: neutral grey, no badge.
 *   - count  >  0  → ALERT: red-tinted + pulsing count badge.
 */
import { dispatchUiCommand } from '../../../utils/uiCommands';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';

interface AlertsPillProps {
  /** Total Need You items (counts.blocking + counts.review). */
  count: number;
  /** Warm the Need You overlay's code + data on hover/focus (prefetchNav). */
  onPrefetch?: () => void;
}

export function AlertsPill({ count, onPrefetch }: AlertsPillProps) {
  const { t } = useTranslation();
  const calm = count === 0;

  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={() => dispatchUiCommand('show-needs-you')}
        onMouseEnter={onPrefetch}
        onFocus={onPrefetch}
        aria-label={calm
          ? tf(t, 'chat.alerts.ariaCalm', 'Alerts — nothing needs you')
          : tf(t, 'chat.alerts.aria', 'Alerts — {{n}} item(s) need you', { n: count })}
        title={calm
          ? tf(t, 'chat.alerts.titleCalm', 'Nothing needs you')
          : tf(t, 'chat.alerts.title', '{{n}} item(s) need you — open Need You', { n: count })}
        data-testid="sidebar-alerts-slot"
        className={[
          'mt-0.5 w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 border transition-colors',
          calm
            ? 'border-transparent text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            : 'bg-red-500/10 border-red-500/35 text-red-300 hover:bg-red-500/20 hover:border-red-500/60',
        ].join(' ')}
      >
        <span className="w-4 flex items-center justify-center">
          <span className="material-symbols-outlined icon-md leading-none">notifications</span>
        </span>
        <span className="flex-1 text-left text-[11.5px] font-mono tracking-wide whitespace-nowrap">{tf(t, 'chat.alerts.label', 'Needs You')}</span>
        {!calm && (
          <span className="min-w-[16px] h-4 px-1 rounded-lg bg-red-500 text-white text-[11px] font-bold font-mono flex items-center justify-center animate-pulse">
            {count}
          </span>
        )}
      </button>
    </div>
  );
}
