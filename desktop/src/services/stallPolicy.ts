/**
 * stallPolicy — the SINGLE source of truth for the liveness-gated stall/watchdog
 * decision (run_d2f25153, approach A2).
 *
 * Two independent client-side 90s terminators used to blind-cancel a stream on
 * byte-silence with NO liveness check (Gate-1 Finding 1):
 *   - the transport stall timer (services/chat.ts, reader.cancel())
 *   - the phase watchdog (stores/MessageStore.ts, endStreaming())
 * Both now consult THIS module so there is ONE authority, not a split-brain
 * (AC4). Kept dependency-free (no React, no service imports) so both the pure
 * service layer and the store can import it without a layering cycle.
 */

/** Loop-independent backend-liveness verdict (mirror of useHealthMonitor's
 *  BackendLiveness). 'alive' = backend up (never terminate within cap);
 *  'dead' = proven death (terminate); 'unknown' = no verdict yet (bounded). */
export type StallLiveness = 'alive' | 'dead' | 'unknown';

/** Re-arm BUDGET (not a hard deadline) for an 'unknown'-liveness stream (app boot,
 *  no health signal yet): it keeps re-arming while total silence is < this, then
 *  cancels on the NEXT fire. Effective cancel time overshoots by up to one stall
 *  interval — e.g. with a 90s interval, cancel lands at ~180s (the fire where
 *  silentMs first reaches ≥120s). That is intentional slack: it MUST exceed
 *  cold-start first-token latency (Bedrock cache-creation of the large injected
 *  context ~38-45s), so it never kills a slow-but-alive cold start (Gate-1
 *  Finding 4 / AC7). Heartbeats (every 15s) reset the accumulator anyway, so a
 *  real cold start never even reaches the first fire. */
export const UNKNOWN_REARM_BUDGET_MS = 120_000;

/** Turn-liveness cap: daemon-liveness ≠ the SDK subprocess is still producing
 *  (Gate-1 Finding 5). Past this total silence on an 'alive' backend we neither
 *  auto-cancel (would kill a live long turn) nor re-arm silently forever (would
 *  strand the spinner if the turn is truly dead) — we surface a
 *  'still working — Stop?' affordance and let the user decide (AC8). */
export const ALIVE_REARM_CAP_MS = 600_000; // 10 min — beyond any legit silent step

/** The stall/watchdog action to take when a timer fires. */
export type StallAction = 'rearm' | 'cancel' | 'affordance';

/** Decide what a fired stall/watchdog timer should do, given the loop-independent
 *  backend liveness verdict and the TOTAL elapsed silence so far. Pure — this is
 *  the entire no-blind-cancel safety story, shared by both terminators.
 *
 *  - 'alive'   → 'rearm' under the turn-liveness cap; 'affordance' at/after it.
 *                NEVER 'cancel' — a live backend's stream is never auto-killed.
 *  - 'dead'    → 'cancel' (proven death/wedge → termination authorized).
 *  - 'unknown' → 'rearm' under the budget (covers cold-start), else 'cancel'. */
export function decideStallAction(
  liveness: StallLiveness,
  totalSilentMs: number,
): StallAction {
  if (liveness === 'dead') return 'cancel';
  if (liveness === 'unknown') {
    return totalSilentMs >= UNKNOWN_REARM_BUDGET_MS ? 'cancel' : 'rearm';
  }
  // alive
  return totalSilentMs >= ALIVE_REARM_CAP_MS ? 'affordance' : 'rearm';
}
