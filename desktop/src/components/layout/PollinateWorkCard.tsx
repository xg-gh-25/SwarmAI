/**
 * PollinateWorkCard — one work's big card over the Pollinate overlay, laid out like the
 * Pipeline run card (PipelineRunCard: the shared run-card classes, a dialog layer, J / K)
 * and read from one record, GET /pollinate/works/view (core/pollinate_works.work_view):
 *
 * - head: title; Brain · kind · size · pages (a video: duration · style) · the folder ·
 *   current version; J / K walk the overlay's works, ↻, ✕;
 * - top band: the brief as written (its first lines) and the newest version's checks, one
 *   mark per check (✓ passed, △ failed on N pages; the guard first). They mark, never hold
 *   a command;
 * - left, tabs: Pages (each slide's image, the ones this revision changes outlined; a click
 *   opens the image in the Canvas), Versions (who, what scope and why, guard and failed
 *   checks), Checks (the newest version's findings; one the work already had is dimmed),
 *   Outputs (the files in out/). A video: Scenes (each scene's frame and start) and Outputs;
 * - right: the backend's actions for this state, the first one primary: Open in Canvas, Open
 *   session (the chat changing it), Continue in chat (a tab's composer prefilled).
 *
 * A failed check or a guard block is the agent's to fix: muted marks, never the attention
 * or error colour. Esc closes (hooks/useLayer). The card writes nothing.
 *
 * @exports PollinateWorkCard, sizeText
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { Tabs } from '../overlay';
import { useDialogLayer } from '../../hooks/useLayer';
import { typingIn } from '../swarm-out/runReview';
import { assetThumbUrl, pollinateService, type PollinateWork, type PollinateWorkView } from '../../services/pollinate';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const btn = 'btn-md btn-secondary disabled:opacity-50';
const BRIEF_LINES = 3;

type Tab = 'pages' | 'versions' | 'checks' | 'outputs' | 'scenes';

/** "1920×1080" / "1080×1440"; null when unknown. */
export function sizeText(w: PollinateWork): string | null {
  return w.width && w.height ? `${w.width}×${w.height}` : null;
}

function bytes(n: number | null): string {
  if (n == null) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function clock(s: number): string {
  const t = Math.floor(s);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

function when(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function PollinateWorkCard({ path, order, onSelect, onClose, onCanvas, onSession, onContinueInChat }: {
  path: string;
  /** the works J / K walk, in the overlay's order */
  order: string[];
  onSelect: (path: string) => void;
  onClose: () => void;
  onCanvas: (path: string) => void;
  onSession: (sessionId: string, w: PollinateWork) => void;
  onContinueInChat: (w: PollinateWork) => void;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<PollinateWorkView | null>(null);
  const [failed, setFailed] = useState(false);
  const [gone, setGone] = useState(false);
  const [fetchKey, setFetchKey] = useState(0);
  const [tab, setTab] = useState<Tab>('pages');
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setFailed(false); setGone(false);
    pollinateService.fetchWorkView(path).then((v) => { if (!alive) return; setView(v); setGone(v === null); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [path, fetchKey]);
  useEffect(() => { setView(null); }, [path]);
  useEffect(() => { if (view) setTab(view.work.kind === 'video' ? 'scenes' : view.work.kind === 'deck' ? 'pages' : 'versions'); }, [view?.work.path]); // eslint-disable-line react-hooks/exhaustive-deps

  const pos = order.indexOf(path);
  const step = useCallback((d: number) => {
    if (pos < 0 || order.length < 2) return;
    onSelect(order[(pos + d + order.length) % order.length]);
  }, [onSelect, order, pos]);
  useDialogLayer(true, dialogRef, onClose);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typingIn(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'J') { e.preventDefault(); step(1); }
      if (e.key === 'k' || e.key === 'K') { e.preventDefault(); step(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step]);

  const w = view?.work;
  const title = w?.title ?? path;
  const video = w?.kind === 'video';
  const meta = w ? [
    w.brain, t(`ui.PollinateOverlay.kind_${w.kind}`, { defaultValue: w.kind }), sizeText(w),
    w.kind === 'deck' && w.slideCount != null ? t('ui.PollinateOverlay.pages', { count: w.slideCount }) : null,
    video && w.durationS != null ? t('ui.PollinateOverlay.seconds', { count: Math.round(w.durationS) }) : null,
    video ? w.style : null, w.path,
    w.current ? t('ui.PollinateOverlay.currentIs', { id: w.current }) : null,
  ].filter(Boolean).join(' · ') : '';

  // the newest version's checks: the guard, then each check with its failed page count
  const newest = view?.versions[view.versions.length - 1];
  const marks: { key: string; ok: boolean; text: string }[] = [];
  if (view?.guard) marks.push({ key: 'guard', ok: view.guard.ok, text: t('ui.PollinateOverlay.guard') });
  if (newest) {
    const failedChecks = new Map<string, number>();
    for (const f of view!.findings) if (f.level === 'fail') failedChecks.set(f.check, (failedChecks.get(f.check) ?? 0) + 1);
    for (const [c, n] of failedChecks) marks.push({ key: c, ok: false, text: `${c} · ${t('ui.PollinateOverlay.pages', { count: n })}` });
    if (newest.status === 'ok' && !failedChecks.size && !view!.guard) marks.push({ key: 'ok', ok: true, text: t('ui.PollinateOverlay.checksPassed') });
  }
  const briefLines = (view?.brief ?? '').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));

  const tabs = (video
    ? [{ key: 'scenes', label: t('ui.PollinateOverlay.tabScenes'), count: view?.scenes.length ?? 0 },
      { key: 'outputs', label: t('ui.PollinateOverlay.tabOutputs'), count: view?.outputs.length ?? 0 }]
    : [
      ...(w?.kind === 'deck' ? [{ key: 'pages', label: t('ui.PollinateOverlay.tabPages'), count: view?.slides.length ?? 0 }] : []),
      { key: 'versions', label: t('ui.PollinateOverlay.tabVersions'), count: view?.versions.length ?? 0 },
      { key: 'checks', label: t('ui.PollinateOverlay.tabChecks'), count: view?.findings.length ?? 0 },
      { key: 'outputs', label: t('ui.PollinateOverlay.tabOutputs'), count: view?.outputs.length ?? 0 },
    ]).map((x) => ({ ...x, key: x.key as Tab, testid: `pollinate-wc-tab-${x.key}` }));

  // what is happening to the work: a revision open, a video still being made (its stage),
  // a document with no passed version yet, a finished video, or nothing
  const nowText = !w ? '' : w.open
    ? t('ui.PollinateOverlay.nowOpen', { count: w.open.scope.length, pages: w.open.scope.length })
    : video
      ? (w.inProgress
        ? (w.progress?.stage ? t('ui.PollinateOverlay.nowMaking', { stage: t(`ui.PollinateOverlay.stage_${w.progress.stage}`) }) : t('ui.PollinateOverlay.nowMakingNone'))
        : t('ui.PollinateOverlay.nowVideo'))
      : !w.current
        ? (w.versions.length ? t('ui.PollinateOverlay.notPassedYet') : t('ui.PollinateOverlay.nowNotCut'))
        : t('ui.PollinateOverlay.nowIdle');

  const openImage = (p: string | null) => { if (p && w?.inWorkspace) onCanvas(p); };

  return createPortal(
    <div className="run-card-scrim fixed inset-0 z-[60] flex items-center justify-center px-4 pb-4 pt-11"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }} data-testid="pollinate-wc-overlay">
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className="flex flex-col w-[1160px] max-w-full h-[min(860px,100%)] run-card overflow-hidden text-body outline-none" data-testid="pollinate-wc">
        <div className="run-card-head flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 border-b border-[var(--color-border)] min-w-0">
          <div className="min-w-0 flex-1 basis-[280px]">
            <div className="text-title font-semibold truncate" title={title}>{title}</div>
            <div className={`text-small truncate ${muted}`} title={meta}>{meta}</div>
          </div>
          <div className={`ml-auto flex items-center gap-1.5 text-small ${muted}`}>
            {pos >= 0 && order.length > 1 && <>
              <span className="whitespace-nowrap tabular-nums">{pos + 1} / {order.length}</span>
              <button type="button" className={btn} onClick={() => step(-1)} aria-label={t('ui.PollinateOverlay.previous')}>K</button>
              <button type="button" className={btn} onClick={() => step(1)} aria-label={t('ui.PollinateOverlay.next')}>J</button>
            </>}
            <button type="button" className={btn} onClick={() => setFetchKey((k) => k + 1)} data-testid="pollinate-wc-refresh">↻</button>
            <button type="button" className={btn} onClick={onClose} aria-label={t('ui.PollinateOverlay.close')} data-testid="pollinate-wc-close">✕</button>
          </div>
        </div>
        {!view || !w ? (
          <div className="flex-1 flex items-center justify-center" data-testid="pollinate-wc-state">
            {failed ? <div className="flex items-center gap-2"><span className={muted}>{t('ui.PollinateOverlay.cardFailed')}</span>
              <button type="button" className={btn} onClick={() => setFetchKey((k) => k + 1)}>{t('ui.PollinateOverlay.retry')}</button></div>
              : gone ? <span className={muted}>{t('ui.PollinateOverlay.notFound')}</span>
                : <span className={muted}>{t('ui.PollinateOverlay.loading')}</span>}
          </div>
        ) : (
          <div className="flex-1 grid min-h-0 grid-cols-1 md:grid-cols-[minmax(0,1fr)_280px] overflow-auto md:overflow-hidden">
            <div className="flex flex-col min-h-0 md:border-r border-[var(--color-border)]">
              {(briefLines.length > 0 || marks.length > 0) && (
                <div className="px-4 py-3 border-b border-[var(--color-border)]" data-testid="pollinate-wc-brief"
                  style={{ background: 'color-mix(in srgb, var(--color-status-running) 5%, transparent)' }}>
                  {briefLines.length > 0 && <>
                    <div className="text-meta font-semibold" style={{ color: 'var(--color-status-running)' }}>{t('ui.PollinateOverlay.brief')}</div>
                    <div className="text-body" title={view.brief ?? undefined}>
                      {briefLines.slice(0, BRIEF_LINES).map((l) => <div key={l} className="truncate">{l}</div>)}
                    </div>
                  </>}
                  {marks.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1 text-small text-[var(--color-text-secondary)]">
                      {marks.map((m) => (
                        <span key={m.key} data-testid="pollinate-wc-check" data-ok={m.ok}>
                          {/* a failed check is the agent's to fix: a muted mark, never the attention colour */}
                          <span className={m.ok ? '' : dim} style={m.ok ? { color: 'var(--color-status-success)' } : undefined}>{m.ok ? '✓' : '△'}</span> {m.text}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <div className="px-5 py-2 border-b border-[var(--color-border)]"><Tabs items={tabs} value={tab} onChange={setTab} /></div>
              <div className="flex-1 md:overflow-auto px-5 py-5" role="tabpanel">
                {tab === 'pages' && (
                  <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(150px,1fr))]" data-testid="pollinate-wc-pages">
                    {view.slides.map((s) => (
                      <button key={s.id} type="button" onClick={() => openImage(s.image)} disabled={!s.image || !w.inWorkspace}
                        className="text-left disabled:cursor-default" data-testid="pollinate-wc-slide" data-changed={s.changed}>
                        <span className="block aspect-video rounded-md overflow-hidden bg-[var(--color-bg-chrome)]"
                          style={{ boxShadow: s.changed ? '0 0 0 2px var(--color-status-running)' : 'inset 0 0 0 1px var(--color-border)' }}>
                          {s.image && w.inWorkspace && <img src={assetThumbUrl(s.image)} alt="" loading="lazy" className="w-full h-full object-cover" />}
                        </span>
                        <span className={`flex gap-1.5 mt-1 text-meta tabular-nums ${muted}`}><span>{s.n}</span><span className={`font-mono ${dim}`}>{s.id}</span></span>
                      </button>
                    ))}
                  </div>
                )}
                {tab === 'scenes' && (
                  <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(150px,1fr))]" data-testid="pollinate-wc-scenes">
                    {view.scenes.map((s) => (
                      <button key={s.id} type="button" onClick={() => openImage(s.image)} disabled={!s.image || !w.inWorkspace} className="text-left disabled:cursor-default">
                        <span className="block aspect-video rounded-md overflow-hidden bg-[var(--color-bg-chrome)]" style={{ boxShadow: 'inset 0 0 0 1px var(--color-border)' }}>
                          {s.image && w.inWorkspace && <img src={assetThumbUrl(s.image)} alt="" loading="lazy" className="w-full h-full object-cover" />}
                        </span>
                        <span className={`flex gap-1.5 mt-1 text-meta tabular-nums ${muted}`}><span>{s.id}</span><span className={`font-mono ${dim}`}>{clock(s.startS)}</span>
                          {s.title && <span className="truncate">{s.title}</span>}</span>
                      </button>
                    ))}
                  </div>
                )}
                {tab === 'versions' && (
                  <div className="flex flex-col" data-testid="pollinate-wc-versions">
                    {[...view.versions].reverse().map((v) => {
                      const fails = Object.entries(v.failCounts);
                      return (
                        <div key={v.id} className="grid grid-cols-[72px_minmax(0,1fr)_auto] gap-3 py-2.5 border-b border-[var(--color-border)] text-body">
                          <span className="font-mono text-small font-semibold">{v.id}</span>
                          <div className="min-w-0">
                            <span className="text-small" style={v.by === 'user' ? { color: 'var(--line-person)' } : undefined}>
                              {v.by === 'user' ? (v.foundOnDisk ? t('ui.PollinateOverlay.youEdited') : t('ui.PollinateOverlay.you')) : t('ui.PollinateOverlay.agent')}
                            </span>
                            <span className={`text-small ${muted}`}>
                              {v.scope.length > 0 && ` · ${t('ui.PollinateOverlay.changes', { count: v.scope.length })}`}
                              {v.note && ` · ${v.note}`}
                              {v.at && ` · ${when(v.at)}`}
                            </span>
                          </div>
                          <span className={`text-small whitespace-nowrap text-right ${muted}`} data-testid="pollinate-wc-version-status" data-status={v.status ?? ''}>
                            {v.status === 'ok' ? <span style={{ color: 'var(--color-status-success)' }}>✓ {t('ui.PollinateOverlay.passed')}</span>
                              : v.status === 'failed' ? <span className={dim}>{t('ui.PollinateOverlay.notPassed')}{v.guardOk === false ? ` · ${t('ui.PollinateOverlay.guard')}` : ''}{fails.length ? ` · ${fails.map(([c, n]) => `${c} ${n}`).join(', ')}` : ''}</span>
                                : '—'}
                            {v.id === w.current && ` · ${t('ui.PollinateOverlay.currentVersion')}`}
                          </span>
                        </div>
                      );
                    })}
                    {view.versions.length === 0 && <div className={`text-small ${muted}`}>{t('ui.PollinateOverlay.noVersions')}</div>}
                  </div>
                )}
                {tab === 'checks' && (
                  <div className="flex flex-col" data-testid="pollinate-wc-checks">
                    {view.guard && !view.guard.ok && view.guard.violations.map((x, i) => (
                      <div key={`g${i}`} className="grid grid-cols-[110px_minmax(0,1fr)] gap-3 py-2 border-b border-[var(--color-border)] text-body">
                        <span className="font-mono text-small">{t('ui.PollinateOverlay.guard')}</span><span className="text-[var(--color-text-secondary)]">{x.detail ?? x.kind}</span>
                      </div>
                    ))}
                    {view.findings.map((f, i) => (
                      <div key={`${f.check}${f.slide}${i}`} className={`grid grid-cols-[110px_minmax(0,1fr)] gap-3 py-2 border-b border-[var(--color-border)] text-body ${f.carried ? dim : ''}`}
                        data-testid="pollinate-wc-finding" data-carried={f.carried}>
                        <span className="font-mono text-small">{f.check}</span>
                        <span>{f.slide && <span className={`font-mono text-small ${dim}`}>{f.slide} · </span>}{f.msg ?? ''}{f.carried ? ` · ${t('ui.PollinateOverlay.carried')}` : ''}</span>
                      </div>
                    ))}
                    {view.findings.length === 0 && (!view.guard || view.guard.ok) && <div className={`text-small ${muted}`}>{t('ui.PollinateOverlay.noFindings')}</div>}
                  </div>
                )}
                {tab === 'outputs' && (
                  <div className="flex flex-col" data-testid="pollinate-wc-outputs">
                    {view.outputs.map((o) => (
                      <button key={o.name} type="button" disabled={!o.path || !w.inWorkspace || o.files != null}
                        onClick={() => o.path && onCanvas(o.path)}
                        className="flex items-center gap-3 px-2.5 py-2 -mx-2.5 rounded-md text-left hover:bg-[var(--color-hover)] disabled:hover:bg-transparent disabled:cursor-default">
                        <span className="flex-1 min-w-0 truncate font-mono text-small">out/{o.name}</span>
                        <span className={`text-small tabular-nums ${dim}`}>{o.files != null ? t('ui.PollinateOverlay.files', { count: o.files }) : bytes(o.bytes)}</span>
                      </button>
                    ))}
                    {view.outputs.length === 0 && <div className={`text-small ${muted}`}>{t('ui.PollinateOverlay.noOutputs')}</div>}
                  </div>
                )}
              </div>
            </div>
            <div className="p-3.5 bg-[var(--color-bg-chrome)] md:overflow-auto border-t md:border-t-0 border-[var(--color-border)] flex flex-col gap-2" data-testid="pollinate-wc-actions">
              <div className={`text-small ${muted}`}>{t('ui.PollinateOverlay.now')} <span className="text-[var(--color-text)]" data-testid="pollinate-wc-now">{nowText}</span></div>
              {view.actions.map((a, i) => (
                <div key={a.key} className="contents">
                  {i === 1 && <div className={`text-meta mt-1 ${muted}`}>{t('ui.PollinateOverlay.also')}</div>}
                  <button type="button" data-testid={`pollinate-wc-cmd-${a.key}`}
                    onClick={() => {
                      if (a.key === 'open_canvas' && a.path) onCanvas(a.path);
                      else if (a.key === 'open_session' && a.session) onSession(a.session, w);
                      else if (a.key === 'continue_in_chat') onContinueInChat(w);
                    }}
                    className={`w-full text-left rounded-md border px-2.5 py-2 ${a.primary ? 'border-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_14%,transparent)]' : 'border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-hover)]'}`}>
                    <div className="text-body font-semibold">{t(`ui.PollinateOverlay.cmd_${a.key}`)}{a.key === 'open_session' ? ' ↗' : ''}</div>
                    <div className={`text-small ${muted}`}>{t(`ui.PollinateOverlay.does_${a.key}`, { file: (a.path ?? '').split('/').pop() })}</div>
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
