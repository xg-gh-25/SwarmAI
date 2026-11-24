/**
 * The Canvas's Runs tab (design "the runs this session dispatched, visible in the
 * dispatching tab" r2 §3 ②): the runs the active chat tab's session dispatched to
 * Hives, where each stands and what it asks of the person.
 *
 * - ``CanvasRunsTab``: the header's third tab button, shown only while the session
 *   dispatched any listed run; its count is the unsettled runs (the board's Running
 *   cards), amber when one waits on the person (sessionRuns.useSessionRunsMark). When
 *   Runs is shown and the session's last listed run goes, it brings Artifacts back.
 * - ``CanvasRuns``: the body, Swarm Out's own RunBoard in its ``session`` scope: the same
 *   cards, rows and big card (RunOverlay) as the Swarm Out panel, fed by the one shared
 *   runs and fleet polls (components/swarm-out/runsPoll), never a poll of its own.
 *
 * Each subscribes on its own, so a poll re-renders these two and never the Canvas
 * (FileViewerPanel is memoised: a keystroke or a poll must not repaint the file viewer).
 *
 * @exports CanvasRunsTab, CanvasRuns
 */
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { RunBoard } from '../swarm-out/RunBoard';
import { reloadRuns, useSharedFleet, useSharedRuns } from '../swarm-out/runsPoll';
import { useSessionRunsMark } from '../swarm-out/sessionRuns';
import { getOverlayCtxBridge } from '../layout/overlayRegistry';

const TAB = 'inline-flex items-center gap-1.5 px-2.5 py-[2px] rounded-md text-[12px] transition-colors';
const ON = 'bg-[var(--color-card)] text-[var(--color-text)] shadow-[0_0_0_1px_var(--color-border)]';
const OFF = 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]';

export function CanvasRunsTab({ sessionId, selected, onSelect, onGone }: {
  sessionId: string | null | undefined; selected: boolean; onSelect: () => void;
  /** Runs is shown but the session lists no run any more (its last one went): the
   *  Canvas goes back to Artifacts, so no hidden tab stays selected */
  onGone: () => void;
}) {
  const { t } = useTranslation();
  const mark = useSessionRunsMark(sessionId);
  const gone = selected && mark === null;
  useEffect(() => { if (gone) onGone(); }, [gone, onGone]);
  if (!mark) return null;
  return (
    <button type="button" role="tab" aria-selected={selected} data-testid="canvas-tab-runs" onClick={onSelect}
      className={`${TAB} ${selected ? ON : OFF}`}>
      {t('sessionRuns.tab')}
      {mark.live > 0 && (
        <span data-testid="canvas-runs-count" data-waits={mark.waits || undefined}
          className="text-[10.5px] px-1.5 rounded-full tabular-nums"
          style={mark.waits
            ? { background: 'var(--color-status-warning)', color: 'var(--color-bg)' }
            : { background: 'color-mix(in srgb, var(--color-text) 10%, transparent)' }}>{mark.live}</span>
      )}
    </button>
  );
}

export function CanvasRuns({ sessionId }: { sessionId: string | null | undefined }) {
  const runs = useSharedRuns(true);
  const fleet = useSharedFleet(true);
  const bridge = getOverlayCtxBridge();
  return (
    <div className="h-full overflow-y-auto px-4 py-4" data-testid="canvas-runs">
      <RunBoard scope="session" sessionId={sessionId ?? null} compact fleet={fleet.data}
        source={{ data: runs.data, failed: runs.failed, reload: reloadRuns }}
        onShowAll={bridge.showSwarmOut ? () => bridge.showSwarmOut?.() : undefined} />
    </div>
  );
}
