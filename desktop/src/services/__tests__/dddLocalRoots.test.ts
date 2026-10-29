/**
 * Local repositories and the starter kind (design 2026-10-29-code-intel-auto D1/D2).
 * Remove is a JSON write from the app (`X-Swarm-User-Action`): the route refuses a
 * cross-site post without it. The starter kind is a read with the path as a param.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../api';
import { getStarterKind, removeLocalRoot } from '../ddd';

const mockApi = api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };

describe('local roots', () => {
  beforeEach(() => { mockApi.get.mockReset(); mockApi.post.mockReset(); });

  it('Remove sends JSON with the user-action header', async () => {
    mockApi.post.mockResolvedValue({ data: { localRoots: [] } });
    await removeLocalRoot('My Brain', '/Users/me/code/app');
    const [url, body, config] = mockApi.post.mock.calls[0];
    expect(url).toBe('/ddd/brains/My%20Brain/local-roots/remove');
    expect(body).toEqual({ path: '/Users/me/code/app' });
    expect(config.headers['Content-Type']).toBe('application/json');
    expect(config.headers['X-Swarm-User-Action']).toBeTruthy();
  });

  it('the starter kind passes the path as a query param', async () => {
    mockApi.get.mockResolvedValue({ data: { path: '/p', role: 'SHELF', why: '', roots: [] } });
    expect((await getStarterKind('/p q')).role).toBe('SHELF');
    expect(mockApi.get).toHaveBeenCalledWith('/ddd/starter-kind', { params: { path: '/p q' } });
  });
});
