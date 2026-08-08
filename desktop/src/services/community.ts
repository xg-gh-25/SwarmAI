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

/** One configured signal source (feed) — read-only view of config.yaml. */
export interface CommunitySource {
  id: string;
  name: string;
  type: string;
  tier: string;
  enabled: boolean;
  /** "manual" (default when absent) | "self-tune" | "user". */
  managedBy: string;
  sourceCount: number;
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
  tags?: string[];
}

export const communityService = {
  async fetchFeed(): Promise<CommunityFeedItem[]> {
    const res = await api.get('/api/community/feed');
    return (res.data?.items ?? []) as CommunityFeedItem[];
  },

  async fetchSources(): Promise<CommunitySource[]> {
    const res = await api.get('/api/community/sources');
    const raw = (res.data?.sources ?? []) as RawSource[];
    return raw.map((s) => ({
      id: s.id,
      name: s.name,
      type: s.type,
      tier: s.tier,
      enabled: s.enabled,
      managedBy: s.managed_by,
      sourceCount: s.source_count,
      tags: s.tags ?? [],
    }));
  },

  async fetchEngagement(): Promise<CommunityEngagement> {
    const res = await api.get('/api/community/engagement');
    const d = res.data ?? {};
    return {
      commentsPosted: d.comments_posted ?? 0,
      repliesReceived: d.replies_received ?? 0,
      maintainerReplies: d.maintainer_replies ?? 0,
      stars: d.stars ?? null,
    };
  },
};
