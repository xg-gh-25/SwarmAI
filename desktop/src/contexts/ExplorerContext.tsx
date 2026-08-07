/* eslint-disable react-refresh/only-export-components */
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
  useDeferredValue,
  startTransition,
  type ReactNode,
} from 'react';
import { workspaceService } from '../services/workspace';
import type { TreeNode } from '../types';

// ─────────────────────────────────────────────────────────────────────────────
// Public interface
// ─────────────────────────────────────────────────────────────────────────────

/** Tree sibling sort order. 'default' preserves the built-in ordering
 *  (dirs-first + date-desc for Knowledge/Attachments). The explicit modes
 *  REPLACE that ordering when chosen (a user's explicit sort wins over the
 *  date-desc heuristic — Gate-1 R2). No mtime/size sort: TreeNode carries no
 *  timestamp field, so those would require a backend tree-API change. */
export type SortMode = 'default' | 'name-asc' | 'name-desc' | 'git-first';

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

  // Sort
  sortMode: SortMode;
  setSortMode: (mode: SortMode) => void;

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
  sortMode: SortMode;
  setSortMode: (mode: SortMode) => void;
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

/** Git statuses that count as a live change worth surfacing/floating.
 *  SINGLE SOURCE shared by computeChangedAncestors (default-expand) AND
 *  VirtualizedTree's git-first sort — so the two can never drift (a file that
 *  sorts to the top under git-first is the same file whose dir auto-expands on
 *  load). `conflicting` is included deliberately — a merge conflict is exactly
 *  what should be revealed first. `deleted`/`ignored` are excluded: a deleted
 *  file has nothing to reveal by expanding, and ignored is not "work".
 *  (Note: SectionedExplorer's Working Files card keeps its own narrower
 *  modified/added/untracked display filter — that is a card-display choice,
 *  independent of this expand/sort signal.) */
export const CHANGED_GIT_STATUSES = new Set<string>([
  'modified', 'added', 'untracked', 'conflicting', 'renamed',
]);

/** Collect the ancestor DIRECTORY paths of every git-changed file in the tree.
 *
 *  Used by default-expand-on-first-load: expanding these paths makes uncommitted
 *  changes visible without manual drilling. Returns directory paths only — never
 *  the changed file's own path (you expand its container, not the file).
 *
 *  Scope note (Gate-1 R3): only sees changes within the fetched tree depth
 *  (server default = 3). This is the SAME visibility the Working Files card has
 *  (both walk the same loaded treeData) — changes deeper than the initial fetch
 *  are surfaced when the user expands into them, consistent with lazy loading.
 *
 *  Pure — no side effects. */
export function computeChangedAncestors(nodes: TreeNode[]): Set<string> {
  const ancestors = new Set<string>();
  const walk = (list: TreeNode[], ancestorPaths: string[]): void => {
    for (const node of list) {
      if (node.type === 'directory') {
        if (node.children) walk(node.children, [...ancestorPaths, node.path]);
      } else if (node.gitStatus && CHANGED_GIT_STATUSES.has(node.gitStatus)) {
        for (const ap of ancestorPaths) ancestors.add(ap);
      }
    }
  };
  walk(nodes, []);
  return ancestors;
}

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
// Poll-merge helper (exported for testing)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Merge lazy-expanded children from a previous tree into a fresh tree.
 *
 * The 30s ETag poll refetches the tree at the server's default depth. Any
 * directory that was lazily expanded past that depth (its children injected
 * client-side via ``injectChildren``) comes back in the fresh tree with
 * ``children: null`` (depth-truncated), while its path remains in
 * ``expandedPaths``. Without this merge, ``flattenChildren`` renders those
 * directories as expanded-but-EMPTY until the user collapses and re-expands.
 *
 * This walks the FRESH tree (never the previous one), so a directory that was
 * DELETED on disk is simply absent from ``newTree`` and never gets stale
 * children re-injected — deletions are honored, not masked. Recursion descends
 * into re-injected subtrees so nested expansions (e.g. ``Projects`` AND
 * ``Projects/AIDLC`` both expanded) are all restored, not just the top level.
 *
 * A node is only patched when the fresh node has ``children === null`` AND the
 * previous tree had a non-null children array for the same path — i.e. we only
 * restore what the poll truncated, never overwrite children the server did send.
 *
 * Caveat: re-injected children carry the git_status they had at expand time
 * (the poll did not refetch them). This is inherent to lazy loading and is
 * strictly better than the alternative (the whole subtree vanishing); the next
 * manual expand or refresh refetches fresh status.
 *
 * Pure function — no side effects. Returns a new tree; unaffected nodes are
 * referentially reused.
 */
export function mergeExpandedChildren(
  newTree: TreeNode[],
  prevTree: TreeNode[] | null,
  expandedPaths: Set<string>,
): TreeNode[] {
  if (!prevTree || expandedPaths.size === 0) return newTree;

  // Index the previous tree's directory nodes by path for O(1) lookup.
  const prevByPath = new Map<string, TreeNode>();
  const indexPrev = (nodes: TreeNode[]): void => {
    for (const n of nodes) {
      if (n.type === 'directory') {
        prevByPath.set(n.path, n);
        if (n.children) indexPrev(n.children);
      }
    }
  };
  indexPrev(prevTree);

  const walk = (nodes: TreeNode[]): TreeNode[] =>
    nodes.map((node) => {
      if (node.type !== 'directory') return node;

      // Case 1: fresh node was truncated (children === null) but the path is
      // expanded and we have prior children → restore them, then recurse into
      // the restored subtree so nested expansions are also honored.
      if (node.children === null && expandedPaths.has(node.path)) {
        const prev = prevByPath.get(node.path);
        if (prev && prev.children) {
          return { ...node, children: walk(prev.children) };
        }
        return node;
      }

      // Case 2: fresh node has its own children → recurse (a descendant may be
      // truncated even when this level was sent).
      if (node.children) {
        return { ...node, children: walk(node.children) };
      }

      return node;
    });

  return walk(newTree);
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

  // ── Sort state (session-ephemeral — resets to default per session) ───────
  const [sortMode, setSortMode] = useState<SortMode>('default');

  // ── Search state ────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('');
  // Deferred copy of the query: React lets this lag behind during rapid typing so
  // the expensive full-tree findMatches walk runs once when typing settles, not
  // once per keystroke. Snapshot/restore logic still keys off the immediate query.
  const deferredQuery = useDeferredValue(searchQuery);
  const [matchedPaths, setMatchedPaths] = useState<Set<string>>(() => new Set());
  const [highlightedPaths, setHighlightedPaths] = useState<Set<string>>(() => new Set());

  /** Snapshot of expandedPaths taken before the first search keystroke.
   *  Restored when the search query is cleared. */
  const preSearchExpandedPaths = useRef<Set<string> | null>(null);

  // ── Polling ref (declared early so fetchTree/refreshTree can seed it) ──
  const lastTreeRef = useRef<TreeNode[] | null>(null);

  // Latest expandedPaths, mirrored into a ref so the 30s poll effect (which has
  // an empty dep array and would otherwise capture the mount-time empty Set) can
  // read the current expansion state when merging lazy-loaded children.
  const expandedPathsRef = useRef<Set<string>>(expandedPaths);

  // Latest treeData, mirrored into a ref so toggleExpand can read the current
  // tree WITHOUT closing over treeData. Closing over it made toggleExpand a new
  // function on every 200 poll (treeData gets a fresh array reference), which
  // churned SelectionContext identity → a full re-render of every visible tree
  // row every 30s. Reading via the ref keeps toggleExpand referentially stable
  // (deps [injectChildren] only) while still finding nodes in the latest tree.
  const treeDataRef = useRef<TreeNode[]>(treeData);

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
        // On 304, getTree() returns the same _cachedTree reference (setCachedTree keeps it in sync).
        // Only update state when the reference differs (actual filesystem change → 200 response).
        if (tree !== lastTreeRef.current) {
          // A depth-limited 200 response lacks children that were lazily
          // expanded past the server's default depth. Merge those back from the
          // previous tree so expanded folders don't collapse to empty on every
          // filesystem change (which happens frequently in an active workspace).
          // mergeExpandedChildren walks the FRESH tree, so on-disk deletions are
          // honored (absent nodes get nothing re-injected).
          const merged = mergeExpandedChildren(tree, lastTreeRef.current, expandedPathsRef.current);
          lastTreeRef.current = merged;
          workspaceService.setCachedTree(merged);
          setTreeData(merged);
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
    // Also expand the ancestors of any git-changed file so uncommitted work is
    // visible on first load without manual drilling (default-expand-changed).
    // Seeded into the SAME initial setExpandedPaths as the Knowledge default.
    // The search snapshot (separate effect below) captures whatever expandedPaths
    // holds at the first keystroke — and a keystroke cannot precede treeData load
    // (the input renders under the tree), so this seed always commits before any
    // snapshot. No snapshot/restore race in practice (Gate-1 R4).
    const changedAncestors = computeChangedAncestors(treeData);
    const seed = new Set<string>([...defaults, ...changedAncestors]);
    if (seed.size > 0) {
      setExpandedPaths(seed);
    }
  // Only run once when treeData first populates
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [treeData]);

  // ── Persist session state on change ────────────────────────────────────
  useEffect(() => {
    // Mirror into the ref so the empty-dep poll effect sees the latest set.
    expandedPathsRef.current = expandedPaths;
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

    // Snapshot expandedPaths before the first search keystroke (immediate — keyed
    // on searchQuery, not the deferred value, so the snapshot is taken the instant
    // the user starts typing).
    if (preSearchExpandedPaths.current === null) {
      preSearchExpandedPaths.current = new Set(expandedPaths);
    }

    // Defer the expensive full-tree walk: while the user is typing rapidly,
    // deferredQuery lags behind searchQuery, so they are unequal and we skip the
    // walk entirely — findMatches runs ONCE when typing settles (deferred catches
    // up), instead of once per keystroke. Also avoids a clobber-on-clear race:
    // when searchQuery becomes '', the empty-branch above returns before this walk.
    if (deferredQuery !== searchQuery) return;

    // Use startTransition to avoid blocking UI during large-tree traversals
    startTransition(() => {
      const { matched, ancestors } = findMatches(treeData, deferredQuery);
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
  }, [searchQuery, deferredQuery, treeData]);

  // ── Expand / collapse actions ──────────────────────────────────────────

  /** Recursively find a node by path and inject children into a tree copy. */
  const injectChildren = useCallback((tree: TreeNode[], targetPath: string, children: TreeNode[]): TreeNode[] => {
    return tree.map((node) => {
      if (node.path === targetPath) {
        return { ...node, children };
      }
      if (node.children && targetPath.startsWith(node.path + '/')) {
        return { ...node, children: injectChildren(node.children, targetPath, children) };
      }
      return node;
    });
  }, []);

  /** Set of paths currently being lazy-loaded (prevents duplicate fetches). */
  const loadingPaths = useRef<Set<string>>(new Set());

  // Keep the treeData ref current on every render so toggleExpand (which reads
  // the ref, not a closure) always sees the latest tree without being recreated.
  treeDataRef.current = treeData;

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

    // Lazy load: if the directory has children === null (depth-truncated),
    // fetch its children from the server and inject into the tree.
    const findNode = (nodes: TreeNode[], target: string): TreeNode | null => {
      for (const n of nodes) {
        if (n.path === target) return n;
        if (n.children && target.startsWith(n.path + '/')) {
          const found = findNode(n.children, target);
          if (found) return found;
        }
      }
      return null;
    };

    const node = findNode(treeDataRef.current, path);
    if (node && node.type === 'directory' && node.children === null && !loadingPaths.current.has(path)) {
      loadingPaths.current.add(path);
      workspaceService.expandDirectory(path).then((children) => {
        setTreeData((prev) => {
          const updated = injectChildren(prev, path, children);
          lastTreeRef.current = updated;
          // Keep service cache in sync so ETag poll (304) doesn't revert
          // expanded children. Without this, poll returns stale _cachedTree
          // which lacks the injected children → silent regression every 30s.
          workspaceService.setCachedTree(updated);
          return updated;
        });
      }).catch(() => {
        // Silently fail — node stays collapsed without children
      }).finally(() => {
        loadingPaths.current.delete(path);
      });
    }
  }, [injectChildren]);

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
      sortMode,
      setSortMode,
      matchedPaths,
      highlightedPaths,
    }),
    [
      expandedPaths,
      toggleExpand,
      expandAll,
      collapseAll,
      selectedPath,
      sortMode,
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
