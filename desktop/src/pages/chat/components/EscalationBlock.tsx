/**
 * Inline escalation decision block — renders pipeline judgment decisions
 * directly in the chat stream as a bordered card.
 *
 * Shows severity badge, reason text, decision options with recommended
 * highlight, and resolved state. Follows InlinePermissionRequest visual
 * pattern (card with status transitions).
 *
 * States:
 * 1. Pending — severity badge + reason + options list (recommended highlighted)
 * 2. Resolved — collapsed with resolution text
 *
 * @exports EscalationBlock
 */

import type { EscalationOption } from '../../../types';

const SEVERITY_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  critical: { bg: 'bg-red-500/15', text: 'text-red-400', label: 'CRITICAL' },
  high: { bg: 'bg-orange-500/15', text: 'text-orange-400', label: 'HIGH' },
  medium: { bg: 'bg-yellow-500/15', text: 'text-yellow-400', label: 'MEDIUM' },
  low: { bg: 'bg-blue-500/15', text: 'text-blue-400', label: 'LOW' },
};

interface EscalationBlockProps {
  id: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  reason: string;
  options: EscalationOption[];
  status: 'pending' | 'resolved';
  resolution?: string;
  /** Called when user clicks an option — sends as chat response. */
  onSelectOption?: (escalationId: string, optionLabel: string) => void;
}

export function EscalationBlock({
  id,
  severity,
  reason,
  options,
  status,
  resolution,
  onSelectOption,
}: EscalationBlockProps) {
  const style = SEVERITY_STYLES[severity] || SEVERITY_STYLES.medium;

  if (status === 'resolved') {
    return (
      <div data-testid={`escalation-${id}`} className="my-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-base text-green-400">check_circle</span>
          <span className="text-sm text-[var(--color-text-muted)]">Escalation resolved</span>
          {resolution && (
            <span className="text-sm text-[var(--color-text)]">— {resolution}</span>
          )}
        </div>
      </div>
    );
  }

  return (
    <div data-testid={`escalation-${id}`} className="my-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden">
      {/* Header with severity badge */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--color-border)]">
        <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
          escalator_warning
        </span>
        <span className={`text-xs font-semibold px-1.5 py-0.5 rounded ${style.bg} ${style.text}`}>
          {style.label}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">Escalation</span>
      </div>

      {/* Reason */}
      <div className="px-4 py-3">
        <p className="text-sm text-[var(--color-text)] leading-relaxed">{reason}</p>
      </div>

      {/* Options — clickable when handler provided */}
      {options.length > 0 && (
        <div className="px-4 pb-3 space-y-1.5">
          {options.map((opt, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onSelectOption?.(id, opt.label)}
              disabled={!onSelectOption}
              className={`group/opt w-full text-left flex items-start gap-2 px-3 py-2 rounded-md text-sm transition-colors ${
                opt.recommended
                  ? 'border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 hover:bg-[var(--color-primary)]/15'
                  : 'border border-[var(--color-border)] bg-[var(--color-bg)] hover:bg-[var(--color-hover)]'
              } ${onSelectOption ? 'cursor-pointer' : 'cursor-default'}`}
            >
              <span className="text-[var(--color-text-muted)] shrink-0 mt-0.5">
                {opt.recommended ? '★' : `${i + 1}.`}
              </span>
              <div className="flex-1">
                <span className="font-medium text-[var(--color-text)]">{opt.label}</span>
                {opt.description && (
                  <span className="text-[var(--color-text-muted)]"> — {opt.description}</span>
                )}
              </div>
              {onSelectOption && (
                <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)] opacity-0 group-hover/opt:opacity-100 shrink-0 mt-0.5">
                  send
                </span>
              )}
            </button>
          ))}
          {onSelectOption && (
            <p className="text-[10px] text-[var(--color-text-muted)] mt-1 px-1">
              Click an option to respond
            </p>
          )}
        </div>
      )}
    </div>
  );
}
