/**
 * usePendingToolElapsed — live per-tool elapsed seconds for a PENDING tool card.
 *
 * Problem (run_02e658d0): during a long tool-execution loop the pending tool
 * CARD (MergedToolBlock) shows only a static `animate-spin` icon — visually
 * identical at 5s and 5min, so a long-running tool reads as "frozen" even though
 * the turn is alive. The streaming summary LINE already has a live elapsed timer
 * (useStreamingActivity), but the eye rests on the card, and the card is dead.
 *
 * This hook adds a live per-second elapsed count scoped to ONE tool card. It is
 * deliberately a near-copy of useStreamingActivity's proven-safe timer discipline
 * (run_81a580ba): the three traps that make a streaming-render timer dangerous
 * are all handled the same way the existing hook handles them:
 *
 *   1. **Re-anchor on the per-invocation id, never mount-time.** React reuses a
 *      keyed-list component instance across blocks (AssistantMessageView keys by
 *      `tu-${block.id}`); if the instance is re-pointed at a different tool_use,
 *      a mount-time anchor would show the PRIOR tool's accumulated time. We anchor
 *      a ref to `toolUseId` and reset when it changes.
 *   2. **Gate the interval on `isPending`.** Tabs are keep-mounted, so every
 *      historical completed card stays mounted; an ungated interval would leave N
 *      idle 1s timers running across every tab (the F4 hazard). The interval runs
 *      ONLY while this card is the pending tool; at most ONE per tab.
 *   3. **Always clear the interval** on unmount AND when `isPending` flips false.
 *
 * Purely client-side + component-local: no stored field on ToolUseContent, no
 * MessageStore write, no SSE coupling, no module-level shared state. It therefore
 * does not touch the OT01 reconcile-race / cross-tab-isolation zones — a timer in
 * a component scopes to its own TabView instance by construction.
 *
 * Returns elapsed whole-seconds since this tool became pending, or null when the
 * tool is not pending (caller renders nothing).
 */
import { useEffect, useRef, useState } from 'react';

export function usePendingToolElapsed(
  toolUseId: string,
  isPending: boolean,
): number | null {
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const startRef = useRef<number | null>(null);
  // The tool_use id the timer is anchored to — change ⇒ re-anchor (a reused
  // component instance pointed at a new tool must reset, never accumulate).
  const anchoredIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isPending) {
      // Not the live tool (completed card, or stream ended) — stop + reset so a
      // later re-pending of the SAME instance starts clean.
      startRef.current = null;
      anchoredIdRef.current = null;
      setElapsedSeconds(0);
      return;
    }

    // (Re-)anchor when first becoming pending OR when the instance is re-pointed
    // at a different tool_use block. Keyed on toolUseId (the per-invocation id),
    // NOT mount-time — two Bash calls are distinct invocations and must reset.
    if (startRef.current === null || anchoredIdRef.current !== toolUseId) {
      startRef.current = Date.now();
      anchoredIdRef.current = toolUseId;
      setElapsedSeconds(0);
    }

    const interval = setInterval(() => {
      if (startRef.current !== null) {
        setElapsedSeconds(Math.floor((Date.now() - startRef.current) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isPending, toolUseId]);

  return isPending ? elapsedSeconds : null;
}
