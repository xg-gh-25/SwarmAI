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
