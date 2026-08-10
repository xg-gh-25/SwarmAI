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

  it('fetchFeed preserves the backend truncated/count flags (no silent cut)', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { count: 100, truncated: true, items: [{ path: 'Knowledge/Signals/a.md', category: 'Signals', name: 'a.md', mtime: 1 }] },
    });
    const feed = await communityService.fetchFeed();
    expect(feed.truncated).toBe(true);
    expect(feed.count).toBe(100);
    expect(feed.items).toHaveLength(1);
  });

  it('fetchFeed defaults truncated=false and count to items.length when absent', async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: [{ path: 'p', category: 'Signals', name: 'n', mtime: 1 }] } });
    const feed = await communityService.fetchFeed();
    expect(feed.truncated).toBe(false);
    expect(feed.count).toBe(1);
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

  it('fetchEngagement maps the {kpis, items} contract + nested replies → camelCase', async () => {
    mockApi.get.mockResolvedValue({
      data: {
        kpis: { comments_posted: 216, replies_received: 387, maintainer_replies: 21, stars: null },
        items: [
          {
            repo: 'a/b', issue_number: 1, topic: 'T-MEM', status: 'published',
            comment_url: 'https://github.com/a/b/issues/1#c1', posted_at: '2026-08-01T10:00:00Z',
            confidence: 9, reply_count: 1, has_maintainer_reply: true, needs_followup: true,
            replies: [{ author: 'm1', body: 'merged', is_maintainer: true, created_at: '2026-08-02T10:00:00Z' }],
          },
        ],
      },
    });
    const out = await communityService.fetchEngagement();
    expect(out.kpis).toEqual({ commentsPosted: 216, repliesReceived: 387, maintainerReplies: 21, stars: null });
    expect(out.items).toHaveLength(1);
    expect(out.items[0]).toEqual({
      repo: 'a/b', issueNumber: 1, topic: 'T-MEM', status: 'published',
      commentUrl: 'https://github.com/a/b/issues/1#c1', postedAt: '2026-08-01T10:00:00Z',
      confidence: 9, replyCount: 1, hasMaintainerReply: true, needsFollowup: true,
      replies: [{ author: 'm1', body: 'merged', isMaintainer: true, createdAt: '2026-08-02T10:00:00Z' }],
    });
  });

  it('fetchEngagement falls back to a flat pre-list backend (no kpis wrapper → still maps counts)', async () => {
    // A rolling deploy where the backend still returns the old flat shape must NOT
    // silently zero the strip — the d.kpis ?? d fallback reads the top-level counts.
    mockApi.get.mockResolvedValue({ data: { comments_posted: 2 } });
    const out = await communityService.fetchEngagement();
    expect(out.kpis).toEqual({ commentsPosted: 2, repliesReceived: 0, maintainerReplies: 0, stars: null });
    expect(out.items).toEqual([]);
  });
});
