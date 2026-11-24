/**
 * An SSH machine's runs in the history's cost section (../costStats; design
 * 2026-11-24-swarm-out-ssh-machine §8).
 *
 * Question: is a run on an SSH machine counted with its model cost and no machine cost (the
 * person's own machine), shown as its own side ("ssh"), never left out as unknown, and kept out
 * of the EC2-versus-AgentCore verdict?
 */
import { describe, it, expect } from 'vitest';
import type { SwarmOutMachines, SwarmOutRun } from '../../../services/swarmOut';
import { costStats, SHOWN_CARRIERS, verdict } from '../costStats';
import { carrierLabelKey, runWhere } from '../where';

const T = Date.parse('2026-11-24T12:00:00Z');

function sshRun(n: number): SwarmOutRun {
  return {
    dispatch_id: `so_${String(n).padStart(12, '0')}`, hive_id: 's1', hive: 'dev-dsk', session_id: `s${n}`, title: 't',
    origin: 'swarm-out', sent_at: new Date(T - 3600_000).toISOString(), state: 'stopped', state_reason: 'idle', stage: 'accepted',
    rounds: [{ carrier: 'ssh' }],
    project_agent: { brain: 'GCRAIDLCPreset', mode: 'code-change', revision: 'r', profile: 'p', acceptance: 'a', execution: 'native' },
    result: { active_s: 1800, cost_usd: 2, tokens: { input: 1, output: 1, cache_read: 1, cache_write: 1 } } as SwarmOutRun['result'],
  } as SwarmOutRun;
}

const MACHINES: SwarmOutMachines = { ec2: [], agentcore: null, estimate: true } as unknown as SwarmOutMachines;

describe('ssh carrier has no machine cost', () => {
  it('ssh carrier has no machine cost', () => {
    const runs = [sshRun(1), sshRun(2)];
    expect(runWhere(runs[0], null).carrier).toBe('ssh');
    expect(carrierLabelKey('ssh')).toBe('swarmOut.board.ssh');
    const all = costStats(runs, MACHINES, T - 86_400_000, T, 'all');
    expect(all.leftOut).toBe(0);
    expect(all.model).toBe(4);
    expect(all.busy + all.idle).toBe(0);
    const side = all.kinds[0].sides.ssh!;
    expect(side).toMatchObject({ n: 2, accepted: 2, model: 4, busy: 0, idle: 0 });
    expect(SHOWN_CARRIERS).toContain('ssh');
    expect(verdict(all)).toBeNull();                      // nothing to compare on EC2 and AgentCore
  });
});
