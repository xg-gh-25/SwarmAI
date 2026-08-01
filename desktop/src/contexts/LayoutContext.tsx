/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, useCallback, useMemo, ReactNode } from 'react';

// Modal types that can be opened from the left sidebar
// Skills and MCP modals removed — now integrated as Settings tabs (2026-03-26)
export type ModalType = 'settings' | 'file-editor' | 'workspace-settings' | 'eval';

// Workspace scope - 'all' for all workspaces or a specific workspace ID
export type WorkspaceScope = 'all' | string;

// Session metadata displayed in the TopBar context bar
export interface ActiveSessionMeta {
  topic: string;
  contextPct: number | null;
  fileCount: number;
  agentName: string;
}

// ── Session Meta Context (separate to avoid re-rendering layout consumers) ──
interface SessionMetaContextValue {
  activeSessionMeta: ActiveSessionMeta | null;
  setActiveSessionMeta: (meta: ActiveSessionMeta | null) => void;
}

const SessionMetaContext = createContext<SessionMetaContextValue | undefined>(undefined);

/** Read session metadata (TopBar, BottomBar). Isolated from LayoutContext to
 *  prevent high-frequency meta updates from re-rendering the entire layout tree. */
export function useSessionMeta(): SessionMetaContextValue {
  const ctx = useContext(SessionMetaContext);
  if (!ctx) throw new Error('useSessionMeta must be used within a LayoutProvider');
  return ctx;
}

// Layout context value interface
export interface LayoutContextValue {
  // Workspace scope
  selectedWorkspaceScope: WorkspaceScope;
  setSelectedWorkspaceScope: (scope: WorkspaceScope) => void;

  // Workspace scope validation - Requirement 10.2
  // Call this with workspace IDs to validate stored scope on startup
  validateWorkspaceScope: (workspaceIds: string[]) => void;

  // Modal management
  activeModal: ModalType | null;
  openModal: (modal: ModalType) => void;
  closeModal: () => void;

  // Workspace settings modal - workspace ID for WorkspaceSettingsModal
  workspaceSettingsId: string;
  setWorkspaceSettingsId: (id: string) => void;

  // Settings tab deep-link (e.g., sidebar Skills icon → settings with skills tab)
  settingsTab: string | undefined;
  setSettingsTab: (tab: string | undefined) => void;
}

// LocalStorage keys for persistence
const STORAGE_KEYS = {
  LAST_WORKSPACE_SCOPE: 'lastWorkspaceScope',
} as const;

// Create the context
const LayoutContext = createContext<LayoutContextValue | undefined>(undefined);

// Helper functions for localStorage
function getStoredString(key: string, defaultValue: string): string {
  if (typeof window === 'undefined') return defaultValue;
  const stored = localStorage.getItem(key);
  return stored ?? defaultValue;
}

// Provider props
interface LayoutProviderProps {
  children: ReactNode;
}

export function LayoutProvider({ children }: LayoutProviderProps) {
  // Workspace scope state
  const [selectedWorkspaceScope, setSelectedWorkspaceScopeState] = useState<WorkspaceScope>(() =>
    getStoredString(STORAGE_KEYS.LAST_WORKSPACE_SCOPE, 'all') as WorkspaceScope
  );

  // Active modal state (not persisted)
  const [activeModal, setActiveModal] = useState<ModalType | null>(null);

  // Workspace settings modal target ID
  const [workspaceSettingsId, setWorkspaceSettingsId] = useState<string>('');

  // Settings tab deep-link (sidebar → specific settings tab)
  const [settingsTab, setSettingsTab] = useState<string | undefined>(undefined);

  // TopBar session context -- ChatPage writes, TopBar/BottomBar read.
  // Hosted in a SEPARATE context (SessionMetaContext) so high-frequency
  // updates don't re-render the entire layout tree.
  const [activeSessionMeta, setActiveSessionMeta] = useState<ActiveSessionMeta | null>(null);
  const sessionMetaValue = useMemo<SessionMetaContextValue>(() => ({
    activeSessionMeta,
    setActiveSessionMeta,
  }), [activeSessionMeta]);

  // Set workspace scope (not persisted by default, but we store last used)
  const setSelectedWorkspaceScope = useCallback((scope: WorkspaceScope) => {
    setSelectedWorkspaceScopeState(scope);
    localStorage.setItem(STORAGE_KEYS.LAST_WORKSPACE_SCOPE, scope);
  }, []);

  // Modal management
  const openModal = useCallback((modal: ModalType) => {
    setActiveModal(modal);
  }, []);

  const closeModal = useCallback(() => {
    setActiveModal(null);
  }, []);

  // Validate workspace scope on initialization - Requirement 10.2
  // If stored scope is invalid (not 'all' and not a valid workspace ID), reset to 'all'
  const validateWorkspaceScope = useCallback((workspaceIds: string[]) => {
    if (selectedWorkspaceScope === 'all') {
      return; // 'all' is always valid
    }
    // Check if the stored workspace ID exists
    if (!workspaceIds.includes(selectedWorkspaceScope)) {
      // Reset to 'all' if the stored workspace no longer exists
      console.log(`Stored workspace scope '${selectedWorkspaceScope}' is invalid, resetting to 'all'`);
      setSelectedWorkspaceScope('all');
    }
  }, [selectedWorkspaceScope, setSelectedWorkspaceScope]);

  const value: LayoutContextValue = useMemo(() => ({
    selectedWorkspaceScope,
    setSelectedWorkspaceScope,
    validateWorkspaceScope,
    activeModal,
    openModal,
    closeModal,
    workspaceSettingsId,
    setWorkspaceSettingsId,
    settingsTab,
    setSettingsTab,
  }), [
    selectedWorkspaceScope,
    setSelectedWorkspaceScope,
    validateWorkspaceScope,
    activeModal,
    openModal,
    closeModal,
    workspaceSettingsId,
    setWorkspaceSettingsId,
    settingsTab,
    setSettingsTab,
  ]);

  return (
    <LayoutContext.Provider value={value}>
      <SessionMetaContext.Provider value={sessionMetaValue}>
        {children}
      </SessionMetaContext.Provider>
    </LayoutContext.Provider>
  );
}

// Custom hook to use the layout context
export function useLayout() {
  const context = useContext(LayoutContext);
  if (context === undefined) {
    throw new Error('useLayout must be used within a LayoutProvider');
  }
  return context;
}

// Export constants for use in other components
export const LAYOUT_CONSTANTS = {
  // A10 redesign (run_1aab916c): the left nav is a 150px row-card column
  // (was a 44px icon rail).
  LEFT_SIDEBAR_WIDTH: 150,
  // Card-detail panel (A11, run_a4ea7a83): the fullscreen overlay floats INSIDE
  // the chat area, so it must clear the chat content's top edge. That edge is a
  // constant: TopBar (h-10 = 40px) + ChatHeader/SessionTabBar (h-10 = 40px) = 80.
  // The tab bar is fixed-height (overflow-x-auto, never wraps), so this does NOT
  // vary with tab count. If either bar's height changes, update this in lockstep.
  CHAT_CONTENT_TOP: 80,
  STORAGE_KEYS,
} as const;
