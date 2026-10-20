/**
 * TurnPhaseLine — what the turn is doing before its first token.
 *
 * The backend sends `turn_phase` (core/turn_phase.py) as a turn moves through
 * preparing → starting (cold spawn only) → waiting for the model. Before this,
 * the wait (median 15s, p90 60s) showed only a pulsing dot. One line: spinner,
 * the step, seconds in that step. It disappears with the first real stream event.
 *
 * @exports TurnPhaseLine     — the line component
 * @exports turnPhaseLabel    — English label per phase (UI chrome stays English)
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TurnPhase } from '../../../types';
import { Spinner } from '../../../components/common';
import { tf } from '../tf';

const LABELS: Record<TurnPhase, [key: string, fallback: string]> = {
  preparing: ['chat.turnPhase.preparing', 'Preparing context'],
  starting: ['chat.turnPhase.starting', 'Starting session'],
  waiting: ['chat.turnPhase.waiting', 'Waiting for model'],
};

export function turnPhaseLabel(phase: TurnPhase): [key: string, fallback: string] {
  return LABELS[phase];
}

/** Seconds are shown from 1s on, so a fast turn never flashes "· 0s". */
export function TurnPhaseLine({ phase, since }: { phase: TurnPhase; since: number }) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [phase, since]);
  const secs = Math.max(0, Math.floor((now - since) / 1000));
  const [key, fallback] = LABELS[phase];
  return (
    <div className="flex items-center gap-2 text-[var(--color-text-muted)]" data-testid="turn-phase" data-phase={phase}>
      <Spinner size="sm" />
      <span className="text-sm">
        {tf(t, key, fallback)}
        {secs >= 1 && <span className="text-[var(--color-text-dim)]"> · {secs}s</span>}
      </span>
    </div>
  );
}
