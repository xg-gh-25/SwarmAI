/**
 * The Hive tab (pages/chat/swarmOut.ts) in useUnifiedTabState (design
 * docs/design/2026-10-29-swarm-out-hive-session-tab.md §4).
 *
 * Questions: is there never more than one Hive tab, and is services/remoteSession
 * registered with exactly its session (P1) through add, close, cleanup and restore;
 * does it stay out of the user's tab count and of removeInvalidTabs; does closing it
 * go back to Swarm Out without creating a tab; is its title fixed.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUnifiedTabState } from '../useUnifiedTabState';
import { SWARM_OUT_TAB_ID, hiveTabId, isHiveTab, normalTabs } from '../../pages/chat/swarmOut';
import { isRemote, setRemote } from '../../services/remoteSession';
import { tabPersistenceService } from '../../services/tabPersistence';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { max_tabs: 4, chat_max: 3, memory_pressure: 'ok' } }) },
}));
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: { load: vi.fn().mockResolvedValue(null), save: vi.fn().mockResolvedValue(undefined) },
}));

const AGENT = 'test-agent';
const hiveCount = (tabs: { id: string }[]) => tabs.filter((t) => isHiveTab(t.id)).length;

async function renderWithLimits() {
  const hook = renderHook(() => useUnifiedTabState(AGENT));
  await act(async () => { await hook.result.current.fetchMaxTabs(); });
  return hook;
}

beforeEach(() => {
  vi.mocked(tabPersistenceService.load).mockResolvedValue(null);
  setRemote(null);
});

describe('Hive tab — add and close', () => {
  it('adds one Hive tab, not selected, registered as remote; a second add is refused', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    const before = result.current.activeTabId;
    let tab: ReturnType<typeof result.current.addHiveTab>;
    act(() => { tab = result.current.addHiveTab('s1', 'hive-a · run', AGENT); });
    expect(tab!.id).toBe(hiveTabId('s1'));
    expect(tab!.sessionId).toBe('s1');
    expect(result.current.activeTabId).toBe(before);
    expect(isRemote('s1')).toBe(true);
    act(() => { tab = result.current.addHiveTab('s2', 'other', AGENT); });
    expect(tab!).toBeUndefined();
    expect(hiveCount(result.current.openTabs)).toBe(1);
    expect(isRemote('s2')).toBe(false);
    expect(isRemote('s1')).toBe(true);
  });

  it('refuses a session id the proxy would not accept: no tab, nothing registered', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    for (const bad of ['run.1', 'a/b', '', 'x'.repeat(129)]) {
      let tab: ReturnType<typeof result.current.addHiveTab>;
      act(() => { tab = result.current.addHiveTab(bad, 't', AGENT); });
      expect(tab!).toBeUndefined();
      expect(isRemote(bad)).toBe(false);
    }
    expect(hiveCount(result.current.openTabs)).toBe(0);
  });

  it('takes no chat slot: with chat_max 3 the user still opens 2 tabs of their own', async () => {
    const { result } = await renderWithLimits();
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    let added: ReturnType<typeof result.current.addTab>;
    act(() => { added = result.current.addTab(AGENT); });
    expect(added!).toBeDefined();
    expect(normalTabs(result.current.openTabs)).toHaveLength(2);
  });

  it('closing the active Hive tab selects Swarm Out, creates no tab and clears the registration', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    act(() => { result.current.selectTab(hiveTabId('s1')); });
    const userTabs = normalTabs(result.current.openTabs).map((t) => t.id);
    act(() => { result.current.closeTab(hiveTabId('s1')); });
    expect(hiveCount(result.current.openTabs)).toBe(0);
    expect(result.current.activeTabId).toBe(SWARM_OUT_TAB_ID);
    expect(normalTabs(result.current.openTabs).map((t) => t.id)).toEqual(userTabs);
    expect(isRemote('s1')).toBe(false);
  });

  it('closing a background Hive tab leaves the active tab as it was', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    const active = result.current.activeTabId;
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    act(() => { result.current.closeTab(hiveTabId('s1')); });
    expect(result.current.activeTabId).toBe(active);
  });

  it('cleanupTabState of the Hive tab clears the registration too', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    act(() => { result.current.cleanupTabState(hiveTabId('s1')); });
    expect(hiveCount(result.current.openTabs)).toBe(0);
    expect(isRemote('s1')).toBe(false);
  });

  it('after a close a new Hive session can be added and is the one registered', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    act(() => { result.current.closeTab(hiveTabId('s1')); });
    act(() => { result.current.addHiveTab('s2', 't2', AGENT); });
    expect(isRemote('s2')).toBe(true);
    expect(isRemote('s1')).toBe(false);
    expect(hiveCount(result.current.openTabs)).toBe(1);
  });

  it('keeps its title (a first message does not rename it)', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.addHiveTab('s1', 'hive-a · run', AGENT); });
    act(() => { result.current.updateTabTitle(hiveTabId('s1'), 'renamed'); });
    expect(result.current.getTabState(hiveTabId('s1'))?.title).toBe('hive-a · run');
  });

  it('removeInvalidTabs never clears the Hive tab (its session is not in this machine\'s list)', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.addHiveTab('s1', 't', AGENT); });
    act(() => { result.current.removeInvalidTabs(new Set()); });
    expect(result.current.getTabState(hiveTabId('s1'))?.sessionId).toBe('s1');
    expect(isRemote('s1')).toBe(true);
  });
});

describe('Hive tab — restore from open_tabs.json', () => {
  const saved = (id: string, extra: Record<string, unknown> = {}) =>
    ({ id, title: `t-${id}`, agentId: AGENT, isNew: false, sessionId: `s-${id}`, ...extra });

  it('restores a saved Hive tab and registers its session before restore returns', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({
      tabs: [saved('a'), { ...saved(hiveTabId('h1')), sessionId: 'h1' }], activeTabId: 'a',
    });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(hiveCount(result.current.openTabs)).toBe(1);
    expect(result.current.getTabState(hiveTabId('h1'))?.sessionId).toBe('h1');
    expect(isRemote('h1')).toBe(true);
    expect(normalTabs(result.current.openTabs).map((t) => t.id)).toEqual(['a']);
  });

  it('takes no Hive tab whose id is not its own session\'s, and registers nothing', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({
      tabs: [saved('a'), { ...saved(hiveTabId('h1')), sessionId: 'local-victim' }], activeTabId: 'a',
    });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(hiveCount(result.current.openTabs)).toBe(0);
    expect(isRemote('local-victim')).toBe(false);
  });

  it('drops a saved Hive tab whose session id the proxy would not accept', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({
      tabs: [saved('a'), { ...saved(hiveTabId('run.1')), sessionId: 'run.1' }], activeTabId: 'a',
    });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(hiveCount(result.current.openTabs)).toBe(0);
    expect(isRemote('run.1')).toBe(false);
  });

  it('a file without a Hive tab clears an earlier registration', async () => {
    setRemote('stale');
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved('a')], activeTabId: 'a' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(isRemote('stale')).toBe(false);
  });

  it('of two saved Hive tabs only one is restored', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({
      tabs: [saved('a'), { ...saved(hiveTabId('h1')), sessionId: 'h1' }, { ...saved(hiveTabId('h2')), sessionId: 'h2' }],
      activeTabId: 'a',
    });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(hiveCount(result.current.openTabs)).toBe(1);
  });
});
