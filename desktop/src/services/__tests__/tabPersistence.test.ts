/**
 * tabPersistenceService.save sends one write at a time, the newest state next.
 *
 * Question: can an earlier, slower write of open_tabs.json land after a later one (two
 * closes in quick succession bringing the first closed tab back)?
 */
import { describe, it, expect, vi } from 'vitest';
import { tabPersistenceService, type OpenTabsFileData } from '../tabPersistence';
import api from '../api';

vi.mock('../api', () => ({ default: { put: vi.fn(), get: vi.fn() } }));

const state = (...ids: string[]): OpenTabsFileData =>
  ({ tabs: ids.map((id) => ({ id, title: id, agentId: 'x', isNew: false })), activeTabId: ids[0] ?? null });

describe('tabPersistenceService.save', () => {
  it('waits for the write in flight, then sends only the newest state', async () => {
    const releases: Array<() => void> = [];
    vi.mocked(api.put).mockImplementation(() => new Promise((resolve) => { releases.push(() => resolve({} as never)); }));
    const first = tabPersistenceService.save(state('a', 'b', 'c'));
    tabPersistenceService.save(state('a', 'b'));
    const last = tabPersistenceService.save(state('a'));
    expect(vi.mocked(api.put).mock.calls).toHaveLength(1);   // one write at a time
    releases[0]();
    await vi.waitFor(() => expect(vi.mocked(api.put).mock.calls).toHaveLength(2));
    expect(vi.mocked(api.put).mock.calls[1][1]).toEqual(state('a'));   // the middle state is skipped
    releases[1]();
    await Promise.all([first, last]);
    expect(vi.mocked(api.put).mock.calls).toHaveLength(2);
  });

  it('a failed write does not stop the next one', async () => {
    vi.mocked(api.put).mockReset();
    vi.mocked(api.put).mockRejectedValueOnce(new Error('down')).mockResolvedValue({} as never);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    await tabPersistenceService.save(state('a'));
    await tabPersistenceService.save(state('b'));
    expect(vi.mocked(api.put).mock.calls.map((c) => c[1])).toEqual([state('a'), state('b')]);
    warn.mockRestore();
  });
});
