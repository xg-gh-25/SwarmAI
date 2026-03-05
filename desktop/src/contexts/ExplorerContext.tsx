/**
 * Explorer context for the SwarmWS Workspace Explorer.
 *
 * Provides shared state between the TopBar (GlobalSearchBar) and the
 * WorkspaceExplorer (VirtualizedTree). State is split into three
 * sub-contexts for render performance:
 *
 * - ``TreeDataContext``   — treeData, isLoading, error, refreshTree
 * - ``SelectionContext``  — expandedPaths, selectedPath, matchedPaths,
 *                           highlightedPaths, focusMode, activeProjectId
 * - ``SearchContext``     — searchQuery, setSearchQuery
 *
 * Key exports:
 * - ``ExplorerProvider``      — Wraps components that need explorer state
 * - ``useTreeData``           — Hook for tree data (fetch-only changes)
 * - ``useSelection``          — Hook for expand/collapse, focus, selection
 * - ``useSearch``             — Hook for search query (keystroke changes)
 * - ``useExplorerContext``    — Convenience hook composing all three
 *
 * Session persistence:
 * - ``expandedPaths``, ``focusMode``, and ``activeProjectId`` are persisted
 *   to sessionStorage under key ``swarmws-explorer-state``.
 * - On mount, state is restored from sessionStorage; read failures fall
 *   back silently to defaults.
 */
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
  startTransition,
  type ReactNode,
} from 'react';
import { workspaceService } from '../services/workspace';
import type { TreeNode } from '../types';

// ─────────────────────────────────────────────────────────────────────────────
// Public interface
// ─────────────────────────────────────────────────────────────────────────────

/** Full explorer state — returned by the convenience useExplorerContext hook. */
export interface ExplorerState {
  // Tree data
  treeData: TreeNode[];
  isLoading: boolean;
  error: string | null;

  // Expand/collapse
  expandedPaths: Set<string>;
  toggleExpand: (path: string) => void;
  expandAll: () => void;
  collapseAll: () => void;

  // Selection
  selectedPath: string | null;
  setSelectedPath: (path: string | null) => void;

  // Search
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  matchedPaths: Set<string>;
  highlightedPaths: Set<string>;

  // Focus Mode
  focusMode: boolean;
  toggleFocusMode: () => void;
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;

  // Actions
  refreshTree: () => void;
}

/** Persisted to sessionStorage under key "swarmws-explorer-state". */
export interface ExplorerSessionState {
  expandedPaths: string[];
  focusMode: boolean;
  activeProjectId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Session storage helpers
// ─────────────────────────────────────────────────────────────────────────────

const SESSION_STORAGE_KEY = 'swarmws-explorer-state';

/** Serialize explorer session state to sessionStorage. */
export function saveSessionState(state: ExplorerSessionState): void {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Silently ignore quota exceeded or disabled sessionStorage
  }
}

/** Deserialize explorer session state from sessionStorage.
 *  Returns null on any read failure (missing, invalid JSON, etc.). */
export function loadSessionState(): ExplorerSessionState | null {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Basic shape validation
    if (
      parsed &&
      Array.isArray(parsed.expandedPaths) &&
      typeof parsed.focusMode === 'boolean'
    ) {
      return parsed as ExplorerSessionState;
    }
    return null;
  } catch {
    // Silently fall back to defaults on read failure
    console.warn('ExplorerContext: failed to read session state, using defaults');
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-context 1: TreeDataContext
// ─────────────────────────────────────────────────────────────────────────────

interface TreeDataContextValue {
  treeData: TreeNode[];
  isLoading: boolean;
  error: string | null;
  refreshTree: () => void;
}

const TreeDataContext = createContext<TreeDataContextValue | undefined>(undefined);

// ─────────────────────────────────────────────────────────────────────────────
// Sub-context 2: SelectionContext
// ─────────────────────────────────────────────────────────────────────────────

interface SelectionContextValue {
  expandedPaths: Set<string>;
  toggleExpand: (path: string) => void;
  expandAll: () => void;
  collapseAll: () => void;
  selectedPath: string | null;
  setSelectedPath: (path: string | null) => void;
  matchedPaths: Set<string>;
  highlightedPaths: Set<string>;
  focusMode: boolean;
  toggleFocusMode: () => void;
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
}

const SelectionContext = createContext<SelectionContextValue | undefined>(undefined);

// ─────────────────────────────────────────────────────────────────────────────
// Sub-context 3: SearchContext
// ─────────────────────────────────────────────────────────────────────────────

interface SearchContextValue {
  searchQuery: string;
  setSearchQuery: (query: string) => void;
}

const SearchContext = createContext<SearchContextValue | undefined>(undefined);

// ─────────────────────────────────────────────────────────────────────────────
// Tree helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Collect all directory paths in a tree (for expandAll). */
function collectAllDirectoryPaths(nodes: TreeNode[]): string[] {
  const paths: string[] = [];
  function walk(node: TreeNode) {
    if (node.type === 'directory') {
      paths.push(node.path);
      node.children?.forEach(walk);
    }
  }
  nodes.forEach(walk);
  return paths;
}

// ─────────────────────────────────────────────────────────────────────────────
// Focus mode helpers (exported for property testing)
// ─────────────────────────────────────────────────────────────────────────────

/** Collect all directory paths under a given node recursively. */
function collectDescendantDirectoryPaths(node: TreeNode): string[] {
  const paths: string[] = [];
  function walk(n: TreeNode) {
    if (n.type === 'directory') {
      paths.push(n.path);
      n.children?.forEach(walk);
    }
  }
  // Walk children only (the node itself is handled by the caller)
  node.children?.forEach(walk);
  return paths;
}

/** Find a node by path in the tree. */
function findNodeByPath(nodes: TreeNode[], targetPath: string): TreeNode | null {
  for (const node of nodes) {
    if (node.path === targetPath) return node;
    if (node.children) {
      const found = findNodeByPath(node.children, targetPath);
      if (found) return found;
    }
  }
  return null;
}

/** Compute the expandedPaths set for focus mode.
 *
 *  When focus mode is enabled:
 *  - Include "Projects" (the parent container) so the active project is visible
 *  - Include the active project path "Projects/{activeProjectId}"
 *  - Recursively include all directory children of the active project
 *  - Do NOT include "Knowledge" (visible in flattened list but collapsed)
 *  - Do NOT include any other project paths under Projects/
 *
 *  @param treeData - The full workspace tree
 *  @param activeProjectId - The ID of the active project (folder name under Projects/)
 *  @returns A new Set of expanded paths for focus mode
 */
export function computeFocusModeExpandedPaths(
  treeData: TreeNode[],
  activeProjectId: string,
): Set<string> {
  const expanded = new Set<string>();

  // Include "Projects" so the active project folder is visible
  expanded.add('Projects');

  // Include the active project path
  const activeProjectPath = `Projects/${activeProjectId}`;
  expanded.add(activeProjectPath);

  // Find the active project node in the tree and expand all its directory children
  const activeProjectNode = findNodeByPath(treeData, activeProjectPath);
  if (activeProjectNode) {
    const descendantPaths = collectDescendantDirectoryPaths(activeProjectNode);
    for (const p of descendantPaths) {
      expanded.add(p);
    }
  }

  // Knowledge is NOT added to expandedPaths — it stays visible in the
  // flattened list (it's a top-level folder) but collapsed.

  return expanded;
}

// ─────────────────────────────────────────────────────────────────────────────
// Search helpers (exported for property testing)
// ─────────────────────────────────────────────────────────────────────────────

/** Check if a node name matches the query (case-insensitive substring). */
export function substringMatch(name: string, query: string): boolean {
  if (!query) return false;
  return name.toLowerCase().includes(query.toLowerCase());
}

/** Find all matching paths and their ancestors in the tree.
 *
 *  Returns two sets:
 *  - ``matched`` — paths whose node name contains the query as a substring
 *  - ``ancestors`` — paths of all ancestor directories of matched nodes
 */
export function findMatches(
  nodes: TreeNode[],
  query: string,
): { matched: Set<string>; ancestors: Set<string> } {
  const matched = new Set<string>();
  const ancestors = new Set<string>();

  function walk(node: TreeNode, ancestorPaths: string[]): boolean {
    const isMatch = substringMatch(node.name, query);
    let hasMatchingDescendant = false;

    if (node.children) {
      for (const child of node.children) {
        if (walk(child, [...ancestorPaths, node.path])) {
          hasMatchingDescendant = true;
        }
      }
    }

    if (isMatch) {
      matched.add(node.path);
      for (const ap of ancestorPaths) ancestors.add(ap);
    }

    if (hasMatchingDescendant) {
      ancestors.add(node.path);
    }

    return isMatch || hasMatchingDescendant;
  }

  for (const node of nodes) walk(node, []);
  return { matched, ancestors };
}

// ─────────────────────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────────────────────

interface ExplorerProviderProps {
  children: ReactNode;
}

export function ExplorerProvider({ children }: ExplorerProviderProps) {
  // ── Restore session state on mount ──────────────────────────────────────
  const sessionState = useRef(loadSessionState());

  // ── Tree data state ────────────────────────────────────────────────────
  const [treeData, setTreeData] = useState<TreeNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── Selection / expand state ───────────────────────────────────────────
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(
    () => new Set(sessionState.current?.expandedPaths ?? [])
  );
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  // ── Search state ────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('');
  const [matchedPaths, setMatchedPaths] = useState<Set<string>>(() => new Set());
  const [highlightedPaths, setHighlightedPaths] = useState<Set<string>>(() => new Set());

  /** Snapshot of expandedPaths taken before the first search keystroke.
   *  Restored when the search query is cleared. */
  const preSearchExpandedPaths = useRef<Set<string> | null>(null);

  // ── Focus mode state ───────────────────────────────────────────────────
  const [focusMode, setFocusMode] = useState(
    () => sessionState.current?.focusMode ?? false
  );
  const [activeProjectId, setActiveProjectId] = useState<string | null>(
    () => sessionState.current?.activeProjectId ?? null
  );

  /** Snapshot of expandedPaths taken before focus mode was enabled.
   *  Restored when focus mode is toggled OFF. */
  const preFocusExpandedPaths = useRef<Set<string> | null>(null);

  // ── Fetch tree data on mount ───────────────────────────────────────────
  const fetchTree = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const tree = await workspaceService.getTree();
      setTreeData(tree);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace tree';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const refreshTree = useCallback(async () => {
    setError(null);
    try {
      const tree = await workspaceService.refreshTree();
      setTreeData(tree);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace tree';
      setError(message);
    }
  }, []);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  // ── Default-expand zone folders on first load (no saved session) ───────
  useEffect(() => {
    if (treeData.length === 0) return;
    // Only seed defaults when there's no saved session (expandedPaths is empty)
    if (expandedPaths.size > 0) return;
    const zoneFolders = ['Knowledge', 'Projects'];
    const defaults = treeData
      .filter((n) => n.type === 'directory' && zoneFolders.includes(n.name))
      .map((n) => n.path);
    if (defaults.length > 0) {
      setExpandedPaths(new Set(defaults));
    }
  // Only run once when treeData first populates
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData]);

  // ── Persist session state on change ────────────────────────────────────
  useEffect(() => {
    saveSessionState({
      expandedPaths: Array.from(expandedPaths),
      focusMode,
      activeProjectId,
    });
  }, [expandedPaths, focusMode, activeProjectId]);

  // ── Search: compute matchedPaths & highlightedPaths on query change ────
  useEffect(() => {
    if (!searchQuery) {
      // Query cleared — restore pre-search snapshot if we have one
      if (preSearchExpandedPaths.current !== null) {
        setExpandedPaths(preSearchExpandedPaths.current);
        preSearchExpandedPaths.current = null;
      }
      setMatchedPaths(new Set());
      setHighlightedPaths(new Set());
      return;
    }

    // Snapshot expandedPaths before the first search keystroke
    if (preSearchExpandedPaths.current === null) {
      preSearchExpandedPaths.current = new Set(expandedPaths);
    }

    // Use startTransition to avoid blocking UI during large-tree traversals
    startTransition(() => {
      const { matched, ancestors } = findMatches(treeData, searchQuery);
      setMatchedPaths(matched);

      if (matched.size === 0) {
        // No matches — keep current expandedPaths, no auto-expand changes
        setHighlightedPaths(new Set());
        return;
      }

      // highlightedPaths = matchedPaths ∪ ancestors
      const highlighted = new Set(matched);
      for (const a of ancestors) highlighted.add(a);
      setHighlightedPaths(highlighted);

      // Temporarily override expandedPaths with highlightedPaths
      setExpandedPaths(highlighted);
    });
  // expandedPaths is intentionally omitted — we only snapshot it on the first
  // search keystroke via preSearchExpandedPaths ref, not on every expand/collapse.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, treeData]);

  // ── Expand / collapse actions ──────────────────────────────────────────
  const toggleExpand = useCallback((path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    const allPaths = collectAllDirectoryPaths(treeData);
    setExpandedPaths(new Set(allPaths));
  }, [treeData]);

  const collapseAll = useCallback(() => {
    setExpandedPaths(new Set());
  }, []);

  // ── Focus mode toggle ────────────────────────────────────────────────
  const toggleFocusMode = useCallback(() => {
    if (activeProjectId === null) return; // no-op when no project selected

    setFocusMode((prev) => {
      const nextFocusMode = !prev;

      if (nextFocusMode) {
        // Toggling ON: snapshot current expandedPaths, then compute focus paths
        preFocusExpandedPaths.current = new Set(expandedPaths);
        const focusPaths = computeFocusModeExpandedPaths(treeData, activeProjectId);
        setExpandedPaths(focusPaths);
      } else {
        // Toggling OFF: restore the snapshot exactly
        if (preFocusExpandedPaths.current !== null) {
          setExpandedPaths(preFocusExpandedPaths.current);
          preFocusExpandedPaths.current = null;
        }
      }

      return nextFocusMode;
    });
  }, [activeProjectId, expandedPaths, treeData]);

  // ── Memoized sub-context values ────────────────────────────────────────
  const treeDataValue = useMemo<TreeDataContextValue>(
    () => ({ treeData, isLoading, error, refreshTree }),
    [treeData, isLoading, error, refreshTree]
  );

  const selectionValue = useMemo<SelectionContextValue>(
    () => ({
      expandedPaths,
      toggleExpand,
      expandAll,
      collapseAll,
      selectedPath,
      setSelectedPath,
      matchedPaths,
      highlightedPaths,
      focusMode,
      toggleFocusMode,
      activeProjectId,
      setActiveProjectId,
    }),
    [
      expandedPaths,
      toggleExpand,
      expandAll,
      collapseAll,
      selectedPath,
      matchedPaths,
      highlightedPaths,
      focusMode,
      toggleFocusMode,
      activeProjectId,
    ]
  );

  const searchValue = useMemo<SearchContextValue>(
    () => ({ searchQuery, setSearchQuery }),
    [searchQuery]
  );

  // ── Render nested providers ────────────────────────────────────────────
  return (
    <TreeDataContext.Provider value={treeDataValue}>
      <SelectionContext.Provider value={selectionValue}>
        <SearchContext.Provider value={searchValue}>
          {children}
        </SearchContext.Provider>
      </SelectionContext.Provider>
    </TreeDataContext.Provider>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Individual hooks (for performance-sensitive components)
// ─────────────────────────────────────────────────────────────────────────────

/** Subscribe to tree data only (changes on fetch). */
export function useTreeData(): TreeDataContextValue {
  const ctx = useContext(TreeDataContext);
  if (!ctx) throw new Error('useTreeData must be used within an ExplorerProvider');
  return ctx;
}

/** Subscribe to selection / expand / focus state (changes on user interaction). */
export function useSelection(): SelectionContextValue {
  const ctx = useContext(SelectionContext);
  if (!ctx) throw new Error('useSelection must be used within an ExplorerProvider');
  return ctx;
}

/** Subscribe to search query only (changes on every debounced keystroke). */
export function useSearch(): SearchContextValue {
  const ctx = useContext(SearchContext);
  if (!ctx) throw new Error('useSearch must be used within an ExplorerProvider');
  return ctx;
}

/** Safe variant of useSearch that returns null when outside ExplorerProvider.
 *  Used by GlobalSearchBar which may render before the provider is wired. */
export function useSearchSafe(): SearchContextValue | null {
  return useContext(SearchContext) ?? null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Convenience hook (composes all three sub-contexts)
// ─────────────────────────────────────────────────────────────────────────────

/** Convenience hook that composes all three sub-contexts into a single ExplorerState.
 *  Use individual hooks (useTreeData, useSelection, useSearch) in
 *  performance-sensitive components to avoid unnecessary re-renders. */
export function useExplorerContext(): ExplorerState {
  const tree = useTreeData();
  const selection = useSelection();
  const search = useSearch();

  return {
    ...tree,
    ...selection,
    ...search,
  };
}
