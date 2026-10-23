/* eslint-disable react-refresh/only-export-components */
/**
 * TerminalContext — React bridge over the module-level TerminalStore.
 *
 * The store owns PTY lifecycle + the tab registry (survives StrictMode). This
 * context exposes a React-friendly view: `tabs` (re-rendered via store
 * subscription), `activeTabId`, open/close/setActive, and the bottom-panel
 * open/collapsed state — which persists to localStorage (the terminal panel
 * owns its own collapse state; the workspace explorer no longer has one).
 *
 * Design notes:
 *   - Only `panelOpen` is persisted, NOT the tab list (Gate-1 H5 rejected:
 *     PTYs are OS resources that don't survive restart; persisting tabs would
 *     spawn phantom shells on next launch).
 *   - openTerminal reveals the panel (so explorer right-click "open terminal
 *     here" makes the terminal visible without a second action).
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { terminalStore, type OpenTerminalOptions } from '../stores/TerminalStore';
import { terminalLocation } from '../stores/terminalLocation';

const PANEL_OPEN_KEY = 'terminalPanelOpen';

/** Window event the BottomBar (outside this provider) dispatches to toggle the
 *  panel — same decoupling idiom as 'swarm:show-code-graph'. */
export const TERMINAL_TOGGLE_EVENT = 'swarm:toggle-terminal';

/** Window event: reveal the panel at the current location (terminalLocation) —
 *  the BottomBar location chip and the ⌘⇧` switch. TerminalPanel handles it. */
export const TERMINAL_GOTO_EVENT = 'swarm:terminal-goto';

function getStoredBoolean(key: string, def: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? def : v === 'true';
  } catch {
    return def;
  }
}

export interface TerminalContextValue {
  /** Open terminal tabs, in insertion order (re-rendered on store change). */
  tabs: ReturnType<typeof terminalStore.list>;
  activeTabId: string | null;
  panelOpen: boolean;
  /** Open a new terminal; returns its tab id. Reveals the panel + activates it. */
  openTerminal: (opts: OpenTerminalOptions) => string;
  closeTerminal: (id: string) => void;
  /** Replace an (exited) shell with a fresh one at the same location; returns the new id. */
  restartTerminal: (id: string) => string | null;
  setActiveTab: (id: string) => void;
  togglePanel: () => void;
  setPanelOpen: (open: boolean) => void;
}

const TerminalContext = createContext<TerminalContextValue | null>(null);

export function TerminalProvider({ children }: { children: ReactNode }) {
  // Subscribe to the store so tab open/close/status re-renders consumers.
  const tabs = useSyncExternalStore(
    (cb) => terminalStore.subscribe(cb),
    () => terminalStore.listSnapshot(),
  );

  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [panelOpen, setPanelOpenState] = useState<boolean>(() =>
    getStoredBoolean(PANEL_OPEN_KEY, false),
  );

  const setPanelOpen = useCallback((open: boolean) => {
    setPanelOpenState(open);
    try {
      localStorage.setItem(PANEL_OPEN_KEY, String(open));
    } catch {
      /* ignore quota/availability errors */
    }
  }, []);

  const togglePanel = useCallback(() => {
    setPanelOpen(!panelOpen);
  }, [panelOpen, setPanelOpen]);

  // Let the decoupled BottomBar button toggle the panel via a window event.
  useEffect(() => {
    const onToggle = () => togglePanel();
    window.addEventListener(TERMINAL_TOGGLE_EVENT, onToggle);
    return () => window.removeEventListener(TERMINAL_TOGGLE_EVENT, onToggle);
  }, [togglePanel]);

  const openTerminal = useCallback(
    (opts: OpenTerminalOptions): string => {
      const tab = terminalStore.openTerminal(opts);
      setActiveTabId(tab.id);
      setPanelOpen(true); // reveal the panel when a terminal is opened
      return tab.id;
    },
    [setPanelOpen],
  );

  const closeTerminal = useCallback(
    (id: string) => {
      terminalStore.closeTerminal(id);
      // Re-point active to a remaining tab (or null) if we closed the active one.
      setActiveTabId((prev) => {
        if (prev !== id) return prev;
        const remaining = terminalStore.list();
        return remaining.length > 0 ? remaining[remaining.length - 1].id : null;
      });
    },
    [],
  );

  const setActiveTab = useCallback((id: string) => setActiveTabId(id), []);

  const restartTerminal = useCallback(
    (id: string): string | null => {
      const old = terminalStore.get(id);
      if (!old) return null;
      const fresh = terminalStore.openTerminal({ cwd: old.cwd });
      terminalStore.closeTerminal(id);
      setActiveTabId(fresh.id);
      return fresh.id;
    },
    [],
  );

  const value: TerminalContextValue = {
    tabs,
    activeTabId,
    panelOpen,
    openTerminal,
    closeTerminal,
    restartTerminal,
    setActiveTab,
    togglePanel,
    setPanelOpen,
  };

  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export function useTerminal(): TerminalContextValue {
  const ctx = useContext(TerminalContext);
  if (!ctx) throw new Error('useTerminal must be used within a TerminalProvider');
  return ctx;
}

// Global terminal keys the layout mounts: Ctrl/Cmd-` toggles the panel;
// Ctrl/Cmd-Shift-` switches the chat's location SwarmWS ↔ working repo and shows
// the terminal there. Matched on e.code (Shift turns e.key into '~' on US layouts).
export function useTerminalHotkey(togglePanel: () => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const backquote = e.code === 'Backquote' || e.key === '`';
      if (!backquote || !(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      if (e.shiftKey) {
        if (terminalLocation.toggleRepo()) window.dispatchEvent(new CustomEvent(TERMINAL_GOTO_EVENT));
        return;
      }
      togglePanel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePanel]);
}
