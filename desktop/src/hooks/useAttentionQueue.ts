/**
 * useAttentionQueue — data hook for the unified "Need You" channel.
 *
 * Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md
 *
 * REPLACES useRadarAttention's 3-source frontend merge with a SINGLE poll of the
 * backend AttentionAuthority (GET /api/attention). The backend now owns
 * aggregation + tiering + brain attribution, so this hook is a thin poller.
 *
 * Principle 1 (decouple from chat/streaming): there is NO getStreamingState /
 * waiting-tab source anymore. A waiting tab is obvious in its own tab; Need You
 * carries only escalation / paused-decision / cultivation / governance / broken-job.
 *
 * Exports:
 * - useAttentionQueue — React hook → { items, counts } (single 30s poll)
 */
import { useState, useEffect, useCallback } from 'react';
import { attentionService, type AttentionEntry } from '../services/attention';

const POLL_MS = 30_000;

function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

export interface AttentionQueue {
  items: AttentionEntry[];
  counts: { blocking: number; review: number };
}

const EMPTY: AttentionQueue = { items: [], counts: { blocking: 0, review: 0 } };

/**
 * Poll the unified attention queue. Fails soft — a transient fetch error keeps
 * the last-good queue rather than blanking the pill.
 */
export function useAttentionQueue(): AttentionQueue {
  const [queue, setQueue] = useState<AttentionQueue>(EMPTY);

  const poll = useCallback(async () => {
    const next = await attentionService.fetchAttention().catch(() => null);
    if (!next) return; // keep last-good on transient failure
    setQueue((prev) => (sameJson(prev, next) ? prev : next));
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  return queue;
}
