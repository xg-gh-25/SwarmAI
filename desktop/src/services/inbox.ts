/**
 * inbox — client for the Inbox API (`/api/inbox`, design docs/design/2026-11-20-inbox.md
 * §7-§8). The shapes below mirror `backend/schemas/inbox.py`, which is the source of
 * truth: a field added there is added here under the same (snake_case) name, so the UI
 * reads exactly what the router returns.
 *
 * Sending is the one place that differs from the other services: `send` returns a
 * refusal (403 caller is not the desktop UI, 409 target_changed / redacted / not_open)
 * as a VALUE instead of throwing, because the overlay shows the server's reason under
 * the draft and must never retry a send by itself. The shared axios interceptor would
 * flatten FastAPI's `{detail: {...}}` body (the reason code and the redacted spans), so
 * `send` accepts 403/409 through `validateStatus` and parses the body here. No method in
 * this file retries.
 *
 * Feed configuration (`/api/inbox/feeds`) moved here from `/api/community/feeds` in the
 * same shape; `listFeeds` maps it to the `CommunitySource` shape the feed editor reads.
 *
 * Every write carries `X-Swarm-User-Action` (USER_ACTION_HEADER).
 *
 * Key exports: `inboxService`, the schema types, `parseSendRefusal`, `channelKey`.
 */
import api from './api';
import type { CommunitySource } from './community';

export type LaneName = 'reply' | 'proposed' | 'aware' | 'trend';
export type SendStatusName = 'reserved' | 'sent' | 'failed' | 'unknown';
export type SourceHealthName = 'ok' | 'auth_failed' | 'error' | 'read_only';
export type ItemStateName = 'open' | 'replied' | 'done' | 'dismissed' | 'snoozed';
export type SendsFilter = 'all' | 'mine' | 'auto';
export type ItemAction = 'later' | 'dismiss' | 'done';

export interface InboxCounts {
  reply: number;
  proposed: number;
  aware: number;
  trend: number;
  sends_need_you: number;
  skipped: number;
  unhealthy_sources: string[];
  updated_at: string | null;
}

export interface ItemOut {
  id: string;
  lane: LaneName;
  state: ItemStateName;
  source_id: string;
  source_kind: string;
  channel_label: string;
  author: string;
  author_role: string;
  where: string;
  received_at: string;
  late: boolean;
  ask: string;
  ask_sub: string;
  excerpt: string;
  url: string;
  priority: number;
  producer: string | null;
}

export interface TargetOut {
  label: string;
  recipients: string[];
  public: boolean;
  flags: string[];
  target_sha: string;
  can_send: boolean;
}

export interface DraftOut {
  id: string;
  item_id: string;
  text: string;
  lang: string;
  note: string;
  created_at: string;
}

export interface SendRequest {
  draft_id: string | null;
  text: string;
  target_sha: string;
}

export interface SendOut {
  id: string;
  item_id: string;
  origin: 'click' | 'auto';
  producer: string | null;
  status: SendStatusName;
  to_label: string;
  source_kind: string;
  channel_label: string;
  text: string;
  url: string;
  created_at: string;
  sent_at: string | null;
  error: string;
  blocked_by_source: boolean;
  has_reply: boolean;
  reply_item_id: string | null;
}

export interface SendsOut {
  need_you: SendOut[];
  sent: SendOut[];
  total_7d: number;
  auto_7d: number;
}

export interface TrendPoint {
  title: string;
  why: string;
  source_label: string;
  item_id: string | null;
  url: string;
}

export interface TrendSummaryOut {
  generated_at: string | null;
  points: TrendPoint[];
  feeds: number;
  watched_repos: number;
}

export interface SourceOut {
  id: string;
  kind: string;
  channel_label: string;
  backend: string;
  identity: string;
  interval_s: number;
  health: SourceHealthName;
  fix: string;
  last_pull_at: string | null;
  last_error: string;
  can_send: boolean;
  detail: string;
}

/** A send the server refused. `code` is the reason the router names (`target_changed`,
 *  `redacted`, `not_open`, or `forbidden` for a 403); `message` is its text for the
 *  person; `spans` are the [start, end) character ranges of the draft that redaction
 *  would change (only for `redacted`). */
export interface SendRefusal {
  status: number;
  code: string;
  message: string;
  spans: Array<{ start: number; end: number }>;
}

export type SendResult = { ok: true; send: SendOut } | { ok: false; refusal: SendRefusal };

/** The channel colour family of a source kind (`--color-channel-<key>` in index.css).
 *  `feed:<type>` is one family; a kind with no token yet is `other`. */
export type ChannelKey = 'slack' | 'mail' | 'github' | 'feed' | 'other';
export function channelKey(sourceKind: string): ChannelKey {
  const k = (sourceKind || '').toLowerCase();
  if (k === 'slack' || k === 'mail' || k === 'github' || k === 'feed') return k;
  if (k.startsWith('feed:')) return 'feed';
  return 'other';
}

const enc = encodeURIComponent;

/** Every Inbox write carries this header: a cross-site page cannot set it (it is not a
 *  CORS-safelisted header), so the router can refuse a write that did not come from
 *  the app (design §8; the same convention as awsAccount / ingestion). */
export const USER_ACTION_HEADER = 'X-Swarm-User-Action';
const ua = (action: string) => ({ headers: { [USER_ACTION_HEADER]: `inbox-${action}` } });

function spansOf(raw: unknown): Array<{ start: number; end: number }> {
  if (!Array.isArray(raw)) return [];
  const out: Array<{ start: number; end: number }> = [];
  for (const s of raw) {
    let start: unknown;
    let end: unknown;
    if (Array.isArray(s)) [start, end] = s;
    else if (s && typeof s === 'object') ({ start, end } = s as { start?: unknown; end?: unknown });
    if (typeof start === 'number' && typeof end === 'number' && end > start && start >= 0) out.push({ start, end });
  }
  return out;
}

/** Read a 403/409 body. FastAPI puts the reason in `detail`: a string (the code or a
 *  sentence) or an object `{code | reason, message, spans}`. A top-level `{code,
 *  message}` is read too. The contract does not fix this shape yet (see the run's
 *  merge notes), so every form above is accepted. */
export function parseSendRefusal(status: number, body: unknown): SendRefusal {
  const data = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const detail = data.detail;
  let code = '';
  let message = '';
  let spans: Array<{ start: number; end: number }> = [];
  if (typeof detail === 'string') {
    if (/^[a-z_]+$/.test(detail)) code = detail;
    else message = detail;
  } else if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    code = typeof d.code === 'string' ? d.code : typeof d.reason === 'string' ? d.reason : '';
    message = typeof d.message === 'string' ? d.message : '';
    spans = spansOf(d.spans);
  }
  if (!code && typeof data.code === 'string') code = data.code;
  if (!message && typeof data.message === 'string') message = data.message;
  if (!spans.length) spans = spansOf(data.spans);
  if (!code) code = status === 403 ? 'forbidden' : 'conflict';
  return { status, code, message, spans };
}

function toCounts(raw: unknown): InboxCounts {
  const d = (raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}) as Partial<InboxCounts>;
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
  return {
    reply: n(d.reply),
    proposed: n(d.proposed),
    aware: n(d.aware),
    trend: n(d.trend),
    sends_need_you: n(d.sends_need_you),
    skipped: n(d.skipped),
    unhealthy_sources: Array.isArray(d.unhealthy_sources) ? d.unhealthy_sources.filter((s): s is string => typeof s === 'string') : [],
    updated_at: typeof d.updated_at === 'string' ? d.updated_at : null,
  };
}

const list = <T,>(raw: unknown): T[] => (Array.isArray(raw) ? (raw as T[]) : []);

interface RawFeed {
  id: string; name: string; type: string; tier: string; enabled: boolean; managed_by?: string;
  members?: string[]; member_count?: number; members_truncated?: boolean; member_kind?: string | null; tags?: string[];
}

export const inboxService = {
  async counts(): Promise<InboxCounts> {
    return toCounts((await api.get('/inbox/counts')).data);
  },
  async items(lane: LaneName): Promise<ItemOut[]> {
    return list<ItemOut>((await api.get(`/inbox/items?lane=${enc(lane)}`)).data);
  },
  async target(id: string): Promise<TargetOut> {
    return (await api.get<TargetOut>(`/inbox/items/${enc(id)}/target`)).data;
  },
  async draft(id: string): Promise<DraftOut> {
    return (await api.get<DraftOut>(`/inbox/items/${enc(id)}/draft`)).data;
  },
  async redraft(id: string): Promise<DraftOut> {
    return (await api.post<DraftOut>(`/inbox/items/${enc(id)}/redraft`, undefined, ua('redraft'))).data;
  },
  /** The only call that sends. One POST, never retried; 403/409 come back as a refusal. */
  async send(id: string, body: SendRequest): Promise<SendResult> {
    const res = await api.post(`/inbox/items/${enc(id)}/send`, body, {
      ...ua('send'),
      validateStatus: (s) => (s >= 200 && s < 300) || s === 403 || s === 409,
    });
    if (res.status === 403 || res.status === 409) return { ok: false, refusal: parseSendRefusal(res.status, res.data) };
    return { ok: true, send: res.data as SendOut };
  },
  async action(id: string, action: ItemAction): Promise<ItemOut> {
    return (await api.post<ItemOut>(`/inbox/items/${enc(id)}/action`, { action }, ua('action'))).data;
  },
  async sends(filter: SendsFilter): Promise<SendsOut> {
    const d = (await api.get(`/inbox/sends?filter=${enc(filter)}`)).data as Partial<SendsOut> | undefined;
    return {
      need_you: list<SendOut>(d?.need_you),
      sent: list<SendOut>(d?.sent),
      total_7d: typeof d?.total_7d === 'number' ? d.total_7d : 0,
      auto_7d: typeof d?.auto_7d === 'number' ? d.auto_7d : 0,
    };
  },
  async notSent(sendId: string): Promise<SendOut> {
    return (await api.post<SendOut>(`/inbox/sends/${enc(sendId)}/not-sent`, undefined, ua('not-sent'))).data;
  },
  async resend(sendId: string): Promise<SendOut> {
    return (await api.post<SendOut>(`/inbox/sends/${enc(sendId)}/resend`, undefined, ua('resend'))).data;
  },
  async trendSummary(): Promise<TrendSummaryOut> {
    const d = (await api.get('/inbox/trends/summary')).data as Partial<TrendSummaryOut> | undefined;
    return {
      generated_at: typeof d?.generated_at === 'string' ? d.generated_at : null,
      points: list<TrendPoint>(d?.points),
      feeds: typeof d?.feeds === 'number' ? d.feeds : 0,
      watched_repos: typeof d?.watched_repos === 'number' ? d.watched_repos : 0,
    };
  },
  async sources(): Promise<SourceOut[]> {
    return list<SourceOut>((await api.get('/inbox/sources')).data);
  },
  async retrySource(id: string): Promise<SourceOut> {
    return (await api.post<SourceOut>(`/inbox/sources/${enc(id)}/retry`, undefined, ua('retry'))).data;
  },
  async refresh(): Promise<InboxCounts> {
    return toCounts((await api.post('/inbox/refresh', undefined, ua('refresh'))).data);
  },

  // ── feed configuration (moved from /api/community/feeds, same shapes) ──
  async listFeeds(): Promise<CommunitySource[]> {
    const d = (await api.get('/inbox/feeds')).data as { sources?: RawFeed[]; feeds?: RawFeed[] } | RawFeed[] | undefined;
    const raw = Array.isArray(d) ? d : (d?.sources ?? d?.feeds ?? []);
    return raw.map((s) => ({
      id: s.id,
      name: s.name,
      type: s.type,
      tier: s.tier,
      enabled: s.enabled,
      managedBy: s.managed_by ?? 'manual',
      members: s.members ?? [],
      memberCount: s.member_count ?? 0,
      membersTruncated: s.members_truncated ?? false,
      memberKind: s.member_kind ?? null,
      tags: s.tags ?? [],
    }));
  },
  async addFeed(feed: { id: string; name: string; type: string; tier?: string }): Promise<void> {
    await api.post('/inbox/feeds', feed, ua('feeds'));
  },
  async updateFeed(id: string, patch: { enabled?: boolean; tier?: string }): Promise<void> {
    await api.put(`/inbox/feeds/${enc(id)}`, patch, ua('feeds'));
  },
  async deleteFeed(id: string): Promise<void> {
    await api.delete(`/inbox/feeds/${enc(id)}`, ua('feeds'));
  },
  // member values hold slashes (URLs): sent in the body, never the path
  async addFeedMember(id: string, value: string): Promise<void> {
    await api.post(`/inbox/feeds/${enc(id)}/members`, { value }, ua('feeds'));
  },
  async deleteFeedMember(id: string, value: string): Promise<void> {
    await api.delete(`/inbox/feeds/${enc(id)}/members`, { data: { value }, ...ua('feeds') });
  },
};
