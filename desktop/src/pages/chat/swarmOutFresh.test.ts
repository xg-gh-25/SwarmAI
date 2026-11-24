/**
 * New session in the Swarm Out tab hands the old session's runs to the new one: the old
 * id is kept at the press, and once the tab's new session has its id the daemon is asked
 * to move them (tried again when it refuses, e.g. the session row lands a moment late).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../stores/tabNoticeStore', () => ({ tabNoticeStore: { post: vi.fn() } }));

import { HANDOVER_TRIES_MS, handOverWithRetry, planSwarmOutHandover, startSwarmOutFresh } from './swarmOutFresh';
import { SWARM_OUT_TAB_ID } from './swarmOut';

describe('Swarm Out New session hand-over', () => {
  it('keeps the old session id at the press', () => {
    const tab: { sessionId?: string } = { sessionId: 'old-1' };
    const handOverLater = vi.fn();
    const changed = startSwarmOutFresh({
      getTab: () => tab as never, liveBlock: null,
      reset: () => { tab.sessionId = undefined; }, clearContext: () => {},
      openOld: () => {}, handOverLater, t: (k) => k,
    });
    expect(changed).toBe(true);
    expect(handOverLater).toHaveBeenCalledWith('old-1');
  });

  it('hands over once the Swarm Out tab binds its new session, never for another tab', () => {
    expect(planSwarmOutHandover('old-1', SWARM_OUT_TAB_ID, 'new-2')).toEqual({ from: 'old-1', to: 'new-2' });
    expect(planSwarmOutHandover('old-1', 'tab-7', 'new-2')).toBeNull();
    expect(planSwarmOutHandover(null, SWARM_OUT_TAB_ID, 'new-2')).toBeNull();
    expect(planSwarmOutHandover('old-1', SWARM_OUT_TAB_ID, 'old-1')).toBeNull();
  });

  it('tries the hand-over again when the daemon refuses, then gives up', async () => {
    const waits: number[] = [];
    const wait = async (ms: number) => { waits.push(ms); };
    const flaky = vi.fn()
      .mockRejectedValueOnce(new Error('not in the session table yet'))
      .mockResolvedValueOnce({ moved: ['so_aaaaaaaaaaaa'] });
    expect(await handOverWithRetry(flaky, 'old-1', 'new-2', wait)).toEqual(['so_aaaaaaaaaaaa']);
    expect(flaky).toHaveBeenCalledTimes(2);
    const never = vi.fn().mockRejectedValue(new Error('down'));
    expect(await handOverWithRetry(never, 'old-1', 'new-2', wait)).toBeNull();
    expect(never).toHaveBeenCalledTimes(HANDOVER_TRIES_MS.length);
    expect(waits.every((ms) => ms > 0)).toBe(true);
  });
});

describe('ChatPage wiring', () => {
  it('keeps the old session at New session and hands over on the tab\'s session bind', () => {
    const src = readFileSync(resolve(__dirname, '../ChatPage.tsx'), 'utf-8');
    expect(src).toMatch(/handOverLater: \(sid\) => \{ swarmOutHandoverRef\.current = sid; \}/);
    // inside the helper both bind paths (active tab, any tab's session_start) call
    const body = src.slice(src.indexOf('const backfillDispatchForTab = useCallback'));
    expect(body.slice(0, 800)).toMatch(/planSwarmOutHandover\(swarmOutHandoverRef\.current, tabId, boundSessionId\)/);
  });
});
