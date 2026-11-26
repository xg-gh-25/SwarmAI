/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * is the one place for where tasks run (it replaced the left-nav Hive page; design
 * docs/design/2026-11-20-swarm-out-multi-carrier.md §6.8; visual design mock
 * Knowledge/Mockups/2026-11-23-swarm-out-dashboard-redesign-v6.html). The header names
 * where tasks can run (FleetPills, one pill per place). Three views:
 *  - Run (RunBoard): what is running, a run that needs the person marked on its own card,
 *    and what finished in the last 72 hours.
 *  - History (HistoryView): one time range for every number: how much was accepted with
 *    no one stepping in, the runs by Brain, Project Agent or where they ran, every run.
 *  - Settings (SwarmOutSettings): everything about where tasks run, the AWS account
 *    included; nothing here sends the person to the App's Settings.
 * OPEN_SWARM_OUT_SETTINGS (any link to the Hives) opens the settings view; ChatPage
 * switches to this tab. An upgrading user who has a Hive sees one note, once, saying the
 * Hive page moved here and what Elastic Workers is.
 *
 * The fleet (/api/swarm-out/fleet) is polled here while the panel is shown: it gives the
 * header's pills and each run's place. The setup guide (HiveGuide) replaces the run view only while the AWS
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
import { OpenSessionContext, PanelHiddenContext } from './openSession';
import { useFleet } from './live';
import { FleetPills } from './FleetPills';
import { RunBoard } from './RunBoard';

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
function useAccountConnected(hidden: boolean): [boolean | null, (v: boolean) => void] {
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    if (hidden) return undefined;
    let stop = false;
    awsAccountService.get().then((v) => { if (!stop) setConnected(v.state !== 'unconnected'); })
      .catch(() => { if (!stop) setConnected(null); });
    return () => { stop = true; };
  }, [hidden]);
  return [connected, setConnected];
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
  const [connected, setConnected] = useAccountConnected(hidden);
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
      className={`px-3 py-[3px] rounded-md text-small transition-colors ${view === v ? 'bg-[var(--color-card)] text-[var(--color-text)] font-semibold shadow-sm' : `${muted} hover:text-[var(--color-text)]`}`}
    >{label}</button>
  );
  return (
    <OpenSessionContext.Provider value={onOpenSession ?? null}>
    <PanelHiddenContext.Provider value={hidden}>
    <div
      ref={rootRef}
      className="min-w-0 flex flex-col min-h-0 bg-[var(--color-bg)] text-body"
      style={{ flex: '1 1 0', display: hidden ? 'none' : undefined }}
      data-testid="swarm-out-dashboard"
      data-compact={compact || undefined}
    >
      <div className={`flex ${compact ? 'flex-wrap' : ''} items-center gap-x-3.5 gap-y-2 ${padX} ${compact ? 'py-3' : 'py-3.5'} flex-shrink-0 min-w-0 border-b border-[var(--color-border)]`}>
        <h2 className="m-0 text-title font-semibold whitespace-nowrap">{t('ui.SwarmOutDashboard.title')}</h2>
        <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0">{seg('run', t('swarmOut.view.run'))}{seg('history', t('swarmOut.view.history'))}{seg('settings', t('swarmOut.view.settings'))}</div>
        <div className={`${compact ? 'w-full' : 'ml-auto'} min-w-0 flex ${compact ? 'justify-start' : 'justify-end'}`}>
          <FleetPills fleet={fleet} failed={fleetFailed} hidden={hidden} />
        </div>
      </div>
      <div key={view} className={`so-view-in flex-1 overflow-y-auto overflow-x-hidden ${padX} ${compact ? 'pt-4 pb-6' : 'pt-5 pb-8'}`}
        data-testid="swarm-out-view" data-view={view}>
        {view === 'settings' ? <SwarmOutSettings fleet={fleet} compact={compact} onConnected={(v) => setConnected(v.state !== 'unconnected')} />
          : view === 'run'
          ? (connected === false
            ? <HiveGuide onConnected={(v) => setConnected(v.state !== 'unconnected')} />
            : <>
              {showNote && (fleet?.hives.length ?? 0) > 0 && (
                <div className="flex items-start gap-3 mb-5 px-3.5 py-3 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-card)] text-small text-[var(--color-text-secondary)]"
                  style={{ borderLeft: '3px solid var(--color-primary)' }} data-testid="swarm-out-single-entry-note">
                  <div className="flex-1 min-w-0">
                    <div><b className="text-[var(--color-text)]">{t('swarmOut.note.moved')}</b>{t('swarmOut.note.movedDetail')}</div>
                    <div className="mt-1"><b className="text-[var(--color-text)]">{t('swarmOut.note.elastic')}</b>{t('swarmOut.note.elasticDetail')}</div>
                  </div>
                  <button type="button" className={`whitespace-nowrap ${muted} hover:underline`} onClick={dismissNote}
                    data-testid="swarm-out-single-entry-note-ok">{t('swarmOut.note.ok')}</button>
                </div>
              )}
              <RunBoard hidden={hidden} fleet={fleet} now={at} compact={compact} />
            </>)
          : <HistoryView hidden={hidden} now={at} compact={compact} fleet={fleet} />}
      </div>
    </div>
    </PanelHiddenContext.Provider>
    </OpenSessionContext.Provider>
  );
}
