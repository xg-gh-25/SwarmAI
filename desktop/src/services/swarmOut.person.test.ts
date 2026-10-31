/**
 * Every command the App sends on a run says it is the person's (design
 * 2026-10-31-swarm-out-agent-owns-its-runs §2.1): the backend treats a command that
 * does not say so as an agent's, which may not, for instance, discard a run whose
 * commits are not landed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({
  default: { get: vi.fn(), post: vi.fn(async () => ({ data: {} })) },
}));

import api from './api';
import { swarmOutService } from './swarmOut';

describe('swarmOutService run commands', () => {
  beforeEach(() => vi.mocked(api.post).mockClear());

  it('sends by: person with each of them', async () => {
    const id = 'so_aaaaaaaaaaaa';
    await swarmOutService.returnPrepare(id, 'https://x/y.git');
    await swarmOutService.returnLand(id, 'k', 't', true);
    await swarmOutService.accept(id, '2026-10-31T00:00:00Z');
    await swarmOutService.manualLanded(id);
    await swarmOutService.rerun(id, 'fix it');
    await swarmOutService.stop(id);
    await swarmOutService.discard(id, 'no longer needed');
    const calls = vi.mocked(api.post).mock.calls;
    expect(calls).toHaveLength(7);
    for (const [path, body] of calls) {
      expect(body, String(path)).toMatchObject({ by: 'person' });
    }
  });
});
