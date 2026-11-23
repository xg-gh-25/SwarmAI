/**
 * How often a person stepped into a run (live.interventionsOf; design
 * docs/design/2026-11-01-brain-eval-and-runs.md §3).
 *
 * Question: is a record that cannot say what the person said "not known", never zero?
 */
import { describe, it, expect } from 'vitest';
import type { SwarmOutRun } from '../../services/swarmOut';
import { interventionsOf } from './live';

const r = (id: string, human: Record<string, unknown> | null, o: Record<string, unknown> = {}): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h', hive: 'demo', session_id: 's', title: id, origin: 'swarm-out',
  sent_at: '2026-10-31T09:00:00Z', state: 'stopped',
  result: human === null ? null : { provisional: false, work: { human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, ...human } } },
  ...o,
}) as unknown as SwarmOutRun;

describe('interventionsOf', () => {
  it('a record that cannot tell what the person said is not known, never a smaller number', () => {
    expect(interventionsOf(r('x', { answered_questions: 2 }).result!)).toBeNull();
    expect(interventionsOf(r('x', { answered_questions: 2, said: 1 }).result!)).toBe(3);
  });
});
