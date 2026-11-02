/**
 * PipelineRunCard — a local pipeline run's big card, over the Pipelines overlay (design
 * docs/design/2026-11-01-brain-eval-and-runs.md §4.2, option B). It is laid out like the
 * Swarm Out run card (swarm-out/RunOverlay) and reads one record, GET
 * /pipelines/{id}/view, so a local run and a Hive run read the same way:
 *
 * - top: what done looks like (the acceptance criteria of its REQUIREMENTS) and the
 *   machine's checks (the run audit's dimensions, ✓ PASS / △ otherwise: they mark, they
 *   never hold a command);
 * - left, tabs (the overlays' shared Tabs): what came back (the REPORT and the landed
 *   commits as the chat's turn-card rows: a click opens the file in the Canvas; the report
 *   rendered as the chat renders an answer), autonomy (each time a person stepped in, and
 *   the audit), process (each stage and its attempts), cost, lessons;
 * - right: the command panel. It renders the run's ``actions`` exactly as the backend
 *   gave them (routers/pipelines.run_actions) and decides nothing: answer (the question's
 *   options), resume, continue in its session, confirm landed (the person names the
 *   commits in a big box), cancel, open session.
 *
 * A run only moves when its session drives it: answering, resuming and continuing open
 * the owner session with the backend's continue prompt (``onContinue``). Keys: J / K the
 * next / previous run waiting for you (the order frozen when the card opened), Esc steps
 * back from a box, then out. It is a dialog layer (hooks/useLayer). A file opened by a
 * click inside closes the card and the overlay, so the Canvas is in view.
 *
 * Exports: PipelineRunCard
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import MarkdownRenderer from '../common/MarkdownRenderer';
import { pipelinesService, type PipelineCommand, type PipelineRunView } from '../../services/pipelines';
import api from '../../services/api';
import { SectionLabel, Tabs } from '../overlay';
import { FileItemRow, ItemBox, ItemRow } from '../../pages/chat/components/ItemRows';
import { useDialogLayer } from '../../hooks/useLayer';
import { typingIn } from '../swarm-out/runReview';
import { TONE_VAR } from '../swarm-out/tone';
import { tokenM } from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const btn = 'btn-md btn-secondary disabled:opacity-50';

type Tab = 'deliverable' | 'autonomy' | 'process' | 'cost' | 'lessons';

const when = (iso: string | null | undefined) => {
  if (!iso) return '—';
  const t = Date.parse(iso);
  return Number.isNaN(t) ? iso : new Date(t).toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

/** "repo=commit" per line, or one bare commit (a single-repo run). */
export function parseCommits(text: string): Record<string, string> | null {
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return null;
  const out: Record<string, string> = {};
  for (const line of lines) {
    const at = line.indexOf('=');
    const repo = at > 0 ? line.slice(0, at).trim() : '';
    const sha = (at > 0 ? line.slice(at + 1) : line).trim();
    if (!/^[0-9a-fA-F]{7,64}$/.test(sha) || repo in out) return null;
    out[repo] = sha;
  }
  return out;
}

export function PipelineRunCard({ id, order, onSelect, onClose, onContinue, onReload, onLeave, onSettled }: {
  id: string;
  /** the runs J / K walk (waiting for you), frozen when the card opened */
  order: string[];
  onSelect: (id: string) => void;
  onClose: () => void;
  /** open *sessionId* with *prompt* in its composer; false when it could not be opened */
  onContinue?: (sessionId: string, prompt: string) => Promise<boolean>;
  /** the run changed: read the list again */
  onReload: () => void;
  /** the person left for the Canvas or a chat tab: the overlay steps aside too */
  onLeave: () => void;
  /** a command settled the run (it no longer waits on the person): drop it from the order */
  onSettled?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<PipelineRunView | null>(null);
  const [failed, setFailed] = useState(false);
  const [gone, setGone] = useState(false);
  const [fetchKey, setFetchKey] = useState(0);
  const [tab, setTab] = useState<Tab>('deliverable');
  const [mode, setMode] = useState<'list' | 'confirm'>('list');
  // where to continue, when no chat tab could take the run: kept while the commands refresh
  const [note, setNote] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setFailed(false); setGone(false);
    pipelinesService.fetchRunView(id).then((v) => { if (!alive) return; setView(v); setGone(v === null); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [id, fetchKey]);
  useEffect(() => { setTab('deliverable'); setMode('list'); setView(null); setNote(null); }, [id]);

  const pos = order.indexOf(id);
  const step = useCallback((d: number) => {
    if (pos < 0 || order.length === 0) return;
    const next = order[(pos + d + order.length) % order.length];
    if (next) onSelect(next);
  }, [onSelect, order, pos]);

  useDialogLayer(true, dialogRef, () => { if (mode !== 'list') setMode('list'); else onClose(); });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (mode !== 'list' || typingIn(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'J') { e.preventDefault(); step(1); }
      if (e.key === 'k' || e.key === 'K') { e.preventDefault(); step(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mode, step]);

  // a file opened by a click inside: the card and the overlay step aside for the Canvas
  const clickInside = useRef(false);
  const markClick = () => { clickInside.current = true; queueMicrotask(() => { clickInside.current = false; }); };
  useEffect(() => {
    const onOpenFile = (e: Event) => {
      if (!clickInside.current || (e as CustomEvent).detail?.by === 'agent') return;
      onClose(); onLeave();
    };
    document.addEventListener('swarm:open-file', onOpenFile);
    return () => document.removeEventListener('swarm:open-file', onOpenFile);
  }, [onClose, onLeave]);

  const s = view?.summary, d = view?.detail;
  const title = d?.requirement || s?.requirement || id;
  const iv = view?.interventions;
  const total = s?.interventions?.total ?? null;
  const word = total == null ? null : total === 0 ? t('swarmOut.review.autonomy.full') : t('swarmOut.review.autonomy.steppedIn', { count: total });
  const tabs = [
    { key: 'deliverable', label: t('swarmOut.review.tab.deliverable'), count: (d?.commits.length ?? 0) + (d?.reportPath ? 1 : 0) },
    { key: 'autonomy', label: t('swarmOut.review.tab.autonomy') },
    { key: 'process', label: t('swarmOut.review.tab.process'), count: view?.stages.length ?? 0 },
    { key: 'cost', label: t('swarmOut.review.tab.cost') },
    { key: 'lessons', label: t('swarmOut.review.tab.lessons'), count: d?.reflectLessons.length ?? 0 },
  ].map((x) => ({ ...x, key: x.key as Tab, testid: `pipeline-card-tab-${x.key}` }));
  const checks = (s?.audit ?? []).map((a) => ({ key: a.dim, ok: a.verdict === 'PASS', text: `${a.dim} ${a.verdict}` }));
  const meta = [d?.project, d?.profile, id, d?.createdAt ? t('ui.PipelineOverlay.started', { when: when(d.createdAt) }) : null]
    .filter(Boolean).join(' · ');

  // pt-11: the macOS title bar overlays the top 28px of the window (TitleBarStyle::Overlay,
  // src-tauri/src/lib.rs); the card starts 16px below it, and its height is a share of the
  // scrim's own box (100%), not of the viewport height, so the whole card stays in view.
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-4 pb-4 pt-11" style={{ background: 'color-mix(in srgb, black 55%, transparent)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && mode === 'list') onClose(); }} data-testid="pipeline-card-overlay">
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} onClickCapture={markClick}
        className="flex flex-col w-[1240px] max-w-full h-[min(880px,100%)] rounded-[14px] border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl overflow-hidden text-body outline-none"
        data-testid="pipeline-card">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 border-b border-[var(--color-border)] min-w-0">
          <div className="min-w-0 flex-1 basis-[280px]">
            <div className="text-title font-semibold truncate" title={title}>{title}</div>
            <div className={`text-small truncate ${muted}`}>
              {meta}
              {word && <> · <button type="button" className="underline-offset-2 hover:underline" style={{ color: total === 0 ? TONE_VAR.ok : TONE_VAR.attention }}
                onClick={() => setTab('autonomy')} data-testid="pipeline-card-autonomy-word">{word}</button></>}
            </div>
          </div>
          <div className={`ml-auto flex flex-wrap items-center gap-1.5 text-small ${muted}`}>
            {pos >= 0 && <>
              <span className="whitespace-nowrap" data-testid="pipeline-card-position">{t('swarmOut.review.position', { n: pos + 1, total: order.length })}</span>
              <button type="button" className={btn} onClick={() => step(-1)} disabled={mode !== 'list'} aria-label={t('swarmOut.review.previous')}>K</button>
              <button type="button" className={btn} onClick={() => step(1)} disabled={mode !== 'list'} aria-label={t('swarmOut.review.next')}>J</button>
            </>}
            <button type="button" className={btn} onClick={() => setFetchKey((k) => k + 1)} data-testid="pipeline-card-refresh">↻ {t('swarmOut.review.refresh')}</button>
            <button type="button" className={btn} onClick={onClose} aria-label={t('swarmOut.review.close')} data-testid="pipeline-card-close">✕</button>
          </div>
        </div>
        {!view ? (
          <div className="flex-1 flex items-center justify-center" data-testid="pipeline-card-state">
            {failed ? <div className="flex items-center gap-2"><span className={muted}>{t('ui.PipelineOverlay.cardFailed')}</span>
              <button type="button" className={btn} onClick={() => setFetchKey((k) => k + 1)}>{t('swarmOut.review.retry')}</button></div>
              : gone ? <span className={muted}>{t('ui.PipelineOverlay.detailNotFound')}</span>
                : <span className={muted}>{t('hiveFleet.loading')}</span>}
          </div>
        ) : (
          <div className="flex-1 grid min-h-0 grid-cols-1 md:grid-cols-[minmax(0,1fr)_300px] overflow-auto md:overflow-hidden">
            <div className="flex flex-col min-h-0 md:border-r border-[var(--color-border)]">
              {(view.acceptance.length > 0 || checks.length > 0) && (
                <div className="px-4 py-3 border-b max-h-[30%] overflow-auto" style={{ background: `color-mix(in srgb, ${TONE_VAR.attention} 7%, transparent)`, borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 30%, var(--color-border))` }}
                  data-testid="pipeline-card-acceptance">
                  {view.acceptance.length > 0 && <>
                    <div className="text-meta font-semibold" style={{ color: TONE_VAR.attention }}>{t('swarmOut.review.acceptance')}</div>
                    <ul className="mt-0.5 flex flex-col gap-0.5">
                      {view.acceptance.map((a) => <li key={a.id || a.text} className="text-body break-words"><span className={`mr-1.5 tabular-nums ${muted}`}>{a.id}</span>{a.text}</li>)}
                    </ul>
                  </>}
                  {checks.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1 text-small">
                      {checks.map((c) => <span key={c.key} data-testid="pipeline-card-check" data-ok={c.ok}>
                        <span style={{ color: c.ok ? TONE_VAR.ok : TONE_VAR.attention }}>{c.ok ? '✓' : '△'}</span> {c.text}</span>)}
                    </div>
                  )}
                </div>
              )}
              <div className="px-5 py-2 border-b border-[var(--color-border)]"><Tabs items={tabs} value={tab} onChange={setTab} /></div>
              <div className="flex-1 md:overflow-auto px-5 py-5" role="tabpanel">
                {tab === 'deliverable' && <Deliverable view={view} />}
                {tab === 'autonomy' && (
                  <div className="flex flex-col gap-3 text-small" data-testid="pipeline-card-autonomy">
                    {iv == null ? <div className={muted}>{t('ui.PipelineOverlay.ivUnknown')}</div> : <>
                      <div>{t('ui.PipelineOverlay.ivSummary', { answers: iv.answers.length, corrections: iv.corrections == null ? '—' : iv.corrections.length, rescues: iv.rescues.length })}</div>
                      {iv.answers.map((a, i) => <div key={`a${i}`}><span className={muted}>{when(a.at)} · {t('ui.PipelineOverlay.ivAnswer')}</span> {a.question ?? ''} {a.optionId ? `→ ${a.optionId}` : ''}</div>)}
                      {(iv.corrections ?? []).map((c, i) => <div key={`c${i}`}><span className={muted}>{c.at.slice(5, 16).replace('T', ' ')} · {t('ui.PipelineOverlay.ivCorrection')}</span> {c.text}</div>)}
                      {iv.rescues.map((r, i) => <div key={`r${i}`}><span className={muted}>{when(r.at)} · {t('ui.PipelineOverlay.ivRescue')}</span> {t(`ui.PipelineOverlay.rescue_${r.kind}`, { defaultValue: r.kind })}</div>)}
                      {iv.corrections == null && <div className={muted}>{t('ui.PipelineOverlay.ivCorrectionsUnknown')}</div>}
                      <div className={muted}>{t('ui.PipelineOverlay.ivNot')}</div>
                    </>}
                    {d?.auditCard && <pre className="whitespace-pre-wrap rounded-md border border-[var(--color-border)] px-2.5 py-2 text-meta leading-relaxed font-mono text-[var(--color-text-muted)]">{d.auditCard}</pre>}
                  </div>
                )}
                {tab === 'process' && (
                  <div className="flex flex-col gap-1 text-small" data-testid="pipeline-card-process">
                    {view.stages.map((st) => (
                      <div key={st.name} className="flex items-baseline gap-3">
                        <span className="w-[90px] uppercase tracking-wide text-meta">{st.name}</span>
                        <span className={muted}>{st.status ?? '—'}</span>
                        {st.attempts != null && st.attempts > 1 && <span style={{ color: TONE_VAR.attention }}>{t('ui.PipelineOverlay.attempts', { count: st.attempts })}</span>}
                      </div>
                    ))}
                    {d?.checkpointReason && <div className={`mt-2 ${muted}`}>⏸ {d.checkpointReason}</div>}
                  </div>
                )}
                {tab === 'cost' && (
                  <div className="grid grid-cols-2 gap-3 max-w-[520px] text-small" data-testid="pipeline-card-cost">
                    <div><div className={muted}>{t('ui.PipelineOverlay.costTime')}</div><div className="text-title font-semibold tabular-nums">{s?.cycleTimeMin == null ? '—' : t('ui.PipelineOverlay.minutes', { n: Math.round(s.cycleTimeMin) })}</div></div>
                    <div><div className={muted}>{t('ui.PipelineOverlay.costTokens')}</div><div className="text-title font-semibold tabular-nums">{tokenM(s?.billedTokens ?? s?.tokensActual)}</div></div>
                    <div><div className={muted}>{t('ui.PipelineOverlay.costUsd')}</div><div className="text-title font-semibold tabular-nums">{s?.costUsd == null ? t('ui.PipelineOverlay.notRecorded') : `$${s.costUsd.toFixed(2)}`}</div></div>
                    <div><div className={muted}>{t('ui.PipelineOverlay.costRework')}</div><div className="text-title font-semibold tabular-nums">{s?.rework ?? '—'}</div></div>
                  </div>
                )}
                {tab === 'lessons' && (
                  d && d.reflectLessons.length ? <ul className="flex flex-col gap-1.5 text-small" data-testid="pipeline-card-lessons">{d.reflectLessons.map((l, i) => <li key={i}>• {l}</li>)}</ul>
                    : <div className={`text-small ${muted}`}>{t('swarmOut.review.noLessons')}</div>
                )}
              </div>
            </div>
            <div className="p-3.5 bg-[var(--color-bg-chrome)] md:overflow-auto border-t md:border-t-0 border-[var(--color-border)]">
              {/* keyed by the run and its commands: a step to another run, or the commands
                  the refetch brings, start it fresh (no late note, no stale button) */}
              <Commands key={`${view.detail.id}:${view.actions.map((a) => a.key).join()}`} view={view} mode={mode} setMode={setMode}
                onContinue={onContinue} onSettled={() => onSettled?.(view.detail.id)} note={note} setNote={setNote}
                onChanged={() => { onReload(); setFetchKey((k) => k + 1); }} onLeave={() => { onClose(); onLeave(); }} />
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function Deliverable({ view }: { view: PipelineRunView }) {
  const { t } = useTranslation();
  const d = view.detail;
  const landed = d.landedAs ? Object.entries(d.landedAs) : [];
  return (
    <div className="flex flex-col gap-4" data-testid="pipeline-card-deliverable">
      {(d.commits.length > 0 || landed.length > 0) && (
        <ItemBox title={t('ui.PipelineOverlay.landedCommits')} testId="pipeline-card-commits">
          {d.commits.map((c) => (
            <ItemRow key={`${c.repo}${c.sha}`} testId="pipeline-card-commit" badge={<span className={muted}>⎇</span>}
              name={c.sha.slice(0, 9)} folder={[c.repo, t('ui.PipelineOverlay.files', { count: c.files.length })].filter(Boolean).join(' · ')} />
          ))}
          {landed.map(([repo, sha]) => (
            <ItemRow key={`l${repo}${sha}`} testId="pipeline-card-landed-as" badge={<span className={muted}>⎇</span>}
              name={sha.slice(0, 9)} folder={[repo, t('ui.PipelineOverlay.landedAs')].filter(Boolean).join(' · ')} />
          ))}
        </ItemBox>
      )}
      {d.reportPath && (
        <ItemBox title={t('ui.PipelineOverlay.documents')} testId="pipeline-card-docs">
          <FileItemRow file={{ path: d.reportPath, absolutePath: d.reportPath }} testId="pipeline-card-report" />
        </ItemBox>
      )}
      {d.reportMd ? (
        <div data-testid="pipeline-card-report-md">
          <SectionLabel>{t('ui.PipelineOverlay.report')}</SectionLabel>
          <div className="mt-1"><MarkdownRenderer content={d.reportMd} /></div>
        </div>
      ) : !d.commits.length && !landed.length && <div className={`text-small ${muted}`}>{t('swarmOut.review.noResult')}</div>}
    </div>
  );
}

/** The commands the backend gave, in its order: the primary one solid, the rest under
 *  "Also". A decision's options are the answer; confirm landed opens a big box. */
function Commands({ view, mode, setMode, onContinue, onChanged, onLeave, onSettled, note, setNote }: {
  view: PipelineRunView; mode: 'list' | 'confirm'; setMode: (m: 'list' | 'confirm') => void;
  onContinue?: (sessionId: string, prompt: string) => Promise<boolean>;
  onChanged: () => void; onLeave: () => void; onSettled: () => void;
  note: string | null; setNote: (n: string | null) => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<string | null>(null);
  // a command went through: every button stays off until the refetched commands replace these
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [commits, setCommits] = useState('');
  const d = view.detail;
  const owner = d.ownerSessionId;

  /** hand the run to its session; when no tab could take it, say where */
  const handOver = async (prompt: string, sessionId: string | null) => {
    const sid = sessionId ?? owner;
    if (sid && onContinue && await onContinue(sid, prompt)) { onLeave(); return; }
    onChanged();
    setNote(t('ui.PipelineOverlay.openChatNote', { id: d.id }));
  };
  const run = async (key: string, f: () => Promise<void>) => {
    setBusy(key); setError(null); setNote(null);
    try { await f(); } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail || t('ui.PipelineOverlay.commandFailed'));
    } finally { setBusy(null); }
  };
  const act = (key: PipelineCommand) => {
    if (key === 'resume') return run(key, async () => {
      const { ownerSessionId } = await pipelinesService.resumeRun(d.id); setDone(true); onSettled(); await handOver(d.continuePrompt, ownerSessionId);
    });
    if (key === 'continue' || key === 'open_session') return run(key, () => handOver(key === 'continue' ? d.continuePrompt : '', null));
    if (key === 'cancel') return run(key, async () => { await api.patch(`/pipelines/${d.id}/cancel`); setDone(true); onSettled(); onChanged(); });
    if (key === 'confirm_landed') { setMode('confirm'); return undefined; }
    return undefined;
  };
  const answer = (optionId: string) => run(`answer:${optionId}`, async () => {
    const decision = d.decision!;
    const { ownerSessionId } = await pipelinesService.answerDecision(d.id, decision.requestId, optionId);
    setDone(true); onSettled();
    const choice = decision.options.find((o) => o.id === optionId)?.choice ?? optionId;
    await handOver(`I answered decision ${decision.requestId} on the dashboard: ${optionId} — ${choice}. ${d.continuePrompt}`, ownerSessionId);
  });
  const parsed = parseCommits(commits);
  const submitConfirm = async () => {
    if (!parsed) return;
    await run('confirm_landed', async () => {
      await pipelinesService.confirmLanded(d.id, parsed); setDone(true); setMode('list'); onSettled(); onChanged();
    });
  };

  if (mode === 'confirm') {
    return (
      <div className="flex flex-col gap-2" data-testid="pipeline-card-confirm">
        <div className="text-body font-semibold">{t('ui.PipelineOverlay.cmd_confirm_landed')}</div>
        <div className={`text-small ${muted}`}>{t('ui.PipelineOverlay.confirmHow')}</div>
        <textarea value={commits} onChange={(e) => setCommits(e.target.value)} rows={5} autoFocus
          placeholder={t('ui.PipelineOverlay.confirmPlaceholder')} data-testid="pipeline-card-confirm-input"
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && parsed) { e.preventDefault(); void submitConfirm(); } }}
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-input-bg)] px-2 py-1.5 font-mono text-small" />
        {commits.trim() && !parsed && <div className="text-small" style={{ color: TONE_VAR.error }}>{t('ui.PipelineOverlay.confirmBad')}</div>}
        {error && <div className="text-small break-words" role="alert" style={{ color: TONE_VAR.error }} data-testid="pipeline-card-error">{error}</div>}
        <div className="flex gap-2">
          <button type="button" className="btn-sm btn-primary" disabled={!parsed || busy !== null || done} onClick={() => { void submitConfirm(); }}
            data-testid="pipeline-card-confirm-submit">{t('ui.PipelineOverlay.confirmSubmit')}</button>
          <button type="button" className="btn-sm btn-secondary" onClick={() => setMode('list')}>{t('swarmOut.review.cancel')}</button>
        </div>
      </div>
    );
  }
  const actions = view.actions;
  if (!actions.length) return <div className={`text-small ${muted}`} data-testid="pipeline-card-nothing">{t('swarmOut.review.nothingToDo')}</div>;
  const primary = actions.find((a) => a.primary);
  const rest = actions.filter((a) => a !== primary);
  const button = (key: PipelineCommand, solid: boolean) => key === 'answer' ? null : (
    <button key={key} type="button" disabled={busy !== null || done} onClick={() => { void act(key); }} data-testid={`pipeline-card-cmd-${key}`}
      className={`w-full text-left rounded-md border px-2.5 py-2 disabled:opacity-50 ${solid ? 'border-[var(--color-primary)] bg-[color-mix(in_srgb,var(--color-primary)_14%,transparent)]' : 'border-[var(--color-border)] hover:bg-[var(--color-hover)]'}`}
      style={key === 'cancel' ? { color: TONE_VAR.error } : undefined}>
      <div className="flex items-center gap-2 text-body font-semibold">
        <span className="flex-1">{t(`ui.PipelineOverlay.cmd_${key}`)}{key === 'open_session' ? ' ↗' : ''}</span>
      </div>
      <div className={`text-small font-normal ${muted}`}>{t(`ui.PipelineOverlay.does_${key}`)}</div>
    </button>
  );
  return (
    <div className="flex flex-col gap-2" data-testid="pipeline-card-commands">
      <div className={`text-small ${muted}`}>{t('swarmOut.review.now')} <span className="text-[var(--color-text)]">{t(`ui.PipelineOverlay.now_${primary?.key ?? 'none'}`)}</span></div>
      {primary?.key === 'answer' && d.decision && (
        <div className="flex flex-col gap-1.5" data-testid="pipeline-card-decision">
          <div className="text-body">{d.decision.question}</div>
          {d.decision.whyEvidenceInsufficient && <div className={`text-small ${muted}`}>{d.decision.whyEvidenceInsufficient}</div>}
          {d.decision.options.map((o) => (
            <button key={o.id} type="button" disabled={busy !== null || done} onClick={() => { void answer(o.id); }} data-testid={`pipeline-card-option-${o.id}`}
              className="w-full text-left rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 py-1.5 hover:border-[var(--color-primary)] disabled:opacity-50">
              <div className="text-small">{o.id} — {o.choice}
                {o.reversible === false && <span className="ml-1.5 text-meta" style={{ color: TONE_VAR.error }}>{t('ui.PipelineOverlay.irreversible')}</span>}
                {o.risk === 'high' && <span className="ml-1.5 text-meta" style={{ color: TONE_VAR.error }}>{t('ui.PipelineOverlay.highRisk')}</span>}
              </div>
              {o.consequence && <div className={`text-meta ${muted}`}>{o.consequence}</div>}
            </button>
          ))}
        </div>
      )}
      {primary && primary.key !== 'answer' && button(primary.key, true)}
      {rest.length > 0 && <div className={`text-meta mt-1 ${muted}`}>{t('swarmOut.review.also')}</div>}
      {rest.map((a) => button(a.key, false))}
      {error && <div className="text-small break-words" role="alert" style={{ color: TONE_VAR.error }} data-testid="pipeline-card-error">{error}</div>}
      {note && <div className={`text-small ${muted}`} data-testid="pipeline-card-note">{note}</div>}
    </div>
  );
}
