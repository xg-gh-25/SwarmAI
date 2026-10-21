/**
 * ArtifactsOverlay — the left-nav "Artifacts" surface: a TIME-GROUPED retrieval list.
 *
 * run_cb127db1 (mockup v2 — Knowledge/Designs/2026-09-30-artifacts-overlay-v2-mock.html):
 * the overlay is a scannable RETRIEVAL LIST, not a thumbnail gallery. Opening Artifacts is a
 * retrieval task ("find the thing I made recently"), so the design follows Finder/VS-Code
 * recents: rows grouped by TIME (This week / Last week / Earlier-fold), zero faux thumbnails,
 * each row = extension badge + friendly title + full dir path + absolute timestamp.
 *
 *  - The active ROLE (default Deliverables) is the PRIMARY, shown time-grouped. Type chips
 *    switch the primary role. The other three roles are DEMOTED to collapsible drawers below.
 *  - A time-window chip defaults to "past 2 weeks" (This week + Last week shown; older rows
 *    fold into a "▸ N more" row). Typing in search IGNORES the window (matches across all time)
 *    — the retrieval intent "I know it exists" must not be blocked by recency.
 *  - ROLE is computed ONCE in the backend (product_registry.derive_role); this component
 *    renders it, never re-derives (run_4de279ca).
 *  - Still a pure SELECTOR (History's twin): a row click dispatches swarm:open-file (→ current-
 *    tab Canvas) then closes. Star/favorite is deferred to the next round.
 *
 * @exports ArtifactsContent
 * @exports timeBucket / groupByTime — pure time-bucketers (unit-tested)
 * @exports absoluteTime / relativeTime / dirDisplay / friendlyTitle / fileBadge — pure row helpers
 * @exports parentRunLabel — pure Pipeline-row fallback label
 * @exports OPEN_FILE_EVENT — the swarm:open-file event name (shared with the row-click contract)
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { radarService } from '../../services/radar';
import type { Product, ProductRole } from '../../services/radar';
import { fileIcon, fileIconColor } from '../../utils/fileUtils';

/** The window CustomEvent that opens a file in the current tab's Canvas.
 *  Same name LibraryOverlay/BrainHub dispatch — do NOT rename (proprioception contract). */
export const OPEN_FILE_EVENT = 'swarm:open-file';

/** Debounce for the filename filter (mirrors HistoryOverlay's search debounce feel). */
const SEARCH_DEBOUNCE_MS = 200;

/** loading-B poll: while the store is still empty (day-one backfill running in the
 *  background), re-fetch every POLL_MS so the products appear on their own — but stop
 *  after POLL_MAX_MS so a genuinely product-less workspace does not poll forever. */
const BACKFILL_POLL_MS = 2500;
const BACKFILL_POLL_MAX_MS = 45_000;

/** The backend ignores workspace_id (resolves the single workspace from DB config);
 *  a non-empty placeholder satisfies the required query param. */
const WS_PLACEHOLDER = 'default';

/** The 2-week default window (Earlier = older than this, folded). */
const WINDOW_DAYS = 14;

// ── Role model ──────────────────────────────────────────────────────────────
const ROLE_ORDER: ProductRole[] = ['Deliverables', 'Knowledge', 'Pipeline', 'Activity'];
const ROLE_LABEL: Record<ProductRole, string> = {
  Deliverables: 'Deliverables',
  Knowledge: 'Knowledge',
  Pipeline: 'Pipeline',
  Activity: 'Activity',
  Other: 'Other',
};
/** Per-role dot color for the filter chips (tracks the theme). */
const ROLE_DOT: Record<ProductRole, string> = {
  Deliverables: 'var(--color-primary)',
  Knowledge: 'var(--color-git-added, #4a9)',
  Pipeline: 'var(--color-git-modified, #d59a26)',
  Activity: 'var(--color-text-faint, #889)',
  Other: 'var(--color-text-faint, #889)',
};

// ── Time bucketing ──────────────────────────────────────────────────────────
export type TimeBucket = 'This week' | 'Last week' | 'Earlier';
const BUCKET_ORDER: TimeBucket[] = ['This week', 'Last week', 'Earlier'];

export interface TimeGroup {
  bucket: TimeBucket;
  products: Product[];
}

/**
 * Bucket a product by the age of its lastTouched vs `now`:
 * <7d → This week, 7–14d → Last week, >14d → Earlier. An unparseable/missing date
 * falls into Earlier (AC6 — a row is NEVER dropped for a bad date). Pure.
 */
export function timeBucket(iso: string, now: number = Date.now()): TimeBucket {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return 'Earlier';
  const days = (now - ts) / 86400000;
  if (days < 7) return 'This week';
  if (days < WINDOW_DAYS) return 'Last week';
  return 'Earlier';
}

/**
 * Group products into the fixed time buckets, newest (lastTouched) first within each,
 * returning only non-empty buckets in display order. Pure. `now` injectable for tests.
 */
export function groupByTime(products: Product[], now: number = Date.now()): TimeGroup[] {
  const buckets: Record<TimeBucket, Product[]> = { 'This week': [], 'Last week': [], Earlier: [] };
  for (const p of products) buckets[timeBucket(p.lastTouched, now)].push(p);
  const byNewest = (a: Product, b: Product) =>
    (b.lastTouched || '').localeCompare(a.lastTouched || '');
  return BUCKET_ORDER.filter((b) => buckets[b].length > 0).map((b) => ({
    bucket: b,
    products: [...buckets[b]].sort(byNewest),
  }));
}

// ── Row helpers ─────────────────────────────────────────────────────────────
/** basename of a workspace-relative path. */
function baseName(path: string): string {
  const i = path.lastIndexOf('/');
  return i >= 0 ? path.slice(i + 1) : path;
}

/**
 * AC4: absolute local timestamp `YYYY-MM-DD HH:MM` — the primary display (XG asked for a
 * real timestamp, not "2h"). Unparseable → '' (a row must never crash on a bad date).
 */
export function absoluteTime(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Relative "time ago" — demoted to the row's hover title. Unparseable → ''. Pure. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return '';
  const diff = now - ts;
  if (diff < 0) return 'now';
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'now';
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d`;
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Max characters before the dir path is elided with a leading …/ (AC4). */
const DIR_MAX = 40;
/**
 * AC4: directory display — the FULL dir path by default; only when it exceeds DIR_MAX
 * does it elide the front with `…/`, keeping the deepest (most identifying) segments.
 * `Projects/AIDLC/assets/deck.html` → `Projects/AIDLC/assets` (full). A deep path →
 * `…/<deepest segments that fit>`. A bare filename → ''. Pure.
 */
export function dirDisplay(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  const dirs = parts.slice(0, -1); // drop the filename
  if (dirs.length === 0) return '';
  const full = dirs.join('/');
  if (full.length <= DIR_MAX) return full;
  // Elide from the front: keep the deepest segments that fit under DIR_MAX (with the "…/").
  const kept: string[] = [];
  for (let i = dirs.length - 1; i >= 0; i--) {
    const candidate = [dirs[i], ...kept];
    if (`…/${candidate.join('/')}`.length > DIR_MAX && kept.length > 0) break;
    kept.unshift(dirs[i]);
  }
  return `…/${kept.join('/')}`;
}

/**
 * AC4: a human-friendly title from the filename — strip a leading YYYY-MM-DD(-) date,
 * strip ONLY the last extension, de-slug [-_] to spaces, collapse + title-case. Falls
 * back to the raw basename if empty (all-date name), so a row never renders blank. Pure.
 */
export function friendlyTitle(name: string): string {
  const base = baseName(name);
  if (!base) return '';
  const noDate = base.replace(/^\d{4}-\d{2}-\d{2}-?/, '');
  const noExt = noDate.replace(/\.[^.]+$/, '');
  const words = noExt.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').replace(/^[.\s]+|[.\s]+$/g, '').trim();
  if (!words) return base;
  return words.replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * AC4: a compact extension-typed badge (DECK/IMG/PDF/MD/HTML/DOCX/DOC) + its color.
 * ORDERED — deck wins for a .pptx / Pollinate / `-deck` path (a content-package deck,
 * deliberate). `.md` renders MD (SwarmAI ships lots of markdown: DDD/weeklies/designs).
 * Defaults to DOC (never empty). Pure.
 */
export function fileBadge(path: string): { badge: string; color: string; kind: string } {
  const p = path.replace(/\\/g, '/').toLowerCase();
  const ext = p.match(/\.([a-z0-9]+)$/)?.[1] ?? '';
  if (['pptx', 'ppt', 'key'].includes(ext) || /\/(deck|pollinate)\b/.test(p) || p.includes('-deck'))
    return { badge: 'DECK', color: 'var(--color-git-modified, #d59a26)', kind: 'deck' };
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'avif'].includes(ext))
    return { badge: 'IMG', color: '#b06fd0', kind: 'image' };
  if (ext === 'pdf') return { badge: 'PDF', color: '#e5484d', kind: 'pdf' };
  if (ext === 'md' || ext === 'markdown') return { badge: 'MD', color: '#66cc99', kind: 'md' };
  if (['html', 'htm'].includes(ext)) return { badge: 'HTML', color: 'var(--color-git-added, #4bb58a)', kind: 'html' };
  if (['docx', 'doc'].includes(ext)) return { badge: 'DOCX', color: '#5b8def', kind: 'docx' };
  return { badge: 'DOC', color: 'var(--color-text-muted, #aab2c0)', kind: 'doc' };
}

/**
 * Label a Pipeline REPORT.md row by its parent run dir when no server displayLabel is
 * present — `.../.artifacts/runs/run_x/REPORT.md` → "run_x". Falls back to basename. Pure.
 */
export function parentRunLabel(path: string): string {
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
  const i = parts.lastIndexOf('runs');
  if (i >= 0 && i + 1 < parts.length) return parts[i + 1];
  return baseName(path);
}

/**
 * The visible label for a row. Pipeline uses the server displayLabel (a task name, not the
 * bare "REPORT.md"), falling back to the parent run dir. Every OTHER role uses the friendly
 * title derived from the filename — the backend's displayLabel for those is just the basename
 * (not friendly), and the mockup wants the readable title (e.g. "Ai Native Deck").
 */
function rowLabel(p: Product): string {
  if (p.role === 'Pipeline') return p.displayLabel || parentRunLabel(p.path);
  return friendlyTitle(p.path);
}

/** Open a file in the current tab's Canvas (same contract as LibraryOverlay). */
export function dispatchOpenFile(path: string): void {
  document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path } }));
}

export interface ArtifactsContentProps {
  /** Host-owned close — called after a row opens a file (return to chat/Canvas). */
  close: () => void;
  /** Test seam: override the fetch (defaults to radarService.fetchProducts). */
  fetchProducts?: (wsId: string) => Promise<Product[]>;
  /** Test seam: inject "now" so time buckets are deterministic. */
  now?: number;
  /** Test seam: override the star-toggle call (defaults to radarService.setStarred). */
  setStarred?: (wsId: string, path: string, starred: boolean) => Promise<void>;
}

/** react-query key for the products list — shared by the fetch + the optimistic star patch. */
const PRODUCTS_QUERY_KEY = ['workspace-products'] as const;

/**
 * The Artifacts retrieval body (the host wraps it in scrim + panel + header chrome).
 */
export function ArtifactsContent({ close, fetchProducts, now, setStarred }: ArtifactsContentProps) {
  const fetcher = fetchProducts ?? radarService.fetchProducts;
  const starrer = setStarred ?? radarService.setStarred;
  const nowMs = now ?? Date.now();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } = useQuery({
    queryKey: PRODUCTS_QUERY_KEY,
    queryFn: () => fetcher(WS_PLACEHOLDER),
    staleTime: 30_000,
    refetchInterval: (query) => {
      const rows = query.state.data as Product[] | undefined;
      if (rows && rows.length > 0) return false;
      const firstFetch = query.state.dataUpdatedAt || Date.now();
      if (Date.now() - firstFetch > BACKFILL_POLL_MAX_MS) return false;
      return BACKFILL_POLL_MS;
    },
  });

  const [searchRaw, setSearchRaw] = useState('');
  const [search, setSearch] = useState('');
  // The PRIMARY role shown time-grouped (default Deliverables — the products you reopen).
  const [primaryRole, setPrimaryRole] = useState<ProductRole>('Deliverables');
  // Starred-only view (the ★ Starred chip). A SEPARATE filter axis from primaryRole:
  // when on, it flattens ALL starred products across every role AND ignores the time
  // window (a favorite is retrieved regardless of recency — the 3rd retrieval path).
  const [starredOnly, setStarredOnly] = useState(false);
  // Earlier-fold expanded?
  const [foldOpen, setFoldOpen] = useState(false);
  // Which demoted drawers are open.
  const [openDrawers, setOpenDrawers] = useState<Record<string, boolean>>({});

  // Optimistic star toggle: patch the cached row immediately (Doherty — instant feel),
  // fire the PUT, invalidate to reconcile, and roll back the patch on error.
  // Derives `next` from the LIVE cache row (not the closed-over `p` prop) so two rapid
  // clicks before the server reconciles don't both compute off a stale pre-patch render
  // (UX-review LOW: read live state, never a snapshot — RP57/RP58-adjacent).
  const toggleStar = useCallback(
    async (p: Product) => {
      const prev = queryClient.getQueryData<Product[]>(PRODUCTS_QUERY_KEY);
      const liveRow = prev?.find((r) => r.path === p.path);
      const next = !((liveRow ?? p).starred ?? false);
      queryClient.setQueryData<Product[]>(PRODUCTS_QUERY_KEY, (rows) =>
        (rows ?? []).map((r) => (r.path === p.path ? { ...r, starred: next } : r)),
      );
      try {
        await starrer(WS_PLACEHOLDER, p.path, next);
        queryClient.invalidateQueries({ queryKey: PRODUCTS_QUERY_KEY });
      } catch {
        // roll back the optimistic patch on failure
        if (prev) queryClient.setQueryData<Product[]>(PRODUCTS_QUERY_KEY, prev);
      }
    },
    [queryClient, starrer],
  );

  const starredCount = useMemo(
    () => (Array.isArray(data) ? data.filter((p) => p.starred).length : 0),
    [data],
  );

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchRaw.trim().toLowerCase()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchRaw]);

  const searching = search.length > 0;

  const all = useMemo(() => (Array.isArray(data) ? data : []), [data]);

  // Search matches the VISIBLE label + path, across ALL roles and ALL time (window ignored).
  const searchMatch = (p: Product): boolean => {
    if (!search) return true;
    const hay = `${baseName(p.path)} ${rowLabel(p)}`.toLowerCase();
    return hay.includes(search);
  };

  // PRIMARY rows. In starred-only view: ALL starred products across every role, window
  // ignored (a favorite crosses time). Otherwise: the primary role's rows (search still
  // matches across all time; else time-grouped with a foldable Earlier bucket).
  const primaryProducts = useMemo(
    () =>
      starredOnly
        ? all.filter((p) => p.starred && searchMatch(p))
        : all.filter((p) => p.role === primaryRole && searchMatch(p)),
    [all, primaryRole, starredOnly, search],
  );
  const primaryGroups = useMemo(() => groupByTime(primaryProducts, nowMs), [primaryProducts, nowMs]);

  // Demoted drawers = the other three roles — HIDDEN in starred-only view (the starred
  // list is a flat cross-role view, drawers would be redundant/confusing there).
  const drawerRoles = starredOnly ? [] : ROLE_ORDER.filter((r) => r !== primaryRole);
  const drawerProducts = (role: ProductRole) =>
    all
      .filter((p) => p.role === role && searchMatch(p))
      .sort((a, b) => (b.lastTouched || '').localeCompare(a.lastTouched || ''));

  // Empty-state decision. In starred-only view it is scoped to starred rows (so 0 starred
  // shows the star guide, not the generic "no products").
  const totalShown = starredOnly
    ? primaryProducts.length
    : all.filter(searchMatch).length;

  const openRow = (p: Product) => {
    dispatchOpenFile(p.path);
    close();
  };

  // Split the primary groups into the WINDOW (This week + Last week) vs Earlier (folded).
  // Searching OR starred-only both IGNORE the window (show everything across all time).
  const ignoreWindow = searching || starredOnly;
  const windowGroups = ignoreWindow
    ? primaryGroups
    : primaryGroups.filter((g) => g.bucket !== 'Earlier');
  const earlierGroup = ignoreWindow ? undefined : primaryGroups.find((g) => g.bucket === 'Earlier');
  const earlierCount = earlierGroup?.products.length ?? 0;

  return (
    <div className="flex flex-col h-full min-h-0" data-testid="artifacts-overlay">
      {/* Search + role chips + time-window chip */}
      <div className="px-3 pt-3 pb-2 shrink-0">
        <input
          type="text"
          value={searchRaw}
          onChange={(e) => setSearchRaw(e.target.value)}
          placeholder="Search all artifacts (ignores the time window)…"
          data-testid="artifacts-search"
          className="w-full px-3 py-2 rounded-lg bg-[var(--color-input-bg,var(--color-bg-secondary))] border border-[var(--color-border)] text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none focus:border-[var(--color-primary)]"
        />
        <div className="flex items-center gap-1.5 mt-2 flex-wrap" data-testid="artifacts-rolefilter">
          {/* ★ Starred chip — FIRST (mockup): a separate filter axis. Click toggles a
              starred-only, cross-role, window-ignoring view. Clicking it OFF returns to
              the role view. */}
          <button
            onClick={() => setStarredOnly((v) => !v)}
            data-testid="artifacts-chip-Starred"
            aria-pressed={starredOnly}
            className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
              starredOnly
                ? 'border-[var(--color-git-modified,#d59a26)] text-[var(--color-git-modified,#d59a26)] bg-[color-mix(in_srgb,var(--color-git-modified,#d59a26)_14%,transparent)]'
                : 'border-[var(--color-git-modified,#d59a26)] text-[var(--color-git-modified,#d59a26)] hover:bg-[color-mix(in_srgb,var(--color-git-modified,#d59a26)_8%,transparent)]'
            }`}
          >
            <span aria-hidden="true">★</span> Starred · {starredCount}
          </button>
          {ROLE_ORDER.map((role) => {
            const on = !starredOnly && primaryRole === role;
            return (
              <button
                key={role}
                onClick={() => {
                  setStarredOnly(false);
                  setPrimaryRole(role);
                }}
                data-testid={`artifacts-chip-${role}`}
                aria-pressed={on}
                className={`flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                  on
                    ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_12%,transparent)]'
                    : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                }`}
              >
                <span
                  aria-hidden="true"
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ backgroundColor: ROLE_DOT[role] }}
                />
                {ROLE_LABEL[role]}
              </button>
            );
          })}
          {/* time-window chip (right-aligned) */}
          <span
            data-testid="artifacts-window-chip"
            className={`ml-auto text-[11px] px-2.5 py-1 rounded-full border border-[var(--color-border)] ${
              ignoreWindow ? 'text-[var(--color-text-faint)] opacity-60' : 'text-[var(--color-text-muted)]'
            }`}
          >
            {starredOnly ? '时间窗 · 收藏时忽略' : searching ? '时间窗 · 搜索时忽略' : '时间窗 · 过去两周'}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-4" data-testid="artifacts-list">
        {isLoading ? (
          <div className="flex flex-col gap-1 px-1 py-3" data-testid="artifacts-loading">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-9 rounded-md bg-[var(--color-hover)] animate-pulse" />
            ))}
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center gap-2 px-3 py-10 text-center" data-testid="artifacts-error">
            <span className="text-[22px]" aria-hidden="true">🐝</span>
            <p className="text-[11px] text-[var(--color-text-muted)]">Couldn't load products. Try again in a moment.</p>
          </div>
        ) : totalShown === 0 && starredOnly ? (
          <div className="flex flex-col items-center justify-center gap-2 px-3 py-10 text-center" data-testid="artifacts-starred-empty">
            <span className="text-[22px]" aria-hidden="true">★</span>
            <p className="text-[11px] text-[var(--color-text-muted)] max-w-[240px]">
              No favorites yet. Hover any row and click ☆ to star the products you reopen often — they show here across all time.
            </p>
          </div>
        ) : totalShown === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-3 py-10 text-center" data-testid="artifacts-empty">
            <span className="text-[22px]" aria-hidden="true">🐝</span>
            <p className="text-[11px] text-[var(--color-text-muted)] max-w-[240px]">
              {all.length === 0
                ? 'No products yet. Decks, reports, and files you create show up here.'
                : 'No products match your search.'}
            </p>
          </div>
        ) : (
          <div className="flex flex-col">
            {/* PRIMARY: time-grouped list of the active role */}
            {windowGroups.map((g) => (
              <section key={g.bucket} data-testid={`artifacts-timegroup-${g.bucket.replace(/\s/g, '-')}`}>
                <div className="flex items-center gap-2 px-1 pt-4 pb-1.5 text-[11px] uppercase tracking-wide text-[var(--color-text-faint)]">
                  <span>{g.bucket}</span>
                  <span className="opacity-70">· {g.products.length}</span>
                  <span className="flex-1 h-px bg-[var(--color-border)] opacity-40" />
                </div>
                {g.products.map((p) => (
                  <ArtifactRow key={p.path} p={p} now={nowMs} onOpen={openRow} onStar={toggleStar} />
                ))}
              </section>
            ))}

            {/* Earlier fold (hidden while searching) */}
            {!ignoreWindow && earlierCount > 0 && (
              foldOpen ? (
                <section data-testid="artifacts-timegroup-Earlier">
                  <div className="flex items-center gap-2 px-1 pt-4 pb-1.5 text-[11px] uppercase tracking-wide text-[var(--color-text-faint)]">
                    <span>Earlier</span>
                    <span className="opacity-70">· {earlierCount}</span>
                    <span className="flex-1 h-px bg-[var(--color-border)] opacity-40" />
                  </div>
                  {earlierGroup!.products.map((p) => (
                    <ArtifactRow key={p.path} p={p} now={nowMs} onOpen={openRow} onStar={toggleStar} />
                  ))}
                </section>
              ) : (
                <button
                  data-testid="artifacts-fold"
                  onClick={() => setFoldOpen(true)}
                  className="mt-3 mx-1 flex items-center gap-2 px-3 py-2 rounded-lg border border-dashed border-[var(--color-border)] text-[12px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors text-left"
                >
                  <span className="text-[var(--color-text-faint)]">▸</span>
                  Earlier · <b className="text-[var(--color-text)] font-medium">{earlierCount}</b> more · 点击展开
                </button>
              )
            )}

            {/* DEMOTED drawers: the non-primary roles */}
            <div className="mt-4 border-t border-[var(--color-border)] opacity-100">
              {drawerRoles.map((role) => {
                const items = drawerProducts(role);
                if (items.length === 0) return null;
                const open = !!openDrawers[role];
                return (
                  <section key={role} data-testid={`artifacts-drawer-${role}`}>
                    <button
                      onClick={() => setOpenDrawers((d) => ({ ...d, [role]: !open }))}
                      aria-expanded={open}
                      data-testid={`artifacts-drawer-toggle-${role}`}
                      className="w-full flex items-center gap-2 px-1 py-2.5 text-left text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                    >
                      <span
                        className="material-symbols-outlined text-[15px] leading-none text-[var(--color-text-faint)] transition-transform"
                        style={{ transform: open ? 'none' : 'rotate(-90deg)' }}
                        aria-hidden="true"
                      >
                        expand_more
                      </span>
                      <span className="text-[12.5px]">{ROLE_LABEL[role]}</span>
                      <span className="ml-auto text-[var(--color-text-faint)] text-[11px]">{items.length}</span>
                    </button>
                    {open && (
                      <div className="flex flex-col pb-1">
                        {items.map((p) => (
                          <ArtifactRow key={p.path} p={p} now={nowMs} onOpen={openRow} onStar={toggleStar} />
                        ))}
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── A single list row (mockup .row) ─────────────────────────────────────────
// The row is a <div role="button"> (NOT a <button>) so the star control can be a real
// nested <button> — nesting a button inside a button is invalid HTML. The star's onClick
// calls stopPropagation so a star toggle never also opens the file (RP20 nested-clickable).
function ArtifactRow({
  p,
  now,
  onOpen,
  onStar,
}: {
  p: Product;
  now: number;
  onOpen: (p: Product) => void;
  onStar: (p: Product) => void;
}) {
  const { badge, color } = fileBadge(p.path);
  const label = rowLabel(p);
  const starred = p.starred ?? false;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(p)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen(p);
        }
      }}
      title={p.path}
      data-testid="artifacts-row"
      className="group w-full flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-[var(--color-hover)] transition-colors text-left cursor-pointer"
    >
      <span
        data-testid="artifacts-row-badge"
        className="shrink-0 w-[42px] text-center text-[8.5px] font-bold tracking-wide py-[3px] rounded-[5px] text-black leading-none"
        style={{ backgroundColor: color }}
      >
        {badge}
      </span>
      <span className="flex-1 min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="text-[13px] font-medium text-[var(--color-text)] truncate leading-tight">{label}</span>
          {p.gitignored && (
            <span
              data-testid="artifacts-gitignored-badge"
              className="shrink-0 text-[8.5px] px-1 py-px rounded bg-[var(--color-git-modified,#d59a26)]/20 text-[var(--color-git-modified,#d59a26)] font-medium leading-none"
              title="Not in git (local-only) — surfaced anyway"
            >
              local
            </span>
          )}
        </span>
        <span className="block text-[11px] text-[var(--color-text-faint)] truncate leading-tight mt-0.5">
          {dirDisplay(p.path)}
        </span>
      </span>
      <span
        data-testid="artifacts-row-time"
        className="shrink-0 text-[11px] text-[var(--color-text-muted)] tabular-nums text-right min-w-[118px] leading-tight"
        title={relativeTime(p.lastTouched, now)}
      >
        {absoluteTime(p.lastTouched)}
      </span>
      {/* Star toggle — hover-visible when unstarred, always-lit gold when starred (Tufte
          deference). stopPropagation so a star click never also opens the file (RP20). */}
      <button
        type="button"
        data-testid="artifacts-row-star"
        aria-pressed={starred}
        aria-label={starred ? 'Unstar' : 'Star'}
        title={starred ? 'Starred' : 'Star'}
        onClick={(e) => {
          e.stopPropagation();
          onStar(p);
        }}
        className={`shrink-0 w-5 text-center text-[14px] leading-none transition-opacity ${
          starred
            ? 'text-[var(--color-git-modified,#d59a26)] opacity-100'
            : 'text-[var(--color-text-faint)] opacity-0 group-hover:opacity-100 hover:text-[var(--color-git-modified,#d59a26)]'
        }`}
      >
        {starred ? '★' : '☆'}
      </button>
    </div>
  );
}

// Keep fileIcon/fileIconColor imported for potential row-icon use; referenced here to avoid
// an unused-import error while the row uses the extension badge instead of a file icon.
void fileIcon;
void fileIconColor;
