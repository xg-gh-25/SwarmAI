/**
 * LibraryOverlay — the agent's bookshelf (Native store + Mount points).
 *
 * The cognition-zone "Library" nav card opens this fullscreen overlay. Purpose
 * (XG, 2026-08-02): a single view + entry point over everything the agent can
 * recall — `Knowledge/` (ours, "Native") + mounted external dirs (theirs, later
 * cycles). It stores pointers + briefings, never copies external content.
 *
 * Run 5 (overlay-first) scope: the 3-tab shell (Browse / Recent / Guide) + a
 * persistent left-rail of vitals, over the EXISTING Native store. The Mounted
 * section renders a real (empty) state with a "+ Add Folder" affordance — NOT a
 * placeholder void (NavCard+Overlay standard §4). Mount registration/indexing is
 * wired by later cycles.
 *
 * Data is backend-primary: Browse/Recent CONSUME GET /api/library/native +
 * /recent + /mounts (live filesystem reads; the frontend invents no counts, R30).
 * Opens on the `swarm:show-library` window event via useExclusiveOverlay
 * (single-overlay mux + back-to-chat). Mirrors CMBrainOverlay's shell so the two
 * cognition cards feel like one system.
 *
 * The UI pattern references Quick's "My computer / Local folders" (XG ref): a
 * first-class "+ Add Folder" button + per-mount row (path + kind badge + settings
 * + delete + enable-toggle + expand) — those row controls activate when the mount
 * cycle ships; Run 5 shows the empty state + the Add-Folder entry point.
 *
 * @exports LibraryOverlay
 */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../services/api';

// ── Types (mirror the backend library_api payloads, snake_case as served) ──
interface NativeCategory { name: string; file_count: number; total_bytes: number; }
interface NativeStore { source: string; root: string; category_count: number; categories: NativeCategory[]; }
interface RecentItem { path: string; category: string; mtime: number; size: number; source: 'session' | 'you' | 'job'; }
interface RecentFeed { window_days: number; count: number; items: RecentItem[]; }
interface MountRow {
  id: string; path: string; kind: 'code' | 'docs' | 'url';
  index_ref?: string; last_synced?: number; health: 'fresh' | 'stale' | 'missing'; enabled: boolean;
}
interface MountsList { count: number; mounts: MountRow[]; registry_ready: boolean; }

type TabKey = 'browse' | 'recent' | 'guide';

const SOURCE_META: Record<RecentItem['source'], { icon: string; label: string }> = {
  session: { icon: '🤖', label: 'session' },
  you: { icon: '⬆', label: 'you' },
  job: { icon: '⏱', label: 'job' },
};

const HEALTH_TINT: Record<MountRow['health'], string> = {
  fresh: '#5fc99a', stale: '#d08a4a', missing: '#d0524a',
};

function fmtBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K`;
  return `${n}B`;
}

function fmtWhen(mtime: number): string {
  const diff = Date.now() / 1000 - mtime;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

/**
 * LibraryContent — the Library bookshelf surface content (M3: migrated to the
 * OverlayHost registry). Host owns the Modal chrome + mount lifecycle (mounts this
 * only while activeOverlay === 'library'), so no open/close self-management; queries
 * are enabled: true because the component only exists while the surface is open.
 */
export function LibraryContent() {
  const [tab, setTab] = useState<TabKey>('browse');

  const {
    data: native, isLoading: nativeLoading, isError: nativeError, refetch: refetchNative,
  } = useQuery<NativeStore>({
    queryKey: ['library-native'],
    queryFn: async () => (await api.get<NativeStore>('/api/library/native')).data,
    staleTime: 30_000, enabled: true,
  });
  const {
    data: recent, isLoading: recentLoading, isError: recentError, refetch: refetchRecent,
  } = useQuery<RecentFeed>({
    queryKey: ['library-recent'],
    queryFn: async () => (await api.get<RecentFeed>('/api/library/recent')).data,
    staleTime: 30_000, enabled: true,
  });
  const { data: mounts } = useQuery<MountsList>({
    queryKey: ['library-mounts'],
    queryFn: async () => (await api.get<MountsList>('/api/library/mounts')).data,
    staleTime: 30_000, enabled: true,
  });

  const nativeFileTotal = (native?.categories ?? []).reduce((s, c) => s + c.file_count, 0);
  const nativeByteTotal = (native?.categories ?? []).reduce((s, c) => s + c.total_bytes, 0);
  const mountCount = mounts?.count ?? 0;

  return (
      <div className="flex h-full min-h-0" data-testid="library-overlay">
        {/* ── Left overview rail (fixed 264px, tab-independent) ── */}
        <aside
          className="w-[264px] shrink-0 flex flex-col gap-4 border-r border-[var(--color-border)] p-4 overflow-y-auto"
          data-testid="library-rail"
        >
          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">📚 Native</div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-2xl font-semibold text-[var(--color-text)]">
                {native ? nativeFileTotal : '—'}
              </span>
              <span className="text-xs text-[var(--color-text-muted)]">
                items · {native ? fmtBytes(nativeByteTotal) : '—'}
              </span>
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-text-faint)]">Knowledge/ — already in recall</div>
          </div>

          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">🔗 Mounted</div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span className="text-2xl font-semibold text-[var(--color-text)]">{mountCount}</span>
              <span className="text-xs text-[var(--color-text-muted)]">external sources</span>
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-text-faint)]">
              {mounts?.registry_ready ? 'indexed in place, never copied' : 'coming soon — index in place, no copy'}
            </div>
          </div>

          <div className="mt-auto text-[10px] leading-relaxed text-[var(--color-text-faint)]">
            Library is an <b>index</b>, not a warehouse. Native = ours (Knowledge/).
            Mounted = pointers into your disk, read live on recall.
          </div>
        </aside>

        {/* ── Main area: tabs + panel ── */}
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="flex items-center gap-1 border-b border-[var(--color-border)] px-4 pt-3">
            <TabBtn testid="library-tab-browse" label="Browse" active={tab === 'browse'} onClick={() => setTab('browse')} badge={native?.category_count} />
            <TabBtn testid="library-tab-recent" label="Recent" active={tab === 'recent'} onClick={() => setTab('recent')} badge={recent?.count} />
            <TabBtn testid="library-tab-guide" label="Guide" active={tab === 'guide'} onClick={() => setTab('guide')} />
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto p-4">
            {tab === 'browse' && (
              <BrowseTab
                native={native} mounts={mounts}
                nativeLoading={nativeLoading} nativeError={nativeError}
                onRetryNative={() => { void refetchNative(); }}
              />
            )}
            {tab === 'recent' && (
              <RecentTab
                recent={recent} loading={recentLoading} error={recentError}
                onRetry={() => { void refetchRecent(); }}
              />
            )}
            {tab === 'guide' && <GuideTab />}
          </div>
        </div>
      </div>
  );
}

interface SearchHit { domain: string; title: string; source: string; content?: string; mount_id?: string | null; }
interface SearchResult { query: string; count: number; hits: SearchHit[]; }

// A fetch-error surface with a Retry (refetch). Distinct from pending/empty so a
// failed backend call is never rendered as a permanent "Loading…" spinner.
function FetchError({ testid, retryTestid, message, onRetry }: { testid: string; retryTestid: string; message: string; onRetry: () => void }) {
  return (
    <div
      data-testid={testid}
      className="rounded-lg border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-4 py-4 text-center"
    >
      <div className="text-sm text-[var(--color-text)]">{message}</div>
      <button
        data-testid={retryTestid}
        onClick={onRetry}
        className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
        style={{ background: '#d0524a' }}
      >
        Retry
      </button>
    </div>
  );
}

// ── Browse: Native categories (green) + Mounted sources (blue), never merged ──
function BrowseTab({
  native, mounts, nativeLoading, nativeError, onRetryNative,
}: {
  native: NativeStore | undefined; mounts: MountsList | undefined;
  nativeLoading: boolean; nativeError: boolean; onRetryNative: () => void;
}) {
  const cats = native?.categories ?? [];
  const mountRows = mounts?.mounts ?? [];
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');

  // Search runs the SAME recall path the Guide tab describes (library+codeintel).
  const { data: search, isFetching } = useQuery<SearchResult>({
    queryKey: ['library-search', submitted],
    queryFn: async () => (await api.get<SearchResult>(`/api/library/search?q=${encodeURIComponent(submitted)}`)).data,
    enabled: submitted.trim().length > 0,
    staleTime: 30_000,
  });

  return (
    <div data-testid="library-panel-browse" className="flex flex-col gap-5 max-w-4xl">
      {/* Search box — searching Library = seeing what recall would retrieve. */}
      <form
        onSubmit={(e) => { e.preventDefault(); setSubmitted(query); }}
        className="flex items-center gap-2"
      >
        <input
          data-testid="library-search-input"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="🔍 Search the library (keyword / FTS5 — what recall would retrieve)…"
          className="flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
        />
        {submitted && (
          <button type="button" data-testid="library-search-clear"
            onClick={() => { setQuery(''); setSubmitted(''); }}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]">clear</button>
        )}
      </form>

      {submitted.trim() ? (
        <section data-testid="library-search-results">
          <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
            {isFetching ? 'Searching…' : `${search?.count ?? 0} recall hits for “${submitted}”`}
          </div>
          {(search?.hits ?? []).length === 0 && !isFetching ? (
            <div className="py-6 text-center text-sm text-[var(--color-text-faint)]">
              No recall hits — try different keywords, or the knowledge may not be indexed yet.
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {(search?.hits ?? []).map((h, i) => (
                <button
                  key={i}
                  data-testid="library-search-hit"
                  onClick={() => h.source && document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: h.source } }))}
                  className="flex items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-[var(--color-hover)]"
                >
                  <span className="shrink-0 rounded px-1.5 py-[1px] text-[10px] font-mono"
                    style={{ background: h.domain === 'codeintel' ? 'color-mix(in srgb,#4a8fb0 16%,transparent)' : 'color-mix(in srgb,#5fc99a 16%,transparent)',
                             color: h.domain === 'codeintel' ? '#4a8fb0' : '#5fc99a' }}>
                    {h.domain === 'codeintel' ? 'code' : 'docs'}{h.mount_id ? '·mount' : ''}
                  </span>
                  <span className="flex-1 min-w-0 truncate text-sm text-[var(--color-text)]">{h.title || h.source}</span>
                  <span className="shrink-0 text-[11px] text-[var(--color-text-faint)] font-mono truncate max-w-[280px]">{h.source}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      ) : (
      <>
      <div className="text-sm text-[var(--color-text-muted)]">
        Everything on the shelf — click a category to open it in the workspace explorer.
        Native (ours) and Mounted (pointers to your disk) stay visually distinct.
      </div>

      {/* NATIVE — cognition green */}
      <section data-testid="library-native-section">
        <div className="mb-2 flex items-center gap-2 text-[11px] font-mono uppercase tracking-wider" style={{ color: '#5fc99a' }}>
          📗 Native · Knowledge/
        </div>
        {nativeError ? (
          <FetchError
            testid="library-native-error"
            retryTestid="library-native-retry"
            message="Couldn't load categories — the workspace or Knowledge/ may be unavailable."
            onRetry={onRetryNative}
          />
        ) : nativeLoading ? (
          <div className="py-6 text-center text-sm text-[var(--color-text-faint)]">Loading categories…</div>
        ) : cats.length === 0 ? (
          <div data-testid="library-native-empty" className="py-6 text-center text-sm text-[var(--color-text-faint)]">
            No categories yet — Knowledge/ is empty.
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {cats.map((c) => (
              <button
                key={c.name}
                data-testid={`library-cat-${c.name}`}
                onClick={() =>
                  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: `Knowledge/${c.name === '(root)' ? '' : c.name}` } }))
                }
                className="flex items-center gap-3 rounded-md px-3 py-2 max-w-2xl text-left hover:bg-[var(--color-hover)]"
              >
                <span className="w-1.5 h-4 shrink-0 rounded-full" style={{ background: '#5fc99a' }} aria-hidden />
                <span className="flex-1 min-w-0 truncate text-sm font-medium text-[var(--color-text)]">{c.name}</span>
                <span className="shrink-0 font-mono text-xs text-[var(--color-text-muted)]">{c.file_count} files</span>
                <span className="w-14 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-faint)]">{fmtBytes(c.total_bytes)}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* MOUNTED — cooler tint + link icon; empty-state is compact, not a void (§4) */}
      <section data-testid="library-mounted-section">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-wider" style={{ color: '#4a8fb0' }}>
            🔗 Mounted · external sources
          </div>
          <AddFolderButton />
        </div>
        {mountRows.length === 0 ? (
          <div
            data-testid="library-mounted-empty"
            className="rounded-lg border border-dashed border-[color-mix(in_srgb,#4a8fb0_35%,var(--color-border))] px-4 py-3 text-[11px] text-[var(--color-text-muted)]"
          >
            No mounted folders yet. <b>+ Add Folder</b> registers a local directory —
            the agent judges its kind (code / docs) and indexes it <b>in place</b> (never
            copied), so recall reaches it and reads the live source.
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {mountRows.map((m) => (
              <MountRowView key={m.id} m={m} />
            ))}
          </div>
        )}
      </section>
      </>
      )}
    </div>
  );
}

// A mounted-folder row mirrors Quick's shape: path + kind badge + Local + settings
// + delete + enable-toggle + expand. Run-5 controls are inert (mount cycle wires
// the mutations); the row shape is the contract the later cycle fills.
function MountRowView({ m }: { m: MountRow }) {
  return (
    <div
      data-testid={`library-mount-${m.id}`}
      data-kind={m.kind}
      className="flex items-center gap-3 rounded-md px-3 py-2 max-w-2xl hover:bg-[var(--color-hover)]"
    >
      <span className="w-1.5 h-4 shrink-0 rounded-full" style={{ background: HEALTH_TINT[m.health] }} aria-hidden title={m.health} />
      <div className="flex-1 min-w-0">
        <div className="truncate text-sm font-medium text-[var(--color-text)]">{m.path.split('/').pop()}</div>
        <div className="truncate text-[11px] text-[var(--color-text-faint)] font-mono">{m.path}</div>
      </div>
      <span className="shrink-0 rounded px-1.5 py-[1px] text-[10px] font-mono" style={{ background: 'color-mix(in srgb, #4a8fb0 16%, transparent)', color: '#4a8fb0' }}>{m.kind}</span>
      <span className="shrink-0 text-[10px] font-mono text-[var(--color-text-faint)]">Local</span>
    </div>
  );
}

// "+ Add Folder" — native folder picker (Tauri dialog plugin) → POST
// /api/library/mounts, which judges kind + registers + indexes (code inline,
// docs handed to chat). On success the mounts list refreshes so the new row shows.
function AddFolderButton() {
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const onClick = async () => {
    setBusy(true);
    try {
      const { open: openDialog } = await import('@tauri-apps/plugin-dialog');
      const picked = await openDialog({ directory: true, multiple: false, title: 'Add a folder to mount' });
      if (typeof picked === 'string') {
        try {
          const res = await api.post<{ kind: string; symbols?: number; next?: string }>(
            `/api/library/mounts?path=${encodeURIComponent(picked)}`,
          );
          const d = res.data;
          const msg = d.kind === 'code'
            ? `Mounted (code) — indexed ${d.symbols ?? 0} symbols; recall now reaches it.`
            : `Mounted (docs). ${d.next ?? 'Ask chat to brief the folder into cards.'}`;
          document.dispatchEvent(new CustomEvent('swarm:toast', { detail: { message: msg } }));
          qc.invalidateQueries({ queryKey: ['library-mounts'] });
        } catch (err: unknown) {
          const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          document.dispatchEvent(new CustomEvent('swarm:toast', { detail: { message: detail || 'Mount failed — see logs.' } }));
        }
      }
    } catch {
      // dialog unavailable (non-Tauri/dev) — fall back to chat guidance.
      document.dispatchEvent(new CustomEvent('swarm:toast', { detail: { message: 'Say "mount <path>" in chat to add a folder.' } }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      data-testid="library-add-folder"
      onClick={onClick}
      disabled={busy}
      className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-60"
      style={{ background: '#4a8fb0' }}
    >
      <span className="text-sm leading-none">+</span> Add Folder
    </button>
  );
}

// ── Recent: last-7-days add/edit feed (no fabricated review queue) ──
function RecentTab({
  recent, loading, error, onRetry,
}: {
  recent: RecentFeed | undefined; loading: boolean; error: boolean; onRetry: () => void;
}) {
  const items = recent?.items ?? [];
  return (
    <div data-testid="library-panel-recent" className="flex flex-col gap-3 max-w-3xl">
      <div className="text-sm text-[var(--color-text-muted)]">
        Added or edited in the last {recent?.window_days ?? 7} days — session backflow (🤖),
        your saves (⬆), and job output (⏱). Nothing to "process"; read what you want.
      </div>
      {error ? (
        <FetchError
          testid="library-recent-error"
          retryTestid="library-recent-retry"
          message="Couldn't load the recent feed."
          onRetry={onRetry}
        />
      ) : loading ? (
        <div className="py-8 text-center text-sm text-[var(--color-text-faint)]">Loading…</div>
      ) : items.length === 0 ? (
        <div className="py-8 text-center text-sm text-[var(--color-text-faint)]">Nothing new in the last week.</div>
      ) : (
        <div className="flex flex-col gap-1">
          {items.map((it) => {
            const meta = SOURCE_META[it.source];
            return (
              <button
                key={it.path}
                data-testid="library-recent-item"
                onClick={() => document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: it.path } }))}
                className="flex items-center gap-3 rounded-md px-3 py-2 text-left hover:bg-[var(--color-hover)]"
              >
                <span className="shrink-0 text-sm" title={meta.label}>{meta.icon}</span>
                <span className="flex-1 min-w-0 truncate text-sm text-[var(--color-text)]">{it.path.replace('Knowledge/', '')}</span>
                <span className="shrink-0 text-[11px] text-[var(--color-text-faint)]">{it.category}</span>
                <span className="w-16 shrink-0 text-right font-mono text-[11px] text-[var(--color-text-faint)]">{fmtWhen(it.mtime)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Guide: how recall USES this + how to manage. TRUE mechanism only (R30 / DoD6):
// verified against session_router._maybe_inject_recall + recall_multi (2026-08-02) —
// once-per-session latch, keyword/FTS5/BM25, NO vector leg (allow_embed=False),
// [RECALLED] tag. Describing a vector/hybrid leg here would be a stale-teaching bug.
const RECALL_STEPS: Array<{ icon: string; title: string; desc: string }> = [
  { icon: '🕐', title: 'When', desc: 'first substantive message of a session (once-per-session latch), not every message' },
  { icon: '🔍', title: 'How', desc: 'keywords from your message → keyword / FTS5 / BM25 match across 5 domains (no vector/embed leg)' },
  { icon: '📥', title: 'What lands', desc: 'matching chunks injected into the system prompt, tagged [RECALLED] — a lead to verify, not fact' },
  { icon: '🔗', title: 'On a mount hit', desc: 'recall lands on the pointer → the agent Reads the LIVE source (progressive)' },
  { icon: '💧', title: 'Indexing', desc: 'sync_knowledge_index scans Knowledge/, chunks + delta-syncs to FTS5 on the session hook' },
];
const MANAGE_ROWS: Array<{ want: string; say: string; result: string }> = [
  { want: 'Add a note/fact', say: 'remember: <X>', result: 'routed by s_persist to the right home' },
  { want: 'Ingest a URL/article', say: 'learn this <url>', result: 's_learn-content card (source + briefing)' },
  { want: 'Mount a folder', say: '+ Add Folder  ·  or "mount <path>"', result: 'agent judges kind → indexes in place (no copy)' },
  { want: 'Organize', say: 'organize my notes by topic', result: 'agent re-files / de-dupes' },
  { want: 'Ask what\'s known', say: 'what did I learn about X', result: 'recall + synthesize' },
];

function GuideTab() {
  return (
    <div data-testid="library-panel-guide" className="flex flex-col gap-6 max-w-4xl">
      {/* How recall uses this */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          🧠 How recall uses your library
        </div>
        <div className="text-sm text-[var(--color-text-muted)] mb-3">
          Searching Library IS this same recall path, run manually — you see what the agent would retrieve.
        </div>
        <div data-testid="library-recall-steps" className="flex flex-col gap-1.5">
          {RECALL_STEPS.map((s) => (
            <div key={s.title} className="flex items-start gap-3 rounded-md border border-[var(--color-border)] px-3 py-2">
              <span className="shrink-0 text-base leading-none mt-0.5">{s.icon}</span>
              <div className="min-w-0">
                <span className="text-xs font-semibold text-[var(--color-text)]">{s.title}</span>
                <span className="ml-2 text-[11px] text-[var(--color-text-muted)]">{s.desc}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* How to manage */}
      <section>
        <div className="mb-2 text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
          💬 How to manage — chat-native (heavy ops), overlay (light ops)
        </div>
        <div className="flex flex-col gap-1">
          {MANAGE_ROWS.map((r) => (
            <div key={r.want} className="grid grid-cols-[1fr_1.2fr_1.4fr] items-center gap-3 rounded-md px-3 py-1.5 text-xs">
              <span className="text-[var(--color-text)]">{r.want}</span>
              <code className="font-mono text-[11px] text-[var(--color-text-muted)] bg-[var(--color-hover)] rounded px-1.5 py-[1px] truncate">{r.say}</code>
              <span className="text-[11px] text-[var(--color-text-faint)]">{r.result}</span>
            </div>
          ))}
        </div>
        <div className="mt-2 text-[10px] text-[var(--color-text-faint)]">
          Overlay = browse + light ops (open · delete · move · unmount · refresh). Chat = add + heavy semantic ops.
        </div>
      </section>
    </div>
  );
}

function TabBtn({
  testid, label, active, onClick, badge,
}: { testid: string; label: string; active: boolean; onClick: () => void; badge?: number }) {
  return (
    <button
      data-testid={testid}
      onClick={onClick}
      className={
        'flex items-center gap-1.5 rounded-t-md px-3 py-2 text-sm font-medium transition-colors ' +
        (active
          ? 'text-[var(--color-text)] border-b-2 border-[#5fc99a]'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-b-2 border-transparent')
      }
    >
      {label}
      {badge != null && <span className="rounded-full bg-[var(--color-hover)] px-1.5 text-[10px] text-[var(--color-text-faint)]">{badge}</span>}
    </button>
  );
}
