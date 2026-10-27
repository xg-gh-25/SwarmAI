/** swarmOutService.answer: an approval carries what the card showed (shown_sha256), so
 *  the Hive delivers it only to that command (fixing spec B); a question does not. */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const post = vi.fn(async () => ({ data: {} }));
vi.mock('../api', () => ({ default: { post: (...a: unknown[]) => post(...(a as [])), get: vi.fn() } }));
import { swarmOutService, type SwarmOutAsk } from '../swarmOut';

const base = { hive: 'w', hive_id: 'h1', session_id: 's1', request_id: 'r1', asked_at: '' };

beforeEach(() => post.mockClear());

describe('swarmOutService.answer', () => {
  it('sends shown_sha256 with an approval', async () => {
    const ask: SwarmOutAsk = { ...base, kind: 'approval', command: 'ls', shown_sha256: 'abc' };
    await swarmOutService.answer(ask, { decision: 'approve' });
    expect(post).toHaveBeenCalledWith('/swarm-out/asks/h1/s1/r1/answer', { decision: 'approve', shown_sha256: 'abc' });
  });

  it('sends a question answer as is', async () => {
    const ask: SwarmOutAsk = { ...base, kind: 'question', questions: [] };
    await swarmOutService.answer(ask, { answers: { q: 'a' } });
    expect(post).toHaveBeenCalledWith('/swarm-out/asks/h1/s1/r1/answer', { answers: { q: 'a' } });
  });
});
