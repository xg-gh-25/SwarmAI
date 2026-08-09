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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { List } from 'react-window';
import type { TreeNode } from '../../types';
import { workspaceService } from '../../services/workspace';
import TreeNodeRow from '../workspace-explorer/TreeNodeRow';

const ROW_HEIGHT = 32;
const KNOWLEDGE_ROOT = 'Knowledge';

/**
 * Noise filter (run_a75197d9) — hides infra/build junk so a rooted tree shows only
 * REAL browsable content (docs / code / specs), never a 74MB code_intel.db, WAL
 * sidecars, .lock files, .artifacts run dirs, or multi-MB *-archive.md dumps.
 * Applied in flatten() so BOTH rendered rows AND any derived counts exclude noise
 * (non-vacuous — rows are the sole flatten output). By-NAME so it's tree-position
 * independent. Library's Knowledge/ root has none of these, so it's inert there —
 * but keeping the filter in the shared component means every rooted tree benefits.
 */
function isNoiseNode(name: string): boolean {
  if (name.startsWith('.')) return true;               // .artifacts, .git, .project.json, .ddd-usage.json, dotfiles
  if (name === '__pycache__' || name === 'node_modules') return true;
  // suffix junk: *.lock (incl *.md.lock), sqlite db + WAL/SHM sidecars, big generated blobs
  if (/\.(lock|db|db-shm|db-wal)$/.test(name)) return true;
  if (name === 'code-intel.json') return true;         // 3.3MB generated graph dump
  if (/-archive\.md$/.test(name)) return true;         // IMPROVEMENT-archive.md etc. (multi-MB, not browsable)
  return false;
}

/** A visible, flattened tree row (only expanded subtrees are included). */
interface FlatRow {
  node: TreeNode;
  depth: number;
  isExpanded: boolean;
  /** true when this row is an infra/noise node kept visible (showAllFiles) but
   *  rendered dimmed. false = a normal row. */
  dimmed: boolean;
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
function flatten(
  nodes: TreeNode[],
  expandedPaths: Set<string>,
  showAllFiles: boolean,
  depth = 0,
  out: FlatRow[] = [],
): FlatRow[] {
  for (const node of sortChildren(nodes)) {
    const noise = isNoiseNode(node.name);
    // Default (Library bookshelf): hide infra/build junk (run_a75197d9).
    // showAllFiles (Brain Hub full-tree browse, run_cfb460ac): KEEP the node but
    // render it dimmed — the user asked for the real complete tree, nothing hidden.
    if (noise && !showAllFiles) continue;
    const isDir = node.type === 'directory';
    const isExpanded = isDir && expandedPaths.has(node.path);
    out.push({ node, depth, isExpanded, dimmed: noise });
    if (isExpanded && Array.isArray(node.children) && node.children.length > 0) {
      flatten(node.children, expandedPaths, showAllFiles, depth + 1, out);
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
  const { node, depth, isExpanded, dimmed } = row;
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
      forceDim={dimmed}
    />
  );
}

interface LibraryTreeProps {
  /** Root directory to browse (run_a75197d9). Default 'Knowledge' → the Library
   *  bookshelf (unchanged). A slashed path (e.g. 'Projects/SwarmAI') roots the tree
   *  at that subtree — loaded via expandDirectory (the backend tree endpoint has no
   *  per-subtree root, so a single-segment root is found in getTree while a nested
   *  root is fetched directly). */
  rootPath?: string;
  /** Optional file-open handler (run_a75197d9). DEFAULT (omitted) = dispatch the
   *  `swarm:open-file` document event directly (the Library bookshelf behavior,
   *  unchanged). A caller that must run side-effects first — e.g. Brain Hub closes
   *  its host overlay BEFORE the file opens in Canvas so the FileViewer isn't
   *  rendered UNDER the overlay — passes this and takes over the dispatch. */
  onFileOpen?: (path: string) => void;
  /** Show ALL files including infra/noise (run_cfb460ac). DEFAULT false = the
   *  Library bookshelf behavior (isNoiseNode hides .db/.lock/dotfiles/-archive.md).
   *  true = Brain Hub full-tree browse: the REAL complete tree, infra files kept
   *  but rendered DIMMED (never hidden). Per-instance so Library stays filtered. */
  showAllFiles?: boolean;
  /** Constrain the tree to a bounded left column instead of spanning full width
   *  (run_cfb460ac). A CSS max-width value (e.g. '420px'). DEFAULT undefined =
   *  full width (Library bookshelf, unchanged). Brain Hub passes a bound so the
   *  tree doesn't stretch across the whole overlay. */
  maxWidth?: string;
  /** Lower bound for the tree column width (run_4de3103f). A CSS min-width value
   *  (e.g. '320px') so deep DDD paths aren't truncated when maxWidth is a small %.
   *  DEFAULT undefined = no floor (Library bookshelf, unchanged). */
  minWidth?: string;
  /** Hug the tree to its CONTENT height instead of filling all available height
   *  (run_4de3103f). DEFAULT false = fill (Library bookshelf: the tree owns the
   *  whole panel). true = Brain Hub Browse: the List height becomes
   *  min(measuredAvailable, contentHeight) so a SHORT tree only takes its content
   *  height and a sibling below it (the Code Graph disclosure) hugs the tree
   *  instead of being pushed to the panel bottom. The container div KEEPS
   *  flex-1 min-h-0 (the ResizeObserver measure target — never 0), so a
   *  content-heavy tree still caps at the measured available height and scrolls
   *  internally; virtualization is never collapsed (Gate-0 skeptic hard constraint). */
  hugContent?: boolean;
}

/**
 * LibraryTree — expandable file tree rooted at `rootPath` (default 'Knowledge').
 * Self-contained state (no ExplorerContext): own tree roots + expanded-path set +
 * per-dir lazy load. Reused by both the Library bookshelf (Knowledge root) and the
 * Brain-Hub DDD detail (Projects/<name> root), with the noise filter (isNoiseNode)
 * hiding infra junk so a project root shows only real browsable content.
 */
export function LibraryTree({ rootPath = KNOWLEDGE_ROOT, onFileOpen, showAllFiles = false, maxWidth, minWidth, hugContent = false }: LibraryTreeProps = {}) {
  const [roots, setRoots] = useState<TreeNode[] | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  // hugContent is ACTIVE only when a parent exists to measure available height off
  // (REVIEW HIGH): with no parent we fall back to the fill layout so the box can't
  // lock to a low content-height. Set once in the measure effect after mount.
  const [hugActive, setHugActive] = useState(false);
  // Directories currently mid-lazy-load, so a double-toggle doesn't double-fetch.
  const loadingDirs = useRef<Set<string>>(new Set());
  // Measured height for react-window (overlay is fullscreen; height must be real).
  const containerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(400);
  // Display leaf of the root, for the copy strings (e.g. 'Knowledge' / 'SwarmAI').
  const rootLeaf = rootPath.split('/').filter(Boolean).pop() ?? rootPath;
  const isNested = rootPath.includes('/');

  const loadRoots = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      let children: TreeNode[];
      if (isNested) {
        // Nested root (e.g. Projects/<name>): no top-level node in getTree — fetch
        // its children directly. depth 2 → root's dirs + their immediate children.
        children = await workspaceService.expandDirectory(rootPath, 2);
      } else {
        // Single-segment root (Knowledge): find the top-level node in the full tree.
        const tree = await workspaceService.getTree(2);
        const rootNode = tree.find((n) => n.path === rootPath || n.name === rootPath);
        children = rootNode && Array.isArray(rootNode.children) ? rootNode.children : [];
      }
      setRoots(children);
    } catch {
      setError(true);
      setRoots(null);
    } finally {
      setLoading(false);
    }
  }, [rootPath, isNested]);

  useEffect(() => { void loadRoots(); }, [loadRoots]);

  // Measure AVAILABLE height for the virtualized List (ResizeObserver, fullscreen).
  // Guard on ResizeObserver presence — jsdom (test env) and older runtimes lack it;
  // there we fall back to a one-shot clientHeight read + the sane default.
  //
  // hugContent decoupling (run_4de3103f): the measure target must be the AVAILABLE
  // space, NOT the container's own box — because when hugContent shrinks the
  // container to listHeight, reading its own clientHeight would feed listHeight back
  // into `height`, and min(height, content) would lose its cap (a short tree would
  // pin `height` low forever). So we measure the PARENT's clientHeight (the flex-1
  // wrapper that owns the real available space) and observe it. Default (bookshelf)
  // still measures self — its container IS the flex-1 fill, so parent==self space.
  // useLayoutEffect (not useEffect): hugActive + the first measurement must be set
  // BEFORE the browser paints, else the tree renders once in the fill layout (400px)
  // then snaps to content height on the next frame — a visible flash (Gate-2 HIGH).
  // Layout-effect runs synchronously post-mutation, pre-paint, so the first painted
  // frame is already the hug layout.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // hugContent measures the PARENT (the flex-1 available space); if there is no
    // parent (detached/orphaned — should not happen in Brain Hub, but a reuse or a
    // test env could), fall back to measuring self. Observing self is safe: the
    // fallback below ALSO drops the content-height so `el` fills like the default,
    // so measuring `el` yields a real available height rather than locking low.
    const hugWithParent = hugContent && !!el.parentElement;
    setHugActive(hugWithParent);
    const measureEl = hugWithParent ? el.parentElement! : el;
    const measure = () => setHeight(Math.max(120, measureEl.clientHeight || 400));
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(measureEl);
    return () => ro.disconnect();
  }, [hugContent]);

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
    if (onFileOpen) { onFileOpen(node.path); return; }   // caller takes over (e.g. close-overlay-first)
    document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: node.path } }));
  }, [onFileOpen]);

  const rows = useMemo(() => (roots ? flatten(roots, expanded, showAllFiles) : []), [roots, expanded, showAllFiles]);

  const rowProps = useMemo<RowProps>(() => ({ rows, onToggle, onOpen }), [rows, onToggle, onOpen]);

  // List height. Default (bookshelf) = the full measured available height (the tree
  // owns the panel). hugContent (Brain Hub) = min(measuredAvailable, contentHeight):
  // a SHORT tree shrinks to its rows so a sibling below (Code Graph) hugs it; a TALL
  // tree still caps at `height` and scrolls internally (virtualization intact). The
  // measure target (`height`, from the flex-1 container) is unchanged either way —
  // hugContent only shrinks the List, never the measured value (Gate-0 constraint).
  const listHeight = hugActive ? Math.min(height, rows.length * ROW_HEIGHT) : height;

  // Container style: maxWidth/minWidth bound the column. When hugContent, the box
  // takes exactly listHeight (so a short tree hugs its rows and the sibling below
  // follows immediately); available height is measured off the parent instead.
  const containerStyle: React.CSSProperties = {
    ...(maxWidth ? { maxWidth } : {}),
    ...(minWidth ? { minWidth } : {}),
    ...(hugActive ? { height: listHeight, flexShrink: 0 } : {}),
  };
  // Default / no-parent fallback: flex-1 min-h-0 (fill the available space). hugActive
  // (hugContent WITH a parent to measure): content-sized box via inline height, so a
  // sibling below hugs the tree — no flex-1 (that fill is what pinned it), and no
  // min-h-0 either (REVIEW LOW: min-height:0 is meaningless on a non-flex box).
  const containerClass = hugActive ? '' : 'flex-1 min-h-0';

  return (
    <div ref={containerRef} data-testid="library-tree" className={containerClass} style={Object.keys(containerStyle).length ? containerStyle : undefined}>
      {error ? (
        <div
          data-testid="library-tree-error"
          className="rounded-lg border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-4 py-4 text-center"
        >
          <div className="text-sm text-[var(--color-text)]">Couldn't load the {rootLeaf} tree.</div>
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
          {/* Distinguish genuinely-empty from all-noise-filtered (meta-review LOW):
              a dir that HAS entries but all were infra-filtered isn't "empty" — say so,
              else the user reads "is empty" as a data-loss bug. */}
          {roots && roots.length > 0
            ? `Only build/infra files here — nothing to browse in ${rootLeaf}/.`
            : `${rootLeaf}/ is empty.`}
        </div>
      ) : (
        <List
          style={{ height: listHeight, width: '100%', overflow: 'auto' }}
          rowCount={rows.length}
          rowHeight={ROW_HEIGHT}
          rowComponent={LibraryRow}
          rowProps={rowProps}
          role="tree"
          aria-label={`${rootLeaf} file tree`}
        />
      )}
    </div>
  );
}

export default LibraryTree;
