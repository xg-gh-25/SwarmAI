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
  // The toolName the timer is currently anchored to — change ⇒ re-anchor.
  const anchoredToolNameRef = useRef<string | null>(null);
  const activeToolName = displayedActivity?.toolName ?? null;

  useEffect(() => {
    if (!isStreaming) {
      startRef.current = null;
      anchoredToolNameRef.current = null;
      setElapsedSeconds(0);
      return;
    }
    // (Re-)anchor when the stream just started OR the displayed tool changed.
    // toolName === null (Thinking…) keeps the existing anchor — there is no
    // tool boundary to reset on, so elapsed reflects time since thinking began.
    const toolChanged =
      activeToolName !== null && activeToolName !== anchoredToolNameRef.current;
    if (startRef.current === null || toolChanged) {
      startRef.current = Date.now();
      anchoredToolNameRef.current = activeToolName;
      setElapsedSeconds(0);
    }
    const interval = setInterval(() => {
      if (startRef.current !== null) {
        setElapsedSeconds(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isStreaming, activeToolName]);

  return { displayedActivity, elapsedSeconds };
}
