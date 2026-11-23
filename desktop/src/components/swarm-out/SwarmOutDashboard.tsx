/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * is the one place for where tasks run (it replaced the left-nav Hive page; design
 * docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8, mock
 * Knowledge/Mockups/2026-11-23-swarm-out-single-entry.html v3). Three views:
 *  - Runs (LiveDispatches): where tasks run (one read-only row per Hive and Elastic
 *    Workers, each naming its execution surface), the questions waiting, the runs.
 *  - History (HistoryView): how much came back done unattended, the runs by Brain, type
 *    or Hive, the five audit dimensions, and every run, each opening into its card.
 *  - Settings (SwarmOutSettings): the few things only the person decides.
 * OPEN_SWARM_OUT_SETTINGS (any link to the Hives) opens the settings view; ChatPage
 * switches to this tab. An upgrading user who has a Hive sees one note, once, saying the
 * Hive page moved here and what Elastic Workers is.
 *
 * The fleet (/api/swarm-out/fleet) is polled here while the panel is shown: it gives the
 * header's Hive count. The setup guide (HiveGuide) replaces the run view only while the AWS
 * account is not connected: a stopped Hive never stops the run view. Responsive: the panel measures its own width (it shares the
 * row with the chat column and the Canvas); below COMPACT_BELOW px type steps down.
 * Labels come from the locale files (swarmOut.*); status colours from ./tone.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { HiveGuide } from './HiveGuide';
import { HistoryView } from './HistoryView';
import { SwarmOutSettings } from './SwarmOutSettings';
import { awsAccountService } from '../../services/awsAccount';
import { OPEN_SWARM_OUT_SETTINGS } from '../common/settingsEvents';
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

/** The one-time note for a user who had Hives before Swarm Out became their only place. */
export const NOTE_SEEN_KEY = 'swarmOut.singleEntryNote.seen';

function noteSeen(): boolean {
  try { return window.localStorage.getItem(NOTE_SEEN_KEY) === '1'; } catch { return true; }
}

/** Whether the AWS account is connected (null until known): without it there is nowhere
 *  to send a task, and the panel shows only the setup guide. */
function useAccountConnected(hidden: boolean): boolean | null {
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    if (hidden) return undefined;
    let stop = false;
    awsAccountService.get().then((v) => { if (!stop) setConnected(v.state !== 'unconnected'); })
      .catch(() => { if (!stop) setConnected(null); });
    return () => { stop = true; };
  }, [hidden]);
  return connected;
}

export function SwarmOutDashboard({ hidden = false, onOpenSession, now }: {
  hidden?: boolean;
  /** Open a run's Hive session in the Hive tab (run titles become buttons). */
  onOpenSession?: (sessionId: string, title: string, readOnly?: boolean) => void;
  now?: number;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<'run' | 'history' | 'settings'>('run');
  const [showNote, setShowNote] = useState(() => !noteSeen());
  const connected = useAccountConnected(hidden);
  useEffect(() => {
    const go = () => setView('settings');
    window.addEventListener(OPEN_SWARM_OUT_SETTINGS, go);
    return () => window.removeEventListener(OPEN_SWARM_OUT_SETTINGS, go);
  }, []);
  const dismissNote = () => {
    try { window.localStorage.setItem(NOTE_SEEN_KEY, '1'); } catch { /* shown again next time */ }
    setShowNote(false);
  };
  const rootRef = useRef<HTMLDivElement>(null);
  const width = usePanelWidth(rootRef);
  const compact = width > 0 && width < COMPACT_BELOW;
  const padX = compact ? 'px-4' : 'px-6';
  const { data: fleet, failed: fleetFailed } = useFleet(hidden);
  const at = now ?? Date.now();
  const seg = (v: 'run' | 'history' | 'settings', label: string) => (
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
        <h2 className={`m-0 ${compact ? 'text-[15px]' : 'text-[17px]'} font-semibold whitespace-nowrap`}>{t('ui.SwarmOutDashboard.title')}</h2>
        <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0">{seg('run', t('swarmOut.view.run'))}{seg('history', t('swarmOut.view.history'))}{seg('settings', t('swarmOut.view.settings'))}</div>
        {fleet !== null && (
          <span className={`ml-auto text-[12px] truncate min-w-0 ${muted}`} data-testid="swarm-out-hive-count">
            {fleet.running > 0
              ? <><span style={{ color: TONE_VAR.ok }}>{t('swarmOut.header.running', { count: fleet.running })}</span>{t('swarmOut.header.keepsGoing')}</>
              : fleet.hives.length > 0 ? t('swarmOut.header.noneRunning', { count: fleet.hives.length }) : t('swarmOut.header.noHive')}
          </span>
        )}
      </div>
      <div className={`flex-1 overflow-y-auto overflow-x-hidden ${padX} ${compact ? 'pt-3 pb-5' : 'pt-4 pb-6'}`}>
        {view === 'settings' ? <SwarmOutSettings />
          : view === 'run'
          ? (connected === false
            ? <HiveGuide />
            : <>
              {showNote && (fleet?.hives.length ?? 0) > 0 && (
                <div className="flex items-start gap-3 mb-4 px-3.5 py-3 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)] text-[12.5px] text-[var(--color-text-secondary)]"
                  style={{ borderLeft: '3px solid var(--color-primary)' }} data-testid="swarm-out-single-entry-note">
                  <div className="flex-1 min-w-0">
                    <div><b className="text-[var(--color-text)]">{t('swarmOut.note.moved')}</b>{t('swarmOut.note.movedDetail')}</div>
                    <div className="mt-1"><b className="text-[var(--color-text)]">{t('swarmOut.note.elastic')}</b>{t('swarmOut.note.elasticDetail')}</div>
                  </div>
                  <button type="button" className={`whitespace-nowrap ${muted} hover:underline`} onClick={dismissNote}
                    data-testid="swarm-out-single-entry-note-ok">{t('swarmOut.note.ok')}</button>
                </div>
              )}
              <LiveDispatches hidden={hidden} fleet={fleet} fleetFailed={fleetFailed} now={at} compact={compact} />
            </>)
          : <HistoryView hidden={hidden} now={at} compact={compact} />}
      </div>
    </div>
    </PanelHiddenContext.Provider>
    </OpenSessionContext.Provider>
  );
}
