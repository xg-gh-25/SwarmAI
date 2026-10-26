// When to retry a failed sync: 30s, then 60s, then 120s, then give up.
export const RETRY_DELAYS_SECONDS = [30, 60, 120];

export function nextRetryDelay(attempt: number): number | null {
  return attempt < RETRY_DELAYS_SECONDS.length ? RETRY_DELAYS_SECONDS[attempt] : null;
}
