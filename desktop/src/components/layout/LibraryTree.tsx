/**
 * LibraryTree — an expandable, virtualized file tree scoped to `Knowledge/`,
 * rendered inside the Library overlay's Browse tab.
 *
 * WHY a focused component (not the WorkspaceExplorer's VirtualizedTree): that
 * tree is coupled to ExplorerContext (useTreeData/useSelection), EXPLORER_SECTIONS
 * (it renders ALL zones — Knowledge/Projects/Attachments), and drag/rename/
 * context-menu machinery — none of which the Library browse surface wants. The
 * clean reuse boundary is the three primitives that ARE decoupled:
 *   1. `workspaceService.getTree` / `.expandDirectory` — the live filesystem tree
 *      endpoint (GET /api/workspace/tree[/expand]); Library invents no data (R30).
 *   2. `TreeNodeRow` — the props-only leaf row renderer (no context, no hooks).
 *   3. react-window v2 `List` — virtualization (Knowledge/ has 400+-file folders
 *      like Notes/JobResults; a non-virtualized tree would blow the DOM — GUI05
 *      "consider performance before building").
 *
 * Scope: we fetch the full workspace tree once, then keep ONLY the `Knowledge`
 * subtree's children as our roots (backend has no per-subtree root endpoint; the
 * tree endpoint already returns Knowledge as a top-level node). Expansion is
 * per-directory lazy-load via `expandDirectory` (depth-truncated dirs arrive as
 * `children === null`).
 *
 * File open reuses the EXISTING `swarm:open-file` document event → useCanvasHost
 * resolves + opens it in Canvas. Directories toggle-expand; ONLY `type==='file'`
 * nodes dispatch open-file (the handler is file-oriented — a directory path would
 * render an empty Canvas, so we never dispatch one).
 *
 * @exports LibraryTree
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { List } from 'react-window';
import type { TreeNode } from '../../types';
import { workspaceService } from '../../services/workspace';
import TreeNodeRow from '../workspace-explorer/TreeNodeRow';

const ROW_HEIGHT = 32;
const KNOWLEDGE_ROOT = 'Knowledge';

/** A visible, flattened tree row (only expanded subtrees are included). */
interface FlatRow {
  node: TreeNode;
  depth: number;
  isExpanded: boolean;
}

/** Date prefix pattern: YYYY-MM-DD at the start of a name (newest-first sort). */
const DATE_PREFIX_RE = /^\d{4}-\d{2}-\d{2}/;

/**
 * Sort children: directories before files; date-prefixed names descending
 * (newest first — Knowledge/ is date-heavy), else alphabetical. Mirrors the
 * explorer's Knowledge ordering so the two surfaces agree.
 */
function sortChildren(children: TreeNode[]): TreeNode[] {
  return [...children].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
    const aDate = DATE_PREFIX_RE.test(a.name);
    const bDate = DATE_PREFIX_RE.test(b.name);
    if (aDate && bDate) return b.name.localeCompare(a.name); // newest first
    if (aDate !== bDate) return aDate ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

/**
 * Flatten the visible tree: only descend into directories whose path is in
 * `expandedPaths`. Depth starts at 0 for the Knowledge roots.
 */
function flatten(nodes: TreeNode[], expandedPaths: Set<string>, depth = 0, out: FlatRow[] = []): FlatRow[] {
  for (const node of sortChildren(nodes)) {
    const isDir = node.type === 'directory';
    const isExpanded = isDir && expandedPaths.has(node.path);
    out.push({ node, depth, isExpanded });
    if (isExpanded && Array.isArray(node.children) && node.children.length > 0) {
      flatten(node.children, expandedPaths, depth + 1, out);
    }
  }
  return out;
}

/**
 * Immutably replace a directory node's children within a tree (used to inject
 * lazily-loaded children on expand). Returns a new array; untouched branches
 * keep their identity.
 */
function setChildrenAt(nodes: TreeNode[], targetPath: string, children: TreeNode[]): TreeNode[] {
  return nodes.map((n) => {
    if (n.path === targetPath) return { ...n, children };
    if (n.type === 'directory' && Array.isArray(n.children) && n.children.length > 0) {
      return { ...n, children: setChildrenAt(n.children, targetPath, children) };
    }
    return n;
  });
}

/** Props for a react-window row — the flat rows + the two callbacks. */
interface RowProps {
  rows: FlatRow[];
  onToggle: (node: TreeNode) => void;
  onOpen: (node: TreeNode) => void;
}

/** react-window v2 rowComponent — delegates to the shared TreeNodeRow.
 * Signature declares the keys react-window injects (index/style/ariaAttributes)
 * so `List`'s rowProps type resolves to exactly RowProps (mirrors VirtualizedTree). */
function LibraryRow(props: {
  ariaAttributes: { 'aria-posinset': number; 'aria-setsize': number; role: 'listitem' };
  index: number;
  style: React.CSSProperties;
} & RowProps) {
  const { index, style, rows, onToggle, onOpen } = props;
  const row = rows[index];
  if (!row) return null;
  const { node, depth, isExpanded } = row;
  const activate = () => {
    if (node.type === 'directory') onToggle(node);
    else onOpen(node);
  };
  return (
    <TreeNodeRow
      node={node}
      depth={depth}
      isExpanded={isExpanded}
      isSelected={false}
      isMatched={false}
      // directory → toggle expand; file → open in Canvas. Both single-click AND
      // double-click activate (Library favors single-click browse; TreeNodeRow's
      // own single-click already toggles directories via onToggle).
      onToggle={activate}
      onSelect={() => { if (node.type === 'file') onOpen(node); }}
      onContextMenu={() => { /* no context menu in Library browse */ }}
      onDoubleClick={activate}
      style={style}
    />
  );
}

/**
 * LibraryTree — expandable Knowledge/ file tree. Self-contained state (no
 * ExplorerContext): own tree roots + expanded-path set + per-dir lazy load.
 */
export function LibraryTree() {
  const [roots, setRoots] = useState<TreeNode[] | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  // Directories currently mid-lazy-load, so a double-toggle doesn't double-fetch.
  const loadingDirs = useRef<Set<string>>(new Set());
  // Measured height for react-window (overlay is fullscreen; height must be real).
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(400);

  const loadRoots = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      // depth 2 → Knowledge + its immediate children (deeper dirs arrive truncated).
      const tree = await workspaceService.getTree(2);
      const knowledge = tree.find((n) => n.path === KNOWLEDGE_ROOT || n.name === KNOWLEDGE_ROOT);
      const children = knowledge && Array.isArray(knowledge.children) ? knowledge.children : [];
      setRoots(children);
    } catch {
      setError(true);
      setRoots(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadRoots(); }, [loadRoots]);

  // Measure container height for the virtualized List (ResizeObserver, fullscreen).
  // Guard on ResizeObserver presence — jsdom (test env) and older runtimes lack it;
  // there we fall back to a one-shot clientHeight read + the sane default.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => setHeight(Math.max(120, el.clientHeight || 400));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const onToggle = useCallback((node: TreeNode) => {
    const path = node.path;
    const isOpen = expanded.has(path);
    if (isOpen) {
      setExpanded((prev) => { const next = new Set(prev); next.delete(path); return next; });
      return;
    }
    // Expanding: lazy-load children if truncated (children === null) or absent.
    const needsLoad = node.children === null || node.children === undefined;
    setExpanded((prev) => { const next = new Set(prev); next.add(path); return next; });
    if (needsLoad && !loadingDirs.current.has(path)) {
      loadingDirs.current.add(path);
      void workspaceService
        .expandDirectory(path, 2)
        .then((children) => { setRoots((prev) => (prev ? setChildrenAt(prev, path, children) : prev)); })
        .catch(() => { /* leave expanded+empty; a retry re-fires on next toggle */ })
        .finally(() => { loadingDirs.current.delete(path); });
    }
  }, [expanded]);

  const onOpen = useCallback((node: TreeNode) => {
    // File-only: the swarm:open-file handler resolves + opens in Canvas. A
    // directory path would render an empty Canvas, so directories never dispatch.
    if (node.type !== 'file') return;
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: node.path } }));
  }, []);

  const rows = useMemo(() => (roots ? flatten(roots, expanded) : []), [roots, expanded]);

  const rowProps = useMemo<RowProps>(() => ({ rows, onToggle, onOpen }), [rows, onToggle, onOpen]);

  return (
    <div ref={containerRef} data-testid="library-tree" className="flex-1 min-h-0">
      {error ? (
        <div
          data-testid="library-tree-error"
          className="rounded-lg border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-4 py-4 text-center"
        >
          <div className="text-sm text-[var(--color-text)]">Couldn't load the Knowledge tree.</div>
          <button
            data-testid="library-tree-retry"
            onClick={() => { void loadRoots(); }}
            className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
            style={{ background: '#d0524a' }}
          >
            Retry
          </button>
        </div>
      ) : loading ? (
        <div className="py-6 text-center text-sm text-[var(--color-text-faint)]">Loading tree…</div>
      ) : rows.length === 0 ? (
        <div data-testid="library-tree-empty" className="py-6 text-center text-sm text-[var(--color-text-faint)]">
          Knowledge/ is empty.
        </div>
      ) : (
        <List
          style={{ height, width: '100%', overflow: 'auto' }}
          rowCount={rows.length}
          rowHeight={ROW_HEIGHT}
          rowComponent={LibraryRow}
          rowProps={rowProps}
          role="tree"
          aria-label="Knowledge library tree"
        />
      )}
    </div>
  );
}

export default LibraryTree;
