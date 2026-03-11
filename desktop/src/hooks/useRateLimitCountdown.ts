/**
 * Isolated countdown hook for rate-limit UI components.
 *
 * Runs a 1-second interval timer only while mounted and while
 * `remainingSeconds > 0`, avoiding app-wide re-renders. Designed
 * to be used alongside `useRateLimiter` — accepts the limiter's
 * `getRemainingSeconds` function and an endpoint string.
 *
 * Key exports:
 * - `useRateLimitCountdown` — returns `remainingSeconds` state
 *
 * Validates: Requirements 6.3
 */

import { useState, useEffect } from 'react';

interface UseRateLimitCountdownOptions {
  /** Function that returns seconds remaining for an endpoint. */
  getRemainingSeconds: (endpoint: string) => number;
  /** The endpoint to track. */
  endpoint: string;
}

export function useRateLimitCountdown({
  getRemainingSeconds,
  endpoint,
}: UseRateLimitCountdownOptions): number {
  const [remainingSeconds, setRemainingSeconds] = useState(() =>
    getRemainingSeconds(endpoint),
  );

  useEffect(() => {
    // Seed initial value
    setRemainingSeconds(getRemainingSeconds(endpoint));

    const interval = setInterval(() => {
      const secs = getRemainingSeconds(endpoint);
      setRemainingSeconds(secs);
      if (secs <= 0) {
        clearInterval(interval);
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [getRemainingSeconds, endpoint]);

  return remainingSeconds;
}
