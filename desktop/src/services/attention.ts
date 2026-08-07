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
 */
import api from './api';

/** One normalized attention item (mirrors backend AttentionItem, snake→camel). */
export interface AttentionEntry {
  id: string;
  source: 'escalation' | 'paused_run' | 'cultivation' | 'governance' | 'job';
  tier: 'blocking' | 'review';
  /** Owning brain/project, or null for OS-level (governance/job infra). */
  brain: string | null;
  title: string;
  detail: string;
  dispatch: { message: string; context?: Record<string, unknown> };
}

export interface AttentionResult {
  items: AttentionEntry[];
  counts: { blocking: number; review: number };
}

/** Raw backend shape (snake_case per FastAPI). */
interface RawAttention {
  items?: Array<{
    id: string;
    source: AttentionEntry['source'];
    tier: AttentionEntry['tier'];
    brain: string | null;
    title: string;
    detail?: string;
    dispatch?: { message: string; context?: Record<string, unknown> };
  }>;
  counts?: { blocking?: number; review?: number };
}

function normalize(raw: RawAttention): AttentionResult {
  const items: AttentionEntry[] = (raw.items ?? []).map((it) => ({
    id: it.id,
    source: it.source,
    tier: it.tier,
    brain: it.brain ?? null,
    title: it.title,
    detail: it.detail ?? '',
    dispatch: it.dispatch ?? { message: it.title },
  }));
  return {
    items,
    counts: {
      blocking: raw.counts?.blocking ?? items.filter((i) => i.tier === 'blocking').length,
      review: raw.counts?.review ?? items.filter((i) => i.tier === 'review').length,
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
    const response = await api.get<RawAttention>(`/api/attention${q}`);
    return normalize(response.data ?? {});
  },
};
