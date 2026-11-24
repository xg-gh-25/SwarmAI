/**
 * attention — client for the unified "Need You" channel (GET /api/attention).
 *
 * Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md
 *
 * ONE backend read-aggregation layer (AttentionAuthority) behind BOTH the
 * needs-you overlay AND the AlertsPill badge. Replaces the old 3-source frontend
 * merge (paused pipelines + jobs + waiting-tabs) — critically, the `waiting`
 * source and its `getStreamingState` poll are GONE (design principle 1: Need You
 * is decoupled from chat/streaming; a waiting tab is already obvious in its tab).
 *
 * Item action = the item's `dispatch.message` is injected into a chat tab via
 * the existing onItemClick mechanism (no /act endpoint, no new dispatch channel).
 * Each item says who it waits on (`handler`): only the person's items count as
 * needing the person.
 */
import api from './api';

/** One normalized attention item (mirrors backend AttentionItem, snake→camel).
 *  Every text field is written by the backend FOR THE USER and shown as-is:
 *  `title` = what is needed of them, `summary` = which thing it is,
 *  `dispatch.message` = the prompt in their voice (fill or send). */
export interface AttentionEntry {
  id: string;
  source: 'escalation' | 'paused_run' | 'governance' | 'job' | 'job_digest' | 'db_recovery' | 'ingestion' | 'local_root' | 'swarm_out' | 'todo' | 'aws_account' | 'template_drift' | 'inbox';
  tier: 'blocking' | 'review';
  /** Owning brain/project, or null for OS-level (governance/job infra). */
  brain: string | null;
  title: string;
  summary: string;
  detail: string;
  dispatch: { message: string; context?: Record<string, unknown> };
  /** Who it waits on (backend handler_of): the person, or Swarm itself. */
  handler: 'person' | 'agent';
  /** When it started waiting (ISO), when the source knows it. */
  since: string | null;
  /** Swarm's own automatic run on the item (backend core/attention_auto), else null. A
   *  run that did not work comes back as the person's item, with its note in `detail`. */
  auto: AttentionAuto | null;
}

export interface AttentionAuto {
  state: 'running' | 'done' | 'failed' | 'interrupted';
  /** When the state last changed (ISO). */
  at: string | null;
  note: string;
  /** The session the run used, when it had one (a digest has none). */
  sessionId: string | null;
}

const AUTO_STATES = new Set<AttentionAuto['state']>(['running', 'done', 'failed', 'interrupted']);

function normalizeAuto(raw: unknown): AttentionAuto | null {
  if (!raw || typeof raw !== 'object') return null;
  const a = raw as { state?: unknown; at?: unknown; note?: unknown; session_id?: unknown };
  if (typeof a.state !== 'string' || !AUTO_STATES.has(a.state as AttentionAuto['state'])) return null;
  return {
    state: a.state as AttentionAuto['state'],
    at: typeof a.at === 'string' && a.at ? a.at : null,
    note: typeof a.note === 'string' ? a.note : '',
    sessionId: typeof a.session_id === 'string' && a.session_id ? a.session_id : null,
  };
}

export interface AttentionResult {
  items: AttentionEntry[];
  /** ``person`` is what the badge, the Welcome card and the tab count show. */
  counts: { blocking: number; review: number; person: number; agent: number };
}

/** Raw backend shape (snake_case per FastAPI). */
interface RawAttention {
  items?: Array<{
    id: string;
    source: AttentionEntry['source'];
    tier: AttentionEntry['tier'];
    brain: string | null;
    title: string;
    summary?: string;
    detail?: string;
    dispatch?: { message: string; context?: Record<string, unknown> };
    handler?: string;
    since?: string | null;
    auto?: unknown;
  }>;
  counts?: { blocking?: number; review?: number; person?: number; agent?: number };
}

function normalize(raw: RawAttention): AttentionResult {
  const items: AttentionEntry[] = (raw.items ?? []).map((it) => ({
    id: it.id,
    source: it.source,
    tier: it.tier,
    brain: it.brain ?? null,
    title: it.title,
    summary: it.summary ?? '',
    detail: it.detail ?? '',
    dispatch: it.dispatch ?? { message: it.title },
    // anything but an explicit "agent" waits on the person (an older backend sends none)
    handler: it.handler === 'agent' ? 'agent' : 'person',
    since: typeof it.since === 'string' && it.since ? it.since : null,
    auto: normalizeAuto(it.auto),
  }));
  return {
    items,
    counts: {
      blocking: raw.counts?.blocking ?? items.filter((i) => i.tier === 'blocking').length,
      review: raw.counts?.review ?? items.filter((i) => i.tier === 'review').length,
      person: raw.counts?.person ?? items.filter((i) => i.handler === 'person').length,
      agent: raw.counts?.agent ?? items.filter((i) => i.handler === 'agent').length,
    },
  };
}

export const attentionService = {
  /**
   * Fetch the unified Need You queue.
   * @param brain optional — scope to one project (governance/OS-level excluded).
   */
  async fetchAttention(brain?: string): Promise<AttentionResult> {
    const q = brain ? `?brain=${encodeURIComponent(brain)}` : '';
    // Shared `api` instance's interceptor already prepends /api (api.ts) — pass a
    // BARE path here, never `/api/...` (that double-prefixes → /api/api → 404).
    const response = await api.get<RawAttention>(`/attention${q}`);
    return normalize(response.data ?? {});
  },
};
