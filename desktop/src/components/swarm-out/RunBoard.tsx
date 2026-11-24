/**
 * RunBoard — the run view of the Swarm Out panel (design: Knowledge/Mockups/
 * 2026-11-23-swarm-out-dashboard-redesign-v6.html, the Run view).
 *
 * Two parts, nothing else:
 *  - Running: every run not settled yet, what waits on the person first (runReview.sections:
 *    you, then running). There is no separate "needs you" list: a run that needs the
 *    person says so on its own card (an amber border and pill, the station it waits at,
 *    and the question itself to answer there, or "Open card" for what is decided on the
 *    run's big card). A question whose session is none of the listed runs sits at the top.
 *  - Recently done: the runs that settled (or stopped) in the last 72 hours, the latest
 *    started first (the time each row shows); older ones are in the history.
 * Every card and row names its Brain · Project Agent, where it ran (EC2 or AgentCore and
 * the Hive's name: where.runWhere), its start time and how long it took (where.stamp,
 * where.runMinutes). A card shows the run's place on the dispatch track (track.dispatchTrack:
 * five stations, a pipeline task's live stage under "on Hive") and what its agent is doing
 * now (live_now). A click opens the run's big card (RunOverlay), where every command is.
 *
 * Motion (index.css "Swarm Out board"): a live dot's ring, the flowing segment at the
 * current station, a card that needs the person breathing, a card or row that arrives
 * after the first load sliding in. Everything a Hive wrote is plain text.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { swarmOutService, type SwarmOutAsk, type SwarmOutFleet, type SwarmOutRun } from '../../services/swarmOut';
import { openSwarmOutSettings } from '../common/settingsEvents';
import { agentFacts, errorLabel, useRuns, when } from './live';
import { useOpenRun } from './openSession';
import { RunOverlay } from './RunOverlay';
import { actionOf, asksOf, DONE_STAGES, sections, stageLabel, stageOf, taskTag, waitingSince } from './runReview';
import { SwarmOutWaitCard } from './SwarmOutWaitCard';
import { TONE_VAR } from './tone';
import { CHIP_COLOR, DISPATCH_STATIONS, dispatchTrack, type ChipKind } from './track';
import { waitingUnasked } from './useSwarmOutActivity';
import { carrierLabelKey, minutesText, runMinutes, runWhere, stamp } from './where';
import { outcomeOf, personSteps } from './historyStats';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const RECENT_MS = 72 * 3600 * 1000;

/** The ids that appeared after the first answer, for ~1.6 s (the arrival animation). */
function useFresh(ids: string[], loaded: boolean): Set<string> {
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const key = ids.join(',');
  useEffect(() => {
    if (!loaded) return undefined;
    if (seen.current === null) { seen.current = new Set(ids); return undefined; }
    const now = ids.filter((id) => !seen.current!.has(id));
    ids.forEach((id) => seen.current!.add(id));
    if (now.length === 0) { setFresh((f) => (f.size ? new Set() : f)); return undefined; }
    setFresh(new Set(now));
    const timer = window.setTimeout(() => setFresh(new Set()), 1600);
    return () => window.clearTimeout(timer);
    // the ids, joined, are the dependency
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, loaded]);
  return fresh;
}

/** A number that pops when it changes. */
function Count({ n, testId }: { n: number; testId?: string }) {
  const [bump, setBump] = useState(0);
  const prev = useRef(n);
  useEffect(() => { if (prev.current !== n) { prev.current = n; setBump((b) => b + 1); } }, [n]);
  return (
    <span key={bump} data-testid={testId}
      className={`inline-block min-w-[18px] px-1.5 rounded-full bg-[var(--color-bg-chrome)] text-center tabular-nums text-[var(--color-text-secondary)] ${bump ? 'so-bump' : ''}`}>{n}</span>
  );
}

function SectionHead({ title, count, right }: { title: string; count: number; right?: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 mb-2.5 text-meta font-semibold tracking-[0.06em] text-[var(--color-text-muted)]">
      <span>{title}</span><Count n={count} />
      {right && <span className="ml-auto font-normal tracking-normal">{right}</span>}
    </div>
  );
}

/** "EC2", "AgentCore" or "SSH" and the Hive's name. */
export function WhereTag({ r, fleet }: { r: SwarmOutRun; fleet: SwarmOutFleet | null }) {
  const { t } = useTranslation();
  const w = runWhere(r, fleet);
  const ac = w.carrier === 'agentcore';
  return (
    <span className="inline-flex items-center gap-1.5 min-w-0" data-testid="so-where" data-carrier={w.carrier}>
      <span className="px-1.5 rounded text-[10.5px] leading-[15px] font-semibold whitespace-nowrap"
        style={ac
          ? { color: 'var(--color-zone-work)', background: 'color-mix(in srgb, var(--color-zone-work) 12%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--color-zone-work) 30%, transparent)' }
          : { color: 'var(--color-text-secondary)', background: 'var(--color-bg-chrome)', boxShadow: 'inset 0 0 0 1px var(--color-border)' }}>
        {t(carrierLabelKey(w.carrier))}
      </span>
      <span className="truncate text-[var(--color-text-secondary)]">{w.name}</span>
    </span>
  );
}

/** Brain · Project Agent, then where it ran. */
export function RunMeta({ r, fleet }: { r: SwarmOutRun; fleet: SwarmOutFleet | null }) {
  const f = agentFacts(r);
  return (
    <div className={`flex flex-wrap items-center gap-x-3.5 gap-y-1 text-small ${muted} min-w-0`}>
      <span className="min-w-0 truncate" title={f.title} data-testid="so-brain">
        <b className={`font-semibold ${f.none ? muted : 'text-[var(--color-text)]'}`}>{f.brain}</b>{f.none ? '' : ` · ${f.agent}`}
      </span>
      <WhereTag r={r} fleet={fleet} />
    </div>
  );
}

/** "11/23 14:02 · 18 min": when it started and how long it took, together. */
export function StartAndTime({ r, now, open, stacked = false }: { r: SwarmOutRun; now: number; open: boolean; stacked?: boolean }) {
  return (
    <span className={`inline-flex ${stacked ? 'flex-col items-end' : 'items-baseline gap-1.5'} text-small whitespace-nowrap tabular-nums ${muted}`}
      data-testid="so-when">
      <span>{stamp(r.sent_at)}</span>
      {!stacked && <span className={dim}>·</span>}
      <b className="font-semibold text-[var(--color-text-secondary)]">{minutesText(runMinutes(r, now, open))}</b>
    </span>
  );
}

/** The five stations, the passed ones filled, the current one ringed with a flowing
 *  segment after it; a pipeline task names its live stage under "on Hive". */
function Track({ r, kind, at }: { r: SwarmOutRun; kind: ChipKind; at: typeof DISPATCH_STATIONS[number] }) {
  const { t } = useTranslation();
  const idx = DISPATCH_STATIONS.indexOf(at);
  const c = CHIP_COLOR[kind];
  const live = r.state === 'running' ? r.live_stage : null;
  const tag = taskTag(r);
  const subOf = (i: number): string | null => {
    if (i !== idx) return null;
    if (kind === 'you') return tag?.text ?? t('swarmOut.board.waitsYou');
    if (kind === 'judge') return t('swarmOut.board.agentAt');
    if (at === 'on_hive') {
      if (live?.stage) {
        const n = live.stages.indexOf(live.stage);
        return n >= 0 && live.stages.length ? `${live.stage.toUpperCase()} ${n + 1}/${live.stages.length}` : live.stage.toUpperCase();
      }
      return r.project_agent?.execution ?? null;
    }
    return null;
  };
  return (
    <ol className="grid grid-cols-5 mt-3.5 m-0 p-0 list-none" style={{ ['--so-c' as string]: c }} data-testid="so-track" data-at={at} data-kind={kind}>
      {DISPATCH_STATIONS.map((s, i) => {
        const passed = i < idx;
        const here = i === idx;
        const sub = subOf(i);
        return (
          <li key={s} className="relative pt-5 min-w-0" data-testid="so-station" data-state={here ? 'here' : passed ? 'passed' : 'ahead'}
            aria-current={here ? 'step' : undefined}>
            {i < DISPATCH_STATIONS.length - 1 && (
              <span aria-hidden className={`absolute top-[5px] left-[14px] right-[2px] h-[2px] rounded-sm ${here ? 'so-flow' : ''}`}
                style={{ background: passed ? `color-mix(in srgb, ${c} 45%, transparent)` : 'var(--color-border)' }} />
            )}
            <span aria-hidden className={`absolute top-0 left-0 w-3 h-3 rounded-full ${here ? 'so-node-here' : ''}`}
              style={here ? { background: c, boxShadow: `0 0 0 4px color-mix(in srgb, ${c} 20%, transparent)` }
                : passed ? { background: `color-mix(in srgb, ${c} 55%, transparent)` }
                  : { background: 'var(--color-bg)', boxShadow: 'inset 0 0 0 2px var(--color-border)' }} />
            <div className={`text-meta truncate ${here ? 'font-semibold' : passed ? muted : dim}`} style={here ? { color: c } : undefined}>
              {t(`swarmOut.track.${s}`)}<span className="sr-only"> · {t(here ? 'swarmOut.board.stationHere' : passed ? 'swarmOut.board.stationPassed' : 'swarmOut.board.stationAhead')}</span>
            </div>
            {sub && <div className="text-meta truncate tabular-nums text-[var(--color-text-secondary)]" data-testid="so-station-sub">{sub}</div>}
          </li>
        );
      })}
    </ol>
  );
}

function StopButton({ r, onDone }: { r: SwarmOutRun; onDone: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <button type="button" disabled={busy} data-testid="so-stop"
      className="btn-sm border border-transparent hover:border-[var(--color-border)] disabled:opacity-50"
      style={{ color: TONE_VAR.error }}
      title={failed ? t('swarmOut.review.stopFailed') : t('swarmOut.review.cmd.stop.does')}
      onClick={(e) => {
        e.stopPropagation();
        setBusy(true); setFailed(false);
        swarmOutService.stop(r.dispatch_id).then(onDone).catch(() => setFailed(true)).finally(() => setBusy(false));
      }}>■ {t('swarmOut.review.cmd.stop.name')}</button>
  );
}

function OpenSessionButton({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  const open = useOpenRun(r);
  if (!open || !actionOf(r, 'open_session')) return null;
  return (
    <button type="button" data-testid="so-open-session"
      className="btn-sm border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-hover)]"
      title={t('swarmOut.hiveTab.open', { title: r.title })}
      onClick={(e) => { e.stopPropagation(); open(); }}>{t('swarmOut.board.openSession')} ↗</button>
  );
}

/** One run that is not settled. */
function RunningCard({ r, asks, fleet, now, fresh, needsYou, onOpen, onReload }: {
  r: SwarmOutRun; asks: SwarmOutAsk[]; fleet: SwarmOutFleet | null; now: number; fresh: boolean;
  /** the run is in "waiting for you" (runReview.sections) */
  needsYou: boolean;
  onOpen: () => void; onReload: () => void;
}) {
  const { t } = useTranslation();
  const { at, kind } = dispatchTrack(r);
  const you = kind === 'you' || needsYou;
  const tag = taskTag(r);
  const own = you ? asksOf(r, asks) : [];
  const nowLine = r.state === 'running' ? r.live_now : null;
  const dot = you ? TONE_VAR.attention : kind === 'judge' ? 'var(--line-judge)' : kind === 'stop' ? TONE_VAR.error : TONE_VAR.busy;
  return (
    // a click anywhere opens the big card; the title is the keyboard's way in (the card
    // holds buttons of its own, so it is not itself a button)
    <div onClick={onOpen}
      className={`so-card so-card--click relative overflow-hidden rounded-xl border bg-[var(--color-card)] px-4 py-3.5 cursor-pointer min-w-0 ${you ? 'so-card--you' : ''} ${fresh ? 'so-enter' : ''}`}
      style={{ borderColor: you ? 'color-mix(in srgb, var(--color-status-warning) 55%, var(--color-border))' : 'var(--color-border)' }}
      data-testid="so-run-card" data-id={r.dispatch_id} data-kind={kind}>
      <div className="flex items-center gap-2.5 min-w-0">
        <span aria-hidden className="so-live w-2 h-2 rounded-full flex-none" style={{ background: dot }} />
        <button type="button" className="flex-1 min-w-0 truncate text-left text-heading font-semibold hover:underline" title={r.title}
          aria-haspopup="dialog" data-testid="so-run-title" onClick={(e) => { e.stopPropagation(); onOpen(); }}>{r.title}</button>
        {you && <span className="so-pop text-meta font-semibold px-2 py-0.5 rounded-full text-white whitespace-nowrap"
          style={{ background: TONE_VAR.attention }} data-testid="so-you-pill">{tag?.text ?? t('swarmOut.board.waitsYou')}</span>}
        {kind === 'judge' && <span className="text-meta font-semibold px-2 py-0.5 rounded-full whitespace-nowrap"
          style={{ color: 'var(--line-judge)', background: 'color-mix(in srgb, var(--line-judge) 13%, transparent)' }}>{t('swarmOut.board.agentHolds')}</span>}
        <StartAndTime r={r} now={now} open />
      </div>
      <div className="mt-1.5 flex items-center gap-2 min-w-0">
        <div className="flex-1 min-w-0"><RunMeta r={r} fleet={fleet} /></div>
        {/* in the flow of this row, never over the answer below; a run that waits on the
            person is answered, not stopped, from here */}
        <div className="so-hover flex items-center gap-1.5 flex-none" data-testid="so-card-actions">
          {!you && r.state === 'running' && actionOf(r, 'stop') && <StopButton r={r} onDone={onReload} />}
          <OpenSessionButton r={r} />
        </div>
      </div>
      <Track r={r} kind={kind} at={at} />
      {own.length > 0 ? (
        // the question itself, answered here (the chat's own card)
        <div className="mt-3 flex flex-col gap-2 cursor-default" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}
          role="presentation" data-testid="so-card-asks">
          {own.map((a) => <SwarmOutWaitCard key={`${a.hive_id}/${a.request_id}`} wait={a} onAnswered={onReload} />)}
        </div>
      ) : you ? (
        <div className="mt-3 flex items-center gap-2 px-3 py-2.5 rounded-lg text-body"
          style={{ background: 'color-mix(in srgb, var(--color-status-warning) 8%, transparent)' }} data-testid="so-card-decide">
          <span className="flex-1 min-w-0">{r.why ? stageLabel(stageOf(r), r.why) : (tag?.text ?? t('swarmOut.board.waitsYou'))}</span>
          <button type="button" className="btn-sm font-semibold text-white" style={{ background: TONE_VAR.attention }}
            onClick={(e) => { e.stopPropagation(); onOpen(); }}>{t('swarmOut.board.openCard')}</button>
        </div>
      ) : nowLine && (nowLine.tool || nowLine.text) ? (
        <div className="mt-3 pt-2.5 border-t border-dashed border-[var(--color-border)] flex gap-2 items-baseline font-mono text-meta min-w-0 text-[var(--color-text-muted)]"
          data-testid="so-now" title={nowLine.object ?? nowLine.text ?? undefined}>
          {nowLine.tool && <b className="font-semibold text-[var(--color-text-secondary)] min-w-[36px]">{nowLine.tool}</b>}
          <span className="truncate">{nowLine.tool ? nowLine.object ?? '' : nowLine.text}<i className="so-caret" aria-hidden /></span>
        </div>
      ) : null}
    </div>
  );
}

/** One run that ended: in the last 72 hours here, any of the range in the history
 *  (which adds its cost under the time). */
export function DoneRow({ r, fleet, now, fresh = false, compact, onOpen, cost }: {
  r: SwarmOutRun; fleet: SwarmOutFleet | null; now: number; fresh?: boolean; compact: boolean; onOpen: () => void;
  cost?: string | null;
}) {
  const { t } = useTranslation();
  const o = outcomeOf(r);
  const steps = personSteps(r) ?? 0;
  const mark = o === 'ok' ? { c: TONE_VAR.ok, s: '✓' } : o === 'warn' ? { c: TONE_VAR.attention, s: '!' }
    : o === 'open' ? { c: TONE_VAR.busy, s: '●' } : { c: 'var(--color-text-dim)', s: '–' };
  const stage = stageOf(r);
  return (
    <div role="button" tabIndex={0} aria-haspopup="dialog" onClick={onOpen} data-title={r.title}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(); } }}
      className={`relative grid grid-cols-[18px_minmax(0,1fr)_auto] gap-x-3 items-center px-2.5 py-2 -mx-2.5 rounded-lg cursor-pointer hover:bg-[var(--color-hover)] ${fresh ? 'so-fresh' : ''}`}
      data-testid="so-done-row" data-outcome={o}>
      <span aria-hidden className="w-[18px] h-[18px] rounded-full grid place-items-center text-meta font-semibold"
        style={{ color: mark.c, background: `color-mix(in srgb, ${mark.c} 12%, transparent)` }}>{mark.s}</span>
      <span className="sr-only">{o === 'ok' ? t('swarmOut.hist.ok') : o === 'open' ? t('swarmOut.hist.open') : stageLabel(stage, r.why)}</span>
      <div className="min-w-0">
        <div className={`text-body truncate ${o === 'off' ? muted : ''}`} title={r.title}>
          {r.title}
          {o !== 'ok' && <span className={`text-small ${dim}`}> · {stageLabel(stage, r.why)}</span>}
          {steps > 0 && <span className="text-small" style={{ color: TONE_VAR.attention }}> · {t('swarmOut.review.autonomy.steppedIn', { count: steps })}</span>}
        </div>
        <div className="mt-px"><RunMeta r={r} fleet={fleet} /></div>
      </div>
      <div className="flex flex-col items-end">
        <StartAndTime r={r} now={now} open={outcomeOf(r) === 'open'} stacked={compact} />
        {cost != null && <span className={`text-meta tabular-nums ${dim}`} data-testid="so-cost">{cost}</span>}
      </div>
    </div>
  );
}

/** When a run settled (or stopped): what the 72-hour window is counted from. */
const endOf = (r: SwarmOutRun, asks: SwarmOutAsk[]) => Date.parse(waitingSince(r, asks) ?? r.sent_at);

export function RunBoard({ hidden = false, fleet = null, now, compact = false }: {
  hidden?: boolean; fleet?: SwarmOutFleet | null; now?: number; compact?: boolean;
}) {
  const { t } = useTranslation();
  const { data, failed, reload } = useRuns(hidden);
  const at = now ?? Date.now();
  const runs = useMemo(() => data?.runs ?? [], [data]);
  const asks = useMemo(() => data?.asks ?? [], [data]);
  const answered = data?.answered ?? [];
  const s = sections(runs, asks, at);
  // every run not settled is a card: what waits on you, what runs, and what sections()
  // put under done although it is not settled (an older dispatch never named may still
  // run; a stage with nothing to do here, such as awaiting with only "open session")
  const live = [...s.you, ...s.running, ...s.done.filter((r) => (!r.stage && r.session_unknown)
    || (!DONE_STAGES.includes(stageOf(r)) && stageOf(r) !== 'stopped'))];
  const liveIds = new Set(live.map((r) => r.dispatch_id));
  const recent = runs
    .filter((r) => !liveIds.has(r.dispatch_id) && (DONE_STAGES.includes(stageOf(r)) || stageOf(r) === 'stopped')
      && at - endOf(r, asks) < RECENT_MS)
    // newest first by the time each row shows (its start)
    .sort((a, b) => Date.parse(b.sent_at) - Date.parse(a.sent_at));
  // a question of no listed run, answered at the top; one the Hive refused, or that never
  // reached here, is handled on the Hive's own page (its sign-in is in the settings)
  const listed = new Set(runs.filter((r) => r.session_id).map((r) => `${r.hive_id}/${r.session_id}`));
  const mine = (a: SwarmOutAsk) => listed.has(`${a.hive_id}/${a.session_id}`);
  const loose = [...asks.filter((a) => !a.refused && !mine(a)), ...answered.filter((a) => !mine(a))];
  const refused = asks.filter((a) => a.refused && !mine(a));
  const unasked = data ? waitingUnasked(data) : [];
  const loaded = !!data;
  const freshLive = useFresh(live.map((r) => r.dispatch_id), loaded);
  const freshDone = useFresh(recent.map((r) => r.dispatch_id), loaded);
  // the big card: which run, and the order it walks (frozen when it opened)
  const [open, setOpen] = useState<{ id: string; order: string[] } | null>(null);
  const youCount = s.you.length;
  const youIds = new Set(s.you.map((r) => r.dispatch_id));
  return (
    <div data-testid="so-run-board">
      {failed && <div className={`text-small mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      {(data?.errors ?? []).map((e) => <div key={e} className="text-small mb-1" style={{ color: TONE_VAR.attention }}>{errorLabel(e)}</div>)}

      <section data-testid="so-running">
        <SectionHead title={t('swarmOut.board.running')} count={live.length + loose.length}
          right={youCount ? <span className="font-semibold" style={{ color: TONE_VAR.attention }} data-testid="so-you-count">{t('swarmOut.board.waiting', { count: youCount })}</span> : null} />
        {loose.length > 0 && (
          <div className="flex flex-col gap-2 mb-2.5" data-testid="so-loose">
            {loose.map((a) => (
              <SwarmOutWaitCard key={`${a.hive_id}/${a.request_id}`} wait={a} onAnswered={reload}
                source={<>{a.hive} · <span data-testid="swarm-out-ask-source">{a.dispatch_title
                  ?? (a.possible_dispatch_title ? t('swarmOut.ask.maybe', { title: a.possible_dispatch_title }) : t('swarmOut.ask.otherSession'))}</span>{a.asked_at ? ` · ${when(a.asked_at)}` : ''}</>} />
            ))}
          </div>
        )}
        {(refused.length > 0 || unasked.length > 0) && (
          <div className="mb-2.5" data-testid="so-on-hive-page">
            <div className={`text-small mb-1.5 ${muted}`}>
              {t(refused.length ? 'swarmOut.waiting.refused' : 'swarmOut.waiting.unasked')}{' '}
              <button type="button" className="text-[var(--color-primary)] hover:underline" onClick={openSwarmOutSettings}>{t('swarmOut.board.fleetSettings')}</button>
            </div>
            {refused.length > 0 && (
              <div className="flex flex-col gap-2">
                {refused.map((a) => <SwarmOutWaitCard key={`${a.hive_id}/${a.request_id}`} wait={a} onAnswered={reload} />)}
              </div>
            )}
          </div>
        )}
        {!loaded ? (!failed && <div className={`text-small ${muted}`}>{t('swarmOut.loading')}</div>)
          : live.length === 0 && loose.length === 0 ? (
            <div className="flex items-center gap-3 px-4 py-4 rounded-xl border border-dashed border-[var(--color-border)] text-body text-[var(--color-text-muted)]"
              data-testid="so-running-empty">
              <span aria-hidden className="w-7 h-7 rounded-lg grid place-items-center bg-[var(--color-bg-chrome)] text-[var(--color-text-dim)]">◇</span>
              <span>{t('swarmOut.board.emptyRunning')}</span>
            </div>
          ) : (
            <div className="flex flex-col gap-2.5">
              {live.map((r) => (
                <RunningCard key={r.dispatch_id} r={r} asks={asks} fleet={fleet} now={at} fresh={freshLive.has(r.dispatch_id)}
                  needsYou={youIds.has(r.dispatch_id)}
                  onReload={reload} onOpen={() => setOpen({ id: r.dispatch_id, order: live.map((x) => x.dispatch_id) })} />
              ))}
            </div>
          )}
      </section>

      <section className="mt-8" data-testid="so-recent">
        <SectionHead title={t('swarmOut.board.recent')} count={recent.length} right={<span className={dim}>{t('swarmOut.board.recentWindow')}</span>} />
        {loaded && recent.length === 0 ? <div className={`text-body ${muted}`}>{t('swarmOut.board.emptyRecent')}</div> : (
          <div className="flex flex-col divide-y divide-[var(--color-border)]">
            {recent.map((r) => (
              <DoneRow key={r.dispatch_id} r={r} fleet={fleet} now={at} compact={compact} fresh={freshDone.has(r.dispatch_id)}
                onOpen={() => setOpen({ id: r.dispatch_id, order: recent.map((x) => x.dispatch_id) })} />
            ))}
          </div>
        )}
      </section>

      {open && (
        <RunOverlay runs={runs} asks={asks} answered={answered} order={open.order} id={open.id} now={at}
          onSelect={(id) => setOpen((o) => (o ? { ...o, id } : o))}
          onSettled={(id) => setOpen((o) => (o ? { ...o, order: o.order.filter((x) => x !== id) } : o))}
          onClose={() => setOpen(null)} onReload={reload} />
      )}
    </div>
  );
}
