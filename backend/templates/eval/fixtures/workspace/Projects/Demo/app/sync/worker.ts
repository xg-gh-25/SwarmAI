// Sends queued items; on failure asks backoff.ts when to try again.
import { nextRetryDelay } from './backoff';
import type { SyncItem } from './queue';

export async function sendWithRetry(item: SyncItem, send: (i: SyncItem) => Promise<void>): Promise<boolean> {
  for (let attempt = 0; ; attempt++) {
    try {
      await send(item);
      return true;
    } catch {
      const delay = nextRetryDelay(attempt);
      if (delay === null) return false;           // given up
      await new Promise((r) => setTimeout(r, delay * 1000));
    }
  }
}
