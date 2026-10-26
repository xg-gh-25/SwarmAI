/**
 * The pinned Swarm Out tab (pages/chat/swarmOut.ts) in useUnifiedTabState.
 *
 * Questions these answer: is there always exactly one Swarm Out tab next to at least
 * one tab of the user's own; can it be closed, renamed or squeezed out; does it take
 * one chat slot and nothing more; does restoring open_tabs.json (old files without it,
 * files with it) keep all of that; and does the "user already started a conversation"
 * restore guard still hold now that two tabs exist at start.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUnifiedTabState, MAX_TABS_HARD_CEILING } from '../useUnifiedTabState';
import { SWARM_OUT_TAB_ID, SWARM_OUT_TITLE, isSwarmOutTab, normalTabs } from '../../pages/chat/swarmOut';
import { tabPersistenceService } from '../../services/tabPersistence';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { max_tabs: 4, chat_max: 3, memory_pressure: 'ok' } }) },
}));
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: { load: vi.fn().mockResolvedValue(null), save: vi.fn().mockResolvedValue(undefined) },
}));

const AGENT = 'test-agent';
const swarmOutCount = (tabs: { id: string }[]) => tabs.filter((t) => isSwarmOutTab(t.id)).length;

async function renderWithLimits() {
  const hook = renderHook(() => useUnifiedTabState(AGENT));
  await act(async () => { await hook.result.current.fetchMaxTabs(); });
  return hook;
}

beforeEach(() => {
  vi.mocked(tabPersistenceService.load).mockResolvedValue(null);
});

describe('Swarm Out tab — lifecycle', () => {
  it('starts with exactly one Swarm Out tab and one tab of the user, the user tab active', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    expect(swarmOutCount(result.current.openTabs)).toBe(1);
    expect(normalTabs(result.current.openTabs)).toHaveLength(1);
    expect(result.current.activeTabId).toBe(normalTabs(result.current.openTabs)[0].id);
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.title).toBe(SWARM_OUT_TITLE);
  });

  it('cannot be closed', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.closeTab(SWARM_OUT_TAB_ID); });
    expect(swarmOutCount(result.current.openTabs)).toBe(1);
    expect(normalTabs(result.current.openTabs)).toHaveLength(1);
  });

  it("closing the user's last tab opens a fresh one; Swarm Out stays and is not selected", () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    const only = normalTabs(result.current.openTabs)[0].id;
    act(() => { result.current.closeTab(only); });
    const user = normalTabs(result.current.openTabs);
    expect(user).toHaveLength(1);
    expect(user[0].id).not.toBe(only);
    expect(swarmOutCount(result.current.openTabs)).toBe(1);
    expect(result.current.activeTabId).toBe(user[0].id);
  });

  it("closing the user's last tab while on Swarm Out keeps Swarm Out active", () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    const only = normalTabs(result.current.openTabs)[0].id;
    act(() => { result.current.selectTab(SWARM_OUT_TAB_ID); });
    act(() => { result.current.closeTab(only); });
    expect(normalTabs(result.current.openTabs)).toHaveLength(1);
    expect(result.current.activeTabId).toBe(SWARM_OUT_TAB_ID);
  });

  it('closing the active user tab reselects a user tab, never Swarm Out', async () => {
    const { result } = await renderWithLimits();
    act(() => { result.current.addTab(AGENT); });
    const [first, second] = normalTabs(result.current.openTabs).map((t) => t.id);
    expect(result.current.activeTabId).toBe(second);
    act(() => { result.current.closeTab(second); });
    expect(result.current.activeTabId).toBe(first);
  });

  it('can be selected like any tab', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.selectTab(SWARM_OUT_TAB_ID); });
    expect(result.current.activeTabId).toBe(SWARM_OUT_TAB_ID);
  });

  it('keeps its title', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.updateTabTitle(SWARM_OUT_TAB_ID, 'renamed by first message'); });
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.title).toBe(SWARM_OUT_TITLE);
  });

  it('keeps its title when its session turns out to be gone', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.updateTabSessionId(SWARM_OUT_TAB_ID, 'gone'); });
    act(() => { result.current.removeInvalidTabs(new Set()); });
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.title).toBe(SWARM_OUT_TITLE);
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.sessionId).toBeUndefined();
  });

  it('holds one chat slot of its own: chat_max 3 lets the user open 2 tabs', async () => {
    const { result } = await renderWithLimits();
    let added: ReturnType<typeof result.current.addTab>;
    act(() => { added = result.current.addTab(AGENT); });
    expect(added!).toBeDefined();
    act(() => { added = result.current.addTab(AGENT); });
    expect(added!).toBeUndefined();
    expect(normalTabs(result.current.openTabs)).toHaveLength(2);
  });

  it('falls back to the floor when /max-tabs fails: the user still gets 1 tab and Swarm Out', () => {
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    expect(result.current.maxTabsInfo.chatMax).toBe(2);
    let added: ReturnType<typeof result.current.addTab>;
    act(() => { added = result.current.addTab(AGENT); });
    expect(added!).toBeUndefined();
    expect(normalTabs(result.current.openTabs)).toHaveLength(1);
  });
});

describe('Swarm Out tab — restore from open_tabs.json', () => {
  const saved = (id: string, extra: Record<string, unknown> = {}) =>
    ({ id, title: `t-${id}`, agentId: AGENT, isNew: false, sessionId: `s-${id}`, ...extra });

  it('a file from before Swarm Out gains the pinned tab, user tabs unchanged', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved('a'), saved('b')], activeTabId: 'b' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(normalTabs(result.current.openTabs).map((t) => t.id)).toEqual(['a', 'b']);
    expect(swarmOutCount(result.current.openTabs)).toBe(1);
    expect(result.current.activeTabId).toBe('b');
  });

  it('a file holding Swarm Out restores it once, its session kept, its title pinned', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({
      tabs: [saved('a'), saved(SWARM_OUT_TAB_ID, { title: 'old title' })], activeTabId: SWARM_OUT_TAB_ID,
    });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(swarmOutCount(result.current.openTabs)).toBe(1);
    const so = result.current.getTabState(SWARM_OUT_TAB_ID)!;
    expect(so.sessionId).toBe(`s-${SWARM_OUT_TAB_ID}`);
    expect(so.title).toBe(SWARM_OUT_TITLE);
    expect(result.current.activeTabId).toBe(SWARM_OUT_TAB_ID);
  });

  it('a saved Swarm Out past the user-tab cap keeps its session (it is taken before the cap)', async () => {
    const many = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => saved(id));
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [...many, saved(SWARM_OUT_TAB_ID)], activeTabId: 'a' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.sessionId).toBe(`s-${SWARM_OUT_TAB_ID}`);
    expect(normalTabs(result.current.openTabs).length).toBeLessThanOrEqual(MAX_TABS_HARD_CEILING);
  });

  it('a file holding only Swarm Out still yields a tab of the user', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved(SWARM_OUT_TAB_ID)], activeTabId: 'nope' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    await act(async () => { await result.current.restoreFromFile(); });
    expect(normalTabs(result.current.openTabs)).toHaveLength(1);
    expect(result.current.activeTabId).toBe(normalTabs(result.current.openTabs)[0].id);
  });

  it('is skipped when the user already started a conversation in the Swarm Out tab', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved('a')], activeTabId: 'a' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    act(() => { result.current.updateTabSessionId(SWARM_OUT_TAB_ID, 'started-before-restore'); });
    let restored = true;
    await act(async () => { restored = await result.current.restoreFromFile(); });
    expect(restored).toBe(false);
    expect(result.current.getTabState(SWARM_OUT_TAB_ID)?.sessionId).toBe('started-before-restore');
  });

  it('is skipped when the user already started a conversation in the default tab', async () => {
    vi.mocked(tabPersistenceService.load).mockResolvedValue({ tabs: [saved('a')], activeTabId: 'a' });
    const { result } = renderHook(() => useUnifiedTabState(AGENT));
    const def = normalTabs(result.current.openTabs)[0].id;
    act(() => { result.current.updateTabSessionId(def, 'started-before-restore'); });
    let restored = true;
    await act(async () => { restored = await result.current.restoreFromFile(); });
    expect(restored).toBe(false);
    expect(normalTabs(result.current.openTabs).map((t) => t.id)).toEqual([def]);
  });
});
