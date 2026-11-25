/**
 * LibraryOverlay — the agent's bookshelf: `Knowledge/` (ours) + mounted folders (theirs,
 * indexed in place, never copied). Design: Knowledge/Mockups/2026-11-24-cm-library-redesign.html
 * (the "Library" panel).
 *
 * One search box on top, shared by the three tabs: a query runs the recall path
 * (GET /library/search) and its hits replace the tab body until it is cleared.
 * - Recent: one number (files changed this week) against last week; a bar split by who
 *   wrote them (Swarm's distillation / chats / scheduled jobs) whose legend filters the
 *   list; the list by local day, three days open, the rest behind "earlier"; a CDF row
 *   says how many units CDF wrote into it this week. Under it, one muted line when the
 *   weekly library-health job archived old logs by itself this week.
 * - Browse: the live Knowledge/ tree (LibraryTree), then the mounted folders with
 *   "Add folder". Nothing here is amber: no state of a mount is a decision this
 *   overlay offers (there is no unmount action), so a gone path is a muted line too.
 * - How it works: the four steps (written → stored → indexed → recalled) with live numbers.
 *
 * Data is backend-primary (R30): GET /library/recent (who wrote a file comes from CDF's
 * write journal, backend core/library_recent), /native, /mounts, /health (the job's
 * `auto_archived` record). Opened through the OverlayHost registry (overlaySurfaces).
 *
 * @exports LibraryContent
 */
import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Tabs } from '../overlay';
import { overlayQuery } from './overlayQueries';
import api from '../../services/api';
import { LibraryTree } from './LibraryTree';
import { OverlayError, OverlayLoading } from './OverlayState';
import { Big, CARD, Compare, DIM, MUTED, ZoneLabel, useGrown } from './needYouParts';
import { groupByDay, pad, readableName, sourceCounts, sourceOf, type RecentItem, type RecentSource } from './libraryRecent';

// ── Types (mirror the backend library_api payloads, snake_case as served) ──
interface NativeCategory { name: string; file_count: number; total_bytes: number; }
interface NativeStore { source: string; root: string; category_count: number; categories: NativeCategory[]; }
interface RecentFeed {
  window_days: number; count: number; items: RecentItem[];
  this_week?: number; last_week?: number; by_source?: Partial<Record<RecentSource, number>>;
}
interface MountRow {
  id: string; path: string; kind: 'code' | 'docs' | 'url';
  // a SQLite datetime('now') UTC stamp, null until first indexed: its presence means
  // recall can reach the mount (run_139d7652)
  last_synced?: string | null; health: 'fresh' | 'stale' | 'missing'; enabled: boolean;
}
interface MountsList { count: number; mounts: MountRow[]; registry_ready: boolean; }
interface HealthReport { auto_archived?: { at: number; count: number; bytes?: number } }
interface SearchHit { domain: string; title: string; source: string; content?: string; mount_id?: string | null; }
interface SearchResult { query: string; count: number; hits: SearchHit[]; error?: string; }

type TabKey = 'recent' | 'browse' | 'how';

const SOURCES: RecentSource[] = ['cdf', 'chat', 'job'];
const SOURCE_LABEL_KEY: Record<RecentSource, string> = {
  cdf: 'ui.LibraryOverlay.source.cdf',
  chat: 'ui.LibraryOverlay.source.chat',
  job: 'ui.LibraryOverlay.source.job',
};
const SOURCE_COLOR: Record<RecentSource, string> = {
  cdf: 'var(--line-judge)',
  chat: 'var(--color-zone-cognitive)',
  job: 'var(--color-zone-work)',
};
const OK = 'var(--color-status-success)';
const SHOWN_DAYS = 3;
const WEEK_MS = 7 * 24 * 3600 * 1000;
const FOCUS = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary)]';
const PANEL_ID = 'library-tab-panel';
// after "Add folder" answers that a mount is indexing in the background, the
// mounts list is re-read this often until it is indexed, for at most MOUNT_WATCH_MS
const MOUNT_POLL_MS = 3000;
const MOUNT_WATCH_MS = 10 * 60 * 1000;

/** How often to re-read the mounts: while a watch is on and a mount still has no
 *  index, every MOUNT_POLL_MS; otherwise not (false). */
function mountPollMs(data: MountsList | undefined, watchUntil: number, now: number): number | false {
  if (now >= watchUntil || !data) return false;
  const waiting = data.mounts.some((m) => (m.kind === 'code' || m.kind === 'docs') && m.enabled && !m.last_synced && m.health !== 'missing');
  return waiting ? MOUNT_POLL_MS : false;
}

const openFile = (path: string) => document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));

function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`;
  return `${n} B`;
}

/**
 * LibraryContent — the Library surface content. The OverlayHost owns the chrome and
 * mounts this only while the surface is open.
 */
export function LibraryContent() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabKey>('recent');
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');

  const { data: native } = useQuery(overlayQuery.libraryNative<NativeStore>());
  const recentQ = useQuery(overlayQuery.libraryRecent<RecentFeed>());
  const [watchUntil, setWatchUntil] = useState(0);
  const mountsQ = useQuery({
    ...overlayQuery.libraryMounts<MountsList>(),
    refetchInterval: (q) => mountPollMs(q.state.data, watchUntil, Date.now()),
  });
  const mounts = mountsQ.data;
  const { data: health } = useQuery(overlayQuery.libraryHealth<HealthReport>());

  const fileTotal = (native?.categories ?? []).reduce((s, c) => s + c.file_count, 0);
  const byteTotal = (native?.categories ?? []).reduce((s, c) => s + c.total_bytes, 0);
  const counts = useMemo(() => sourceCounts(recentQ.data), [recentQ.data]);
  const searching = submitted.trim().length > 0;
  const clear = () => { setQuery(''); setSubmitted(''); };

  return (
    <div className="flex h-full min-h-0 flex-col p-5" data-testid="library-overlay">
      <div className="mb-4">
        <Tabs label={t('ui.LibraryOverlay.tabsLabel')} controls={PANEL_ID} value={tab} onChange={(k) => { clear(); setTab(k); }} items={[
          { key: 'recent', label: t('ui.LibraryOverlay.tabRecent'), testid: 'library-tab-recent' },
          { key: 'browse', label: t('ui.LibraryOverlay.tabBrowse'), testid: 'library-tab-browse' },
          { key: 'how', label: t('ui.LibraryOverlay.tabHow'), testid: 'library-tab-how' },
        ]} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); setSubmitted(query); }}
        className={`${CARD} flex shrink-0 items-center gap-2.5 px-3.5 py-2.5`}>
        <span aria-hidden className={`material-symbols-outlined icon-sm ${DIM}`}>search</span>
        <input data-testid="library-search-input" type="text" value={query} onChange={(e) => setQuery(e.target.value)}
          // Escape clears a query first; the overlay's own Escape (which closes it)
          // skips an event a field already handled
          onKeyDown={(e) => { if (e.key === 'Escape' && (query || submitted)) { e.preventDefault(); clear(); } }}
          aria-label={t('ui.LibraryOverlay.searchPlaceholder')} placeholder={t('ui.LibraryOverlay.searchPlaceholder')}
          className="min-w-0 flex-1 border-0 bg-transparent text-body text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-dim)]" />
        {searching ? (
          <button type="button" data-testid="library-search-clear" onClick={clear}
            className={`text-small ${MUTED} hover:text-[var(--color-text)]`}>{t('ui.LibraryOverlay.clearSearch')}</button>
        ) : native && mounts && (
          <span className={`whitespace-nowrap text-meta tabular-nums ${DIM}`} data-testid="library-search-hint">
            {t('ui.LibraryOverlay.searchHint', { files: t('ui.LibraryOverlay.filesN', { count: fileTotal, n: fileTotal.toLocaleString() }), count: mounts.count })}
          </span>
        )}
      </form>

      <div id={PANEL_ID} role="tabpanel" className="mt-6 min-h-0 flex-1 overflow-y-auto">
        {searching ? <SearchResults query={submitted} />
          : tab === 'recent' ? (
            <RecentTab recent={recentQ.data} counts={counts} loading={recentQ.isPending} error={recentQ.isError}
              onRetry={() => { void recentQ.refetch(); }} archived={health?.auto_archived} />
          ) : tab === 'browse' ? (
            <BrowseTab native={native} mounts={mounts} mountsError={mountsQ.isError} onRetryMounts={() => { void mountsQ.refetch(); }}
              onIndexing={() => setWatchUntil(Date.now() + MOUNT_WATCH_MS)} />
          ) : <HowTab recent={recentQ.data} counts={counts} fileTotal={native ? fileTotal : null} byteTotal={byteTotal} mountCount={mounts?.count ?? null} />}
      </div>
    </div>
  );
}

// ── Search: the recall path, run by hand ──
function SearchResults({ query }: { query: string }) {
  const { t } = useTranslation();
  const { data, isFetching, isError, refetch } = useQuery<SearchResult>({
    queryKey: ['library-search', query],
    queryFn: async () => (await api.get<SearchResult>(`/library/search?q=${encodeURIComponent(query)}`)).data,
    staleTime: 30_000,
  });
  const hits = data?.hits ?? [];
  // the backend degrades a failed recall to {hits: [], error}: that is a failure, not "no hits"
  const failed = !isFetching && (isError || !!data?.error);
  return (
    <section data-testid="library-search-results">
      <ZoneLabel>{isFetching ? t('ui.LibraryOverlay.searching') : failed ? query : t('ui.LibraryOverlay.recallHits', { count: data?.count ?? 0, query })}</ZoneLabel>
      {failed ? (
        <OverlayError testid="library-search-error" retryTestid="library-search-retry" message={t('ui.LibraryOverlay.searchFailed')} onRetry={() => { void refetch(); }} />
      ) : hits.length === 0 && !isFetching ? (
        <p className={`text-body ${MUTED}`}>{t('ui.LibraryOverlay.noRecallHits')}</p>
      ) : (
        <div className="-mx-2.5 flex flex-col">
          {hits.map((h, i) => (
            // recall can return several chunks of one file: the index keeps keys unique
            <button key={`${h.source || 'hit'}#${i}`} type="button" data-testid="library-search-hit"
              onClick={() => h.source && openFile(h.source)}
              className={`grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)] ${FOCUS}`}>
              <span className="min-w-0 truncate text-body">
                <span className="mr-2 rounded bg-[var(--color-bg-chrome)] px-1.5 text-meta text-[var(--color-text-secondary)]">
                  {h.domain === 'codeintel' ? t('ui.LibraryOverlay.kindCode') : t('ui.LibraryOverlay.kindDocs')}{h.mount_id ? t('ui.LibraryOverlay.mountSuffix') : ''}
                </span>
                {h.title || h.source}
              </span>
              <span className={`max-w-[280px] truncate font-mono text-meta ${DIM}`}>{h.source}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// ── Recent: this week against last, by who wrote it, by day ──
function RecentTab({ recent, counts, loading, error, onRetry, archived }: {
  recent: RecentFeed | undefined; counts: Record<RecentSource, number>; loading: boolean; error: boolean;
  onRetry: () => void; archived: HealthReport['auto_archived'];
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<RecentSource | null>(null);
  const [allDays, setAllDays] = useState(false);
  // a source whose count fell to 0 on a refetch has no chip left to undo it with: drop
  // the filter for good, so it does not come back by itself when the source returns
  if (picked && !(counts[picked] > 0)) setPicked(null);
  const items = useMemo(() => (recent?.items ?? []).map((it) => ({ ...it, source: sourceOf(it.source) })), [recent]);
  const groups = useMemo(() => groupByDay(items.filter((it) => !picked || it.source === picked),
    { today: t('ui.LibraryOverlay.today'), yesterday: t('ui.LibraryOverlay.yesterday') }), [items, picked, t]);

  if (error) {
    return <OverlayError testid="library-recent-error" retryTestid="library-recent-retry" message={t('ui.LibraryOverlay.recentFailed')} onRetry={onRetry} />;
  }
  if (loading || !recent) return <OverlayLoading />;

  const total = recent.this_week ?? recent.count;
  const pick = (s: RecentSource) => setPicked(picked === s ? null : s);
  const capped = recent.count > recent.items.length;
  const shown = allDays ? groups : groups.slice(0, SHOWN_DAYS);
  const rest = groups.slice(SHOWN_DAYS);
  const archivedNow = archived && archived.count > 0 && Date.now() - archived.at * 1000 < WEEK_MS ? archived.count : 0;
  const archivedLine = archivedNow > 0 && (
    <p className={`mt-3.5 flex items-center gap-2 text-small ${DIM}`} data-testid="library-auto-archived">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: SOURCE_COLOR.cdf }} />
      {t('ui.LibraryOverlay.autoArchived', { count: archivedNow })}
    </p>
  );

  return (
    <div data-testid="library-panel-recent" className="flex flex-col gap-9">
      {total === 0 ? (
        <div>
          <p className={`text-body ${MUTED}`}>{t('ui.LibraryOverlay.nothingNew')}</p>
          {archivedLine}
        </div>
      ) : (
        <>
          <section>
            <Big testid="library-recent-big" value={total.toLocaleString()} unit={t('ui.LibraryOverlay.filesUnit')} what={t('ui.LibraryOverlay.changedThisWeek')}
              compare={recent.last_week != null ? <Compare testid="library-recent-last-week" was={t('ui.LibraryOverlay.lastWeek', { n: recent.last_week.toLocaleString() })} /> : undefined} />
            <SourceSplit counts={counts} picked={picked} onPick={pick} />
          </section>
          <section data-testid="library-recent-days">
            {groups.length === 0 && <p className={`text-body ${MUTED}`} data-testid="library-recent-none">{t('ui.LibraryOverlay.noneListed')}</p>}
            {shown.map((g, gi) => (
              <div key={g.key} data-testid={`library-day-${g.key}`}>
                <div className={`mb-1 flex items-baseline gap-2.5 ${gi ? 'mt-[18px]' : ''}`}>
                  <span className="text-body font-semibold">{g.label}</span>
                  <span className={`ml-auto text-small tabular-nums ${MUTED}`}>{t('ui.LibraryOverlay.dayCount', { count: g.items.length })}</span>
                </div>
                <div className="-mx-2.5 flex flex-col divide-y divide-[var(--color-border)]">
                  {g.items.map((it) => <RecentRow key={it.path} it={it} />)}
                </div>
              </div>
            ))}
            {!allDays && rest.length > 0 && (
              <button type="button" data-testid="library-recent-more" onClick={() => setAllDays(true)}
                className={`mt-2 cursor-pointer text-small text-[var(--color-primary)] hover:underline ${FOCUS}`}>
                {t('ui.LibraryOverlay.earlier', { count: rest.length, files: t('ui.LibraryOverlay.filesN', { count: rest.reduce((s, g) => s + g.items.length, 0), n: rest.reduce((s, g) => s + g.items.length, 0) }) })}
              </button>
            )}
            {capped && (allDays || rest.length === 0) && (
              <p className={`mt-3.5 text-small ${DIM}`} data-testid="library-recent-capped">
                {t('ui.LibraryOverlay.capped', { shown: recent.items.length, total: recent.count.toLocaleString() })}
              </p>
            )}
            {archivedLine}
          </section>
        </>
      )}
    </div>
  );
}

function SourceSplit({ counts, picked, onPick }: {
  counts: Record<RecentSource, number>; picked: RecentSource | null; onPick: (s: RecentSource) => void;
}) {
  const { t } = useTranslation();
  const present = SOURCES.filter((s) => counts[s] > 0);
  const sum = present.reduce((a, s) => a + counts[s], 0) || 1;
  const grown = useGrown(present.map((s) => `${s}${counts[s]}`).join(','));
  return (
    <>
      <div className="mt-4 flex h-2.5 gap-0.5 overflow-hidden rounded-[5px] bg-[var(--color-hover)]" data-testid="library-source-split">
        {present.map((s) => (
          <button key={s} type="button" tabIndex={-1} aria-hidden onClick={() => onPick(s)} data-testid={`library-split-${s}`}
            className="h-full cursor-pointer transition-[width,opacity] duration-700 ease-out hover:brightness-110 motion-reduce:transition-none"
            style={{ width: grown ? `${(counts[s] / sum) * 100}%` : 0, background: SOURCE_COLOR[s], opacity: !picked || picked === s ? 1 : 0.25 }} />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-2 gap-y-1.5">
        {present.map((s) => (
          <button key={s} type="button" aria-pressed={picked === s} onClick={() => onPick(s)} data-testid={`library-source-chip-${s}`}
            className={`btn-sm btn-secondary inline-flex cursor-pointer items-center gap-1.5 rounded-full transition-[opacity,color,border-color] ${FOCUS} ${
              picked === s ? 'border-[var(--color-text-secondary)] text-[var(--color-text)]'
                : `text-[var(--color-text-muted)] hover:text-[var(--color-text)] ${picked ? 'opacity-45 hover:opacity-80' : ''}`}`}>
            <span aria-hidden className="h-2 w-2 rounded-sm" style={{ background: SOURCE_COLOR[s] }} />
            {t(SOURCE_LABEL_KEY[s])}
            <b className="font-semibold tabular-nums text-[var(--color-text)]">{counts[s]}</b>
          </button>
        ))}
      </div>
    </>
  );
}

function RecentRow({ it }: { it: RecentItem }) {
  const { t } = useTranslation();
  const d = new Date(it.mtime * 1000);
  const folder = it.category === '(root)' ? 'Knowledge' : it.category;
  const units = it.source === 'cdf' ? it.cdf_units ?? 0 : 0;
  return (
    <button type="button" data-testid="library-recent-item" data-source={it.source} onClick={() => openFile(it.path)} title={it.path}
      className={`grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--color-hover)] ${FOCUS}`}>
      <span className="min-w-0 truncate text-body">
        <span aria-hidden className="mr-2.5 inline-block h-[7px] w-[7px] rounded-full align-[1px]" style={{ background: SOURCE_COLOR[it.source] }} />
        {folder} · {readableName(it.path)}
      </span>
      <span className={`flex items-baseline gap-3.5 whitespace-nowrap text-small tabular-nums ${MUTED}`}>
        {units > 0 && <b className="font-semibold" style={{ color: SOURCE_COLOR.cdf }} data-testid="library-recent-units">{t('ui.LibraryOverlay.units', { count: units })}</b>}
        <span>{pad(d.getHours())}:{pad(d.getMinutes())}</span>
      </span>
    </button>
  );
}

// ── Browse: the live Knowledge/ tree, then the mounted folders ──
function BrowseTab({ native, mounts, mountsError, onRetryMounts, onIndexing }: {
  native: NativeStore | undefined; mounts: MountsList | undefined; mountsError: boolean; onRetryMounts: () => void;
  onIndexing: () => void;
}) {
  const { t } = useTranslation();
  const rows = mounts?.mounts ?? [];
  return (
    <div data-testid="library-panel-browse" className="flex h-full min-h-0 flex-col gap-9">
      <section data-testid="library-native-section" className="flex min-h-[240px] flex-1 flex-col">
        <ZoneLabel right={native ? <span className={DIM}>{t('ui.LibraryOverlay.dirCount', { count: native.category_count })}</span> : undefined}>
          {t('ui.LibraryOverlay.nativeHeading')}
        </ZoneLabel>
        <LibraryTree />
      </section>
      <section data-testid="library-mounted-section" className="shrink-0">
        <ZoneLabel right={<AddFolderButton onIndexing={onIndexing} />}>{t('ui.LibraryOverlay.mountedHeading')}</ZoneLabel>
        {mountsError ? (
          <OverlayError compact testid="library-mounts-error" retryTestid="library-mounts-retry" message={t('ui.LibraryOverlay.mountsFailed')} onRetry={onRetryMounts} />
        ) : !mounts ? (
          <OverlayLoading testid="library-mounts-loading" />
        ) : rows.length === 0 ? (
          <p data-testid="library-mounted-empty" className={`text-body ${MUTED}`}>{t('ui.LibraryOverlay.mountedEmpty')}</p>
        ) : (
          <div className="flex flex-col gap-2">{rows.map((m) => <MountRowView key={m.id} m={m} />)}</div>
        )}
      </section>
    </div>
  );
}

/** A SQLite `datetime('now')` UTC stamp as a Date (null when absent or unreadable). */
function sqliteUtc(s: string | null | undefined): Date | null {
  if (!s) return null;
  const d = new Date(`${s.replace(' ', 'T')}${/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? '' : 'Z'}`);
  return Number.isNaN(d.getTime()) ? null : d;
}

// A mount: name, path, kind · index state, last sync. All muted: a stale index is a
// date (the agent reads the live source on a hit, so stale only lowers the hit
// chance), and a gone path offers no decision here (there is no unmount action).
function MountRowView({ m }: { m: MountRow }) {
  const { t } = useTranslation();
  const indexed = !!m.last_synced;
  const missing = m.health === 'missing';
  const synced = sqliteUtc(m.last_synced);
  const kind = t(m.kind === 'code' ? 'ui.LibraryOverlay.kindCode' : m.kind === 'url' ? 'ui.LibraryOverlay.kindUrl' : 'ui.LibraryOverlay.kindDocs');
  let name = m.path.split('/').filter(Boolean).pop() ?? m.path;
  if (m.kind === 'url') { try { name = new URL(m.path).hostname || name; } catch { /* not a URL: keep the last segment */ } }
  const state = missing ? t('ui.LibraryOverlay.mountMissing')
    : indexed ? t('ui.LibraryOverlay.mountIndexed')
    : m.kind === 'docs' ? t('ui.LibraryOverlay.mountDocsEmpty') : t('ui.LibraryOverlay.mountNotIndexed');
  let sync: string | null = null;
  if (synced) {
    const days = Math.floor((Date.now() - synced.getTime()) / 86_400_000);
    sync = m.health === 'stale'
      ? (days < 1 ? t('ui.LibraryOverlay.syncedToday') : t('ui.LibraryOverlay.syncedAgo', { count: days }))
      : t('ui.LibraryOverlay.syncedOn', { date: `${pad(synced.getMonth() + 1)}-${pad(synced.getDate())}` });
  }
  return (
    <div data-testid={`library-mount-${m.id}`} data-kind={m.kind} data-indexed={indexed ? 'true' : 'false'} data-health={m.health}
      className={`${CARD} flex items-center gap-3 px-3.5 py-3`}>
      <span aria-hidden className="h-[7px] w-[7px] shrink-0 rounded-full"
        style={{ background: !missing && indexed && m.health === 'fresh' ? OK : 'var(--color-text-dim)' }} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-body font-semibold">{name}</div>
        <div className={`truncate font-mono text-meta ${DIM}`}>{m.path}</div>
      </div>
      <div className={`shrink-0 text-right text-small ${MUTED}`}>
        <div data-testid={`library-mount-state-${m.id}`}>{kind} · {state}</div>
        {sync && <div className={DIM} data-testid={`library-mount-sync-${m.id}`}>{sync}</div>}
      </div>
    </div>
  );
}

// "+ Add folder": native folder picker (Tauri dialog plugin) → POST /library/mounts
// (axios baseURL adds /api), which judges the kind and registers it; its index is
// built in the background (status 'indexing'): `onIndexing` starts
// the mounts re-read, so its row turns "indexed" when the index lands.
function AddFolderButton({ onIndexing }: { onIndexing: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const toast = (message: string) => document.dispatchEvent(new CustomEvent('swarm:toast', { detail: { message } }));
  const onClick = async () => {
    setBusy(true);
    try {
      const { open: openDialog } = await import('@tauri-apps/plugin-dialog');
      const picked = await openDialog({ directory: true, multiple: false, title: t('ui.LibraryOverlay.mountDialogTitle') });
      if (typeof picked === 'string') {
        try {
          const res = await api.post<{ kind: string; status?: string; symbols?: number; chunks?: number }>(
            `/library/mounts?path=${encodeURIComponent(picked)}`,
          );
          const d = res.data;
          if (d.status === 'indexing') onIndexing();
          // a mount with nothing parseable is not reachable by recall: the toast says so
          toast(d.kind === 'code'
            ? (d.status === 'indexing' ? t('ui.LibraryOverlay.mountedCodeIndexing')
              : (d.symbols ?? 0) === 0 ? t('ui.LibraryOverlay.mountedCodeEmpty')
              : t('ui.LibraryOverlay.mountedCode', { count: d.symbols ?? 0 }))
            : d.status === 'indexing' ? t('ui.LibraryOverlay.mountedDocsIndexing')
            : (d.chunks ?? 0) > 0 ? t('ui.LibraryOverlay.mountedDocs', { count: d.chunks ?? 0 })
            : t('ui.LibraryOverlay.mountedDocsEmpty'));
          void qc.invalidateQueries({ queryKey: ['library-mounts'] });
        } catch (err: unknown) {
          const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
          toast(detail || t('ui.LibraryOverlay.mountFailed'));
        }
      }
    } catch {
      // no dialog (non-Tauri / dev): say how to mount from chat
      toast(t('ui.LibraryOverlay.mountViaChat'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button type="button" data-testid="library-add-folder" onClick={onClick} disabled={busy} className="btn-sm btn-primary disabled:opacity-60">
      <span aria-hidden className="material-symbols-outlined icon-xs">add</span> {t('ui.LibraryOverlay.addFolder')}
    </button>
  );
}

// ── How it works: written → stored → indexed → recalled, with live numbers ──
function HowTab({ recent, counts: by, fileTotal, byteTotal, mountCount }: {
  recent: RecentFeed | undefined; counts: Record<RecentSource, number>; fileTotal: number | null; byteTotal: number;
  mountCount: number | null;
}) {
  const { t } = useTranslation();
  const unit = t('ui.LibraryOverlay.filesUnit');
  return (
    <section data-testid="library-panel-how">
      <ZoneLabel>{t('ui.LibraryOverlay.how.title')}</ZoneLabel>
      <div data-testid="library-how-flow"
        className="mt-1.5 flex flex-col gap-3 md:grid md:grid-cols-[minmax(0,1fr)_22px_minmax(0,1fr)_22px_minmax(0,1fr)_22px_minmax(0,1fr)] md:gap-1">
        <Node testid="library-how-written" k={t('ui.LibraryOverlay.how.written', { days: recent?.window_days ?? 7 })} live
          v={recent ? <>{(recent.this_week ?? recent.count).toLocaleString()}{unit && <Unit>{unit}</Unit>}</> : '—'}
          s={[t('ui.LibraryOverlay.how.writtenCdf', { n: by.cdf }), t('ui.LibraryOverlay.how.writtenRest', { chat: by.chat, job: by.job })]} />
        <Arrow />
        <Node testid="library-how-stored" k={t('ui.LibraryOverlay.how.stored')}
          v={fileTotal != null ? <>{fileTotal.toLocaleString()}{unit && <Unit>{unit}</Unit>}</> : '—'}
          s={[t('ui.LibraryOverlay.how.storedSize', { size: fmtBytes(byteTotal) }),
            mountCount == null ? '—' : mountCount > 0 ? t('ui.LibraryOverlay.how.storedMounts', { count: mountCount }) : t('ui.LibraryOverlay.how.storedNoMounts')]} />
        <Arrow />
        <Node testid="library-how-indexed" k={t('ui.LibraryOverlay.how.indexed')} v={t('ui.LibraryOverlay.how.indexedV')}
          s={[t('ui.LibraryOverlay.how.indexedS1'), t('ui.LibraryOverlay.how.indexedS2')]} />
        <Arrow />
        <Node testid="library-how-recalled" k={t('ui.LibraryOverlay.how.recalled')} v={t('ui.LibraryOverlay.how.recalledV')}
          s={[t('ui.LibraryOverlay.how.recalledS1'), t('ui.LibraryOverlay.how.recalledS2')]} />
      </div>
    </section>
  );
}

const Unit = ({ children }: { children: ReactNode }) => <span className={`ml-0.5 text-small ${MUTED}`}>{children}</span>;

function Node({ k, v, s, live, testid }: { k: string; v: ReactNode; s: string[]; live?: boolean; testid: string }) {
  return (
    <div data-testid={testid} className={`${CARD} px-3.5 pb-3 pt-3.5`}>
      <div className={`text-meta font-semibold tracking-[0.06em] ${MUTED}`}>{k}</div>
      <div className="mt-1.5 text-display font-semibold leading-7 tabular-nums" style={live ? { color: SOURCE_COLOR.cdf } : undefined}>{v}</div>
      {s.map((line) => <div key={line} className="mt-0.5 text-small text-[var(--color-text-secondary)]">{line}</div>)}
    </div>
  );
}

function Arrow() {
  return (
    <div aria-hidden className="hidden items-center md:flex">
      <span className="h-0.5 flex-1 bg-[var(--color-border)]" />
      <span className="h-0 w-0 border-y-[5px] border-l-[6px] border-y-transparent border-l-[var(--color-text-dim)]" />
    </div>
  );
}
