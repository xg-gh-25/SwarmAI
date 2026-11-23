/**
 * Where a run ran and its time facts (./where).
 *
 * Questions: does the run's own last round decide EC2 or AgentCore, then its Hive's
 * carrier in the fleet, then the Elastic Workers location's name, else EC2; is the
 * start written MM/DD HH:mm in local time; is the duration the active time once ended
 * and the time since it was sent while open.
 */
process.env.TZ = 'Asia/Shanghai';
import { describe, it, expect } from 'vitest';
import type { SwarmOutFleet, SwarmOutRun } from '../../services/swarmOut';
import { groupedId, minutesText, runMinutes, runWhere, stamp } from './where';

const run = (over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: 'so_1', hive_id: 'h', hive: 'demo', session_id: 's', title: 't', origin: 'swarm-out',
  sent_at: '2026-11-23T06:02:00Z', state: 'running', state_reason: 'streaming', ...over,
});
const fleet = (carrier: 'ec2' | 'agentcore'): SwarmOutFleet => ({ running: 1, hives: [{ name: 'demo', status: 'running', region: null,
  version: null, instance_type: null, reachable: true, streaming: 0, saturated: false, memory_percent: null, carrier }] });

describe('where', () => {
  it('the last round first, then the fleet, then the location name, else EC2', () => {
    expect(runWhere(run({ rounds: [{ carrier: 'ec2' }, { carrier: 'agentcore' }] }), fleet('ec2'))).toEqual({ carrier: 'agentcore', name: 'Elastic Workers' });
    expect(runWhere(run(), fleet('agentcore')).carrier).toBe('agentcore');
    expect(runWhere(run({ hive: 'elastic' }), null).carrier).toBe('agentcore');
    expect(runWhere(run(), null)).toEqual({ carrier: 'ec2', name: 'demo' });
  });

  it('the start as MM/DD HH:mm, local time; the duration in minutes', () => {
    expect(stamp('2026-11-23T06:02:00Z')).toBe('11/23 14:02');
    expect(stamp(null)).toBe('');
    const now = Date.parse('2026-11-23T06:20:30Z');
    expect(runMinutes(run(), now, true)).toBe(18);
    expect(runMinutes(run({ result: { active_s: 4620 } as never }), now, false)).toBe(77);
    expect(runMinutes(run({ result: null }), now, false)).toBeNull();
    expect([minutesText(0), minutesText(18), minutesText(null)]).toEqual(['<1 min', '18 min', '—']);
    expect(groupedId('533267412361')).toBe('5332 6741 2361');
  });
});
