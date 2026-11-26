/**
 * PollinateOverlay — the Work zone's Pollinate surface, laid out like the Pipeline overlay
 * (design: Knowledge/Mockups/2026-11-26-pollinate-overlay-redesign.html).
 *
 * Two tabs under one filter row (kind, and a Brain made from the rows; switching tabs keeps it):
 * - **Works** (default): every work with an open revision as a card — cover, title, how long
 *   the revision has been open, Brain · kind · size · pages, the version track (one dot per
 *   version: the person's in their colour and marked, a failed one hollow, the current one
 *   ringed, the open revision last), and the revision's scope and note with Open in Canvas
 *   and Open session. Every other work as a row: thumbnail, title, Brain · kind · size ·
 *   current version, date and version count, Open in Canvas on hover.
 * - **Eval**: the share of revisions whose first cut passed, 15 days against the 15 before,
 *   30 daily bars, four numbers, which checks fail most and what the guard stopped.
 * A click on a card or row opens the work's big card (PollinateWorkCard).
 *
 * A failed version or a guard block is the agent's to fix, never the person's: it is shown
 * in a muted tone, never in the attention or error colour (the chat's rule for a tool
 * failure the agent recovered from). With no work registered there is one empty state and
 * no tabs or filters.
 *
 * Data: GET /pollinate/works and /pollinate/eval through the shared overlay queries. The
 * overlay writes nothing: Open session opens the work's chat with onContinue, continue in
 * chat fills a tab's composer with onDispatch. Local state only (never MessageStore).
 *
 * @exports PollinateContent, PollinateContentProps, VersionTrack
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { overlayQuery, invalidateOverlay } from './overlayQueries';
import { classifyLoadError } from '../../services/api';
import { assetThumbUrl, type PollinateEval, type PollinateWork } from '../../services/pollinate';
import { Tabs } from '../overlay';
import { OverlayError } from './OverlayState';
import { PollinateWorkCard, sizeText } from './PollinateWorkCard';

export interface PollinateContentProps {
  /** Put a prompt in a chat tab's composer (land+activate, THEN inject); true when it landed. */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close. */
  close: () => void;
  /** Open *sessionId* in a tab with *prompt* in its composer; false when it could not be opened. */
  onContinue?: (sessionId: string, prompt: string) => Promise<boolean>;
}

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const KINDS = ['deck', 'poster', 'page', 'copy', 'video'] as const;
const ROWS_FIRST = 12;
const ROWS_PAGE = 20;
const COMPACT_PX = 720;

/** Open a workspace file in the current tab's Canvas: close the overlay first so the
 *  Canvas is not under it, then the swarm:open-file event the other overlays send. */
export function openInCanvas(path: string, close: () => void): void {
  close();
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } }));
}

function since(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const min = Math.max(0, Math.round((now - t) / 60000));
  if (min < 60) return `${min} min`;
  if (min < 60 * 48) return `${Math.round(min / 60)} h`;
  return `${Math.round(min / 1440)} d`;
}

function day(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
}

/** The cover, or the kind's glyph when there is no image (yet, or outside the workspace). */
function Cover({ w, className }: { w: PollinateWork; className: string }) {
  const [failed, setFailed] = useState(false);
  const thumb = w.inWorkspace ? w.thumbnail : null;
  if (thumb && !failed) {
    return <img src={assetThumbUrl(thumb)} alt="" loading="lazy" className={`${className} object-cover`} onError={() => setFailed(true)} />;
  }
  const icon = { deck: 'slideshow', poster: 'image', page: 'article', copy: 'notes', video: 'movie' }[w.kind] ?? 'draft';
  return (
    <span className={`${className} grid place-items-center bg-[var(--color-bg-chrome)]`}>
      <span className={`material-symbols-outlined icon-lg ${dim}`}>{icon}</span>
    </span>
  );
}

/** Brain · kind · size · pages (a deck) · duration and style (a video) · outside the workspace. */
function Meta({ w, version }: { w: PollinateWork; version?: boolean }) {
  const { t } = useTranslation();
  const bits = [
    sizeText(w),
    w.kind === 'deck' && w.slideCount != null ? t('ui.PollinateOverlay.pages', { count: w.slideCount }) : null,
    w.kind === 'video' && w.durationS != null ? t('ui.PollinateOverlay.seconds', { count: Math.round(w.durationS) }) : null,
    w.kind === 'video' ? w.style : null,
    version && w.current ? w.current : null,
  ].filter(Boolean) as string[];
  return (
    <div className={`flex flex-wrap items-center gap-x-3.5 gap-y-1 text-small min-w-0 ${muted}`}>
      <span className="truncate"><b className="font-semibold text-[var(--color-text)]">{w.brain}</b> · {t(`ui.PollinateOverlay.kind_${w.kind}`, { defaultValue: w.kind })}</span>
      {bits.map((b) => <span key={b} className="tabular-nums whitespace-nowrap">{b}</span>)}
      {!w.inWorkspace && (
        <span className="px-1.5 rounded text-[10.5px] leading-[15px] font-semibold whitespace-nowrap text-[var(--color-text-secondary)] bg-[var(--color-bg-chrome)]"
          style={{ boxShadow: 'inset 0 0 0 1px var(--color-border)' }}>{t('ui.PollinateOverlay.outsideWorkspace')}</span>
      )}
    </div>
  );
}

/** The versions of a work, the last six, then the open revision. Person versions in their
 *  own colour, failed ones hollow (muted, never red), the current one ringed. */
export function VersionTrack({ w }: { w: PollinateWork }) {
  const { t } = useTranslation();
  const shown = w.versions.slice(-6);
  const hidden = w.versions.length - shown.length;
  const you = 'var(--line-person)';
  const busy = 'var(--color-status-running)';
  const scopeText = w.open ? t('ui.PollinateOverlay.scopePages', { count: w.open.scope.length }) : '';
  return (
    <ol className="flex mt-3.5 m-0 p-0 list-none min-w-0" data-testid={`pollinate-track-${w.path}`}>
      {hidden > 0 && (
        <li className="relative pt-5 flex-none w-12 text-meta tabular-nums" data-testid="pollinate-track-more">
          <span className={`absolute -top-[3px] left-0 ${dim}`}>+{hidden}</span>
        </li>
      )}
      {shown.map((v) => {
        const isCur = v.id === w.current;
        const bad = v.status === 'failed';
        const c = v.by === 'user' ? you : busy;
        const sub = v.by === 'user' ? (v.foundOnDisk ? t('ui.PollinateOverlay.youEdited') : t('ui.PollinateOverlay.you')) : '';
        return (
          <li key={v.id} className="relative pt-5 flex-1 min-w-0 max-w-[96px]" data-version={v.id}
            data-state={bad ? 'failed' : isCur ? 'current' : 'passed'} data-by={v.by}>
            <span aria-hidden className="absolute top-[5px] left-[14px] right-[2px] h-[2px] rounded-sm"
              style={{ background: `color-mix(in srgb, ${busy} 40%, transparent)` }} />
            <span aria-hidden className="absolute top-0 left-0 w-3 h-3 rounded-full"
              style={bad ? { background: 'var(--color-bg)', boxShadow: 'inset 0 0 0 2px var(--color-text-dim)' }
                : isCur ? { background: c, boxShadow: `0 0 0 4px color-mix(in srgb, ${c} 20%, transparent)` }
                  : { background: `color-mix(in srgb, ${c} 55%, transparent)` }} />
            <div className={`text-meta tabular-nums truncate ${isCur ? 'font-semibold text-[var(--color-text)]' : muted}`}>{v.id}</div>
            <div className="text-meta truncate" style={v.by === 'user' ? { color: `color-mix(in srgb, ${you} 80%, var(--color-text-muted))` } : undefined}>
              <span className={v.by === 'user' ? '' : dim}>{sub}</span>
              <span className="sr-only"> {bad ? t('ui.PollinateOverlay.failedVersion') : isCur ? t('ui.PollinateOverlay.currentVersion') : ''}</span>
            </div>
          </li>
        );
      })}
      {w.open && (
        <li className="relative pt-5 flex-1 min-w-0 max-w-[96px]" data-state="open">
          <span aria-hidden className="absolute top-0 left-0 w-3 h-3 rounded-full"
            style={{ background: 'var(--color-bg)', boxShadow: `inset 0 0 0 2px ${busy}` }} />
          <div className="text-meta font-semibold truncate" style={{ color: busy }}>{t('ui.PollinateOverlay.revising')}</div>
          <div className={`text-meta truncate ${dim}`}>{scopeText}</div>
        </li>
      )}
    </ol>
  );
}

function WorkCard({ w, now, onOpen, onCanvas, onSession }: {
  w: PollinateWork; now: number; onOpen: () => void; onCanvas: (() => void) | null; onSession: (() => void) | null;
}) {
  const { t } = useTranslation();
  const op = w.open!;
  return (
    <div role="button" tabIndex={0} aria-haspopup="dialog" onClick={onOpen}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
      data-testid={`pollinate-card-${w.path}`}
      className="so-card so-card--click relative grid grid-cols-1 sm:grid-cols-[168px_minmax(0,1fr)] gap-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-3.5 cursor-pointer min-w-0">
      <Cover w={w} className="hidden sm:block w-full aspect-video rounded-md overflow-hidden" />
      <div className="min-w-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <span aria-hidden className="w-2 h-2 rounded-full flex-none" style={{ background: 'var(--color-status-running)' }} />
          <span className="flex-1 min-w-0 truncate text-heading font-semibold" title={w.title}>{w.title}</span>
          {since(op.at, now) && (
            <span className={`text-small whitespace-nowrap tabular-nums ${muted}`}>
              {t('ui.PollinateOverlay.openFor')} <b className="font-semibold text-[var(--color-text-secondary)]">{since(op.at, now)}</b>
            </span>
          )}
        </div>
        <div className="mt-1.5"><Meta w={w} /></div>
        <VersionTrack w={w} />
        <div className="mt-3 flex items-center gap-2 px-3 py-2 rounded-lg text-body min-w-0"
          style={{ background: 'color-mix(in srgb, var(--color-status-running) 6%, transparent)' }}>
          <span className="flex-1 min-w-0 truncate text-[var(--color-text-secondary)]" title={op.note ?? undefined} data-testid="pollinate-card-scope">
            <b className="font-semibold text-[var(--color-text)]">{t('ui.PollinateOverlay.changes', { count: op.scope.length })}</b>
            {op.note ? ` · ${op.note}` : ''}
          </span>
          {onCanvas && <button type="button" className="btn-sm btn-secondary whitespace-nowrap" data-testid="pollinate-card-canvas"
            onClick={(e) => { e.stopPropagation(); onCanvas(); }}>{t('ui.PollinateOverlay.openInCanvas')}</button>}
          {onSession && <button type="button" className="btn-sm btn-ghost whitespace-nowrap" data-testid="pollinate-card-session"
            onClick={(e) => { e.stopPropagation(); onSession(); }}>{t('ui.PollinateOverlay.openSession')} ↗</button>}
        </div>
      </div>
    </div>
  );
}

function WorkRow({ w, onOpen, onCanvas }: { w: PollinateWork; onOpen: () => void; onCanvas: (() => void) | null }) {
  const { t } = useTranslation();
  const n = w.versions.length;
  return (
    <div role="button" tabIndex={0} aria-haspopup="dialog" onClick={onOpen}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
      className="group relative grid grid-cols-[64px_minmax(0,1fr)_auto] gap-x-3.5 items-center px-2.5 py-2 -mx-2.5 rounded-lg cursor-pointer hover:bg-[var(--color-hover)]"
      data-testid={`pollinate-row-${w.path}`}>
      <Cover w={w} className="w-16 aspect-[16/10] rounded-[5px] overflow-hidden" />
      <div className="min-w-0">
        <div className="text-body truncate" title={w.title}>{w.title}</div>
        <div className="mt-px"><Meta w={w} version /></div>
      </div>
      <div className="text-small whitespace-nowrap tabular-nums text-right group-hover:invisible">
        <span className="text-[var(--color-text-secondary)]">{day(w.updated)}</span>
        {w.kind !== 'video' && n > 0 && <span className={dim}> · {t('ui.PollinateOverlay.versionCount', { count: n })}</span>}
      </div>
      {onCanvas && (
        <button type="button" className="btn-sm btn-secondary absolute right-2.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 focus:opacity-100"
          data-testid="pollinate-row-canvas" onClick={(e) => { e.stopPropagation(); onCanvas(); }}>{t('ui.PollinateOverlay.openInCanvas')} ↗</button>
      )}
    </div>
  );
}

function SectionHead({ title, count, testId }: { title: string; count: number; testId: string }) {
  return (
    <div className="flex items-baseline gap-2 mb-2.5 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]" data-testid={testId}>
      <span>{title}</span>
      <span className="inline-block min-w-[18px] px-1.5 rounded-full bg-[var(--color-bg-chrome)] text-center tabular-nums text-[var(--color-text-secondary)]">{count}</span>
    </div>
  );
}

function EvalView({ e, compact, onOpenPath }: { e: PollinateEval; compact: boolean; onOpenPath: (path: string) => void }) {
  const { t } = useTranslation();
  const max = Math.max(1, ...e.daily.map((d) => d.firstPass + d.later + d.notPassed));
  const half = Math.floor(e.daily.length / 2);
  const cur = e.rate.cur;
  const num = (v: number | null) => (v == null ? '—' : String(Math.round(v * 10) / 10));
  const four: [string, number | null, number | null][] = [
    [t('ui.PollinateOverlay.evWorks'), e.worksChanged.cur, e.worksChanged.prev],
    [t('ui.PollinateOverlay.evVersions'), e.versionsMedian.cur, e.versionsMedian.prev],
    [t('ui.PollinateOverlay.evPerson'), e.personVersions.cur, e.personVersions.prev],
    [t('ui.PollinateOverlay.evGuard'), e.guardBlocks.cur, e.guardBlocks.prev],
  ];
  const top = e.checkHits[0]?.count ?? 1;
  const box = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
  return (
    <div data-testid="pollinate-eval">
      <section className={`${box} px-[18px] py-4`}>
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-[28px] leading-[34px] font-semibold tabular-nums" data-testid="pollinate-eval-rate">
            {cur ? `${cur.pct}%` : '—'}
            {cur && <span className={`text-title font-normal ${dim}`}> · {cur.passed} / {cur.revisions}</span>}
          </span>
          <span className="flex-1 text-body">{t('ui.PollinateOverlay.evRate', { days: e.days })}</span>
          <span className={`text-small px-2 py-px rounded-full bg-[var(--color-bg-chrome)] ${muted}`}>
            {t('ui.PollinateOverlay.evBefore', { days: e.days })} {e.rate.prev ? `${e.rate.prev.pct}%` : '—'}
          </span>
        </div>
        <div className="flex items-end gap-[3px] h-[84px] mt-[18px] border-b border-[var(--color-border)]" aria-hidden>
          {e.daily.map((d, i) => {
            const h = (x: number) => `${(x / max) * 80}px`;
            return (
              <div key={d.date} className={`flex-1 flex flex-col justify-end h-full ${i < half ? 'opacity-40' : ''} ${i === half ? 'border-l border-dashed border-[var(--color-border)]' : ''}`}
                title={`${d.date} · ${t('ui.PollinateOverlay.evFirst')} ${d.firstPass} · ${t('ui.PollinateOverlay.evLater')} ${d.later} · ${t('ui.PollinateOverlay.evNot')} ${d.notPassed}`}>
                {d.notPassed > 0 && <i className="block" style={{ height: h(d.notPassed), background: 'color-mix(in srgb, var(--color-text-dim) 70%, transparent)' }} />}
                {d.later > 0 && <i className="block" style={{ height: h(d.later), background: 'var(--color-status-warning)' }} />}
                {d.firstPass > 0 && <i className="block rounded-t-[2px]" style={{ height: h(d.firstPass), background: 'var(--color-status-success)' }} />}
              </div>
            );
          })}
        </div>
        <div className={`flex justify-between mt-1 text-meta ${dim}`}>
          <span>{t('ui.PollinateOverlay.evAgo', { days: e.days * 2 })}</span><span>{t('ui.PollinateOverlay.evAgo', { days: e.days })}</span><span>{t('ui.PollinateOverlay.evToday')}</span>
        </div>
        <div className={`flex flex-wrap gap-x-4 gap-y-1 mt-2 text-small ${muted}`}>
          <span><i className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: 'var(--color-status-success)' }} />{t('ui.PollinateOverlay.evFirst')}</span>
          <span><i className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: 'var(--color-status-warning)' }} />{t('ui.PollinateOverlay.evLater')}</span>
          <span><i className="inline-block w-2 h-2 rounded-sm mr-1.5" style={{ background: 'color-mix(in srgb, var(--color-text-dim) 70%, transparent)' }} />{t('ui.PollinateOverlay.evNot')}</span>
        </div>
      </section>
      <div className={`grid gap-2.5 mt-2.5 ${compact ? 'grid-cols-2' : 'grid-cols-4'}`}>
        {four.map(([k, c, p]) => (
          <div key={k} className={`${box} px-3.5 py-3`} data-testid="pollinate-eval-number">
            <div className={`text-small ${muted}`}>{k}</div>
            <div className="text-[20px] leading-7 font-semibold tabular-nums">{num(c)}</div>
            <div className={`text-small ${dim}`}>{t('ui.PollinateOverlay.evBefore', { days: e.days })} {num(p)}</div>
          </div>
        ))}
      </div>
      {e.checkHits.length > 0 && (
        <section className={`${box} px-[18px] py-4 mt-8`} data-testid="pollinate-eval-hits">
          <div className="flex items-baseline gap-2 mb-2">
            <span className="text-body font-semibold">{t('ui.PollinateOverlay.evHits')}</span>
          </div>
          <div className="flex flex-col gap-[7px]">
            {e.checkHits.map((h, i) => (
              <div key={h.check} className={`grid items-center gap-3 text-body ${compact ? 'grid-cols-[96px_minmax(0,1fr)_40px]' : 'grid-cols-[110px_minmax(0,1fr)_200px]'}`}>
                <span className="font-mono text-small text-[var(--color-text-secondary)]">{h.check}</span>
                <span className="h-1.5 rounded bg-[var(--color-hover)] overflow-hidden">
                  <i className="block h-full" style={{ width: `${(h.count / top) * 100}%`, background: i === 0 ? 'var(--color-status-running)' : 'color-mix(in srgb, var(--color-status-running) 55%, transparent)' }} />
                </span>
                <span className={`text-small truncate ${muted}`}><b className="font-semibold tabular-nums text-[var(--color-text-secondary)]">{h.count}</b>
                  {!compact && ` · ${h.topTitle} ${h.topCount}`}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      {e.guardList.length > 0 && (
        <section className="mt-8" data-testid="pollinate-eval-guard">
          <SectionHead title={t('ui.PollinateOverlay.evGuardList')} count={e.guardList.length} testId="pollinate-eval-guard-head" />
          {e.guardList.map((g) => (
            <div key={`${g.path}${g.version}`} role="button" tabIndex={0} onClick={() => onOpenPath(g.path)}
              onKeyDown={(ev) => { if (ev.key === 'Enter') onOpenPath(g.path); }}
              className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 px-2 py-2 -mx-2 rounded-md cursor-pointer hover:bg-[var(--color-hover)] text-body">
              <div className="min-w-0">
                <div className="truncate">{g.title} <span className={`text-small tabular-nums ${muted}`}>· {g.version}</span></div>
                {/* the guard did its job: muted, never the attention colour */}
                <div className="text-small text-[var(--color-text-secondary)] truncate" data-testid="pollinate-guard-why">{g.violations.join(' · ')}</div>
              </div>
              <span className={`text-small ${muted}`}>{t('ui.PollinateOverlay.open')} ›</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}

export function PollinateContent({ onDispatch, close, onContinue }: PollinateContentProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const worksQ = useQuery(overlayQuery.pollinateWorks());
  const works = useMemo(() => worksQ.data?.works ?? [], [worksQ.data]);
  const worksErr: unknown = worksQ.isError && !worksQ.data ? worksQ.error : null;
  const has = works.length > 0;
  const evalQ = useQuery({ ...overlayQuery.pollinateEval(), enabled: has });
  const [tab, setTab] = useState<'works' | 'eval'>('works');
  const [kind, setKind] = useState<string>('');
  const [brain, setBrain] = useState<string>('');
  const [shown, setShown] = useState(ROWS_FIRST);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 60_000); return () => window.clearInterval(id); }, []);
  const [box, setBox] = useState<HTMLDivElement | null>(null);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    if (!box || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([en]) => setCompact(en.contentRect.width < COMPACT_PX));
    ro.observe(box);
    return () => ro.disconnect();
  }, [box]);

  const brains = useMemo(() => Array.from(new Set(works.map((w) => w.brain))).sort(), [works]);
  const kinds = useMemo(() => KINDS.filter((k) => works.some((w) => w.kind === k)), [works]);
  const filtered = useMemo(() => works.filter((w) => (!kind || w.kind === kind) && (!brain || w.brain === brain)), [works, kind, brain]);
  const revising = filtered.filter((w) => w.open);
  const rest = filtered.filter((w) => !w.open);
  const reload = useCallback(() => { void invalidateOverlay(qc, 'pollinate'); }, [qc]);

  const canvas = (w: PollinateWork) => (w.inWorkspace && w.mainOutput ? () => openInCanvas(w.mainOutput!, close) : null);
  const continueInChat = (w: PollinateWork) => {
    if (onDispatch(t('ui.PollinateOverlay.continuePrompt', { kind: t(`ui.PollinateOverlay.kind_${w.kind}`, { defaultValue: w.kind }), path: w.path, title: w.title }))) {
      requestAnimationFrame(() => requestAnimationFrame(() => close()));
    }
  };
  // the chat that is changing the work; when it is gone (deleted, cannot be resumed) the
  // work goes to a chat tab instead, so the click always lands somewhere
  const openSession = (sid: string, w: PollinateWork) => {
    if (!onContinue) { continueInChat(w); return; }
    void onContinue(sid, '').then((ok) => { if (ok) close(); else continueInChat(w); });
  };
  const session = (w: PollinateWork) => {
    const sid = w.open?.session ?? w.versions[w.versions.length - 1]?.session ?? null;
    return sid ? () => openSession(sid, w) : null;
  };
  const order = [...revising, ...rest].map((w) => w.path);

  const segment = (k: string, label: string) => (
    <button key={k || 'all'} type="button" role="radio" aria-checked={kind === k} onClick={() => setKind(k)} data-testid={`pollinate-kind-${k || 'all'}`}
      className={`px-2.5 h-7 rounded-md text-small whitespace-nowrap ${kind === k ? 'font-semibold text-[var(--color-text)] bg-[color-mix(in_srgb,var(--color-text)_8%,transparent)]' : muted}`}>{label}</button>
  );

  return (
    <div ref={setBox} className="flex-1 min-h-0 flex flex-col relative" data-testid="pollinate-overlay">
      {has && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-[var(--color-border)]">
          <Tabs value={tab} onChange={setTab} testid="pollinate-tabs" items={[
            { key: 'works', label: t('ui.PollinateOverlay.tabWorks'), testid: 'pollinate-tab-works' },
            { key: 'eval', label: t('ui.PollinateOverlay.tabEval'), testid: 'pollinate-tab-eval' },
          ]} />
          <div className="ml-auto flex flex-wrap items-center gap-2" data-testid="pollinate-filter">
            {kinds.length > 1 && (
              <div role="radiogroup" aria-label={t('ui.PollinateOverlay.kindLabel')} className="inline-flex gap-0.5 rounded-lg p-[3px] bg-[color-mix(in_srgb,var(--color-text)_5%,transparent)]">
                {segment('', t('ui.PollinateOverlay.kindAll'))}
                {kinds.map((k) => segment(k, t(`ui.PollinateOverlay.kind_${k}`)))}
              </div>
            )}
            {brains.length > 1 && (
              <select value={brain} onChange={(e) => setBrain(e.target.value)} aria-label={t('ui.PollinateOverlay.brainLabel')} data-testid="pollinate-brain"
                className="h-7 rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 text-small">
                <option value="">{t('ui.PollinateOverlay.brainAll')}</option>
                {brains.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            )}
          </div>
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
        {!!worksErr && (
          <OverlayError testid="pollinate-load-error" retryTestid="pollinate-load-retry"
            message={classifyLoadError(worksErr, 'works')} onRetry={reload} className="mx-auto my-8 max-w-sm" />
        )}
        {!worksErr && worksQ.data && !has && (
          <div className={`flex items-center gap-3 px-4 py-4 rounded-xl border border-dashed border-[var(--color-border)] text-body ${muted}`}
            data-testid="pollinate-empty"><span aria-hidden className={dim}>◇</span>{t('ui.PollinateOverlay.empty')}</div>
        )}
        {has && tab === 'works' && (
          <div data-testid="pollinate-works">
            {revising.length > 0 && (
              <section className="mb-8" data-testid="pollinate-revising">
                <SectionHead title={t('ui.PollinateOverlay.revisingTitle')} count={revising.length} testId="pollinate-revising-head" />
                <div className="flex flex-col gap-2.5">
                  {revising.map((w) => <WorkCard key={w.path} w={w} now={now} onOpen={() => setOpenPath(w.path)} onCanvas={canvas(w)} onSession={session(w)} />)}
                </div>
              </section>
            )}
            <section data-testid="pollinate-list">
              <SectionHead title={t('ui.PollinateOverlay.worksTitle')} count={rest.length} testId="pollinate-list-head" />
              {rest.length === 0
                ? <div className={`text-small ${dim}`}>{t('ui.PollinateOverlay.noneHere')}</div>
                : <div className="flex flex-col divide-y divide-[var(--color-border)]">
                  {rest.slice(0, shown).map((w) => <WorkRow key={w.path} w={w} onOpen={() => setOpenPath(w.path)} onCanvas={canvas(w)} />)}
                </div>}
              {rest.length > shown && (
                <button type="button" className="mt-2 text-small text-[var(--color-primary)] hover:underline" data-testid="pollinate-more"
                  onClick={() => setShown((n) => n + ROWS_PAGE)}>{t('ui.PollinateOverlay.more', { count: Math.min(ROWS_PAGE, rest.length - shown) })} ›</button>
              )}
            </section>
          </div>
        )}
        {has && tab === 'eval' && (evalQ.data
          ? <EvalView e={evalQ.data} compact={compact} onOpenPath={setOpenPath} />
          : evalQ.isError
            ? <OverlayError testid="pollinate-eval-error" retryTestid="pollinate-eval-retry"
              message={classifyLoadError(evalQ.error, 'eval')} onRetry={() => { void evalQ.refetch(); }} className="mx-auto my-8 max-w-sm" />
            : <div className={`text-small ${dim}`}>{t('ui.PollinateOverlay.loading')}</div>)}
      </div>
      {openPath && (
        <PollinateWorkCard path={openPath} order={order} onSelect={setOpenPath} onClose={() => setOpenPath(null)}
          onCanvas={(p) => openInCanvas(p, close)}
          onSession={(sid, w) => openSession(sid, w)}
          onContinueInChat={(w) => continueInChat(w)} />
      )}
    </div>
  );
}
