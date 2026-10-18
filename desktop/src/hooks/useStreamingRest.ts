/**
 * useStreamingRest — per-tab "resting" cue for the streaming bee.
 *
 * Returns `true` once a streaming tab has gone STREAMING_REST_MS (30s) without
 * a change of `progressKey`, and `false` again the moment the key changes.
 *
 * What the key must be: TabView passes its render-source `messages` array. Its
 * identity changes ONLY when the tab's MessageStore content changes —
 * `getSnapshot()` is memoized on the store content version, and the SSE
 * heartbeat (every 15s) calls `MessageStore.touch()`, which resets the
 * watchdog without bumping that version. So "resting" means "nothing new has
 * been written for 30s" (silent thinking, a long tool, also a dropped
 * connection until the watchdog / reconnect logic takes over) — never "no bytes
 * at all", which the 15s heartbeat would make unreachable. Events that write no
 * content (heartbeat, still_working, thinking_progress) deliberately do not
 * wake it: that silent stretch is exactly what "mulling it over" describes. It
 * is NOT a stall alarm; that is `isLikelyStalled` in useChatStreamingLifecycle,
 * a different signal (one ref shared by all tabs, 60-600s thresholds).
 *
 * How: the timer records WHICH key it rested on; the hook reports resting only
 * while that is still the current key. A new key therefore reads "not resting"
 * on the very render it arrives — no stale resting frame — without any
 * setState during render (the key changes on every streamed token, so a
 * render-phase update would re-render TabView twice per token).
 *
 * Gating (same shape as useStreamingActivity, F4): the timer runs only while
 * `isStreaming` is true, so idle tabs hold no timers; stream end and unmount
 * clear it.
 *
 * @exports useStreamingRest, STREAMING_REST_MS
 */
import { useEffect, useState } from 'react';

/** Quiet time before the bee rests. */
export const STREAMING_REST_MS = 30_000;

/** Box around the rested-on key, so a key that is itself null/undefined still
 *  compares by identity against "not rested". */
type RestedOn = { key: unknown } | null;

export function useStreamingRest(isStreaming: boolean, progressKey: unknown): boolean {
  const [restedOn, setRestedOn] = useState<RestedOn>(null);

  useEffect(() => {
    if (!isStreaming) {
      // Clear so a later stream that starts on the same key begins awake.
      setRestedOn(null);
      return;
    }
    const timer = setTimeout(() => setRestedOn({ key: progressKey }), STREAMING_REST_MS);
    return () => clearTimeout(timer);
  }, [isStreaming, progressKey]);

  return isStreaming && restedOn !== null && restedOn.key === progressKey;
}
