/**
 * community service — path + normalization contract.
 *
 * Why this file exists (regression guard, run bugfix 2026-08-09): every tab of the
 * Community overlay showed "Couldn't load" in production while the backend returned
 * 200 on `curl /api/community/*`. Root cause: the axios `api` interceptor PREPENDS
 * `/api` to every request (api.ts baseURL = `${host}/api`), so a service method must
 * pass a BARE path (`/community/feed`), NOT `/api/community/feed`. community.ts was
 * the only service that hard-coded the `/api` prefix → real requests hit the doubled
 * `/api/api/community/...` → 404 → the overlay's error branch on all 3 tabs.
 *
 * The CommunityOverlay.test.tsx mocks the whole service, so it could never catch a
 * wrong URL. These tests mock `../api` and assert the EXACT path the service sends —
 * pinning the single-`/api` contract so the double-prefix can't come back silently.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { communityService } from '../community';

const mockApi = api as unknown as {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  put: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
};

describe('communityService — request paths (single /api, added by the interceptor)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.get.mockResolvedValue({ data: {} });
    mockApi.post.mockResolvedValue({ data: {} });
    mockApi.put.mockResolvedValue({ data: {} });
    mockApi.delete.mockResolvedValue({ data: {} });
  });

  it('fetchFeed calls the bare /community/feed path (NOT /api/community/feed)', async () => {
    await communityService.fetchFeed();
    expect(mockApi.get).toHaveBeenCalledWith('/community/feed');
  });

  it('fetchSources calls /community/sources', async () => {
    await communityService.fetchSources();
    expect(mockApi.get).toHaveBeenCalledWith('/community/sources');
  });

  it('fetchEngagement calls /community/engagement', async () => {
    await communityService.fetchEngagement();
    expect(mockApi.get).toHaveBeenCalledWith('/community/engagement');
  });

  it('addSource POSTs to /community/feeds', async () => {
    await communityService.addSource({ id: 'x', name: 'X', type: 'rss' });
    expect(mockApi.post).toHaveBeenCalledWith('/community/feeds', { id: 'x', name: 'X', type: 'rss' });
  });

  it('updateSource PUTs to /community/feeds/{id} (id encoded)', async () => {
    await communityService.updateSource('my:feed', { enabled: false });
    expect(mockApi.put).toHaveBeenCalledWith('/community/feeds/my%3Afeed', { enabled: false });
  });

  it('deleteSource DELETEs /community/feeds/{id} (id encoded)', async () => {
    await communityService.deleteSource('my:feed');
    expect(mockApi.delete).toHaveBeenCalledWith('/community/feeds/my%3Afeed');
  });

  it('addMember POSTs value in the BODY to /community/feeds/{id}/members (id encoded)', async () => {
    await communityService.addMember('my:feed', 'https://a.com/f');
    expect(mockApi.post).toHaveBeenCalledWith('/community/feeds/my%3Afeed/members', { value: 'https://a.com/f' });
  });

  it('deleteMember DELETEs with value in config.data (body, not path — slash-safe)', async () => {
    await communityService.deleteMember('my:feed', 'https://a.com/a/b/c');
    expect(mockApi.delete).toHaveBeenCalledWith('/community/feeds/my%3Afeed/members', { data: { value: 'https://a.com/a/b/c' } });
  });

  it('no community path is ever double-prefixed with /api', async () => {
    await communityService.fetchFeed();
    await communityService.fetchSources();
    await communityService.fetchEngagement();
    await communityService.addSource({ id: 'x', name: 'X', type: 'rss' });
    await communityService.updateSource('x', { tier: 'leaders' });
    await communityService.deleteSource('x');
    await communityService.addMember('x', 'v');
    await communityService.deleteMember('x', 'v');
    const allPaths = [
      ...mockApi.get.mock.calls,
      ...mockApi.post.mock.calls,
      ...mockApi.put.mock.calls,
      ...mockApi.delete.mock.calls,
    ].map((c) => c[0] as string);
    expect(allPaths.length).toBe(8);
    for (const p of allPaths) {
      expect(p.startsWith('/community/')).toBe(true);
      expect(p.startsWith('/api/')).toBe(false); // the bug: '/api/community/...'
    }
  });
});

describe('communityService — snake→camel normalization', () => {
  beforeEach(() => vi.clearAllMocks());

  it('fetchSources maps managed_by + member fields → camelCase (no dead source_count)', async () => {
    mockApi.get.mockResolvedValue({
      data: {
        sources: [
          { id: 'a', name: 'A', type: 'rss', tier: 'engineering', enabled: true, managed_by: 'user',
            members: ['u1', 'u2'], member_count: 2, members_truncated: false,
            member_kind: 'urls', tags: ['x'] },
        ],
      },
    });
    const out = await communityService.fetchSources();
    expect(out[0]).toEqual({
      id: 'a', name: 'A', type: 'rss', tier: 'engineering', enabled: true,
      managedBy: 'user', members: ['u1', 'u2'], memberCount: 2,
      membersTruncated: false, memberKind: 'urls', tags: ['x'],
    });
    expect('sourceCount' in out[0]).toBe(false);  // dead field removed
  });

  it('fetchSources defaults member fields when backend omits them (back-compat)', async () => {
    mockApi.get.mockResolvedValue({
      data: { sources: [{ id: 'b', name: 'B', type: 'rss', tier: 'engineering', enabled: true, managed_by: 'manual' }] },
    });
    const out = await communityService.fetchSources();
    expect(out[0].members).toEqual([]);
    expect(out[0].memberCount).toBe(0);
    expect(out[0].membersTruncated).toBe(false);
    expect(out[0].memberKind).toBeNull();
  });

  it('fetchEngagement defaults missing fields (0 / null stars), no fabricated quality', async () => {
    mockApi.get.mockResolvedValue({ data: { comments_posted: 2 } });
    const out = await communityService.fetchEngagement();
    expect(out).toEqual({ commentsPosted: 2, repliesReceived: 0, maintainerReplies: 0, stars: null });
  });
});
