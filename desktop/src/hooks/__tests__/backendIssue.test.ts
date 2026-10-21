/**
 * backendIssue — the reason the UI gives when the backend does not answer.
 *
 * What is tested: each watchdog payload maps to its short label and sentence —
 * slowed by the CPU (with load and the busiest processes), low memory, low battery,
 * busy in our code (with how long and where); not responding as stopped (process
 * gone), paused (process there, heartbeat stale), stuck (heartbeat fresh, own code),
 * and a starved wedge says "slowed", never "stuck"; an old port-only payload parses
 * to an empty diagnosis and still gets a plain message.
 */
import { describe, it, expect } from 'vitest';
import { describeBackendIssue, parseBackendDiag } from '../backendIssue';

const d = (p: unknown) => parseBackendDiag(p);

describe('describeBackendIssue', () => {
  it('slowed: CPU, with the evidence', () => {
    const i = describeBackendIssue('degraded', d({ heartbeatFresh: true, loopAge: 9, processAlive: true,
      stall: { cause: 'cpu', starved: true, loadPerCore: 12.93, topCpu: [{ name: 'python3.12', cpu: 340 }, { name: 'node', cpu: 120 }] } }));
    expect(i.short).toBe('Slowed: CPU fully used');
    expect(i.detail).toContain('load 12.9 per core');
    expect(i.detail).toContain('python3.12 340%, node 120%');
    expect(i.detail).toContain('still running');
  });

  it('slowed: memory / battery; busy names where and how long', () => {
    expect(describeBackendIssue('degraded', d({ stall: { cause: 'memory', memPercent: 95.2, memPressure: 'critical' } })).detail)
      .toContain('95% used, macOS memory pressure critical');
    expect(describeBackendIssue('degraded', d({ stall: { cause: 'low_battery', battery: 3 } })).short).toBe('Slowed: low battery');
    const busy = describeBackendIssue('degraded', d({ loopAge: 12.4, stall: { cause: 'busy', busyWith: 'routers/artifacts.py:get_artifact_drift' } }));
    expect(busy.short).toBe('Backend busy');
    expect(busy.detail).toContain('for 12s in routers/artifacts.py → get_artifact_drift');
  });

  it('not responding: stopped, paused, stuck — and a starved wedge is "slowed"', () => {
    expect(describeBackendIssue('not_responding', d({ processAlive: false, heartbeatFresh: false })).short).toBe('Backend stopped');
    expect(describeBackendIssue('not_responding', d({ processAlive: true, heartbeatFresh: false })).short).toBe('Backend paused');
    const stuck = describeBackendIssue('not_responding', d({ processAlive: true, heartbeatFresh: true, loopAge: 40,
      stall: { cause: 'busy', starved: false, busyWith: 'core/x.py:f' } }));
    expect(stuck.short).toBe('Backend stuck');
    expect(stuck.detail).toContain('for 40s: stuck in core/x.py → f');
    expect(describeBackendIssue('not_responding', d({ processAlive: true, heartbeatFresh: true, loopAge: 40,
      stall: { cause: 'cpu', starved: true } })).short).toBe('Slowed: CPU fully used');
  });

  it('an old port-only payload still gets a plain message', () => {
    expect(d(18321)).toEqual({});
    expect(describeBackendIssue('degraded', d(18321)).short).toBe('Reconnecting…');
    expect(describeBackendIssue('not_responding', d(null)).short).toBe('Backend not responding');
    expect(describeBackendIssue('unreachable').short).toBe('Backend unreachable');
  });

  it('paused states only what was seen, not a guessed cause (an overloaded host pauses it too)', () => {
    const watched = describeBackendIssue('degraded', d({ heartbeatFresh: false, processAlive: true, suspended: true, loopAge: 12 }));
    const plain = describeBackendIssue('not_responding', d({ processAlive: true, heartbeatFresh: false }));
    for (const p of [watched, plain]) {
      expect(p.detail).toContain('has not run');
      expect(p.detail).toContain('process is still there');
      expect(p.detail).not.toMatch(/slept|battery|suspended it/i);
    }
    expect(watched.detail).toContain('12s');
  });

  it('a suspended process reads as paused, also on a degraded event', () => {
    const p = describeBackendIssue('degraded', d({ heartbeatFresh: false, processAlive: true, suspended: true }));
    expect(p.short).toBe('Backend paused');
    expect(p.cause).toBe('paused');
    expect(d({ suspended: 'yes' }).suspended).toBe(false);
  });

  it('past the watchdog cap a "suspension" while awake reads as stuck, not paused', () => {
    const i = describeBackendIssue('not_responding',
      d({ heartbeatFresh: false, processAlive: true, suspended: false, suspendedSecs: 600 }));
    expect(i.short).toBe('Backend stuck');
    expect(i.detail).toContain('has not run for 10 min while this Mac was awake');
    expect(describeBackendIssue('not_responding',
      d({ heartbeatFresh: false, processAlive: true })).short).toBe('Backend paused');
  });
});
