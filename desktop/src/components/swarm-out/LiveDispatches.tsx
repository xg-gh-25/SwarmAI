/**
 * LiveDispatches — the run view of the Swarm Out panel, on real data only
 * (design docs/design/2026-10-28-swarm-out-live-dashboard.md).
 *
 * It answers three things, in this order: where the work stands (one compact card on
 * top: the questions waiting, the free slots and the runs running as three numbers, and
 * each Hive's slots under them), does a Hive need me (the questions waiting, each with
 * its answer controls — the one thing in the accent colour), and how are the runs I
 * sent (running, stopped in the last 24 h, state unknown). What has no data source yet
 * says TBD and which source is missing; nothing on this view is a sample. What went
 * on in the past (the runs by Brain, type, Hive) is the history view's (HistoryView).
 *
 * A run that is not running any more shows what its Hive recorded (design
 * docs/design/2026-10-28-swarm-out-run-results.md): how it ended, what it did, its
 * numbers; the details (the reply, code per repository, files, steps, a pipeline's
 * five rows) open on demand, across the whole row, with a second Hide at their end.
 * Every card names its Brain and Project Agent as two labelled fields, and its facts
 * (ended, duration, tokens, cost, human loops, failed steps) as a row of cells. A
 * pipeline run's Autonomy Judge tag sits next to how it ended: its verdicts come with
 * /runs, the numbers behind them once the details are read. A run whose Hive session
 * this Host knows has an explicit Open button (the title opens it too).
 *
 * The reply is the agent's markdown, rendered by the app's own MarkdownRenderer (the
 * chat's: no raw HTML, remote images dropped, an external link opens only on a click,
 * in the browser).
 *
 * Status colours follow ./tone (green done or free, blue running, amber needs you or
 * did not finish, red failed, grey unknown): only the dot and the status word are
 * coloured. Labels come from the locale files (swarmOut.*); everything a Hive wrote
 * (question text, command, reason, the reply, paths) is data, never translated; all of
 * it but the reply is shown as plain text. Polls /api/swarm-out/runs every 30 s while shown;
 * the fleet comes from the panel (one poll of /api/swarm-out/fleet).
 */
import { useCallback, useEffect, useState } from 'react';
import { useOpenRun } from './openSession';
import { useTranslation } from 'react-i18next';
import { swarmOutService, type SwarmOutAsk, type SwarmOutFleet, type SwarmOutQuestion, type SwarmOutResult, type SwarmOutRun } from '../../services/swarmOut';
import { JudgeTag } from './JudgeTag';
import { ReturnPanel } from './ReturnPanel';
import { LessonsAccept } from './LessonsAccept';
import MarkdownRenderer from '../common/MarkdownRenderer';
import { agentFacts, checksIncomplete, checksLine, endedLabel, errorLabel, gateLine, hiveStatusLabel, lessonsLine, panelNumbers, resultErrorLabel, runGroups, runJudge, runStats, stateLabel, tokenStats, useRuns, when, workLines, type RunStat } from './live';
import { dotStyle, hiveTone, runTone, TONE_VAR, type Tone } from './tone';

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';
const btn = 'px-2.5 py-1 rounded-md text-[12.5px] border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50';

function errText(e: unknown, fallback: string): string {
  const m = (e as { message?: string })?.message;
  return m && m.length < 200 ? m : fallback;
}

/** A small status dot in a tone (./tone). */
export function Dot({ tone, className = '' }: { tone: Tone; className?: string }) {
  return <span aria-hidden className={`inline-block w-[7px] h-[7px] rounded-full flex-shrink-0 ${className}`} style={dotStyle(tone)} data-tone={tone} />;
}

/** A card border tinted for a tone that should stand out (attention, error); others keep the plain border. */
const cardTint = (tone: Tone): React.CSSProperties | undefined =>
  tone === 'error' || tone === 'attention' ? { borderColor: `color-mix(in srgb, ${TONE_VAR[tone]} 45%, var(--color-border))` } : undefined;

function AskCard({ ask, onDone }: { ask: SwarmOutAsk; onDone: () => void }) {
  const { t } = useTranslation();
  // single-select / typed answers: the text; multi-select: the picked labels
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [multi, setMulti] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const send = async (body: { answers?: Record<string, string>; decision?: 'approve' | 'deny' }) => {
    setBusy(true);
    setError('');
    try {
      await swarmOutService.answer(ask, body);
      onDone();
    } catch (e) {
      setError(errText(e, t('swarmOut.live.submitFailed')));
    } finally {
      setBusy(false);
    }
  };

  const questions = ask.questions ?? [];
  const isMulti = (q: SwarmOutQuestion) => q.multiSelect && q.options.length > 0;
  // a multi-select answer is its picks joined with ", " in option order (as the chat's
  // AskUserQuestion); kept as a list until sent, so a label with a comma stays whole
  const answerOf = (q: SwarmOutQuestion) => (isMulti(q)
    ? q.options.map((o) => o.label).filter((l) => (multi[q.question] ?? []).includes(l)).join(', ')
    : (picks[q.question] ?? ''));
  const complete = questions.length > 0 && questions.every((q) => answerOf(q).trim());
  const toggle = (q: SwarmOutQuestion, label: string) => {
    if (!isMulti(q)) { setPicks((p) => ({ ...p, [q.question]: label })); return; }
    setMulti((m) => {
      const cur = m[q.question] ?? [];
      return { ...m, [q.question]: cur.includes(label) ? cur.filter((x) => x !== label) : [...cur, label] };
    });
  };
  const picked = (q: SwarmOutQuestion, label: string) =>
    isMulti(q) ? (multi[q.question] ?? []).includes(label) : picks[q.question] === label;
  const omitted = (ask.omitted?.questions ?? 0) + (ask.omitted?.options ?? 0);

  return (
    <div className={`${card} px-4 py-3`} style={{ borderLeft: `3px solid ${TONE_VAR.attention}`, ...cardTint('attention') }} data-testid="swarm-out-ask">
      <div className={`text-[12px] flex items-center gap-1.5 min-w-0 ${muted}`}>
        <Dot tone="attention" />
        <span className="min-w-0 truncate">{ask.hive} · <span data-testid="swarm-out-ask-source">{ask.dispatch_title
          ?? (ask.possible_dispatch_title ? t('swarmOut.ask.maybe', { title: ask.possible_dispatch_title }) : t('swarmOut.ask.otherSession'))}</span> · {when(ask.asked_at)} · <span style={{ color: TONE_VAR.attention }}>{ask.refused ? t('swarmOut.ask.notDelivered') : t('swarmOut.ask.waiting')}</span></span>
      </div>
      {ask.refused ? (
        <div className="mt-1 text-[12.5px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-refused">
          {t('swarmOut.ask.refused')}
        </div>
      ) : ask.kind === 'approval' ? (
        <>
          <div className="mt-1 font-medium">{t('swarmOut.ask.approvalTitle')}{ask.tool_name && <span className={`ml-1.5 font-mono text-[12px] ${muted}`} data-testid="swarm-out-ask-tool">{ask.tool_name}</span>}</div>
          {ask.reason && <div className={`mt-0.5 text-[12.5px] ${muted}`}>{ask.reason}</div>}
          <pre className="mt-2 mb-0 p-2 rounded-md bg-[var(--color-hover)] text-[12px] whitespace-pre-wrap break-all font-mono">{ask.command}</pre>
          {ask.truncated && (
            <div className="mt-1 text-[12px] text-[var(--color-status-warning)]" data-testid="swarm-out-ask-truncated">
              {t('swarmOut.ask.truncated')}
            </div>
          )}
          <div className="mt-2 flex gap-2">
            {!ask.truncated && <button type="button" className={btn} disabled={busy} onClick={() => send({ decision: 'approve' })}>{t('swarmOut.ask.approve')}</button>}
            <button type="button" className={btn} disabled={busy} onClick={() => send({ decision: 'deny' })}>{t('swarmOut.ask.deny')}</button>
          </div>
        </>
      ) : (
        <>
          {questions.map((q) => (
            <div key={q.question} className="mt-2">
              <div className="font-medium whitespace-pre-wrap">{q.question}</div>
              {q.options.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {q.options.map((o) => (
                    <button
                      key={o.label}
                      type="button"
                      title={o.description || undefined}
                      aria-pressed={picked(q, o.label)}
                      className={`${btn} ${picked(q, o.label) ? 'bg-[var(--color-hover)] font-medium' : ''}`}
                      onClick={() => toggle(q, o.label)}
                    >{o.label}</button>
                  ))}
                </div>
              )}
              {/* a multi-select question with options is answered by its picks only */}
              {!(q.multiSelect && q.options.length > 0) && (
                <input
                  aria-label={t('swarmOut.ask.answerFor', { question: q.question })}
                  className="mt-1.5 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1 text-[12.5px]"
                  placeholder={q.options.length ? t('swarmOut.ask.ownAnswer') : t('swarmOut.ask.yourAnswer')}
                  value={q.options.some((o) => o.label === picks[q.question]) ? '' : (picks[q.question] ?? '')}
                  onChange={(e) => setPicks((p) => ({ ...p, [q.question]: e.target.value }))}
                />
              )}
            </div>
          ))}
          {omitted > 0 && (
            <div className={`mt-1.5 text-[12px] ${muted}`} data-testid="swarm-out-ask-omitted">
              {t('swarmOut.ask.omitted', { count: omitted })}
            </div>
          )}
          <div className="mt-2">
            <button type="button" className={btn} disabled={busy || !complete}
              onClick={() => send({ answers: Object.fromEntries(questions.map((q) => [q.question, answerOf(q)])) })}>{t('swarmOut.ask.submit')}</button>
          </div>
        </>
      )}
      {error && <div className="mt-1.5 text-[12px] text-[var(--color-status-error)]" role="alert">{error}</div>}
    </div>
  );
}

/** A part with no data source yet: its title and what is missing, demoted. */
export function Tbd({ title, missing, testId }: { title: string; missing: string; testId?: string }) {
  const { t } = useTranslation();
  return (
    <section className="mt-5" data-testid={testId ?? 'swarm-out-tbd'}>
      <h3 className={`m-0 text-[13px] font-semibold ${muted}`}>{title}</h3>
      <div className="mt-1 text-[12px] text-[var(--color-text-dim)]">{t('swarmOut.tbd', { missing })}</div>
    </section>
  );
}

/** One headline number inline (value, then its label); its tone colours the value
 *  (none = neutral: a zero or unknown). */
function Num({ value, label, tone, testId }: { value: React.ReactNode; label: string; tone?: Tone; testId: string }) {
  return (
    <div className="flex items-baseline gap-1.5 min-w-0" data-testid={testId} data-tone={tone}>
      <span className="text-[18px] font-semibold leading-none tabular-nums" style={tone ? { color: TONE_VAR[tone] } : undefined}>{value}</span>
      <span className={`text-[12px] ${muted}`}>{label}</span>
    </div>
  );
}

/** The top card: the three numbers in its header, each Hive's memory under them (a
 *  Hive queues a new task only when its memory is under critical pressure). What the
 *  count and the bar mean is explained on hover (the title and every status). */
function Fleet({ fleet, failed, numbers }: { fleet: SwarmOutFleet | null; failed?: boolean; numbers: React.ReactNode }) {
  const { t } = useTranslation();
  const hives = fleet?.hives ?? [];
  const note = t('swarmOut.fleet.note');
  return (
    <section className={card} data-testid="swarm-out-fleet">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5 px-4 pt-3 pb-2">
        <h3 className="m-0 text-[13px] font-semibold" title={note}>{t('swarmOut.fleet.title')}</h3>
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 min-w-0" data-testid="kpi-grid">{numbers}</div>
      </div>
      {failed && <div className={`px-4 text-[12px] ${muted}`} data-testid="swarm-out-fleet-failed">{t('swarmOut.fleet.failed')}</div>}
      {fleet === null ? (!failed && <div className={`px-4 pb-3 text-[12px] ${muted}`}>{t('swarmOut.loading')}</div>) : (
        <div className="grid grid-cols-[minmax(80px,220px)_minmax(0,1fr)_minmax(0,max-content)] items-center gap-x-3 gap-y-2 px-4 pt-2.5 pb-3 border-t border-[var(--color-border)]">
          {hives.map((h) => {
            const up = h.status === 'running' && !!h.reachable;
            const count = { count: h.streaming ?? 0 };
            const status = h.status !== 'running' ? hiveStatusLabel(h.status)
              : !h.reachable ? t('swarmOut.fleet.unreachable')
              // an older Hive reports no memory: its saturated still means a full stream cap
              : h.saturated ? t(h.memory_percent == null ? 'swarmOut.fleet.busy' : 'swarmOut.fleet.full', count)
              : t('swarmOut.fleet.used', count);
            const tone = hiveTone(h);
            const mem = up && h.memory_percent != null ? Math.min(100, Math.max(0, h.memory_percent)) : null;
            const memTone: Tone = h.saturated ? 'attention' : 'ok';
            return (
              <div key={h.name} className="contents" data-testid="swarm-out-fleet-hive" data-tone={tone}>
                <div className="font-medium min-w-0 flex items-start gap-2" title={h.name}>
                  <Dot tone={tone} className="mt-[6px]" />
                  <div className="min-w-0">
                    <div className="truncate">{h.name}</div>
                    <div className={`text-[11.5px] font-normal truncate ${muted}`}>{[h.version, h.instance_type].filter(Boolean).join(' · ')}</div>
                  </div>
                </div>
                {/* the Hive's memory in use: green while a new task starts at once, amber
                    once it is under critical pressure (a new task queues) */}
                <div className="min-w-0 h-[12px] rounded-[3px] border overflow-hidden" data-testid="swarm-out-memory"
                  data-percent={mem ?? undefined}
                  title={mem != null ? t('swarmOut.fleet.memory', { percent: Math.round(mem) }) : undefined}
                  style={{ opacity: mem != null ? 1 : 0.4, borderColor: `color-mix(in srgb, ${TONE_VAR[memTone]} 55%, transparent)`,
                    background: `color-mix(in srgb, ${TONE_VAR[memTone]} 10%, transparent)` }}>
                  {mem != null && <div className="h-full" style={{ width: `${mem}%`, background: TONE_VAR[memTone] }} />}
                </div>
                <div className={`text-[12px] text-right truncate min-w-0 ${tone === 'error' || tone === 'attention' ? '' : muted}`}
                  style={tone === 'error' || tone === 'attention' ? { color: TONE_VAR[tone] } : undefined}
                  title={`${status} · ${note}`} data-testid="swarm-out-fleet-status">{status}</div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** The details of a run, read from its whole record. The record is held by the card
 *  (`full` / `setFull`), so the card's Autonomy Judge tag gets its numbers from it. */
function ResultDetail({ r, full, setFull, onRefreshed, onClose }: {
  r: SwarmOutRun; full: SwarmOutResult | null; setFull: (res: SwarmOutResult | null) => void;
  onRefreshed: () => void; onClose: () => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('loading');
  const id = r.dispatch_id;
  const load = useCallback(async () => {
    setState('loading');
    try {
      const rec = await swarmOutService.result(id);
      setFull(rec.record?.result ?? null);
      setState('idle');
    } catch {
      setState('failed');
    }
  }, [id, setFull]);
  useEffect(() => { void load(); }, [load]);   // once, when the details open
  const refresh = async () => {
    setState('loading');
    try {
      await swarmOutService.refresh(r.dispatch_id);
      onRefreshed();
      await load();
    } catch {
      setState('failed');
    }
  };
  // the details come from the whole record only: /runs carries counts, not the
  // Hive-written text and paths shown here
  const res = full;
  const tools = Object.entries(res?.work.steps.by_tool ?? {});
  return (
    <div className="mt-2 pt-2 border-t border-[var(--color-border)] text-[12.5px]" data-testid="swarm-out-run-detail">
      {state === 'loading' && <div className={muted}>{t('swarmOut.loading')}</div>}
      {state === 'failed' && <div className={muted}>{t('swarmOut.detail.failed')}</div>}
      {res?.error?.message && <div className="mb-2 text-[var(--color-status-error)] whitespace-pre-wrap">{res.error.message}</div>}
      {full?.report ? (
        <>
          <div className={`text-[11.5px] ${muted}`}>{t('swarmOut.detail.lastReply')}</div>
          <div className="mt-1 px-3 py-2 rounded-md bg-[var(--color-hover)] text-[12.5px] max-h-[480px] overflow-auto" data-testid="swarm-out-run-report">
            <MarkdownRenderer content={full.report} />
          </div>
          {full.report_chars > full.report.length && <div className={`mt-1 text-[11.5px] ${muted}`}>{t('swarmOut.detail.fullReport', { count: full.report_chars })}</div>}
        </>
      ) : full && <div className={muted}>{t('swarmOut.detail.noReply')}</div>}
      {(res?.work.repos ?? []).filter((x) => x.files.length || x.commits.length).map((x) => (
        <div key={x.dir ?? x.url ?? ''} className="mt-2" data-testid="swarm-out-run-repo">
          <div className={`text-[11.5px] ${muted}`}>{x.dir}{x.ref ? ` @ ${x.ref}` : ''}{t('swarmOut.detail.commitsOnly')}</div>
          {x.files.map((f, i) => <div key={`${i}:${f.path}`} className="font-mono text-[11.5px] truncate" title={f.path}>{f.path} <span className={muted}>+{f.added ?? '?'} −{f.deleted ?? '?'}</span></div>)}
          {x.files_total > x.files.length && <div className={`text-[11.5px] ${muted}`}>{t('swarmOut.detail.moreFiles', { count: x.files_total - x.files.length })}</div>}
          {x.commits.length > 0 && <div className={`font-mono text-[11.5px] ${muted}`}>{x.commits.map((c) => c.slice(0, 9)).join(' ')}{x.commits_total > x.commits.length ? t('swarmOut.detail.commitsTotal', { count: x.commits_total }) : ''}</div>}
          {r.project_agent?.delivery === 'bundle' && x.url && x.bundle?.sha256 && <ReturnPanel dispatchId={r.dispatch_id} url={x.url} />}
          {x.bundle?.error && <div className={`text-[11.5px] ${muted}`} data-testid="swarm-out-return-unavailable">{t('swarmOut.detail.bundleError', { error: x.bundle.error })}</div>}
        </div>
      ))}
      <LessonsAccept dispatchId={r.dispatch_id} lessons={r.result?.lessons} onDone={onRefreshed} />
      {(res?.work.files ?? []).length > 0 && (
        <div className="mt-2" data-testid="swarm-out-run-files">
          <div className={`text-[11.5px] ${muted}`}>{t('swarmOut.detail.filesWritten')}</div>
          {res!.work.files.map((f, i) => f.local ? (
            // came back into the run's Brain (core/hive_files): opens the copy here
            <button key={`${i}:${f.path ?? ''}`} type="button" title={f.local} data-testid="swarm-out-run-file-local"
              className="block max-w-full text-left font-mono text-[11.5px] truncate text-[var(--color-primary)] hover:underline"
              onClick={() => document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: f.local } }))}>
              {f.local} <span className={muted}>{t('swarmOut.detail.fileHere')}</span>
            </button>
          ) : <div key={`${i}:${f.path ?? ''}`} className="font-mono text-[11.5px] truncate" title={f.path}>{f.path}
            {f.not_placed && <span className={muted} data-testid="swarm-out-run-file-not-placed"> {t('swarmOut.detail.fileNotPlaced')}</span>}</div>)}
        </div>
      )}
      {tools.length > 0 && (
        <div className={`mt-2 text-[11.5px] ${muted}`} data-testid="swarm-out-run-steps">
          {t('swarmOut.detail.toolCalls', { count: res!.work.steps.total, list: tools.map(([n, c]) => `${n} ${c}`).join(' · ') })}
        </div>
      )}
      <div className="mt-2 flex gap-2">
        <button type="button" className={btn} disabled={state === 'loading'} onClick={() => { void refresh(); }}>{t('swarmOut.detail.refresh')}</button>
        {/* the details can be long: Hide is also at their end, not only at the top */}
        <button type="button" className={btn} onClick={onClose} data-testid="swarm-out-run-collapse">{t('swarmOut.detail.collapse')}</button>
      </div>
    </div>
  );
}

/** The card's facts as cells (label over value), wrapping to as many columns as fit; a
 *  strong cell (the total cost, the billed tokens) reads a size up. Also the cells of a
 *  local Run's card (layout/LocalRunCard), so the two cards read the same. */
export function Stats({ stats, testId = 'swarm-out-run-numbers', min = 104 }: { stats: RunStat[]; testId?: string; min?: number }) {
  return (
    <dl className="mt-2 mb-0 grid gap-x-3 gap-y-1.5 pt-2 border-t border-[var(--color-border)]"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))` }} data-testid={testId}>
      {stats.map((x) => (
        <div key={x.key} className="min-w-0" title={x.title} data-testid={`swarm-out-stat-${x.key}`}>
          <dt className="text-[10.5px] text-[var(--color-text-dim)] truncate">{x.label}</dt>
          <dd className={`m-0 tabular-nums truncate ${x.strong ? 'text-[14px] font-semibold' : 'text-[12.5px]'}`}
            style={x.tone ? { color: TONE_VAR[x.tone] } : undefined}>{x.value}</dd>
          {x.sub && <dd className="m-0 text-[10.5px] truncate" style={{ color: TONE_VAR[x.tone ?? 'unknown'] }}>{x.sub}</dd>}
        </div>
      ))}
    </dl>
  );
}

/** The Brain and the Project Agent, each a labelled field. */
function AgentFacts({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const f = agentFacts(r);
  const field = (icon: string, label: string, value: string, testId: string, title?: string) => (
    <span className="inline-flex items-center gap-1 min-w-0 max-w-full" title={title} data-testid={testId}>
      <span className="material-symbols-outlined text-[14px] text-[var(--color-text-dim)]" aria-hidden>{icon}</span>
      <span className="text-[var(--color-text-dim)]">{label}</span>
      <span className={`truncate ${f.none ? muted : 'text-[var(--color-text)] font-medium'}`}>{value}</span>
    </span>
  );
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] min-w-0" data-testid="swarm-out-run-brain">
      {field('psychology', t('swarmOut.card.brain'), f.brain, 'swarm-out-run-brain-name')}
      {field('smart_toy', t('swarmOut.card.agent'), f.agent, 'swarm-out-run-agent', f.title)}
    </div>
  );
}

function RunResult({ r, full }: { r: SwarmOutRun; full: SwarmOutResult | null }) {
  const { t } = useTranslation();
  const res = r.result;
  if (!res) {
    if (r.result_status === 'pending') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">{t('swarmOut.result.pending')}</div>;
    if (r.result_status === 'failed') return <div className={`text-[11.5px] mt-1 ${muted}`} data-testid="swarm-out-run-result">{t('swarmOut.result.failed', { reason: resultErrorLabel(r.result_error) })}</div>;
    return null;
  }
  const ended = endedLabel(res);
  const work = workLines(res);
  const checks = checksLine(res);
  // the verdicts come with /runs; the numbers behind them only in the whole record
  const judge = (full && runJudge(full)) ?? runJudge(res);
  return (
    <div className="mt-1.5" data-testid="swarm-out-run-result">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <div className="text-[12.5px] font-medium" style={ended.tone === 'busy' ? undefined : { color: TONE_VAR[ended.tone] }}
          data-testid="swarm-out-run-ended" data-tone={ended.tone}>{ended.text}</div>
        {judge && <JudgeTag judge={judge} title={r.title} />}
      </div>
      {work.map((w) => <div key={w} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-work">{w}</div>)}
      {[checks, gateLine(res), lessonsLine(res)].filter(Boolean).map((l) => (
        <div key={l!} className="text-[12px] mt-0.5 break-words" data-testid="swarm-out-run-checks"
          style={l === checks && checksIncomplete(res) ? { color: TONE_VAR.attention } : undefined}>{l}</div>
      ))}
      <Stats stats={runStats(res)} />
      <Stats stats={tokenStats(res)} testId="swarm-out-run-tokens" min={72} />
      {r.result_error && <div className={`text-[11.5px] mt-0.5 ${muted}`}>{t('swarmOut.result.lastCheck', { reason: resultErrorLabel(r.result_error) })}</div>}
    </div>
  );
}

/** A run's title: a button that opens its Hive session when the run has one this Host
 *  knows (design docs/design/2026-10-29-swarm-out-hive-session-tab.md §4), else text. */
export function RunTitle({ r, className }: { r: SwarmOutRun; className: string }) {
  const { t } = useTranslation();
  const open = useOpenRun(r);
  if (!open) return <span className={className} title={r.title}>{r.title}</span>;
  return (
    <button type="button" className={`${className} text-left hover:underline cursor-pointer`}
      title={t('swarmOut.hiveTab.open', { title: r.title })} data-testid="swarm-out-run-open"
      onClick={open}>{r.title}</button>
  );
}

/** The card's explicit way into the run's Hive session (the title is a button too, but
 *  nothing about a title says it can be clicked). */
function OpenRunButton({ r, open }: { r: SwarmOutRun; open: () => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" className={`${btn} inline-flex items-center gap-1`} onClick={open}
      title={t('swarmOut.hiveTab.open', { title: r.title })} data-testid="swarm-out-run-open-button">
      <span className="material-symbols-outlined text-[15px]" aria-hidden>open_in_new</span>
      {t('swarmOut.card.open')}
    </button>
  );
}

export function RunCard({ r, onReload }: { r: SwarmOutRun; onReload?: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<SwarmOutResult | null>(null);
  const canOpen = !!r.result || r.result_status === 'failed';
  const openRun = useOpenRun(r);
  const tone = runTone(r);
  return (
    // only a failed run, or one waiting on the person, tints its border: a stopped run keeps
    // its amber dot and word but does not need anything, so it does not compete with them
    // open, a card takes the whole row so the reply has room to be read
    <div className={`${card} px-3.5 py-3 min-w-0`}
      style={{ ...(tone === 'error' || r.state === 'waiting' ? cardTint(tone) : {}), ...(open ? { gridColumn: '1 / -1' } : {}) }}
      data-testid="swarm-out-run" data-state={r.state ?? 'unknown'} data-tone={tone} data-open={open || undefined}>
      <div className="flex items-start gap-2 min-w-0">
        <span className="h-[18px] flex items-center"><Dot tone={tone} className={tone === 'busy' ? 'swarm-out-icon--busy' : ''} /></span>
        <RunTitle r={r} className="font-medium min-w-0 flex-1 line-clamp-2 leading-[18px]" />
        <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.sent_at)}</span>
      </div>
      <div className={`text-[11.5px] mt-0.5 truncate ${muted}`}>
        {r.hive} · <span data-testid={r.session_unknown ? 'swarm-out-run-unknown' : 'swarm-out-run-state'}>{stateLabel(r)}</span>
      </div>
      <AgentFacts r={r} />
      <RunResult r={r} full={full} />
      {(canOpen || openRun) && <div className="mt-2 flex items-center gap-3 min-w-0">
        {canOpen && (
          <button type="button" className={`inline-flex items-center gap-0.5 text-[12px] ${muted} hover:text-[var(--color-text)]`} aria-expanded={open}
            onClick={() => setOpen((o) => !o)} data-testid="swarm-out-run-toggle">
            {open ? t('swarmOut.card.collapse') : t('swarmOut.card.details')}
            <span className="material-symbols-outlined text-[16px]" aria-hidden>{open ? 'expand_less' : 'expand_more'}</span>
          </button>
        )}
        {openRun && <span className="ml-auto"><OpenRunButton r={r} open={openRun} /></span>}
      </div>}
      {open && <ResultDetail r={r} full={full} setFull={setFull} onRefreshed={onReload ?? (() => {})} onClose={() => setOpen(false)} />}
    </div>
  );
}

const runGrid = { gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 300px), 1fr))' };

function RunGroup({ title, rows, empty, onReload }: { title: string; rows: SwarmOutRun[]; empty: string; onReload?: () => void }) {
  return (
    <section className="mt-5" data-testid="swarm-out-run-group" data-group={title}>
      <div className="flex items-baseline gap-2 mb-2">
        <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
        {rows.length > 0 && <span className="text-[12px] text-[var(--color-text-dim)] tabular-nums">{rows.length}</span>}
      </div>
      {rows.length === 0 ? <div className={`text-[12.5px] ${muted}`}>{empty}</div> : (
        <div className="grid gap-3" style={runGrid}>
          {rows.map((r) => <RunCard key={r.dispatch_id} r={r} onReload={onReload} />)}
        </div>
      )}
    </section>
  );
}

function OpenHive({ onOpenHive }: { onOpenHive?: () => void }) {
  const { t } = useTranslation();
  return onOpenHive ? <button type="button" className={`ml-2 ${btn}`} onClick={onOpenHive} data-testid="swarm-out-open-hive-admin">{t('swarmOut.live.openHiveAdmin')}</button> : null;
}

export function LiveDispatches({ hidden = false, fleet = null, fleetFailed = false, onOpenHive, now }: {
  hidden?: boolean; fleet?: SwarmOutFleet | null; fleetFailed?: boolean; onOpenHive?: () => void; now?: number;
}) {
  const { t } = useTranslation();
  const { data, failed, reload } = useRuns(hidden);
  const at = now ?? Date.now();
  const asks = data?.asks ?? [];
  const open = asks.filter((a) => !a.refused);
  const refused = asks.filter((a) => a.refused);
  const n = panelNumbers(data, fleet);
  const groups = runGroups(data?.runs ?? [], at, asks);
  const noRuns = !!data && data.runs.length === 0;
  const dash = '—';
  return (
    <div data-testid="swarm-out-live">
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      {(data?.errors ?? []).map((e) => <div key={e} className="text-[12px] mb-1" style={{ color: TONE_VAR.attention }}>{errorLabel(e)}</div>)}
      <Fleet fleet={fleet} failed={fleetFailed} numbers={<>
        <Num testId="kpi-waiting" label={t('swarmOut.kpi.waiting')} tone={n.waiting || n.onHivePage ? 'attention' : undefined}
             value={<>{n.waiting ?? dash}{n.onHivePage ? <span className="text-[12.5px] font-medium ml-2" data-testid="kpi-on-hive-page">{t('swarmOut.kpi.onHivePage', { count: n.onHivePage })}</span> : null}</>} />
        {/* free slots: green while there is room, amber when every running Hive is full */}
        <Num testId="kpi-free" label={t('swarmOut.kpi.free')} tone={n.capacity ? (n.capacity.free > 0 ? 'ok' : 'attention') : undefined}
             value={n.capacity ? <>{n.capacity.free}<span className={`text-[12.5px] font-medium ml-1 ${muted}`}>/ {n.capacity.total}</span></> : dash} />
        <Num testId="kpi-running" label={t('swarmOut.kpi.running')} tone={n.running ? 'busy' : undefined} value={n.running ?? dash} />
      </>} />

      <section className="mt-5" data-testid="swarm-out-waiting">
        <div className="flex items-baseline gap-2 mb-2">
          <h3 className="m-0 text-[13px] font-semibold">{t('swarmOut.waiting.title')}</h3>
          <button type="button" className={`ml-auto ${btn}`} onClick={reload}>{t('swarmOut.waiting.refresh')}</button>
        </div>
        {data && open.length === 0 && groups.waitingUnasked.length === 0 && <div className={`text-[12.5px] ${muted}`}>{t('swarmOut.waiting.none')}</div>}
        <div className="flex flex-col gap-2">
          {open.map((a) => <AskCard key={`${a.hive_id}/${a.request_id}`} ask={a} onDone={reload} />)}
        </div>
        {groups.waitingUnasked.length > 0 && (
          <div className="mt-2" data-testid="swarm-out-waiting-unasked">
            <div className={`text-[12px] mb-1.5 ${muted}`}>{t('swarmOut.waiting.unasked')}<OpenHive onOpenHive={onOpenHive} /></div>
            <div className="grid gap-3" style={runGrid}>
              {groups.waitingUnasked.map((r) => <RunCard key={r.dispatch_id} r={r} />)}
            </div>
          </div>
        )}
        {refused.length > 0 && (
          <div className="mt-3" data-testid="swarm-out-refused-group">
            <div className={`text-[12px] mb-1.5 ${muted}`}>{t('swarmOut.waiting.refused')}<OpenHive onOpenHive={onOpenHive} /></div>
            <div className="flex flex-col gap-2">
              {refused.map((a) => <AskCard key={`${a.hive_id}/${a.request_id}`} ask={a} onDone={reload} />)}
            </div>
          </div>
        )}
      </section>

      {noRuns ? (
        <div className={`mt-5 text-[12px] ${muted}`}>{t('swarmOut.noRuns')}</div>
      ) : (
        <>
          <RunGroup title={t('swarmOut.groups.running')} rows={groups.running} empty={t('swarmOut.groups.runningEmpty')} />
          <RunGroup title={t('swarmOut.groups.stopped')} rows={groups.stopped} empty={t('swarmOut.groups.stoppedEmpty')} onReload={reload} />
          {groups.unknown.length > 0 && <RunGroup title={t('swarmOut.groups.unknown')} rows={groups.unknown} empty="" />}
        </>
      )}
      <Tbd title={t('swarmOut.tbdParts.scheduled')} missing={t('swarmOut.tbdParts.scheduledMissing')} />
    </div>
  );
}
