/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * shows the Hives and the runs sent to them, on real data only (design
 * docs/design/2026-10-28-swarm-out-live-dashboard.md). Two views:
 *  - Runs (LiveDispatches): the fleet on top (its three numbers — questions waiting,
 *    free slots, runs running — and each Hive's slots), the questions waiting under
 *    it, then the runs by state.
 *  - History (HistoryView): how much came back done unattended, the runs by Brain, type
 *    or Hive, the five audit dimensions, and every run, each opening into its card.
 *    Worker occupancy has no data source yet: it says TBD.
 *
 * The fleet (/api/swarm-out/fleet) is polled here while the panel is shown: it gives the
 * header's Hive count and decides whether the setup guide (HiveGuide) replaces the run
 * view (no Hive running). Responsive: the panel measures its own width (it shares the
 * row with the chat column and the Canvas); below COMPACT_BELOW px type steps down.
 * Labels come from the locale files (swarmOut.*); status colours from ./tone.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HiveGuide } from './HiveGuide';
import { HistoryView } from './HistoryView';
import { LiveDispatches } from './LiveDispatches';
import { OpenSessionContext, PanelHiddenContext } from './openSession';
import { useFleet } from './live';
import { TONE_VAR } from './tone';

/** Below this panel width (CSS px) the dashboard uses its compact layout. */
export const COMPACT_BELOW = 820;

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

export function SwarmOutDashboard({ hidden = false, onOpenHive, onOpenSession, now }: {
  hidden?: boolean; onOpenHive?: () => void;
  /** Open a run's Hive session in the Hive tab (run titles become buttons). */
  onOpenSession?: (sessionId: string, title: string, readOnly?: boolean) => void;
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
    <PanelHiddenContext.Provider value={hidden}>
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
            : <LiveDispatches hidden={hidden} fleet={fleet} fleetFailed={fleetFailed} onOpenHive={onOpenHive} now={at} compact={compact} />)
          : <HistoryView hidden={hidden} now={at} compact={compact} />}
      </div>
    </div>
    </PanelHiddenContext.Provider>
    </OpenSessionContext.Provider>
  );
}
