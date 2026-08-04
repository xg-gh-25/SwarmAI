/**
 * PollinateOverlay — the WORK-zone "Pollinate" content-asset gallery. A structural
 * sibling of PipelineOverlay (fullscreen Modal + fetch-once + absolute detail drawer),
 * but ASSET-CENTRIC: the first-class object is a produced media asset (poster / video
 * / narrative / readme) + where it's headed, NOT a run. The primary axis is
 * newest-first — the user comes to REVIEW what Swarm just made and go publish it.
 *
 * Two views (Gallery | Insights toggle, JobsRunsOverlay pattern):
 *  - Gallery: newest-first content cards (newest expanded), each an asset grid of
 *    platform×format cells (image thumbnails via /api/workspace/file/raw); a
 *    client-side search/filter/sort toolbar; an asset detail drawer (inline big image
 *    + copy-caption + open-account for manual publish).
 *  - Insights: 5 client-side rollups (by-type / by-channel / by-domain / production
 *    trend / publish funnel), all from the SAME fetched payload. NO token panel —
 *    pollinate can't attribute per-run tokens today (design §4b, ToDo 4053103d).
 *
 * NO LIVE POLLING — fetch-once on open (retro surface; a running pollinate lives in
 * its chat tab). Local state only — never MessageStore / active-tab mutation (OT01).
 * WRITES GO THROUGH CHAT (Gate-1 #7): "Produce for {platform}" / "Resume" inject a
 * chat prompt via onDispatch. Publish is manual-assist (copy caption + open account) —
 * auto-distribution is a parked ToDo (85f7f5d1).
 *
 * @exports PollinateOverlay
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { classifyLoadError } from '../../services/api';
import {
  pollinateService,
  assetThumbUrl,
  type PollinateAssetsResponse,
  type PollinateContentCard,
  type PollinateAsset,
} from '../../services/pollinate';

export interface PollinateOverlayProps {
  /** Hand a prompt to a chat tab (land+activate, THEN inject). Mirrors ChatPage's
   *  dispatch — a bare inject no-ops with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
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

function stateDot(s: string): string {
  if (s === 'published') return 'bg-emerald-500';
  if (s === 'ready-to-publish') return 'bg-sky-500';
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
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className={`flex items-center justify-center bg-[var(--color-bg)] ${className ?? ''}`} data-testid="pollinate-img-fallback">
        <span className="material-symbols-outlined text-[28px] text-[var(--color-text-faint)]" title={`Preview unavailable: ${alt}`}>
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

export function PollinateOverlay({ onDispatch }: PollinateOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-pollinate');
  const [data, setData] = useState<PollinateAssetsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<unknown>(null); // B3: fetch failed (was permanent Loading/blank). Stores the error so classifyLoadError can distinguish 4xx contract vs outage.
  const [reloadTick, setReloadTick] = useState(0);
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

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setLoadErr(null);
    // B3: a rejected fetch used to leave loading=true forever (blank gallery) —
    // now .catch surfaces an error state with Retry.
    pollinateService.fetchAssets()
      .then((d) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setLoadErr(e); setLoading(false); } });
    return () => { cancelled = true; };
  }, [open, reloadTick]);

  useEffect(() => {
    if (!open) {
      setView('gallery'); setQuery(''); setPlatform(''); setFormat('');
      setDomain(''); setToPublishOnly(false); setSort('newest'); setSelected(null);
    }
  }, [open]);

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
    <Modal isOpen={open} onClose={close} title="Pollinate" size="fullscreen" mode="POLLINATE" fullscreenWidth="xl">
      <div className="flex-1 min-h-0 flex flex-col relative" data-testid="pollinate-overlay">
        {/* Header: Gallery|Insights toggle */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border)]">
          <span className="text-xs font-medium text-[var(--color-text-muted)]">Content Assets</span>
          {loading && <span className="text-[11px] text-[var(--color-text-faint)]">Loading…</span>}
          <div className="flex-1" />
          {(['gallery', 'insights'] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              data-testid={`pollinate-view-${v}`}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                view === v ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {v === 'gallery' ? 'Gallery' : 'Insights'}
            </button>
          ))}
        </div>

        {/* Overall strip (both views) */}
        {o && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 px-4 py-2 border-b border-[var(--color-border)]" data-testid="pollinate-overall">
            <Stat label="Topics" value={String(o.cardCount)} />
            <Stat label="Assets" value={String(o.assetCount)} />
            <Stat label="Ready" value={String(o.ready)} />
            <Stat label="Published" value={String(o.published)} />
            <Stat label="In progress" value={String(o.inProgress)} accent={o.inProgress > 0} />
            <Stat label="Channels" value={String(Object.keys(o.platformDist).length)} />
          </div>
        )}

        {view === 'gallery' ? (
          <>
            {/* Toolbar: search + filters + sort (client-side) */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border)] flex-wrap">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="🔍 search topic / caption / domain…"
                data-testid="pollinate-search"
                className="flex-1 min-w-[160px] px-2.5 py-1 text-xs rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
              />
              <Select value={platformFilter} onChange={setPlatform} options={allPlatforms} placeholder="platform" testid="pollinate-filter-platform" />
              <Select value={formatFilter} onChange={setFormat} options={allFormats} placeholder="format" testid="pollinate-filter-format" />
              {/* ⚪ to-publish state chip — surface what still needs posting (design §1.3). */}
              <button
                onClick={() => setToPublishOnly((v) => !v)}
                data-testid="pollinate-chip-to-publish"
                className={`px-2.5 py-1 text-[11px] font-medium rounded-full border transition-colors whitespace-nowrap ${
                  toPublishOnly
                    ? 'bg-primary/15 text-primary border-primary'
                    : 'text-[var(--color-text-muted)] border-[var(--color-border)] hover:bg-[var(--color-hover)]'
                }`}
              >
                ⚪ to publish
              </button>
              <Select
                value={sort}
                onChange={(v) => setSort(v as Sort)}
                options={['newest', 'to-publish']}
                placeholder="sort"
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
                    className={`px-2.5 py-0.5 text-[11px] font-medium rounded-full border transition-colors whitespace-nowrap ${
                      domainFilter === d
                        ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/60'
                        : 'text-emerald-500/80 border-emerald-500/30 hover:bg-emerald-500/10'
                    }`}
                  >
                    {d}
                  </button>
                ))}
              </div>
            )}

            {/* Body: content cards (newest-first, newest expanded) */}
            <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
              {/* Fetch failure (B3) — distinct from "no content", with Retry. */}
              {!!loadErr && !loading && (
                <div
                  data-testid="pollinate-load-error"
                  className="mx-auto my-8 max-w-sm rounded-lg border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-4 py-4 text-center"
                >
                  <div className="text-sm text-[var(--color-text)]">{classifyLoadError(loadErr, 'content assets')}</div>
                  <button
                    data-testid="pollinate-load-retry"
                    onClick={() => setReloadTick((t) => t + 1)}
                    className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
                    style={{ background: '#d0524a' }}
                  >
                    Retry
                  </button>
                </div>
              )}
              {!loading && !loadErr && filtered.length === 0 && (
                <div className="text-center py-10 text-sm text-[var(--color-text-faint)]">
                  {cards.length === 0
                    ? 'No content yet. Ask Swarm in chat to pollinate a topic.'
                    : 'No assets match your search.'}
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
          </>
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
    </Modal>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`rounded-md border px-2.5 py-1.5 ${accent ? 'border-amber-500/50 bg-amber-500/10' : 'border-[var(--color-border)] bg-[var(--color-card)]'}`}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{label}</div>
      <div className={`text-sm font-mono ${accent ? 'text-amber-500' : 'text-[var(--color-text)]'}`}>{value}</div>
    </div>
  );
}

function Select({ value, onChange, options, placeholder, testid }: {
  value: string; onChange: (v: string) => void; options: string[]; placeholder: string; testid: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      data-testid={testid}
      className="px-2 py-1 text-xs rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text-muted)]"
    >
      <option value="">{placeholder}: all</option>
      {options.map((op) => <option key={op} value={op}>{op}</option>)}
    </select>
  );
}

function ContentCard({ card, defaultExpanded, onOpenAsset, onProduce }: {
  card: PollinateContentCard;
  defaultExpanded: boolean;
  onOpenAsset: (a: PollinateAsset) => void;
  onProduce: (platform: string) => void;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return (
    <div className="rounded-md border border-[var(--color-border)]" data-testid={`pollinate-card-${card.run}`}>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors text-left"
      >
        <span className="material-symbols-outlined text-[16px] text-[var(--color-text-faint)]">
          {expanded ? 'expand_more' : 'chevron_right'}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)] truncate flex-1 min-w-0">{card.topic}</span>
        {card.domain && <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">[{card.domain}]</span>}
        <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">{fmtDate(card.createdAt)}</span>
        <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">
          {card.publishedCount > 0 && <span className="text-emerald-500">🟢{card.publishedCount} </span>}
          ⚪{card.readyCount}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] p-3">
          {card.assets.length === 0 ? (
            <div className="text-[11px] text-[var(--color-text-faint)]">No assets produced yet.</div>
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
                      <span className="material-symbols-outlined text-[28px] text-[var(--color-text-faint)]">
                        {a.format === 'video' ? 'movie' : a.format === 'narrative' ? 'article' : a.format === 'readme' ? 'description' : 'draft'}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 px-1.5 py-1">
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${stateDot(a.publishStatus)}`} />
                    <span className="text-[10px] text-[var(--color-text-muted)] truncate flex-1 min-w-0">
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
              className="mt-2 flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-md text-primary hover:bg-primary/10 transition-colors"
            >
              <span className="material-symbols-outlined text-[14px]">add</span>Produce more
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
    try {
      await navigator.clipboard.writeText(body ?? asset.filePath);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // B7: clipboard blocked was a silent no-op — the button showed no
      // 'Copied' and no error, so the user couldn't tell it failed.
      setCopyErr(true);
      setTimeout(() => setCopyErr(false), 2500);
    }
  }, [body, asset.filePath]);

  // Platforms this topic has NOT produced yet (known universe − produced) → offer to make.
  const missingPlatforms = knownChannels.filter((p) => !card.platforms.includes(p));

  return (
    <div
      className="absolute top-0 right-0 bottom-0 w-[460px] max-w-[92%] z-10 bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-xl flex flex-col"
      data-testid="pollinate-asset-drawer"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="text-xs font-medium text-[var(--color-text)] truncate flex-1">{asset.platform || asset.format} · {asset.fileName}</span>
        <button onClick={onClose} className="material-symbols-outlined text-[18px] text-[var(--color-text-faint)] hover:text-[var(--color-text)]">close</button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
        <div className="text-[11px] font-mono text-[var(--color-text-faint)]">
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
            <span className="material-symbols-outlined text-[36px] text-[var(--color-text-faint)]">
              {asset.format === 'video' ? 'movie' : 'description'}
            </span>
            <div className="text-[11px] text-[var(--color-text-muted)] mt-1 font-mono break-all">{asset.filePath}</div>
          </div>
        )}

        {/* Caption / narrative BODY — the text you grab to go publish (design §4, moment ②) */}
        {(captionPath || bodyLoading) && (
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">
              {captionAsset && isPublishKit(captionAsset) ? 'Publish kit'
                : captionAsset?.format === 'narrative' ? 'Narrative' : 'Caption'}
            </div>
            {bodyLoading ? (
              <div className="text-[11px] text-[var(--color-text-faint)]">Loading…</div>
            ) : body ? (
              <div
                data-testid="pollinate-caption-body"
                className="max-h-56 overflow-y-auto rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12px] text-[var(--color-text-muted)] whitespace-pre-wrap leading-relaxed"
              >
                {body}
              </div>
            ) : (
              <div className="text-[11px] text-[var(--color-text-faint)]">Caption body unavailable.</div>
            )}
          </div>
        )}

        {/* Publish state + manual-assist actions */}
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${stateDot(asset.publishStatus)}`} />
          <span className="text-[11px] text-[var(--color-text-muted)]">
            {asset.publishStatus === 'published' ? 'published'
              : asset.publishStatus === 'ready-to-publish' ? 'ready to publish'
              : 'ready'}
          </span>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={doCopy}
            data-testid="pollinate-copy-btn"
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 transition-colors"
          >
            <span className="material-symbols-outlined text-[15px]">{copyErr ? 'error' : copied ? 'check' : 'content_copy'}</span>
            {copyErr ? 'Copy blocked' : copied ? 'Copied' : (body ? 'Copy caption' : 'Copy path')}
          </button>
          {asset.platform && (
            <button
              onClick={() => onOpenAccount(asset.platform)}
              data-testid="pollinate-open-account-btn"
              className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors"
            >
              <span className="material-symbols-outlined text-[15px]">open_in_new</span>Open {asset.platform}
            </button>
          )}
        </div>

        {/* Same topic — platforms not yet produced → offer to make (design §4). */}
        {missingPlatforms.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Not yet produced for</div>
            <div className="flex gap-1.5 flex-wrap" data-testid="pollinate-missing-platforms">
              {missingPlatforms.map((p) => (
                <button
                  key={p}
                  onClick={() => onProduce(p)}
                  data-testid={`pollinate-produce-platform-${p}`}
                  className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-md text-[var(--color-text-muted)] border border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors"
                >
                  <span className="material-symbols-outlined text-[13px]">add</span>{p}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function InsightsView({ data, loading }: { data: PollinateAssetsResponse | null; loading: boolean }) {
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

  if (loading && !insights) return <div className="flex-1 px-4 py-6 text-xs text-[var(--color-text-faint)]">Loading…</div>;
  if (!insights) return <div className="flex-1 px-4 py-6 text-xs text-[var(--color-text-faint)]">No data.</div>;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4" data-testid="pollinate-insights">
      {/* Publish funnel — the most useful panel for a creator */}
      <Panel title="Publish funnel">
        <div className="flex items-end gap-3">
          <FunnelBar label="Produced" value={insights.produced} max={insights.produced} />
          <FunnelBar label="Ready to publish" value={insights.readyToPublish} max={insights.produced} />
          <FunnelBar label="Published" value={insights.published} max={insights.produced} />
        </div>
        {insights.published === 0 && insights.produced > 0 && (
          <div className="text-[11px] text-amber-500 mt-2">
            {insights.produced} assets made, 0 marked published — a posted-URL state exists once auto-distribution ships.
          </div>
        )}
      </Panel>

      <Panel title="By channel — coverage (published / total)">
        <BarList entries={Object.entries(insights.byChannel).map(([k, v]) => [k, v.total])}
                 sub={(k) => `${insights.byChannel[k].published}/${insights.byChannel[k].total} published`} />
      </Panel>

      <Panel title="By content-type">
        <BarList entries={Object.entries(insights.byType)} />
      </Panel>

      <Panel title="By domain">
        <BarList entries={Object.entries(insights.byDomain)} />
      </Panel>

      <Panel title="Production trend — topics per week">
        {insights.trendPts.length === 0 ? (
          <div className="text-[11px] text-[var(--color-text-faint)]">No dated topics.</div>
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
      <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1.5">{title}</div>
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2.5">{children}</div>
    </div>
  );
}

function BarList({ entries, sub }: { entries: [string, number][]; sub?: (k: string) => string }) {
  if (entries.length === 0) return <div className="text-[11px] text-[var(--color-text-faint)]">—</div>;
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <div className="space-y-1.5">
      {entries.sort(([, a], [, b]) => b - a).map(([k, v]) => (
        <div key={k} className="text-[11px]">
          <div className="flex justify-between text-[var(--color-text-muted)]">
            <span>{k}</span>
            <span className="font-mono">{sub ? sub(k) : v}</span>
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
      <div className="text-sm font-mono text-[var(--color-text)]">{value}</div>
      <div className="w-full h-16 flex items-end">
        <div className="w-full rounded-t bg-primary/40" style={{ height: `${Math.max(pct, 3)}%` }} />
      </div>
      <div className="text-[10px] text-[var(--color-text-faint)] text-center mt-1">{label}</div>
    </div>
  );
}
