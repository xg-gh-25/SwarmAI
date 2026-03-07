/**
 * Evolution session badge for the Swarm Radar panel.
 *
 * Displays a compact count of successful evolutions in the current session,
 * broken down by trigger type (reactive, proactive, stuck). Designed to sit
 * in the Swarm Radar header or as a standalone indicator.
 *
 * Key exports:
 * - ``EvolutionBadge``       — Badge component showing evolution counts
 * - ``EvolutionSessionCount``— Data model for session evolution counts
 * - ``useEvolutionCount``    — Hook to derive counts from chat messages
 */

import { useMemo } from 'react';

/** Counts of successful evolutions by trigger type for the current session. */
export interface EvolutionSessionCount {
  reactive: number;
  proactive: number;
  stuck: number;
  total: number;
}

/** Empty counts constant. */
const EMPTY_COUNTS: EvolutionSessionCount = {
  reactive: 0,
  proactive: 0,
  stuck: 0,
  total: 0,
};

/**
 * Derive evolution session counts from an array of evolution result messages.
 *
 * Each message should have ``eventType`` and ``data`` fields matching the
 * SSE evolution_result event shape.
 */
export function deriveEvolutionCounts(
  resultEvents: Array<{ data: Record<string, unknown> }>,
): EvolutionSessionCount {
  const counts = { ...EMPTY_COUNTS };
  for (const evt of resultEvents) {
    if (evt.data.outcome !== 'success') continue;
    const trigger = (evt.data.triggerType as string) || 'reactive';
    if (trigger === 'reactive') counts.reactive++;
    else if (trigger === 'proactive') counts.proactive++;
    else if (trigger === 'stuck') counts.stuck++;
    counts.total++;
  }
  return counts;
}

interface EvolutionBadgeProps {
  counts: EvolutionSessionCount;
}

/** Compact badge showing evolution count with trigger-type breakdown on hover. */
export function EvolutionBadge({ counts }: EvolutionBadgeProps) {
  if (counts.total === 0) return null;

  const breakdown = useMemo(() => {
    const parts: string[] = [];
    if (counts.reactive > 0) parts.push(`⚡${counts.reactive}`);
    if (counts.proactive > 0) parts.push(`🔍${counts.proactive}`);
    if (counts.stuck > 0) parts.push(`🔄${counts.stuck}`);
    return parts.join(' ');
  }, [counts.reactive, counts.proactive, counts.stuck]);

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 8px',
        borderRadius: '10px',
        backgroundColor: 'rgba(34, 197, 94, 0.15)',
        color: '#22c55e',
        fontSize: '11px',
        fontWeight: 500,
        whiteSpace: 'nowrap',
      }}
      title={`Evolutions this session: ${breakdown}`}
      aria-label={`${counts.total} successful evolution${counts.total !== 1 ? 's' : ''} this session`}
    >
      <span style={{ fontSize: '12px' }}>🧬</span>
      <span>{counts.total}</span>
    </span>
  );
}
