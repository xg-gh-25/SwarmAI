import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react';

// Modal types that can be opened from the left sidebar
export type ModalType = 'workspaces' | 'swarmcore' | 'skills' | 'mcp' | 'agents' | 'settings' | 'file-editor' | 'workspace-settings';

// Workspace scope - 'all' for all workspaces or a specific workspace ID
export type WorkspaceScope = 'all' | string;

// Layout context value interface
export interface LayoutContextValue {
  // Workspace Explorer state
  workspaceExplorerCollapsed: boolean;
  setWorkspaceExplorerCollapsed: (collapsed: boolean) => void;
  workspaceExplorerWidth: number;
  setWorkspaceExplorerWidth: (width: number) => void;
  
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
  
  // Responsive state
  isNarrowViewport: boolean;
}

// LocalStorage keys for persistence
const STORAGE_KEYS = {
  WORKSPACE_EXPLORER_COLLAPSED: 'workspaceExplorerCollapsed',
  WORKSPACE_EXPLORER_WIDTH: 'workspaceExplorerWidth',
  LAST_WORKSPACE_SCOPE: 'lastWorkspaceScope',
} as const;

// Default values
const DEFAULT_WORKSPACE_EXPLORER_WIDTH = 280;
const MIN_WORKSPACE_EXPLORER_WIDTH = 200;
const MAX_WORKSPACE_EXPLORER_WIDTH = 500;

// Create the context
const LayoutContext = createContext<LayoutContextValue | undefined>(undefined);

// Helper functions for localStorage
function getStoredBoolean(key: string, defaultValue: boolean): boolean {
  if (typeof window === 'undefined') return defaultValue;
  const stored = localStorage.getItem(key);
  if (stored === null) return defaultValue;
  return stored === 'true';
}

function getStoredNumber(key: string, defaultValue: number, min?: number, max?: number): number {
  if (typeof window === 'undefined') return defaultValue;
  const stored = localStorage.getItem(key);
  if (stored === null) return defaultValue;
  const parsed = parseInt(stored, 10);
  if (isNaN(parsed)) return defaultValue;
  // Apply constraints if provided
  let value = parsed;
  if (min !== undefined) value = Math.max(min, value);
  if (max !== undefined) value = Math.min(max, value);
  return value;
}

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
  // Workspace Explorer collapsed state with localStorage persistence
  const [workspaceExplorerCollapsed, setWorkspaceExplorerCollapsedState] = useState<boolean>(() =>
    getStoredBoolean(STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED, false)
  );

  // Workspace Explorer width with localStorage persistence
  const [workspaceExplorerWidth, setWorkspaceExplorerWidthState] = useState<number>(() =>
    getStoredNumber(
      STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH,
      DEFAULT_WORKSPACE_EXPLORER_WIDTH,
      MIN_WORKSPACE_EXPLORER_WIDTH,
      MAX_WORKSPACE_EXPLORER_WIDTH
    )
  );

  // Workspace scope state
  const [selectedWorkspaceScope, setSelectedWorkspaceScopeState] = useState<WorkspaceScope>(() =>
    getStoredString(STORAGE_KEYS.LAST_WORKSPACE_SCOPE, 'all') as WorkspaceScope
  );

  // Active modal state (not persisted)
  const [activeModal, setActiveModal] = useState<ModalType | null>(null);

  // Workspace settings modal target ID
  const [workspaceSettingsId, setWorkspaceSettingsId] = useState<string>('');

  // Persist collapsed state to localStorage
  const setWorkspaceExplorerCollapsed = useCallback((collapsed: boolean) => {
    setWorkspaceExplorerCollapsedState(collapsed);
    localStorage.setItem(STORAGE_KEYS.WORKSPACE_EXPLORER_COLLAPSED, String(collapsed));
  }, []);

  // Persist width to localStorage with constraints
  const setWorkspaceExplorerWidth = useCallback((width: number) => {
    // Clamp width to min/max constraints
    const clampedWidth = Math.max(
      MIN_WORKSPACE_EXPLORER_WIDTH,
      Math.min(MAX_WORKSPACE_EXPLORER_WIDTH, width)
    );
    setWorkspaceExplorerWidthState(clampedWidth);
    localStorage.setItem(STORAGE_KEYS.WORKSPACE_EXPLORER_WIDTH, String(clampedWidth));
  }, []);

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

  // Track if we're in a narrow viewport (below 768px)
  const [isNarrowViewport, setIsNarrowViewport] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.innerWidth < 768;
  });

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

  // Export validateWorkspaceScope for use by WorkspaceExplorer
  // This allows validation when workspaces are loaded

  // Handle responsive auto-collapse on window resize
  useEffect(() => {
    const handleResize = () => {
      const isNarrow = window.innerWidth < 768;
      setIsNarrowViewport(isNarrow);
      
      // Auto-collapse when screen width falls below 768px (Requirement 1.8, 11.1)
      if (isNarrow && !workspaceExplorerCollapsed) {
        setWorkspaceExplorerCollapsed(true);
      }
    };

    window.addEventListener('resize', handleResize);
    // Check on mount
    handleResize();

    return () => window.removeEventListener('resize', handleResize);
  }, [workspaceExplorerCollapsed, setWorkspaceExplorerCollapsed]);

  // Enhanced setWorkspaceExplorerCollapsed that respects narrow viewport
  const setWorkspaceExplorerCollapsedWithViewportCheck = useCallback((collapsed: boolean) => {
    // If trying to expand while in narrow viewport, prevent it (keep collapsed)
    if (!collapsed && isNarrowViewport) {
      // Don't allow expansion when viewport is narrow
      return;
    }
    setWorkspaceExplorerCollapsed(collapsed);
  }, [isNarrowViewport, setWorkspaceExplorerCollapsed]);

  const value: LayoutContextValue = {
    workspaceExplorerCollapsed,
    setWorkspaceExplorerCollapsed: setWorkspaceExplorerCollapsedWithViewportCheck,
    workspaceExplorerWidth,
    setWorkspaceExplorerWidth,
    selectedWorkspaceScope,
    setSelectedWorkspaceScope,
    validateWorkspaceScope,
    activeModal,
    openModal,
    closeModal,
    workspaceSettingsId,
    setWorkspaceSettingsId,
    isNarrowViewport,
  };

  return (
    <LayoutContext.Provider value={value}>
      {children}
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
  DEFAULT_WORKSPACE_EXPLORER_WIDTH,
  MIN_WORKSPACE_EXPLORER_WIDTH,
  MAX_WORKSPACE_EXPLORER_WIDTH,
  NARROW_VIEWPORT_BREAKPOINT: 768,
  LEFT_SIDEBAR_WIDTH: 56,
  STORAGE_KEYS,
} as const;
