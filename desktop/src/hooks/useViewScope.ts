import { useState, useEffect, useCallback, useMemo } from 'react';

/**
 * View scope modes for workspace navigation.
 * - 'global': Aggregates items across all non-archived workspaces
 * - 'scoped': Shows only items for the selected workspace
 */
export type ViewScope = 'global' | 'scoped';

const STORAGE_KEY = 'swarm-view-scope';

/**
 * Read persisted view scope from localStorage.
 * Returns null if no persisted value or invalid.
 */
export function readPersistedScope(): ViewScope | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'global' || stored === 'scoped') {
      return stored;
    }
  } catch {
    // localStorage unavailable (SSR, privacy mode, etc.)
  }
  return null;
}

/**
 * Persist view scope to localStorage.
 */
export function persistScope(scope: ViewScope): void {
  try {
    localStorage.setItem(STORAGE_KEY, scope);
  } catch {
    // localStorage unavailable
  }
}

/**
 * Determine the default view scope for a workspace.
 * - SwarmWS (isDefault=true): defaults to 'global'
 * - Custom workspaces: defaults to 'scoped'
 */
export function getDefaultScope(isDefaultWorkspace: boolean): ViewScope {
  return isDefaultWorkspace ? 'global' : 'scoped';
}

/**
 * Resolve the effective view scope considering persistence and workspace type.
 * - For SwarmWS: use persisted value if available, otherwise 'global'
 * - For custom workspaces: always default to 'scoped' (user can toggle to 'global')
 */
export function resolveScope(
  isDefaultWorkspace: boolean,
  persisted: ViewScope | null
): ViewScope {
  if (isDefaultWorkspace && persisted !== null) {
    return persisted;
  }
  return getDefaultScope(isDefaultWorkspace);
}

/**
 * Compute the effective workspace ID for API calls based on view scope.
 * - 'global' → 'all'
 * - 'scoped' → the actual workspace ID
 */
export function getEffectiveWorkspaceId(
  viewScope: ViewScope,
  selectedWorkspaceId: string
): string {
  return viewScope === 'global' ? 'all' : selectedWorkspaceId;
}

export interface UseViewScopeOptions {
  /** Whether the currently selected workspace is the default (SwarmWS) */
  isDefaultWorkspace: boolean;
  /** The currently selected workspace ID (used to compute effectiveWorkspaceId) */
  selectedWorkspaceId?: string;
}

export interface UseViewScopeReturn {
  /** Current view scope */
  viewScope: ViewScope;
  /** Update the view scope (persists to localStorage) */
  setViewScope: (scope: ViewScope) => void;
  /** Whether the current scope is global (convenience) */
  isGlobalView: boolean;
  /** Effective workspace ID for API calls: 'all' when global, actual ID when scoped */
  effectiveWorkspaceId: string;
}

/**
 * Custom hook for managing the view/scope toggle state.
 *
 * - SwarmWS defaults to 'global', persists selection across sessions
 * - Custom workspaces default to 'scoped'
 * - Persistence uses localStorage with key 'swarm-view-scope'
 * - Returns effectiveWorkspaceId for API calls ('all' or actual workspace ID)
 *
 * Requirements: 37.1-37.4, 37.6, 37.7
 */
export function useViewScope({ isDefaultWorkspace, selectedWorkspaceId = '' }: UseViewScopeOptions): UseViewScopeReturn {
  const [viewScope, setViewScopeState] = useState<ViewScope>(() => {
    const persisted = readPersistedScope();
    return resolveScope(isDefaultWorkspace, persisted);
  });

  // When workspace type changes (switching between SwarmWS and custom),
  // re-resolve the scope
  useEffect(() => {
    const persisted = readPersistedScope();
    setViewScopeState(resolveScope(isDefaultWorkspace, persisted));
  }, [isDefaultWorkspace]);

  const setViewScope = useCallback(
    (scope: ViewScope) => {
      setViewScopeState(scope);
      // Only persist for SwarmWS (default workspace)
      if (isDefaultWorkspace) {
        persistScope(scope);
      }
    },
    [isDefaultWorkspace]
  );

  const isGlobalView = useMemo(() => viewScope === 'global', [viewScope]);

  const effectiveWorkspaceId = useMemo(
    () => getEffectiveWorkspaceId(viewScope, selectedWorkspaceId),
    [viewScope, selectedWorkspaceId]
  );

  return { viewScope, setViewScope, isGlobalView, effectiveWorkspaceId };
}
