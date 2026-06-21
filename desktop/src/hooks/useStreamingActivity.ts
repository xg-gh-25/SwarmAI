/**
 * useStreamingActivity — per-tab streaming activity label + elapsed timer.
 *
 * Computes the debounced spinner activity (`displayedActivity`) and the
 * `elapsedSeconds` counter for a SINGLE tab, derived from that tab's own
 * `isStreaming` flag and `messages`. Extracted from the monolithic
 * `useChatStreamingLifecycle` (which produced one set of values for the active
 * tab only) so each keep-mounted `TabView` can compute its own — a prerequisite
 * for rendering N tabs concurrently (chat-tab-view-isolation, F4).
 *
 * Gating (F4): the per-second elapsed timer and the activity debounce timer run
 * ONLY while `isStreaming` is true. Idle / background non-streaming tabs run no
 * timers at all, so mounting several `TabView`s does not spawn N idle timers.
 *
 * @exports useStreamingActivity
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { Message } from '../types';
import {
  deriveStreamingActivity,
  MIN_ACTIVITY_DISPLAY_MS,
  type StreamingActivity,
} from './useChatStreamingLifecycle';

export interface StreamingActivityResult {
  /** Debounced activity label — stable for at least MIN_ACTIVITY_DISPLAY_MS. */
  displayedActivity: StreamingActivity | null;
  /** Seconds since this tab's stream started (0 when not streaming). */
  elapsedSeconds: number;
}

/**
 * @param isStreaming - this tab's authoritative streaming flag
 * @param messages    - this tab's messages (from its own MessageStore)
 */
export function useStreamingActivity(
  isStreaming: boolean,
  messages: Message[],
): StreamingActivityResult {
  // Raw activity derived from messages — null = "Thinking…" (no content yet).
  const streamingActivity = useMemo(
    () => deriveStreamingActivity(isStreaming, messages),
    [isStreaming, messages],
  );

  // ── Debounced activity label (min display duration to avoid flicker) ──
  const [displayedActivity, setDisplayedActivity] = useState<StreamingActivity | null>(null);
  const lastChangeRef = useRef<number>(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      setDisplayedActivity(streamingActivity);
      lastChangeRef.current = 0;
      return;
    }
    // null (no content) shows immediately — preserves the "Thinking…" path.
    if (streamingActivity === null) {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      setDisplayedActivity(null);
      return;
    }
    const now = Date.now();
    const elapsed = now - lastChangeRef.current;
    if (elapsed >= MIN_ACTIVITY_DISPLAY_MS || lastChangeRef.current === 0) {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      setDisplayedActivity(streamingActivity);
      lastChangeRef.current = now;
    } else {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        setDisplayedActivity(streamingActivity);
        lastChangeRef.current = Date.now();
      }, MIN_ACTIVITY_DISPLAY_MS - elapsed);
    }
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    };
  }, [streamingActivity, isStreaming]);

  // ── Elapsed seconds — timer runs ONLY while streaming (F4 gate) ──
  // The elapsed counter measures how long the CURRENT activity has been
  // displayed, NOT the whole stream. It re-anchors whenever the debounced
  // `displayedActivity.toolName` changes, so the rendered "{tool} · {elapsed}"
  // reads correctly (e.g. "adversarial · 2m" = this tool ran 2 min). Anchoring
  // to the DEBOUNCED label value (not raw `streamingActivity`) keeps the label
  // and timer in lock-step — anchoring to the raw value would let them diverge
  // by the debounce window, re-introducing the very staleness this fixes.
  // The "total wait" signal is still carried by the no-tool "Thinking… {elapsed}"
  // path (toolName === null → anchor stays at stream start). (run_81a580ba)
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const startRef = useRef<number | null>(null);
  // The tool_use block id the timer is currently anchored to — change ⇒
  // re-anchor. Keyed on the per-invocation id (NOT the tool NAME): a recurring
  // name (Read → think → Read, or two Bash calls) is a distinct invocation and
  // must re-anchor, else the timer shows the cumulative run of all calls with
  // that name. Anchoring on the DEBOUNCED displayedActivity.toolId keeps the
  // label and timer in lock-step (anchoring on the raw value would let them
  // diverge by the debounce window). (run_81a580ba)
  const anchoredToolIdRef = useRef<string | null>(null);
  const activeToolId = displayedActivity?.toolId ?? null;

  useEffect(() => {
    if (!isStreaming) {
      startRef.current = null;
      anchoredToolIdRef.current = null;
      setElapsedSeconds(0);
      return;
    }
    // (Re-)anchor when the stream just started OR the displayed tool_use block
    // changed. toolId === null (Thinking…) keeps the existing anchor — there is
    // no tool boundary to reset on, so elapsed reflects time since the current
    // activity (thinking, or the stream) began.
    const toolChanged =
      activeToolId !== null && activeToolId !== anchoredToolIdRef.current;
    if (startRef.current === null || toolChanged) {
      startRef.current = Date.now();
      anchoredToolIdRef.current = activeToolId;
      setElapsedSeconds(0);
    }
    const interval = setInterval(() => {
      if (startRef.current !== null) {
        setElapsedSeconds(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isStreaming, activeToolId]);

  return { displayedActivity, elapsedSeconds };
}
