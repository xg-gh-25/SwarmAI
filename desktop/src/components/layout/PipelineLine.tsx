/**
 * PipelineLine — the Pipelines overlay's Line tab (design: the factory-line overlays,
 * Knowledge/Designs/2026-11-18-swarmai-factory-line-overlays-mockup.html). Every run in
 * flight, wherever it runs, as a track along the same stations, so what runs in parallel,
 * where each run stands and where work loops back read at a glance:
 *
 * - one sentence (on the line, running, waiting on you with the oldest, stopped);
 * - Waiting on you: the runs that ask something, as the overlay's rows (a click opens the
 *   run's big card, where its commands are), the oldest first;
 * - the line: the stations are the pipeline stages EVALUATE…REFLECT (Gate-0 and Gate-2
 *   marked as machine gates), then RETURN · LAND · JUDGE where a Hive run comes back.
 *   Runs are grouped by Brain; a Hive run that is a work package of a local run sits
 *   under it. A track runs along its profile's own stages; its chip sits at its current
 *   stage: blue an agent is working, amber it waits on you (the only amber here), a
 *   dashed red border it stopped, light purple the dispatching agent judges it. ↺N marks
 *   a stage that ran N times. A Hive run on its Hive spans the pipeline stations as one
 *   band: the Hive does not report its stage while it runs, so none is guessed;
 * - Where time goes: per pipeline station, the median minutes working and waiting on you
 *   and the extra rounds, aligned to the station columns (runs that recorded stage times);
 * - Done: the finished runs, folded, as the overlay's rows.
 *
 * Pure presentation over ``pipelineRows`` (lineGroups, stationCounts, verdictOf,
 * timeGoes); the rows' order and states are the backend's. No fixed slot count is shown:
 * neither this machine nor a Hive has one.
 *
 * Exports: PipelineLine
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ago } from '../swarm-out/runReview';
import { TONE_VAR } from '../swarm-out/tone';
import type { ChipKind } from '../swarm-out/track';
import {
  GATES, PIPELINE_STATIONS, RETURN_STATIONS, STATIONS, lineGroups, sections, stationCounts, timeGoes, verdictOf,
  type PipelineRow, type Station, type Track,
} from './pipelineRows';

const muted = 'text-[var(--color-text-muted)]';
const DONE_PAGE = 20;
const COLS = `200px repeat(${STATIONS.length}, minmax(0, 1fr))`;
const JUDGE = 'var(--line-judge)';
const BACK_ZONE = 'color-mix(in srgb, var(--line-judge) 6%, transparent)';

/** A chip's colours, one meaning per kind (./tone; light purple: the agent judges). */
export const CHIP_STYLE: Record<ChipKind, React.CSSProperties> = {
  run: { color: TONE_VAR.busy, background: `color-mix(in srgb, ${TONE_VAR.busy} 11%, var(--color-card))`,
    borderColor: `color-mix(in srgb, ${TONE_VAR.busy} 35%, var(--color-border))` },
  you: { color: TONE_VAR.attention, background: `color-mix(in srgb, ${TONE_VAR.attention} 13%, var(--color-card))`,
    borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 45%, var(--color-border))` },
  stop: { color: TONE_VAR.error, background: 'var(--color-card)', borderColor: TONE_VAR.error, borderStyle: 'dashed', borderWidth: 1.5 },
  judge: { color: JUDGE, background: `color-mix(in srgb, ${JUDGE} 11%, var(--color-card))`,
    borderColor: `color-mix(in srgb, ${JUDGE} 35%, var(--color-border))` },
  done: { color: 'var(--color-text-muted)', background: 'var(--color-card)', borderColor: 'var(--color-border)' },
};

/** The run's place as a neutral pill: this machine, or the Hive's name. */
export function WherePill({ r }: { r: PipelineRow }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center px-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-card)] text-meta whitespace-nowrap text-[var(--color-text-muted)]"
      data-testid="pipeline-row-where">{r.where === 'local' ? t('ui.PipelineOverlay.local') : r.hive ?? 'Hive'}</span>
  );
}

const stationLabel = (s: Station, t: (k: string) => string) =>
  s === 'return' ? t('ui.PipelineOverlay.fl_return') : s === 'land' ? t('ui.PipelineOverlay.fl_land')
    : s === 'judge' ? t('ui.PipelineOverlay.fl_judge') : s.toUpperCase();

function chipText(tr: Track, now: number, t: (k: string, o?: Record<string, unknown>) => string): string {
  const r = tr.row;
  if (tr.kind === 'you') return [r.task ? t(`ui.PipelineOverlay.task_${r.task}`) : t('ui.PipelineOverlay.fl_waitsOnYou'), ago(r.since, now)].join(' · ');
  if (tr.kind === 'stop') return t('ui.PipelineOverlay.fl_stopped');
  if (tr.kind === 'judge') return t('ui.PipelineOverlay.fl_agentJudges');
  return t('ui.PipelineOverlay.fl_working');      // where it works: the pill beside it
}

function Chip({ tr, now, onOpen, band }: { tr: Track; now: number; onOpen: (key: string) => void; band?: boolean }) {
  const { t } = useTranslation();
  const r = tr.row;
  return (
    <button type="button" onClick={() => onOpen(r.key)} title={r.title} data-testid={`pipeline-track-chip-${r.key}`} data-kind={tr.kind}
      className={`relative z-[1] w-full min-w-0 text-left rounded-md border px-1.5 py-1 ${band ? '' : 'max-w-full'}`} style={CHIP_STYLE[tr.kind]}>
      <span className="block truncate text-small font-semibold">
        {tr.kind === 'run' && <span aria-hidden className="inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle swarm-out-icon--busy" style={{ background: 'currentColor' }} />}
        {chipText(tr, now, t)}
      </span>
      <span className="block mt-0.5"><WherePill r={r} /></span>
    </button>
  );
}

function TrackRow({ tr, now, onOpen, child }: { tr: Track; now: number; onOpen: (key: string) => void; child?: boolean }) {
  const r = tr.row;
  const on = new Set(tr.path);
  // a Hive run, or a run whose stage is not recorded: its pipeline part is one band
  const band = r.where === 'hive' || tr.at === null;
  const atIdx = tr.at ? STATIONS.indexOf(tr.at) : -1;
  const seg = (s: Station, i: number) => {
    if (band && (PIPELINE_STATIONS as readonly string[]).includes(s)) return null;    // drawn as the band
    if (!on.has(s) || atIdx < 0 || i > atIdx) return null;
    const flow = tr.kind === 'run' || tr.kind === 'judge';
    return <span aria-hidden className={`absolute top-1/2 -mt-[1.5px] h-[3px] left-0 ${i === atIdx ? 'right-1/2' : 'right-0'} ${flow ? 'pipeline-line-flow' : ''}`}
      style={{ background: `color-mix(in srgb, ${(RETURN_STATIONS as readonly string[]).includes(s) ? JUDGE : TONE_VAR.busy} 40%, transparent)` }} />;
  };
  return (
    <div className="grid border-t border-[var(--color-border)] min-w-0" style={{ gridTemplateColumns: COLS }} data-testid={`pipeline-track-${r.key}`} data-kind={tr.kind}>
      <button type="button" onClick={() => onOpen(r.key)} title={r.title}
        className={`min-w-0 text-left py-1.5 pr-2 ${child ? 'pl-3 border-l-2' : ''}`} style={child ? { borderColor: `color-mix(in srgb, ${JUDGE} 40%, transparent)` } : undefined}>
        <span className="block truncate text-small font-semibold text-[var(--color-text)]">{r.wp ? `${r.wp} · ${r.title}` : r.title}</span>
        <span className={`block truncate text-meta ${muted}`}>{r.profile || '—'}</span>
      </button>
      {band && (
        // a Hive run's pipeline part: one band over the pipeline stations (its stage is
        // not reported while it runs), then the stations it reached back here
        <div className="relative flex items-center px-1 min-w-0" style={{ gridColumn: `2 / span ${PIPELINE_STATIONS.length}` }}>
          <span aria-hidden className={`absolute left-1 right-0 top-1/2 -mt-[1.5px] h-[3px] ${tr.at === null && tr.kind === 'run' ? 'pipeline-line-flow' : ''}`}
            style={{ background: `color-mix(in srgb, ${TONE_VAR.busy} 40%, transparent)` }} />
          {tr.at === null && <div className="relative w-[180px] max-w-full"><Chip tr={tr} now={now} onOpen={onOpen} band /></div>}
        </div>
      )}
      {STATIONS.map((s, i) => {
        if (band && (PIPELINE_STATIONS as readonly string[]).includes(s)) return null;
        const back = (RETURN_STATIONS as readonly string[]).includes(s);
        return (
          <div key={s} className="relative flex items-center px-1 min-h-[46px] min-w-0" style={back ? { background: BACK_ZONE } : undefined} data-station={s}>
            {seg(s, i)}
            {tr.rounds[s] && <span className="absolute -top-0.5 right-0.5 z-[2] text-meta font-bold px-1 rounded-lg border bg-[var(--color-card)]"
              style={{ color: TONE_VAR.attention, borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 45%, var(--color-border))` }}
              data-testid="pipeline-track-rounds">↺{tr.rounds[s]}</span>}
            {tr.at === s && <Chip tr={tr} now={now} onOpen={onOpen} />}
          </div>
        );
      })}
    </div>
  );
}

export function PipelineLine({ rows, now, onOpen, renderRow }: {
  rows: PipelineRow[]; now: number; onOpen: (key: string) => void;
  /** the overlay's row (Waiting on you, Done) */
  renderRow: (r: PipelineRow) => React.ReactNode;
}) {
  const { t } = useTranslation();
  const [showDone, setShowDone] = useState(false);
  const [donePage, setDonePage] = useState(0);
  const s = useMemo(() => sections(rows), [rows]);
  const groups = useMemo(() => lineGroups(rows), [rows]);
  const counts = useMemo(() => stationCounts(groups), [groups]);
  const v = useMemo(() => verdictOf(groups), [groups]);
  const time = useMemo(() => timeGoes(rows), [rows]);
  const doneShown = s.done.slice(0, (donePage + 1) * DONE_PAGE);
  const zones: [string, number, boolean][] = [
    [t('ui.PipelineOverlay.fl_zoneIntake'), 1, false], [t('ui.PipelineOverlay.fl_zoneDesign'), 2, false],
    [t('ui.PipelineOverlay.fl_zoneBuild'), 3, false], [t('ui.PipelineOverlay.fl_zoneDeliver'), 2, false],
    [t('ui.PipelineOverlay.fl_zoneBack'), 3, true]];
  const maxMin = Math.max(1, ...PIPELINE_STATIONS.map((x) => (time.stations[x].agentMin ?? 0) + (time.stations[x].waitMin ?? 0)));
  const h2 = (key: string, title: string, hint?: string, accent?: string) => (
    <div className="flex items-baseline gap-2 mt-5 mb-2" data-testid={`pipeline-section-${key}`}>
      <h3 className="m-0 text-body font-semibold" style={accent ? { color: accent } : undefined}>{title}</h3>
      {hint && <span className={`ml-auto text-small ${muted}`}>{hint}</span>}
    </div>
  );

  return (
    <div data-testid="pipeline-line">
      <div className="text-title leading-snug text-[var(--color-text-secondary,var(--color-text))]" data-testid="pipeline-line-verdict">
        {v.onLine === 0 ? t('ui.PipelineOverlay.fl_vNone') : <>
          <b className="text-[var(--color-text)]">{t('ui.PipelineOverlay.fl_vOnLine', { count: v.onLine })}</b>
          {t('ui.PipelineOverlay.fl_comma')}{t('ui.PipelineOverlay.fl_vRunning', { count: v.running })}
          {v.waiting > 0 && <>{t('ui.PipelineOverlay.fl_period')}<span className="font-semibold" style={{ color: TONE_VAR.attention }}>{t('ui.PipelineOverlay.fl_vWaiting', { count: v.waiting })}</span>
            {v.oldest && <>{t('ui.PipelineOverlay.fl_comma')}{t('ui.PipelineOverlay.fl_vOldest', { station: v.oldest.station ? stationLabel(v.oldest.station, t) : t('ui.PipelineOverlay.fl_onHiveShort'), ago: ago(v.oldest.since, now) })}</>}</>}
          {v.stopped > 0 && <>{t('ui.PipelineOverlay.fl_semicolon')}<span className="font-semibold" style={{ color: TONE_VAR.error }}>{t('ui.PipelineOverlay.fl_vStopped', { count: v.stopped })}</span></>}
          {t('ui.PipelineOverlay.fl_end')}</>}
      </div>

      {s.you.length > 0 && <>
        {h2('you', t('ui.PipelineOverlay.fl_waiting'), t('ui.PipelineOverlay.fl_waitingHint'), TONE_VAR.attention)}
        <div className="flex flex-col rounded-lg border" data-testid="pipeline-waiting"
          style={{ borderColor: `color-mix(in srgb, ${TONE_VAR.attention} 40%, var(--color-border))`, background: `color-mix(in srgb, ${TONE_VAR.attention} 6%, transparent)` }}>
          {s.you.map(renderRow)}
        </div>
      </>}

      {h2('line', t('ui.PipelineOverlay.fl_theLine'), t('ui.PipelineOverlay.fl_lineHint'))}
      <div className={`flex flex-wrap gap-x-5 gap-y-1 text-small mb-2 ${muted}`} data-testid="pipeline-line-legend">
        {(['run', 'you', 'stop', 'judge'] as ChipKind[]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span aria-hidden className="inline-block w-3 h-3 rounded-[3px] border" style={CHIP_STYLE[k]} />{t(`ui.PipelineOverlay.fl_legend_${k}`)}</span>
        ))}
        <span style={{ color: TONE_VAR.attention }} className="font-semibold">{t('ui.PipelineOverlay.fl_legendRounds')}</span>
        <span>◇ {t('ui.PipelineOverlay.fl_machineGate')}</span>
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[980px]">
          <div className="grid" style={{ gridTemplateColumns: COLS }}>
            <div />
            {zones.map(([label, span, back]) => (
              <div key={label} className="text-meta uppercase tracking-wide pl-1 pb-1 border-b-2" style={{ gridColumn: `span ${span}`,
                color: back ? JUDGE : 'var(--color-text-muted)', borderColor: back ? JUDGE : 'var(--color-border)' }}>{label}</div>
            ))}
          </div>
          <div className="grid sticky top-0 z-[3] bg-[var(--color-bg)] border-b border-[var(--color-border)]" style={{ gridTemplateColumns: COLS }} data-testid="pipeline-stations">
            <div />
            {STATIONS.map((st) => (
              <div key={st} className="px-1 py-1.5 min-w-0" style={(RETURN_STATIONS as readonly string[]).includes(st) ? { background: BACK_ZONE } : undefined} data-testid={`pipeline-station-${st}`}>
                <div className="text-meta font-bold tracking-wide text-[var(--color-text)] truncate">{stationLabel(st, t)}</div>
                <div className={`text-meta truncate ${muted}`}>{GATES[st] ? `◇ ${GATES[st]}` : ' '}</div>
                <div className={`text-meta truncate ${muted}`} data-testid="pipeline-station-count">
                  {counts[st].n ? t('ui.PipelineOverlay.fl_onIt', { count: counts[st].n }) : ' '}
                  {counts[st].you > 0 && <> · <b style={{ color: TONE_VAR.attention }}>{t('ui.PipelineOverlay.fl_youAt', { count: counts[st].you })}</b></>}
                </div>
              </div>
            ))}
          </div>
          {groups.length === 0 && <div className={`py-6 text-center text-small ${muted}`} data-testid="pipeline-line-empty">{t('ui.PipelineOverlay.fl_lineEmpty')}</div>}
          {groups.map((g) => (
            <div key={g.brain} data-testid={`pipeline-line-brain-${g.brain}`}>
              <div className="flex items-baseline gap-2 mt-3 pt-1.5 pb-1 border-t-2 border-[var(--color-text)]">
                <b className="text-body text-[var(--color-text)]">{g.brain}</b>
                <span className={`text-small ${muted}`}>{t('ui.PipelineOverlay.fl_brainHead', { count: g.count, running: g.running })}</span>
                {g.waiting > 0 && <span className="text-small font-semibold" style={{ color: TONE_VAR.attention }}>{t('ui.PipelineOverlay.fl_vWaiting', { count: g.waiting })}</span>}
              </div>
              {g.items.map((it) => (
                <div key={it.track.row.key}>
                  {it.children.length > 0 && (
                    <div className={`text-small pt-1.5 ${muted}`} data-testid="pipeline-line-swarm-run">
                      ◆ <b className="text-[var(--color-text)]">{t('ui.PipelineOverlay.fl_swarmRun', { count: it.children.length })}</b>
                    </div>
                  )}
                  <TrackRow tr={it.track} now={now} onOpen={onOpen} />
                  {it.children.map((c) => <TrackRow key={c.row.key} tr={c} now={now} onOpen={onOpen} child />)}
                </div>
              ))}
            </div>
          ))}

          {h2('time', t('ui.PipelineOverlay.fl_timeTitle'), time.covered ? t('ui.PipelineOverlay.fl_timeCovers', { count: time.covered }) : undefined)}
          {time.covered === 0 ? <div className={`text-small ${muted}`} data-testid="pipeline-time-none">{t('ui.PipelineOverlay.fl_timeNone')}</div> : (
            <div className="grid" style={{ gridTemplateColumns: COLS }} data-testid="pipeline-time">
              <div className={`self-end pb-1 text-small ${muted}`}>{t('ui.PipelineOverlay.fl_timeHint')}</div>
              {PIPELINE_STATIONS.map((st) => {
                const x = time.stations[st];
                const a = x.agentMin ?? 0, w = x.waitMin ?? 0, H = 110 / maxMin;
                return (
                  <div key={st} className="flex flex-col items-center justify-end min-h-[150px] pb-1" data-testid={`pipeline-time-${st}`}>
                    <div className="text-meta text-center leading-tight text-[var(--color-text)] mb-0.5">
                      {x.runs ? <b>{t('ui.PipelineOverlay.fl_min', { n: Math.round(a + w) })}</b> : <span className={muted}>—</span>}
                      {w > 0 && <div style={{ color: TONE_VAR.attention }} className="font-semibold">{t('ui.PipelineOverlay.fl_waitMin', { n: Math.round(w) })}</div>}
                      {x.rounds > 0 && <div style={{ color: TONE_VAR.attention }} className="font-bold">{t('ui.PipelineOverlay.fl_extraRounds', { count: x.rounds })}</div>}
                    </div>
                    <div className="w-[26px] flex flex-col-reverse rounded-t-[5px] rounded-b-[2px] overflow-hidden">
                      <span style={{ height: a * H, background: `color-mix(in srgb, ${TONE_VAR.busy} 45%, transparent)` }} />
                      <span style={{ height: w * H, background: TONE_VAR.attention }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <button type="button" className="flex items-baseline gap-2 mt-5 mb-1.5" aria-expanded={showDone} onClick={() => setShowDone((x) => !x)}
        data-testid="pipeline-section-done">
        <span className="text-body font-semibold">{t('ui.PipelineOverlay.sectionDone')}</span>
        <span className={`text-small tabular-nums ${muted}`}>{s.done.length}</span>
        <span className={`text-small ${muted}`}>{showDone ? t('swarmOut.review.fold') : t('swarmOut.review.unfold')}</span>
      </button>
      {showDone && <div className="flex flex-col">{doneShown.map(renderRow)}</div>}
      {showDone && s.done.length > doneShown.length && (
        <button type="button" className="text-left px-2.5 py-1.5 text-small text-[var(--color-primary)]" onClick={() => setDonePage((p) => p + 1)}
          data-testid="pipeline-done-more">{t('ui.PipelineOverlay.moreDone', { count: s.done.length - doneShown.length })} ›</button>
      )}
    </div>
  );
}
