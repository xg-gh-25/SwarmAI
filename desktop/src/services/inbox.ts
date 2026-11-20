/**
 * inbox — client for the Inbox API (`/api/inbox`, design docs/design/2026-11-20-inbox.md
 * §7-§8). The shapes below mirror `backend/schemas/inbox.py`, which is the source of
 * truth: a field added there is added here under the same (snake_case) name, so the UI
 * reads exactly what the router returns.
 *
 * Two calls return a refusal as a VALUE instead of throwing, because the UI shows the
 * server's reason in place and must never retry by itself:
 *   - `send`: 403 (caller is not the desktop UI) / 409 (target_changed, redacted,
 *     not_open, ...), whose body is `{detail: SendRefusalOut}`;
 *   - `addSource`: 400 whoami_failed (with the source's `fix`), 409 already_added, 422,
 *     503 secret_store_unavailable, whose body is `{detail: SourceRefusalOut}`.
 * The shared axios interceptor would flatten FastAPI's `{detail: {...}}` body (the
 * reason code, the redacted spans, the fix), so these accept those statuses through
 * `validateStatus` and parse the body here. No method in this file retries.
 *
 * Secrets (a Slack user token, a mail password) are sent once in a request body and
 * never come back: no type below has a field that could hold one.
 *
 * Feed configuration (`/api/inbox/feeds`, moved from the deleted Community page in the
 * same shape, `FeedsOut`); `listFeeds` maps it to the `FeedSource` shape the feed editor
 * reads.
 *
 * Every write carries `X-Swarm-User-Action` (USER_ACTION_HEADER).
 *
 * Key exports: `inboxService`, the schema types, `parseSendRefusal`,
 * `parseSourceRefusal`, `channelKey`.
 */
import api from './api';

/** One configured feed, a view of config.yaml `feeds:` (camelCase of `FeedOut`). */
export interface FeedSource {
  id: string;
  name: string;
  type: string;
  tier: string;
  enabled: boolean;
  /** "manual" (default when absent) | "user" (edited in the Inbox) | an older config's value. */
  managedBy: string;
  /** The feed's editable string members (urls/keywords/repos/…), capped. */
  members: string[];
  /** The true total member count (even when `members` is capped). */
  memberCount: number;
  /** true when `members` is a capped view of a longer list. */
  membersTruncated: boolean;
  /** The config key the members live under (e.g. "urls"), or null = no editable members. */
  memberKind: string | null;
  tags: string[];
}

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
  /** why sending is off (read_only): shown in the channel row */
  status_detail: string;
  enabled: boolean;
  /** watched_channels, extra_addresses, ... (never a secret) */
  options: Record<string, unknown>;
}

/** A source this machine can connect with one click (`addSource` with its kind,
 *  backend and options). */
export interface AvailableSourceOut {
  kind: string;
  backend: string;
  channel_label: string;
  label: string;
  options: Record<string, unknown>;
  identity: string;
  detail: string;
  added: boolean;
  source_id: string | null;
}

export interface SourceCreateRequest {
  kind: string;
  backend: string;
  options?: Record<string, unknown>;
  /** slack-web: user_token; imap: password (and smtp_password) */
  secrets?: Record<string, string>;
}

export interface SourcePatchRequest {
  enabled?: boolean;
  interval_s?: number;
  watched_channels?: string[];
  extra_addresses?: string[];
}

export interface SourceDeletedOut {
  id: string;
  items: number;
  drafts: number;
  sends: number;
}

/** `SourceRefusalOut` with the HTTP status: why a source was not added. */
export interface SourceRefusal {
  status: number;
  code: string;
  message: string;
  fix: string;
}

export type AddSourceResult = { ok: true; source: SourceOut } | { ok: false; refusal: SourceRefusal };

export interface FeedOut {
  id: string; name: string; type: string; tier: string; enabled: boolean; managed_by: string;
  member_kind: string | null; member_count: number; members: string[]; members_truncated: boolean; tags: string[];
}

export interface FeedsOut {
  count: number;
  sources: FeedOut[];
}

/** `SendRefusalOut` (backend/schemas/inbox.py) with the HTTP status. `code` is the
 *  reason (`target_changed`, `redacted`, `not_open`, `cannot_send`, `source_removed`,
 *  `forbidden` for a 403); `message` its text for the person; `spans` the [start, end)
 *  code-point ranges of the draft redaction would change (only for `redacted`);
 *  `target` the fresh target (only for `target_changed`); `reason` which caller check
 *  refused (403). */
export interface SendRefusal {
  status: number;
  code: string;
  message: string;
  spans: Array<{ start: number; end: number }>;
  target?: TargetOut | null;
  reason?: string;
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
    if (!s || typeof s !== 'object') continue;
    const { start, end } = s as { start?: unknown; end?: unknown };
    if (typeof start === 'number' && typeof end === 'number' && end > start && start >= 0) out.push({ start, end });
  }
  return out;
}

const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** The `detail` object of a FastAPI error body, or `{message}` for a string detail (a
 *  framework 4xx that is not the contract's). */
function detailOf(body: unknown): Record<string, unknown> {
  const detail = body && typeof body === 'object' ? (body as Record<string, unknown>).detail : undefined;
  if (typeof detail === 'string') return { message: detail };
  return detail && typeof detail === 'object' && !Array.isArray(detail) ? (detail as Record<string, unknown>) : {};
}

/** Read a 403/409 body of a sending route: `{detail: SendRefusalOut}`. Malformed spans
 *  are dropped, never trusted. */
export function parseSendRefusal(status: number, body: unknown): SendRefusal {
  const d = detailOf(body);
  const target = d.target && typeof d.target === 'object' ? (d.target as TargetOut) : null;
  return {
    status,
    code: str(d.code) || (status === 403 ? 'forbidden' : 'conflict'),
    message: str(d.message),
    spans: spansOf(d.spans),
    ...(target ? { target } : {}),
    ...(str(d.reason) ? { reason: str(d.reason) } : {}),
  };
}

/** Read a refused source write: `{detail: SourceRefusalOut}` (400/409/422/503). */
export function parseSourceRefusal(status: number, body: unknown): SourceRefusal {
  const d = detailOf(body);
  return { status, code: str(d.code) || 'refused', message: str(d.message), fix: str(d.fix) };
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
  /** What this machine can connect without typing anything; `refresh` probes again. It
   *  starts the configured MCP servers to list their tools, so it carries the header too. */
  async availableSources(refresh = false): Promise<AvailableSourceOut[]> {
    return list<AvailableSourceOut>((await api.get(`/inbox/sources/available${refresh ? '?refresh=true' : ''}`, ua('available'))).data);
  },
  /** One POST; the backend stores the source only after its whoami succeeded. A refusal
   *  (whoami failed with its fix, already added, invalid) comes back as a value. */
  async addSource(body: SourceCreateRequest): Promise<AddSourceResult> {
    const res = await api.post('/inbox/sources', body, {
      ...ua('add-source'),
      validateStatus: (s) => (s >= 200 && s < 300) || s === 400 || s === 409 || s === 422 || s === 503,
    });
    if (res.status >= 400) return { ok: false, refusal: parseSourceRefusal(res.status, res.data) };
    return { ok: true, source: res.data as SourceOut };
  },
  async updateSource(id: string, patch: SourcePatchRequest): Promise<SourceOut> {
    return (await api.patch<SourceOut>(`/inbox/sources/${enc(id)}`, patch, ua('update-source'))).data;
  },
  async removeSource(id: string): Promise<SourceDeletedOut> {
    return (await api.delete<SourceDeletedOut>(`/inbox/sources/${enc(id)}`, ua('remove-source'))).data;
  },
  /** Store a token or password of a source; it is never returned. */
  async setSourceSecret(id: string, name: string, value: string): Promise<SourceOut> {
    return (await api.post<SourceOut>(`/inbox/sources/${enc(id)}/secret`, { name, value }, ua('secret'))).data;
  },
  async refresh(): Promise<InboxCounts> {
    return toCounts((await api.post('/inbox/refresh', undefined, ua('refresh'))).data);
  },

  // ── feed configuration (config.yaml `feeds:`) ──
  async listFeeds(): Promise<FeedSource[]> {
    const d = (await api.get('/inbox/feeds')).data as Partial<FeedsOut> | undefined;
    return list<Partial<FeedOut> & Pick<FeedOut, 'id' | 'name' | 'type' | 'tier' | 'enabled'>>(d?.sources).map((s) => ({
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
