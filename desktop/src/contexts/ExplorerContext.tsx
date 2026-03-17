/**
 * Explorer context for the SwarmWS Workspace Explorer.
 *
 * Provides shared state between the TopBar and the
 * WorkspaceExplorer (VirtualizedTree). State is split into three
 * sub-contexts for render performance:
 *
 * - ``TreeDataContext``   — treeData, isLoading, error, refreshTree
 * - ``SelectionContext``  — expandedPaths, selectedPath, matchedPaths,
 *                           highlightedPaths
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
 * - ``expandedPaths`` is persisted to sessionStorage under key
 *   ``swarmws-explorer-state``.
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

  // Actions
  refreshTree: () => void;
}

/** Persisted to sessionStorage under key "swarmws-explorer-state". */
export interface ExplorerSessionState {
  expandedPaths: string[];
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
      Array.isArray(parsed.expandedPaths)
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

  // ── Polling ref (declared early so fetchTree/refreshTree can seed it) ──
  const lastTreeRef = useRef<TreeNode[] | null>(null);

  // ── Fetch tree data on mount ───────────────────────────────────────────
  const fetchTree = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const tree = await workspaceService.getTree();
      lastTreeRef.current = tree; // Seed polling ref to avoid redundant first-poll re-render
      setTreeData(tree);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace tree';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  // lastTreeRef is a stable ref — safe to omit from deps
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshTree = useCallback(async () => {
    setError(null);
    try {
      const tree = await workspaceService.refreshTree();
      lastTreeRef.current = tree; // Keep polling ref in sync after manual refresh
      setTreeData(tree);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Failed to load workspace tree';
      setError(message);
    }
  // lastTreeRef is a stable ref — safe to omit from deps
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  // ── Auto-refresh via ETag polling ─────────────────────────────────────
  // Poll getTree() every 30 seconds. The service-layer ETag cache makes
  // this very lightweight: when nothing changed the server returns 304
  // and getTree() returns the same cached array reference. We compare
  // against lastTreeRef so we only call setTreeData on actual changes.
  // Reduced from 5s → 15s → 30s to cut CPU from git status + fs scan.
  useEffect(() => {
    const POLL_INTERVAL_MS = 30_000;
    const id = setInterval(async () => {
      // Skip polling when tab is hidden to save resources
      if (document.hidden) return;
      try {
        const tree = await workspaceService.getTree();
        // On 304, getTree() returns the same _cachedTree reference.
        // Only update state when the reference differs (actual change).
        if (tree !== lastTreeRef.current) {
          lastTreeRef.current = tree;
          setTreeData(tree);
        }
      } catch {
        // Silently ignore polling errors — manual refresh still works
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  // ── Default-expand zone folders on first load (no saved session) ───────
  useEffect(() => {
    if (treeData.length === 0) return;
    // Only seed defaults when there's no saved session (expandedPaths is empty)
    if (expandedPaths.size > 0) return;
    // Only Knowledge expanded by default; Projects and Attachments start collapsed
    const defaultExpanded = ['Knowledge'];
    const defaults = treeData
      .filter((n) => n.type === 'directory' && defaultExpanded.includes(n.name))
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
    });
  }, [expandedPaths]);

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
    }),
    [
      expandedPaths,
      toggleExpand,
      expandAll,
      collapseAll,
      selectedPath,
      matchedPaths,
      highlightedPaths,
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
 *  Returns null instead of throwing when the provider is not in the tree. */
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
