/**
 * PollinateOverlay — the WORK-zone "Pollinate" surface. A structural sibling of
 * PipelineOverlay (fullscreen Modal + fetch-once + absolute detail drawer).
 *
 * Two views (Gallery | Insights toggle, JobsRunsOverlay pattern):
 *  - Gallery: first the WORKS (Pollinate v2, GET /pollinate/works): one card per
 *    registered work — cover thumbnail, title, version — whose one action is "Open in
 *    Canvas" for its main output; a card is marked only when its newest version
 *    failed its checks (a failed cut leaves `current` on the last passing version). Below them, "Earlier runs": the older run gallery
 *    (GET /pollinate/assets) — newest-first content cards (newest expanded), each an
 *    asset grid with thumbnails via /api/workspace/file/raw, a client-side
 *    search/filter/sort toolbar, and an asset detail drawer (big image + copy-caption
 *    + open-account).
 *  - Insights: the run rollup strip + 5 client-side rollups (by-type / by-channel /
 *    by-domain / production trend / publish funnel), all from the SAME run payload.
 *    NO token panel — pollinate can't attribute per-run tokens today.
 *
 * NO LIVE POLLING — fetch-once on open (retro surface; a running pollinate lives in
 * its chat tab). Local state only — never MessageStore / active-tab mutation (OT01).
 * WRITES GO THROUGH CHAT (Gate-1 #7): "Produce for {platform}" / "Resume" inject a
 * chat prompt via onDispatch. The overlay writes nothing itself.
 *
 * @exports PollinateOverlay
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { overlayQuery, invalidateOverlay } from './overlayQueries';
import { classifyLoadError } from '../../services/api';
import {
  pollinateService,
  assetThumbUrl,
  type PollinateAssetsResponse,
  type PollinateContentCard,
  type PollinateAsset,
  type PollinateWork,
  type PollinateWorksResponse,
} from '../../services/pollinate';
import { WorkbenchToolbar, OverlayDrawer } from './overlayShell';
import { OverlayError } from './OverlayState';
import { copyToClipboard } from '../../utils/clipboard';

export interface PollinateContentProps {
  /** Hand a prompt to a chat tab (land+activate, THEN inject). Mirrors ChatPage's
   *  dispatch — a bare inject no-ops with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close (called after a successful dispatch). */
  close: () => void;
}

type View = 'gallery' | 'insights';
type Sort = 'newest' | 'to-publish';

/** Parse a timestamp as LOCAL time. A date-ONLY string ("2026-05-03" — the common
 *  dir-name fallback) is parsed by `new Date()` as UTC midnight, which renders as the
 *  PREVIOUS day for users west of UTC (Gate-2 LOW). Split date-only strings manually
 *  so the displayed day matches the stored day regardless of timezone. */
function parseLocal(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return new Date(iso);
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = parseLocal(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Open a workspace file in the current tab's Canvas: close the overlay first so the
 *  Canvas is not rendered under it, then the same swarm:open-file event the other
 *  overlays send (handled by the chat page's Canvas host). */
function openInCanvas(path: string, close: () => void): void {
  close();
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
}

function stateDot(s: string): string {
  if (s === 'published') return 'bg-[var(--color-status-success)]';
  if (s === 'ready-to-publish') return 'bg-[var(--color-primary)]';
  return 'bg-[var(--color-text-faint)]'; // ready (unknown)
}

/** ISO week (Mon) of a timestamp — for the production trend. */
function isoWeek(iso: string | null): string | null {
  if (!iso) return null;
  const d = parseLocal(iso);
  if (isNaN(d.getTime())) return null;
  const day = (d.getDay() + 6) % 7; // Mon=0
  const mon = new Date(d);
  mon.setDate(d.getDate() - day);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${mon.getFullYear()}-${p(mon.getMonth() + 1)}-${p(mon.getDate())}`;
}

/** Image with a broken-image fallback (B5): if the raw-file endpoint 404s or the
 *  file moved, show a neutral placeholder icon instead of the browser's broken
 *  glyph. Used for both the gallery thumbnail and the drawer's big image. */
function ImgWithFallback({ src, alt, className, lazy }: { src: string; alt: string; className?: string; lazy?: boolean }) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className={`flex items-center justify-center bg-[var(--color-bg)] ${className ?? ''}`} data-testid="pollinate-img-fallback">
        <span className="material-symbols-outlined icon-xl text-[var(--color-text-faint)]" title={t('ui.PollinateOverlay.previewUnavailable', { name: alt })}>
          broken_image
        </span>
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading={lazy ? 'lazy' : undefined}
      className={className}
      onError={() => setFailed(true)}
    />
  );
}

export function PollinateContent({ onDispatch, close }: PollinateContentProps) {
  const { t } = useTranslation();
  const [view, setView] = useState<View>('gallery');
  const [query, setQuery] = useState('');
  const [platformFilter, setPlatform] = useState<string>('');
  const [formatFilter, setFormat] = useState<string>('');
  const [domainFilter, setDomain] = useState<string>('');
  const [toPublishOnly, setToPublishOnly] = useState(false);
  const [sort, setSort] = useState<Sort>('newest');
  const [selected, setSelected] = useState<{ card: PollinateContentCard; asset: PollinateAsset } | null>(null);
  // Caption-body cache lifted to the PARENT (Gate-1 MED): AssetDrawer unmounts on every
  // close, so a cache inside it would be destroyed each time. Keyed by workspace path,
  // it survives open/close while the overlay is open → re-opening an asset is instant.
  const captionCache = useRef<Map<string, string | null>>(new Map());

  // The asset list through the shared overlay query (overlayQueries): a reopen inside
  // the freshness window or after a nav hover shows the cached gallery at once.
  const qc = useQueryClient();
  const assetsQ = useQuery(overlayQuery.pollinate());
  const data: PollinateAssetsResponse | null = assetsQ.data ?? null;
  const loading = assetsQ.isFetching;
  const loadErr: unknown = assetsQ.isError && !assetsQ.data ? assetsQ.error : null;
  const reload = useCallback(() => { void invalidateOverlay(qc, 'pollinate'); }, [qc]);
  // The registered works — their own query, so a failure here never hides the runs.
  const worksQ = useQuery(overlayQuery.pollinateWorks());
  const works: PollinateWorksResponse | null = worksQ.data ?? null;
  const worksErr: unknown = worksQ.isError && !worksQ.data ? worksQ.error : null;

  // Keep the OPEN drawer in sync with re-fetched data (a Retry refetches): re-derive
  // `selected` from the fresh list by run + file path.
  useEffect(() => {
    if (!data) return;
    setSelected((prev) => {
      if (!prev) return prev;
      const card = data.cards.find((c) => c.run === prev.card.run);
      const asset = card?.assets.find((a) => a.filePath === prev.asset.filePath);
      if (!card || !asset) return prev; // asset vanished (e.g. filtered) → keep last known
      return { card, asset };
    });
  }, [data]);

  const dispatchToChat = useCallback((prompt: string) => {
    const landed = onDispatch(prompt);
    if (landed) requestAnimationFrame(() => requestAnimationFrame(() => close()));
  }, [onDispatch, close]);

  const cards = data?.cards ?? [];

  // Client-side filter/search over the already-fetched list (design §1.3 — zero
  // backend index at this scale). Search matches topic/domain/platform/format/file.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = cards.filter((c) => {
      if (platformFilter && !c.platforms.includes(platformFilter)) return false;
      if (formatFilter && !c.formats.includes(formatFilter)) return false;
      if (domainFilter && c.domain !== domainFilter) return false;
      // to-publish = has at least one asset still awaiting posting (ready / ready-to-publish).
      if (toPublishOnly && !c.assets.some((a) => a.publishStatus !== 'published')) return false;
      if (!q) return true;
      const hay = [
        c.topic, c.domain ?? '', c.run,
        ...c.platforms, ...c.formats,
        ...c.assets.map((a) => a.fileName),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
    if (sort === 'to-publish') {
      // Surface cards with the most ready (not-yet-published) assets first.
      out = [...out].sort((a, b) => b.readyCount - a.readyCount);
    }
    // 'newest' preserves the backend's newest-first order.
    return out;
  }, [cards, query, platformFilter, formatFilter, domainFilter, toPublishOnly, sort]);

  const allPlatforms = useMemo(
    () => Array.from(new Set(cards.flatMap((c) => c.platforms))).sort(),
    [cards],
  );
  const allFormats = useMemo(
    () => Array.from(new Set(cards.flatMap((c) => c.formats))).sort(),
    [cards],
  );
  // Domain chips — from NON-NULL card.domain only (many cards have domain=null; a null
  // chip would filter to nothing and confuse). Gate-1 MED.
  const allDomains = useMemo(
    () => Array.from(new Set(cards.map((c) => c.domain).filter((d): d is string => !!d))).sort(),
    [cards],
  );

  const o = data?.overall;

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="pollinate-overlay">
        {/* Sub-header: label + Gallery|Insights toggle (shared WorkbenchToolbar). */}
        <WorkbenchToolbar
          loading={loading}
          left={<span className="text-small text-[var(--color-text-muted)]">{t('ui.PollinateOverlay.contentAssets')}</span>}
          right={(['gallery', 'insights'] as View[]).map((v) => (
            <button
              key={v}
              role="radio"
              aria-checked={view === v}
              onClick={() => setView(v)}
              data-testid={`pollinate-view-${v}`}
              className={`px-2.5 py-1 text-small rounded-md transition-colors ${
                view === v ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {v === 'gallery' ? t('ui.PollinateOverlay.gallery') : t('ui.PollinateOverlay.insights')}
            </button>
          ))}
        />

        {/* Run rollup strip — Insights only: it counts the earlier runs, not the works
            the gallery leads with (one signal per surface). */}
        {view === 'insights' && o && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 px-4 py-2 border-b border-[var(--color-border)]" data-testid="pollinate-overall">
            <Stat label={t('ui.PollinateOverlay.statTopics')} value={String(o.cardCount)} />
            <Stat label={t('ui.PollinateOverlay.statAssets')} value={String(o.assetCount)} />
            <Stat label={t('ui.PollinateOverlay.statReady')} value={String(o.ready)} />
            <Stat label={t('ui.PollinateOverlay.statPublished')} value={String(o.published)} />
            <Stat label={t('ui.PollinateOverlay.statInProgress')} value={String(o.inProgress)} accent={o.inProgress > 0} />
            <Stat label={t('ui.PollinateOverlay.statChannels')} value={String(Object.keys(o.platformDist).length)} />
          </div>
        )}

        {view === 'gallery' ? (
          <div className="flex-1 min-h-0 overflow-y-auto" data-testid="pollinate-gallery">
            <WorksSection
              data={works}
              loading={worksQ.isFetching}
              error={worksErr}
              onRetry={reload}
              onOpen={(path) => openInCanvas(path, close)}
            />
            {/* Earlier runs: only when there are some — a person with none sees the works
                section alone, not a second empty state under filters that do nothing. */}
            {cards.length > 0 && (<>
            <div className="px-4 pt-4 pb-1 text-meta uppercase tracking-wide text-[var(--color-text-faint)]" data-testid="pollinate-earlier-runs">
              {t('ui.PollinateOverlay.earlierRuns')}
            </div>
            {/* Toolbar: search + filters + sort (client-side, over the runs) */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border)] flex-wrap">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('ui.PollinateOverlay.searchPlaceholder')}
                data-testid="pollinate-search"
                className="flex-1 min-w-[160px] px-2.5 py-1 text-small rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
              />
              <Select value={platformFilter} onChange={setPlatform} options={allPlatforms} placeholder={t('ui.PollinateOverlay.filterPlatform')} testid="pollinate-filter-platform" />
              <Select value={formatFilter} onChange={setFormat} options={allFormats} placeholder={t('ui.PollinateOverlay.filterFormat')} testid="pollinate-filter-format" />
              {/* to-publish state chip — surface what still needs posting (design §1.3). */}
              <button
                onClick={() => setToPublishOnly((v) => !v)}
                data-testid="pollinate-chip-to-publish"
                className={`btn-sm btn-secondary rounded-full transition-colors whitespace-nowrap ${
                  toPublishOnly
                    ? 'bg-primary/15 text-primary border-primary'
                    : 'text-[var(--color-text-muted)] border-[var(--color-border)] hover:bg-[var(--color-hover)]'
                }`}
              >
                {t('ui.PollinateOverlay.toPublish')}
              </button>
              <Select
                value={sort}
                onChange={(v) => setSort(v as Sort)}
                options={['newest', 'to-publish']}
                labels={{ newest: t('ui.PollinateOverlay.sortNewest'), 'to-publish': t('ui.PollinateOverlay.sortToPublish') }}
                placeholder={t('ui.PollinateOverlay.sort')}
                testid="pollinate-sort"
              />
            </div>
            {/* Domain filter chips — click to AND-filter (design §1.3; mockup toolbar). */}
            {allDomains.length > 0 && (
              <div className="flex items-center gap-1.5 px-4 py-1.5 border-b border-[var(--color-border)] flex-wrap" data-testid="pollinate-domain-chips">
                {allDomains.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDomain((cur) => (cur === d ? '' : d))}
                    data-testid={`pollinate-domain-chip-${d}`}
                    className={`btn-sm btn-secondary rounded-full transition-colors whitespace-nowrap ${
                      domainFilter === d
                        ? 'bg-[color-mix(in_srgb,var(--color-status-success)_15%,transparent)] text-[var(--color-status-success)] border-[color-mix(in_srgb,var(--color-status-success)_60%,transparent)]'
                        : 'text-[color-mix(in_srgb,var(--color-status-success)_80%,transparent)] border-[color-mix(in_srgb,var(--color-status-success)_30%,transparent)] hover:bg-[color-mix(in_srgb,var(--color-status-success)_10%,transparent)]'
                    }`}
                  >
                    {d}
                  </button>
                ))}
              </div>
            )}
            </>)}

            {/* Body: content cards (newest-first, newest expanded) */}
            <div className="px-4 py-3 space-y-3">
              {/* Fetch failure (B3) — distinct from "no content", with Retry. */}
              {!!loadErr && !loading && (
                <OverlayError
                  testid="pollinate-load-error"
                  retryTestid="pollinate-load-retry"
                  message={classifyLoadError(loadErr, 'content assets')}
                  onRetry={reload}
                  className="mx-auto my-8 max-w-sm"
                />
              )}
              {!loading && !loadErr && cards.length > 0 && filtered.length === 0 && (
                <div className="text-center py-10 text-body text-[var(--color-text-faint)]">
                  {t('ui.PollinateOverlay.noMatch')}
                </div>
              )}
              {filtered.map((card, idx) => (
                <ContentCard
                  key={card.run}
                  card={card}
                  defaultExpanded={idx === 0}
                  onOpenAsset={(asset) => setSelected({ card, asset })}
                  onProduce={(platform) =>
                    dispatchToChat(`Resume pollinate for "${card.topic}" and produce the ${platform} asset (run dir ${card.run}).`)}
                />
              ))}
            </div>
          </div>
        ) : (
          <InsightsView data={data} loading={loading} />
        )}

        {/* Asset detail drawer */}
        {selected && (
          <AssetDrawer
            card={selected.card}
            asset={selected.asset}
            knownChannels={o?.knownChannels ?? []}
            captionCache={captionCache.current}
            onClose={() => setSelected(null)}
            onOpenAccount={(platform) =>
              dispatchToChat(`Open my ${platform} account so I can publish "${selected.card.topic}".`)}
            onProduce={(platform) =>
              dispatchToChat(`Resume pollinate for "${selected.card.topic}" and produce the ${platform} asset (run dir ${selected.card.run}).`)}
          />
        )}
    </div>
  );
}

/** The kind's placeholder icon when a work has no cover image (yet). */
function kindIcon(kind: string): string {
  if (kind === 'deck') return 'slideshow';
  if (kind === 'poster') return 'image';
  if (kind === 'page') return 'article';
  if (kind === 'copy') return 'notes';
  return 'draft';
}

/** The works section: one card per registered work. A card answers one decision —
 *  "open this work to review it" — so its one action is Open in Canvas; the only
 *  thing that stands out is the mark on a work whose newest version failed its
 *  checks. Kind, version and date stay muted. */
function WorksSection({ data, loading, error, onRetry, onOpen }: {
  data: PollinateWorksResponse | null;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onOpen: (path: string) => void;
}) {
  const { t } = useTranslation();
  const works = data?.works ?? [];
  return (
    <section className="px-4 pt-3" data-testid="pollinate-works">
      <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)] mb-2">
        {t('ui.PollinateOverlay.works')}
      </div>
      {!!error && !loading && (
        <OverlayError
          testid="pollinate-works-error"
          retryTestid="pollinate-works-retry"
          message={classifyLoadError(error, 'works')}
          onRetry={onRetry}
          className="max-w-sm"
        />
      )}
      {!data && loading && (
        <div className="text-meta text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.loading')}</div>
      )}
      {!error && data && works.length === 0 && (
        <div className="text-small text-[var(--color-text-faint)]" data-testid="pollinate-works-empty">
          {t('ui.PollinateOverlay.worksEmpty')}
        </div>
      )}
      {works.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {works.map((w) => <WorkCard key={w.path} work={w} onOpen={onOpen} />)}
        </div>
      )}
      {(data?.skipped ?? 0) > 0 && (
        <div className="mt-2 text-meta text-[var(--color-text-faint)]" data-testid="pollinate-works-skipped">
          {t('ui.PollinateOverlay.worksSkipped', { count: data?.skipped ?? 0 })}
        </div>
      )}
    </section>
  );
}

function WorkCard({ work, onOpen }: { work: PollinateWork; onOpen: (path: string) => void }) {
  const { t } = useTranslation();
  // Canvas and the raw-file endpoint open workspace files only (design §4).
  const openPath = work.inWorkspace ? work.mainOutput : null;
  const thumb = work.inWorkspace ? work.thumbnail : null;
  const failed = work.status === 'failed';
  const cover = thumb ? (
    <ImgWithFallback src={assetThumbUrl(thumb)} alt={work.title} lazy className="w-full h-full object-cover" />
  ) : (
    <span className="material-symbols-outlined icon-xl text-[var(--color-text-faint)]">{kindIcon(work.kind)}</span>
  );
  return (
    <div className="flex flex-col gap-1.5 min-w-0" data-testid={`pollinate-work-${work.path}`}>
      {openPath ? (
        <button
          type="button"
          onClick={() => onOpen(openPath)}
          title={t('ui.PollinateOverlay.openInCanvas')}
          className="w-full aspect-[16/10] rounded-md overflow-hidden bg-[var(--color-bg)] flex items-center justify-center hover:ring-1 hover:ring-primary transition-shadow"
        >
          {cover}
        </button>
      ) : (
        <div className="aspect-[16/10] rounded-md overflow-hidden bg-[var(--color-bg)] flex items-center justify-center">
          {cover}
        </div>
      )}
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-body text-[var(--color-text)] truncate flex-1 min-w-0" title={work.title}>{work.title}</span>
        {failed && (
          <span
            className="shrink-0 inline-flex items-center gap-0.5 text-meta text-[var(--color-status-error)]"
            data-testid="pollinate-work-failed"
            title={work.current
              ? t('ui.PollinateOverlay.checksFailedTip', { latest: work.latest ?? '', current: work.current })
              : t('ui.PollinateOverlay.checksFailedTipNoCurrent', { latest: work.latest ?? '' })}
          >
            <span className="material-symbols-outlined icon-xs">error</span>
            {t('ui.PollinateOverlay.checksFailed')}
          </span>
        )}
      </div>
      <div className="text-meta tabular-nums text-[var(--color-text-faint)] truncate">
        {work.kind} · {work.current ?? '—'} · {fmtDate(work.updated)}
      </div>
      {openPath ? (
        <button
          type="button"
          onClick={() => onOpen(openPath)}
          data-testid={`pollinate-work-open-${work.path}`}
          className="self-start btn-sm btn-soft"
        >
          <span className="material-symbols-outlined icon-xs">open_in_new</span>
          {t('ui.PollinateOverlay.openInCanvas')}
        </button>
      ) : (
        <span className="text-meta text-[var(--color-text-faint)] truncate" title={work.path}>
          {work.inWorkspace ? t('ui.PollinateOverlay.notRendered') : t('ui.PollinateOverlay.outsideWorkspace')}
        </span>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`rounded-md border px-2.5 py-1.5 ${accent ? 'border-[color-mix(in_srgb,var(--color-status-warning)_50%,transparent)] bg-[color-mix(in_srgb,var(--color-status-warning)_10%,transparent)]' : 'border-[var(--color-border)] bg-[var(--color-card)]'}`}>
      <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)]">{label}</div>
      <div className={`text-body tabular-nums ${accent ? 'text-[var(--color-status-warning)]' : 'text-[var(--color-text)]'}`}>{value}</div>
    </div>
  );
}

function Select({ value, onChange, options, labels, placeholder, testid }: {
  value: string; onChange: (v: string) => void; options: string[];
  /** Shown text per option value; the value itself when absent. */
  labels?: Record<string, string>;
  placeholder: string; testid: string;
}) {
  const { t } = useTranslation();
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      data-testid={testid}
      className="px-2 py-1 text-small rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text-muted)]"
    >
      <option value="">{t('ui.PollinateOverlay.filterAll', { name: placeholder })}</option>
      {options.map((op) => <option key={op} value={op}>{labels?.[op] ?? op}</option>)}
    </select>
  );
}

function ContentCard({ card, defaultExpanded, onOpenAsset, onProduce }: {
  card: PollinateContentCard;
  defaultExpanded: boolean;
  onOpenAsset: (a: PollinateAsset) => void;
  onProduce: (platform: string) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div className="rounded-md border border-[var(--color-border)]" data-testid={`pollinate-card-${card.run}`}>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors text-left"
      >
        <span className="material-symbols-outlined icon-sm text-[var(--color-text-faint)]">
          {expanded ? 'expand_more' : 'chevron_right'}
        </span>
        <span className="text-body text-[var(--color-text)] truncate flex-1 min-w-0">{card.topic}</span>
        {card.domain && <span className="text-meta tabular-nums text-[var(--color-text-faint)] shrink-0">[{card.domain}]</span>}
        <span className="text-meta tabular-nums text-[var(--color-text-faint)] shrink-0">{fmtDate(card.createdAt)}</span>
        <span className="text-meta tabular-nums text-[var(--color-text-faint)] shrink-0">
          {card.publishedCount > 0 && <span className="text-[var(--color-status-success)]">{t('ui.PollinateOverlay.publishedCount', { count: card.publishedCount })} · </span>}
          {t('ui.PollinateOverlay.readyCount', { count: card.readyCount })}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] p-3">
          {card.assets.length === 0 ? (
            <div className="text-meta text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.noAssets')}</div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
              {card.assets.map((a, i) => (
                <button
                  key={`${a.filePath}-${i}`}
                  onClick={() => onOpenAsset(a)}
                  data-testid={`pollinate-asset-${card.run}-${i}`}
                  className="group rounded border border-[var(--color-border)] overflow-hidden hover:border-primary transition-colors text-left"
                >
                  <div className="aspect-[4/3] bg-[var(--color-bg)] flex items-center justify-center overflow-hidden">
                    {a.isImage ? (
                      <ImgWithFallback
                        src={assetThumbUrl(a.filePath)}
                        alt={a.fileName}
                        lazy
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <span className="material-symbols-outlined icon-xl text-[var(--color-text-faint)]">
                        {a.format === 'video' ? 'movie' : a.format === 'narrative' ? 'article' : a.format === 'readme' ? 'description' : 'draft'}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 px-1.5 py-1">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${stateDot(a.publishStatus)}`} />
                    <span className="text-meta text-[var(--color-text-muted)] truncate flex-1 min-w-0">
                      {a.platform || a.format}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
          {/* An in-progress topic can be pushed further — route to chat (Gate-1 #7). */}
          {(card.status === 'running' || card.status === 'review') && (
            <button
              onClick={() => onProduce(card.platforms[0] || 'the next platform')}
              data-testid={`pollinate-produce-${card.run}`}
              className="mt-2 btn-sm btn-soft"
            >
              <span className="material-symbols-outlined icon-xs">add</span>{t('ui.PollinateOverlay.produceMore')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** True when a text asset is a publish-KIT (structured metadata + frontmatter), not a
 *  clean caption body. The backend classifies publish-kit.md as format='caption', so we
 *  disambiguate by filename — a publish-kit's body would paste YAML+markdown scaffold
 *  into a post, so it's the LAST-resort caption source and gets an honest label. */
function isPublishKit(a: PollinateAsset): boolean {
  return /publish-kit/i.test(a.fileName);
}

/** The caption/narrative body an asset carries or points at. For a TEXT asset it's the
 *  asset's own file; for an IMAGE asset it's the sibling caption/narrative in the SAME
 *  card AND same TRUTHY platform (Gate-1 BLOCK: platform='' must NOT match across
 *  unrelated bare/tracks assets → return null, image-only). Gate-2 HIGH: prefer a REAL
 *  caption/narrative sibling over a publish-kit (whose body is metadata scaffold, not
 *  post text) — only fall back to the publish-kit when nothing cleaner exists. */
function resolveCaptionAsset(card: PollinateContentCard, asset: PollinateAsset): PollinateAsset | null {
  if (!asset.isImage) return asset; // a text asset IS its own body
  if (!asset.platform) return null;  // bare/tracks image → no reliable sibling key
  const siblings = card.assets.filter(
    (a) => a.platform === asset.platform && !a.isImage &&
      (a.format === 'caption' || a.format === 'narrative'),
  );
  // Prefer a clean caption/narrative; publish-kit is the last resort.
  return siblings.find((a) => !isPublishKit(a)) ?? siblings[0] ?? null;
}

function AssetDrawer({ card, asset, knownChannels, captionCache, onClose, onOpenAccount, onProduce }: {
  card: PollinateContentCard;
  asset: PollinateAsset;
  knownChannels: string[];
  captionCache: Map<string, string | null>;
  onClose: () => void;
  onOpenAccount: (platform: string) => void;
  onProduce: (platform: string) => void;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [copyErr, setCopyErr] = useState(false); // B7: clipboard-blocked was silent
  const [body, setBody] = useState<string | null>(null);
  const [bodyLoading, setBodyLoading] = useState(false);

  const captionAsset = resolveCaptionAsset(card, asset);
  const captionPath = captionAsset?.filePath ?? null;

  // Lazily fetch the caption/narrative BODY on open — ONE /workspace/file call, cached
  // in the parent-owned Map so re-opening is instant. Keyed on the primitive path
  // (stable string) → loop-safe. /pollinate/assets is never touched (87ms baseline).
  useEffect(() => {
    if (!captionPath) { setBody(null); return; }
    if (captionCache.has(captionPath)) { setBody(captionCache.get(captionPath) ?? null); return; }
    let cancelled = false;
    setBodyLoading(true);
    void pollinateService.fetchAssetBody(captionPath).then((txt) => {
      if (cancelled) return;
      // Cache only a SUCCESSFUL body (Gate-2 MED): caching null would make a transient
      // fetch failure permanent for the session — a null naturally re-fetches next open.
      if (txt !== null) captionCache.set(captionPath, txt);
      setBody(txt);
      setBodyLoading(false);
    });
    return () => { cancelled = true; };
  }, [captionPath, captionCache]);

  // Copy the real BODY text (the whole point — grab the caption to go publish);
  // fall back to the path only if the body couldn't be fetched.
  const doCopy = useCallback(async () => {
    setCopyErr(false);
    // copyToClipboard, not navigator.clipboard: the latter is denied in the Tauri webview.
    if (await copyToClipboard(body ?? asset.filePath)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      // B7: clipboard blocked was a silent no-op — the button showed no
      // 'Copied' and no error, so the user couldn't tell it failed.
      setCopyErr(true);
      setTimeout(() => setCopyErr(false), 2500);
    }
  }, [body, asset.filePath]);

  // Platforms this topic has NOT produced yet (known universe − produced) → offer to make.
  const missingPlatforms = knownChannels.filter((p) => !card.platforms.includes(p));

  return (
    <OverlayDrawer widthPx={460} maxWidthPct={92} z={10} testid="pollinate-asset-drawer" stopPropagation={false}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="text-small text-[var(--color-text)] truncate flex-1">{asset.platform || asset.format} · {asset.fileName}</span>
        <button onClick={onClose} className="material-symbols-outlined icon-md text-[var(--color-text-faint)] hover:text-[var(--color-text)]">close</button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
        <div className="text-meta tabular-nums text-[var(--color-text-faint)]">
          {card.topic} · {fmtDate(card.createdAt)}
        </div>
        {asset.isImage ? (
          <ImgWithFallback
            src={assetThumbUrl(asset.filePath)}
            alt={asset.fileName}
            className="w-full rounded border border-[var(--color-border)] min-h-[120px]"
          />
        ) : (
          <div className="rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-6 text-center">
            <span className="material-symbols-outlined icon-xl text-[var(--color-text-faint)]">
              {asset.format === 'video' ? 'movie' : 'description'}
            </span>
            <div className="text-meta text-[var(--color-text-muted)] mt-1 font-mono break-all">{asset.filePath}</div>
          </div>
        )}

        {/* Caption / narrative BODY — the text you grab to go publish (design §4, moment ②) */}
        {(captionPath || bodyLoading) && (
          <div>
            <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)] mb-1">
              {captionAsset && isPublishKit(captionAsset) ? t('ui.PollinateOverlay.publishKit')
                : captionAsset?.format === 'narrative' ? t('ui.PollinateOverlay.narrative') : t('ui.PollinateOverlay.caption')}
            </div>
            {bodyLoading ? (
              <div className="text-meta text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.loading')}</div>
            ) : body ? (
              <div
                data-testid="pollinate-caption-body"
                className="max-h-56 overflow-y-auto rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-small text-[var(--color-text-muted)] whitespace-pre-wrap leading-relaxed"
              >
                {body}
              </div>
            ) : (
              <div className="text-meta text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.captionUnavailable')}</div>
            )}
          </div>
        )}

        {/* Publish state (from the run's publish kit) */}
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${stateDot(asset.publishStatus)}`} />
          <span className="text-meta text-[var(--color-text-muted)]">
            {asset.publishStatus === 'published' ? t('ui.PollinateOverlay.statePublished')
              : asset.publishStatus === 'ready-to-publish' ? t('ui.PollinateOverlay.stateReadyToPublish')
              : t('ui.PollinateOverlay.stateReady')}
          </span>
        </div>

        <div className="flex gap-2 flex-wrap">
          <button
            onClick={doCopy}
            data-testid="pollinate-copy-btn"
            className="btn-sm btn-soft transition-colors"
          >
            <span className="material-symbols-outlined icon-xs">{copyErr ? 'error' : copied ? 'check' : 'content_copy'}</span>
            {copyErr ? t('ui.PollinateOverlay.copyBlocked') : copied ? t('ui.PollinateOverlay.copied') : (body ? t('ui.PollinateOverlay.copyCaption') : t('ui.PollinateOverlay.copyPath'))}
          </button>
          {asset.platform && (
            <button
              onClick={() => onOpenAccount(asset.platform)}
              data-testid="pollinate-open-account-btn"
              className="btn-md btn-ghost"
            >
              <span className="material-symbols-outlined icon-sm">open_in_new</span>{t('ui.PollinateOverlay.openPlatform', { platform: asset.platform })}
            </button>
          )}
        </div>

        {/* Same topic — platforms not yet produced → offer to make (design §4). */}
        {missingPlatforms.length > 0 && (
          <div>
            <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)] mb-1">{t('ui.PollinateOverlay.notProducedFor')}</div>
            <div className="flex gap-1.5 flex-wrap" data-testid="pollinate-missing-platforms">
              {missingPlatforms.map((p) => (
                <button
                  key={p}
                  onClick={() => onProduce(p)}
                  data-testid={`pollinate-produce-platform-${p}`}
                  className="btn-sm btn-secondary transition-colors"
                >
                  <span className="material-symbols-outlined icon-xs">add</span>{p}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </OverlayDrawer>
  );
}

function InsightsView({ data, loading }: { data: PollinateAssetsResponse | null; loading: boolean }) {
  const { t } = useTranslation();
  const insights = useMemo(() => {
    if (!data) return null;
    const cards = data.cards;
    const byType = data.overall.formatDist;
    // Seed with the KNOWN-CHANNEL universe (server SSOT) so a FULLY-neglected channel
    // (0 assets anywhere) still renders — that's the whole point of this coverage view
    // (design §4b). platform_dist alone can never reveal a channel with zero assets.
    const byChannel: Record<string, { total: number; published: number }> = {};
    for (const ch of data.overall.knownChannels) byChannel[ch] = { total: 0, published: 0 };
    for (const c of cards) {
      for (const a of c.assets) {
        if (!a.platform) continue;
        byChannel[a.platform] ??= { total: 0, published: 0 };
        byChannel[a.platform].total += 1;
        if (a.publishStatus === 'published') byChannel[a.platform].published += 1;
      }
    }
    const byDomain = data.overall.domainDist;
    // Production trend: content packages per ISO week.
    const trend: Record<string, number> = {};
    for (const c of cards) {
      const w = isoWeek(c.createdAt);
      if (w) trend[w] = (trend[w] ?? 0) + 1;
    }
    const trendPts = Object.entries(trend).sort(([a], [b]) => a.localeCompare(b));
    // Publish funnel: produced → ready-to-publish → published.
    const produced = data.overall.assetCount;
    const published = data.overall.published;
    const readyToPublish = cards.flatMap((c) => c.assets).filter((a) => a.publishStatus === 'ready-to-publish').length;
    return { byType, byChannel, byDomain, trendPts, produced, readyToPublish, published };
  }, [data]);

  if (loading && !insights) return <div className="flex-1 px-4 py-6 text-small text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.loading')}</div>;
  if (!insights) return <div className="flex-1 px-4 py-6 text-small text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.noData')}</div>;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4" data-testid="pollinate-insights">
      {/* Publish funnel — the most useful panel for a creator */}
      <Panel title={t('ui.PollinateOverlay.funnel')}>
        <div className="flex items-end gap-3">
          <FunnelBar label={t('ui.PollinateOverlay.produced')} value={insights.produced} max={insights.produced} />
          <FunnelBar label={t('ui.PollinateOverlay.readyToPublish')} value={insights.readyToPublish} max={insights.produced} />
          <FunnelBar label={t('ui.PollinateOverlay.statPublished')} value={insights.published} max={insights.produced} />
        </div>
        {insights.published === 0 && insights.produced > 0 && (
          <div className="text-meta text-[var(--color-status-warning)] mt-2">
            {t('ui.PollinateOverlay.noneMarked', { count: insights.produced })}
          </div>
        )}
      </Panel>

      <Panel title={t('ui.PollinateOverlay.byChannel')}>
        <BarList entries={Object.entries(insights.byChannel).map(([k, v]) => [k, v.total])}
                 sub={(k) => t('ui.PollinateOverlay.channelPublished', { published: insights.byChannel[k].published, total: insights.byChannel[k].total })} />
      </Panel>

      <Panel title={t('ui.PollinateOverlay.byType')}>
        <BarList entries={Object.entries(insights.byType)} />
      </Panel>

      <Panel title={t('ui.PollinateOverlay.byDomain')}>
        <BarList entries={Object.entries(insights.byDomain)} />
      </Panel>

      <Panel title={t('ui.PollinateOverlay.trend')}>
        {insights.trendPts.length === 0 ? (
          <div className="text-meta text-[var(--color-text-faint)]">{t('ui.PollinateOverlay.noDated')}</div>
        ) : (
          <div className="flex items-end gap-1 h-12">
            {insights.trendPts.map(([week, n]) => {
              const max = Math.max(...insights.trendPts.map(([, x]) => x), 1);
              return (
                <div key={week} className="flex-1 flex flex-col items-center justify-end" title={`${week}: ${n}`}>
                  <div className="w-full rounded-t bg-primary/40" style={{ height: `${(n / max) * 100}%`, minHeight: 2 }} />
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {/* Token panel intentionally ABSENT — pollinate can't attribute per-run tokens
          today (design §4b, ToDo 4053103d). Showing a zero/guessed number = fabricated metric. */}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-meta uppercase tracking-wide text-[var(--color-text-faint)] mb-1.5">{title}</div>
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5">{children}</div>
    </div>
  );
}

function BarList({ entries, sub }: { entries: [string, number][]; sub?: (k: string) => string }) {
  if (entries.length === 0) return <div className="text-meta text-[var(--color-text-faint)]">—</div>;
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <div className="space-y-1.5">
      {entries.sort(([, a], [, b]) => b - a).map(([k, v]) => (
        <div key={k} className="text-meta">
          <div className="flex justify-between text-[var(--color-text-muted)]">
            <span>{k}</span>
            <span className="tabular-nums">{sub ? sub(k) : v}</span>
          </div>
          <div className="h-1.5 rounded bg-[var(--color-hover)]">
            <div className="h-full rounded bg-primary/50" style={{ width: `${(v / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function FunnelBar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex-1 flex flex-col items-center">
      <div className="text-body tabular-nums text-[var(--color-text)]">{value}</div>
      <div className="w-full h-16 flex items-end">
        <div className="w-full rounded-t bg-primary/40" style={{ height: `${Math.max(pct, 3)}%` }} />
      </div>
      <div className="text-meta text-[var(--color-text-faint)] text-center mt-1">{label}</div>
    </div>
  );
}
