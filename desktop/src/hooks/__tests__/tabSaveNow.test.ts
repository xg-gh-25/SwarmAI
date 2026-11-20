/**
 * open_tabs.json is written as soon as the tab set changes (useUnifiedTabState's save
 * effect), not after a delay.
 *
 * Question: does a closed tab stay closed when the app quits or reloads right after the
 * close? A 500 ms debounce lost the close, so the tab came back on the next start.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUnifiedTabState } from '../useUnifiedTabState';
import { normalTabs } from '../../pages/chat/swarmOut';
import { tabPersistenceService } from '../../services/tabPersistence';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { max_tabs: 5, chat_max: 4, memory_pressure: 'ok' } }) },
}));
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: { load: vi.fn(), save: vi.fn().mockResolvedValue(undefined) },
}));

const AGENT = 'test-agent';
const saved = (id: string) => ({ id, title: `t-${id}`, agentId: AGENT, isNew: false, sessionId: `s-${id}` });
const lastSavedIds = () => {
  const calls = vi.mocked(tabPersistenceService.save).mock.calls;
  return calls[calls.length - 1][0].tabs.map((t) => t.id);
};

beforeEach(() => {
  vi.mocked(tabPersistenceService.save).mockClear();
  vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved('a'), saved('b')], activeTabId: 'a' });
});

describe('open tabs are saved at once', () => {
  it('a close is written in the same tick, with no timer to wait for', async () => {
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useUnifiedTabState(AGENT));
      await act(async () => { await result.current.restoreFromFile(); });
      act(() => { result.current.closeTab('b'); });
      expect(lastSavedIds()).not.toContain('b');
      expect(lastSavedIds()).toContain('a');
      expect(normalTabs(result.current.openTabs).map((t) => t.id)).toEqual(['a']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a render that changes nothing persisted writes nothing', async () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    const before = vi.mocked(tabPersistenceService.save).mock.calls.length;
    act(() => { result.current.updateTabState('a', { isStreaming: true }); });
    act(() => { result.current.updateTabState('a', { isStreaming: false }); });
    expect(vi.mocked(tabPersistenceService.save).mock.calls.length).toBe(before);
  });
});
