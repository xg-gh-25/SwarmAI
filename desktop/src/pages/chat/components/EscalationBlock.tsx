/**
 * Inline escalation decision block — a pipeline judgment the user settles, in the one
 * frame every card that waits on the user uses (ActCard; design
 * docs/design/2026-10-26-turn-cards-unified.md §1 D).
 *
 * States:
 * 1. Pending — "Swarm needs your call", the severity as grey header meta, the reason,
 *    the options as ActOption boxes (a recommended one says so); clicking sends it.
 * 2. Resolved — one grey line that keeps the decision: the reason → the resolution.
 *
 * @exports EscalationBlock
 */

import type { EscalationOption } from '../../../types';
import { useTranslation } from 'react-i18next';
import { tf } from '../tf';
import { ActDone, ActFrame, ActOption } from '../../../components/common/ActCard';

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
  const { t } = useTranslation();

  if (status === 'resolved') {
    return (
      <div data-testid={`escalation-${id}`}>
        <ActDone label={tf(t, 'chat.escalation.decided', 'Decided')}>
          {reason}{resolution && <> → <span className="text-[var(--color-text)]">{resolution}</span></>}
        </ActDone>
      </div>
    );
  }

  return (
    <div data-testid={`escalation-${id}`}>
      <ActFrame icon="flag" title={tf(t, 'chat.escalation.title', 'Swarm needs your call')}
        meta={tf(t, `chat.escalation.severity.${severity}`, severity)}>
        <p className="text-[13px] leading-relaxed text-[var(--color-text)]">{reason}</p>
        {options.length > 0 && (
          <div className="space-y-1.5">
            {options.map((opt, i) => (
              <ActOption key={i} label={opt.label} description={opt.description || undefined}
                recommended={!!opt.recommended}
                recommendedLabel={tf(t, 'chat.escalation.recommended', 'Recommended')}
                onClick={() => onSelectOption?.(id, opt.label)} disabled={!onSelectOption} />
            ))}
          </div>
        )}
      </ActFrame>
    </div>
  );
}
