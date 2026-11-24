/** The tab's "waiting" count is what the person can still answer here. */
import { describe, it, expect } from 'vitest';
import { activityOf } from './useSwarmOutActivity';
import type { SwarmOutRuns } from '../../services/swarmOut';

const runs = (asks: Partial<SwarmOutRuns['asks'][number]>[]): SwarmOutRuns =>
  ({ runs: [], asks: asks as SwarmOutRuns['asks'], errors: [] });

describe('activityOf', () => {
  it('does not count an ask whose answer the Hive refused', () => {
    expect(activityOf(runs([{ request_id: 'r1', refused: true }, { request_id: 'r2' }])).waiting).toBe(1);
  });
  it('counts every open ask', () => {
    expect(activityOf(runs([{ request_id: 'r1' }, { request_id: 'r2' }])).waiting).toBe(2);
  });
});

describe('an ask follows its run (design r2 T6)', () => {
  const run = (needs: boolean | null, over: Record<string, unknown> = {}) => ({
    dispatch_id: 'd1', hive_id: 'h1', session_id: 's1', stage: 'needs_answer', state: 'waiting',
    needs_person: needs, actions: [], ...over,
  }) as unknown as SwarmOutRuns['runs'][number];
  const withRun = (r: SwarmOutRuns['runs'][number], asks: Partial<SwarmOutRuns['asks'][number]>[]): SwarmOutRuns =>
    ({ runs: [r], asks: asks as SwarmOutRuns['asks'], errors: [] });

  it('does not count an ask of a run its dispatching agent answers for', () => {
    expect(activityOf(withRun(run(false), [{ request_id: 'q', hive_id: 'h1', session_id: 's1' }])).waiting).toBe(0);
  });
  it('counts an ask of a run that waits on the person, of a run no session answers for, of no listed run', () => {
    expect(activityOf(withRun(run(true), [{ request_id: 'q', hive_id: 'h1', session_id: 's1' }])).waiting).toBe(1);
    expect(activityOf(withRun(run(null), [{ request_id: 'q', hive_id: 'h1', session_id: 's1' }])).waiting).toBe(1);
    expect(activityOf(withRun(run(false), [{ request_id: 'q', hive_id: 'h1', session_id: 'other' }])).waiting).toBe(1);
  });
  it("does not count the agent's run that waits with no question mirrored", () => {
    expect(activityOf(withRun(run(false), [])).waiting).toBe(0);
    expect(activityOf(withRun(run(null), [])).waiting).toBe(1);
  });
});
