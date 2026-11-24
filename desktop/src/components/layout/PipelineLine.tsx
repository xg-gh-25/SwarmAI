/**
 * PipelineLine — the Pipelines overlay's Runs tab (design: Knowledge/Mockups/
 * 2026-11-24-pipeline-overlay-redesign.html), in the Swarm Out run board's look:
 *
 * - Running: every run in flight as a card, the ones waiting on you first (amber, what
 *   they ask on the card, a button that opens the big card where the commands are), then
 *   the stopped ones (red, dashed), then those the dispatching agent judges and those
 *   working. A card: title, start · minutes, Brain · profile and where it runs (this
 *   machine, or EC2 / AgentCore with the Hive's name), and its stations: the eight
 *   pipeline stages (a stage its profile skips struck through, ↺N where a stage ran N
 *   times), plus RETURN · LAND · JUDGE for a Hive run. A Hive run on its Hive stands at
 *   the stage this host last read (backend core/swarm_out_live); until one is read no
 *   station is marked: no stage is guessed. A work package of a local Swarm Run sits
 *   under its lead.
 * - Recently done: the finished runs of the overlay's 30 days, newest first, a row each
 *   (✓ landed, – discarded, ! not landed or failed), start · minutes and cost.
 * - The median minutes per stage, over the runs that recorded stage times (working and
 *   waiting on you, the extra rounds), shown only when some run recorded them.
 *
 * Pure presentation over ``pipelineRows`` (sections, inFlight, waitingCounts, timeGoes); the rows'
 * order and states are the backend's.
 *
 * Exports: PipelineLine, WherePill
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TONE_VAR } from '../swarm-out/tone';
import { WhereTag } from '../swarm-out/RunBoard';
import { useOpenRun } from '../swarm-out/openSession';
import { minutesText, stamp } from '../swarm-out/where';
import type { ChipKind } from '../swarm-out/track';
import {
  autonomyOf, inFlight, PIPELINE_STATIONS, RETURN_STATIONS, sections, timeGoes, waitingCounts,
  type PipelineRow, type Station, type Track,
} from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const DONE_FIRST = 8;
const DONE_PAGE = 20;
const JUDGE = 'var(--line-judge)';

/** A card's colour, one meaning per kind (../swarm-out/tone; light purple: the agent judges). */
const KIND_COLOR: Record<ChipKind, string> = {
  run: TONE_VAR.busy, you: TONE_VAR.attention, stop: TONE_VAR.error, judge: JUDGE, done: 'var(--color-text-dim)',
};

/** Where a run is: a neutral "this machine" tag, or EC2 / AgentCore and the Hive's name. */
export function WherePill({ r }: { r: PipelineRow }) {
  const { t } = useTranslation();
  if (r.where === 'hive' && r.hive_run) return <span data-testid="pipeline-row-where"><WhereTag r={r.hive_run} fleet={null} /></span>;
  return (
    <span className="px-1.5 rounded text-[10.5px] leading-[15px] font-semibold whitespace-nowrap text-[var(--color-text-secondary)] bg-[var(--color-bg-chrome)]"
      style={{ boxShadow: 'inset 0 0 0 1px var(--color-border)' }} data-testid="pipeline-row-where">
      {r.where === 'local' ? t('ui.PipelineOverlay.local') : r.hive ?? 'Hive'}
    </span>
  );
}

const stationLabel = (s: Station, t: (k: string) => string) =>
  s === 'return' ? t('ui.PipelineOverlay.fl_return') : s === 'land' ? t('ui.PipelineOverlay.fl_land')
    : s === 'judge' ? t('ui.PipelineOverlay.fl_judge') : s.toUpperCase();

/** Brain · profile, then where it runs. */
function Meta({ r }: { r: PipelineRow }) {
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 text-small min-w-0 ${muted}`}>
      <span className="truncate"><b className="font-semibold text-[var(--color-text)]">{r.brain}</b>{r.profile ? ` · ${r.profile}` : ''}</span>
      <WherePill r={r} />
    </div>
  );
}

/** "11/24 08:41 · 38 min": when it started and how long it ran (so far). */
function When({ r, now, stacked = false }: { r: PipelineRow; now: number; stacked?: boolean }) {
  const start = Date.parse(r.createdAt);
  const min = r.durationMin != null ? Math.round(r.durationMin)
    : r.endedAt == null && !Number.isNaN(start) ? Math.max(0, Math.round((now - start) / 60000)) : null;
  return (
    <span className={`inline-flex ${stacked ? 'flex-col items-end' : 'items-baseline gap-1.5'} text-small whitespace-nowrap tabular-nums ${muted}`}
      data-testid="pipeline-when">
      <span>{stamp(r.createdAt)}</span>
      {!stacked && <span className={dim}>·</span>}
      <b className="font-semibold text-[var(--color-text-secondary)]">{minutesText(min)}</b>
    </span>
  );
}

/** The run's stations: passed filled, the current one ringed, a flowing segment after it. */
function Stations({ tr }: { tr: Track }) {
  const { t } = useTranslation();
  const r = tr.row;
  const hive = r.where === 'hive';
  const all: Station[] = hive ? [...PIPELINE_STATIONS, ...RETURN_STATIONS] : [...PIPELINE_STATIONS];
  // back here (RETURN · LAND · JUDGE): it went through the pipeline, every stage of it passed
  const atReturn = !!tr.at && (RETURN_STATIONS as readonly string[]).includes(tr.at);
  const idx = tr.at ? all.indexOf(tr.at) : -1;
  // a stage its profile does not run, from the profile's own stages (only when they were read)
  const profile = new Set((r.stages ?? []).map((x) => x.name));
  const skipped = (s: Station) => profile.size > 0 && (PIPELINE_STATIONS as readonly string[]).includes(s) && !profile.has(s);
  const c = KIND_COLOR[tr.kind];
  // what it asks is said once, in the card's pill; a stopped run has no pill, so its station says it
  const sub = tr.kind === 'stop' ? t('ui.PipelineOverlay.fl_stopped') : null;
  return (
    <ol className="grid mt-3.5 m-0 p-0 list-none" data-testid={`pipeline-track-${r.key}`} data-kind={tr.kind}
      style={{ gridTemplateColumns: hive ? `repeat(${PIPELINE_STATIONS.length}, minmax(0,1fr)) 10px repeat(${RETURN_STATIONS.length}, minmax(0,.8fr))`
        : `repeat(${PIPELINE_STATIONS.length}, minmax(0,1fr))` }}>
      {all.map((s, i) => {
        const here = i === idx;
        const skip = !here && !atReturn && skipped(s);
        const passed = !skip && idx >= 0 && i < idx;
        const gap = hive && s === RETURN_STATIONS[0];
        const back = (RETURN_STATIONS as readonly string[]).includes(s);
        const tone = here ? c : back ? JUDGE : TONE_VAR.busy;
        return (
          <li key={s} className={`relative pt-5 min-w-0 ${skip ? 'opacity-40' : ''}`} data-station={s}
            data-state={here ? 'here' : skip ? 'skipped' : passed ? 'passed' : 'ahead'} aria-current={here ? 'step' : undefined}
            style={gap ? { gridColumnStart: PIPELINE_STATIONS.length + 2 } : undefined}>
            {i < all.length - 1 && (
              <span aria-hidden className={`absolute top-[5px] left-[14px] right-[2px] h-[2px] rounded-sm ${here && (tr.kind === 'run' || tr.kind === 'judge') ? 'so-flow' : ''}`}
                style={{ ['--so-c' as string]: c, background: idx >= 0 && i < idx ? `color-mix(in srgb, ${tone} 45%, transparent)` : 'var(--color-border)' }} />
            )}
            <span aria-hidden className={`absolute top-0 left-0 w-3 h-3 rounded-full ${here ? 'so-node-here' : ''}`}
              style={here ? { ['--so-c' as string]: c, background: c, boxShadow: `0 0 0 4px color-mix(in srgb, ${c} 20%, transparent)` }
                : passed ? { background: `color-mix(in srgb, ${tone} 55%, transparent)` }
                  : { background: 'var(--color-bg)', boxShadow: 'inset 0 0 0 2px var(--color-border)' }} />
            {tr.rounds[s] && (
              <span className="absolute -top-[3px] left-4 text-[10px] leading-[13px] font-bold tabular-nums" style={{ color: TONE_VAR.attention }}
                data-testid="pipeline-track-rounds"><span aria-hidden>↺{tr.rounds[s]}</span><span className="sr-only">{t('ui.PipelineOverlay.ln_ranTimes', { count: tr.rounds[s] })}</span></span>
            )}
            <div className={`text-meta truncate tracking-wide ${here ? 'font-semibold' : passed ? muted : dim} ${skip ? 'line-through' : ''}`}
              style={here ? { color: c } : back && !passed ? { color: `color-mix(in srgb, ${JUDGE} 70%, var(--color-text-dim))` } : undefined}>
              {stationLabel(s, t)}
              <span className="sr-only"> · {skip ? t('ui.PipelineOverlay.ln_skipped') : t(here ? 'swarmOut.board.stationHere' : passed ? 'swarmOut.board.stationPassed' : 'swarmOut.board.stationAhead')}</span>
            </div>
            {here && sub && <div className="text-meta truncate text-[var(--color-text-secondary)]" data-testid="pipeline-station-sub">{sub}</div>}
          </li>
        );
      })}
    </ol>
  );
}

function OpenButton({ go }: { go: () => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" className="btn-sm btn-ghost whitespace-nowrap" data-testid="pipeline-row-open"
      onClick={(e) => { e.stopPropagation(); go(); }}>{t('swarmOut.board.openSession')} ↗</button>
  );
}

function HiveOpen({ r }: { r: NonNullable<PipelineRow['hive_run']> }) {
  const open = useOpenRun(r);
  return open ? <OpenButton go={open} /> : null;
}

/** Open the run's session: its owner session here, or the Hive's (Swarm Out's rule). */
function OpenSession({ r, onOpenSession }: { r: PipelineRow; onOpenSession: (r: PipelineRow) => void }) {
  if (r.where === 'hive') return r.hive_run ? <HiveOpen r={r.hive_run} /> : null;
  return r.ownerSessionId ? <OpenButton go={() => onOpenSession(r)} /> : null;
}

/** One run in flight. A click anywhere opens its big card. */
function RunCard({ tr, now, onOpen, onOpenSession, child }: {
  tr: Track; now: number; onOpen: (key: string) => void; onOpenSession: (r: PipelineRow) => void; child?: boolean;
}) {
  const { t } = useTranslation();
  const r = tr.row;
  const you = tr.kind === 'you';
  const stop = tr.kind === 'stop';
  const c = KIND_COLOR[tr.kind];
  const live = r.where === 'hive' && r.hive_run?.state === 'running' ? r.hive_run.live_now : null;
  const ask = you || stop;
  const reason = r.local?.checkpointReason || null;
  return (
    <div onClick={() => onOpen(r.key)} data-testid={`pipeline-run-${r.key}`} data-stage={r.stage} data-where={r.where} data-kind={tr.kind}
      className={`so-card so-card--click relative overflow-hidden rounded-xl border bg-[var(--color-card)] px-4 py-3.5 cursor-pointer min-w-0 ${you ? 'so-card--you' : ''} ${child ? 'ml-6' : ''}`}
      style={{ borderColor: you ? `color-mix(in srgb, ${TONE_VAR.attention} 55%, var(--color-border))` : stop ? `color-mix(in srgb, ${TONE_VAR.error} 60%, var(--color-border))` : 'var(--color-border)',
        borderStyle: stop ? 'dashed' : 'solid' }}>
      <div className="flex items-center gap-2.5 min-w-0">
        <span aria-hidden className={`w-2 h-2 rounded-full flex-none ${stop ? '' : 'so-live'}`} style={{ background: c }} />
        <button type="button" className="flex-1 min-w-0 truncate text-left text-heading font-semibold hover:underline" title={r.title}
          aria-haspopup="dialog" onClick={(e) => { e.stopPropagation(); onOpen(r.key); }}>{r.wp ? `${r.wp} · ${r.title}` : r.title}</button>
        {you && <span className="so-pop text-meta font-semibold px-2 py-0.5 rounded-full whitespace-nowrap" style={{ background: TONE_VAR.attention, color: 'var(--color-bg)' }}
          data-testid="pipeline-row-tag">{r.task ? t(`ui.PipelineOverlay.task_${r.task}`) : t('ui.PipelineOverlay.fl_waitsOnYou')}</span>}
        {tr.kind === 'judge' && <span className="text-meta font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
          style={{ color: JUDGE, background: `color-mix(in srgb, ${JUDGE} 13%, transparent)` }}>{t('ui.PipelineOverlay.fl_agentJudges')}</span>}
        <When r={r} now={now} />
      </div>
      <div className="mt-1.5 flex items-center gap-2 min-w-0">
        <div className="flex-1 min-w-0"><Meta r={r} /></div>
        {!ask && <div className="so-hover flex-none"><OpenSession r={r} onOpenSession={onOpenSession} /></div>}
      </div>
      <Stations tr={tr} />
      {ask ? (
        <div className="mt-3 flex items-center gap-2 px-3 py-2.5 rounded-lg text-body" data-testid="pipeline-card-ask"
          style={{ background: `color-mix(in srgb, ${stop ? TONE_VAR.error : TONE_VAR.attention} 8%, transparent)` }}>
          {/* why it waits or stopped, when the run recorded it; the pill already says what it asks */}
          <span className="flex-1 min-w-0 truncate text-[var(--color-text-secondary)]" title={reason ?? undefined}>
            {reason ?? (r.task ? t(`ui.PipelineOverlay.task_${r.task}`) : stop ? t('ui.PipelineOverlay.fl_stopped') : t('ui.PipelineOverlay.fl_waitsOnYou'))}</span>
          <button type="button" className="btn-sm font-semibold" style={{ background: stop ? TONE_VAR.error : TONE_VAR.attention, color: 'var(--color-bg)' }}
            onClick={(e) => { e.stopPropagation(); onOpen(r.key); }}>{t('swarmOut.board.openCard')}</button>
          <OpenSession r={r} onOpenSession={onOpenSession} />
        </div>
      ) : live && (live.tool || live.text) ? (
        <div className="mt-3 pt-2.5 border-t border-dashed border-[var(--color-border)] flex gap-2 items-baseline font-mono text-meta min-w-0 text-[var(--color-text-muted)]"
          data-testid="pipeline-now" title={live.object ?? live.text ?? undefined}>
          {live.tool && <b className="font-semibold text-[var(--color-text-secondary)] min-w-[36px]">{live.tool}</b>}
          <span className="truncate">{live.tool ? live.object ?? '' : live.text}<i className="so-caret" aria-hidden /></span>
        </div>
      ) : null}
    </div>
  );
}

/** One finished run: ✓ landed, – discarded, ! not landed or failed. */
function DoneRow({ r, now, onOpen, compact }: { r: PipelineRow; now: number; onOpen: (key: string) => void; compact: boolean }) {
  const { t } = useTranslation();
  const ok = r.outcome === 'landed' || r.outcome === 'manual';
  const off = r.outcome === 'discarded' || r.outcome === 'other';
  const mark = ok ? { c: TONE_VAR.ok, s: '✓' } : off ? { c: 'var(--color-text-dim)', s: '–' } : { c: TONE_VAR.attention, s: '!' };
  const word = autonomyOf(r);
  const rounds = (r.stages ?? []).reduce((n, s) => n + Math.max(0, (s.attempts ?? 1) - 1), 0);
  const cost = r.costUsd == null || !Number.isFinite(r.costUsd) ? null : `$${r.costUsd < 10 ? r.costUsd.toFixed(2) : r.costUsd.toFixed(1)}`;
  return (
    <div role="button" tabIndex={0} aria-haspopup="dialog" onClick={() => onOpen(r.key)}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(r.key); } }}
      className="grid grid-cols-[18px_minmax(0,1fr)_auto] gap-x-3 items-center px-2.5 py-2 -mx-2.5 rounded-lg cursor-pointer hover:bg-[var(--color-hover)]"
      data-testid={`pipeline-run-${r.key}`} data-stage={r.stage} data-where={r.where} data-outcome={r.outcome}>
      <span aria-hidden className="w-[18px] h-[18px] rounded-full grid place-items-center text-meta font-semibold"
        style={{ color: mark.c, background: `color-mix(in srgb, ${mark.c} 12%, transparent)` }}>{mark.s}</span>
      <span className="sr-only">{t(`ui.PipelineOverlay.outcome_${r.outcome}`)}</span>
      <div className="min-w-0">
        <div className={`text-body truncate ${off ? muted : ''}`} title={r.title}>
          {r.title}
          {!ok && <span className="text-small" style={off ? undefined : { color: TONE_VAR.attention }}>
            <span className={off ? dim : ''}> · {t(`ui.PipelineOverlay.outcome_${r.outcome}`)}</span></span>}
          {word && !word.full && <span className="text-small" style={{ color: TONE_VAR.attention }}> · {t('swarmOut.review.autonomy.steppedIn', { count: word.n })}</span>}
          {rounds > 0 && <span className={`text-small ${dim}`}> · ↺{rounds}</span>}
        </div>
        <div className="mt-px"><Meta r={r} /></div>
      </div>
      <div className="flex flex-col items-end">
        <When r={r} now={now} stacked={compact} />
        {cost && <span className={`text-meta tabular-nums ${dim}`} data-testid="pipeline-cost">{cost}</span>}
      </div>
    </div>
  );
}

function SectionHead({ title, count, right, testId }: { title: string; count: number; right?: React.ReactNode; testId: string }) {
  return (
    <div className="flex items-baseline gap-2 mb-2.5 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]" data-testid={testId}>
      <span>{title}</span>
      <span className="inline-block min-w-[18px] px-1.5 rounded-full bg-[var(--color-bg-chrome)] text-center tabular-nums text-[var(--color-text-secondary)]">{count}</span>
      {right && <span className="ml-auto font-normal tracking-normal">{right}</span>}
    </div>
  );
}

export function PipelineLine({ rows, now, onOpen, onOpenSession, compact = false }: {
  rows: PipelineRow[]; now: number; onOpen: (key: string) => void;
  /** open a local run's owner session */
  onOpenSession: (r: PipelineRow) => void;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [doneShown, setDoneShown] = useState(DONE_FIRST);
  const s = useMemo(() => sections(rows), [rows]);
  const time = useMemo(() => timeGoes(rows), [rows]);
  // every run in flight, the most urgent first (pipelineRows.inFlight), a Swarm Run's packages under it
  const items = useMemo(() => inFlight(rows), [rows]);
  const all = items.flatMap((i) => [i.track, ...i.children]);
  // the same count as the tab's label (waitingCounts)
  const { waiting: nYou, stopped: nStop } = useMemo(() => waitingCounts(rows), [rows]);
  const totals = PIPELINE_STATIONS.map((st) => (time.stations[st].agentMin ?? 0) + (time.stations[st].waitMin ?? 0));
  const max = Math.max(...totals, 0);

  return (
    <div data-testid="pipeline-line">
      <section data-testid="pipeline-running">
        <SectionHead title={t('ui.PipelineOverlay.ln_running')} count={all.length} testId="pipeline-section-running"
          right={(nYou > 0 || nStop > 0) && <>
            {nYou > 0 && <span style={{ color: TONE_VAR.attention }}>{t('ui.PipelineOverlay.ln_youCount', { count: nYou })}</span>}
            {nYou > 0 && nStop > 0 && <span className={dim}> · </span>}
            {nStop > 0 && <span style={{ color: TONE_VAR.error }}>{t('ui.PipelineOverlay.ln_stopCount', { count: nStop })}</span>}
          </>} />
        {items.length === 0 ? (
          <div className={`flex items-center gap-3 px-4 py-4 rounded-xl border border-dashed border-[var(--color-border)] text-body ${muted}`}
            data-testid="pipeline-line-empty"><span aria-hidden className={dim}>◇</span>{t('ui.PipelineOverlay.ln_none')}</div>
        ) : (
          <div className="flex flex-col gap-2.5">
            {items.map((it) => (
              <div key={it.track.row.key} className="flex flex-col gap-2.5">
                <RunCard tr={it.track} now={now} onOpen={onOpen} onOpenSession={onOpenSession} />
                {it.children.map((c) => <RunCard key={c.row.key} tr={c} now={now} onOpen={onOpen} onOpenSession={onOpenSession} child />)}
              </div>
            ))}
          </div>
        )}
      </section>

      {s.done.length > 0 && <section className="mt-8" data-testid="pipeline-done">
        <SectionHead title={t('ui.PipelineOverlay.ln_done')} count={s.done.length} testId="pipeline-section-done"
          right={<span className={dim}>{t('ui.PipelineOverlay.ln_doneWindow')}</span>} />
        <div className="flex flex-col divide-y divide-[var(--color-border)]">
          {s.done.slice(0, doneShown).map((r) => <DoneRow key={r.key} r={r} now={now} onOpen={onOpen} compact={compact} />)}
        </div>
        {s.done.length > doneShown && (
          <button type="button" className="mt-2 text-small text-[var(--color-primary)] hover:underline" data-testid="pipeline-done-more"
            onClick={() => setDoneShown((n) => n + DONE_PAGE)}>
            {t('ui.PipelineOverlay.moreDone', { count: Math.min(DONE_PAGE, s.done.length - doneShown) })} ›</button>
        )}
      </section>}

      {time.covered > 0 && max > 0 && (
        <section className="mt-8 so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-[18px] py-4" data-testid="pipeline-time">
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="text-body font-semibold">{t('ui.PipelineOverlay.ln_timeTitle')}</span>
            <span className={`ml-auto text-small ${muted}`}>{t('ui.PipelineOverlay.ln_timeRuns', { count: time.covered })}</span>
          </div>
          <div className="grid items-end gap-x-2.5 h-[150px]" style={{ gridTemplateColumns: `repeat(${PIPELINE_STATIONS.length}, minmax(0,1fr))` }}>
            {PIPELINE_STATIONS.map((st, i) => {
              const x = time.stations[st];
              const a = x.agentMin ?? 0, w = x.waitMin ?? 0, total = totals[i];
              const top = total === max;
              return (
                <div key={st} className="flex flex-col items-center justify-end h-full" data-testid={`pipeline-time-${st}`}
                  title={t('ui.PipelineOverlay.ln_barTip', { work: Math.round(a), wait: Math.round(w) })}>
                  <span className="sr-only">{st.toUpperCase()}</span>
                  <span className="text-meta font-semibold tabular-nums min-h-[15px]" style={{ color: TONE_VAR.attention }}>{x.rounds > 0 ? `↺${x.rounds}` : ''}</span>
                  <span className="text-small font-semibold tabular-nums text-[var(--color-text)]">{x.runs ? minutesText(total < 1 ? 0 : Math.round(total)) : '—'}</span>
                  <span className="w-[26px] flex flex-col-reverse rounded-t-[5px] rounded-b-[2px] overflow-hidden mt-0.5" aria-hidden>
                    <span style={{ height: Math.max(total && !w ? 3 : 0, (a / max) * 100), background: top ? TONE_VAR.busy : `color-mix(in srgb, ${TONE_VAR.busy} 55%, transparent)` }} />
                    {w > 0 && <span style={{ height: (w / max) * 100, background: TONE_VAR.attention }} />}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="grid gap-x-2.5 mt-1.5 pt-1.5 border-t border-[var(--color-border)]" style={{ gridTemplateColumns: `repeat(${PIPELINE_STATIONS.length}, minmax(0,1fr))` }}>
            {PIPELINE_STATIONS.map((st) => <span key={st} aria-hidden className={`text-meta font-semibold tracking-wide text-center truncate ${muted}`}>{st.toUpperCase()}</span>)}
          </div>
        </section>
      )}
    </div>
  );
}
