/**
 * Swarm Out's autonomy line (live.autonomyLine; design
 * docs/design/2026-11-01-brain-eval-and-runs.md §3, §5).
 *
 * Question: over the last 30 days, does it count the finished runs, those nobody stepped
 * into (an answer or a message counts; an approval does not), and the average, leaving
 * out a running run, an older one, and one whose record cannot say what the person said?
 */
import { describe, it, expect } from 'vitest';
import type { SwarmOutRun } from '../../services/swarmOut';
import { autonomyLine, interventionsOf } from './live';

const NOW = Date.parse('2026-11-01T12:00:00Z');
const r = (id: string, human: Record<string, unknown> | null, o: Record<string, unknown> = {}): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h', hive: 'demo', session_id: 's', title: id, origin: 'swarm-out',
  sent_at: '2026-10-31T09:00:00Z', state: 'stopped',
  result: human === null ? null : { provisional: false, work: { human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, ...human } } },
  ...o,
}) as unknown as SwarmOutRun;

describe('autonomyLine', () => {
  it('counts finished runs nobody stepped into, and the average, leaving out the unknown', () => {
    const got = autonomyLine([
      r('quiet', { approved: 3, said: 0 }),                     // approvals are oversight
      r('asked', { answered_questions: 1, said: 0 }),
      r('steered', { said: 2 }),
      r('old-record', { answered_questions: 0 }),               // said not counted then: unknown
      r('going', { said: 0 }, { state: 'running' }),
      r('no-result', null),
      r('last-month', { said: 0 }, { sent_at: '2026-09-01T09:00:00Z' }),
    ], NOW);
    expect(got).toEqual({ finished: 4, known: 3, unattended: 1, avg: 1 });
    expect(autonomyLine([], NOW)).toEqual({ finished: 0, known: 0, unattended: 0, avg: null });
    // a record that cannot tell what the person said is not known, never a smaller number
    expect(interventionsOf(r('x', { answered_questions: 2 }).result!)).toBeNull();
    expect(interventionsOf(r('x', { answered_questions: 2, said: 1 }).result!)).toBe(3);
  });
});
