/**
 * Layer-4 wire contract, frontend half: the shared fixture payloads (asserted
 * against the REAL backend output by backend/tests/test_context_usage_wire_contract.py)
 * driven through the REAL apply path — the SSE stream handler and the GET
 * refresh — with only the HTTP transport mocked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import contract from './fixtures/context-usage-snapshots.json';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import api from '../services/api';
import { createMockDeps, initTestTab, makeMessage, resetTestState, testTabMap } from './helpers/streamingTestUtils';

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

function setup(tabId: string, sessionId: string) {
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  act(() => {
    initTestTab(tabId);
    testTabMap.get(tabId)!.sessionId = sessionId;
    result.current.setIsStreaming(true);
    messageStoreRegistry.getOrCreate(tabId, { sessionId })
      .replace([makeMessage({ id: `m-${tabId}`, role: 'assistant', content: [] })]);
  });
  return { result, handler: result.current.createStreamHandler(`m-${tabId}`, tabId) };
}

describe('context-usage wire contract (frontend half)', () => {
  beforeEach(() => { resetTestState(); vi.restoreAllMocks(); });

  it('SSE: live → post-compact → none, exactly as the backend serializes them', () => {
    const { result, handler } = setup('w1', 's1');
    act(() => { handler(contract.live_critical as never); });
    expect(result.current.contextWarning).toMatchObject({ level: 'critical', pct: 90, tokensEst: 900000 });
    act(() => { handler(contract.post_compact as never); });
    expect(result.current.contextWarning).toMatchObject({ level: 'ok', pct: 4, tokensEst: 41000 });
    act(() => { handler(contract.none as never); });
    expect(result.current.contextWarning).toBeNull();
  });

  it('GET: a transcript snapshot fills the ring after restore', async () => {
    const { result } = setup('w2', 's2');
    vi.spyOn(api, 'get').mockResolvedValue({ data: contract.transcript } as never);
    await act(async () => { await result.current.refreshContextUsage('w2'); });
    expect(result.current.contextWarning).toMatchObject({ level: 'ok', pct: 27, tokensEst: 270000 });
  });
});
