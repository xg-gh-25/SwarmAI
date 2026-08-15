/**
 * brainRecall — the Brain Hub search-box client (run_d0cd4414, AC7).
 *
 * Wires the frontend to the pre-existing GET /api/ddd/brains/{name}/recall
 * endpoint (ddd_brain.py:1825, "the Brain Hub's search box"). These pin:
 *  - the EXACT bare path + query param shape the axios `api` client sends
 *    (the api interceptor prepends /api, so the service must send a BARE path),
 *  - empty/blank q short-circuits to [] with NO network call (the endpoint
 *    already returns empty for blank q, but skipping the call is cheaper + the
 *    debounced input fires on every keystroke),
 *  - hits are returned as-is (backend already emits the hit shape).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../api';
import { brainRecall } from '../ddd';

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn> };

describe('brainRecall', () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it('sends a BARE /ddd/brains/{name}/recall path with q as a query param', async () => {
    mockApi.get.mockResolvedValue({
      data: { query: 'foo', count: 1, hits: [
        { domain: 'ddd', title: 'TECH.md § Recall', source: 'Projects/SwarmAI/2-understanding/TECH.md', content: 'recall is pure-filesystem...' },
      ] },
    });
    const hits = await brainRecall('SwarmAI', 'foo');
    // bare path (no leading /api — the api interceptor adds it), name url-encoded, q param
    expect(mockApi.get).toHaveBeenCalledWith(
      '/ddd/brains/SwarmAI/recall',
      { params: { q: 'foo' } },
    );
    expect(hits).toHaveLength(1);
    expect(hits[0].source).toBe('Projects/SwarmAI/2-understanding/TECH.md');
  });

  it('url-encodes a brain name with special chars', async () => {
    mockApi.get.mockResolvedValue({ data: { query: 'x', count: 0, hits: [] } });
    await brainRecall('a/b brain', 'x');
    expect(mockApi.get).toHaveBeenCalledWith(
      '/ddd/brains/a%2Fb%20brain/recall',
      { params: { q: 'x' } },
    );
  });

  it('empty query short-circuits to [] with NO network call', async () => {
    const hits = await brainRecall('SwarmAI', '');
    expect(hits).toEqual([]);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('whitespace-only query short-circuits to [] with NO network call', async () => {
    const hits = await brainRecall('SwarmAI', '   ');
    expect(hits).toEqual([]);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('returns [] when the backend omits hits (fail-soft contract)', async () => {
    mockApi.get.mockResolvedValue({ data: { query: 'foo', count: 0 } });
    const hits = await brainRecall('SwarmAI', 'foo');
    expect(hits).toEqual([]);
  });
});
