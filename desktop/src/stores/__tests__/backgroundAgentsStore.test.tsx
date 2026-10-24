/**
 * backgroundAgentsStore — the daemon's background agents, per session.
 *
 * What is tested: a live envelope replaces one session's list; a snapshot replaces
 * every session and marks the store loaded; malformed entries are dropped; and
 * `agentStatus`: only an Agent whose result said `background` gets a status — the
 * live entry when the daemon knows it, `lost` once a snapshot was read without it,
 * `running` before any snapshot; a stored end (`background` + final status) as is.
 * `useHasRunningAgent` re-renders its caller only when its answer flips, never on a
 * progress update (counted renders of a probe component on the shared store).
 */
import { describe, it, expect, afterEach } from 'vitest';
import { act, render } from '@testing-library/react';
import { agentStatus, backgroundAgentsStore, createBackgroundAgentsStore, useHasRunningAgent } from '../backgroundAgentsStore';

describe('backgroundAgentsStore', () => {
  it('apply replaces one session, applySnapshot replaces all and marks loaded', () => {
    const s = createBackgroundAgentsStore();
    let n = 0;
    s.subscribe(() => { n += 1; });
    s.apply('a', [{ toolUseId: 'x', status: 'running' }, { nope: 1 }, null]);
    expect(s.get('a').map((g) => g.toolUseId)).toEqual(['x']);
    expect(s.loaded()).toBe(false);
    s.applySnapshot({ b: [{ toolUseId: 'y', status: 'completed', durationMs: 5 }] });
    expect(s.get('a')).toEqual([]);
    expect(s.get('b')[0]).toMatchObject({ toolUseId: 'y', status: 'completed', durationMs: 5 });
    expect(s.loaded()).toBe(true);
    expect(n).toBe(2);
  });

  it('an unchanged snapshot or envelope notifies nobody (polled every 3s while streaming)', () => {
    const s = createBackgroundAgentsStore();
    let n = 0;
    s.subscribe(() => { n += 1; });
    const snap = { a: [{ toolUseId: 'x', status: 'running' }] };
    s.applySnapshot(snap);
    s.applySnapshot({ a: [{ toolUseId: 'x', status: 'running' }] });
    s.apply('a', [{ toolUseId: 'x', status: 'running' }]);
    expect(n).toBe(1);
    s.apply('a', [{ toolUseId: 'x', status: 'completed' }]);
    expect(n).toBe(2);
  });

  it('agentStatus: only background launches; live, else lost once loaded, else running', () => {
    const live = [{ toolUseId: 'x', description: '', status: 'completed' as const, durationMs: 7 }];
    expect(agentStatus(live, true, 'x', 'completed')).toBeNull(); // a foreground agent
    expect(agentStatus(live, true, 'x', 'background')).toMatchObject({ status: 'completed', durationMs: 7 });
    expect(agentStatus([], true, 'x', 'background')).toEqual({ status: 'lost' });
    expect(agentStatus([], false, 'x', 'background')).toEqual({ status: 'running' });
    expect(agentStatus([], true, 'x', 'failed', { background: true, durationMs: 3 })).toEqual({ status: 'failed', durationMs: 3 });
  });

  describe('useHasRunningAgent', () => {
    afterEach(() => { act(() => { backgroundAgentsStore.reset(); }); });
    it('re-renders only when the answer flips, not on a progress update', () => {
      const seen: boolean[] = [];
      function Probe() { seen.push(useHasRunningAgent('s', ['g1'])); return null; }
      render(<Probe />);
      expect(seen).toEqual([false]);
      act(() => { backgroundAgentsStore.apply('s', [{ toolUseId: 'g1', status: 'running', toolUses: 1 }]); });
      act(() => { backgroundAgentsStore.apply('s', [{ toolUseId: 'g1', status: 'running', toolUses: 2 }]); });
      act(() => { backgroundAgentsStore.apply('s', [{ toolUseId: 'g1', status: 'running', toolUses: 3 }, { toolUseId: 'other', status: 'running' }]); });
      expect(seen).toEqual([false, true]);
      act(() => { backgroundAgentsStore.apply('s', [{ toolUseId: 'g1', status: 'completed' }]); });
      expect(seen).toEqual([false, true, false]);
    });
  });
});
