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
 *   GET /api/community/hot-topics  → community DEMAND (TECH.md Rankings, read-only)
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

/** One GitHub Hot Topic row (community DEMAND) — parsed from TECH.md Rankings. */
export interface CommunityHotTopic {
  rank: number;
  topic: string;
  evidence: string;
  trend: string;
}

/** Hot Topics payload — the snapshot + its own freshness date. */
export interface CommunityHotTopics {
  /** The table's snapshot date (YYYY-MM-DD) — a manual scan, NOT live. null if absent. */
  updated: string | null;
  topics: CommunityHotTopic[];
}

/** Scalar KPI summary for the Outbound strip — data-backed only (no fabricated score). */
export interface CommunityEngagementKpis {
  commentsPosted: number;
  repliesReceived: number;
  maintainerReplies: number;
  stars: number | null;
}

/** One reply we received on a comment we posted (nested under an engagement item). */
export interface CommunityReply {
  author: string;
  body: string;
  isMaintainer: boolean;
  createdAt: string;
}

/** One outbound engagement — a comment we posted, with any replies it drew. */
export interface CommunityEngagementItem {
  repo: string;
  issueNumber: number | null;
  topic: string;
  status: string;
  commentUrl: string;
  postedAt: string;
  confidence: number | null;
  replyCount: number;
  hasMaintainerReply: boolean;
  needsFollowup: boolean;
  replies: CommunityReply[];
}

/** Outbound engagement — scalar KPIs + the clickable per-engagement list. */
export interface CommunityEngagement {
  kpis: CommunityEngagementKpis;
  items: CommunityEngagementItem[];
}

interface RawReply {
  author?: string;
  body?: string;
  is_maintainer?: boolean;
  created_at?: string;
}

interface RawEngagementItem {
  repo?: string;
  issue_number?: number | null;
  topic?: string;
  status?: string;
  comment_url?: string;
  posted_at?: string;
  confidence?: number | null;
  reply_count?: number;
  has_maintainer_reply?: boolean;
  needs_followup?: boolean;
  replies?: RawReply[];
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

  async fetchHotTopics(): Promise<CommunityHotTopics> {
    // Backend returns {updated, topics:[{rank,topic,evidence,trend}], count}.
    // Single-word lowercase keys → no snake→camel mapping needed. NOT an array
    // (unlike fetchFeed) — carries both the topics AND the freshness date.
    const res = await api.get('/community/hot-topics');
    return {
      updated: res.data?.updated ?? null,
      topics: (res.data?.topics ?? []) as CommunityHotTopic[],
    };
  },

  async fetchEngagement(): Promise<CommunityEngagement> {
    // New contract: {kpis:{...counts}, items:[...list]}. The old response was the
    // flat counts object; read from d.kpis now (a flat-shape fallback keeps an old
    // backend from silently zeroing the strip during a rolling deploy).
    const res = await api.get('/community/engagement');
    const d = res.data ?? {};
    const k = d.kpis ?? d; // fallback: pre-list backend returned counts at top level
    const rawItems = (d.items ?? []) as RawEngagementItem[];
    return {
      kpis: {
        commentsPosted: k.comments_posted ?? 0,
        repliesReceived: k.replies_received ?? 0,
        maintainerReplies: k.maintainer_replies ?? 0,
        stars: k.stars ?? null,
      },
      items: rawItems.map((it) => ({
        repo: it.repo ?? '',
        issueNumber: it.issue_number ?? null,
        topic: it.topic ?? '',
        status: it.status ?? '',
        commentUrl: it.comment_url ?? '',
        postedAt: it.posted_at ?? '',
        confidence: it.confidence ?? null,
        replyCount: it.reply_count ?? 0,
        hasMaintainerReply: it.has_maintainer_reply ?? false,
        needsFollowup: it.needs_followup ?? false,
        replies: (it.replies ?? []).map((r) => ({
          author: r.author ?? '',
          body: r.body ?? '',
          isMaintainer: r.is_maintainer ?? false,
          createdAt: r.created_at ?? '',
        })),
      })),
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
