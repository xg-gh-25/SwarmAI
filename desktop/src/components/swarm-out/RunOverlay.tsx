/**
 * RunOverlay — one run's big card, over the Swarm Out tab (design
 * docs/design/2026-10-31-swarm-out-run-review.md §2.2).
 *
 * Left, to look: the acceptance strip on top (what done looks like, and the machine's
 * checks on what came back, each ✓ or △: they mark, they never hold a command), then
 * tabs (the overlays' shared Tabs): what came back, autonomy, process, cost, lessons.
 * What came back is drawn with the chat's own parts: the deliverables and a repository's
 * files as the turn cards' rows (pages/chat/components/ItemRows: a file that came back
 * opens in the Canvas, one left on the Hive says so), the final reply as the chat renders
 * an answer, as Hive content (pages/chat/remoteContentContext: its paths are the Hive's,
 * never links here). Right, to act: the command panel.
 *
 * The command panel renders the run's ``actions`` exactly as the backend gave them
 * (core/swarm_out_settle.derive): the primary one solid, the others under "Also", discard
 * last and apart. Each command is its name, one line of what it does and its key. A
 * command that needs words (re-run with a correction, discard) turns the panel into a
 * big box in place, never a dialog: the deliverable stays in view. Landing commits shows
 * the review of each repository in the panel (protected paths ticked separately).
 *
 * The card is a dialog layer (hooks/useLayer): focus moves in and back, Tab stays
 * inside, Esc goes to the topmost layer. Keys: J / K the next / previous run waiting for
 * you (the order frozen when the card opened, a run dropped from it once settled), the
 * commands' own keys, Esc to step back (from a box, a landing or a confirmation to the
 * commands; from the commands, out). While a box is open nothing else (J / K, a click
 * outside) can throw away what was typed. A single key never fires while the cursor is in
 * a field. After accepting or discarding, the card moves to the next run waiting for you,
 * or closes (from the history: closes). It closes when you leave it: opening the run's
 * session, a file opened by a click inside it (a swarm:open-file an agent sends never
 * closes it), the Swarm Out panel going off screen, or its run disappearing. History rows
 * and a Brain's run cards open this same card.
 *
 * A returned repository's changed file opens its diff: the file is not on this machine,
 * only in the commits taken back (refs/hive/…), so a click takes them back for review if
 * they are not yet (return/prepare: nothing lands), reads both versions from git's
 * objects (return/file) and opens them in the Canvas as a read-only versions document
 * (swarm:open-file-versions, which only a click here sends). A binary or too large file
 * says so on its row instead.
 */
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import MarkdownRenderer from '../common/MarkdownRenderer';
import { swarmOutService, type SwarmOutAction, type SwarmOutAsk, type SwarmOutFileVersions, type SwarmOutHostAnswer, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { OPEN_FILE_VERSIONS_EVENT, openFileVersions } from '../file-viewer/utils/fileVersions';
import { Stats } from './LiveDispatches';
import { SwarmOutWaitCard } from './SwarmOutWaitCard';
import { SectionLabel, Tabs } from '../overlay';
import { FileTypeIcon } from '../common/FileTypeIcon';
import { FileItemRow, ItemBox, ItemRow, shortFolder } from '../../pages/chat/components/ItemRows';
import { RemoteContentContext } from '../../pages/chat/remoteContentContext';
import { ReturnPanel } from './ReturnPanel';
import { agentFacts, checksLine, executionLine, gateLine, lessonsLine, resultErrorLabel, runJudge, runStats, stateLabel, tokenStats, workLines } from './live';
import { useDialogLayer } from '../../hooks/useLayer';
import { getOverlayCtxBridge } from '../layout/overlayRegistry';
import { PanelHiddenContext, useOpenRun } from './openSession';
import { actionsOf, ago, autonomyWord, commandForKey, commandMeta, machineChecks, stageOf, typingIn, waitingSince, waitsOf } from './runReview';
import { DIMENSIONS, DIMENSION_ROLE } from './shapes';
import { TONE_VAR } from './tone';
import { modKeyLabel } from '../../utils/platform';

const muted = 'text-[var(--color-text-muted)]';
const btn = 'btn-md btn-secondary disabled:opacity-50';

type Tab = 'deliverable' | 'autonomy' | 'process' | 'cost' | 'lessons';
/** what the command panel shows: the commands, or one of them taking words or a confirmation */
export type PanelMode = 'list' | 'rerun' | 'discard' | 'land' | 'manual';

/** HTTP status of a failed api call (the api interceptor's ApiError, or axios's shape). */
function statusOf(e: unknown): number | undefined {
  return (e as { statusCode?: number })?.statusCode ?? (e as { response?: { status?: number } })?.response?.status;
}

function detailOf(e: unknown, fallback: string): string {
  const d = (e as { response?: { data?: { detail?: unknown } }; detail?: unknown });
  const v = d?.response?.data?.detail ?? d?.detail;
  if (typeof v === 'string' && v.length < 300) return v;
  const m = (e as { message?: string })?.message;
  return m && m.length < 300 ? m : fallback;
}

/** What the agent that dispatched the run answered for the person, oldest first: what
 *  was asked, what it picked and why (design 2026-10-31-hive-approvals H8). Everything
 *  but the reason was written by the Hive, and is shown as plain text. */
function HostAnswers({ answers, now }: { answers: SwarmOutHostAnswer[]; now: number }) {
  const { t } = useTranslation();
  if (answers.length === 0) return null;
  return (
    <div className="mt-6 flex flex-col gap-3" data-testid="swarm-out-review-host-answers">
      <SectionLabel>{t('swarmOut.review.hostAnswers.title', { count: answers.length })}</SectionLabel>
      {answers.map((a, i) => (
        <div key={`${a.request_id ?? ''}-${i}`} className="flex flex-col gap-0.5 text-[12.5px]" data-testid="swarm-out-review-host-answer">
          <div className="whitespace-pre-wrap line-clamp-3 text-[var(--color-text-secondary)]">{a.asked || t('swarmOut.review.hostAnswers.noText')}</div>
          <div>
            <span className="font-semibold">→ {a.pick || '—'}</span>
            {a.why && <span className={muted}> · {t('swarmOut.review.hostAnswers.why', { why: a.why })}</span>}
          </div>
          <div className={`text-[11.5px] ${muted}`}>
            {t(`swarmOut.review.hostAnswers.class.${a.class ?? 'unknown'}`, { defaultValue: a.class ?? '' })}
            {a.at ? ` · ${ago(a.at, now)}` : ''}
          </div>
        </div>
      ))}
    </div>
  );
}

/** The autonomy tab: a pipeline run's five Judge rows, else who stepped in and how; then
 *  what the dispatching agent answered for the person. */
function AutonomyTab({ res, answers, now }: { res: SwarmOutResult | null; answers: SwarmOutHostAnswer[]; now: number }) {
  return (
    <>
      <AutonomyBody res={res} hostListed={answers.length > 0} />
      <HostAnswers answers={answers} now={now} />
    </>
  );
}

function AutonomyBody({ res, hostListed }: { res: SwarmOutResult | null; hostListed: boolean }) {
  const { t } = useTranslation();
  if (!res) return <div className={muted}>{t('swarmOut.review.noResult')}</div>;
  const judge = runJudge(res);
  const word = autonomyWord(res);
  if (judge) {
    return (
      <div data-testid="swarm-out-review-autonomy" data-kind="judge">
        <div className="text-[13.5px]">{t('swarmOut.review.autonomy.judgeHead', { state: word?.state ?? '' })}</div>
        <table className="mt-3 w-full text-[12.5px] border-collapse">
          <thead><tr className={muted}>
            <th className="text-left font-semibold py-1.5 pr-2 border-b border-[var(--color-border)]">{t('swarmOut.review.autonomy.dim')}</th>
            <th className="text-left font-semibold py-1.5 pr-2 border-b border-[var(--color-border)]">{t('swarmOut.review.autonomy.role')}</th>
            <th className="text-left font-semibold py-1.5 pr-2 border-b border-[var(--color-border)]">{t('swarmOut.review.autonomy.verdict')}</th>
            <th className="text-left font-semibold py-1.5 border-b border-[var(--color-border)]">{t('swarmOut.review.autonomy.number')}</th>
          </tr></thead>
          <tbody>
            {DIMENSIONS.map((d, i) => (
              <tr key={d} data-testid="swarm-out-review-dim">
                <td className="py-1.5 pr-2 border-b border-[var(--color-border)]">{d}</td>
                <td className={`py-1.5 pr-2 border-b border-[var(--color-border)] ${muted}`}>{t(`swarmOut.judge.role.${DIMENSION_ROLE[i]}`)}</td>
                <td className="py-1.5 pr-2 border-b border-[var(--color-border)] font-semibold"
                  style={{ color: judge.verdicts[i] === 'PASS' ? TONE_VAR.ok : judge.verdicts[i] === 'BLOCK' ? TONE_VAR.error : judge.verdicts[i] === 'WARN' ? TONE_VAR.attention : undefined }}>
                  {judge.verdicts[i] === 'NA' ? t('ui.RunOverlay.notApplicable') : judge.verdicts[i]}</td>
                <td className={`py-1.5 border-b border-[var(--color-border)] ${muted}`}>{judge.numbers[i]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  const h = res.work.human;
  const cells: [string, number][] = [
    ['approved', h.approved], ['denied', h.denied], ['answered', h.answered_questions],
    ['said', h.said ?? 0], ['unanswered', h.unanswered],
    // what the dispatching agent answered itself: never counted as the person's; a
    // count only when its answers are not listed below (an older record)
    ...(!hostListed && (res.work.host_handled ?? 0) > 0 ? [['host', res.work.host_handled ?? 0] as [string, number]] : []),
  ];
  return (
    <div data-testid="swarm-out-review-autonomy" data-kind="human">
      <div className="text-[13.5px]">{word?.text}</div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {cells.map(([k, n]) => (
          <div key={k} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2" data-testid={`swarm-out-review-human-${k}`}>
            <div className="text-[18px] font-semibold tabular-nums">{n}</div>
            <div className={`text-[11.5px] ${muted}`}>{t(`swarmOut.review.human.${k}`)}</div>
          </div>
        ))}
      </div>
      <div className={`mt-2 text-[12px] ${muted}`}>{t('swarmOut.review.human.steps', { failed: res.work.steps.failed, total: res.work.steps.total })}</div>
    </div>
  );
}

/** The repositories whose commits come back for landing here, by the backend's own
 *  rule (core/swarm_out_settle._code: a sealed bundle with a directory). */
function landable(full: SwarmOutResult | null) {
  return (full?.work.repos ?? []).filter((x) => x.dir && x.bundle && !x.bundle.error && (x.bundle.sha256 || x.bundle.available));
}

const baseName = (p: string) => p.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || p;
const dirOf = (p: string) => p.replace(/\/[^/]*$/, '') || '/';

/** What came back: the error, the deliverables (the files here
 *  open in the Canvas; the ones left on the Hive say so), the commits per repository,
 *  and the final reply. Everything but the question is read from the whole record
 *  (``full``): /runs counts files and steps, without their paths and text. */
function DeliverableTab({ r, full, fullRead, fullFailed, onRetry, waits }: {
  r: SwarmOutRun; full: SwarmOutResult | null;
  /** the record was read (``full`` null then: it holds no result yet, or none any more) */
  fullRead: boolean;
  fullFailed: boolean; onRetry: () => void;
  /** how many of the run's waits the card shows above the tabs */
  waits: number;
}) {
  const { t } = useTranslation();
  const stage = stageOf(r);
  const showSwarmOut = getOverlayCtxBridge().showSwarmOut;
  const files = full?.work.files ?? [];
  const repos = (full?.work.repos ?? []).filter((x) => x.files.length || x.commits.length);
  const report = full?.report ?? '';
  const id = r.dispatch_id;
  // a line on a changed file's row: opening, binary, too large, or why it failed
  const [fileNote, setFileNote] = useState<Record<string, string>>({});
  const opening = useRef(new Set<string>());
  useEffect(() => { setFileNote({}); }, [id]);
  const note = (k: string, text: string | null) => setFileNote((n) => {
    const next = { ...n };
    if (text) next[k] = text; else delete next[k];
    return next;
  });
  const openChange = async (url: string, path: string, head: string | null) => {
    const k = `${url}\n${path}`;
    if (opening.current.has(k)) return;
    opening.current.add(k);
    note(k, t('swarmOut.review.repoFile.opening'));
    try {
      let v: SwarmOutFileVersions | null = null;
      try {
        v = await swarmOutService.returnFile(id, url, path);
      } catch (e) {
        if (statusOf(e) !== 409) throw e;          // 409: not taken back for review yet
      }
      // not taken back yet, or taken back before a later turn's commits: take them
      // back for review (nothing lands), then read the file
      if (!v || (head && v.tip_sha !== head)) {
        await swarmOutService.returnPrepare(id, url);
        v = await swarmOutService.returnFile(id, url, path);
      }
      if (v.binary || v.too_large) { note(k, t(v.binary ? 'swarmOut.review.repoFile.binary' : 'swarmOut.review.repoFile.tooLarge')); return; }
      note(k, null);
      openFileVersions({ source: `hive:${id}@${v.tip_sha.slice(0, 12)}`, path: v.path, base: v.base, tip: v.tip });
    } catch (e) {
      note(k, t('swarmOut.review.repoFile.failed', { error: detailOf(e, t('swarmOut.detail.failed')) }));
    } finally {
      opening.current.delete(k);
    }
  };
  return (
    <div className="flex flex-col gap-5 max-w-[880px]" data-testid="swarm-out-review-deliverable">
      {stage === 'needs_answer' && waits === 0 && (
        <div className="text-[13px] flex items-center gap-2" data-testid="swarm-out-review-answer-elsewhere">
          <span className={muted}>{t('swarmOut.review.answerElsewhere')}</span>
          {showSwarmOut && <button type="button" className={btn} onClick={() => showSwarmOut()}>{t('swarmOut.review.openSwarmOut')}</button>}
        </div>
      )}
      {stage === 'running' && (
        <div className="text-[13px]" data-testid="swarm-out-review-now">
          <span style={{ color: TONE_VAR.busy }}>● </span>{stateLabel(r)}
          {(full ?? r.result) && <span className={muted}> · {t('swarmOut.review.stepsSoFar', { count: (full ?? r.result)!.work.steps.total })}</span>}
        </div>
      )}
      {!full && !fullRead && !fullFailed && stage !== 'needs_answer' && <div className={muted}>{t('swarmOut.loading')}</div>}
      {!full && fullRead && stage !== 'running' && (
        <div className={`text-[13px] ${muted}`} data-testid="swarm-out-review-no-result">
          {r.result_status === 'failed' ? t('swarmOut.result.failed', { reason: resultErrorLabel(r.result_error) }) : t('swarmOut.result.pending')}
        </div>
      )}
      {!full && fullFailed && (
        <div className="text-[13px] flex items-center gap-2" data-testid="swarm-out-review-full-failed">
          <span className={muted}>{t('swarmOut.detail.failed')}</span>
          <button type="button" className={btn} onClick={onRetry}>{t('swarmOut.review.retry')}</button>
        </div>
      )}
      {full?.error?.message && (
        <div role="alert" className="rounded-lg px-3 py-2 text-[13px] whitespace-pre-wrap break-words" data-testid="swarm-out-review-error-message"
          style={{ color: TONE_VAR.error, background: `color-mix(in srgb, ${TONE_VAR.error} 9%, transparent)` }}>{full.error.message}</div>
      )}
      {files.length > 0 && (
        <ItemBox testId="swarm-out-review-files" title={t('swarmOut.review.deliverables', { count: files.length })}>
          {files.map((f, i) => (f.local ? (
            <FileItemRow key={`${i}:${f.local}`} testId="swarm-out-review-file" file={{ path: baseName(f.local), absolutePath: f.local }} />
          ) : (
            <ItemRow key={`${i}:${f.path ?? ''}`} testId="swarm-out-review-file-hive" data={{ path: f.path ?? '' }}
              badge={<FileTypeIcon name={baseName(f.path ?? '')} size="sm" />} name={baseName(f.path ?? '')}
              folder={shortFolder(dirOf(f.path ?? ''))} folderTitle={f.path}
              tail={<span className={`ml-auto pl-3 text-[11.5px] whitespace-nowrap ${muted}`} data-testid={f.not_placed ? 'swarm-out-review-file-not-placed' : undefined}>
                {f.not_placed ? t('swarmOut.review.notPlaced') : t('swarmOut.review.onHive')}</span>} />
          )))}
        </ItemBox>
      )}
      {repos.map((x) => (
        <ItemBox key={x.dir ?? x.url ?? ''} testId="swarm-out-review-repo"
          title={`${x.dir ?? x.url ?? ''}${x.ref ? ` @ ${x.ref}` : ''}`}
          tail={<span className="tabular-nums">{t('swarmOut.work.commits', { count: x.commits_total })} · +{x.added} −{x.deleted}</span>}
          footer={(x.files_total > x.files.length || x.bundle?.error || x.commits.length > 0) && (
            <div className={`px-3 py-1.5 border-t border-[var(--color-border)] text-[11.5px] ${muted}`}>
              {x.commits.length > 0 && <div className="font-mono">{x.commits.map((c) => c.slice(0, 9)).join(' ')}{x.commits_total > x.commits.length ? t('swarmOut.detail.commitsTotal', { count: x.commits_total }) : ''}</div>}
              {x.files_total > x.files.length && <div>{t('swarmOut.detail.moreFiles', { count: x.files_total - x.files.length })}</div>}
              {x.bundle?.error && <div>{t('swarmOut.detail.bundleError', { error: x.bundle.error })}</div>}
            </div>
          )}>
          {x.files.map((f, i) => {
            const k = `${x.url ?? ''}\n${f.path}`;
            return (
              <ItemRow key={`${i}:${f.path}`} testId="swarm-out-review-repo-file" badge={<FileTypeIcon name={baseName(f.path)} size="sm" />}
                name={baseName(f.path)} folder={f.path.includes('/') ? dirOf(f.path) : undefined} folderTitle={f.path}
                link={!!x.url} rowOpens openTitle={x.url ? t('swarmOut.review.repoFile.open') : undefined}
                onOpen={x.url ? () => { void openChange(x.url!, f.path, x.head); } : undefined}
                tail={<span className={`ml-auto pl-3 text-[11.5px] tabular-nums whitespace-nowrap ${muted}`}>+{f.added ?? '?'} −{f.deleted ?? '?'}</span>}
                below={fileNote[k] ? <div className={`px-3 pb-1.5 pl-9 text-[11.5px] ${muted}`} data-testid="swarm-out-review-repo-file-note">{fileNote[k]}</div> : undefined} />
            );
          })}
        </ItemBox>
      ))}
      {report ? (
        <section data-testid="swarm-out-review-report">
          <SectionLabel>{t('swarmOut.review.reply')}</SectionLabel>
          <RemoteContentContext.Provider value>
            <div className="mt-2 text-[14px] leading-[1.7]"><MarkdownRenderer content={report} /></div>
          </RemoteContentContext.Provider>
        </section>
      ) : full && !files.length && !repos.length && !waits && !full.error?.message && <div className={muted}>{t('swarmOut.detail.noReply')}</div>}
    </div>
  );
}

/** A key belongs to the card when it is pressed inside it, or with focus on the page
 *  itself (nothing focused). No scope: every key (the panel on its own). */
function inScope(scope: React.RefObject<HTMLElement | null> | undefined, target: EventTarget | null): boolean {
  if (!scope) return true;
  if (!(target instanceof Node) || target === document.body) return true;
  return !!scope.current?.contains(target);
}

const STAGE_TONE: Record<string, string> = {
  running: TONE_VAR.busy, needs_answer: TONE_VAR.attention, awaiting: TONE_VAR.attention, stopped: TONE_VAR.error,
  accepted: TONE_VAR.ok, discarded: 'var(--color-text-muted)', expired: 'var(--color-text-muted)',
};

/** The right column: what can be done now, as the backend said. The mode (the commands,
 *  or one of them taking words or a confirmation) is the card's, so Esc and J / K can
 *  respect it. */
export function CommandPanel({ r, full, fullRead = false, fullFailed = false, onRetry, asks = [], hasWaits = true, mode, setMode, onDone, onShowDeliverable, onLeave, onReload, now, scope }: {
  r: SwarmOutRun; full: SwarmOutResult | null;
  /** the record was read and holds no result (nothing to land) */
  fullRead?: boolean;
  /** the whole record could not be read (landing needs it) */
  fullFailed?: boolean; onRetry?: () => void;
  asks?: SwarmOutAsk[];
  /** the card shows a wait to answer (else the answer is given on the Hive's page) */
  hasWaits?: boolean;
  mode: PanelMode; setMode: (m: PanelMode) => void;
  /** a command went through; ``closed``: the run is settled now (move on) */
  onDone: (closed: boolean) => void;
  onShowDeliverable: () => void;
  /** close the card before the run's session opens beneath it */
  onLeave?: () => void;
  onReload?: () => void;
  now?: number;
  /** the card: its single-key commands answer only a key pressed inside it (or with
   *  focus nowhere), never one meant for a layer opened above it */
  scope?: React.RefObject<HTMLElement | null>;
}) {
  const { t } = useTranslation();
  const openRun = useOpenRun(r);
  const actions = actionsOf(r);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [landed, setLanded] = useState<string[]>([]);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const id = r.dispatch_id;
  const stage = stageOf(r);
  useEffect(() => { setText(''); setError(null); setLanded([]); }, [id]);
  useEffect(() => { if (mode === 'rerun' || mode === 'discard') boxRef.current?.focus(); }, [mode]);
  // landing ends when the backend says the run is no longer waiting for acceptance
  useEffect(() => {
    if (mode === 'land' && stage !== 'awaiting') { setMode('list'); onDone(true); }
  }, [mode, stage, setMode, onDone]);

  const run = useCallback(async (fn: () => Promise<unknown>, closed: boolean) => {
    setBusy(true); setError(null);
    try {
      await fn();
      setMode('list'); setText('');
      onDone(closed);
    } catch (e) {
      setError(t('swarmOut.review.failed', { error: detailOf(e, t('swarmOut.live.submitFailed')) }));
    } finally {
      setBusy(false);
    }
  }, [onDone, setMode, t]);

  const openSession = useCallback(() => { if (!openRun) return; onLeave?.(); openRun(); }, [onLeave, openRun]);
  // ``busy`` holds every command while one is in flight: a second ⌘↩ or click is a
  // separate event, rendered after the first set it
  const give = useCallback((a: SwarmOutAction) => {
    if (busy) return;
    switch (a.key) {
      case 'accept':
        if (a.variant === 'land') { setMode('land'); return; }
        if (a.variant === 'manual') { setMode('manual'); return; }
        if (r.result?.ended_at) void run(() => swarmOutService.accept(id, r.result!.ended_at!), true);
        return;
      case 'rerun': setMode('rerun'); return;
      case 'discard': setMode('discard'); return;
      case 'stop': void run(() => swarmOutService.stop(id), false); return;
      case 'open_session': openSession(); return;
      case 'answer': onShowDeliverable(); return;
      default:
    }
  }, [busy, id, openSession, onShowDeliverable, r.result, run, setMode]);

  // the panel's own keys (never while the cursor is in a field)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (mode !== 'list' || typingIn(e.target) || !inScope(scope, e.target)) return;
      const a = commandForKey(actions, e);
      if (a) { e.preventDefault(); give(a); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [actions, give, mode, scope]);

  const submitBox = () => {
    if (busy) return;
    if (mode === 'rerun' && text.trim()) void run(() => swarmOutService.rerun(id, text), false);
    if (mode === 'discard') void run(() => swarmOutService.discard(id, text), true);
  };
  const reasonLabel = r.state_reason === 'answer_sent' || r.state_reason === 'answer_refused'
    || r.state_reason === 'background'
    ? t(`swarmOut.review.stateReason.${r.state_reason}`) : null;

  const header = (
    <div className="text-[12.5px]" data-testid="swarm-out-review-stage" data-stage={stage}>
      <span className={muted}>{t('swarmOut.review.now')} </span>
      <b style={{ color: r.state_reason === 'answer_sent' ? TONE_VAR.busy : STAGE_TONE[stage] }}>{reasonLabel ?? t(`swarmOut.review.stage.${stage}`)}</b>
      {r.why && <span className={muted}> · {t(`swarmOut.review.why.${r.why}`, { defaultValue: r.why })}</span>}
      <span className={muted}> · {ago(waitingSince(r, asks), now ?? Date.now())}</span>
      {r.cleanup_soon && <div className="mt-1 text-[12px]" style={{ color: TONE_VAR.attention }} data-testid="swarm-out-review-cleanup">{t('swarmOut.review.cleanupSoonLong')}</div>}
    </div>
  );
  const sheet = (props: Omit<React.ComponentProps<typeof ConfirmSheet>, 'onCancel' | 'busy' | 'error'>) => (
    <div className="flex flex-col gap-4">
      {header}
      <ConfirmSheet {...props} busy={busy} error={error} onCancel={() => setMode('list')} />
    </div>
  );

  if (mode === 'rerun' || mode === 'discard') {
    const rerun = mode === 'rerun';
    return sheet({
      testId: `swarm-out-review-${mode}`, danger: !rerun,
      title: t(rerun ? 'swarmOut.review.rerunBox.title' : 'swarmOut.review.discardBox.title'),
      consequence: rerun ? undefined : t(r.settle?.code === 'unsealed' ? 'swarmOut.review.discardBox.consequenceUnsealed' : 'swarmOut.review.discardBox.consequence'),
      confirmLabel: t(rerun ? 'swarmOut.review.rerunBox.submit' : 'swarmOut.review.discardBox.submit'),
      confirmDisabled: rerun && !text.trim(), onConfirm: submitBox,
      children: (
        <>
          <label className={`text-[12px] ${muted}`} htmlFor={`swarm-out-box-${id}`}>{t(rerun ? 'swarmOut.review.rerunBox.label' : 'swarmOut.review.discardBox.label')}</label>
          <textarea id={`swarm-out-box-${id}`} ref={boxRef} rows={rerun ? 7 : 5} maxLength={rerun ? 20000 : 2000}
            className="w-full min-h-[120px] rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 text-[13px] leading-[1.5] resize-y"
            value={text} onChange={(e) => setText(e.target.value)}
            placeholder={t(rerun ? 'swarmOut.review.rerunBox.placeholder' : 'swarmOut.review.discardBox.placeholder')}
            data-testid="swarm-out-review-box" />
          {rerun && <div className={`text-[11.5px] ${muted}`}>{t('swarmOut.review.rerunBox.note')}</div>}
        </>
      ),
    });
  }

  if (mode === 'manual') {
    return sheet({
      testId: 'swarm-out-review-manual', title: t('swarmOut.review.cmd.accept.manual.name'),
      consequence: t('swarmOut.review.manualWhy'), confirmLabel: t('swarmOut.review.manualConfirm'), confirmTone: 'ok',
      onConfirm: () => { void run(() => swarmOutService.manualLanded(id), true); },
    });
  }

  if (mode === 'land') {
    const repos = landable(full);
    const all = repos.length > 0 && repos.every((x) => landed.includes(x.dir!));
    return sheet({
      testId: 'swarm-out-review-land', title: t('swarmOut.review.cmd.accept.land.name'),
      confirmLabel: t('swarmOut.review.landDone'), confirmDisabled: !all, confirmTestId: 'swarm-out-review-land-done',
      onConfirm: () => { setMode('list'); onDone(true); },
      children: (
        <>
          {!full && !fullRead && !fullFailed && <div className={muted}>{t('swarmOut.loading')}</div>}
          {!full && fullRead && <div className={`text-[12px] ${muted}`}>{t('swarmOut.review.landNothing')}</div>}
          {!full && fullFailed && (
            <div className="text-[12px]" data-testid="swarm-out-review-land-failed">
              <span style={{ color: TONE_VAR.error }}>{t('swarmOut.detail.failed')}</span>
              {onRetry && <button type="button" className="ml-2 btn-sm btn-secondary" onClick={onRetry}>{t('swarmOut.review.retry')}</button>}
            </div>
          )}
          {full && repos.length === 0 && <div className={`text-[12px] ${muted}`}>{t('swarmOut.review.landNothing')}</div>}
          {repos.map((x) => (
            <div key={x.dir!} className="min-w-0">
              <div className={`text-[11.5px] truncate ${muted}`}>{x.dir}</div>
              {x.url ? <ReturnPanel dispatchId={id} url={x.url} autoPrepare onLanded={() => { setLanded((l) => [...l, x.dir!]); onReload?.(); }}
                // landed by hand counts for the whole run: offered only when every other
                // repository has landed (or there is none)
                onByHand={repos.every((o) => o.dir === x.dir || landed.includes(o.dir!)) ? () => setMode('manual') : undefined} />
                : <div className={`text-[12px] ${muted}`} data-testid="swarm-out-review-land-no-url">{t('swarmOut.review.landNoUrl')}</div>}
            </div>
          ))}
        </>
      ),
    });
  }

  const hint = actions.find((a) => a.variant === 'hint');
  const commands = actions.filter((a) => a.variant !== 'hint');
  const primary = commands.find((a) => a.primary);
  const discard = commands.find((a) => a.key === 'discard');
  // "re-run with a correction" sits with accept: the choice in front of you is pass, or send back
  const beside = primary?.key === 'accept' ? commands.find((a) => a.key === 'rerun') : undefined;
  const others = commands.filter((a) => a !== primary && a !== discard && a !== beside);
  // a button holds only what it does and its key; what it means is a line under its group
  const cmd = (a: SwarmOutAction, look: 'primary' | 'plain' | 'danger') => {
    const m = commandMeta(a);
    const name = t(m.name);
    const cls = look === 'danger' || (look === 'primary' && a.key === 'stop') ? 'btn-danger'
      : look === 'primary' ? 'btn-primary' : 'btn-secondary';
    const style: React.CSSProperties | undefined = look === 'primary' && a.key === 'accept'
      ? { background: TONE_VAR.ok, color: '#05210f' } : undefined;
    const disabled = busy || (a.key === 'open_session' && !openRun) || (a.key === 'accept' && a.variant === 'verdict' && !r.result?.ended_at);
    return (
      <button key={`${a.key}:${a.variant ?? ''}`} type="button" disabled={disabled} onClick={() => give(a)}
        className={`btn-md ${cls} w-full justify-between disabled:opacity-50`} style={style}
        data-testid={`swarm-out-cmd-${a.key}`} data-primary={a.primary || undefined} data-variant={a.variant}>
        <span>{a.key === 'discard' && !name.endsWith('…') ? `${name}…` : name}</span>
        {m.hint && <span className="text-[11px] opacity-70" data-testid="swarm-out-command-key">{m.hint}</span>}
      </button>
    );
  };
  const help = (list: SwarmOutAction[]) => (
    <div className={`flex flex-col gap-0.5 text-[11.5px] ${muted}`} data-testid="swarm-out-command-help">
      {list.map((a) => <div key={`${a.key}:${a.variant ?? ''}`}>{list.length > 1 && <b className="font-medium">{t(commandMeta(a).name)}: </b>}{t(commandMeta(a).does)}</div>)}
    </div>
  );
  const head = [primary, beside].filter(Boolean) as SwarmOutAction[];
  return (
    <div className="flex flex-col gap-5 h-full" data-testid="swarm-out-command-panel">
      {header}
      {hint && (
        <div className="rounded-lg px-3 py-2.5 text-[12.5px]" data-testid="swarm-out-command-hint"
          style={{ background: `color-mix(in srgb, ${TONE_VAR.attention} 9%, transparent)` }}>
          {hasWaits ? t(`swarmOut.review.hint.${hint.key}`, { defaultValue: t(commandMeta(hint).does) }) : t('swarmOut.review.answerElsewhere')}
          {commandMeta(hint).hint && <span className="ml-2 text-[11px] opacity-70">{commandMeta(hint).hint}</span>}
        </div>
      )}
      {commands.length === 0 && !hint && <div className={`text-[12px] ${muted}`}>{t('swarmOut.review.nothingToDo')}</div>}
      {head.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {head.map((a) => cmd(a, a === primary ? 'primary' : 'plain'))}
          {help(head)}
        </div>
      )}
      {others.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className={`text-[11px] tracking-wide ${muted}`}>{t('swarmOut.review.also')}</div>
          {others.map((a) => cmd(a, 'plain'))}
          {help(others)}
        </div>
      )}
      {discard && (
        <div className="mt-auto pt-3 border-t border-[var(--color-border)] flex flex-col gap-1.5">
          {cmd(discard, 'danger')}
          {help([discard])}
        </div>
      )}
      {error && <div className="text-[12px]" style={{ color: TONE_VAR.error }} role="alert" data-testid="swarm-out-review-error">{error}</div>}
    </div>
  );
}

/** One confirmation, in the panel in place of the commands (design
 *  2026-10-31-swarm-out-single-loop-fixes §2.3): its title (red for one that cannot be
 *  undone), what it does, what it needs typed, the error if it failed, and one row:
 *  Cancel (Esc) on the left, the confirm (⌘↩) on the right. */
export function ConfirmSheet({ title, consequence, children, confirmLabel, onConfirm, onCancel, confirmDisabled,
  danger = false, confirmTone, busy = false, error, testId, confirmTestId = 'swarm-out-review-submit' }: {
  title: string; consequence?: string; children?: React.ReactNode;
  confirmLabel: string; onConfirm: () => void; onCancel: () => void;
  confirmDisabled?: boolean; danger?: boolean; confirmTone?: 'ok'; busy?: boolean; error?: string | null;
  testId: string; confirmTestId?: string;
}) {
  const { t } = useTranslation();
  const solid: React.CSSProperties = danger ? { background: TONE_VAR.error, color: '#fff', borderColor: 'transparent' }
    : confirmTone === 'ok' ? { background: TONE_VAR.ok, color: '#05210f', borderColor: 'transparent' } : {};
  return (
    <div className="flex flex-col gap-2.5" data-testid="confirm-sheet" data-for={testId}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!busy && !confirmDisabled) onConfirm(); }
      }}>
      <div data-testid={testId} className="contents">
        <div className="text-[14px] font-semibold" style={danger ? { color: TONE_VAR.error } : undefined}>{title}</div>
        {consequence && <div className="text-[12.5px] text-[var(--color-text-secondary)]">{consequence}</div>}
        {children}
        {error && <div className="text-[12px]" style={{ color: TONE_VAR.error }} role="alert" data-testid="swarm-out-review-error">{error}</div>}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button type="button" className="btn-md btn-secondary" onClick={onCancel} data-testid="swarm-out-review-cancel">
            {t('swarmOut.review.cancel')} <span className="text-[11px] opacity-70">Esc</span>
          </button>
          <button type="button" className={`btn-md ${danger || confirmTone ? '' : 'btn-primary'} disabled:opacity-50`} style={solid}
            disabled={busy || confirmDisabled} onClick={onConfirm} data-testid={confirmTestId}>
            {confirmLabel} <span className="text-[11px] opacity-70">{modKeyLabel()}↩</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export function RunOverlay({ runs, asks, answered = [], order, walk = 'waiting', id, onSelect, onSettled, onClose, onFileOpened, onReload, now }: {
  runs: SwarmOutRun[]; asks: SwarmOutAsk[];
  /** the waits answered from here: each stays on the card as its one line */
  answered?: SwarmOutAsk[];
  /** the runs J / K walk, frozen when the card opened: the runs waiting for you, or the
   *  history's records as listed (``walk``) */
  order: string[];
  walk?: 'waiting' | 'records';
  id: string;
  onSelect: (id: string) => void;
  /** a run was settled from the card: drop it from the order */
  onSettled?: (id: string) => void;
  onClose: () => void;
  /** a file was opened from inside the card (it has closed): the host steps aside too,
   *  so the Canvas is in view (a Brain page's overlay) */
  onFileOpened?: () => void;
  onReload: () => void;
  now: number;
}) {
  const { t } = useTranslation();
  const r = runs.find((x) => x.dispatch_id === id) ?? null;
  const hidden = useContext(PanelHiddenContext);
  const [tab, setTab] = useState<Tab>('deliverable');
  const [mode, setMode] = useState<PanelMode>('list');
  const [full, setFull] = useState<SwarmOutResult | null>(null);
  const [fullRead, setFullRead] = useState(false);
  const [fullFailed, setFullFailed] = useState(false);
  const [hostAnswers, setHostAnswers] = useState<SwarmOutHostAnswer[]>([]);
  const [fetchKey, setFetchKey] = useState(0);
  // an answer half given in a wait card: kept like a half-written correction
  const [askDirty, setAskDirty] = useState(false);
  const waitsRef = useRef<HTMLDivElement>(null);
  // one stable callback per card: each says whether it holds a pick; the card keeps
  // what is typed while any one does
  const dirtyFor = useMemo(() => {
    const held = new Set<string>();
    const made = new Map<string, (d: boolean) => void>();
    return (key: string) => {
      let f = made.get(key);
      if (!f) {
        f = (d: boolean) => { if (d) held.add(key); else held.delete(key); setAskDirty(held.size > 0); };
        made.set(key, f);
      }
      return f;
    };
  }, [id]);
  const dialogRef = useRef<HTMLDivElement>(null);
  // focus goes back to what opened the card when it closes (it closes by unmounting)
  const opener = useRef<HTMLElement | null>(typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null);
  useEffect(() => () => { const el = opener.current; if (el && el.isConnected) el.focus(); }, []);
  const openRun = useOpenRun(r ?? ({ session_id: null } as SwarmOutRun));
  const endedAt = r?.result?.ended_at ?? null;
  useEffect(() => { setTab('deliverable'); setMode('list'); setAskDirty(false); }, [id]);
  const keeps = mode !== 'list' || askDirty;     // something typed that a step away would lose
  const keepsRef = useRef(keeps);
  keepsRef.current = keeps;
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const askDirtyRef = useRef(askDirty);
  askDirtyRef.current = askDirty;
  useEffect(() => {
    let alive = true;
    setFull(null); setFullRead(false); setFullFailed(false); setHostAnswers([]);
    swarmOutService.result(id).then((rec) => {
      if (alive) { setFull(rec.record?.result ?? null); setHostAnswers(rec.host_answers ?? []); setFullRead(true); }
    })
      .catch(() => { if (alive) setFullFailed(true); });
    return () => { alive = false; };
  }, [id, endedAt, fetchKey]);
  // the card never outlives its place: its run gone, or the panel off screen
  useEffect(() => { if (!r || hidden) onClose(); }, [r, hidden, onClose]);
  // a file opened by a click inside it opens in the Canvas, beneath: the card steps
  // aside. The click is marked in the capture phase and the open-file event it causes is
  // sent in the same task; one an agent sends (detail.by 'agent') is not the person's.
  const clickInside = useRef(false);
  const markClick = () => { clickInside.current = true; queueMicrotask(() => { clickInside.current = false; }); };
  useEffect(() => {
    const onOpenFile = (e: Event) => {
      if (!clickInside.current || (e as CustomEvent).detail?.by === 'agent' || keepsRef.current) return;
      onClose();
      onFileOpened?.();
    };
    // a changed file's diff (swarm:open-file-versions) is only ever sent by a click on
    // this card's row, after its reads: the card steps aside unless a correction or a
    // discard note is being typed, or an answer is half given (landing is not lost:
    // reading a protected file before ticking it is what the diff is for)
    const onOpenVersions = () => {
      if (modeRef.current === 'rerun' || modeRef.current === 'discard' || askDirtyRef.current) return;
      onClose();
      onFileOpened?.();
    };
    document.addEventListener('swarm:open-file', onOpenFile);
    document.addEventListener(OPEN_FILE_VERSIONS_EVENT, onOpenVersions);
    return () => {
      document.removeEventListener('swarm:open-file', onOpenFile);
      document.removeEventListener(OPEN_FILE_VERSIONS_EVENT, onOpenVersions);
    };
  }, [onClose, onFileOpened]);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    setRefreshing(true); setRefreshError(null);
    try {
      const got = await swarmOutService.refresh(id);
      if (got?.result_error) setRefreshError(resultErrorLabel(got.result_error));
      onReload(); setFetchKey((k) => k + 1);
    } catch {
      setRefreshError(t('swarmOut.detail.failed'));
    } finally { setRefreshing(false); }
  }, [id, onReload, t]);
  useEffect(() => { setRefreshError(null); }, [id]);

  const pos = order.indexOf(id);
  const step = useCallback((d: number) => {
    if (pos < 0 || order.length === 0) return;
    const next = order[(pos + d + order.length) % order.length];
    if (next) onSelect(next);
  }, [onSelect, order, pos]);
  const onDone = useCallback((closed: boolean) => {
    onReload();
    if (!closed) return;
    onSettled?.(id);
    // the history's records are not a queue of work: settling one ends the look at it
    if (walk === 'records') { onClose(); return; }
    const rest = order.filter((x) => x !== id);
    const next = rest[pos >= 0 ? Math.min(pos, rest.length - 1) : 0];
    if (next) onSelect(next); else onClose();
  }, [id, onClose, onReload, onSelect, onSettled, order, pos, walk]);

  // Esc: back from a box / landing / confirmation to the commands, else out
  useDialogLayer(true, dialogRef, () => { if (mode !== 'list') setMode('list'); else if (!askDirty) onClose(); });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (keeps || typingIn(e.target) || !inScope(dialogRef, e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'j' || e.key === 'J') { e.preventDefault(); step(1); }
      if (e.key === 'k' || e.key === 'K') { e.preventDefault(); step(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [keeps, step]);

  const res = full ?? r?.result ?? null;
  const word = useMemo(() => autonomyWord(res), [res]);
  if (!r) return null;
  const waits = waitsOf(r, asks, answered);
  const leaveTo = () => { onClose(); };
  const facts = agentFacts(r);
  const acceptance = r.project_agent?.acceptance;
  const checks = machineChecks(r, full);
  // the check statuses were read from the commands' text (the Hive keeps no check record)
  const checksInferred = (full?.checks_inferred ?? r.result?.checks_inferred) === true;
  // ... or from the Hive's own record of each run: said, since it is the Hive's word
  const checksFromRecord = !checksInferred && (full?.checks_from_record ?? r.result?.checks_from_record) === true;
  const delivered = (res?.work.files.length ?? 0) + (full?.work.repos ?? []).filter((x) => x.commits_total > 0).length;
  const lessons = res?.lessons?.returned ?? 0;
  const running = stageOf(r) === 'running';
  const tabs: { key: Tab; label: string; count?: number; testid: string }[] = [
    { key: 'deliverable', label: running ? t('swarmOut.review.tab.now') : t('swarmOut.review.tab.deliverable'), count: running ? undefined : delivered },
    { key: 'autonomy', label: t('swarmOut.review.tab.autonomy') },
    { key: 'process', label: t('swarmOut.review.tab.process'), count: res?.work.steps.total ?? 0 },
    { key: 'cost', label: t('swarmOut.review.tab.cost') },
    { key: 'lessons', label: t('swarmOut.review.tab.lessons'), count: lessons },
  ].map((x) => ({ ...x, key: x.key as Tab, testid: `swarm-out-review-tab-${x.key}` }));
  const doneAgo = res?.ended_at ? (now - Date.parse(res.ended_at) < 60_000 ? t('swarmOut.review.doneJustNow') : t('swarmOut.review.doneAgo', { ago: ago(res.ended_at, now) })) : null;
  const meta = [facts.brain, facts.agent, r.hive, doneAgo, res ? t('swarmOut.review.turns', { count: res.turns }) : null,
    res?.cost_usd != null ? `$${res.cost_usd.toFixed(1)}` : null].filter(Boolean).join(' · ');

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'color-mix(in srgb, #000 55%, transparent)' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget && !keeps) onClose(); }} data-testid="swarm-out-review-overlay">
      <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={r.title} onClickCapture={markClick}
        className="flex flex-col w-[1240px] max-w-full h-[min(880px,calc(100vh-32px))] rounded-[14px] border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl overflow-hidden text-[13px] outline-none">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 border-b border-[var(--color-border)] min-w-0">
          <div className="min-w-0 flex-1 basis-[280px]">
            <div className="text-[15px] font-semibold truncate" title={r.title}>{r.title}</div>
            <div className={`text-[12px] truncate ${muted}`}>
              {meta}
              {word && <> · <button type="button" className="underline-offset-2 hover:underline" style={{ color: word.kind === 'full' || word.state === 'PASS' ? TONE_VAR.ok : TONE_VAR.attention }}
                onClick={() => setTab('autonomy')} data-testid="swarm-out-review-autonomy-word">{word.text}</button></>}
            </div>
          </div>
          <div className={`ml-auto flex flex-wrap items-center gap-1.5 text-[12px] ${muted}`}>
            {pos >= 0 && <>
              <span className="whitespace-nowrap" data-testid="swarm-out-review-position">{t(walk === 'records' ? 'swarmOut.review.positionRecords' : 'swarmOut.review.position', { n: pos + 1, total: order.length })}</span>
              <button type="button" className={btn} onClick={() => step(-1)} disabled={keeps} aria-label={t(walk === 'records' ? 'swarmOut.review.previousRecord' : 'swarmOut.review.previous')}>K</button>
              <button type="button" className={btn} onClick={() => step(1)} disabled={keeps} aria-label={t(walk === 'records' ? 'swarmOut.review.nextRecord' : 'swarmOut.review.next')}>J</button>
            </>}
            {openRun && actionsOf(r).some((a) => a.key === 'open_session') && (
              <button type="button" className={`${btn} font-semibold whitespace-nowrap`} style={{ color: 'var(--color-primary)', borderColor: 'var(--color-primary)' }}
                onClick={() => { leaveTo(); openRun(); }} data-testid="swarm-out-review-open">{t('swarmOut.review.cmd.open.name')} ↗</button>
            )}
            <button type="button" className={btn} onClick={() => { void refresh(); }} disabled={refreshing}
              title={t('swarmOut.detail.refresh')} data-testid="swarm-out-review-refresh">↻ {t('swarmOut.review.refresh')}</button>
            {refreshError && <span className="text-[12px]" style={{ color: TONE_VAR.error }} role="alert" data-testid="swarm-out-review-refresh-error">{refreshError}</span>}
            <button type="button" className={btn} onClick={onClose} aria-label={t('swarmOut.review.close')} data-testid="swarm-out-review-close">✕</button>
          </div>
        </div>
        <div className="flex-1 grid min-h-0 grid-cols-1 md:grid-cols-[minmax(0,1fr)_290px] overflow-auto md:overflow-hidden">
          <div className="flex flex-col min-h-0 md:border-r border-[var(--color-border)]">
            {(acceptance || checks.length > 0) && (
              <div className="px-4 py-3 border-b" style={{ background: `color-mix(in srgb, ${TONE_VAR.attention} 7%, transparent)`, borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 30%, var(--color-border))` }}
                data-testid="swarm-out-review-acceptance">
                {acceptance && <>
                  <div className="text-[11.5px] font-semibold" style={{ color: TONE_VAR.attention }}>{t('swarmOut.review.acceptance')}</div>
                  <div className="text-[14px] mt-0.5 break-words">{acceptance}</div>
                </>}
                {checks.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1 text-[12px]">
                    {checks.map((c) => <span key={c.key} data-testid="swarm-out-review-check" data-ok={c.ok}>
                      <span style={{ color: c.ok ? TONE_VAR.ok : TONE_VAR.attention }}>{c.ok ? '✓' : '△'}</span> {c.text}
                      {c.key === 'checks' && checksInferred && <span className={muted} data-testid="swarm-out-review-checks-inferred"> · {t('swarmOut.review.check.inferred')}</span>}
                      {c.key === 'checks' && checksFromRecord && <span className={muted} data-testid="swarm-out-review-checks-recorded"> · {t('swarmOut.review.check.fromRecord')}</span>}</span>)}
                  </div>
                )}
              </div>
            )}
            {waits.length > 0 && (
              // what waits on you is the decision of this moment: above the tabs (design
              // 2026-10-31-swarm-out-single-loop-fixes §2.2), the chat's own card
              <div ref={waitsRef} className="px-5 pt-3 flex flex-col gap-2 max-h-[55%] overflow-auto" data-testid="swarm-out-review-waits">
                {waits.map((w) => (
                  <SwarmOutWaitCard key={`${w.hive_id}/${w.request_id}`} wait={w} onAnswered={onReload}
                    onDirty={dirtyFor(`${w.hive_id}/${w.request_id}`)}
                    onOpenSession={openRun ? () => { leaveTo(); openRun(); } : undefined} />
                ))}
              </div>
            )}
            <div className="px-5 py-2 border-b border-[var(--color-border)]"><Tabs items={tabs} value={tab} onChange={setTab} /></div>
            <div className="flex-1 md:overflow-auto px-5 py-5" role="tabpanel">
              {tab === 'deliverable' && <DeliverableTab r={r} full={full} fullRead={fullRead} fullFailed={fullFailed} onRetry={() => setFetchKey((k) => k + 1)}
                waits={waits.length} />}
              {tab === 'autonomy' && <AutonomyTab res={res} answers={hostAnswers} now={now} />}
              {tab === 'process' && (
                <div className="flex flex-col gap-1 text-[12.5px]" data-testid="swarm-out-review-process">
                  {res ? [...workLines(res), executionLine(res), checksLine(res), gateLine(res)].filter(Boolean).map((l) => <div key={l!}>{l}</div>)
                    : <div className={muted}>{t('swarmOut.review.noResult')}</div>}
                  {res && Object.keys(res.work.steps.by_tool).length > 0 && (
                    <div className={muted}>{t('swarmOut.detail.toolCalls', { count: res.work.steps.total, list: Object.entries(res.work.steps.by_tool).map(([n, c]) => `${n} ${c}`).join(' · ') })}</div>
                  )}
                </div>
              )}
              {tab === 'cost' && (res ? <><Stats stats={runStats(res)} /><Stats stats={tokenStats(res)} testId="swarm-out-run-tokens" min={72} /></>
                : <div className={muted}>{t('swarmOut.review.noResult')}</div>)}
              {tab === 'lessons' && <div className="text-[12.5px]">{(res && lessonsLine(res)) || <span className={muted}>{t('swarmOut.review.noLessons')}</span>}</div>}
            </div>
          </div>
          <div className="p-3.5 bg-[var(--color-bg-chrome)] md:overflow-auto border-t md:border-t-0 border-[var(--color-border)]">
            <CommandPanel r={r} full={full} fullRead={fullRead} fullFailed={fullFailed} onRetry={() => setFetchKey((k) => k + 1)} asks={asks}
              hasWaits={waits.length > 0}
              mode={mode} setMode={setMode} onDone={onDone}
              onShowDeliverable={() => {
                const target = waitsRef.current?.querySelector<HTMLElement>('button:not([disabled])');
                if (!target) { setTab('deliverable'); return; }     // no card here: where to answer it
                target.scrollIntoView?.({ block: 'nearest' }); target.focus();
              }}
              onLeave={leaveTo} onReload={onReload} now={now} scope={dialogRef} />
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
