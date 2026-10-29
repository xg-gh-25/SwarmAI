/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * shows the Hives and the runs sent to them, on real data only (design
 * docs/design/2026-10-28-swarm-out-live-dashboard.md). Two views:
 *  - Runs (LiveDispatches): the questions waiting, the fleet and its free slots, the
 *    runs by state.
 *  - History: runs sent per day and the last week's runs by Hive, from the dispatch log.
 *    The cross-run audit (verdict, five dimensions, by type, occupancy) and grouping by
 *    brain or Project Agent have no data source yet: each says TBD and what is missing.
 *
 * The fleet (/api/swarm-out/fleet) is polled here while the panel is shown: it gives the
 * header's Hive count and decides whether the setup guide (HiveGuide) replaces the run
 * view (no Hive running). Responsive: the panel measures its own width (it shares the
 * row with the chat column and the Canvas); below COMPACT_BELOW px type steps down.
 * Labels come from the locale files (swarmOut.*); status colours from ./tone.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmOutRun } from '../../services/swarmOut';
import { HiveGuide } from './HiveGuide';
import { Dot, LiveDispatches, RunTitle, Tbd } from './LiveDispatches';
import { OpenSessionContext } from './openSession';
import { dailyByOutcome, endedLabel, runsByHive, stateLabel, useFleet, useRuns, when, type OutcomeBucket } from './live';
import { runTone, TONE_VAR, type Tone } from './tone';

/** Below this panel width (CSS px) the dashboard uses its compact layout. */
export const COMPACT_BELOW = 820;

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';

/** The panel's own content width, tracked with a ResizeObserver (0 before layout,
 *  and in jsdom, which has no ResizeObserver: then the wide layout is used). */
function usePanelWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/** The history buckets in stacking order (errors on top, so a failure is never buried),
 *  each in its tone's colour: the same colours as the run cards. */
const BUCKETS: { key: OutcomeBucket; tone: Tone }[] = [
  { key: 'error', tone: 'error' },
  { key: 'stopped', tone: 'attention' },
  { key: 'completed', tone: 'ok' },
  { key: 'other', tone: 'unknown' },
];
const bucketColor = (tone: Tone): string => (tone === 'unknown' ? 'var(--color-border)' : TONE_VAR[tone]);

function DailyBars({ runs, now }: { runs: SwarmOutRun[]; now: number }) {
  const { t } = useTranslation();
  const label = (k: OutcomeBucket) => t(`swarmOut.bucket.${k}`);
  const days = dailyByOutcome(runs, now);
  const max = Math.max(1, ...days.map((d) => d.count));
  return (
    <section className={card} data-testid="swarm-out-daily">
      <div className="flex items-baseline gap-2 px-4 pt-3 pb-1">
        <h3 className="m-0 text-[13px] font-semibold">{t('swarmOut.daily.title')}</h3>
        <span className="text-[12px] text-[var(--color-text-dim)]">{t('swarmOut.daily.sub')}</span>
      </div>
      <div className="px-4 pb-3">
        <div className="flex items-end gap-[6px] h-[90px] border-b border-[var(--color-border)]">
          {days.map((d) => (
            <div key={d.label} className="flex-1 flex flex-col justify-end h-full"
              title={d.count
                ? t('swarmOut.daily.dayWith', { day: d.label, count: d.count, parts: BUCKETS.filter((b) => d.by[b.key]).map((b) => `${label(b.key)} ${d.by[b.key]}`).join(t('swarmOut.sep.clause')) })
                : t('swarmOut.daily.day', { day: d.label, count: d.count })}
              data-testid="swarm-out-day" data-count={d.count} data-error={d.by.error}>
              {/* top down in BUCKETS order: errors first, so a failure is never buried */}
              {BUCKETS.map((b) => d.by[b.key] > 0 && (
                <div key={b.key} style={{ height: `${(d.by[b.key] / max) * 100}%`, background: bucketColor(b.tone) }}
                  className="first:rounded-t" />
              ))}
            </div>
          ))}
        </div>
        <div className={`flex justify-between text-[10.5px] mt-1 ${muted}`}>
          <span>{days[0].label}</span>
          <span className="flex gap-2">{BUCKETS.map((b) => (
            <span key={b.key} className="inline-flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm" style={{ background: bucketColor(b.tone) }} />{label(b.key)}</span>
          ))}</span>
          <span>{days[days.length - 1].label}</span>
        </div>
      </div>
    </section>
  );
}

function History({ hidden, now }: { hidden: boolean; now: number }) {
  const { t } = useTranslation();
  const { data, failed } = useRuns(hidden);
  const runs = data?.runs ?? [];
  const groups = runsByHive(runs, now);
  return (
    <div data-testid="swarm-out-history">
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>{t('swarmOut.runsUnreadable')}</div>}
      {data ? <DailyBars runs={runs} now={now} /> : !failed && <div className={`text-[12px] ${muted}`}>{t('swarmOut.loading')}</div>}
      <section className="mt-6" data-testid="run-history">
        <div className="flex items-baseline gap-2 px-0.5">
          <h3 className="m-0 text-[13px] font-semibold">{t('swarmOut.history.title')}</h3>
          <span className="text-[12px] text-[var(--color-text-dim)]">{t('swarmOut.history.sub')}</span>
        </div>
        <div className="mt-1 text-[12px] text-[var(--color-text-dim)] px-0.5">{t('swarmOut.history.groupingTbd')}</div>
        {data && groups.length === 0 && <div className={`mt-2 text-[12.5px] ${muted}`}>{t('swarmOut.history.empty')}</div>}
        {groups.map((g) => (
          <div key={g.hive} className="mt-4" data-testid="history-group" data-group={g.hive}>
            <div className="flex items-baseline gap-2 px-0.5 mb-1.5">
              <h4 className="m-0 text-[13px] font-semibold">{g.hive}</h4>
              <span className="text-[12px] text-[var(--color-text-dim)] tabular-nums" data-testid="history-group-count">{t('swarmOut.history.count', { count: g.runs.length })}</span>
            </div>
            <ul className={`${card} m-0 list-none px-1.5 py-0.5`}>
              {g.runs.map((r) => {
                const tone = runTone(r);
                return (
                  <li key={r.dispatch_id} className="px-2 py-2 border-t border-[var(--color-border)] first:border-t-0 min-w-0" data-testid="history-run" data-tone={tone}>
                    <div className="flex items-start gap-2 min-w-0">
                      <span className="h-[18px] flex items-center"><Dot tone={tone} /></span>
                      <RunTitle r={r} className="min-w-0 flex-1 line-clamp-2 leading-[18px]" />
                      <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.sent_at)}</span>
                    </div>
                    {/* the status word in its tone; grey (unknown) stays the muted text colour */}
                    <div className={`text-[11.5px] mt-0.5 pl-[15px] ${tone === 'unknown' || tone === 'busy' ? muted : ''}`}
                      style={tone === 'unknown' || tone === 'busy' ? undefined : { color: TONE_VAR[tone] }}>{r.result ? endedLabel(r.result).text : stateLabel(r)}</div>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </section>
      <Tbd title={t('swarmOut.tbdParts.verdict')} missing={t('swarmOut.tbdParts.verdictMissing')} testId="swarm-out-tbd-verdict" />
      <Tbd title={t('swarmOut.tbdParts.dimensions')} missing={t('swarmOut.tbdParts.dimensionsMissing')} testId="swarm-out-tbd-dimensions" />
      <Tbd title={t('swarmOut.tbdParts.occupancy')} missing={t('swarmOut.tbdParts.occupancyMissing')} testId="swarm-out-tbd-occupancy" />
      <Tbd title={t('swarmOut.tbdParts.type')} missing={t('swarmOut.tbdParts.typeMissing')} testId="swarm-out-tbd-type" />
    </div>
  );
}

export function SwarmOutDashboard({ hidden = false, onOpenHive, onOpenSession, now }: {
  hidden?: boolean; onOpenHive?: () => void;
  /** Open a run's Hive session in the Hive tab (run titles become buttons). */
  onOpenSession?: (sessionId: string, title: string) => void;
  now?: number;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<'run' | 'history'>('run');
  const rootRef = useRef<HTMLDivElement>(null);
  const width = usePanelWidth(rootRef);
  const compact = width > 0 && width < COMPACT_BELOW;
  const padX = compact ? 'px-4' : 'px-6';
  const { data: fleet, failed: fleetFailed } = useFleet(hidden);
  const at = now ?? Date.now();
  const seg = (v: 'run' | 'history', label: string) => (
    <button
      type="button"
      onClick={() => setView(v)}
      aria-pressed={view === v}
      className={`px-3 py-[3px] rounded-md text-[12.5px] ${view === v ? 'bg-[var(--color-card)] text-[var(--color-text)] font-medium shadow-sm' : muted}`}
    >{label}</button>
  );
  return (
    <OpenSessionContext.Provider value={onOpenSession ?? null}>
    <div
      ref={rootRef}
      className={`min-w-0 flex flex-col min-h-0 bg-[var(--color-bg)] ${compact ? 'text-[12.5px]' : 'text-[13px]'}`}
      style={{ flex: '1 1 0', display: hidden ? 'none' : undefined }}
      data-testid="swarm-out-dashboard"
      data-compact={compact || undefined}
    >
      <div className={`flex items-center gap-3 ${padX} ${compact ? 'pt-3' : 'pt-4'} flex-shrink-0 min-w-0`}>
        <h2 className={`m-0 ${compact ? 'text-[15px]' : 'text-[17px]'} font-semibold whitespace-nowrap`}>Swarm Out</h2>
        <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0">{seg('run', t('swarmOut.view.run'))}{seg('history', t('swarmOut.view.history'))}</div>
        {fleet !== null && (
          <span className={`ml-auto text-[12px] truncate min-w-0 ${muted}`} data-testid="swarm-out-hive-count">
            {fleet.running > 0
              ? <><span style={{ color: TONE_VAR.ok }}>{t('swarmOut.header.running', { count: fleet.running })}</span>{t('swarmOut.header.keepsGoing')}</>
              : fleet.hives.length > 0 ? t('swarmOut.header.noneRunning', { count: fleet.hives.length }) : t('swarmOut.header.noHive')}
          </span>
        )}
      </div>
      <div className={`flex-1 overflow-y-auto overflow-x-hidden ${padX} ${compact ? 'pt-3 pb-5' : 'pt-4 pb-6'}`}>
        {view === 'run'
          ? (fleet !== null && fleet.running === 0
            ? <HiveGuide hives={fleet.hives} onOpenHive={onOpenHive} />
            : <LiveDispatches hidden={hidden} fleet={fleet} fleetFailed={fleetFailed} onOpenHive={onOpenHive} now={at} />)
          : <History hidden={hidden} now={at} />}
      </div>
    </div>
    </OpenSessionContext.Provider>
  );
}
