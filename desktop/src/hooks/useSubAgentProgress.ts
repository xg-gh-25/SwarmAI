/**
 * useSubAgentProgress — polls backend for sub-agent progress when streaming.
 *
 * Returns tiered awareness level (T0-T4) based on elapsed time when an Agent
 * tool is actively running in the current session. Enables the frontend to
 * display progress banners that inform the user without force-killing agents.
 *
 * Polling: every 5s, only while isStreaming === true.
 * Stops immediately when backend reports active: false.
 */

import { useEffect, useRef, useState } from 'react';
import { chatService } from '../services/chat';

export type SubAgentTier = 0 | 1 | 2 | 3 | 4;

export interface SubAgentProgress {
  active: boolean;
  elapsedS: number;
  label: string | null;
  tier: SubAgentTier;
}

const POLL_INTERVAL_MS = 5000;

/** Compute tier from elapsed seconds. */
function computeTier(elapsedS: number): SubAgentTier {
  if (elapsedS >= 900) return 4;
  if (elapsedS >= 480) return 3;
  if (elapsedS >= 180) return 2;
  if (elapsedS >= 60) return 1;
  return 0;
}

export function useSubAgentProgress(
  sessionId: string | null,
  isStreaming: boolean,
): SubAgentProgress {
  const [progress, setProgress] = useState<SubAgentProgress>({
    active: false,
    elapsedS: 0,
    label: null,
    tier: 0,
  });

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Only poll when streaming and we have a session
    if (!isStreaming || !sessionId) {
      // Reset state when streaming stops — use functional update to avoid stale closure
      setProgress(prev => prev.active ? { active: false, elapsedS: 0, label: null, tier: 0 } : prev);
      return;
    }

    let cancelled = false;

    const poll = async () => {
      try {
        const data = await chatService.getSubAgentProgress(sessionId);
        if (cancelled) return; // Guard against setting state after cleanup
        if (data.active) {
          setProgress({
            active: true,
            elapsedS: data.elapsed_s,
            label: data.label,
            tier: computeTier(data.elapsed_s),
          });
        } else {
          // Functional update: skip if already inactive (prevents new object → re-render)
          setProgress(prev => prev.active ? { active: false, elapsedS: 0, label: null, tier: 0 } : prev);
        }
      } catch {
        // Silently ignore poll errors (session might have ended)
      }
    };

    // Start polling after a short initial delay (don't poll for quick tool calls)
    const startDelay = setTimeout(() => {
      if (cancelled) return;
      poll(); // First poll
      intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);
    }, 3000); // Wait 3s before first poll (most tool calls < 3s)

    return () => {
      cancelled = true;
      clearTimeout(startDelay);
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isStreaming, sessionId]);

  return progress;
}
