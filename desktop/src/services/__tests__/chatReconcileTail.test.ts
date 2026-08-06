/**
 * Regression tests for chatService.getSessionMessagesReconcileTail (#4 — reconcile
 * tail-increment fetch, run_acaa53ea).
 *
 * The turn-end reconcile sites fetch only the newest RECONCILE_TAIL rows instead
 * of the full history (O(N)→constant per turn). The SUBTLE hazard the fix must
 * NOT introduce: the per-session ETag cache slot (_messageEtags) is keyed by the
 * backend ETag "session:count", which is LIMIT-AGNOSTIC (chat.py:1158-1163 →
 * 304 on count-match regardless of ?limit). If a capped reconcile wrote its
 * small slice into that shared slot, a later FULL initial-load would send the
 * same etag, get a 304, and receive the truncated 50-row slice — silently
 * cutting visible history on tab-switch AND hiding "Load earlier messages".
 *
 * These tests lock the two properties that make the fix safe:
 *   1. The reconcile tail fetch NEVER sends If-None-Match and NEVER writes cache.
 *   2. The poisoning chain is broken: reconcile-then-initial-load returns the
 *      FULL list, not the reconcile slice (the cross-tab truncation the fix
 *      exists to avoid, and which a persist-lag-only test would miss).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { chatService } from '../chat';

/** Build N minimal backend message rows (snake_case wire shape). */
function rows(n: number, startId = 1): Record<string, unknown>[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m${startId + i}`,
    session_id: 'sess-heavy',
    role: i % 2 === 0 ? 'user' : 'assistant',
    content: [{ type: 'text', text: `msg ${startId + i}` }],
    created_at: `2026-08-06T00:00:${String(startId + i).padStart(2, '0')}Z`,
  }));
}

describe('chatService.getSessionMessagesReconcileTail (#4 cache isolation)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Clear the module-level per-session cache between tests.
    chatService.invalidateMessageCache('sess-heavy');
  });

  it('requests ?limit=<tail> and returns the capped rows', async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: rows(50, 1250), headers: {} });

    const msgs = await chatService.getSessionMessagesReconcileTail('sess-heavy', 50);

    expect(api.get).toHaveBeenCalledTimes(1);
    const [url] = vi.mocked(api.get).mock.calls[0];
    expect(url).toBe('/chat/sessions/sess-heavy/messages?limit=50');
    expect(msgs).toHaveLength(50);
  });

  it('NEVER sends If-None-Match — so the backend cannot 304 it to a stale slice', async () => {
    // Pre-seed the cache as if a full initial-load had run (etag present).
    vi.mocked(api.get).mockResolvedValueOnce({
      status: 200,
      data: rows(200, 1),
      headers: { etag: '"sess-heavy:1299"' },
    });
    await chatService.getSessionMessagesPaginated('sess-heavy', 200); // seeds cache

    vi.mocked(api.get).mockResolvedValueOnce({ status: 200, data: rows(50, 1250), headers: {} });
    await chatService.getSessionMessagesReconcileTail('sess-heavy', 50);

    // The reconcile call is the 2nd api.get; assert it carried NO If-None-Match.
    const secondCallConfig = vi.mocked(api.get).mock.calls[1][1] as { headers?: Record<string, string> } | undefined;
    const sentHeaders = secondCallConfig?.headers ?? {};
    expect(sentHeaders['If-None-Match']).toBeUndefined();
  });

  it('does NOT poison the shared cache: reconcile-then-initial-load returns FULL history (the cross-tab truncation guard)', async () => {
    // 1. Send invalidates the cache (as streamChat does).
    chatService.invalidateMessageCache('sess-heavy');

    // 2. Turn-end reconcile fetches the 50-row tail. If this wrote the cache,
    //    the slot would become { etag "sess-heavy:1299", messages: 50 rows }.
    vi.mocked(api.get).mockResolvedValueOnce({ status: 200, data: rows(50, 1250), headers: { etag: '"sess-heavy:1299"' } });
    const tail = await chatService.getSessionMessagesReconcileTail('sess-heavy', 50);
    expect(tail).toHaveLength(50);

    // 3. A later initial-load (tab-switch) does a full 200 fetch. Because the
    //    reconcile did NOT write the cache, there is no poisoned etag to send,
    //    so this is a fresh 200 with the full history — NOT a 304 → 50 rows.
    vi.mocked(api.get).mockResolvedValueOnce({ status: 200, data: rows(200, 1100), headers: { etag: '"sess-heavy:1299"' } });
    const initial = await chatService.getSessionMessagesPaginated('sess-heavy', 200);

    // THE ASSERTION THAT WOULD FAIL under the poisoning bug: full history, not 50.
    expect(initial).toHaveLength(200);

    // And the initial-load fetch must not have been short-circuited to a 304:
    // its request carried no stale reconcile-written etag.
    const initialCallConfig = vi.mocked(api.get).mock.calls[1][1] as { headers?: Record<string, string> } | undefined;
    const initialHeaders = initialCallConfig?.headers ?? {};
    expect(initialHeaders['If-None-Match']).toBeUndefined();
  });
});
