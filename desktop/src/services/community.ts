/**
 * community — client for the Community overlay's read-only endpoints (Phase-1).
 *
 * The Community overlay is SwarmAI's two-way membrane with the outside world:
 * inbound signals (Feed + Sources) and outbound engagement (Engagement & Reports).
 * Phase-1 is READ-ONLY — three GET endpoints; Phase-2 adds /sources CRUD.
 *
 *   GET /api/community/feed        → recent signal digests + reports (files)
 *   GET /api/community/sources     → configured feeds (read-only)
 *   GET /api/community/engagement  → GitHub community metrics (data-backed only)
 *
 * Backend is snake_case (FastAPI); this layer normalizes to the shapes the
 * overlay renders. All numbers come from the backend (never frontend .length).
 */
import api from './api';

/** One community feed item — a recent signal digest or report file. */
export interface CommunityFeedItem {
  path: string;
  category: 'Signals' | 'Reports' | string;
  name: string;
  mtime: number;
}

/** One configured signal source (feed) — a view of config.yaml. */
export interface CommunitySource {
  id: string;
  name: string;
  type: string;
  tier: string;
  enabled: boolean;
  /** "manual" (default when absent) | "self-tune" | "user". */
  managedBy: string;
  /** Legacy urls-only count — RETAINED for back-compat. Prefer memberCount. */
  sourceCount: number;
  /** The feed's editable string members (urls/keywords/queries/repos/…), capped. */
  members: string[];
  /** ACCURATE total member count (the true total even if `members` is capped). */
  memberCount: number;
  /** true when `members` is a capped view of a longer list. */
  membersTruncated: boolean;
  /** The config key the members live under (e.g. "urls"), or null = no editable members. */
  memberKind: string | null;
  tags: string[];
}

/** Outbound engagement metrics — data-backed only (no fabricated quality score). */
export interface CommunityEngagement {
  commentsPosted: number;
  repliesReceived: number;
  maintainerReplies: number;
  stars: number | null;
}

interface RawSource {
  id: string;
  name: string;
  type: string;
  tier: string;
  enabled: boolean;
  managed_by: string;
  source_count: number;
  members?: string[];
  member_count?: number;
  members_truncated?: boolean;
  member_kind?: string | null;
  tags?: string[];
}

export const communityService = {
  async fetchFeed(): Promise<CommunityFeedItem[]> {
    const res = await api.get('/community/feed');
    return (res.data?.items ?? []) as CommunityFeedItem[];
  },

  async fetchSources(): Promise<CommunitySource[]> {
    const res = await api.get('/community/sources');
    const raw = (res.data?.sources ?? []) as RawSource[];
    return raw.map((s) => ({
      id: s.id,
      name: s.name,
      type: s.type,
      tier: s.tier,
      enabled: s.enabled,
      managedBy: s.managed_by,
      sourceCount: s.source_count,
      members: s.members ?? [],
      memberCount: s.member_count ?? 0,
      membersTruncated: s.members_truncated ?? false,
      memberKind: s.member_kind ?? null,
      tags: s.tags ?? [],
    }));
  },

  async fetchEngagement(): Promise<CommunityEngagement> {
    const res = await api.get('/community/engagement');
    const d = res.data ?? {};
    return {
      commentsPosted: d.comments_posted ?? 0,
      repliesReceived: d.replies_received ?? 0,
      maintainerReplies: d.maintainer_replies ?? 0,
      stars: d.stars ?? null,
    };
  },

  // ── Phase-2 writes (Sources editable) — all serialize with self_tune on one lock ──

  async addSource(feed: { id: string; name: string; type: string; tier?: string }): Promise<void> {
    await api.post('/community/feeds', feed);
  },

  async updateSource(id: string, patch: { enabled?: boolean; tier?: string }): Promise<void> {
    await api.put(`/community/feeds/${encodeURIComponent(id)}`, patch);
  },

  async deleteSource(id: string): Promise<void> {
    await api.delete(`/community/feeds/${encodeURIComponent(id)}`);
  },

  // ── Member-level writes (edit a feed's internal urls/keywords/queries/…) ──
  // Member values contain slashes (URLs) → sent in the request BODY, never a path
  // param. Bare /community/... path (the axios interceptor prepends /api).

  async addMember(id: string, value: string): Promise<void> {
    await api.post(`/community/feeds/${encodeURIComponent(id)}/members`, { value });
  },

  async deleteMember(id: string, value: string): Promise<void> {
    await api.delete(`/community/feeds/${encodeURIComponent(id)}/members`, { data: { value } });
  },
};
