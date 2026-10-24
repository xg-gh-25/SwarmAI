/**
 * community — client for the Community overlay's read-only endpoints (Phase-1).
 *
 * The Community overlay shows inbound signals (Feed) and the configured feeds
 * (Sources, editable through the /feeds endpoints).
 *
 *   GET /api/community/feed        → recent signal digests + reports (files)
 *   GET /api/community/sources     → configured feeds (read-only)
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

/** The /community/feed response: capped item list + honest truncation flag.
 *  `truncated` is the backend's own signal that the list was cut at the cap
 *  (more exist on disk) — the UI must surface it, not silently show a partial
 *  list as if it were the whole feed. */
export interface CommunityFeed {
  items: CommunityFeedItem[];
  count: number;
  truncated: boolean;
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
  /** The feed's editable string members (urls/keywords/repos/…), capped. */
  members: string[];
  /** ACCURATE total member count (the true total even if `members` is capped). */
  memberCount: number;
  /** true when `members` is a capped view of a longer list. */
  membersTruncated: boolean;
  /** The config key the members live under (e.g. "urls"), or null = no editable members. */
  memberKind: string | null;
  tags: string[];
}

interface RawSource {
  id: string;
  name: string;
  type: string;
  tier: string;
  enabled: boolean;
  managed_by: string;
  members?: string[];
  member_count?: number;
  members_truncated?: boolean;
  member_kind?: string | null;
  tags?: string[];
}

export const communityService = {
  async fetchFeed(): Promise<CommunityFeed> {
    const res = await api.get('/community/feed');
    // Preserve `truncated`/`count` — the backend marks a capped feed honestly, and
    // dropping them here (returning a bare item[]) is what made the cut SILENT: the
    // UI showed 100 of N with no "more on disk" disclosure. Mirrors fetchSources,
    // which already keeps memberCount/membersTruncated.
    const items = (res.data?.items ?? []) as CommunityFeedItem[];
    return {
      items,
      count: typeof res.data?.count === 'number' ? res.data.count : items.length,
      truncated: res.data?.truncated === true,
    };
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
      members: s.members ?? [],
      memberCount: s.member_count ?? 0,
      membersTruncated: s.members_truncated ?? false,
      memberKind: s.member_kind ?? null,
      tags: s.tags ?? [],
    }));
  },

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
