/**
 * The attention client's reading of who an item waits on (backend handler_of): an item the
 * backend marks "agent" is Swarm's; anything else, a missing field included (an older
 * backend), is the person's; the person's and Swarm's counts follow the items when the
 * backend sends none.
 */
import { describe, it, expect, vi } from 'vitest';

const apiGet = vi.fn();
vi.mock('./api', () => ({ default: { get: (...a: unknown[]) => apiGet(...a) } }));

import { attentionService } from './attention';

const raw = (handler?: string, since?: string | null) => ({
  id: `x:${handler ?? 'none'}`, source: 'job', tier: 'blocking', brain: null, title: 't',
  ...(handler === undefined ? {} : { handler }), ...(since === undefined ? {} : { since }),
});

describe('attentionService.fetchAttention', () => {
  it('keeps the backend split and its counts', async () => {
    apiGet.mockResolvedValueOnce({ data: { items: [raw('agent', '2026-11-24T02:00:00+00:00'), raw('person', null)],
      counts: { blocking: 2, review: 0, person: 1, agent: 1 } } });
    const r = await attentionService.fetchAttention();
    expect(r.items.map((i) => i.handler)).toEqual(['agent', 'person']);
    expect(r.items[0].since).toBe('2026-11-24T02:00:00+00:00');
    expect(r.items[1].since).toBeNull();
    expect(r.counts).toEqual({ blocking: 2, review: 0, person: 1, agent: 1 });
  });

  it("an older backend's items, or an unknown handler, are the person's, and the counts follow", async () => {
    apiGet.mockResolvedValueOnce({ data: { items: [raw(), raw('robot'), raw('agent')], counts: { blocking: 3, review: 0 } } });
    const r = await attentionService.fetchAttention();
    expect(r.items.map((i) => i.handler)).toEqual(['person', 'person', 'agent']);
    expect(r.counts.person).toBe(2);
    expect(r.counts.agent).toBe(1);
  });
});
