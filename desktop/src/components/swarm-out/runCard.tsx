/**
 * The run card's zones (design: Knowledge/Designs/2026-11-18-swarm-out-run-card-redesign-mockup.html),
 * drawn by RunOverlay in this order: where the run is (``DispatchHero``: the dispatch
 * track, a pipeline run's stage strip, then the funnel's gates), what its agent is doing
 * (``NowLine``), what it was sent to do (``SentAs``) and what done looks like
 * (``DoneWhen``). Presentational: every value comes from the run as the backend gave it.
 *
 * The Hive's words (the now line) are its data: plain text, never markdown or a link.
 */
import { useTranslation } from 'react-i18next';
import type { SwarmOutRun } from '../../services/swarmOut';
import { FunnelLine } from './funnel';
import { ago, clock } from './runReview';
import { acceptanceClauses } from './runCardRules';
import { CHIP_COLOR, DISPATCH_STATIONS, dispatchTrack } from './track';
import { TONE_VAR } from './tone';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';

/** A zone's small label, the same across the card. */
export function ZoneLabel({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 mb-2.5 text-[11px] font-semibold uppercase tracking-[0.07em] text-[var(--color-text-muted)]">
      <span>{children}</span>
      {aside && <span className={`ml-auto normal-case tracking-normal font-normal text-[12px] ${dim}`}>{aside}</span>}
    </div>
  );
}

/** Where the run is: five stations, the passed ones checked, its own one marked in the
 *  colour of what it is (./track); a pipeline run names its stage in ON HIVE and shows
 *  its stages below; the funnel's gates (who, when, the commit) under it. */
export function DispatchHero({ r, now }: { r: SwarmOutRun; now: number }) {
  const { t } = useTranslation();
  const { at, kind } = dispatchTrack(r);
  const idx = DISPATCH_STATIONS.indexOf(at);
  const color = CHIP_COLOR[kind];
  const running = r.state === 'running';
  const stage = at === 'on_hive' && running ? r.live_stage?.stage ?? null : null;
  const started = (r.timings ?? []).reduce((n, x) => n + (typeof x.seconds === 'number' ? x.seconds : 0), 0);
  const steps = r.result?.work.steps.total ?? 0;
  const value = (s: typeof DISPATCH_STATIONS[number]): string | null => {
    if (s === 'sent') {
      const at0 = clock(r.sent_at, now);
      return started > 0 ? t('swarmOut.review.card.sentIn', { at: at0, n: Math.round(started) }) : at0;
    }
    if (s === 'on_hive') {
      const exec = r.project_agent?.execution ?? null;
      if (at === 'on_hive' && running) return [ago(r.sent_at, now), exec].filter(Boolean).join(' · ');
      if (idx > 1 && steps > 0) return t('swarmOut.review.card.steps', { count: steps });
      return exec;
    }
    return null;
  };
  const stages = stage ? (r.live_stage?.stages ?? []) : [];
  const cur = stages.indexOf(stage ?? '');
  return (
    <div data-testid="swarm-out-card-track" data-at={at} data-kind={kind}>
      <div className="grid grid-cols-5">
        {DISPATCH_STATIONS.map((s, i) => {
          const passed = i < idx || kind === 'done';
          const here = i === idx && kind !== 'done';
          const v = value(s);
          return (
            <div key={s} className="relative pt-[24px] min-w-0" data-testid="swarm-out-card-station" data-station={s}
              data-state={here ? 'here' : passed ? 'passed' : 'ahead'}>
              {i < DISPATCH_STATIONS.length - 1 && (
                <span aria-hidden className={`absolute top-[7px] left-[16px] right-0 h-[2px] ${here && kind === 'run' ? 'pipeline-line-flow' : ''}`}
                  style={{ background: here && kind === 'run' ? `color-mix(in srgb, ${color} 45%, transparent)`
                    : passed ? 'color-mix(in srgb, var(--color-text-muted) 55%, transparent)' : 'var(--color-border)' }} />
              )}
              <span aria-hidden className="absolute top-0 left-0 w-4 h-4 rounded-full grid place-items-center text-[9px] font-bold"
                style={here
                  ? { background: color, boxShadow: `0 0 0 5px color-mix(in srgb, ${color} 18%, transparent)` }
                  : passed ? { background: 'var(--color-text-muted)', color: 'var(--color-bg)' }
                    : { border: '2px solid var(--color-border)', background: 'var(--color-bg)' }}>
                {passed && !here ? '✓' : ''}
              </span>
              <div className="text-[11px] font-bold tracking-[0.08em] truncate"
                style={{ color: here ? color : passed ? 'var(--color-text-muted)' : 'var(--color-text-dim)' }}>
                {t(`swarmOut.track.${s}`)}{s === 'on_hive' && stage ? ` · ${stage.toUpperCase()}` : ''}
              </div>
              {v && <div className={`mt-0.5 text-[12.5px] truncate ${here ? 'text-[var(--color-text)]' : muted}`}>{v}</div>}
            </div>
          );
        })}
      </div>
      {stages.length > 0 && (
        <div className="mt-3.5 flex flex-wrap gap-1.5 text-[11px] font-semibold tracking-[0.05em]" data-testid="swarm-out-card-stages">
          {stages.map((s, i) => (
            <span key={s} className="px-2 py-0.5 rounded-md" data-state={i < cur ? 'done' : i === cur ? 'now' : 'ahead'}
              style={i === cur ? { color: TONE_VAR.busy, background: `color-mix(in srgb, ${TONE_VAR.busy} 14%, transparent)` }
                : { color: i < cur ? 'var(--color-text-muted)' : 'var(--color-text-dim)', background: 'var(--color-bg-chrome)' }}>
              {i < cur ? '✓ ' : ''}{s.toUpperCase()}
            </span>
          ))}
        </div>
      )}
      <FunnelLine r={r} />
    </div>
  );
}

/** What the agent of a running task is doing, as last read from its Hive: its newest
 *  line of text, its newest tool call, and how long ago that was read. Nothing when the
 *  run is not running or nothing was read. */
export function NowLine({ r, now }: { r: SwarmOutRun; now: number }) {
  const { t } = useTranslation();
  const n = r.state === 'running' ? r.live_now : null;
  if (!n || (!n.text && !n.tool)) return null;
  return (
    <div className="mt-5 grid grid-cols-[auto_minmax(0,1fr)_auto] gap-x-3 gap-y-1 items-baseline" data-testid="swarm-out-card-now">
      <span aria-hidden className="w-2 h-2 rounded-full -translate-y-px" style={{ background: TONE_VAR.busy }} />
      <span className="text-[15px] leading-[1.55] text-[var(--color-text)] break-words" data-testid="swarm-out-card-now-text">
        {n.text ?? ''}
      </span>
      <span className={`text-[12px] whitespace-nowrap ${dim}`}>{t('swarmOut.review.card.readAgo', { ago: ago(n.checked_at, now) })}</span>
      {n.tool && (
        <span className={`col-start-2 col-span-2 font-mono text-[12px] truncate ${muted}`} data-testid="swarm-out-card-now-tool" title={n.object ?? undefined}>
          <b className="inline-block min-w-[44px] font-semibold text-[var(--color-text-secondary)]">{n.tool}</b>{n.object ?? ''}
        </span>
      )}
    </div>
  );
}

/** What the run was sent to do: the dispatching agent's summary of the task (the
 *  backend's ``brief``), labelled as that, never as the person's own words. */
export function SentAs({ r }: { r: SwarmOutRun }) {
  const { t } = useTranslation();
  if (typeof r.brief !== 'string' || !r.brief.trim()) return null;
  return (
    <section data-testid="swarm-out-card-ask">
      <ZoneLabel>{t('swarmOut.review.card.sentAs')}</ZoneLabel>
      <div className="pl-3.5 border-l-2 border-[var(--color-border)] text-[13.5px] leading-[1.6] text-[var(--color-text-secondary)] break-words max-w-[62em]">
        {r.brief}
      </div>
    </section>
  );
}

/** What done looks like: the acceptance as numbered rows, one per clause. */
export function DoneWhen({ acceptance, aside }: { acceptance: string | null | undefined; aside?: React.ReactNode }) {
  const { t } = useTranslation();
  const rows = acceptanceClauses(acceptance);
  if (rows.length === 0) return null;
  return (
    <div data-testid="swarm-out-card-done-when">
      <ZoneLabel aside={aside}>{t('swarmOut.review.card.doneWhen')}</ZoneLabel>
      <ol className="m-0 p-0 list-none max-w-[62em]">
        {rows.map((c, i) => (
          <li key={i} className={`grid grid-cols-[22px_minmax(0,1fr)] gap-2.5 py-[7px] text-[13px] leading-[1.5] text-[var(--color-text-secondary)] ${i ? 'border-t border-[var(--color-border)]' : ''}`}
            data-testid="swarm-out-card-criterion">
            <span className={`text-[12px] tabular-nums pt-px ${dim}`}>{i + 1}</span>
            <span className="break-words">{c}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
