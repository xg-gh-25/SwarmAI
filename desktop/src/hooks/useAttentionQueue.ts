/**
 * useAttentionQueue — data hook for the unified "Need You" channel.
 *
 * Design: Knowledge/Designs/2026-08-08-unified-need-you-channel-design.md;
 * one shared cache since docs/design/welcome-screen-v2.md.
 *
 * A thin poller of the backend AttentionAuthority (GET /api/attention). It reads
 * the SAME react-query key as the Needs You overlay and the Welcome screen card
 * (`overlayQuery.needsYou()`, ['overlay','needs-you']) and adds a 30s
 * refetchInterval, so the pill badge, the overlay and the Welcome card always show
 * one number from one request.
 *
 * Principle 1 (decouple from chat/streaming): there is NO getStreamingState /
 * waiting-tab source. Need You carries only escalation / paused-decision /
 * governance / broken-job.
 *
 * Fails soft: a failed refetch keeps the last-good data (react-query keeps `data`
 * on error), and before the first success the queue is empty.
 *
 * Exports:
 * - useAttentionQueue — React hook → { items, counts }
 */
import { useQuery } from '@tanstack/react-query';
import { overlayQuery } from '../components/layout/overlayQueries';
import type { AttentionEntry } from '../services/attention';

const POLL_MS = 30_000;

export interface AttentionQueue {
  items: AttentionEntry[];
  counts: { blocking: number; review: number };
}

const EMPTY: AttentionQueue = { items: [], counts: { blocking: 0, review: 0 } };

export function useAttentionQueue(): AttentionQueue {
  const { data } = useQuery({ ...overlayQuery.needsYou(), refetchInterval: POLL_MS });
  return data ?? EMPTY;
}
