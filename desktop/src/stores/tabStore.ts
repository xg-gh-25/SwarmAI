/**
 * Zustand-based single source of truth for all tab state.
 *
 * Replaces the dual-state pattern (tabMapRef + useState) that caused
 * tab-switch content loss bugs (4 reports, COE'd). Stream handlers
 * write directly to this store; React components subscribe via
 * selectors. No manual sync on tab switch.
 *
 * Design reference:
 *   .kiro/specs/multi-session-rearchitecture/design.md §Frontend
 *
 * @module tabStore
 */

import { create } from 'zustand';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Content block in a message (text, tool_use, tool_result, etc.) */
export interface ContentBlock {
  type: string;
  text?: string;
  [key: string]: unknown;
}

/** A single chat message */
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: ContentBlock[];
  timestamp: string;
  model?: string;
  isError?: boolean;
  [key: string]: unknown;
}

/** Context window warning */
export interface ContextWarning {
  level: 'ok' | 'warn' | 'critical';
  pct: number;
  tokensEst: number;
  message: string;
}

/** Pending question from ask_user_question */
export interface PendingQuestion {
  toolUseId: string;
  questions: unknown[];
}

/** Tab status for UI indicators */
export type TabStatus =
  | 'idle'
  | 'streaming'
  | 'error'
  | 'waiting_input'
  | 'permission_needed'
  | 'complete_unread'
  | 'queued';

/** Per-tab runtime state */
export interface TabState {
  tabId: string;
  sessionId: string;
  agentId: string;
  title: string;
  createdAt: string;

  // Runtime state (not persisted)
  messages: Message[];
  isStreaming: boolean;
  isPending: boolean;
  status: TabStatus;
  contextWarning: ContextWarning | null;
  pendingQuestion: PendingQuestion | null;
  pendingPermissionRequestId: string | null;
  messagesLoaded: boolean;
  lastUsed: number;
}

/** Persisted tab metadata (saved to open_tabs.json) */
export interface PersistedTabData {
  tabId: string;
  sessionId: string;
  agentId: string;
  title: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Store interface
// ---------------------------------------------------------------------------

export interface TabStore {
  tabs: Record<string, TabState>;
  activeTabId: string | null;

  // Tab CRUD
  createTab: (agentId: string) => string;
  closeTab: (tabId: string) => void;
  setActiveTab: (tabId: string) => void;

  // Per-tab state updates (called by stream handler)
  setStreaming: (tabId: string, streaming: boolean) => void;
  setStatus: (tabId: string, status: TabStatus) => void;
  setSessionId: (tabId: string, sessionId: string) => void;
  setMessages: (tabId: string, messages: Message[]) => void;
  appendMessage: (tabId: string, message: Message) => void;
  updateMessage: (tabId: string, messageId: string, update: Partial<Message>) => void;
  appendTextDelta: (tabId: string, messageId: string, text: string) => void;
  setContextWarning: (tabId: string, warning: ContextWarning | null) => void;
  setPendingQuestion: (tabId: string, pq: PendingQuestion | null) => void;
  setPendingPermissionRequestId: (tabId: string, requestId: string | null) => void;
  setMessagesLoaded: (tabId: string, loaded: boolean) => void;

  // Persistence
  getPersistedTabs: () => PersistedTabData[];
  restoreTabs: (tabs: PersistedTabData[], activeTabId?: string) => void;

  // Queries
  getTab: (tabId: string) => TabState | undefined;
  getActiveTab: () => TabState | undefined;
}

// ---------------------------------------------------------------------------
// Helper: generate tab ID
// ---------------------------------------------------------------------------

let _tabCounter = 0;
function generateTabId(): string {
  _tabCounter += 1;
  return `tab-${Date.now()}-${_tabCounter}`;
}

function createEmptyTab(tabId: string, agentId: string): TabState {
  return {
    tabId,
    sessionId: '',
    agentId,
    title: 'New Chat',
    createdAt: new Date().toISOString(),
    messages: [],
    isStreaming: false,
    isPending: false,
    status: 'idle',
    contextWarning: null,
    pendingQuestion: null,
    pendingPermissionRequestId: null,
    messagesLoaded: false,
    lastUsed: Date.now(),
  };
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useTabStore = create<TabStore>((set, get) => ({
  tabs: {},
  activeTabId: null,

  // ── Tab CRUD ───────────────────────────────────────────────────

  createTab: (agentId: string) => {
    const tabId = generateTabId();
    const tab = createEmptyTab(tabId, agentId);
    set((state) => ({
      tabs: { ...state.tabs, [tabId]: tab },
      activeTabId: tabId,
    }));
    return tabId;
  },

  closeTab: (tabId: string) => {
    set((state) => {
      const { [tabId]: _, ...remaining } = state.tabs;
      const tabIds = Object.keys(remaining);
      const newActiveId =
        state.activeTabId === tabId
          ? tabIds[tabIds.length - 1] || null
          : state.activeTabId;
      return { tabs: remaining, activeTabId: newActiveId };
    });
  },

  setActiveTab: (tabId: string) => {
    const tab = get().tabs[tabId];
    if (tab) {
      set((state) => ({
        activeTabId: tabId,
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, lastUsed: Date.now() },
        },
      }));
    }
  },

  // ── Per-tab state updates ──────────────────────────────────────

  setStreaming: (tabId, streaming) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: {
            ...tab,
            isStreaming: streaming,
            status: streaming ? 'streaming' : 'idle',
            lastUsed: Date.now(),
          },
        },
      };
    });
  },

  setStatus: (tabId, status) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, status } },
      };
    });
  },

  setSessionId: (tabId, sessionId) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, sessionId } },
      };
    });
  },

  setMessages: (tabId, messages) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, messages, messagesLoaded: true },
        },
      };
    });
  },

  appendMessage: (tabId, message) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, messages: [...tab.messages, message] },
        },
      };
    });
  },

  updateMessage: (tabId, messageId, update) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      const messages = tab.messages.map((msg) =>
        msg.id === messageId ? { ...msg, ...update } : msg,
      );
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, messages } },
      };
    });
  },

  appendTextDelta: (tabId, messageId, text) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      const messages = tab.messages.map((msg) => {
        if (msg.id !== messageId) return msg;
        const content = [...msg.content];
        const lastBlock = content[content.length - 1];
        if (lastBlock && lastBlock.type === 'text') {
          content[content.length - 1] = {
            ...lastBlock,
            text: (lastBlock.text || '') + text,
          };
        } else {
          content.push({ type: 'text', text });
        }
        return { ...msg, content };
      });
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, messages } },
      };
    });
  },

  setContextWarning: (tabId, warning) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, contextWarning: warning },
        },
      };
    });
  },

  setPendingQuestion: (tabId, pq) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: { ...state.tabs, [tabId]: { ...tab, pendingQuestion: pq } },
      };
    });
  },

  setPendingPermissionRequestId: (tabId, requestId) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, pendingPermissionRequestId: requestId },
        },
      };
    });
  },

  setMessagesLoaded: (tabId, loaded) => {
    set((state) => {
      const tab = state.tabs[tabId];
      if (!tab) return state;
      return {
        tabs: {
          ...state.tabs,
          [tabId]: { ...tab, messagesLoaded: loaded },
        },
      };
    });
  },

  // ── Persistence ────────────────────────────────────────────────

  getPersistedTabs: () => {
    const { tabs } = get();
    return Object.values(tabs).map((tab) => ({
      tabId: tab.tabId,
      sessionId: tab.sessionId,
      agentId: tab.agentId,
      title: tab.title,
      createdAt: tab.createdAt,
    }));
  },

  restoreTabs: (persistedTabs, activeTabId) => {
    const tabs: Record<string, TabState> = {};
    for (const pt of persistedTabs) {
      tabs[pt.tabId] = {
        ...createEmptyTab(pt.tabId, pt.agentId),
        sessionId: pt.sessionId,
        title: pt.title,
        createdAt: pt.createdAt,
      };
    }
    set({
      tabs,
      activeTabId: activeTabId || Object.keys(tabs)[0] || null,
    });
  },

  // ── Queries ────────────────────────────────────────────────────

  getTab: (tabId) => get().tabs[tabId],
  getActiveTab: () => {
    const { tabs, activeTabId } = get();
    return activeTabId ? tabs[activeTabId] : undefined;
  },
}));
