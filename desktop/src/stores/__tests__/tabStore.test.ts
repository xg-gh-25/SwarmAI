/**
 * Unit and property tests for the Zustand tab store.
 *
 * Property 16: Tab state serialization round-trip
 * Feature: multi-session-rearchitecture
 *
 * @module tabStore.test
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useTabStore } from '../tabStore';

// Reset store between tests
beforeEach(() => {
  useTabStore.setState({ tabs: {}, activeTabId: null });
});

describe('TabStore CRUD', () => {
  it('createTab returns a valid tab ID', () => {
    const tabId = useTabStore.getState().createTab('default');
    expect(tabId).toBeTruthy();
    expect(tabId.startsWith('tab-')).toBe(true);
  });

  it('createTab sets the new tab as active', () => {
    const tabId = useTabStore.getState().createTab('default');
    expect(useTabStore.getState().activeTabId).toBe(tabId);
  });

  it('createTab initializes tab in idle state', () => {
    const tabId = useTabStore.getState().createTab('default');
    const tab = useTabStore.getState().tabs[tabId];
    expect(tab).toBeDefined();
    expect(tab.status).toBe('idle');
    expect(tab.isStreaming).toBe(false);
    expect(tab.messages).toEqual([]);
    expect(tab.agentId).toBe('default');
  });

  it('closeTab removes the tab', () => {
    const tabId = useTabStore.getState().createTab('default');
    useTabStore.getState().closeTab(tabId);
    expect(useTabStore.getState().tabs[tabId]).toBeUndefined();
  });

  it('closeTab selects another tab when active is closed', () => {
    const tab1 = useTabStore.getState().createTab('default');
    const tab2 = useTabStore.getState().createTab('default');
    expect(useTabStore.getState().activeTabId).toBe(tab2);

    useTabStore.getState().closeTab(tab2);
    expect(useTabStore.getState().activeTabId).toBe(tab1);
  });

  it('setActiveTab updates activeTabId', () => {
    const tab1 = useTabStore.getState().createTab('default');
    const tab2 = useTabStore.getState().createTab('default');
    useTabStore.getState().setActiveTab(tab1);
    expect(useTabStore.getState().activeTabId).toBe(tab1);
  });
});

describe('TabStore state updates', () => {
  it('setStreaming updates streaming and status', () => {
    const tabId = useTabStore.getState().createTab('default');
    useTabStore.getState().setStreaming(tabId, true);
    const tab = useTabStore.getState().tabs[tabId];
    expect(tab.isStreaming).toBe(true);
    expect(tab.status).toBe('streaming');
  });

  it('setMessages replaces messages and marks loaded', () => {
    const tabId = useTabStore.getState().createTab('default');
    const msgs = [{ id: 'msg-1', role: 'user' as const, content: [{ type: 'text', text: 'hi' }], timestamp: '' }];
    useTabStore.getState().setMessages(tabId, msgs);
    const tab = useTabStore.getState().tabs[tabId];
    expect(tab.messages).toHaveLength(1);
    expect(tab.messagesLoaded).toBe(true);
  });

  it('appendTextDelta appends to last text block', () => {
    const tabId = useTabStore.getState().createTab('default');
    const msgs = [{ id: 'msg-1', role: 'assistant' as const, content: [{ type: 'text', text: 'Hello' }], timestamp: '' }];
    useTabStore.getState().setMessages(tabId, msgs);
    useTabStore.getState().appendTextDelta(tabId, 'msg-1', ' world');
    const tab = useTabStore.getState().tabs[tabId];
    expect(tab.messages[0].content[0].text).toBe('Hello world');
  });

  it('setContextWarning updates warning', () => {
    const tabId = useTabStore.getState().createTab('default');
    const warning = { level: 'warn' as const, pct: 75, tokensEst: 150000, message: 'test' };
    useTabStore.getState().setContextWarning(tabId, warning);
    expect(useTabStore.getState().tabs[tabId].contextWarning).toEqual(warning);
  });
});

describe('Property 16: Tab state serialization round-trip', () => {
  // Feature: multi-session-rearchitecture, Property 16

  it('getPersistedTabs + restoreTabs preserves tab metadata', () => {
    // Create tabs with different data
    const tab1 = useTabStore.getState().createTab('agent-1');
    const tab2 = useTabStore.getState().createTab('agent-2');
    useTabStore.getState().setSessionId(tab1, 'session-aaa');
    useTabStore.getState().setSessionId(tab2, 'session-bbb');

    // Persist
    const persisted = useTabStore.getState().getPersistedTabs();
    expect(persisted).toHaveLength(2);

    // Clear and restore
    useTabStore.setState({ tabs: {}, activeTabId: null });
    useTabStore.getState().restoreTabs(persisted, tab1);

    // Verify round-trip
    const restored = useTabStore.getState();
    expect(Object.keys(restored.tabs)).toHaveLength(2);
    expect(restored.tabs[tab1].sessionId).toBe('session-aaa');
    expect(restored.tabs[tab2].sessionId).toBe('session-bbb');
    expect(restored.tabs[tab1].agentId).toBe('agent-1');
    expect(restored.tabs[tab2].agentId).toBe('agent-2');
    expect(restored.activeTabId).toBe(tab1);
  });

  it('restoreTabs with empty array produces empty store', () => {
    useTabStore.getState().restoreTabs([]);
    expect(Object.keys(useTabStore.getState().tabs)).toHaveLength(0);
    expect(useTabStore.getState().activeTabId).toBeNull();
  });

  it('messages are NOT persisted (lazy loading)', () => {
    const tabId = useTabStore.getState().createTab('default');
    useTabStore.getState().setMessages(tabId, [
      { id: 'msg-1', role: 'user', content: [{ type: 'text', text: 'hi' }], timestamp: '' },
    ]);

    const persisted = useTabStore.getState().getPersistedTabs();
    // Persisted data should NOT contain messages
    expect((persisted[0] as Record<string, unknown>).messages).toBeUndefined();
  });
});
